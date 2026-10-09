# Deployment

> Tiqora is **not production ready**. Treat this document as a draft of the
> intended deployment shape, not an operational runbook.

## Images

Multi-stage `Dockerfile` produces a single runtime image with the API code and
built frontend static assets. Process role is selected at start:

| Command / env | Process |
|---|---|
| `api` (default) | FastAPI (uvicorn) |
| `worker` | Background worker (Znuny-write poller, indexing, outbox/webhooks, daemon takeovers, background-job runner for exports and the GenericInterface mails (`ArticleSend`, auto responses) etc.; poll interval `TIQORA_JOBS_POLL_INTERVAL_SECONDS`, default 2, range 1-60) |
| `ai-worker` | AI subsystem loop (triage, auto-reply, auto-summary), isolated from the main worker; inert unless `operation_mode=tiqora_primary` and `daemon.ai_worker.enabled` |
| `mcp` | FastMCP server |

Registry targets (CI):

- `ghcr.io/cygnusnetworks/tiqora`
- `docker.io/cygnusnetworks/tiqora`

Built only on release tags (`v*`): `latest`, `stable`, `<major>.<minor>.<patch>`,
`<major>.<minor>`, `sha-<shortsha>`. Day-to-day `main` pushes are not published
to these registries.

## Compose example

See [docker-compose.example.yml](../docker-compose.example.yml) for a commented
deployment skeleton with:

- `tiqora-api`, `tiqora-worker`, `tiqora-ai-worker`, `tiqora-mcp`
- Choice of **PostgreSQL or MariaDB** (comments show both; enable one)
- Redis, Meilisearch
- Optional Mailpit for non-production mail sinks

Copy to `docker-compose.yml`, set secrets via environment or a secrets manager,
and never commit real credentials.

