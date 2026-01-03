FROM kalilinux/kali-rolling:latest

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,target=/var/lib/apt,sharing=locked \
	apt update && apt -y install kali-linux-headless

COPY ./ /py-agterm/
WORKDIR /py-agterm

RUN if ! command -v uv >/dev/null 2>&1; then \
        curl -LsSf https://astral.sh/uv/install.sh | sh && \
        mv /root/.local/bin/uv /usr/local/bin/uv && \
        mv /root/.local/bin/uvx /usr/local/bin/uvx; \
    fi

# RUN uv venv
RUN uv sync
RUN uv pip install .
EXPOSE 5000
WORKDIR /root

CMD ["/py-agterm/.venv/bin/python", "-m", "py_agterm.mcp_server"]