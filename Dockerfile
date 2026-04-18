# ── Stage 1: Build dependencies ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-editable

# ── Stage 2: Runtime image ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PORT=8080 \
    API_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY . .

ENV PATH="/app/.venv/bin:$PATH"

# Expose nothing statically here, Cloud Run/Railway will use PORT.
# Using shell form for CMD so env vars are expanded at runtime.
CMD uvicorn api.app:app --host $API_HOST --port $PORT --log-level info
