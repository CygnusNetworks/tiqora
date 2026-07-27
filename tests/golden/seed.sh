#!/bin/bash
# Seed baseline fixtures into the shared golden-master DB via real peer console.
# Honours GOLDEN_PEER / GOLDEN_COMPOSE_PROJECT from peer_env.py.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GOLDEN_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load peer exports if not already set.
if [ -z "${GOLDEN_COMPOSE_PROJECT:-}" ]; then
  # Prefer backend venv pyyaml; fall back to system python3.
  if [ -x "${ROOT}/.venv/bin/python" ]; then
    PY="${ROOT}/.venv/bin/python"
  else
    PY=python3
  fi
  # shellcheck disable=SC1090
  eval "$("$PY" "${GOLDEN_DIR}/peer_env.py" "${GOLDEN_PEER:-}")"
fi

COMPOSE=(docker compose -p "${GOLDEN_COMPOSE_PROJECT}" -f "${GOLDEN_DIR}/docker-compose.golden.yml")

console() {
    "${COMPOSE[@]}" exec -T znuny /usr/local/bin/znuny-entrypoint.sh console "$@"
}

echo "[seed] peer=${GOLDEN_PEER} project=${GOLDEN_COMPOSE_PROJECT}"
echo "[seed] applying Tiqora alembic chain (tiqora_* tables)"
(
  cd "${ROOT}/backend" && \
    DATABASE_URL="${GOLDEN_DB_ASYNC_URL:-mysql+aiomysql://znuny:znuny@127.0.0.1:3307/znuny}" \
    uv run alembic upgrade head
)

echo "[seed] agent user 'golden.agent'"
console "Admin::User::Add --user-name golden.agent --first-name Golden --last-name Agent \
    --email-address golden.agent@example.invalid --password golden-agent-pw --group admin" || true

echo "[seed] queue 'Golden'"
console "Admin::Queue::Add --name Golden --group users \
    --system-address-id 1 --first-response-time 60 --update-time 120 --solution-time 240" || true

echo "[seed] customer user 'golden.customer'"
console "Admin::CustomerUser::Add --user-name golden.customer --first-name Golden \
    --last-name Customer --email-address golden.customer@example.invalid \
    --customer-id golden-customer --password golden-customer-pw" || true

echo "[seed] done"
