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

## OIDC / SSO configuration

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

## Kerberos / SPNEGO configuration

Optional. The **production Docker image** already includes the `kerberos`
extra (`gssapi`) and MIT Kerberos runtime libraries; SPNEGO stays inert until
`TIQORA_SPNEGO_ENABLED=true` and a keytab are set. For a local/dev install
without the image, install the extra yourself (`uv sync --extra kerberos`) —
without `gssapi`, `/api/v1/auth/spnego` returns `501`.

| Variable | Example | Notes |
|---|---|---|
| `TIQORA_SPNEGO_ENABLED` | `true` | Off by default |
| `KRB5_KTNAME` | `/etc/tiqora/tiqora.keytab` | Service keytab, read-only to the API process user |

Production SPN (Cygnus): `HTTP/tiqora.cygnusnetworks.de@CYGNUSNETWORKS.DE`.

Docker Compose keytab mount (see `docker-compose.example.yml` and
`docs/deploy/docker-compose.md`):

```yaml
environment:
  TIQORA_SPNEGO_ENABLED: "true"
  KRB5_KTNAME: /etc/tiqora/tiqora.keytab
volumes:
  - ./secrets/tiqora.keytab:/etc/tiqora/tiqora.keytab:ro
  # Optional: pure acceptors usually need no krb5.conf; default_realm can help.
  # - ./secrets/krb5.conf:/etc/krb5.conf:ro
```

Manual verification against a real MIT Kerberos KDC:

1. On the KDC, create a service principal for the Tiqora API host and export
   a keytab:
   ```
   kadmin.local -q "addprinc -randkey HTTP/tiqora.cygnusnetworks.de@CYGNUSNETWORKS.DE"
   kadmin.local -q "ktadd -k /etc/tiqora/tiqora.keytab HTTP/tiqora.cygnusnetworks.de@CYGNUSNETWORKS.DE"
   ```
   (Substitute your own host/realm for non-Cygnus deployments.)
2. Deploy the keytab to the Tiqora API host (or mount it into the container
   read-only), owned/readable only by the process user, and set
   `KRB5_KTNAME=/etc/tiqora/tiqora.keytab` + `TIQORA_SPNEGO_ENABLED=true`.
3. Reverse proxy must forward the `Authorization: Negotiate` header
   unmodified to `/api/v1/auth/spnego` (do not strip it).
4. The browser must reach the host that matches the keytab SPN
   (`tiqora.cygnusnetworks.de` in production). Configure the browser to allow
   SPNEGO for that site:
   - Firefox: `network.negotiate-auth.trusted-uris` = `tiqora.cygnusnetworks.de`
   - Chrome/Edge (Linux): `--auth-server-allowlist=tiqora.cygnusnetworks.de` or
     the `AuthServerAllowlist` policy.
5. On a domain-joined / `kinit`'d client, `curl --negotiate -u : -c -
   https://tiqora.cygnusnetworks.de/api/v1/auth/spnego` should return a session
   cookie for a user whose Kerberos principal's primary part matches an
   existing `users.login` with `sso_eligible` set.
6. Common failure modes: clock skew > 5 min (Kerberos requires tight time
   sync — run NTP/chrony), keytab not readable by the API process user,
   `KRB5_KTNAME` unset (gssapi silently fails to find credentials), SPN
   mismatch (the keytab principal must match the hostname the browser sees),
   and agents not flagged `sso_eligible`.

## LDAP / Active Directory configuration

Optional bind-search-bind auth against an LDAP/AD directory, tried as a
fallback when local password auth fails (mirrors Znuny's chained
`AuthModule::LDAP`/`CustomerAuth::LDAP`). Separate on/off switches and
settings exist for agent login (`users`) and the customer portal
(`customer_user`) — see `tiqora.domain.auth_ldap` /
`tiqora.domain.customer_auth_ldap` / `tiqora.domain._ldap_core`.

**No auto-provisioning in v1**: the LDAP UID resolved by the search must
match an existing, valid local `users.login` (agent) or
`customer_user.login` (portal) row, or the login is rejected. Provision the
account locally first, then point it at LDAP.

Agent auth (`TIQORA_LDAP_*`) — customer portal uses the identical shape
under `TIQORA_CUSTOMER_LDAP_*`:

| Variable | Example | Notes |
|---|---|---|
| `TIQORA_LDAP_ENABLED` | `true` | Off by default |
| `TIQORA_LDAP_HOST` | `ldap.example.com` | |
| `TIQORA_LDAP_PORT` | `389` | `636` for implicit LDAPS |
| `TIQORA_LDAP_USE_SSL` | `false` | Implicit TLS (LDAPS) |
| `TIQORA_LDAP_USE_STARTTLS` | `false` | STARTTLS on a plaintext connection |
| `TIQORA_LDAP_BASE_DN` | `ou=people,dc=example,dc=com` | Search base |
| `TIQORA_LDAP_BIND_DN` | `cn=svc-tiqora,dc=example,dc=com` | Search account; empty = anonymous bind |
| `TIQORA_LDAP_BIND_PASSWORD` | secret | |
| `TIQORA_LDAP_UID_ATTR` | `uid` | `sAMAccountName` for AD |
| `TIQORA_LDAP_ALWAYS_FILTER` | `(objectClass=inetOrgPerson)` | ANDed onto every search |
| `TIQORA_LDAP_GROUP_DN` | `cn=helpdesk,ou=groups,dc=example,dc=com` | Optional group-membership gate |
| `TIQORA_LDAP_ACCESS_ATTR` | `memberUid` | Attribute checked under `GROUP_DN` |
| `TIQORA_LDAP_USER_ATTR` | `DN` | `DN` compares the user's full DN; anything else compares the login |

Simplifications vs. `Kernel::System::Auth::LDAP`: no `Die` (hard-crash on
connect failure), `UserSuffix`, `UserLowerCase`, or per-directory charset
knobs — Tiqora is UTF-8 throughout and treats every connection error as an
auth failure, never a process crash.

## TOTP QR enrollment

`POST /api/v1/auth/totp/enroll` returns the `otpauth://` secret/URI as
before; `GET /api/v1/auth/totp/enroll/qr` renders that same pending
enrollment as an `image/svg+xml` QR code (404 if there is no pending
enrollment). The agent security page (`/agent/security`) uses it directly as
an `<img src>` — the request is cookie-authenticated and same-origin (see
`vite.config.ts`'s `/api` proxy), so no extra client wiring is needed.

## Parallel operation deployment

When co-running with Znuny:

1. Point Tiqora at the **same** database Znuny uses (read/write credentials).
2. Do **not** run `versions_owned` migrations.
3. Install the `TiqoraSync` OPM on Znuny (or lower cache TTLs).
4. Keep Znuny daemon jobs enabled until Tiqora daemon feature flags say otherwise.
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
