#!/bin/sh
set -e
ROLE="${1:-api}"
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
  *)
    echo "Unknown role: $ROLE (expected api|worker|mcp)" >&2
    exit 1
    ;;
esac
