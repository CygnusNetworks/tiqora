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

# Run backend unit/integration tests
test:
    cd backend && uv run pytest -q

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

# Install frontend deps
fe-install:
    cd frontend && pnpm install

# Frontend dev server
fe-dev:
    cd frontend && pnpm dev

# Frontend production build
fe-build:
    cd frontend && pnpm build

# --- Docker image ---

# Build multi-stage application image locally
build:
    docker build -t tiqora:local .

# Validate compose files
compose-check:
    docker compose -f docker-compose.dev.yml config -q
    docker compose -f docker-compose.example.yml config -q
