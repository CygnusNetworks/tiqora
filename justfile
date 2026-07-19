# Tiqora common development targets
# Usage: just <recipe>

set dotenv-load := false

default:
    @just --list

# --- Dev stack ---

# Start development infrastructure (DB, Redis, Meili, Mailpit)
dev-up:
    docker compose -f docker-compose.dev.yml up -d

# Stop development infrastructure
dev-down:
    docker compose -f docker-compose.dev.yml down

# Tail development stack logs
dev-logs:
    docker compose -f docker-compose.dev.yml logs -f

# --- Backend ---

# Install / sync Python workspace deps
sync:
    cd backend && uv sync

# Run API server (reload)
api:
    cd backend && uv run uvicorn tiqora.api.app:create_app --factory --reload --host 0.0.0.0 --port 8000

# Run background worker
worker:
    cd backend && uv run python -m tiqora.worker

# Run MCP server process
mcp:
    cd backend && uv run python -m tiqora.mcp_server

# --- Quality ---

# Run backend unit/integration tests (db tests auto-skip without Docker)
test:
    cd backend && uv run pytest -q

# Unit tests only (no Docker / testcontainers)
test-unit:
    cd backend && uv run pytest -q -m "not db"

# DB integration tests (MariaDB + Postgres via testcontainers)
test-db:
    cd backend && uv run pytest -q -m db

# Lint (ruff) + type-check (mypy)
lint:
    cd backend && uv run ruff check src tests
    cd backend && uv run ruff format --check src tests
    cd backend && uv run mypy src/tiqora

# Auto-fix lint issues
fmt:
    cd backend && uv run ruff check --fix src tests
    cd backend && uv run ruff format src tests

# --- Frontend ---

# Install frontend workspace deps (pnpm monorepo)
fe-install:
    npm exec -y pnpm@9 -- install

# Frontend dev server
fe-dev:
    npm exec -y pnpm@9 -- --filter tiqora-frontend dev

# Frontend production build
fe-build:
    npm exec -y pnpm@9 -- --filter @tiqora/api-client build
    npm exec -y pnpm@9 -- --filter tiqora-frontend build

# Frontend unit tests (vitest)
fe-test:
    npm exec -y pnpm@9 -- --filter tiqora-frontend test

# Frontend lint (eslint + tsc)
fe-lint:
    npm exec -y pnpm@9 -- --filter tiqora-frontend lint

# Generate OpenAPI types into packages/api-client
api-client-gen:
    cd backend && uv run python -c "from tiqora.api.app import create_app; import json; print(json.dumps(create_app().openapi(), indent=2))" > ../packages/api-client/openapi.json
    npm exec -y pnpm@9 -- --filter @tiqora/api-client build

# Playwright e2e (mocked /api/v1, chromium only)
e2e:
    npm exec -y pnpm@9 -- --filter tiqora-frontend exec playwright install chromium
    npm exec -y pnpm@9 -- --filter tiqora-frontend e2e

# --- Docker image ---

# Build multi-stage application image locally
build:
    docker build -t tiqora:local .

# Validate compose files
compose-check:
    docker compose -f docker-compose.dev.yml config -q
    docker compose -f docker-compose.example.yml config -q
