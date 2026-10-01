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
| `tiqora-ai-worker` | same image (`command: ["ai-worker"]`) | Runs the AI subsystem (auto-reply worker, auto-summary scan) in its **own** process so a hung LLM call never stalls postmaster/outbox/indexing. Inert until `operation_mode=tiqora_primary` **and** `daemon.ai_worker.enabled`. No exposed port. |
| `tiqora-mcp` | same image (`command: ["mcp"]`) | The MCP server for AI/LLM integrations (see [`../api/mcp.md`](../api/mcp.md)). Port `8001`. |
| `mailpit` (optional, commented out) | `axllent/mailpit` | SMTP catch-all for non-production environments — never enable against a mailbox real customers use. |

The image is published to both `ghcr.io/cygnusnetworks/tiqora` and
`docker.io/cygnusnetworks/tiqora` (Docker Hub mirror); either works, pick
whichever your registry access/pull-through cache prefers.

Note that `tiqora-api`, `tiqora-worker`, `tiqora-ai-worker`, and `tiqora-mcp`
are the **same image** running different entrypoint subcommands — there is one
artifact to pull and version, not four.

## Environment variable reference

All settings load from environment variables (see `tiqora.config.Settings`);
a `.env` file next to the process is also read if present. Defaults shown
are the code defaults, not necessarily sane production values.

### Core

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_ENV` | `development` (code) / `production` (image) | The Docker image sets `TIQORA_ENV=production`; the code default applies only outside the image. |
| `TIQORA_DEBUG` | `false` | Never `true` in production (verbose errors). **Startup hard-fails** if true when `TIQORA_ENV=production`. |
| `TIQORA_LOG_LEVEL` | `INFO` | Standard Python logging levels. |
| `TIQORA_SECRET_KEY` | *(insecure placeholder)* | **Must** be overridden — generate with `openssl rand -hex 32`. Used for Fernet at-rest encryption (SMTP passwords, TOTP seeds, channel credentials). **Startup hard-fails** in production if still the default or shorter than 32 characters. |

### Data stores

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://tiqora:tiqora@localhost:5432/tiqora` | Async SQLAlchemy URL. PostgreSQL: `postgresql+asyncpg://...`. MariaDB/MySQL: `mysql+aiomysql://...`. Enable only one DB service. |
| `REDIS_URL` | `redis://localhost:6379/0` | Compose example requires `REDIS_PASSWORD` and uses `redis://:${REDIS_PASSWORD}@redis:6379/0`. |
| `MEILI_URL` | `http://localhost:7700` | |
| `MEILI_MASTER_KEY` | `tiqora-dev-master-key` | **Must** be overridden in production; shared between `meilisearch` and every Tiqora process that talks to it. **Startup hard-fails** if still a known dev/default value when `TIQORA_ENV=production`. |
| `MEILI_TICKETS_INDEX` | `tickets` | |
| `MEILI_KB_INDEX` | `kb` | |

