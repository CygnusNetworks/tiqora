# Docker Compose deployment guide

This walks through [`docker-compose.example.yml`](../../docker-compose.example.yml)
at the repository root — copy it to `docker-compose.yml`, fill in secrets,
and adjust the notes below before going anywhere near production traffic.

```sh
cp docker-compose.example.yml docker-compose.yml
```

## Services

| Service | Image | Role |
|---|---|---|
| `postgres` (or `mariadb`, mutually exclusive) | `postgres:16` / `mariadb:10.11` | Primary datastore. Enable **exactly one**; set `DATABASE_URL` to match. |
| `redis` | `redis:7-alpine` | Sessions, presence keys, SSE pub/sub, rate limiting. |
| `meilisearch` | `getmeili/meilisearch:v1.11` | Ticket + knowledge-base full-text search index. |
| `tiqora-api` | `ghcr.io/cygnusnetworks/tiqora:latest` (`command: ["api"]`) | The FastAPI HTTP server (`/api/v1`, `/api/portal`, `/znuny-compat`) **and the web UI (SPA) at `/`** — one image ships backend + frontend. Port `8000`. Set `TIQORA_SERVE_FRONTEND=0` to disable and front the UI with a separate static host. |
| `tiqora-worker` | same image (`command: ["worker"]`) | Background poller: Znuny-write detection, search indexing, webhooks, daemon takeovers (postmaster/escalation/notifications/GenericAgent). No exposed port. |
| `tiqora-mcp` | same image (`command: ["mcp"]`) | The MCP server for AI/LLM integrations (see [`../api/mcp.md`](../api/mcp.md)). Port `8001`. |
| `mailpit` (optional, commented out) | `axllent/mailpit` | SMTP catch-all for non-production environments — never enable against a mailbox real customers use. |

The image is published to both `ghcr.io/cygnusnetworks/tiqora` and
`docker.io/cygnusnetworks/tiqora` (Docker Hub mirror); either works, pick
whichever your registry access/pull-through cache prefers.

Note that `tiqora-api`, `tiqora-worker`, and `tiqora-mcp` are the **same
image** running different entrypoint subcommands — there is one artifact to
pull and version, not three.

## Environment variable reference

All settings load from environment variables (see `tiqora.config.Settings`);
a `.env` file next to the process is also read if present. Defaults shown
are the code defaults, not necessarily sane production values.

### Core

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_ENV` | `development` | Set to `production` in deployment. |
| `TIQORA_DEBUG` | `false` | Never `true` in production (verbose errors). |
| `TIQORA_LOG_LEVEL` | `INFO` | Standard Python logging levels. |
| `TIQORA_SECRET_KEY` | *(insecure placeholder)* | **Must** be overridden — generate with `openssl rand -hex 32`. Used for session/token signing. |

### Data stores

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://tiqora:tiqora@localhost:5432/tiqora` | Async SQLAlchemy URL. PostgreSQL: `postgresql+asyncpg://...`. MariaDB/MySQL: `mysql+aiomysql://...`. Enable only one DB service. |
| `REDIS_URL` | `redis://localhost:6379/0` | |
| `MEILI_URL` | `http://localhost:7700` | |
| `MEILI_MASTER_KEY` | `tiqora-dev-master-key` | **Must** be overridden in production; shared between `meilisearch` and every Tiqora process that talks to it. |
| `MEILI_TICKETS_INDEX` | `tickets` | |
| `MEILI_KB_INDEX` | `kb` | |

### HTTP

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_CORS_ORIGINS` | `http://localhost:5173,http://localhost:8000` | Comma-separated origin list for the frontend. Set to your real UI origin(s), e.g. `https://tickets.example.com`. |

### Sessions

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_SESSION_COOKIE` | `tiqora_session` | Agent session cookie name. |
| `TIQORA_SESSION_TTL` | `86400` (seconds) | |
| `TIQORA_SESSION_COOKIE_SECURE` | `false` | **Set to `true`** once served over HTTPS (always, in production). |
| `TIQORA_SESSION_COOKIE_SAMESITE` | `lax` | |
| `TIQORA_CUSTOMER_SESSION_COOKIE` | `tiqora_customer_session` | Separate cookie for the customer portal; reuses the TTL/secure/samesite settings above. |

### Znuny-write poller / indexing

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_POLLER_INTERVAL` | `15` (seconds) | How often the worker checks for Znuny-side writes during parallel operation. |
| `TIQORA_INDEX_BATCH_SIZE` | `500` | Rows per Meilisearch indexing batch. |

