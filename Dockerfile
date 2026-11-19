# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Dependencies first, so this layer is cached until the lock file changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project

# openapi.json is bundled into the wheel (the MCP server builds its tools from it).
COPY openapi.json ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra postgres

FROM python:3.12-slim
RUN useradd --system --create-home --uid 10001 helpdesk
WORKDIR /app
COPY --from=builder --chown=helpdesk:helpdesk /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HELPDESK_HOST=0.0.0.0 \
    HELPDESK_PORT=8000
USER helpdesk
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
# Apply migrations, then serve. Run `helpdesk seed` or `helpdesk create-api-key` as one-off commands.
CMD ["sh", "-c", "helpdesk migrate && exec helpdesk serve"]
