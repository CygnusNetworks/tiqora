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
# Base: python:*-slim-bookworm (Debian 12). gssapi (kerberos extra) needs
# libkrb5 headers + a C compiler to build the extension against MIT Kerberos
# from source -- unless a prebuilt wheel is supplied (see GSSAPI_AMD64_WHEEL_URL
# below), which sidesteps the compile entirely.
FROM python:${PYTHON_VERSION}-slim-bookworm AS python-deps
ARG TARGETARCH
WORKDIR /app/backend
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Internal-only escape hatch: compiling gssapi's C extension against
# libkrb5-dev costs ~2min per build for a package that essentially never
# changes (measured on jenkins.cygnusnet.de: "Prepared 142 packages in
# 2m 01s", almost entirely gssapi). GSSAPI_AMD64_WHEEL_URL, when set, points
# at a wheel prebuilt once (by hand) and hosted on pypi.cygnusnet.de,
# sidestepping the compile. Left empty (the default, and what GitHub Actions
# always uses), this is a no-op and both this and the `uv sync` step below
# behave exactly as before. Same pattern as ~/git/auzui's Dockerfile/
# Jenkinsfile, minus the arm64 leg -- tiqora only ever builds linux/amd64
# (see Jenkinsfile).
ARG GSSAPI_AMD64_WHEEL_URL=""

# gcc/libkrb5-dev are only needed to compile gssapi's C extension from
# source, so skip the apt install entirely when a prebuilt wheel is used.
RUN gssapi_wheel_url=""; \
    if [ "$TARGETARCH" = "amd64" ]; then gssapi_wheel_url="$GSSAPI_AMD64_WHEEL_URL"; fi; \
    if [ -z "$gssapi_wheel_url" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends \
            gcc \
            libkrb5-dev \
            krb5-config \
        && rm -rf /var/lib/apt/lists/*; \
    fi

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src
# For the version-match guard below only -- kept out of the workdir so it
# can't confuse `uv sync` (backend/ has no lock file of its own; the real
# uv.lock lives at the workspace root and isn't otherwise part of this
# stage's build context).
COPY uv.lock /tmp/uv.lock
# Include the optional kerberos extra (gssapi) so SPNEGO can be enabled at
# runtime via TIQORA_SPNEGO_ENABLED + KRB5_KTNAME. Stays inert until then.
RUN gssapi_wheel_url=""; \
    if [ "$TARGETARCH" = "amd64" ]; then gssapi_wheel_url="$GSSAPI_AMD64_WHEEL_URL"; fi; \
    if [ -n "$gssapi_wheel_url" ]; then \
        locked_version=$(grep -A1 '^name = "gssapi"' /tmp/uv.lock 2>/dev/null | grep '^version' | sed -E 's/version = "(.*)"/\1/'); \
        wheel_version=$(echo "$gssapi_wheel_url" | sed -E 's#.*/gssapi-([0-9.]+)-.*#\1#'); \
        if [ -n "$locked_version" ] && [ "$locked_version" != "$wheel_version" ]; then \
            echo "ERROR: GSSAPI_AMD64_WHEEL_URL points at gssapi $wheel_version but uv.lock pins $locked_version -- rebuild the wheel and update the URL in the Jenkinsfile" >&2; \
            exit 1; \
        fi; \
        uv sync --no-dev --no-editable --extra kerberos --no-install-package gssapi \
        && uv pip install "$gssapi_wheel_url"; \
    else \
        uv sync --no-dev --no-editable --extra kerberos; \
    fi

# ---------- Runtime ----------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
WORKDIR /app

RUN useradd --create-home --uid 10001 tiqora \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgssapi-krb5-2 \
        libkrb5-3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-deps /app/backend/.venv /app/.venv
COPY backend/src /app/backend/src
COPY backend/alembic /app/backend/alembic
COPY backend/alembic.ini /app/backend/alembic.ini
COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY --from=frontend-build /workspace/frontend/dist /app/frontend/dist
COPY docker/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

# Build provenance surfaced in the System-Info page / health endpoints / OpenAPI
# (see backend/src/tiqora/__init__.py + config.py). Same describe/sha the frontend
# gets; empty in plain local builds → falls back to package metadata.
ARG TIQORA_VERSION=""
ARG TIQORA_GIT_SHA=""
ARG TIQORA_BUILD_TIME=""

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/backend/src" \
    PYTHONUNBUFFERED=1 \
    TIQORA_ENV=production \
    TIQORA_VERSION=$TIQORA_VERSION \
    TIQORA_GIT_SHA=$TIQORA_GIT_SHA \
    TIQORA_BUILD_TIME=$TIQORA_BUILD_TIME

WORKDIR /app/backend
USER tiqora
EXPOSE 8000 8001

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["api"]