### Schema ownership (parallel-operation gate)

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_SCHEMA_OWNERSHIP` | `false` | **Keep `false`** for the entire parallel-operation period with an existing Znuny install. Only set once the cutover runbook says to — see [`../guide/znuny-to-tiqora.md`](../guide/znuny-to-tiqora.md) and [`../cutover.md`](../cutover.md). |

### OIDC / SSO

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_OIDC_ENABLED` | `false` | |
| `TIQORA_OIDC_ISSUER` | *(empty)* | e.g. `https://idp.example.com/realms/tiqora` |
| `TIQORA_OIDC_CLIENT_ID` | *(empty)* | |
| `TIQORA_OIDC_CLIENT_SECRET` | *(empty)* | |
| `TIQORA_OIDC_SCOPES` | `openid profile email` | |
| `TIQORA_OIDC_CLAIM` | `preferred_username` | Claim mapped to `users.login`. No auto-provisioning — the claim value must match an existing, valid user. |
| `TIQORA_OIDC_REDIRECT_URI` | *(empty)* | e.g. `https://tickets.example.com/api/v1/auth/oidc/callback` |

### Kerberos / SPNEGO

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_SPNEGO_ENABLED` | `false` | Requires the optional `kerberos` extra (`gssapi`). |
| `KRB5_KTNAME` | *(empty)* | Path to a keytab reachable by the container, e.g. `/etc/krb5.keytab`. |

### LDAP/AD — agent auth

| Variable | Default |
|---|---|
| `TIQORA_LDAP_ENABLED` | `false` |
| `TIQORA_LDAP_HOST` | *(empty)*, e.g. `ldap.example.internal` |
| `TIQORA_LDAP_PORT` | `389` |
| `TIQORA_LDAP_USE_SSL` | `false` |
| `TIQORA_LDAP_USE_STARTTLS` | `false` |
| `TIQORA_LDAP_BASE_DN` | *(empty)*, e.g. `dc=example,dc=com` |
| `TIQORA_LDAP_BIND_DN` | *(empty)* |
| `TIQORA_LDAP_BIND_PASSWORD` | *(empty)* |
| `TIQORA_LDAP_UID_ATTR` | `uid` |
| `TIQORA_LDAP_ALWAYS_FILTER` | *(empty)* |
| `TIQORA_LDAP_GROUP_DN` | *(empty)* — optional group-membership gate |
| `TIQORA_LDAP_ACCESS_ATTR` | `memberUid` |
| `TIQORA_LDAP_USER_ATTR` | `DN` |

Same no-auto-provisioning rule as OIDC: the resolved LDAP UID must match an
existing, valid `users.login` row.

### LDAP/AD — customer portal auth

Mirrors the agent LDAP settings above, prefixed `TIQORA_CUSTOMER_LDAP_*`
instead of `TIQORA_LDAP_*` (`ENABLED`, `HOST`, `PORT`, `USE_SSL`,
`USE_STARTTLS`, `BASE_DN`, `BIND_DN`, `BIND_PASSWORD`, `UID_ATTR`,
`ALWAYS_FILTER`, `GROUP_DN`, `ACCESS_ATTR`, `USER_ATTR`), matched against
`customer_user.login` instead of `users.login`.

### TOTP 2FA

| Variable | Default |
|---|---|
| `TIQORA_TOTP_PENDING_TTL` | `300` (seconds) |
| `TIQORA_TOTP_ISSUER` | `Tiqora` |

### Webhooks

| Variable | Default |
|---|---|
| `TIQORA_WEBHOOK_TIMEOUT` | `10.0` (seconds, per attempt) |
| `TIQORA_WEBHOOK_MAX_ATTEMPTS` | `3` |

### Postmaster (inbound mail)

| Variable | Default |
|---|---|
| `TIQORA_POSTMASTER_INTERVAL` | `60` (seconds) — poll cadence once the `daemon.postmaster.enabled` takeover flag (a `tiqora_settings` DB row, not an env var) is on. |
| `TIQORA_SMTP_HOST` | `localhost` |
| `TIQORA_SMTP_PORT` | `25` |
| `TIQORA_SMTP_USE_TLS` | `false` |
| `TIQORA_SMTP_USER` | *(empty)* |
| `TIQORA_SMTP_PASSWORD` | *(empty)* |

### Daemon takeover poll intervals (Phase 4b)

| Variable | Default |
|---|---|
| `TIQORA_ESCALATION_INTERVAL` | `300` (seconds) |
| `TIQORA_NOTIFICATIONS_INTERVAL` | `60` (seconds) |
| `TIQORA_GENERIC_AGENT_INTERVAL` | `60` (seconds) |

Each function's actual on/off switch is a `tiqora_settings` DB row
(`daemon.<name>.enabled`), not an environment variable — these env vars only
set the worker's poll cadence once a function is turned on. See
[`../guide/znuny-to-tiqora.md`](../guide/znuny-to-tiqora.md) Stage 3 and
[`../parallel-operation.md`](../parallel-operation.md) for how/when to flip
those flags.

## Volumes

| Volume | Mounted by | Contents |
|---|---|---|
| `tiqora_pg` | `postgres` | PostgreSQL data directory. |
| `tiqora_mysql` (commented out) | `mariadb` | MariaDB data directory, if using MariaDB instead. |
| `tiqora_redis` | `redis` | Redis RDB/AOF persistence. |
| `tiqora_meili` | `meilisearch` | Search index data. |

The `tiqora-api`/`tiqora-worker`/`tiqora-mcp` containers themselves are
stateless — no application-data volume is needed for them.

## Connecting to an existing Znuny database

For a fresh, standalone Tiqora deployment, use the bundled `postgres` (or
`mariadb`) service as-is. For a **parallel-operation deployment against an
existing Znuny 6.5 database** (see
[`../guide/znuny-to-tiqora.md`](../guide/znuny-to-tiqora.md)):

1. Remove (or never enable) the bundled `postgres`/`mariadb` service in your
   compose file — you don't want Tiqora managing a second, empty database
   alongside Znuny's real one.
2. Point `DATABASE_URL` at the existing database host instead:
   ```yaml
   environment:
     DATABASE_URL: postgresql+asyncpg://tiqora:YOUR_DB_PASSWORD@db.example.internal:5432/znuny_production
     # or, MariaDB:
     # DATABASE_URL: mysql+aiomysql://tiqora:YOUR_DB_PASSWORD@db.example.internal:3306/znuny_production
   ```
3. Make sure the Tiqora containers can reach `db.example.internal` on the
   network (external network entry, VPN, or the host running Docker already
   having a route — this is infrastructure-specific and outside Compose's
   scope).
4. Use a dedicated, least-privilege DB user (see Stage 1 of
   [`../guide/znuny-to-tiqora.md`](../guide/znuny-to-tiqora.md)) — do not
   reuse Znuny's own DB user/credentials.
5. Keep `TIQORA_SCHEMA_OWNERSHIP` unset/`false` for the entire
   parallel-operation period.

## Networking and exposing ports

The example file publishes `tiqora-api` on `8000` and `tiqora-mcp` on `8001`
directly to the host. In production:

- **Do not** publish the database or Redis ports (`5432`/`3306`/`6379`) —
  they're commented out in the example for exactly this reason. Keep them
  reachable only on the internal Compose network.
- Bind `tiqora-api`/`tiqora-mcp` to localhost and put a reverse proxy in
  front, rather than publishing `0.0.0.0:8000`/`0.0.0.0:8001` directly:
  ```yaml
  ports:
    - "127.0.0.1:8000:8000"
  ```
  and similarly for `tiqora-mcp` on `8001`.
- Keep `/metrics` (Prometheus exposition on the API process) internal-only —
  do not expose it through the public reverse-proxy vhost. Either restrict
  it at the proxy layer (a separate `location` block that only your
  Prometheus scraper's source IP can reach) or scrape it over the Compose
  network directly from a Prometheus container that never faces the
  internet.

## Reverse proxy

TLS termination happens at the reverse proxy, not in the Tiqora containers —
they speak plain HTTP on the Compose network. Terminate TLS at nginx/Traefik/
Caddy with your normal certificate management (ACME, internal CA, etc.).

### nginx example

```nginx
# Main API + UI
server {
    listen 443 ssl;
    server_name tickets.example.com;

    ssl_certificate     /etc/ssl/tickets.example.com/fullchain.pem;
    ssl_certificate_key /etc/ssl/tickets.example.com/privkey.pem;

    # "/" proxies to the api, which serves BOTH the web UI (SPA) and the API —
    # no separate static web root to deploy.
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # SSE event stream: proxy_buffering off is required, or agents will
    # not see ticket updates in real time (see docs/api/rest-v1.md#realtime-events-sse).
    location /api/v1/events/stream {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
    }

    # Keep metrics off the public vhost entirely, or restrict by source IP:
    location /metrics {
        allow 10.0.0.0/8;   # your monitoring network — adjust
        deny all;
        proxy_pass http://127.0.0.1:8000;
    }
}

