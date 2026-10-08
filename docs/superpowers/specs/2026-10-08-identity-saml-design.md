# Identity: SAML, multi-IdP SSO, provisioning — design

> Part of the [competitive parity roadmap](2026-10-08-competitive-parity-roadmap-design.md).
> Status: proposed 2026-10-08. Decisions below are fixed unless marked "open".

## Goal

Close the only "No" Tiqora has in the SSO row of the comparison
(`site/compare.html`: "OIDC, LDAP/AD, Kerberos/SPNEGO, TOTP, passkeys — No SAML")
and go past Znuny 7.3 and Zammad: SAML **and** OIDC for agents **and**
customers, several identity providers at once, configured in the admin UI,
with just-in-time provisioning and group → role sync.

## Current state (2026-10-08)

| Area | State | Where |
|---|---|---|
| Local password (Znuny hashes) | Yes, agents + customers | `domain/auth.py`, `domain/customer_auth.py` |
| LDAP/AD | Fallback after local; one directory; optional group gate; **no provisioning, no role/attribute sync** | `domain/_ldap_core.py`, `auth_ldap.py`, `customer_auth_ldap.py` |
| OIDC | Agents only; **one** provider; claim must match existing `users.login`; agent must be `sso_eligible`; no JIT, no group mapping; PKCE; SSRF-pinned token call | `domain/oidc.py`, `/api/v1/auth/oidc/*` |
| Kerberos/SPNEGO | Agents; optional `gssapi` extra | `domain/spnego.py` |
| TOTP, passkeys | Agents only | `tiqora_user_totp`, `tiqora_user_passkey` |
| SAML | **None** — no `python3-saml`/`pysaml2`/`xmlsec` dependency | — |
| Customer portal | Password + LDAP fallback only; no SSO, no 2FA | `api/portal/auth*` |
| Config | Env vars (`TIQORA_OIDC_*`, `TIQORA_LDAP_*`, …) + `tiqora_user_auth_config` + `auth.totp.*` in `tiqora_settings` | `config.py`, `domain/auth_config.py` |
| Sessions | Opaque Redis sessions, sliding + absolute TTL, per-user revocation index, pending-2FA state | `domain/auth.py::SessionStore` |

## Parity checklist (Znuny 7.3, Zammad 7)

What the competitors ship (research 2026-10-08):

- **Znuny 7.3** (`Kernel::System::Auth::SAML` + `CustomerAuth::SAML`, since
  7.3.1): agents and customers; IdP metadata by URL or inline XML; optional
  request signing; several backends via numeric suffix (= several IdPs);
  agent-side `AuthSyncModule::SAML` with `UserSyncMap` (givenName/sn/mail
  required), initial groups, group and role sync from attributes. *Not
  documented:* customer attribute sync, SLO, encrypted assertions.
- **Zammad 7**: one SAML IdP, manual fields only (no metadata URL import),
  SP metadata at `/auth/saml/metadata`, SLO, optional sign/encrypt, account
  linking by email, **no** group/role mapping.

Checklist:

- [ ] SAML 2.0 SP for agents
- [ ] SAML for customers
- [ ] IdP metadata by URL and by upload; SP metadata endpoint
- [ ] Attribute mapping (login, email, first/last name)
- [ ] Group and role sync from assertion attributes (Znuny 7.3 agents)
- [ ] Several IdPs at once (Znuny 7.3)
- [ ] Single logout (SP- and IdP-initiated) — Zammad has it, Znuny does not document it
- [ ] LDAP user sync: attribute map, group/role sync on login (Znuny `Auth::Sync::LDAP` `UserSyncMap`, `UserSyncGroupsDefinition`, `UserSyncRolesDefinition`)
- [ ] Auto-create agents on first SSO/LDAP login (Znuny LDAP sync creates users)
- [ ] Social/Microsoft/Google login (Zammad) — covered by generic OIDC presets

## Beyond parity ("besser als")

1. **Several IdPs side by side**, each OIDC or SAML, each for agents,
   customers or both, each with its own login button — configured and
   *tested* in the admin UI ("Testanmeldung" shows the received claims /
   assertion attributes and the resulting mapping before anything is saved).
2. **Customer portal SSO + 2FA + passkeys** — none of the open-source
   competitors offer passkeys for end customers.
3. **Stable subject binding**: an IdP identity (`iss`+`sub` / IdP entity ID +
   NameID) is linked to a user once; renaming the login in the IdP does not
   lock anyone out or hijack another account.
