#!/bin/bash
# Seed baseline fixtures into the shared golden-master DB via real Znuny
# console commands. Idempotent-ish: console Add commands fail loudly if the
# entity already exists, so this is safe to skip on re-run failures.
set -uo pipefail

COMPOSE="docker compose -f $(dirname "$0")/docker-compose.golden.yml"
console() {
    $COMPOSE exec -T znuny su -s /bin/bash otrs -c "perl /opt/otrs/bin/otrs.Console.pl $*"
}

echo "[seed] agent user 'golden.agent'"
console "Admin::User::Add --user-name golden.agent --first-name Golden --last-name Agent \
    --email-address golden.agent@example.invalid --password golden-agent-pw --group admin"

echo "[seed] queue 'Golden'"
console "Admin::Queue::Add --name Golden --group users \
    --system-address-id 1 --first-response-time 60 --update-time 120 --solution-time 240 \
    --calendar Calendar1"

echo "[seed] customer user 'golden.customer'"
console "Admin::CustomerUser::Add --user-name golden.customer --first-name Golden \
    --last-name Customer --email-address golden.customer@example.invalid \
    --customer-id golden-customer --password golden-customer-pw"

echo "[seed] done"
