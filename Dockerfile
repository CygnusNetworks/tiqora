# syntax=docker/dockerfile:1.7
# Multi-stage build: frontend static assets + Python runtime.
# Process role via CMD: api | worker | mcp

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22

# ---------- Frontend build ----------
FROM node:${NODE_VERSION}-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml* frontend/package-lock.json* ./
RUN corepack enable \
    && if [ -f pnpm-lock.yaml ]; then \
         corepack prepare pnpm@9 --activate \
         && (pnpm install --frozen-lockfile || pnpm install); \
       elif [ -f package-lock.json ]; then \
         npm ci; \
       else \
         npm install; \
       fi
COPY frontend/ ./
RUN if command -v pnpm >/dev/null 2>&1 && [ -f pnpm-lock.yaml ]; then pnpm build; else npm run build; fi

# ---------- Python deps ----------
FROM python:${PYTHON_VERSION}-slim-bookworm AS python-deps
WORKDIR /app/backend
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src
RUN uv sync --no-dev --no-editable

# ---------- Runtime ----------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
WORKDIR /app

RUN useradd --create-home --uid 10001 tiqora \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-deps /app/backend/.venv /app/.venv
COPY backend/src /app/backend/src
COPY backend/alembic /app/backend/alembic
COPY backend/alembic.ini /app/backend/alembic.ini
COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY --from=frontend-build /frontend/dist /app/frontend/dist
COPY docker/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/backend/src" \
    PYTHONUNBUFFERED=1 \
    TIQORA_ENV=production

WORKDIR /app/backend
USER tiqora
EXPOSE 8000 8001

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["api"]
