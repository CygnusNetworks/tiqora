#!/bin/bash
# Entrypoint for multi-peer golden OTRS/Znuny containers.
#
# - Renders Kernel/Config.pm from the template + env vars.
# - Waits for MariaDB; first boot loads schema (auto-detects otrs-* vs plain names).
# - Console via /opt/otrs/bin/console.pl (symlink set at image build).
# - Fixes permissions and starts Apache (mod_perl) in the foreground.
set -euo pipefail

: "${DB_HOST:=mariadb}"
: "${DB_PORT:=3306}"
: "${DB_NAME:=znuny}"
: "${DB_USER:=znuny}"
: "${DB_PASSWORD:=znuny}"
: "${DB_ROOT_PASSWORD:=root}"
: "${ZNUNY_FQDN:=znuny.golden.local}"
: "${ZNUNY_SYSTEM_ID:=10}"
: "${GOLDEN_PEER:=znuny-6.5}"

INSTALL=/opt/otrs
CONSOLE_PL="${INSTALL}/bin/console.pl"
SETPERM_PL="${INSTALL}/bin/SetPermissions.pl"
DB_DIR="${INSTALL}/scripts/database"

echo "[znuny-entrypoint] peer=${GOLDEN_PEER} install=${INSTALL}"

echo "[znuny-entrypoint] rendering Kernel/Config.pm"
sed \
    -e "s/__DB_HOST__/${DB_HOST}/" \
    -e "s/__DB_NAME__/${DB_NAME}/" \
    -e "s/__DB_USER__/${DB_USER}/" \
    -e "s/__DB_PASSWORD__/${DB_PASSWORD}/" \
    -e "s/__FQDN__/${ZNUNY_FQDN}/" \
    -e "s/__SYSTEM_ID__/${ZNUNY_SYSTEM_ID}/" \
    "${INSTALL}/Kernel/Config.pm.tmpl" > "${INSTALL}/Kernel/Config.pm"
chown otrs:otrs "${INSTALL}/Kernel/Config.pm"

echo "[znuny-entrypoint] waiting for MariaDB at ${DB_HOST}:${DB_PORT}"
for _ in $(seq 1 60); do
    if mysql -h "$DB_HOST" -P "$DB_PORT" -u root -p"$DB_ROOT_PASSWORD" -e "SELECT 1" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done
mysql -h "$DB_HOST" -P "$DB_PORT" -u root -p"$DB_ROOT_PASSWORD" -e "SELECT 1" >/dev/null

echo "[znuny-entrypoint] ensuring database/user exist"
mysql -h "$DB_HOST" -P "$DB_PORT" -u root -p"$DB_ROOT_PASSWORD" <<-SQL
    CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
    CREATE USER IF NOT EXISTS '${DB_USER}'@'%' IDENTIFIED BY '${DB_PASSWORD}';
    GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'%';
    FLUSH PRIVILEGES;
SQL

TABLE_COUNT=$(mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" -N -B \
    -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DB_NAME}' AND table_name='ticket'")

_schema_file() {
    local base="$1"
    if [ -f "${DB_DIR}/${base}.mysql.sql" ]; then
        echo "${DB_DIR}/${base}.mysql.sql"
    elif [ -f "${DB_DIR}/otrs-${base}.mysql.sql" ]; then
        echo "${DB_DIR}/otrs-${base}.mysql.sql"
    else
        echo ""
    fi
}

if [ "$TABLE_COUNT" -eq 0 ]; then
    echo "[znuny-entrypoint] loading schema (first boot)"
    S=$(_schema_file schema)
    I=$(_schema_file initial_insert)
    P=$(_schema_file schema-post)
    if [ -z "$S" ] || [ -z "$I" ] || [ -z "$P" ]; then
        echo "[znuny-entrypoint] ERROR: missing schema SQL under ${DB_DIR}" >&2
        ls -la "${DB_DIR}" >&2 || true
        exit 1
    fi
    echo "[znuny-entrypoint] using $(basename "$S"), $(basename "$I"), $(basename "$P")"
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" < "$S"
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" < "$I"
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" < "$P"
    echo "[znuny-entrypoint] schema loaded"
else
    echo "[znuny-entrypoint] schema already present, skipping load"
fi

echo "[znuny-entrypoint] fixing permissions"
if [ -x "$SETPERM_PL" ] || [ -f "$SETPERM_PL" ]; then
    perl "$SETPERM_PL" --otrs-user=otrs --web-group=www-data 2>/dev/null \
        || perl "$SETPERM_PL" --znuny-user=otrs --web-group=www-data 2>/dev/null \
        || true
fi
# FileStorable cache creates nested dirs under var/tmp at runtime.
mkdir -p "${INSTALL}/var/tmp" "${INSTALL}/var/log"
chown -R otrs:otrs "${INSTALL}/var/tmp" "${INSTALL}/var/log"
chmod -R ug+rwX "${INSTALL}/var/tmp" "${INSTALL}/var/log"

echo "[znuny-entrypoint] rebuild config cache"
su -s /bin/bash otrs -c "perl ${CONSOLE_PL} Maint::Config::Rebuild" || true

if [ "${1:-}" = "console" ]; then
    shift
    chown -R otrs:otrs "${INSTALL}/var/tmp" 2>/dev/null || true
    exec su -s /bin/bash otrs -c "perl ${CONSOLE_PL} $*"
fi

echo "[znuny-entrypoint] starting apache2"
exec apache2ctl -D FOREGROUND