# MCP server — streamable-HTTP needs the same no-buffering, long-timeout
# treatment as SSE, PLUS careful trailing-slash handling: the FastMCP
# endpoint lives at /mcp/ (note the slash), and a 307 redirect from a
# missing trailing slash can race with concurrent MCP session setup
# (initialize POST + GET SSE) and drop the follow-up request. Point
# clients at the trailing-slash URL directly and avoid rewriting it.
server {
    listen 443 ssl;
    server_name mcp.tickets.example.com;

    ssl_certificate     /etc/ssl/mcp.tickets.example.com/fullchain.pem;
    ssl_certificate_key /etc/ssl/mcp.tickets.example.com/privkey.pem;

    location /mcp/ {
        proxy_pass http://127.0.0.1:8001/mcp/;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Key requirements for the MCP location, all load-bearing for streamable-HTTP:

- `proxy_buffering off` — MCP streams Server-Sent Events over the same
  connection as the POST request/response; buffering delays or breaks
  delivery.
- `proxy_http_version 1.1` (with `Connection ""`) — required for chunked /
  long-lived streaming responses to work through nginx at all.
- A **long `proxy_read_timeout`** — MCP sessions can be held open far longer
  than a typical HTTP request; the default nginx timeout will kill live
  sessions.
- **Trailing-slash consistency** — configure clients to call `/mcp/`
  directly rather than relying on the redirect from `/mcp`, and avoid
  `rewrite`/`return 301` tricks on this location that could introduce the
  same race.

### Traefik / Caddy note

Both handle HTTP/1.1 streaming and disable response buffering by default
for backends that don't declare `Content-Length` (which is the case here),
so the nginx-specific `proxy_buffering off`/`proxy_http_version 1.1` knobs
usually have no equivalent needed. Still explicitly set a **long response
timeout** for the MCP and SSE routes (Traefik:
`traefik.http.middlewares.<name>.forwardauth`/service-level
`responseForwarding` timeout, or router-level `idleTimeout`; Caddy:
`transport http { read_timeout ... }` on the relevant `reverse_proxy`
block) — their defaults are tuned for short-lived requests, not long-lived
streaming connections.

## Running migrations on first start

`tiqora migrate upgrade` must be run once before `tiqora-api`/`tiqora-worker`
serve traffic against a new database. It is **not** run automatically by
the `api`/`worker`/`mcp` container commands (deliberately — you don't want a
container restart silently applying migrations against a shared,
possibly-live Znuny database).

```sh
docker compose run --rm tiqora-api tiqora migrate upgrade
```

Run this after every image upgrade that includes new `tiqora_*` migrations,
before restarting the long-running services. See
[`../guide/znuny-to-tiqora.md`](../guide/znuny-to-tiqora.md) for the
distinction between the always-available `versions_tiqora/` chain and the
gated `versions_owned/` chain (only unlocked post-cutover).
