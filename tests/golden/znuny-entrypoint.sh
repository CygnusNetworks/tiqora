#!/bin/bash
# Entrypoint for the golden-master Znuny 6.5.22 container.
#
# - Renders Kernel/Config.pm from the template + env vars.
# - Waits for MariaDB.
# - On first boot (tables missing): loads schema.mysql.sql,
#   initial_insert.mysql.sql, schema-post.mysql.sql in that known-good order
#   (see docs/parallel-operation.md "Foreign keys and orphans").
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

echo "[znuny-entrypoint] rendering Kernel/Config.pm"
sed \
    -e "s/__DB_HOST__/${DB_HOST}/" \
    -e "s/__DB_NAME__/${DB_NAME}/" \
    -e "s/__DB_USER__/${DB_USER}/" \
    -e "s/__DB_PASSWORD__/${DB_PASSWORD}/" \
    -e "s/__FQDN__/${ZNUNY_FQDN}/" \
    -e "s/__SYSTEM_ID__/${ZNUNY_SYSTEM_ID}/" \
    /opt/otrs/Kernel/Config.pm.tmpl > /opt/otrs/Kernel/Config.pm
chown otrs:otrs /opt/otrs/Kernel/Config.pm

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

if [ "$TABLE_COUNT" -eq 0 ]; then
    echo "[znuny-entrypoint] loading schema (first boot)"
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" < /opt/otrs/scripts/database/schema.mysql.sql
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" < /opt/otrs/scripts/database/initial_insert.mysql.sql
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" < /opt/otrs/scripts/database/schema-post.mysql.sql
    echo "[znuny-entrypoint] schema loaded"
else
    echo "[znuny-entrypoint] schema already present, skipping load"
fi

echo "[znuny-entrypoint] fixing permissions"
perl /opt/otrs/bin/otrs.SetPermissions.pl --otrs-user=otrs --web-group=www-data || true

echo "[znuny-entrypoint] running SetPackageList / rebuild config cache"
su -s /bin/bash otrs -c "perl /opt/otrs/bin/otrs.Console.pl Maint::Config::Rebuild" || true

if [ "${1:-}" = "console" ]; then
    shift
    exec su -s /bin/bash otrs -c "perl /opt/otrs/bin/otrs.Console.pl $*"
fi

echo "[znuny-entrypoint] starting apache2"
exec apache2ctl -D FOREGROUND
