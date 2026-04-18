# ── Stage 1: Build dependencies ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-editable

# ── Stage 2: Runtime image ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PORT=${API_PORT} \
    API_HOST=${API_HOST} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY . .

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE ${API_PORT}

CMD ["sh", "-c", "uvicorn", "api.app:main", "--host", "${API_HOST}", "--port", "${API_PORT}", "--log-level", "info"]