4. **Managed role sync without clobbering**: only roles/groups that a mapping
   rule manages are added/removed; manual assignments stay.
5. **SCIM 2.0 provisioning** (phase 3): Entra ID / Okta / Keycloak push
   users and groups, deprovisioning invalidates sessions immediately (session
   revocation index already exists).
6. **Step-up per IdP**: require an MFA claim (`amr` / SAML `AuthnContextClassRef`)
   from the IdP, otherwise fall back to Tiqora TOTP/passkey — today SSO
   unconditionally skips 2FA.

## Fixed decisions

### SAML library: `pysaml2`

- Actively maintained (IdentityPython), supports metadata aggregates,
  several IdPs per SP, encrypted assertions, SLO (redirect + POST bindings).
- Signature work runs through the `xmlsec1` **binary** (subprocess), so there
  is no `python-xmlsec` / `lxml` libxml2 ABI-mismatch problem (the classic
  `python3-saml` failure mode in Docker builds).
- Cost: the runtime image needs `xmlsec1` (Debian package, ~1 MB plus
  `libxmlsec1-openssl`). Add it to the `Dockerfile` runtime stage, behind the
  same pattern as the optional `kerberos` extra: new uv extra `saml`, and the
  `/api/v1/auth/methods` discovery reports `saml: false` + admin hint when the
  binary or package is missing (same as SPNEGO returns 501 without `gssapi`).
- Alternative recorded: `python3-saml` (OneLogin). Rejected for the ABI
  issue and slower maintenance. `signxml` alone is not a full SP.
- All `pysaml2` calls run in an executor (sync library, subprocess) — same
  rule as `ldap3` and `gssapi`.

### Configuration moves from env into the database

- New table `tiqora_identity_provider`:
  `id, kind (oidc|saml|ldap), name, slug, audience (agent|customer|both),
  enabled, sort, button_label, button_icon, config JSON (secrets
  Fernet-encrypted like channel secrets), jit_enabled, jit_defaults JSON,
  attribute_map JSON, role_rules JSON, require_mfa_claim, created/changed`.
- New table `tiqora_identity_link`: `provider_id, subject, user_kind
  (agent|customer), user_login, first_seen, last_seen` — unique
  (`provider_id`, `subject`). The first login with no link falls back to the
  configured matching claim (login or email, as today), then stores the link.
- New table `tiqora_identity_managed_assignment`: which `role_user` /
  `group_user` rows were created by a sync rule (so removal only touches
  those).
