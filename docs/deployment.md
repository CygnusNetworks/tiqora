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
| `tiqora <command…>` | One-off CLI run through the entrypoint (`tiqora migrate upgrade`, `tiqora plugins list`, `tiqora plugins bake …`); no server is started |

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
| `REDIS_URL` | `redis://redis:6379/0` | Sessions, presence, SSE pub/sub, rate limiting, report result cache |
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
| `TIQORA_CHAT_BOT_CONCURRENCY` | `4` | `ai-worker` only: how many web chat assistant turns run at the same time (1-32). |
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

## Erweiterungen einbauen (Plugins)

Plugins (see [plugins.md](plugins.md)) are either **baked into the image**
(this section) or installed as `.tqplugin` packages in the web UI (next
section, "Erweiterungen zur Laufzeit installieren"). There is no runtime
`pip install`, and the baked image ships without `pip`; runtime packages must
be pure Python with dependencies already in the image.

### Bake workflow

1. Write `plugins.txt` (pinned names or built wheels, optional
   `--hash=sha256:…` per line, at most one `--index-url https://…`; git
   sources, `--extra-index-url` and non-https wheel addresses are rejected). Use hashes in production:
   once any line has a hash pip requires hashes for the transitive
   dependencies too.
2. Generate the build context with the image you run today:

   ```bash
   docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
     ghcr.io/cygnusnetworks/tiqora:<version> \
     tiqora plugins bake /work/plugins.txt --output /work/build
   ```

   (`--base-image <image>` when the image has no release version. Without
   `--user` the command runs as the image user `tiqora`, which usually cannot
   write to the bind-mounted directory: "Fehler: Permission denied".)
3. `docker build -t tiqora-with-plugins build/`. The last build step runs
   `tiqora plugins verify --import --strict`: a plugin that is incomplete
   (for example `web/index.js` dropped from the wheel by `.gitignore`), cannot
   be imported, or pins `tiqora`/`tiqora-sdk` fails the build.
4. Use the new image for `tiqora-api`, `tiqora-worker`, `tiqora-mcp` **and** `tiqora-ai-worker`.
   The `ai-worker` loads plugins too (since SDK 0.2.0): use the same image and the same plugin volume as for the other roles.

### Restart order

Roll out in this order: `tiqora-api` first, then `tiqora-worker`,
`tiqora-mcp` and `tiqora-ai-worker`. The first release with plugins adds the `tiqora_plugin*` tables
through the API's migration at start (`TIQORA_RUN_MIGRATIONS=1`). A worker,
MCP or ai-worker process that starts earlier boots core-only, logs `plugins_boot_failed`
and retries loading plugins every 60 seconds for up to 30 minutes; after that
it needs a restart. Run exactly **one** `tiqora-worker` replica: with two,
every plugin task runs twice and event subscribers race for their cursors.
Switching a plugin on or off takes effect after a restart of all of them (the `tiqora-ai-worker` too, where you run one).

