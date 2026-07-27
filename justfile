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

# --- Golden-master (real peer container vs Tiqora) — manual multi-peer ---
# Select peer via GOLDEN_PEER (default znuny-6.5). See tests/golden/peers.yaml.
# Full matrix is manual only: just golden-all-peers  (no nightly / no PR gate).

# List peer ids from peers.yaml
golden-peers:
    python3 tests/golden/peer_env.py --list

# List peers whose source_dir is present on disk
golden-peers-ready:
    python3 tests/golden/peer_env.py --list-ready

# Show resolved env for GOLDEN_PEER (or default)
golden-peer-env:
    python3 tests/golden/peer_env.py

# Build + start peer stack (GOLDEN_PEER, default znuny-6.5)
golden-up:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(python3 tests/golden/peer_env.py "${GOLDEN_PEER:-}")"
    if [ "${GOLDEN_SOURCE_OK}" != "1" ]; then
      echo "ERROR: peer ${GOLDEN_PEER}: source missing or incomplete at ${GOLDEN_SOURCE_PATH}" >&2
      echo "Place a real release tree there (no external symlink; Docker COPY requires a real dir)." >&2
      exit 1
    fi
    echo "[golden-up] peer=${GOLDEN_PEER} project=${GOLDEN_COMPOSE_PROJECT} source=${GOLDEN_SOURCE_DIR}"
    export GOLDEN_PEER GOLDEN_SOURCE_DIR GOLDEN_COMPOSE_PROJECT
    docker compose -p "${GOLDEN_COMPOSE_PROJECT}" \
      -f tests/golden/docker-compose.golden.yml up -d --build --wait

# Seed baseline fixtures (admin agent, queue, customer user) into the shared DB
golden-seed:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(python3 tests/golden/peer_env.py "${GOLDEN_PEER:-}")"
    export GOLDEN_PEER GOLDEN_SOURCE_DIR GOLDEN_COMPOSE_PROJECT GOLDEN_DB_URL GOLDEN_DB_ASYNC_URL
    docker compose -p "${GOLDEN_COMPOSE_PROJECT}" \
      -f tests/golden/docker-compose.golden.yml exec -T znuny \
      /usr/local/bin/znuny-entrypoint.sh console Maint::Config::Rebuild
    bash tests/golden/seed.sh

# Stop the golden-master stack (keeps the DB volume)
golden-down:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(python3 tests/golden/peer_env.py "${GOLDEN_PEER:-}")"
    docker compose -p "${GOLDEN_COMPOSE_PROJECT}" \
      -f tests/golden/docker-compose.golden.yml down

# Stop the golden-master stack and remove the DB volume
golden-clean:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(python3 tests/golden/peer_env.py "${GOLDEN_PEER:-}")"
    docker compose -p "${GOLDEN_COMPOSE_PROJECT}" \
      -f tests/golden/docker-compose.golden.yml down -v

# Run the golden-master pytest suite (requires golden-up + golden-seed first)
golden-test:
    #!/usr/bin/env bash
    set -euo pipefail
    eval "$(python3 tests/golden/peer_env.py "${GOLDEN_PEER:-}")"
    export GOLDEN=1
    export GOLDEN_PEER GOLDEN_COMPOSE_PROJECT GOLDEN_DB_URL GOLDEN_DB_ASYNC_URL
    cd backend && uv run pytest -q -m golden ../tests/golden

# One-shot: up + seed + test + clean for GOLDEN_PEER
golden-run:
    #!/usr/bin/env bash
    set -euo pipefail
    just golden-up
    just golden-seed
    just golden-test
    just golden-clean

# Sequential Layer-B matrix for every peer with source on disk (manual only)
golden-all-peers:
    #!/usr/bin/env bash
    set -euo pipefail
    # Portable peer list (bash 3.2+; avoid mapfile)
    peers=()
    while IFS= read -r peer; do
      [ -n "${peer}" ] && peers+=("${peer}")
    done < <(python3 tests/golden/peer_env.py --list-ready)
    if [ "${#peers[@]}" -eq 0 ]; then
      echo "ERROR: no peers with source present. See tests/golden/README-multi-peer.md" >&2
      exit 1
    fi
    echo "[golden-all-peers] ready: ${peers[*]}"
    failed=()
    for peer in "${peers[@]}"; do
      echo ""
      echo "======== GOLDEN peer=${peer} ========"
      if GOLDEN_PEER="${peer}" just golden-run; then
        echo "[golden-all-peers] OK ${peer}"
      else
        echo "[golden-all-peers] FAIL ${peer}" >&2
        failed+=("${peer}")
        GOLDEN_PEER="${peer}" just golden-clean || true
      fi
    done
    if [ "${#failed[@]}" -gt 0 ]; then
      echo "[golden-all-peers] failed: ${failed[*]}" >&2
      exit 1
    fi
    echo "[golden-all-peers] all ${#peers[@]} peer(s) green"
