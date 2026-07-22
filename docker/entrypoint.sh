#!/bin/sh
set -e
ROLE="${1:-api}"

# Apply DB migrations before the API starts, unless disabled. Uses the
# ownership-gated migrate command: the owned chain is applied ONLY when the
# schema-ownership gate is active, so parallel-operation deployments never
# alter Znuny tables (a plain `alembic upgrade head` is also safe now, but
# this is the intended entrypoint).
if [ "$ROLE" = "api" ] && [ "${TIQORA_RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "Running migrations (tiqora migrate upgrade)..."
  python -m tiqora.main migrate upgrade
fi

case "$ROLE" in
  api)
    exec python -m tiqora.main api --host 0.0.0.0 --port 8000
    ;;
  worker)
    exec python -m tiqora.main worker
    ;;
  mcp)
    exec python -m tiqora.main mcp
    ;;
  ai-worker)
    exec python -m tiqora.main ai-worker
    ;;
  *)
    echo "Unknown role: $ROLE (expected api|worker|mcp|ai-worker)" >&2
    exit 1
    ;;
esac