### Environment variables

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_PLUGINS_SAFE_MODE` | `false` | `1` imports no plugin code in api/worker/mcp/ai-worker. The admin page still lists plugins (without importing them). |
| `TIQORA_PLUGINS_DISABLED` | *(empty)* | Comma-separated plugin ids that are not loaded, whatever their switch says. |
| `TIQORA_PLUGINS_AUTO_ENABLE` | `true` | Newly discovered plugins start switched on; `0` requires a click in the admin page. |
| `TIQORA_PLUGINS_IMPORT_TIMEOUT_SECONDS` | `20` (1–300) | Import limit per plugin. A timed-out import keeps running in its daemon thread and its module-level side effects can still happen; an import that holds the GIL in C cannot be timed out at all (use safe mode or `TIQORA_PLUGINS_DISABLED`). |
| `TIQORA_PLUGINS_BOOT_BUDGET_SECONDS` | `60` (1–3600) | Total import time of all plugins per process start; plugins after it are marked failed without being imported. |
| `TIQORA_PLUGIN_EVENTS_INTERVAL_SECONDS` | `5` (5–300) | Tick of the event delivery loop (`plugin_events`). |
| `TIQORA_PLUGIN_EVENTS_GAP_HOLD_SECONDS` | `60` (5–3600) | How long a missing `ticket_history` id holds delivery back before it is skipped. Run Galera with a single write node: events from rows committed late on another write node can be missed. |
| `TIQORA_PLUGIN_TASKS_INTERVAL_SECONDS` | `15` (5–300) | Tick of the scheduled-task loop (`plugin_tasks`). |

Plugin handler and task timeouts are cooperative: blocking code in a plugin
freezes the process, and a running task holds the `plugin_tasks` loop for up to
300 seconds. Browser safe mode (`?plugins=off`) loads no plugin code in that
tab. The SPA CSP (`script-src 'self'`, `connect-src 'self'`) is the only
browser-side control for plugin frontends and is enforced only in production
(`TIQORA_CSP_ENFORCE`).

## Erweiterungen zur Laufzeit installieren

Besides baking (above), admins can install plugin packages (`.tqplugin`) in the
web UI (Admin, Erweiterungen) or fetch them from a package source. The package
itself is stored in the **database**; the plugins directory only holds the
unpacked working copy that each process re-creates from it. The image ships
`/var/lib/tiqora/plugins` and `/var/lib/tiqora/plugin-repository`, owned by
user `tiqora`, so this works without any compose change. The shipped compose
examples additionally mount a named volume `tiqora_plugins` on `tiqora-api`,
`tiqora-worker`, `tiqora-ai-worker` and `tiqora-mcp`.

### Volume

- One shared volume or one per container both work, because the database holds
  the packages (every process unpacks them itself).
- A **bind mount** keeps the host's ownership, so the host directory must
  belong to uid 10001: `chown -R 10001:10001 /srv/tiqora/plugins`, mounted at
  `/var/lib/tiqora/plugins`.
- Read-only root file system (`read_only: true`): mount the plugins volume
  read-write, or set `TIQORA_PLUGINS_RUNTIME_INSTALL=0` and bake plugins into
  the image instead.

### Start order

`tiqora-worker`, `tiqora-ai-worker` and `tiqora-mcp` must start **after** the
api is healthy: the api runs the plugin migrations at its start, and a
process that boots earlier keeps those plugins in the failed state until its
next restart. The compose examples therefore use
`depends_on: tiqora-api: condition: service_healthy`. Without that (other
orchestrators), restart these three once after the first start that applied
new plugin migrations. Kubernetes: gate the pods on the api with an init
container that waits for `/health`, or roll them out after the api.

### Restart policy and exit code 75

After an install, update or removal the processes restart in a coordinated way:
each one leaves with **exit code 75** and relies on the container runtime to
start it again. Every service needs `restart: unless-stopped` (or `always`;
Kubernetes: the default `restartPolicy: Always`). On platforms without
automatic restarts set `TIQORA_PLUGINS_COORDINATED_RESTART=0`; the admin page
then shows that a restart is pending and you restart the services yourself.
Running plugin installs hold the restart back, so a half-applied database
change is never interrupted.

### Environment variables

| Variable | Default | Notes |
|---|---|---|
| `TIQORA_PLUGINS_DIR` | `/var/lib/tiqora/plugins` | Working copy of installed packages. Must be writable for uid 10001. |
| `TIQORA_PLUGINS_RUNTIME_INSTALL` | `true` | `0` switches the install, update and removal endpoints off (bake only). |
| `TIQORA_PLUGINS_ALLOW_UNSIGNED` | `false` | Allow installing packages without a trusted signature. Admins can sign their own packages, so this gate prevents accidents, not abuse. |
| `TIQORA_PLUGINS_TRUSTED_KEYS` | *(empty)* | Additional trusted publisher public keys (`ed25519:<base64>`, comma-separated). |
| `TIQORA_PLUGINS_MAX_PACKAGE_MB` | `15` (1-25) | Largest accepted package. Must fit the database setting `max_allowed_packet` (MariaDB); otherwise the upload is refused with a hint to raise it or lower this value. |
| `TIQORA_PLUGINS_COORDINATED_RESTART` | `true` | `0` for platforms without automatic restarts (see above). |
| `TIQORA_PLUGINS_RESTART_TIMEOUT_SECONDS` | `180` (30-1800) | How long the coordinated restart waits for the processes before it fails. |
| `TIQORA_PLUGINS_MIGRATE_TIMEOUT_SECONDS` | `300` (1-3600) | Limit per plugin database migration (run in a subprocess). |
| `TIQORA_PLUGIN_REPOSITORIES` | *(empty)* | JSON list of package sources managed by the deployment (example below). |
| `TIQORA_PLUGIN_REPOSITORY_FILE_ROOT` | `/var/lib/tiqora/plugin-repository` | `file://` sources must lie below this directory. |
| `TIQORA_PLUGIN_REPOSITORIES_INTERVAL_SECONDS` | `21600` | How often the daemon refreshes the package sources. |

`TIQORA_PLUGIN_REPOSITORIES` example (`public_key` is optional; without it the
index cannot be verified and its packages are treated as unverified):

```bash
TIQORA_PLUGIN_REPOSITORIES='[
  {"name": "Example Plugins", "url": "https://plugins.example.org/index.json",
   "public_key": "ed25519:AAAA...base64..."},
  {"name": "Offline mirror", "url": "file:///var/lib/tiqora/plugin-repository/index.json"}
]'
```

