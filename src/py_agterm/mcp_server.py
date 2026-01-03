from collections.abc import AsyncIterator

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession

from dataclasses import dataclass
from contextlib import asynccontextmanager
from py_agterm import AGTerm


@dataclass
class AGTermConfig:
    command: str = "/bin/bash"
    max_history_bytes: int = 5 * 1024 * 1024
    ready_markers: list[str] = None


class AGTermSession:
    """Create a session with an AGTerm instance."""

    def __init__(self, agterm: AGTerm):
        self.agterm = agterm

    @staticmethod
    async def connect() -> "AGTermSession":
        """Connect to an AGTerm instance via MCP."""
        agterm = AGTerm()
        if agterm.is_alive():
            agterm.send_and_read_until_ready("")  # Initial read to get to prompt
            return AGTermSession(agterm=agterm)

    async def disconnect(self) -> None:
        """Disconnect from the AGTerm instance."""
        self.agterm.close()

    def execute_command(self, command: str, timeout_ms: int = 10000) -> str:
        """Execute a command in the AGTerm instance and return the output."""
        return self.agterm.send_and_read_until_ready(command, timeout_ms=timeout_ms)


@dataclass
class TerminalContext:
    """Terminal Context for AGTerm"""

    agterm: AGTermSession


@asynccontextmanager
async def agterm_session_context(server: FastMCP) -> AsyncIterator[TerminalContext]:
    """Async context manager for AGTerm session."""
    session = await AGTermSession.connect()
    try:
        yield TerminalContext(agterm=session)
    finally:
        await session.disconnect()


mcp = FastMCP("agterm", lifespan=agterm_session_context, host="0.0.0.0", port=5000)


@mcp.tool()
def agterm_tool(
    ctx: Context[ServerSession, TerminalContext], command: str, timeout_ms: int = 10000
) -> str:
    """Description: Tool to execute commands in AGTerm. This is a
    Provide 'command' parameter to execute the command.
    Optionally provide 'timeout_ms' parameter for command timeout (ms).

    """

    if not command:
        ctx.log.warning("No command provided to agterm_tool.")
    output = ctx.request_context.lifespan_context.agterm.execute_command(
        command, timeout_ms=timeout_ms
    )
    return output


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
