# Deployment

> Tiqora is **not production ready**. Treat this document as a draft of the
> intended deployment shape, not an operational runbook.

## Images

Multi-stage `Dockerfile` produces a single runtime image with the API code and
built frontend static assets. Process role is selected at start:

| Command / env | Process |
|---|---|
| `api` (default) | FastAPI (uvicorn) |
| `worker` | taskiq worker |
| `mcp` | FastMCP server |

Registry targets (CI):

- `ghcr.io/cygnusnetworks/tiqora`
- `docker.io/cygnusnetworks/tiqora`

Tags: `latest` (main), semver on release tags, `sha-<shortsha>`.

## Compose example

See [docker-compose.example.yml](../docker-compose.example.yml) for a commented
deployment skeleton with:

- `tiqora-api`, `tiqora-worker`, `tiqora-mcp`
- Choice of **PostgreSQL or MariaDB** (comments show both; enable one)
- Redis, Meilisearch
- Optional Mailpit for non-production mail sinks

Copy to `docker-compose.yml`, set secrets via environment or a secrets manager,
and never commit real credentials.

## Required configuration

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@db:5432/tiqora` | Or `mysql+aiomysql://…` |
| `REDIS_URL` | `redis://redis:6379/0` | Sessions + taskiq |
| `MEILI_URL` | `http://meilisearch:7700` | |
| `MEILI_MASTER_KEY` | strong secret | Production required |
| `TIQORA_SECRET_KEY` | random 32+ bytes | Cookie/session material |
| `TIQORA_LOG_LEVEL` | `INFO` | |
| `TIQORA_CORS_ORIGINS` | `https://helpdesk.example.com` | Comma-separated |
| `TIQORA_TOTP_PENDING_TTL` | `300` | Seconds a pending-2FA session stays valid |
| `TIQORA_TOTP_ISSUER` | `Tiqora` | Shown in authenticator apps |
| `TIQORA_WEBHOOK_MAX_ATTEMPTS` | `3` | Delivery retries with exponential backoff |
| `TIQORA_WEBHOOK_TIMEOUT` | `10.0` | Per-attempt HTTP timeout (seconds) |

Full settings live in `tiqora.config.Settings` (pydantic-settings). See below
for OIDC and Kerberos/SPNEGO-specific variables.

## OIDC / SSO configuration (Phase 3c)

Optional; the agent login page shows a "Sign in with SSO" button once
`TIQORA_OIDC_ENABLED=true` (discovered via `GET /api/v1/auth/methods`).

| Variable | Example | Notes |
|---|---|---|
| `TIQORA_OIDC_ENABLED` | `true` | Off by default |
| `TIQORA_OIDC_ISSUER` | `https://idp.example.com/realms/tiqora` | `/.well-known/openid-configuration` is appended |
| `TIQORA_OIDC_CLIENT_ID` | `tiqora` | |
| `TIQORA_OIDC_CLIENT_SECRET` | secret | |
| `TIQORA_OIDC_SCOPES` | `openid profile email` | |
| `TIQORA_OIDC_CLAIM` | `preferred_username` | Claim mapped to `users.login` |
| `TIQORA_OIDC_REDIRECT_URI` | `https://helpdesk.example.com/api/v1/auth/oidc/callback` | Must be registered with the provider |

**No auto-provisioning in v1**: the claim value must match an existing
`users.login` with `valid_id = 1`, or the callback returns `403`. Provision
agents in Znuny/Tiqora admin first, then point them at SSO.

## Kerberos / SPNEGO configuration (Phase 3c)

Optional; requires the `kerberos` extra (`uv sync --extra kerberos`, installs
`gssapi`) — without it, `/api/v1/auth/spnego` returns `501`.

| Variable | Example | Notes |
|---|---|---|
| `TIQORA_SPNEGO_ENABLED` | `true` | Off by default |
| `KRB5_KTNAME` | `/etc/tiqora/tiqora.keytab` | Service keytab, read-only to the API process user |

Manual verification against a real MIT Kerberos KDC:

1. On the KDC, create a service principal for the Tiqora API host and export
   a keytab:
   ```
   kadmin.local -q "addprinc -randkey HTTP/helpdesk.example.com@EXAMPLE.COM"
   kadmin.local -q "ktadd -k /etc/tiqora/tiqora.keytab HTTP/helpdesk.example.com@EXAMPLE.COM"
   ```
2. Deploy the keytab to the Tiqora API host, owned/readable only by the
   process user, and set `KRB5_KTNAME=/etc/tiqora/tiqora.keytab` +
   `TIQORA_SPNEGO_ENABLED=true`.
3. Reverse proxy must pass the `Authorization` header through unmodified to
   `/api/v1/auth/spnego`.
4. Configure the browser to allow SPNEGO for the site:
   - Firefox: `network.negotiate-auth.trusted-uris` = `helpdesk.example.com`
   - Chrome/Edge (Linux): `--auth-server-allowlist=helpdesk.example.com` or
     the `AuthServerAllowlist` policy.
5. On a domain-joined / `kinit`'d client, `curl --negotiate -u : -c -
   https://helpdesk.example.com/api/v1/auth/spnego` should return a session
   cookie for a user whose Kerberos principal's primary part matches an
   existing `users.login`.
6. Common failure modes: clock skew > 5 min (Kerberos requires tight time
   sync — run NTP/chrony), keytab not readable by the API process user,
   `KRB5_KTNAME` unset (gssapi silently fails to find credentials), and SPN
   mismatch (the keytab principal must match the hostname the browser sees).

## Parallel operation deployment

When co-running with Znuny:

1. Point Tiqora at the **same** database Znuny uses (read/write credentials).
2. Do **not** run `versions_owned` migrations.
3. Install the `TiqoraSync` OPM on Znuny (or lower cache TTLs).
4. Keep Znuny daemon jobs enabled until Phase 4 feature flags say otherwise.
5. Terminate TLS at a reverse proxy; route `/api`, `/mcp`, and static UI paths
   to Tiqora; leave Znuny paths as needed during migration.

## Reverse proxy sketch

```
https://helpdesk.example.com/          → tiqora-api (static + SPA)
https://helpdesk.example.com/api/      → tiqora-api
https://helpdesk.example.com/mcp       → tiqora-mcp
https://znuny.example.com/             → existing Znuny (until cutover)
```

WebSocket/SSE: ensure proxy timeouts allow long-lived MCP SSE and UI event
streams (no aggressive idle kills).

## Observability

- Scrape `GET /metrics` with Prometheus or Zabbix HTTP agent.
- A Zabbix template will live under `deploy/zabbix/` (placeholder for now).
- Ship stdout JSON logs (structlog) to your log stack.

## Backups

- Database: same RPO/RTO as Znuny today (shared DB during parallel operation).
- Meilisearch: rebuildable from DB (prefer rebuild over fragile index backups
  until documented otherwise).
- Redis: sessions are disposable; job queues should use durable Redis config
  once workers are critical path.

## Security checklist (draft)

- [ ] TLS everywhere
- [ ] Strong `MEILI_MASTER_KEY` and DB passwords
- [ ] Network isolation: DB not public
- [ ] MCP API keys rotated and scoped
- [ ] AGPL obligations understood for network-facing deployments