Environment-managed sources cannot be edited or deleted in the web UI. A new
plugin that creates tables is refused when another plugin already uses its
short name; removing a plugin offers "Daten behalten" or a backup before the
tables are dropped.

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

### SCIM provisioning

Identity providers can push accounts and groups to `/scim/v2` (guide:
[guide/identity-providers.md](guide/identity-providers.md#bereitstellung-mit-scim)). **Deploy blockers**
for the Puppet-managed nginx (not inspectable from this repository, so a checklist item):

```nginx
location /scim/ {
    proxy_pass http://tiqora-api;   # same upstream as /api/
    # same proxy_set_header lines as the /api/ block: the per-IP lockout and the
    # Location/meta.location fallback (no TIQORA_PUBLIC_BASE_URL) read them
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 2m;
    proxy_read_timeout 90s;
}
```

Without this block `/scim/` is not reachable (the IdP's "test connection" fails with 404 or an HTML page).
`TIQORA_TRUSTED_PROXIES` (and `TIQORA_FORWARDED_ALLOW_IPS`) must be set as well: the failed-token lockout is
per client IP, so behind an unconfigured proxy every bad token counts against the proxy address.
A request with a valid token is never subject to that lockout.

| Variable | Default | Meaning |
|---|---|---|
| `TIQORA_SCIM_RATE_LIMIT_ENABLED` | `true` | per-token request throttle |
| `TIQORA_SCIM_RATE_LIMIT_MAX` / `TIQORA_SCIM_RATE_LIMIT_WINDOW` | `600` / `60` | requests per window and token |
| `TIQORA_SCIM_AUTH_FAIL_MAX` / `TIQORA_SCIM_AUTH_FAIL_WINDOW` | `10` / `600` | failed token attempts per IP and window before the lockout |
| `TIQORA_SCIM_AUTH_LOCKOUT` | `900` | lockout seconds (429 for bad tokens only) |
| `TIQORA_SCIM_MAX_BODY_BYTES` | `2097152` | largest accepted request body |
| `TIQORA_SCIM_MAX_MEMBERS` | `5000` | members a group request may carry |
| `TIQORA_SCIM_ROLE_SYNC_INTERVAL` | `15` | seconds between role-sync ticks (daemon `scim_role_sync`) |
| `TIQORA_SCIM_EVENT_RETENTION_DAYS` | `180` | audit retention (daemon `scim_events_purge`) |

Release checklist: the `/scim/` nginx block exists, `TIQORA_TRUSTED_PROXIES` is set, `TIQORA_PUBLIC_BASE_URL`
is set. Each SCIM request is bounded work on `tiqora_scim_*` tables; Znuny role writes happen in the
`scim_role_sync` worker, so no request approaches the 90 s limit.

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

## GenericInterface requester

Outgoing webservice calls (see [compatibility.md](compatibility.md#requester-and-invokers-tiqora-calls-external-systems))
run in the **worker**. They are off until the takeover flag is set
([parallel-operation.md](parallel-operation.md#taking-over-genericinterface-invokers)).

| Variable | Default | Meaning |
|---|---|---|
| `TIQORA_GI_CERT_DIRS` | *(empty)* | Colon-separated directories from which client certificates, keys and CA files named in a webservice config (`SSLCertificate`, `SSLKey`, `SSLCAFile`, `SSLCADir`, and for REST `X509CertFile`, `X509KeyFile`, `X509CAFile`) may be read. Empty = client certificates are refused. |
| `TIQORA_GI_MAX_TIMEOUT_SECONDS` | `300` | Upper limit (1-900) for a webservice's `Timeout`; a larger value in the YAML is cut to it. |
| `TIQORA_GI_MAX_RESPONSE_BYTES` | `20971520` | Largest response body (bytes) the requester reads; more is an error. |
| `TIQORA_GI_REQUESTER_INTERVAL` | `5` | Seconds between two ticks of the event follower (the Dienste page can override it). |

Further knobs: `TIQORA_GI_REQUESTER_GAP_HOLD_SECONDS` (default 30, how long a gap in the history
ids is held back) and `TIQORA_GI_DEBUG_MAX_CONTENT_BYTES` (default 204800, longest debugger
line). Set the variables on the **worker**.

**Mounting Znuny's certificate directory.** If the webservice YAML points at files below
Znuny's directories (for example `/opt/znuny/var/certificates/`), mount that directory
**read-only** into the worker container and list it in `TIQORA_GI_CERT_DIRS`:

```yaml
worker:
  environment:
    TIQORA_GI_CERT_DIRS: /znuny-certs
  volumes:
    - /opt/znuny/var/certificates:/znuny-certs:ro
```

The path in the YAML must then be the path inside the container (or be re-written to it); a
file outside the listed directories is refused with a plain error message in the debugger.

## Reporting data

The worker service `report_facts` (loop interval `TIQORA_REPORT_FACTS_INTERVAL`
seconds, default 60; switch `daemon.report_facts.enabled` in Admin, Dienste =
services) keeps the report fact tables, the dirty-ticket queue and the daily
snapshots current. Run **one** worker. On the first start after the upgrade the
worker enqueues a **backfill** that builds the facts from the whole
`ticket_history`; large installations need minutes to hours. Reports show a hint
and their numbers are incomplete until it has finished. Operate it from the CLI:

```sh
tiqora report backfill [--since YYYY-MM-DD]   # start or resume; --since starts at the first ticket created on/after that date
tiqora report status                          # state of the report data as JSON
```

The admin page for report data (Admin, Berichte = reports) shows the state and
can rebuild everything; during a rebuild reports are incomplete again. Redis
serves as the report result cache (keys `tiqora:report:v1:*`, TTL
`reporting.cache_seconds`, default 300, plus the counter
`tiqora:report:generation`); if Redis is down reports still run, uncached. Every
new backfill (first build, admin rebuild, CLI) and every GDPR erasure or customer
anonymisation invalidates the whole cache. Reports run inline for at most 75 s,
below nginx's 90 s `/api/` timeout; larger ones run as background jobs.
Cancelling a report job ends it within about a second and frees its slot, but
the database statement it was running is only cut off on the client side: the
server may keep executing it for up to `reporting.query_timeout_seconds`
(default 60).

After an upgrade that changes the report fact version, the worker rebuilds the
facts and events on its own, but stored daily snapshots (backlog history) are
only rewritten for the last two days: run an admin rebuild (Admin,
Berichtsdaten) to recompute the older history as well.

The first backfill (and every admin rebuild) runs as a background job in the
default lane and occupies one of its `jobs.max_concurrency` slots (default 2) for
minutes to hours; report runs, report exports and ticket exports share the
remaining slot meanwhile.

The live `open_now` reports and the `escalated` count read
`ticket.escalation_time`, which Tiqora's `escalation` service keeps current while
Znuny's daemon is stopped; with that service off, "escalated" shows 0.

Changes that write no `ticket_history` row are not picked up: calendar/SLA/queue
escalation settings (working minutes stay as built), a change of
`reporting.time_zone` (earlier daily snapshots keep the old zone), and direct SQL.
Rebuild the report data afterwards.


### Report builder and saved searches

No new service, container or environment variable: saved reports, saved searches
and the Znuny profile import run inside the API (the 90-day purge of deleted reports
runs in the existing worker). The Alembic migration `20261010_0068` adds the
`tiqora_report*` and `tiqora_saved_search*` tables (`tiqora migrate upgrade`). Nothing needs
configuring; the existing `reporting.access` setting also decides who may save
and share: `all_agents` lets every agent save private reports and see shared ones,
sharing needs `rw` on the Znuny `stats` group (or admin); `stats_group` requires `ro`
on that group to view and `rw` to save. Long runs (builder preview, saved report
runs) use the job system, so the same one-worker and 90 s nginx notes apply.

### PDF reports and scheduled delivery

- **Image.** PDF rendering uses WeasyPrint (Python dependency `weasyprint>=63,<71`),
  which needs native libraries. The runtime image installs `libpango-1.0-0`,
  `libpangoft2-1.0-0`, `libharfbuzz0b`, `libharfbuzz-subset0`, `libfontconfig1` and
  `fonts-dejavu-core` (the PDF font, also for umlauts); expect a noticeable size
  increase (Pango, HarfBuzz and their dependencies; compare `docker images` before and
  after on your platform). Custom images need
  the same packages; CI sets `TIQORA_REQUIRE_PDF=1` so a missing library fails the
  tests instead of skipping them.
- **Worker services.** `report_scheduler` (queues due deliveries, interval
  `TIQORA_REPORT_SCHEDULER_INTERVAL`, default 60 s, 15 to 3600) and `report_runs_purge`
  (run-history retention); switches `daemon.report_scheduler.enabled` and
  `daemon.report_runs_purge.enabled` (default on, they only write `tiqora_*` tables),
  visible on the Admin Dienste page. Run one worker; deliveries are jobs in the default
  lane and use one of its slots while rendering.
- **Sender address.** A scheduled mail needs a usable sender: the Tiqora setting
  `notification.sender_email`, else Znuny's `NotificationSenderEmail` when it is a real
  address (the stock `znuny@<fqdn>` placeholder is not). Without one every run fails
  with "Kein Absender für Berichte eingerichtet". Outbound SMTP is the same as for
  article mails.
- **Link in mails.** Set `TIQORA_PUBLIC_BASE_URL` (for example `https://tiqora.example.org`);
  the footer links to the schedule. Empty means no link.
- **Settings** (Admin, Berichte): `reporting.schedule_external_recipients` (default off:
  only agents, groups and roles), `schedule_max_recipients` (50),
  `run_retention_days` (90, max 365), `pdf_row_limit` (1000 per report),
  `delivery_max_attachment_mb` (15).
- **Znuny stats import** needs no configuration; it reads `xml_storage` read-only and runs
  inline within the 75 s budget.
- **Migration.** `20261010_0074` adds the schedule and run tables (`tiqora migrate upgrade`).

## Upgrading to v0.19.0 (wave 4)

Operator notes for the release that adds the ITSM core, the CMDB, the ITSM importer, SCIM 2.0 and the
plugin extension points (release notes: [releases/v0.19.0.md](releases/v0.19.0.md)). Work through the
list before rolling out; every item has a failure mode that is silent until somebody needs the feature.

1. **nginx: add `location /scim/`.** Identity providers push to `/scim/v2`, which is **not** under `/api/`.
   A reverse proxy that lists its locations explicitly (the Puppet-managed nginx in front of production
   does) answers 404 or an HTML page there until a `/scim/` block proxies to `tiqora-api`, exactly like
   `/api/`. The block, `TIQORA_TRUSTED_PROXIES` and `TIQORA_PUBLIC_BASE_URL` are listed under
   [SCIM provisioning](#scim-provisioning). A proxy that sends `/` to the api (the example in
   [deploy/docker-compose.md](deploy/docker-compose.md#nginx-example)) needs no change.
2. **Roll out `tiqora-ai-worker` together with api, worker and mcp.** All four roles load the same code: the
   ai-worker now hosts plugins (SDK 0.2.0) and runs the web chat assistant against the new schema. Use the same
   image (and, with plugins, the same plugin image and volume) for all four and restart the ai-worker in the
   same step. An ai-worker left on the old image does not load plugins and may fail against the new tables.
   Order: `tiqora-api` first (it migrates), then worker, mcp and ai-worker.
3. **Compose: wait for a healthy api.** Only the api container runs the migrations (and, once plugin
   installation from packages is used, the migrations of plugins). Worker, mcp and ai-worker must therefore
   start after the api is *healthy*, not merely started:

   ```yaml
   tiqora-worker:      # and tiqora-ai-worker, tiqora-mcp
     depends_on:
       tiqora-api:
         condition: service_healthy
   ```

   The compose examples in this repository ship this. The production compose file lives outside the
   repository and needs the change by hand. Without it a role that boots before the migration finished keeps
   a plugin with pending database changes in the state "failed" until its next restart (the coordinated
   restart after a runtime install covers that case; a plain stack start does not).
4. **Plugin packages and `max_allowed_packet`.** Plugin packages uploaded in the admin UI or fetched from a
   package repository are stored in the database. `TIQORA_PLUGINS_MAX_PACKAGE_MB` (default 15, maximum 25) is the
   size limit per package; MariaDB's `max_allowed_packet` must be **larger** than that limit, or the upload
   fails with "Paket zu groß für die Datenbank-Einstellung max_allowed_packet". Either raise
   `max_allowed_packet` (for example to 32M) or lower `TIQORA_PLUGINS_MAX_PACKAGE_MB`. Check with
   `SHOW VARIABLES LIKE 'max_allowed_packet'`. nginx's body limit for `/api/` has to be above the package limit as well.
   Installation from packages is only possible in the browser session of an admin (API keys are refused) and is
   switched off in read-only containers; baking plugins into the image stays supported.
5. **Migrations 0077 to 0082.** `20261010_0077` (ITSM link types), `0078` (ITSM core), `0079` (CMDB),
   `0080` (SCIM; also adds columns to `tiqora_identity_provider`), `0081` (ITSM import) and `0082` (plugin
   packages and repositories). They only create `tiqora_*` tables or add columns to existing `tiqora_*` tables;
   no Znuny table is altered, indexed or backfilled. The new tables start empty apart from a few seeded catalog
   and link-type rows. The run takes seconds. Znuny keeps running during the upgrade
   and stays unchanged; the migration is additive, so rolling back the image leaves unused tables behind
   without harm. The API entrypoint runs them (`TIQORA_RUN_MIGRATIONS=1`); with `TIQORA_RUN_MIGRATIONS=0` run
   `tiqora migrate upgrade` once before starting the roles.
6. **ITSM mode is detected once.** On the first start after the migration Tiqora looks in the Znuny database
   for the tables `general_catalog` **and** `cip_allocate` (the ITSM core of Znuny). Found: mode **Znuny führt
   das ITSM** (Mode A, read-only). Not found: mode **Tiqora führt das ITSM** (Mode B). The result is stored in
   `itsm.mode` (source "automatisch erkannt") and never re-detected. A site without Znuny ITSM tables, such as
   production with Znuny 6.5 and `Ticket::Type=0`, `Ticket::Service=0`, therefore starts in Mode B. The mode can be switched off in Admin, ITSM. See
   [itsm.md](itsm.md#wie-der-modus-gewählt-wird). The detection lives in `itsm/mode.py` (`ZNUNY_ITSM_MARKER_TABLES`)
   and is also documented for the importer in [itsm-import.md](itsm-import.md).
7. **Znuny add-on `TiqoraITSM` 1.0.0** (German only) is optional and only for sites whose CIs moved to Tiqora
   through the importer and that keep Znuny agents working (Znuny 6.0 to 7.3). Install the `.opm` from
   `packages/znuny-addon/TiqoraITSM` in the Znuny package manager *after* the switch; it is inert before.
   See [itsm-import.md](itsm-import.md).
8. **Check afterwards.** `tiqora migrate current` shows `20261010_0082 (head)`. `GET /scim/v2/ServiceProviderConfig`
   with a SCIM token answers 200 through the public host name (not 404). Admin, Erweiterungen lists all plugins
   in "Aktiv" on all four processes. Admin, ITSM shows the stored mode.

## Reverse proxy sketch

```
https://helpdesk.example.com/          → tiqora-api (static + SPA)
https://helpdesk.example.com/api/      → tiqora-api
https://helpdesk.example.com/scim/     → tiqora-api (SCIM provisioning, v0.19.0; see "Upgrading to v0.19.0")
https://helpdesk.example.com/mcp       → tiqora-mcp
https://znuny.example.com/             → existing Znuny (until cutover)
```

WebSocket/SSE: ensure proxy timeouts allow long-lived MCP SSE and UI event
streams (no aggressive idle kills).

## Wallboard links

Dashboards can be shown on screens without a login (`/wallboard#<token>`, see
[dashboards.md](dashboards.md#wallboard-und-tv-modus)). Nothing needs to be configured, and nginx needs no
change: the secret is a URL fragment (never sent to a server) that the page forwards in the
`X-Tiqora-Kiosk` header. The links need Redis (data cache and rate limiting). Behind a reverse proxy set
`TIQORA_TRUSTED_PROXIES` and `TIQORA_FORWARDED_ALLOW_IPS` as for the web chat; otherwise every screen
shares one address and one rate-limit bucket (240 requests per minute). Report tiles on a wallboard need
the report result cache, i.e. Redis as well.

## Web chat

The web chat (see the [admin and agent guide](guide/webchat.md)) is served by `tiqora-api` itself: the loader
and the iframe document under `/chat/v1/`, the public visitor API under `/api/chat/v1/`. No nginx change is
needed for routing, because `/chat/` and `/api/chat/` go to `tiqora-api` like the rest of the app. The
`tiqora-worker` runs the chat sweep (waiting timeout, idle chats, retention).

### Chat assistant and website sign-in

The AI first line of the web chat ([setup](guide/webchat.md#5-ki-assistent-einrichten)) runs in the
**`tiqora-ai-worker`**, not in `tiqora-api`. It needs no new service and no new proxy rule, but:

- **The AI worker must run** and be switched on (`daemon.ai_worker.enabled`; while it is off the bot loop neither
  consumes the queue nor refreshes the alive key). The worker consumes the queue `tiqora:chat:bot:queue` (Redis
  list) and rescans the database every 15 s for chats whose last visitor line is unanswered, so a lost wake-up
  costs at most 15 s. It refreshes `tiqora:chat:bot:alive` (TTL 30 s); if it stops, new chats skip the assistant and
  go to humans (the admin editor shows `worker_down`); a chat already with the assistant whose visitor line has
  waited more than 60 s is handed to the team by the chat sweep in `tiqora-worker` (reason `unavailable`, or ended
  with `no_agent` when nobody is there). Redis must be reachable by both `tiqora-api` and `tiqora-ai-worker`.
- **Run exactly one `tiqora-ai-worker`.** A chat turn is leased for 180 s; a second replica would take over a turn
  that runs longer (slow model times several tool rounds) and both would answer the same chat.
- **`TIQORA_CHAT_BOT_CONCURRENCY`** (default `4`, 1-32): assistant turns in parallel per `ai-worker` process. A turn
  holds one LLM call (up to `TIQORA_LLM_TIMEOUT`); raise it only with the provider's rate limit in mind.
- **The queue policy decides.** The topic's queue needs auto-reply, an AI service user and a usable model (see the
  guide). A dedicated chat queue is recommended.
- **Website sign-in secret.** The secret is stored encrypted in the database (keyed by `TIQORA_SECRET_KEY`, like the
  other stored secrets) and cached, still encrypted, for 10 s per API process: a rotated or removed secret stops
  working on every replica within 10 s. There is no environment variable for it. If you ever change
  `TIQORA_SECRET_KEY`, the stored secret can no longer be decrypted and website sign-in stops (visitors fall back to
  the pre-chat form); generate a new secret in the admin editor. Do not log the signed token.
- **Portal chat.** `show_on_portal` additionally needs `TIQORA_PORTAL_ENABLED=true`. The portal page calls the chat
  from Tiqora's own origin and sends its session cookie only to `/api/chat/v1/identity/portal`; set
  `TIQORA_PUBLIC_BASE_URL` so the origin check recognises it.

**Post-deploy check (assistant).** Use a test chat with a topic set to **KI antwortet zuerst** (Admin, **Web-Chat**,
tab **KI**, status **Bereit**). Open the embedded chat and write a question: the assistant must answer within 15 s
(the model's time plus at most one rescan). Then click **Mit einem Menschen sprechen**: the chat must appear under
**Wartende Chats** and ring. Last, stop `tiqora-ai-worker`, wait 30 s and check that the status shows `worker_down`
and a new chat goes straight to the waiting list. The image carries the widget: `docker run --rm --entrypoint ls
<image> /app/webchat/dist` lists `loader.js`.

### Required behind a reverse proxy

| Variable | Why it is REQUIRED |
|---|---|
| `TIQORA_PUBLIC_BASE_URL` (e.g. `https://helpdesk.example.org`) | Tiqora derives its own origin from it. Without it the origin comes from the `Host` header, which behind a TLS-terminating proxy is `http://helpdesk.example.org`: every chat start is then refused as cross-origin, the admin preview is refused, and the iframe's `connect-src` is built from whatever `Host` a request carries. Set it in production. |
| `TIQORA_TRUSTED_PROXIES` and `TIQORA_FORWARDED_ALLOW_IPS` | The proxy addresses. Without them the real visitor address is not seen, so every visitor shares the proxy's rate-limit and stream-cap buckets (a handful of visitors then block everyone). |
| `TIQORA_SESSION_COOKIE_SAMESITE` stays `lax` (default) or `strict`, never `none` | The chat frame is served from the same origin as the agent app. With `SameSite=None` an agent's or admin's session cookie would also travel with requests made from inside the frame on a customer's site, and the frame CSP's `connect-src <own>/api/chat/v1/` would be the only barrier left against a frame XSS calling `/api/v1` as that agent (for example during the admin preview). |

Optional:

| Variable | Default | Meaning |
|---|---|---|
| `TIQORA_CHAT_MAX_STREAMS` | `500` | Process-wide cap on open visitor chat streams. The agents' own event stream (`/api/v1/events`) is not counted, but it also holds a connection and a file descriptor. Clamped to the file-descriptor limit minus 256. Beyond it new visitor streams get `503` + `Retry-After`. |
| `TIQORA_WEBCHAT_DIST_DIR` | `/app/webchat/dist` | Where the built widget lives (set by the image). |
| `TIQORA_WEBCHAT_SWEEP_INTERVAL` | `15` | Sweep interval in seconds. |

Other limits are fixed: 6 concurrent streams per visitor address, 3 per chat (the oldest is dropped), a stream
lives at most 30 minutes (the widget reconnects and resumes with `Last-Event-ID`).

### Reverse proxy notes

- **Streams.** The SSE responses carry `X-Accel-Buffering: no`, which switches nginx buffering off for them;
  a separate `proxy_buffering off` is not needed. The server sends a heartbeat every 25 s, so a
  `proxy_read_timeout` of 90 s (the `/api/` default in our Puppet-managed nginx) is enough.
- **`X-Frame-Options`.** The chat frame (`/chat/v1/frame`) must be embeddable on the customers' websites; it sets
  its own `Content-Security-Policy: frame-ancestors ...` from the chat's allowed websites. If the proxy adds
  `X-Frame-Options` (or a blanket `frame-ancestors`) to all responses, exempt `/chat/v1/frame`, otherwise the
  browser refuses the iframe on every website.
- **Uploads.** Visitors upload up to the chat's limit (default 10 MiB, at most the global attachment limit).
  Raise nginx `client_max_body_size` for `/api/chat/` to at least that size plus overhead (for example `12m`);
  the default of 1 MiB would answer `413` before Tiqora sees the request.

### Post-deploy check (run once on production)

Replace `<k>` by the public key of a chat (Admin, **Web-Chat**, tab **Einbetten**) and use one of its allowed
websites as `origin`. `curl -sI` sends HEAD, which the frame supports; `curl -s -D - -o /dev/null` works too.

```bash
H=helpdesk.example.org
# 1. frame: shows the frame CSP (frame-ancestors, connect-src) and NO X-Frame-Options
curl -sI "https://$H/chat/v1/frame?k=<k>&origin=https://www.example.org" | grep -i -E '^(HTTP|content-security-policy|x-frame-options)'
# 2. loader: 200, served by tiqora-api
curl -sI "https://$H/chat/v1/loader.js" | head -1
# 3. a 10 MiB attachment passes nginx client_max_body_size: a dummy body to the upload route of a
#    nonexistent chat is enough; Tiqora answers with a JSON 4xx, nginx would answer an HTML 413
head -c 10485760 /dev/zero | curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H "Origin: https://$H" -H 'Content-Type: application/octet-stream' --data-binary @- \
  "https://$H/api/chat/v1/sessions/00000000000000000000000000000000/attachments"
# 4. an SSE stream stays open for more than 90 s (heartbeats `: hb` every 25 s keep it alive);
#    open one from the chat widget in a browser with the network tab, or:
curl -sN --max-time 100 -H "X-Chat-Token: <token>" "https://$H/api/chat/v1/sessions/<session id>/stream"
```

Expected: step 1 has a `content-security-policy` with `frame-ancestors` and no `x-frame-options` header; step 2
`HTTP/2 200`; step 3 a `4xx` other than `413` (a `413` here is nginx's `client_max_body_size`); step 4 keeps printing `: hb` lines and is cut only by `--max-time`.
The Puppet-managed nginx cannot be inspected from this repository, which is why this is a checklist.

### Graceful shutdown

On `SIGTERM` uvicorn closes open SSE streams after at most 8 seconds (`timeout_graceful_shutdown`), below
Docker's default stop grace period of 10 s, so the application's shutdown cleanup (stream hub, connection pools)
runs instead of ending in `SIGKILL`. Widgets and agent tabs reconnect on their own.

### Sizing

The load script `backend/scripts/webchat_stream_load.py` creates N sessions against a **test instance** and
holds N SSE streams for M minutes; it prints the streams held, errors and, with `--pid`, the RSS of the API
process. It refuses non-local hosts (every session creates a ticket). Prepare the test instance: web chat on, a
chat whose rate limits are raised (`sessions_per_config_minute` 300, `sessions_per_ip_10min` 100),
`TIQORA_CHAT_MAX_STREAMS` at least N, and `TIQORA_TRUSTED_PROXIES=0.0.0.0/0` so the script's distinct
`X-Forwarded-For` values count as separate visitors.

Measured on 2026-10-11 against the local stack (`docker-compose.local.yml`, one `tiqora-api` container with a
single uvicorn process, OrbStack on a laptop; MariaDB, Redis and the worker alongside; memory from
`docker stats`, since the API runs in a container). Each run created N sessions at 4 to 5 per second and held the
streams for 3 minutes (one run of 20 streams for 30 s beforehand):

| Streams held | Errors | API memory (RSS) | API CPU while holding |
|---|---|---|---|
| idle (after a 20-stream warm-up) | | 232 MiB | 0.16 % |
| 200 | 0 | 255 MiB | about 7 % |
| 500 | 0 | 306 MiB | about 7 % |

That is roughly 0.15 MiB per open stream, with every heartbeat (every 25 s) re-reading the DB per stream. Memory
stayed at the peak (about 307 MiB) after the 500 streams closed. These numbers are for orientation only; they come from one laptop and
idle streams (no messages). Plan for one `tiqora-api` process per roughly 500 open visitor streams (the default
`TIQORA_CHAT_MAX_STREAMS`; agent event streams come on top), and raise the file-descriptor limit before raising the cap.

Each open stream is one asyncio task and one socket; it does not hold a database connection while idle (every
heartbeat opens a short session). Redis carries only wake and typing markers.

### Notifications to visitors

`.invalid` recipients are dropped on every mail path (logged as `mail_recipient_invalid_tld`), and a visitor who
gave no address is never mailed: the placeholder address `visitor-...@chat.invalid` exists only so the article
has a sender.

### MCP and chats

`ticket_reply` over MCP into a **waiting** chat answers `{"error": "chat_not_accepted"}`. The MCP transaction is
already open, so accepting first (as the REST reply does) cannot run, and there is no MCP tool to accept a
chat; a human accepts it, after which MCP replies work. MCP-posted replies have no Redis wake-up: they reach the
visitor with the next stream heartbeat, at most 25 s later (REST replies reach them immediately).

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
- [ ] SCIM: nginx `location /scim/` ([SCIM provisioning](#scim-provisioning)), `TIQORA_TRUSTED_PROXIES` set
- [ ] Web chat: `TIQORA_PUBLIC_BASE_URL`, `TIQORA_TRUSTED_PROXIES` set; [post-deploy check](#post-deploy-check-run-once-on-production) done
