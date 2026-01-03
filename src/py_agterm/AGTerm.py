import os
import pty
import signal
import subprocess
import threading
import time
import re
import select
import fcntl
import termios
import struct
from typing import List, Optional
import logging

# ============================
# Constants & Regex
# ============================
DEFAULT_TIMEOUT_MS = 10_000
MAX_OUTPUT_BYTES = 5 * 1024 * 1024

# Updated Regex: Handles OSC sequences more aggressively across different terminators
ANSI_RE = re.compile(
    rb"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][0-9]*;[\s\S]*?(?:\x07|\x1b\\))"
)


class PtyError(Exception):
    pass


class PtyTimeoutError(PtyError):
    def __init__(self, message, last_output=""):
        super().__init__(f"{message} | Buffer: {repr(last_output[-150:])}")


class AGTerm:
    def __init__(
        self,
        command: str = "/bin/bash",
        max_history_bytes: int = MAX_OUTPUT_BYTES,
        ready_markers: Optional[List[str]] = None,
    ):
        self.command = command
        self.max_history = max_history_bytes
        self.markers = ready_markers or ["$ ", "# ", "pwndbg> ", "(gdb) "]

        self.master_fd: Optional[int] = None
        self.proc: Optional[subprocess.Popen] = None
        self.history = bytearray()
        self.buffer = bytearray()

        self._running = False
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._reader_thread: Optional[threading.Thread] = None

        self.start()

    def _set_terminal_size(self, rows=24, cols=80):
        """Sets the virtual window size to prevent unpredictable line wrapping."""
        if self.master_fd:
            size = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, size)

    def _sanitize(self, data: bytes) -> str:
        if not data:
            return ""
        # 1. Strip ANSI and OSC metadata
        clean = ANSI_RE.sub(b"", data)
        text = clean.decode("utf-8", errors="replace")

        # 2. Fix carriage returns and common shell artifacts
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 3. Aggressive cleanup: remove remaining shell integration artifacts
        # (Sometimes they leak if the escape char is missing)
        text = re.sub(r"133;[A-Z];.*?\x07", "", text)

        return "".join(ch for ch in text if (ch >= " " or ch in "\n\t"))

    def start(self):
        master, slave = pty.openpty()
        self.master_fd = master
        self._set_terminal_size()

        # CRITICAL: We clear PROMPT_COMMAND and set VTE_VERSION=0 to
        # tell the shell (bash/zsh) to stop sending integration metadata.
        env = {
            **os.environ,
            "TERM": "dumb",
            "PS1": "$ ",
            "PROMPT_COMMAND": "",  # Disables Fedora's metadata injection
            "VTE_VERSION": "0",  # Disables VTE shell integration
            "INSIDE_EMACS": "1",  # Many scripts check this to disable fancy codes
        }

        self.proc = subprocess.Popen(
            self.command,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            shell=True,
            preexec_fn=os.setsid,
            env=env,
        )
        os.close(slave)

        # Disable Echo at kernel level
        # try:
        #     attr = termios.tcgetattr(self.master_fd)
        #     attr[3] = attr[3] & ~termios.ECHO
        #     termios.tcsetattr(self.master_fd, termios.TCSANOW, attr)
        # except termios.error:
        #     pass

        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def _reader_loop(self):
        while self._running:
            try:
                r, _, _ = select.select([self.master_fd], [], [], 0.1)
                if not r:
                    continue

                chunk = os.read(self.master_fd, 8192)
                if not chunk:
                    break

                with self._lock:
                    self.buffer.extend(chunk)
                    self.history.extend(chunk)
                    if len(self.history) > self.max_history:
                        self.history = self.history[-self.max_history :]
                    self._cond.notify_all()
            except (OSError, ValueError, TypeError):
                break
        self._running = False

    def write_raw(self, data: bytes):
        if self.master_fd:
            os.write(self.master_fd, data)

    def write(self, text: str):
        # Because we disabled ECHO in start(), we no longer need to manually strip echo
        self.write_raw((text.rstrip() + "\n").encode())

    def send_control(self, char: str):
        char = char.upper()
        if "A" <= char <= "Z":
            ctrl_byte = bytes([ord(char) - ord("A") + 1])
            self.write_raw(ctrl_byte)

    def read_until_ready(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            with self._cond:
                current_text = self._sanitize(bytes(self.buffer))
                for m in self.markers:
                    if m in current_text:
                        self.buffer.clear()
                        return current_text

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PtyTimeoutError("Timeout", current_text)

                if not self.is_alive():
                    raise PtyError("Subprocess died")

                self._cond.wait(timeout=remaining)

    def send_and_read_until_ready(
        self, cmd: str, timeout_ms: int = DEFAULT_TIMEOUT_MS
    ) -> str:
        self.write(cmd)
        return self.read_until_ready(timeout_ms)

    def is_alive(self) -> bool:
        return self._running and self.proc and self.proc.poll() is None

    def close(self):
        self._running = False
        if self.proc:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                self.proc.wait(timeout=0.2)
            except:
                pass
        if self.master_fd:
            try:
                os.close(self.master_fd)
            except:
                pass
            self.master_fd = None
        if self._reader_thread:
            self._reader_thread.join(timeout=0.5)

    def restart(
        self, wait_for_prompt: bool = True, timeout_ms: int = DEFAULT_TIMEOUT_MS
    ):
        self.close()
        with self._lock:
            self.buffer.clear()
            self.history.clear()
        self.start()
        if wait_for_prompt:
            return self.read_until_ready(timeout_ms=timeout_ms)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == "__main__":
    with AGTerm() as term:
        output = term.send_and_read_until_ready("echo Hello, AGTerm!")
        logging.info("Received Output:")
        logging.info(output)