## Required configuration

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@db:5432/tiqora` | Or `mysql+aiomysql://…` |
| `REDIS_URL` | `redis://redis:6379/0` | Sessions, presence, SSE pub/sub, rate limiting |
| `MEILI_URL` | `http://meilisearch:7700` | |
| `MEILI_MASTER_KEY` | strong secret | Production required |
| `TIQORA_SECRET_KEY` | random 32+ bytes | Cookie/session material |
| `TIQORA_LOG_LEVEL` | `INFO` | |
| `TIQORA_CORS_ORIGINS` | `https://helpdesk.example.com` | Comma-separated |
| `TIQORA_SESSION_TTL` | `3600` | Session lifetime in seconds (1h). **Sliding** — renewed on every authenticated request, so effectively the max idle time. SSO/Kerberos agents re-auth transparently on expiry. Also governs customer-portal sessions. |
| `TIQORA_SESSION_ABSOLUTE_TTL` | `43200` | Absolute max session age (12h), independent of the sliding TTL — a session is killed this long after creation even if continuously active, so a stolen token can't be kept alive forever. |
| `TIQORA_TRUSTED_PROXIES` | *(empty)* | Comma-separated reverse-proxy IPs/CIDRs whose `X-Forwarded-For` may be trusted for rate-limit client-IP derivation. Set to your proxy so per-IP login lockout keys on the real client, not the shared proxy IP. |
| `TIQORA_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxy IPs whose `X-Forwarded-For`/`X-Real-IP` uvicorn honours for `request.client.host`. **Required behind a proxy** that is not on `127.0.0.1` (e.g. the Docker gateway), otherwise every request keys on the proxy IP. |
| `TIQORA_PUBLIC_BASE_URL` | `https://helpdesk.example.com` | Public browser URL (no trailing slash) for links in notifications, password-setup mails and the OAuth2 mail callback. Set on API **and** worker. |
| `TIQORA_CSP_ENFORCE` | `false` (dev) / **`true` (production)** | Enforce the SPA Content-Security-Policy. Auto-enabled in production; set `0` to keep report-only. |
| `TIQORA_METRICS_ENABLED` | `true` (dev) / **`false` (production)** | Expose unauthenticated `GET /metrics` on the app port. Off by default in production; set `1` to scrape it (keep it off the public vhost). |
| `TIQORA_PORTAL_ENABLED` | `false` | The customer portal is opt-in: unset/`false` hard-disables it (`/api/portal/*` → 404, `/` → agent login); `true` lets the admin switch decide. |
| `TIQORA_CRYPTO_SMIME_CA_PATH` | *(empty)* | Extra CA bundle for S/MIME chain validation, e.g. a private CA. Signers whose chain validates against neither this, `SMIME::CertPath`, the bundled public roots nor openssl's store are reported `signed_untrusted` (valid signature, untrusted signer) rather than `verified`. |
| `TIQORA_CRYPTO_SMIME_PUBLIC_ROOTS` | *(admin setting, default `true`)* | Trust the bundled Mozilla e-mail roots for inbound S/MIME signatures. See [crypto.md](crypto.md#trust). |
| `TIQORA_CUSTOMER_SMIME_CERT_COLUMN` | *(empty)* | `SMIME::FetchFromCustomer`: `customer_user` column holding the customer's certificate (Znuny `UserSMIMECertificate` map entry). See [crypto.md](crypto.md#smimefetchfromcustomer). |
| `TIQORA_CUSTOMER_LDAP_SMIME_ATTR` / `TIQORA_CUSTOMER_LDAP_EMAIL_ATTR` | `userSMIMECertificate` / `mail` | `SMIME::FetchFromCustomer` from the customer LDAP directory (`TIQORA_CUSTOMER_LDAP_*`): certificate attribute and the attribute matched against the sender address. |
| `TIQORA_STEP_UP_MAX_AGE` | `900` | "Sudo mode" window (seconds): how long after logging in a session may still register or delete a passkey. Past this the agent must sign in again. Only enforced once the account already has a second factor, so first-time and forced enrollment stay frictionless. |
| `TIQORA_COMPAT_WEBSERVICE_RELOAD_SECONDS` | `10` | How often every `tiqora-api` process polls `gi_webservice_config` so GenericInterface webservices added, renamed, edited or disabled in Znuny are served without a restart. |
| `TIQORA_COMPAT_SESSION_WRITE_ZNUNY` | `true` | Compat `SessionCreate` writes Znuny `sessions` rows (valid in Znuny too, Znuny's idle/absolute expiry; expired rows are purged by the worker loop `znuny_session_purge`). `false` keeps Redis tokens. Never writes rows once Tiqora owns the schema. Note Znuny's `SessionCheckRemoteIP` (default `1`): see [compatibility.md](compatibility.md#session-handling). |
| `TIQORA_COMPAT_SESSION_CHECK_REMOTE_IP` | `false` | Bind Znuny session ids presented to the compat GenericInterface to the client IP Znuny recorded (its `SessionCheckRemoteIP`). Off by default: Znuny stored the address **its own** webserver saw, which differs from Tiqora's socket peer behind a reverse proxy, so enabling it there rejects valid sessions. Turn on only when both peers observe the same client address. Idle/absolute session expiry is always enforced regardless of this flag. |
| `TIQORA_TOTP_PENDING_TTL` | `300` | Seconds a pending-2FA session stays valid |
| `TIQORA_TOTP_ISSUER` | `Tiqora` | Shown in authenticator apps |
| `TIQORA_LLM_TIMEOUT` | `180.0` | Per-request HTTP timeout for one LLM chat completion (seconds). Detailed multi-document summaries can run long; raise if you see `LlmTimeoutError`. Default for LLM profiles without their own timeout (Admin → AI → Models). |
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
| `TIQORA_OIDC_BACKCHANNEL_LOGOUT` | `false` | `true` accepts OIDC back-channel logout at `/api/v1/auth/oidc/env-oidc/backchannel-logout` (register that URL with the provider) |

These variables still work unchanged. They are turned into the read-only
identity provider `env-oidc`, shown in the admin UI at
`/admin/identity-providers` (it cannot be edited there). Further OIDC and SAML
providers, per-provider matching, just-in-time accounts and role/group sync
are configured in that UI; see [guide/identity-providers.md](guide/identity-providers.md).

**No auto-provisioning for `env-oidc`**: the claim value must match an
existing `users.login` with `valid_id = 1`, or the login is rejected. Provision
agents in Znuny/Tiqora admin first, then point them at SSO. The agent must be
flagged SSO-eligible, as before.

**The id_token is verified.** Since the identity update every OIDC login
(env and UI-configured) checks the id_token signature against the provider's
JWKS, `iss` against the issuer of the discovery document, `aud`, `exp` and
`nonce`. **Before upgrading, check that the issuer in the discovery document
(`<issuer>/.well-known/openid-configuration`) equals the issuer you configured
and that the `jwks_uri` is reachable from the Tiqora containers** (through the
outbound SSRF guard: private/loopback targets are refused unless the provider
allows internal networks). The JWKS cache is per process.

### Reverse proxy for SSO

Public URL and SAML:

* `TIQORA_PUBLIC_BASE_URL` (API **and** worker) is **required** for SAML and for
  every OIDC provider configured in the UI: redirect URIs, the SAML ACS /
  metadata / logout URLs and the post-logout redirect are derived from it. The
  env OIDC provider keeps working without it as long as
  `TIQORA_OIDC_REDIRECT_URI` is set.
* The image ships `xmlsec1` and the `saml` extra (pysaml2). A custom image
  without them answers SAML logins with `sso_error=saml_unavailable`.
* The reverse proxy must pass SAML ACS POST bodies of up to 1 MB (Tiqora's cap
  is 2 MB per response): nginx `client_max_body_size 2m;` on the `/api/`
  location (the nginx default of 1 MB rejects large signed responses with
  `413`). Do not buffer-reject large headers either (IdP redirects can carry
  long query strings; raise `large_client_header_buffers` if needed).
* The OIDC back-channel logout URL
  (`/api/v1/auth/oidc/<slug>/backchannel-logout`, for `env-oidc`:
  `TIQORA_OIDC_BACKCHANNEL_LOGOUT=true` first) and the SAML single-logout URL
  (`/api/v1/auth/saml/<slug>/sls`) are called **by the IdP**; they must be
  reachable from the IdP without a login wall or IP allow-list that excludes it.
* The worker refreshes SAML IdP metadata URLs daily (04:30 UTC) and keeps the
  last good metadata when a fetch fails.

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

The SPN follows `HTTP/<api-hostname>@<REALM>` — e.g.
`HTTP/tiqora.example.com@EXAMPLE.COM`.

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
   kadmin.local -q "addprinc -randkey HTTP/tiqora.example.com@EXAMPLE.COM"
   kadmin.local -q "ktadd -k /etc/tiqora/tiqora.keytab HTTP/tiqora.example.com@EXAMPLE.COM"
   ```
   (Substitute your own API hostname and Kerberos realm throughout.)
2. Deploy the keytab to the Tiqora API host (or mount it into the container
   read-only), owned/readable only by the process user, and set
   `KRB5_KTNAME=/etc/tiqora/tiqora.keytab` + `TIQORA_SPNEGO_ENABLED=true`.
3. Reverse proxy must forward the `Authorization: Negotiate` header
   unmodified to `/api/v1/auth/spnego` (do not strip it).
4. The browser must reach the host that matches the keytab SPN
   Configure the browser to allow SPNEGO for that site:
   - Firefox: `network.negotiate-auth.trusted-uris` = `tiqora.example.com`
   - Chrome/Edge (Linux): `--auth-server-allowlist=tiqora.example.com` or
     the `AuthServerAllowlist` policy.
5. On a domain-joined / `kinit`'d client, `curl --negotiate -u : -c -
   https://tiqora.example.com/api/v1/auth/spnego` should return a session
   cookie for a user whose Kerberos principal's primary part matches an
   existing `users.login` with `sso_eligible` set.
6. Common failure modes: clock skew > 5 min (Kerberos requires tight time
   sync — run NTP/chrony), keytab not readable by the API process user,
   `KRB5_KTNAME` unset (gssapi silently fails to find credentials), SPN
   mismatch (the keytab principal must match the hostname the browser sees),
   and agents not flagged `sso_eligible`.

**Seamless re-auth on session expiry.** When an agent's (sliding, 1h) session
expires, the SPA sends them to `/login?next=<current path>`; if SPNEGO is
enabled the login page immediately redirects into
`GET /api/v1/auth/spnego?next=<path>`, which re-runs the Negotiate handshake and
302s back to the original page. As long as the Kerberos ticket is still valid the
agent notices nothing. A failed handshake (expired/absent ticket) falls back to
`/login?sso_error=1` — no bare `401`, so the browser never shows a native
credential popup. **SSO is the strong factor**: TOTP/passkey 2FA (including
forced enrollment) is skipped for SSO logins; 2FA enforcement only applies to the
password login path.

## LDAP / Active Directory configuration

Optional bind-search-bind auth against an LDAP/AD directory, tried
after local password auth fails (mirrors Znuny's chained
`AuthModule::LDAP`/`CustomerAuth::LDAP`). Separate on/off switches and
settings exist for agent login (`users`) and the customer portal
(`customer_user`). They appear as the read-only identity providers
`env-ldap` / `env-customer-ldap` — see `tiqora.identity.ldap_login` and
`tiqora.domain._ldap_core`. Further directories with just-in-time
accounts and role/group sync (Znuny AuthSync) are configured as identity
providers in the admin UI and are tried in their sort order; the first
directory that accepts the password wins.

**No auto-provisioning for the env directories**: the LDAP UID resolved by
the search must match an existing, valid local `users.login` (agent) or
`customer_user.login` (portal) row, or the login is rejected. After the
first login the account is linked to the directory entry's stable id
(`objectGUID` / `entryUUID`), so a later rename of the local login keeps
working.

The stored link id for Active Directory is the lowercase hex of the
**raw** 16 `objectGUID` bytes as the directory sends them (the first three
GUID fields little-endian) — not the canonical GUID string that AD tools
show. Example: `{01020304-0506-0708-090a-0b0c0d0e0f10}` is stored as
`0403020106050807090a0b0c0d0e0f10` (Python: `uuid.UUID(...).bytes_le.hex()`).
Keep this in mind when comparing links in the admin list with AD.

Connections use a 5 s connect and 10 s receive timeout, never follow
referrals, and one password login spends at most 20 s across all
directories (no LDAP operation starts after that, and socket timeouts are
capped at the time left). StartTLS is negotiated before any password is
sent. The S/MIME certificate fetch from the customer directory
(`TIQORA_CUSTOMER_LDAP_*`) uses the same connection settings, including
`TIQORA_CUSTOMER_LDAP_TLS_VERIFY`.

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
| `TIQORA_LDAP_TLS_VERIFY` | `false` (default) | Verify the directory's certificate (system CAs, host name) for LDAPS/StartTLS. **Defaults to `false` only for compatibility with existing installs; a warning is logged at startup while it is off.** Set it to `true`. |
| `TIQORA_CUSTOMER_LDAP_TLS_VERIFY` | `false` (default) | The same for the customer-portal directory (`TIQORA_CUSTOMER_LDAP_*`); same startup warning while it is off. |

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

- Scrape `GET /metrics` with Prometheus or Zabbix HTTP agent (requires
  `TIQORA_METRICS_ENABLED=1` in production). It serves the API process's
  own metrics only; `tiqora-worker` has no HTTP port, so its counters and
  poller-lag gauges are not scrapeable. Watch the worker through the
  per-service status keys (Admin → Dienste, `daemon.<slug>.status.*` in
  `tiqora_settings`) and its container healthcheck (heartbeat file).
- A Zabbix template will live under `deploy/zabbix/` (placeholder for now).
- Ship stdout JSON logs (structlog) to your log stack.

## Backups

- Database: same RPO/RTO as Znuny today (shared DB during parallel operation).
- Meilisearch: rebuildable from DB (prefer rebuild over fragile index backups
  until documented otherwise).
- Redis: holds sessions, rate-limit counters, OAuth2 state, presence and
  pub/sub only — there is no job queue in Redis, so losing it logs users out
  but loses no ticket data.

### Meilisearch document-schema changes

When filterable/sortable fields are **added to indexed documents** (not only
index settings), `ensure_index()` alone is not enough: it updates Meili
settings but does **not** rewrite existing documents. Documents written
before the change lack the new fields, so filters on those fields silently
return zero hits.

**After deploy of such a change, run a full rebuild:**

```bash
# full rebuild (do not resume a partial watermark)
tiqora index rebuild --no-resume
```

Known examples:

| Change | Fields | Rebuild required? |
|--------|--------|-------------------|
| Faceted date filters (search facets PR) | `created_ts`, `changed_ts` on ticket docs | **Yes** — date filters exclude pre-change docs until rebuild |
| Archive filter (archived tickets hidden from search) | `archive_flag` on ticket docs | **Yes** — the default `archive_flag = 0` filter excludes pre-change docs (they lack the field), hiding unarchived tickets from search until rebuild |

## Security checklist (draft)

- [ ] TLS everywhere
- [ ] Strong `MEILI_MASTER_KEY` and DB passwords
- [ ] Network isolation: DB not public
- [ ] MCP API keys rotated and scoped
- [ ] AGPL obligations understood for network-facing deployments
