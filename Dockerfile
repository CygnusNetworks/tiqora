# syntax=docker/dockerfile:1.7
# Multi-stage build: frontend static assets + Python runtime.
# Process role via CMD: api | worker | mcp

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22

# ---------- Frontend build ----------
# The frontend lives in a pnpm workspace (root package.json + packages/api-client),
# so the workspace context is required for the install.
FROM node:${NODE_VERSION}-bookworm-slim AS frontend-build
WORKDIR /workspace
# Build provenance surfaced in the UI (see frontend/src/lib/appVersion.ts).
# Wired from CI git ref/sha via docker build-args; empty in plain local builds.
ARG VITE_APP_VERSION=""
ARG VITE_GIT_SHA=""
ENV VITE_APP_VERSION=$VITE_APP_VERSION \
    VITE_GIT_SHA=$VITE_GIT_SHA
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY packages/ ./packages/
COPY frontend/package.json ./frontend/
RUN corepack enable \
    && corepack prepare --activate \
    && pnpm install --frozen-lockfile
COPY frontend/ ./frontend/
RUN pnpm --filter tiqora-frontend build

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
COPY --from=frontend-build /workspace/frontend/dist /app/frontend/dist
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
