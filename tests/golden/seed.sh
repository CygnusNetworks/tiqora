#!/bin/bash
# Seed baseline fixtures into the shared golden-master DB via real Znuny
# console commands. Idempotent-ish: console Add commands fail loudly if the
# entity already exists, so this is safe to skip on re-run failures.
set -uo pipefail

COMPOSE="docker compose -f $(dirname "$0")/docker-compose.golden.yml"
console() {
    $COMPOSE exec -T znuny su -s /bin/bash otrs -c "perl /opt/otrs/bin/otrs.Console.pl $*"
}

echo "[seed] applying Tiqora alembic chain (tiqora_* tables)"
(cd "$(dirname "$0")/../../backend" && \
    DATABASE_URL="${GOLDEN_DB_ASYNC_URL:-mysql+aiomysql://znuny:znuny@127.0.0.1:3307/znuny}" \
    uv run alembic upgrade head)

echo "[seed] agent user 'golden.agent'"
console "Admin::User::Add --user-name golden.agent --first-name Golden --last-name Agent \
    --email-address golden.agent@example.invalid --password golden-agent-pw --group admin"

echo "[seed] queue 'Golden'"
# No --calendar: use the default (non-calendar-specific) TimeWorkingHours
# sysconfig, which both Znuny and Tiqora resolve identically via SysConfig.
console "Admin::Queue::Add --name Golden --group users \
    --system-address-id 1 --first-response-time 60 --update-time 120 --solution-time 240"

echo "[seed] customer user 'golden.customer'"
console "Admin::CustomerUser::Add --user-name golden.customer --first-name Golden \
    --last-name Customer --email-address golden.customer@example.invalid \
    --customer-id golden-customer --password golden-customer-pw"

echo "[seed] done"