- Env config keeps working: on startup an env-defined OIDC/LDAP becomes a
  read-only provider row `source=env` (shown in the UI with "aus
  Umgebungsvariablen, hier nicht änderbar"). No breaking change for
  existing deployments (production uses env OIDC today).
- LDAP gets the same table (`kind=ldap`) so several directories and the
  Znuny `AuthSyncModule` features fit the same model.

### Provisioning writes to Znuny tables (ground rule 3)

- JIT agent creation writes `users` (+ `user_preferences` defaults identical
  to `AdminUser` add: `UserLanguage`, `UserTheme` etc. — verify the exact set
  against `Kernel::System::User::UserAdd` in 6.0 and 7.3), `role_user` /
  `group_user` (schema-profile aware: `groups` vs `permission_groups`,
  surrogate `id` on 7.1+ junction tables). Reuse the existing admin user
  create service — no second write path.
- JIT customer creation only when the customer backend is the DB backend
  (`customer_user` table); with an LDAP customer backend, SSO only binds to
  existing customers.
- Password column for JIT users: a random unusable hash, so a local login is
  impossible until an admin sets one.
- Deprovisioning (SCIM `active=false`, or "IdP says user gone" on LDAP sync):
  set `valid_id = 2`, revoke all sessions. Never delete.

### Session handling

- Successful SAML/OIDC login creates the same Redis session as today.
  Store `idp_id`, `session_index` (SAML) / `sid` (OIDC) on the session for SLO.
- SLO: SP-initiated (logout button → IdP), IdP-initiated (LogoutRequest →
  revoke matching sessions via the per-user index), OIDC back-channel logout
  (phase 2).
- 2FA rule changes from "SSO skips 2FA" to "SSO skips 2FA unless the provider
  has `require_mfa_claim` and the claim is missing → pending-2FA session". The
  current behaviour remains the default.

### Security requirements (tests for each)

- Require signed assertions (or signed response with signed assertion — no
  unsigned fallback); check `Audience`, `Recipient`, `Destination`,
  `NotBefore`/`NotOnOrAfter` (configurable skew, default 120 s),
  `InResponseTo` against a Redis request cache (replay protection; IdP-initiated
  login off by default, per-IdP opt-in).
- Reject XML with DTDs (defusedxml before pysaml2), XSW tests (signature
  wrapping) from a fixed corpus of malicious assertions.
- `RelayState` / `next` validated with the existing same-origin redirect check.
- IdP metadata fetch through `security/outbound.py` (SSRF guard), refreshed
  daily by a worker `_daily_loop`; certificate rollover: accept every signing
  cert in current metadata.
- CSRF check (`security/csrf.py`) exempts only the ACS and SLO POST endpoints.

## API / UI surface

- `GET /api/v1/auth/methods` → list of providers (`slug`, `kind`,
  `button_label`, `icon`) per audience instead of booleans (keep the old
  booleans for one release for the API client).
- Agents: `/api/v1/auth/sso/{slug}/login`, `/api/v1/auth/oidc/{slug}/callback`,
  `/api/v1/auth/saml/{slug}/acs`, `/api/v1/auth/saml/{slug}/sls`,
  `/api/v1/auth/saml/{slug}/metadata`. Customers: same under `/api/portal/auth/…`.
  The existing `/auth/oidc/login|callback` stay as aliases for the env provider.
- Admin: new page **Anmeldung → Identitätsanbieter** (list, create from
  preset: "Microsoft Entra ID", "Google Workspace", "Keycloak", "Authentik",
  "ADFS", "Generic SAML", "Generic OIDC"), tabs *Verbindung*,
  *Zuordnung* (attribute map with live preview), *Rollen* (rules: IdP group
  value → Znuny role(s); "verwaltete Zuordnungen" list), *Test*.
  `AuthConfigPage` (existing) links there.
- Portal login page: provider buttons for `audience in (customer, both)`.
- Portal security page: TOTP + passkeys for customers (reuse agent
  components; new tables `tiqora_customer_totp`, `tiqora_customer_passkey`
  keyed by `customer_user.login`).
- SCIM (phase 3): `/scim/v2/Users`, `/scim/v2/Groups`, bearer token per
  provider, scoped to that provider's audience.

## Parallel operation

- Znuny keeps authenticating its own UI with its own SysConfig; nothing in
  Znuny changes. Users created by JIT in Tiqora are normal Znuny users and can
  log into Znuny with whatever Znuny auth is configured there.
- Role sync writes `role_user` the way `AdminRoleUser` does → TiqoraSync cache
  invalidation for the user/permission caches (new invalidation type if the
  current one does not cover permission caches — verify).

## Tests

- Unit: mapping engine, rule evaluation, managed-assignment diffing.
- Integration: SAML round trip against a test IdP — a Keycloak container in
  the CI compose (both SAML and OIDC from one IdP), plus a static
  `pysaml2`-generated IdP for the malicious-assertion corpus.
- Golden: JIT-created user visible and usable in a real Znuny peer
  (`just golden-all-peers`), roles effective in Znuny.
- Playwright: login button flow against Keycloak.

## Phases

| Phase | Content | Size |
|---|---|---|
| 1 | Provider table + env migration, multi-OIDC, SAML for agents (login, ACS, metadata, SP-initiated SLO), admin UI with test login, Dockerfile `xmlsec1` | 2–3 weeks |
| 2 | Customer SSO (SAML+OIDC), customer TOTP/passkeys, JIT provisioning, role/group sync, IdP-initiated + back-channel logout, LDAP as provider rows with Znuny `AuthSync` parity | 2–3 weeks |
| 3 | SCIM 2.0, step-up MFA claim, per-IdP session lifetime | 1–2 weeks |

## Risks / open

- Znuny 7.3 already has customer SAML and role sync, so phase 1 alone only
  reaches Zammad level; the compare-page cell may say "Yes" only after phase 2.
- `xmlsec1` must be present in the image used by `tiqora-api` only; the
  worker needs it only for metadata validation — same image anyway.
- Production nginx (Puppet-managed) must pass `POST /api/v1/auth/saml/*/acs`
  with large bodies (assertions with group lists can exceed 64 KB) — check
  `client_max_body_size` / proxy buffers.