### HTTP

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_CORS_ORIGINS` | `http://localhost:5173,http://localhost:8000` | Comma-separated origin list for the frontend. Set to your real UI origin(s), e.g. `https://tickets.example.com`. **Startup hard-fails** if the list contains `*` when `TIQORA_ENV=production` (credentialed wildcard CORS). |
| `TIQORA_METRICS_ENABLED` | `true`; **`false` when `TIQORA_ENV=production` and the var is unset** | Expose Prometheus `GET /metrics` on the app port. Set `true` explicitly for internal scrapes in production (nginx deny on the public vhost is still recommended). |
| `TIQORA_CSP_ENFORCE` | `false`; **`true` when `TIQORA_ENV=production` and the var is unset** | When `false`, the SPA CSP is sent as `Content-Security-Policy-Report-Only`. Production enforces it by default (the built SPA has no inline scripts); set `false` explicitly to fall back to report-only. |
| `TIQORA_PUBLIC_BASE_URL` | *(empty)* | Public browser URL, no trailing slash (e.g. `https://tickets.example.com`). Used for notification/password-setup links and the OAuth2 mail callback; set it on API **and** worker. Empty falls back to the first absolute `TIQORA_CORS_ORIGINS` entry. |
| `TIQORA_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxy IPs whose `X-Forwarded-For`/`X-Real-IP` uvicorn trusts for `request.client.host`. **Set to the proxy / Docker gateway IP** when the proxy is not on `127.0.0.1` inside the container, or the per-IP rate limit keys every request on the proxy IP. |
| `TIQORA_SERVE_FRONTEND` | `true` | Serve the bundled SPA from the API container at `/`. |
| `TIQORA_PORTAL_ENABLED` | `false` | The customer portal is opt-in. Unset/`false` hard-disables it (`/api/portal/*` → 404, `/` → agent login) regardless of the admin setting; `true` enables it, after which the admin switch can still turn it off. |

### Sessions

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_SESSION_COOKIE` | `tiqora_session` | Agent session cookie name. |
| `TIQORA_SESSION_TTL` | `3600` (seconds = 1h) | **Sliding** TTL: renewed on every authenticated request, so it is effectively the maximum idle time before logout. SSO/Kerberos agents re-authenticate transparently on expiry, so a short value is low-friction for them. Raise for password-only setups if 1h idle logout is too aggressive. |
| `TIQORA_SESSION_ABSOLUTE_TTL` | `43200` (12h) | Absolute maximum session age regardless of activity — a stolen token cannot be kept alive indefinitely by periodic requests. |
| `TIQORA_TRUSTED_PROXIES` | *(empty)* | Reverse-proxy IPs/CIDRs whose `X-Forwarded-For` is trusted for per-IP rate-limit keys. Set to your proxy so login lockout keys on the real client IP, not the shared proxy IP. |
| `TIQORA_SESSION_COOKIE_SECURE` | `false` in non-production; **`true` when `TIQORA_ENV=production` and the var is unset** | **Startup hard-fails** if set `false` when `TIQORA_ENV=production`. |
| `TIQORA_SESSION_COOKIE_SAMESITE` | `lax` | |
| `TIQORA_CUSTOMER_SESSION_COOKIE` | `tiqora_customer_session` | Separate cookie for the customer portal; reuses the same TTL/secure/samesite settings above (so `TIQORA_SESSION_TTL` also governs portal sessions). |

### Znuny-write poller / indexing

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_POLLER_INTERVAL` | `15` (seconds) | How often the worker checks for Znuny-side writes during parallel operation. |
| `TIQORA_INDEX_BATCH_SIZE` | `500` | Rows per Meilisearch indexing batch. |

### AI subsystem

Most AI configuration is data, not env: LLM providers, MCP clients, per-queue
policies, ACL/limits and the operation-mode gate are all managed in admin
(`/admin/ai/*`) and stored in `tiqora_*` tables. The env knobs are:

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_LLM_TIMEOUT` | `180.0` (seconds) | Per-request HTTP timeout for one LLM chat completion; also used by the attachment vision pre-pass. Detailed multi-document summaries can run long — raise if you see `LlmTimeoutError`. |
| `TIQORA_AI_WORKER_HEARTBEAT_FILE` | `/tmp/tiqora-ai-worker.heartbeat` | Liveness file the `ai-worker` process touches each loop (used by the example healthcheck). `TIQORA_WORKER_HEARTBEAT_FILE` (default `/tmp/tiqora-worker.heartbeat`) is the same for `tiqora-worker`. |
| `TIQORA_AI_WORKER_INTERVAL` | `10` (seconds) | AI worker tick cadence once `daemon.ai_worker.enabled` is on. |

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

The production image ships the `kerberos` optional extra (`gssapi`) and MIT
Kerberos runtime libraries (`libkrb5-3`, `libgssapi-krb5-2`). SPNEGO stays
**inert** until you enable the flag and mount a keytab. Without the flag,
`/api/v1/auth/spnego` is not advertised in `GET /api/v1/auth/methods`.

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_SPNEGO_ENABLED` | `false` | Set `true` only after a keytab is mounted. |
| `KRB5_KTNAME` | *(empty)* | Path inside the container, e.g. `/etc/tiqora/tiqora.keytab`. |

Recommended compose wiring for `tiqora-api` (and `tiqora-mcp` only if it
accepts SPNEGO — typically not needed for MCP):

```yaml
environment:
  TIQORA_SPNEGO_ENABLED: "true"
  KRB5_KTNAME: /etc/tiqora/tiqora.keytab
volumes:
  - ./secrets/tiqora.keytab:/etc/tiqora/tiqora.keytab:ro
  # Optional: pure acceptors usually need no krb5.conf (ticket decrypted with
  # the keytab; no KDC round-trip). a `default_realm` can help.
  # - ./secrets/krb5.conf:/etc/krb5.conf:ro
```

The SPN follows `HTTP/<api-hostname>@<REALM>`, e.g.
`HTTP/tiqora.example.com@EXAMPLE.COM`.

**Operational notes:**

- The reverse proxy must **forward** the `Authorization: Negotiate` header
  unmodified (do not strip it).
- The browser must reach the host that matches the keytab SPN
  (the name the keytab was issued for).
- SPNEGO only elevates agents flagged `sso_eligible`; the principal's primary
  part must still match an existing, valid `users.login`.

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
| `TIQORA_SMTP_ENABLED` | `false` — outbound SMTP is **off** until set to `true`; agent email replies are stored but not sent. |
| `TIQORA_SMTP_HOST` | `localhost` |
| `TIQORA_SMTP_PORT` | `25` |
| `TIQORA_SMTP_USE_TLS` | `false` |
| `TIQORA_SMTP_USER` | *(empty)* |
| `TIQORA_SMTP_PASSWORD` | *(empty)* |

### PGP / S/MIME (key stores shared with Znuny)

The production image ships `gnupg`, `openssl` and the backend `crypto` extra
(`python-gnupg`). Both backends follow Znuny's SysConfig switches (`PGP`,
`SMIME`, default off) and read the key locations from Znuny's settings
(`PGP::Options --homedir`, `SMIME::CertPath`, `SMIME::PrivatePath`). Inside a
container those host paths usually do not exist, so mount the directories and
point the env overrides at them:

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_CRYPTO_PGP_ENABLED` | *(unset → SysConfig `PGP`)* | `true`/`false` overrides the SysConfig switch. |
| `TIQORA_CRYPTO_PGP_GNUPGHOME` | *(unset → `--homedir` of `PGP::Options`)* | gpg keyring directory. |
| `TIQORA_CRYPTO_GPG_BIN` | *(unset → `PGP::Bin` if present, else `gpg`)* | |
| `TIQORA_CRYPTO_SMIME_ENABLED` | *(unset → SysConfig `SMIME`)* | |
| `TIQORA_CRYPTO_SMIME_CERT_DIR` | *(unset → `SMIME::CertPath`)* | `<hash>.<n>` certificates. |
| `TIQORA_CRYPTO_SMIME_PRIVATE_DIR` | *(unset → `SMIME::PrivatePath`)* | `<hash>.<n>` keys + `.P` secrets. |
| `TIQORA_CRYPTO_OPENSSL_BIN` | *(unset → `SMIME::Bin` if present, else `openssl`)* | |
| `TIQORA_CRYPTO_SMIME_CA_PATH` | *(empty)* | Optional CA bundle for signature chain checks. |

Compose wiring for `tiqora-api` **and** `tiqora-worker` (the worker runs the
postmaster, which verifies/decrypts inbound mail):

```yaml
environment:
  TIQORA_CRYPTO_PGP_GNUPGHOME: /var/lib/tiqora/gnupg
  TIQORA_CRYPTO_SMIME_CERT_DIR: /var/lib/tiqora/smime/certs
  TIQORA_CRYPTO_SMIME_PRIVATE_DIR: /var/lib/tiqora/smime/private
volumes:
  # Read-write: key uploads in the admin UI write here, exactly as Znuny does.
  # During parallel operation mount Znuny's own directories (e.g.
  # /opt/otrs/.gnupg, and the SMIME::CertPath / SMIME::PrivatePath dirs).
  - /opt/otrs/.gnupg:/var/lib/tiqora/gnupg
  - /etc/ssl/znuny-certs:/var/lib/tiqora/smime/certs
  - /etc/ssl/znuny-private:/var/lib/tiqora/smime/private
```

**Permissions:** the container runs as uid `10001`. gpg needs to write its
agent socket and lock files into the keyring directory, and the private key
directory must stay unreadable for others (Tiqora creates keys `0600`). When
sharing with Znuny, give both users access via a common group (and `chmod
g+rwX`) or run Tiqora with Znuny's uid (`user: "<otrs-uid>:<gid>"`). The
admin pages **PGP keys** / **S/MIME certificates** and **System info** show
whether the binaries run and the directories are writable (also logged at
startup as `crypto_backend_ready` / `crypto_backend_unusable`).

The S/MIME DB index (`smime_keys`, `smime_signer_cert_relations`) lives in
the shared Znuny database, so no extra volume is needed for it.

### Daemon takeover poll intervals

| Variable | Default |
|---|---|
| `TIQORA_ESCALATION_INTERVAL` | `300` (seconds) |
| `TIQORA_NOTIFICATIONS_INTERVAL` | `60` (seconds) |
| `TIQORA_GENERIC_AGENT_INTERVAL` | `60` (seconds) |
| `TIQORA_UNLOCK_TIMEOUT_INTERVAL` | `300` (seconds) |
| `TIQORA_PENDING_CHECK_INTERVAL` | `600` (seconds) |
| `TIQORA_OUTBOX_DRAIN_INTERVAL` | `60` (seconds) — `daemon.outbox.enabled` defaults **on** |
| `TIQORA_TELEGRAM_POLLER_INTERVAL` | `5` (seconds) |

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

| *(bind mounts)* | `tiqora-api`, `tiqora-worker` | Only when PGP/S-MIME is used: gpg keyring, S/MIME certificate and private-key directories — see [PGP / S/MIME](#pgp--smime-key-stores-shared-with-znuny). |

Apart from those optional key stores, the
`tiqora-api`/`tiqora-worker`/`tiqora-ai-worker`/`tiqora-mcp` containers are
stateless — no application-data volume is needed for them.

## Connecting to an existing Znuny database

For a **fresh, standalone Tiqora deployment**, use the bundled `postgres` (or
`mariadb`) service as-is — that database starts **empty**. After `docker
compose up`, run:

```bash
docker compose run --rm --entrypoint tiqora tiqora-api \
  bootstrap --admin-password '…' --seed
```

`tiqora bootstrap` loads the Znuny 6.5 base schema for greenfield installs
(installer order; multi-version parallel-op peers: [support-matrix.md](../support-matrix.md)), applies
the tiqora Alembic chain (`tiqora_*` only), and sets the admin password. The
API entrypoint’s automatic `tiqora migrate upgrade` alone is **not** enough
for greenfield: it never creates Znuny tables or the seeded `root@localhost`
user. Full runbook: [`../guide/fresh-install.md`](../guide/fresh-install.md).

For a **parallel-operation deployment against an existing OTRS/Znuny 6.0–7.3 database**
(see [`../guide/znuny-to-tiqora.md`](../guide/znuny-to-tiqora.md)):

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

The API container entrypoint runs `python -m tiqora.main migrate upgrade`
before serving traffic, unless `TIQORA_RUN_MIGRATIONS=0` is set. Worker,
AI-worker and MCP roles do not run migrations. Both ownership gates still control whether
the owned migration chain is available.

To migrate explicitly before starting the other roles:

```sh
docker compose run --rm --entrypoint python tiqora-api -m tiqora.main migrate upgrade
```

When automatic API migrations are disabled, run this after every image
upgrade that includes new migrations before restarting the services. See
[`../guide/znuny-to-tiqora.md`](../guide/znuny-to-tiqora.md) for the
distinction between the always-available `versions_tiqora/` chain and the
gated `versions_owned/` chain (only unlocked post-cutover).
