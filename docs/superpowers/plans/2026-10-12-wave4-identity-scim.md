# Wave 4 — Identity phase 3: SCIM 2.0 provisioning, step-up MFA, per-IdP session lifetime — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Identity providers configured in Tiqora can (1) push agents, customers and agent groups through a standards-conformant SCIM 2.0 endpoint (`/scim/v2/Users`, `/scim/v2/Groups`) authenticated by per-provider bearer tokens, where deprovisioning (`active=false` or `DELETE`) invalidates the Znuny account (`valid_id = 2`) and ends every session immediately; (2) demand proof of multi-factor authentication from the IdP (`amr`/`acr` in the OIDC id_token, `AuthnContextClassRef`/MFA attribute in the SAML assertion), falling back to Tiqora's own TOTP/passkey step when it is missing; and (3) give each provider its own (shorter) idle and absolute session lifetime.

**Architecture:** A new sub-package `tiqora.identity.scim` holds the SCIM protocol layer (pure: filter parser, PATCH engine, resource (de)serialisation, discovery documents, error bodies), the token store (sha256-hashed tokens, constant-time compare), the audit log, and two DB services (Users, Groups) that write Znuny accounts **only** through `tiqora.domain.user_provisioning` / `tiqora.identity.provisioning` (wave 1) plus two new validity write services. SCIM groups are mirrored in `tiqora_scim_group*`; they reach Znuny roles/groups only through the existing wave-1 rule engine (`evaluate_rules → diff_assignments → apply_assignments`, managed assignments, no clobbering) via the pseudo attributes `scim:groups` / `scim:group_ids`, applied by a worker loop from a pending-sync table so a large group push never runs long in the request. A FastAPI router mounted at `/scim/v2` (outside `/api`, bearer only, no cookies, own error format) fronts it. Step-up and session lifetime extend wave-1 `complete_login`, `SsoSessionInfo`, `SessionStore` and `CustomerSessionStore`; the admin UI gains two tabs on the existing provider page.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic two-chain, Pydantic v2, redis.asyncio, pysaml2 (wave 1, `saml` extra), authlib (wave 1); React 19 + TanStack Router/Query, Vitest, demo mode (MSW).

**Spec:** `docs/superpowers/specs/2026-10-08-identity-saml-design.md` — "Beyond parity" items 5 (SCIM 2.0) and 6 (step-up per IdP), "Provisioning writes to Znuny tables" (deprovisioning bullet), "Session handling" (2FA rule change), "API / UI surface" (SCIM bullet), phase-3 row of "Phases". Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md`, table "Waves", wave 4 "Identity 3 (SCIM)". Builds on `docs/superpowers/plans/2026-10-08-wave1-identity-saml.md` (Design D0–D17, Deviations 1–13 — especially 6 "no `require_mfa_claim` column — phase 3" and 7 "deprovisioning arrives with SCIM") and the wave-1 rulings `<repo>/.superpowers/sdd/2026-10-08-wave1-identity-saml/rulings.md` (all stay binding; notably: e-mail validation via Znuny CheckEmail — tests use `@example.test` addresses, never `@example.org` for accounts created through `user_provisioning`; group permission writes replace the whole key set; managed assignments are agent-only; identity rows are purged by `user_delete.OWNED` and `gdpr/erasure.py`; the golden read-back runs `Cache CleanUp(Type => 'DBRoleUserGet')`).

## Open questions for the product owner

Every item is decided conservatively in this plan (marked **Assumption:** where used) and can be flipped later without redesign.

1. **Existing accounts are not taken over by default.** A SCIM `POST /Users` for a login that already exists (and is not yet managed by this provider) answers `409 uniqueness` unless the provider switch "Vorhandene Konten übernehmen" is on (default off). (S6)
2. **SCIM never renames a Znuny login.** A changed `userName` is stored on the SCIM mapping row and returned to the IdP, but `users.login` / `customer_user.login` stay (tickets reference customer logins as strings). (S6)
3. **Customer number of SCIM-created customers** = the provider's fixed "Kundennummer" (`jit_defaults.customer_id`) or else the e-mail address; the SCIM enterprise extension (`organization`, `department`, …) is accepted and ignored. (S6)
4. **Groups exist only for agent provisioning,** and only change Znuny roles/groups through role rules on `scim:groups` / `scim:group_ids`; no Znuny role is created automatically; a provider's rules are either all SCIM-based or all attribute-based (no mixing). (S7)
5. **No "last administrator" protection** beyond the system user (id 1), which SCIM can never create, adopt or change. (S6)
6. **`DELETE /Users/{id}` deactivates** (valid_id 2, sessions ended) and hides the resource from SCIM; the Znuny account is never deleted. (S6)
7. **Per-provider session lifetimes can only be shorter** than the global `TIQORA_SESSION_TTL` / `TIQORA_SESSION_ABSOLUTE_TTL`; a SAML `SessionNotOnOrAfter` shortens further. (S10)
8. **Missing MFA proof:** agents with TOTP/passkey get the second-factor step, agents without one are forced to enrol (existing "must enroll" flow); customers without a factor are refused (`sso_error=mfa_required`); per provider the fallback can be set to "ablehnen" (refuse everyone). (S9)
9. **SCIM, step-up and own session lifetimes are only offered for OIDC/SAML providers configured in the UI** — not for env-defined providers (`env-oidc`, read-only rows) and not for LDAP providers. (S2)
10. **SCIM requires the provider to be enabled AND "Bereitstellung aktiv"**; a disabled provider rejects every token (401). (S3)
11. **Tokens always expire** (default 365 days, 1–730), at most 5 active tokens per provider. (S3)
12. **The SCIM log records writes and failures only** (no reads), keeps no attribute values (field names only), and is purged after 180 days. (S8)
13. **Unknown member ids in a group push are ignored** (counted in the log) instead of failing the whole request. (S7)
14. **"active" is read from Znuny** (`valid_id == 1`): if an admin re-enables a SCIM-deactivated account by hand, SCIM reports it active and the IdP may deactivate it again on its next cycle. (S6)
15. **SCIM lives at `/scim/v2`** (spec) — production nginx needs a new `location /scim/` block; until then the endpoint is unreachable from outside. (S5, Task 22)
16. **The SCIM `password` attribute is ignored**; SCIM-created accounts get the wave-1 unusable password hash (login via SSO only). (S6)
17. **MFA proof is read only from the verified OIDC id_token** (never userinfo); a provider that returns no id_token can never satisfy "zweiten Faktor verlangen" and always falls back. (S9)

## Global Constraints

- **Where:** a new worktree off `roadmap-wave1` (path/branch from the controller, pattern `implementer-instructions-wave3.md`; wave 4 runs `itsm1`, `cmdb`, `plugins2a` and this plan in parallel worktrees). Never touch `<repo>` (main); never commit to main or `roadmap-wave1`; never push (a push to origin main deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Use `rtk git`; run `rtk git log -1 --oneline` after every commit (the pre-commit hook can block silently while chained commands still print success). Stage explicit paths only; run `rtk git diff --cached --name-only` before every commit. Never amend/reset/rebase/stash; never restore files you did not change. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. zsh has `noclobber`: overwrite with `>|` or the Write tool.
- **Example data only:** `example.org`, `example.com`, `.test`; accounts created through `user_provisioning` use `@example.test` (Znuny `CheckEmailInvalidAddress` default rejects `example.org/com`, wave-1 ruling); names like "Erika Muster". Never real customer data in tests, docs, fixtures, recorded SCIM sequences or demo mocks.
- **Tables:** write only `tiqora_*` tables directly. Every write to a Znuny table (`users`, `user_preferences`, `customer_user`, `role_user`, `group_user`, `sessions`) goes through the existing write services: `tiqora.domain.user_provisioning` (`create_agent`, `update_agent_profile`, `create_customer_user`, `update_customer_profile`, `grant_role`, `revoke_role`, `set_group_permissions`, and the two functions this plan adds there: `set_agent_validity`, `set_customer_validity`), `tiqora.identity.provisioning` (`provision_agent`, `provision_customer`, `sync_profile`, `apply_assignments`), `tiqora.znuny.sessions` (`delete_agent_sessions`, `delete_customer_sessions`). Never change Znuny DDL.
- **No JOIN between `tiqora_*` and Znuny tables** (prod MariaDB mixes `general_ci`/`unicode_ci` → error 1267): load keys, then query with an IN-list (chunks of ≤ 1000). `tiqora_*`↔`tiqora_*` joins are fine. Case-insensitive lookups on `tiqora_scim_*` use the stored `*_fold` columns; exact lookups of IdP strings use the stored sha256 `*_hash` columns (wave-1 Deviation 1 pattern).
- **Transactions:** SQLAlchemy 2 autobegins on the first read — never `async with db.begin()` after any read on the same session; use `commit()`/`rollback()`; savepoints via `begin_nested()` only as wave 1 does. One full-request test per new endpoint against a real DB session.
- **No long synchronous endpoints:** prod nginx has `proxy_read_timeout 90s`; no endpoint here may run longer than ~15 s. SCIM requests only touch `tiqora_scim_*` rows plus the single account they address; Znuny role/group writes caused by group changes run in the worker loop `scim_role_sync` (S7). Bound: `scim_max_members_per_request` (5000) and `scim_max_body_bytes` (2 MiB).
- **Both dialects:** DB tests run on MariaDB AND PostgreSQL: `@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])` + `request.getfixturevalue(url_fixture)` + `_to_async_url` (pattern: `backend/tests/test_admin_postmaster_filters.py`, identity helpers `backend/tests/_identity_helpers.py`: `FakeRedis`, `seed_agent`, `seed_customer`, `make_provider`, `cleanup_statements`). `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never bare `pytest` / `uv run pytest`). New DB test modules clean up after themselves (`tests._row_cleanup.snapshot_marks` / `delete_above_marks`); never add entries to `backend/tests/db_leak_baseline.txt`. Sync the venv like CI: `uv sync --extra dev --extra crypto --extra saml`.
- **Lint gates before every backend commit:** `cd backend && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora` (ruff format is what turns CI red).
- **Seed ids** only in this plan's band **63000–63399** (agent/customer/role/group ids; logins contain `sc63`, e-mails `…@example.test`); confirm free first with `grep -rnE "63[0-3][0-9]{2}" backend/tests` — if another wave-4 plan took it, move the whole band to the next free 400-wide block and say so in the first commit. Sub-bands per task under "Tasks".
- **Migrations:** exactly one new migration in `backend/alembic/versions_tiqora`, numbered with the **next free number at implementation time** after the then-current primary head (`ls backend/alembic/versions_tiqora | tail -3`; `cd backend && uv run alembic heads` — the task text's "20261010_0073" is already the head of a chain ending in 0076, and other wave-4 plans add migrations in parallel; trust only `alembic heads` in your worktree; the controller re-chains at merge). Follow skill `alembic-two-chain-new-head-forks-graph`; update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version == …` assertion in `backend/tests/test_migrate_cli.py`; ORM in `backend/src/tiqora/db/tiqora/models.py` must produce the same DDL (`backend/tests/test_tiqora_model_ddl.py`).
- **Znuny golden peers** 6.5 and 7.3 exist under `tests/golden` (`GOLDEN=1 just golden-run`, matrix `just golden-all-peers`); Task 22 adds one golden test.
- **Timestamps** in `tiqora_scim_*` are app-written naive UTC (`tiqora.identity.types.utc_now()`); SCIM `meta.created/lastModified` are emitted as ISO 8601 with `Z`; admin API datetimes use `tiqora.domain.schemas.UtcDateTime` (skill `tiqora-naive-utc-datetimes-display-2h-early`).
- **Security (internet-facing `/scim/v2`):** bearer token only in the `Authorization` header (never query string, never cookie); tokens are stored as sha256 hex only, shown once, compared with `hmac.compare_digest`; pre-auth order is fixed (S3: IP lockout → header → lookup → tenant checks → per-token rate limit); every 401 has the same body; secrets, tokens and attribute values are never logged (structlog events carry ids and field names only).
- **OpenAPI:** SCIM routes are `include_in_schema=False` (standard protocol, documented in `docs/guide/identity-providers.md`); admin and auth changes regenerate per skill `tiqora-openapi-regen-or-ci-wipes-schema`: `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`; check `head -c 40 packages/api-client/openapi.json` (`{"components"`) and that the diff is hundreds of lines, not five figures. Never hand-edit `schema.d.ts`. A Pydantic response field WITH a default becomes REQUIRED in TS — update `frontend/src/demo/mockData.ts`, `frontend/src/components/admin/identity/testFixtures.ts` and test fixtures.
- **Frontend (`frontend/`):** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` (`pnpm lint` = eslint + tsc). Never run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). German UI strings address the user with **"Sie/Ihr"**. Demo-mode mocks for new screens (`frontend/src/demo/mockData.ts` / `handlers.ts`), checked with `VITE_DEMO=1` + Playwright (delete `.playwright-mcp` before committing). Colours only via Tailwind tokens (`themeColor()` / `tokens.css`). API wrappers in `frontend/src/lib/identityApi.ts` using `api.request<T>(…)` with `Schemas["…"]` types; messages via `apiErrorMessage` (skill `apierror-detail-is-body-not-string`).
- **UI copy:** German first, plain words named by what admins recognise: "Bereitstellung (SCIM)", "Zugangsschlüssel", "SCIM-Adresse", "Vorhandene Konten übernehmen", "Bereitgestellte Konten", "Gruppen vom Anmeldedienst", "Protokoll", "Sitzung & zweiter Faktor", "Zweiten Faktor vom Anmeldedienst verlangen", "Abmeldung nach Inaktivität", "Spätestens abmelden nach". "SCIM", "amr", "AuthnContextClassRef" appear only in help texts and the technical value lists.

## Review Focus

1. **A token reaches exactly one tenant.** A customer-kind token of provider A cannot read or write agents, Groups, or anything of provider B; a revoked, expired, wrong-provider or disabled-provider token gets the same 401 as a random string; a token in `?access_token=` is ignored; 10 bad tokens from one IP lock that IP for 15 minutes; a valid token over its rate gets 429 + `Retry-After`. Pinned in Task 4 (`test_authenticate_*`) and Task 12 (`test_cross_tenant_ids_are_404`, `test_customer_token_has_no_groups`, `test_query_string_token_ignored`, `test_bad_tokens_lock_ip`).
2. **Deprovisioning ends access at once, even when Redis fails.** After `PATCH active=false` / `DELETE`, the agent's full, pending-2FA and enroll sessions, Znuny `sessions` rows and API keys stop working before the response returns; with Redis down the write still commits and `resolve_session` refuses the invalid account. Pinned in Task 3 (`test_end_agent_sessions_*`) and Task 12 (`test_deactivate_ends_sessions_before_response`, `test_deactivate_with_redis_down_still_blocks_session`).
3. **SCIM never touches what it does not own.** User id 1, an existing unmanaged account without "übernehmen", an account owned by another provider, and a soft-deleted resource are never modified; `GET /Users` and filters never list unmanaged accounts. Pinned in Task 9 (`test_post_existing_login_conflicts_without_adoption`, `test_never_adopts_system_user`, `test_owned_by_other_provider_conflicts`, `test_list_shows_only_managed`).
4. **Group pushes never clobber manual roles and never run long.** A rule-granted role disappears only when membership ends; a role an admin assigned by hand stays; an SSO login of a SCIM-managed agent does not strip SCIM-granted roles; a 5000-member `PUT /Groups` returns in seconds and the role effect arrives through the worker. Pinned in Task 10 (`test_put_group_5000_members_is_fast`), Task 11 (`test_manual_role_survives_group_removal`, `test_sso_login_keeps_scim_roles`).
5. **Step-up cannot be bypassed.** `amr` from userinfo, a forged/absent id_token, an IdP-initiated SAML response without context, a pending-2FA cookie used on `/me`, and the admin test login never yield a full session; a promoted session keeps the SSO logout info and the provider's shorter lifetime. Pinned in Task 15 (`test_amr_from_userinfo_is_ignored`), Task 16 (`test_step_up_pending_cookie_is_not_a_session`, `test_promoted_session_keeps_sso_and_lifetime`) and Task 17 (`test_touch_keeps_provider_idle_ttl`).

Also pinned: SCIM error bodies always use the SCIM error schema with `status` as a string (Task 6/12); Entra-style `"Replace"` ops with `"False"` strings work (Task 7/13); `SessionNotOnOrAfter` in the past never yields a session longer than 60 s (Task 17).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Existing names used exactly as in code: `ProviderRecord`, `ProviderChanges`, `ProviderValidationError`, `ProviderReadOnly`, `provider_from_row`, `record_with_changes`, `update_provider`, `validate_changes`, `_SIMPLE_FIELDS` (`identity/store.py`); `AttributeMap`, `RoleRule`, `JitDefaults`, `merge_config`, `CONFIG_MODELS` (`identity/config_models.py`); `ExternalIdentity`, `SsoSessionDraft`, `LoginPlan`, `plan_login`, `apply_login`, `_plan_assignments`, `REJECT_MESSAGES`, `RejectCode` (`identity/login.py`); `MappedProfile`, `normalize_attributes`, `attribute_present`, `evaluate_rules`, `diff_assignments`, `AssignmentState` (`identity/mapping.py`); `resolve_new_account`, `provision_agent`, `provision_customer`, `current_profile`, `diff_profile`, `sync_profile`, `load_assignment_state`, `apply_assignments` (`identity/provisioning.py`); `SsoSessionInfo`, `store_sso`, `load_sso`, `drop_sso` (`identity/sso_sessions.py`); `complete_login`, `_complete_login`, `_reject_fail`, `TestResultData` (`identity/sso_routes.py`); `SamlServiceProvider`, `SamlAssertionResult`, `_login_redirect_sync` (`identity/saml_sp.py`); `OidcService.authorize_url`, `fetch_tokens_and_claims`, `OidcLoginResult` (`domain/oidc.py`); `SSO_ERROR_CODES`, `set_session_cookie` (`identity/http.py`); `base_url` (`identity/urls.py`); `SessionStore`, `AuthService.create_pending_session`, `create_enroll_session` (`domain/auth.py`); `CustomerSessionStore`, `CustomerAuthService`, `revoke_customer_redis_sessions` (`domain/customer_auth.py`); `AuthRateLimiter`, `ApiKeyRateLimiter`, `RateLimitDecision`, `client_ip` (`security/ratelimit.py`); `OWNED`, `_KIND_SCOPED` (`domain/user_delete.py`); `DaemonService`, `DAEMON_SERVICES` (`worker/services.py`); `_interval_loop`, `_daily_loop` (`worker/__main__.py`); frontend `IdentityProviderPage` (`IdentityProviderTab`, `TABS`), `ConnectionTab`, `TestTab`, `LoginPlanView`, `identityApi.ts`, `ssoErrors.ts` (`SSO_ERROR_CODES`, `ssoErrorKey`), `AuthContext` (`pending2fa`, `pendingFactors`, `mustEnroll2fa`), `CustomerAuthContext`, `LoginPage`, `PortalLoginPage`.

### S0. File map

Backend (new):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/identity/scim/__init__.py` | package docstring |
| `backend/src/tiqora/identity/scim/constants.py` | URNs, media type, limits, `TOKEN_PREFIX` |
| `backend/src/tiqora/identity/scim/errors.py` | `ScimError`, `scim_error_body` |
| `backend/src/tiqora/identity/scim/protocol.py` | filter parser, list params, ListResponse, projection, discovery documents (pure) |
| `backend/src/tiqora/identity/scim/patch.py` | PATCH op parser + appliers for User/Group documents (pure) |
| `backend/src/tiqora/identity/scim/users_map.py` | SCIM User JSON ⇄ `ScimUserInput` / `MappedProfile` / response document (pure) |
| `backend/src/tiqora/identity/scim/tokens.py` | token create/list/revoke/authenticate, `ScimTenant` |
| `backend/src/tiqora/identity/scim/audit.py` | `record_event`, `list_events`, `purge_events`, daily purge tick |
| `backend/src/tiqora/identity/scim/users.py` | User service (create/adopt/get/list/replace/patch/delete) |
| `backend/src/tiqora/identity/scim/groups.py` | Group service + membership |
| `backend/src/tiqora/identity/scim/role_sync.py` | `scim_attributes`, `enqueue_role_sync`, `run_scim_role_sync_tick` (imported function-locally by `store.py` / `login.py` — import cycle, see S7) |
| `backend/src/tiqora/identity/step_up.py` | MFA evidence attributes + `mfa_satisfied` (pure) |
| `backend/src/tiqora/identity/session_lifetime.py` | `effective_lifetime` (pure) |
| `backend/src/tiqora/domain/session_revocation.py` | `end_agent_sessions`, `end_customer_sessions` (extracted from admin routes) |
| `backend/src/tiqora/api/scim/__init__.py` | `scim_router` export |
| `backend/src/tiqora/api/scim/auth.py` | `require_scim_tenant` dependency (pre-auth order) |
| `backend/src/tiqora/api/scim/body.py` | `read_scim_json` (capped, media type) |
| `backend/src/tiqora/api/scim/router.py` | `/scim/v2/*` routes |
| `backend/src/tiqora/api/v1/admin/identity_scim.py` | admin SCIM status/tokens/accounts/groups/events routes |
| `backend/tests/_scim_helpers.py` | `make_scim_app`, `scim_headers`, `seed_scim_provider`, band helpers |
| `backend/tests/fixtures/scim/entra_*.json`, `okta_*.json` | recorded request sequences (made-up data) |

Backend (modified): `db/tiqora/models.py`, `alembic/versions_tiqora/<next>_identity_scim.py`, `config.py` (S4 settings), `identity/types.py` (constants), `identity/config_models.py` (`MfaPolicy`), `identity/store.py`, `identity/mapping.py` (constants only), `identity/login.py` (`_plan_assignments`, `ExternalIdentity` unchanged), `identity/sso_routes.py`, `identity/saml_sp.py`, `identity/sso_sessions.py`, `identity/http.py` (`SSO_ERROR_CODES` += `mfa_required`), `domain/oidc.py` (`acr_values`), `domain/auth.py`, `domain/customer_auth.py`, `domain/user_provisioning.py`, `domain/user_delete.py`, `gdpr/erasure.py`, `security/ratelimit.py`, `api/app.py`, `api/v1/auth.py`, `api/portal/auth.py`, `api/v1/admin/users.py`, `api/v1/admin/customers.py`, `api/v1/admin/identity_providers.py`, `api/v1/admin/identity_schemas.py`, `api/v1/admin/__init__.py`, `domain/schemas.py` (`PendingLoginOut`), `worker/__main__.py`, `worker/services.py`, `domain/settings_store.py`.

Frontend (new): `frontend/src/components/admin/identity/ProvisioningTab.tsx` (+test), `ScimTokenDialog.tsx`, `ScimEventsList.tsx`, `SessionTab.tsx` (+test). Modified: `routes/admin/IdentityProviderPage.tsx`, `components/admin/identity/TestTab.tsx` / `LoginPlanView.tsx` (MFA line), `lib/identityApi.ts`, `lib/ssoErrors.ts` (+test), `auth/AuthContext.tsx`, `auth/CustomerAuthContext.tsx`, `routes/LoginPage.tsx`, `routes/portal/PortalLoginPage.tsx`, `router.tsx` (search param `sso_step_up` on `/login` and `/portal/login`), `demo/mockData.ts`, `demo/handlers.ts`, `components/admin/identity/testFixtures.ts`, `i18n/locales/*.json`.

Docs: `docs/guide/identity-providers.md` (SCIM, step-up, lifetime sections), `docs/deployment.md` (nginx `/scim/`, settings), `docs/architecture.md`, `docs/compatibility.md`.

### S1. Migration (one, Task 1)

```
tiqora_identity_provider  (add)
  require_mfa_claim        BOOL NOT NULL server_default false
  mfa_policy               TEXT NULL                 -- JSON MfaPolicy; NULL → model defaults
  session_idle_seconds     INT  NULL                 -- NULL → global session_ttl_seconds
  session_absolute_seconds INT  NULL                 -- NULL → global session_absolute_ttl_seconds
  scim_enabled             BOOL NOT NULL server_default false
  scim_adopt_existing      BOOL NOT NULL server_default false

tiqora_scim_token
  id            BIGINT PK autoincrement
  provider_id   INT NOT NULL FK → tiqora_identity_provider.id ON DELETE CASCADE
  user_kind     VARCHAR(16) NOT NULL             -- agent | customer (within the provider audience)
  name          VARCHAR(120) NOT NULL
  token_hash    CHAR(64) NOT NULL                -- sha256 hex of the full token
  token_hint    VARCHAR(8) NOT NULL              -- last 4 characters
  created_by    INT NOT NULL                     -- users.id (soft reference)
  created_at    DATETIME NOT NULL
  expires_at    DATETIME NOT NULL
  revoked_at    DATETIME NULL
  last_used_at  DATETIME NULL
  UNIQUE uq_tiqora_scim_token_hash (token_hash)
  INDEX  ix_tiqora_scim_token_provider (provider_id)

tiqora_scim_user
  id                BIGINT PK autoincrement
  provider_id       INT NOT NULL FK → tiqora_identity_provider.id ON DELETE CASCADE
  user_kind         VARCHAR(16) NOT NULL
  scim_id           CHAR(36) NOT NULL            -- uuid4 string, the SCIM "id"
  user_id           INT NOT NULL                 -- users.id | customer_user.id (soft reference)
  user_name         VARCHAR(255) NOT NULL        -- SCIM userName as last sent
  user_name_fold    VARCHAR(255) NOT NULL        -- user_name.casefold()
  external_id       VARCHAR(255) NULL
  external_id_hash  CHAR(64) NULL                -- sha256 hex of external_id (exact match)
  deleted           BOOL NOT NULL server_default false
  version           INT NOT NULL server_default 1
  created           DATETIME NOT NULL
  changed           DATETIME NOT NULL
  UNIQUE uq_tiqora_scim_user_scim_id (scim_id)
  UNIQUE uq_tiqora_scim_user_account (user_kind, user_id)        -- one SCIM owner per account
  INDEX  ix_tiqora_scim_user_name (provider_id, user_kind, user_name_fold)
  INDEX  ix_tiqora_scim_user_ext (provider_id, user_kind, external_id_hash)

tiqora_scim_group
  id                   BIGINT PK autoincrement
  provider_id          INT NOT NULL FK → tiqora_identity_provider.id ON DELETE CASCADE
  scim_id              CHAR(36) NOT NULL
  display_name         VARCHAR(255) NOT NULL
  display_name_fold    VARCHAR(255) NOT NULL
  external_id          VARCHAR(255) NULL
  external_id_hash     CHAR(64) NULL
  version              INT NOT NULL server_default 1
  created              DATETIME NOT NULL
  changed              DATETIME NOT NULL
  UNIQUE uq_tiqora_scim_group_scim_id (scim_id)
  INDEX  ix_tiqora_scim_group_name (provider_id, display_name_fold)
  INDEX  ix_tiqora_scim_group_ext (provider_id, external_id_hash)

tiqora_scim_group_member
  group_id   BIGINT NOT NULL FK → tiqora_scim_group.id ON DELETE CASCADE
  member_id  BIGINT NOT NULL FK → tiqora_scim_user.id ON DELETE CASCADE
  PRIMARY KEY (group_id, member_id)
  INDEX ix_tiqora_scim_group_member_member (member_id)

tiqora_scim_pending_sync
  provider_id INT NOT NULL FK → tiqora_identity_provider.id ON DELETE CASCADE
  user_id     INT NOT NULL                 -- users.id (agents only)
  queued_at   DATETIME NOT NULL
  PRIMARY KEY (provider_id, user_id)
  INDEX ix_tiqora_scim_pending_sync_queued (queued_at)

tiqora_scim_event                          -- no FK: the log outlives a deleted provider
  id            BIGINT PK autoincrement
  provider_id   INT NULL
  token_id      BIGINT NULL
  created       DATETIME NOT NULL
  method        VARCHAR(8)  NOT NULL         -- POST|PUT|PATCH|DELETE|GET|ADMIN
  resource      VARCHAR(8)  NOT NULL         -- User|Group|Token|Auth
  operation     VARCHAR(24) NOT NULL         -- ScimOperation (S8)
  scim_id       CHAR(36) NULL
  user_kind     VARCHAR(16) NULL
  user_id       INT NULL
  status        SMALLINT NOT NULL            -- HTTP status of the answer
  detail        VARCHAR(500) NULL            -- field names / scimType / counts; never values
  actor_user_id INT NULL                     -- admin for token_create/token_revoke
  INDEX ix_tiqora_scim_event_provider (provider_id, id)
  INDEX ix_tiqora_scim_event_created (created)
```

ORM classes: `TiqoraScimToken`, `TiqoraScimUser`, `TiqoraScimGroup`, `TiqoraScimGroupMember`, `TiqoraScimPendingSync`, `TiqoraScimEvent`; new columns on `TiqoraIdentityProvider`. Booleans have `server_default=false()` as in existing models.

### S2. Provider fields (Task 2)

`identity/types.py` adds:

```python
SCIM_KINDS: Final = SSO_KINDS                    # oidc, saml — LDAP and env rows never get SCIM/step-up/lifetime
SESSION_IDLE_MIN: Final = 300
SESSION_ABSOLUTE_MIN: Final = 900
SESSION_FLOOR_SECONDS: Final = 60                # never issue a session shorter than this
```

`identity/config_models.py` adds:

```python
DEFAULT_SAML_MFA_CLASSES: Final = (
    "http://schemas.microsoft.com/claims/multipleauthn",
    "https://refeds.org/profile/mfa",
    "urn:oasis:names:tc:SAML:2.0:ac:classes:MobileTwoFactorContract",
    "urn:oasis:names:tc:SAML:2.0:ac:classes:TimeSyncToken",
)
MfaFallback = Literal["local_second_factor", "reject"]

class MfaPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fallback: MfaFallback = "local_second_factor"
    oidc_amr_values: list[str] = Field(default_factory=lambda: ["mfa"])      # any of these in id_token amr
    oidc_acr_values: list[str] = Field(default_factory=list)                 # id_token acr equal to one of these
    oidc_request_acr_values: str = Field("", max_length=500)                 # sent as acr_values when non-empty
    saml_authn_context_classes: list[str] = Field(default_factory=lambda: list(DEFAULT_SAML_MFA_CLASSES))
    saml_mfa_attribute: str = Field("http://schemas.microsoft.com/claims/authnmethodsreferences", max_length=200)
    saml_mfa_attribute_values: list[str] = Field(
        default_factory=lambda: ["http://schemas.microsoft.com/claims/multipleauthn"])
    saml_request_authn_context: bool = False   # sends RequestedAuthnContext(comparison="minimum") with the classes
    # every list: stripped, empties dropped, de-duplicated, ≤ 20 entries of ≤ 300 chars (validator)
```

`ProviderRecord` gains (frozen dataclass, defaults so existing constructors in tests keep working): `require_mfa_claim: bool = False`, `mfa_policy: MfaPolicy = MfaPolicy()` (use `field(default_factory=MfaPolicy)`), `session_idle_seconds: int | None = None`, `session_absolute_seconds: int | None = None`, `scim_enabled: bool = False`, `scim_adopt_existing: bool = False`. `provider_from_row` reads them (`mfa_policy` NULL or invalid JSON → defaults + warning). `ProviderChanges` gains the same six optional fields; `_SIMPLE_FIELDS` += `require_mfa_claim`, `session_idle_seconds`, `session_absolute_seconds`, `scim_enabled`, `scim_adopt_existing`; `mfa_policy` is stored as `model_dump_json()`. `session_*` are nullable in the changes model: an explicit `null` resets to the global value (add both to `_NULLABLE_FIELDS`).

Validation (`validate_changes` / `update_provider`, messages German, keys = field names):

- any of the six fields on a provider with `source == "env"` → existing `ProviderReadOnly` (unchanged behaviour).
- `require_mfa_claim`, `mfa_policy`, `session_*`, `scim_*` set on `kind == "ldap"` → `{"<field>": "Nur für OpenID Connect und SAML verfügbar."}`. **Assumption** (open question 9).
- `session_idle_seconds` ∉ [`SESSION_IDLE_MIN`, `settings.session_ttl_seconds`] → `"Zwischen 5 Minuten und der allgemeinen Sitzungsdauer ({n} Minuten)."`; `session_absolute_seconds` ∉ [`SESSION_ABSOLUTE_MIN`, `settings.session_absolute_ttl_seconds`] → analogous; both set and idle > absolute → `session_idle_seconds`: `"Darf nicht länger sein als „Spätestens abmelden nach“."` **Assumption** (open question 7). `validate_changes(session, changes)` gains a keyword `settings: Settings` (callers in `api/v1/admin/identity_providers.py` pass it).
- role rules: `rules_use_scim(rules)` true for some but not all rules → `{"role_rules": "Regeln mit Gruppen aus der Bereitstellung (scim:…) können nicht mit anderen Attributen gemischt werden."}`. **Assumption** (open question 4).
- `scim_enabled=True` while `audience` excludes both kinds is impossible (audience always includes one); nothing more.

When `role_rules` or `sync_mode` of a provider with SCIM-based rules changes, `update_provider` calls `enqueue_role_sync(session, provider_id, <all non-deleted agent user_ids of tiqora_scim_user for the provider>)` before its flush (S7).

### S3. Tokens and tenant (Task 4)

```python
# identity/scim/constants.py
TOKEN_PREFIX: Final = "tqscim_"
MAX_ACTIVE_TOKENS: Final = 5
TOKEN_DEFAULT_DAYS: Final = 365
TOKEN_MAX_DAYS: Final = 730
LAST_USED_WRITE_INTERVAL: Final = 60          # seconds between last_used_at writes

# identity/scim/tokens.py
@dataclass(frozen=True, slots=True)
class ScimTokenRecord:
    id: int; provider_id: int; user_kind: UserKind; name: str; token_hint: str
    created_by: int; created_at: datetime; expires_at: datetime
    revoked_at: datetime | None; last_used_at: datetime | None
    @property
    def active(self) -> bool  # not revoked and expires_at > utc_now()

@dataclass(frozen=True, slots=True)
class ScimTenant:
    provider: ProviderRecord
    user_kind: UserKind
    token_id: int

class ScimTokenError(ValueError):            # admin-side validation; .errors: dict[str, str]
    ...

def hash_token(raw: str) -> str                                    # sha256 hex
def new_token() -> str                                             # TOKEN_PREFIX + secrets.token_urlsafe(32)
async def create_token(session, *, provider: ProviderRecord, user_kind: UserKind, name: str,
                       expires_in_days: int, actor_id: int) -> tuple[ScimTokenRecord, str]   # flush only
async def list_tokens(session, provider_id: int) -> list[ScimTokenRecord]
async def revoke_token(session, *, provider_id: int, token_id: int, actor_id: int) -> ScimTokenRecord  # idempotent
async def authenticate_token(session, raw: str, *, settings: Settings) -> ScimTenant | None
```

`create_token` rules: provider `kind in SCIM_KINDS` and `source == "db"`; `audience_includes(provider.audience, user_kind)`; `1 ≤ expires_in_days ≤ TOKEN_MAX_DAYS`; name 1–120 chars after strip; at most `MAX_ACTIVE_TOKENS` active tokens per provider → else `ScimTokenError` with German messages. Admin token create/revoke write a `tiqora_scim_event` (`resource="Token"`, `method="ADMIN"`, `actor_user_id`).

`authenticate_token(raw)`: `raw` must start with `TOKEN_PREFIX`, be ASCII and ≤ 128 chars, else `None` without a DB query; `h = hash_token(raw)`; select the row by `token_hash == h`; `None` when missing; then `hmac.compare_digest(row.token_hash, h)` (constant-time final check); reject revoked / expired; load the provider by id (`get_provider`, decrypts config — needed for rules); reject when `not provider.enabled or not provider.scim_enabled or provider.source != "db" or provider.kind not in SCIM_KINDS or not audience_includes(provider.audience, row.user_kind)`. **Assumption** (open question 10). On success update `last_used_at` only when NULL or older than `LAST_USED_WRITE_INTERVAL` and commit that write in its own short transaction (`commit()`, failure → `rollback()` and continue).

Pre-auth order in `api/scim/auth.py::require_scim_tenant(request) -> ScimTenant` (FastAPI dependency):

1. `ip = client_ip(request, settings.trusted_proxies)`; `ScimRateLimiter.check_ip(ip)` locked → `ScimError(429, None, "Too many failed authentication attempts")` + `Retry-After`.
2. `Authorization` header must be `Bearer <token>` (scheme case-insensitive, exactly one space-separated token); missing/other → 401. Query parameters and cookies are never read.
3. `authenticate_token` → `None` → `ScimRateLimiter.record_auth_failure(ip)`, audit `operation="auth_failed"` (provider/token id only when the hash matched a row), 401.
4. `ScimRateLimiter.check_token(tenant.token_id)` → over limit → audit `rate_limited` (at most one event per token per window: guard key `tiqora:scim:rl:logged:{token_id}`), 429 + `Retry-After`.
5. `request.state.scim_tenant = tenant`; return it.

Every 401: status 401, header `WWW-Authenticate: Bearer realm="scim"`, body `scim_error_body(401, None, "Authentication failed")` — identical for every cause.

### S4. Settings and rate limiting (Task 4)

`config.py` (all with `validation_alias`):

| Field | Env | Default |
|---|---|---|
| `scim_rate_limit_enabled` | `TIQORA_SCIM_RATE_LIMIT_ENABLED` | `True` |
| `scim_rate_limit_max` | `TIQORA_SCIM_RATE_LIMIT_MAX` | `600` |
| `scim_rate_limit_window_seconds` | `TIQORA_SCIM_RATE_LIMIT_WINDOW` | `60` |
| `scim_auth_fail_max` | `TIQORA_SCIM_AUTH_FAIL_MAX` | `10` |
| `scim_auth_fail_window_seconds` | `TIQORA_SCIM_AUTH_FAIL_WINDOW` | `600` |
| `scim_auth_lockout_seconds` | `TIQORA_SCIM_AUTH_LOCKOUT` | `900` |
| `scim_max_body_bytes` | `TIQORA_SCIM_MAX_BODY_BYTES` | `2097152` |
| `scim_max_members_per_request` | `TIQORA_SCIM_MAX_MEMBERS` | `5000` |
| `scim_role_sync_interval_seconds` | `TIQORA_SCIM_ROLE_SYNC_INTERVAL` | `15` |
| `scim_event_retention_days` | `TIQORA_SCIM_EVENT_RETENTION_DAYS` | `180` |

`security/ratelimit.py` adds `class ScimRateLimiter` (own key namespace — never `tiqora:rl:apikey:*`, which would collide with API-key ids):

```python
_SCIM_TOKEN_PREFIX = "tiqora:scim:rl:token:"
_SCIM_FAIL_IP_PREFIX = "tiqora:scim:fail:ip:"
_SCIM_LOCK_IP_PREFIX = "tiqora:scim:lock:ip:"
class ScimRateLimiter:
    def __init__(self, client: Any, settings: Settings) -> None
    async def check_ip(self, ip: str) -> RateLimitDecision          # locked → allowed False, retry_after = lock TTL
    async def record_auth_failure(self, ip: str) -> RateLimitDecision | None   # sliding count; ≥ max → set lock
    async def check_token(self, token_id: int) -> RateLimitDecision # fixed window INCR/EXPIRE like ApiKeyRateLimiter
```

All three fail open when Redis raises (logged once per call at warning level), like the existing limiters; `scim_rate_limit_enabled=False` disables all three (tests).

### S5. Protocol surface (Tasks 6, 12)

Base path `/scim/v2` (`SCIM_PREFIX`), router `include_in_schema=False`, included in `api/app.py` **before** `mount_spa` (the SPA fallback would otherwise answer GETs). Outside `/api`, so `security/csrf.py` does not apply (bearer only; no change there).

| Method + path | Answer |
|---|---|
| `GET /ServiceProviderConfig` | `service_provider_config(base)` — patch ✓, bulk ✗, filter ✓ (`maxResults` 200), changePassword ✗, sort ✗, etag ✗, authenticationSchemes `[oauthbearertoken]` |
| `GET /ResourceTypes`, `GET /ResourceTypes/{name}` | `User` always; `Group` only for agent tenants |
| `GET /Schemas`, `GET /Schemas/{urn}` | core User + Group schema documents (attributes Tiqora reads/returns, with `mutability`, `returned`, `uniqueness`, `caseExact`) |
| `GET /Users` | ListResponse (filter, startIndex, count, attributes, excludedAttributes) |
| `POST /Users` | 201 + `Location` |
| `GET /Users/{id}` | 200 / 404 |
| `PUT /Users/{id}` | 200 |
| `PATCH /Users/{id}` | 200 with the resource (never 204) |
| `DELETE /Users/{id}` | 204 |
| same five for `/Groups` | agent tenants only; customer tenant → 404 `ScimError(404, None, "Groups are not available for customer provisioning")` |
| `/Bulk`, `/Me`, `/.search`, `/Users/.search` | 501 |

Discovery endpoints require the token (deviation 8). Requests: `Content-Type` `application/scim+json` or `application/json` (parameters ignored) else 415; body read by `read_scim_json(request, max_bytes)` in chunks, > max → 413 (`scimType` none), invalid JSON / not an object → 400 `invalidSyntax`. Responses: `media_type="application/scim+json"`, UTF-8. Every error, including FastAPI's own 404/405/422 for paths under `/scim/v2`, is a SCIM error body — handled by catching inside the router (endpoints take `Request` and parse manually; no Pydantic body models) and by a `custom_route_class` / exception handler limited to the SCIM router.

```python
# identity/scim/errors.py
ScimType = Literal["invalidFilter", "tooMany", "uniqueness", "mutability", "invalidSyntax",
                   "invalidPath", "noTarget", "invalidValue", "invalidVers", "sensitive"]
class ScimError(Exception):
    def __init__(self, status: int, scim_type: ScimType | None, detail: str) -> None
def scim_error_body(status: int, scim_type: ScimType | None, detail: str) -> dict[str, Any]
#   {"schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"], "status": "409", "scimType": "uniqueness", "detail": "..."}
#   status is a STRING; scimType omitted when None; detail is English (IdP logs) and never echoes token/values longer than 64 chars
```

```python
# identity/scim/protocol.py
SCHEMA_USER: Final = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_GROUP: Final = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_ENTERPRISE: Final = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
SCHEMA_LIST: Final = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCHEMA_PATCH: Final = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
MAX_COUNT: Final = 200
DEFAULT_COUNT: Final = 100

FilterAttr = Literal["id", "userName", "externalId", "emails.value", "displayName"]
@dataclass(frozen=True, slots=True)
class ScimFilter:
    attr: FilterAttr
    value: str
def parse_filter(raw: str | None, *, resource: Literal["User", "Group"]) -> ScimFilter | None
#   grammar: <attrPath> SP "eq" SP <JSON string>; operator case-insensitive; attribute names case-insensitive;
#   User attrs: id, userName, externalId, emails.value, emails[type eq "work"].value (→ "emails.value");
#   Group attrs: id, displayName, externalId; anything else (and/or/pr/co/sw, unknown attr, non-string value,
#   trailing text, > 1000 chars) → ScimError(400, "invalidFilter", ...)
@dataclass(frozen=True, slots=True)
class ListParams:
    start_index: int        # ≥ 1 (values < 1 become 1)
    count: int              # 0..MAX_COUNT (missing → DEFAULT_COUNT; > MAX → MAX; negative → 0)
    attributes: frozenset[str] | None          # lower-cased top-level names
    excluded: frozenset[str]                   # lower-cased top-level names
def parse_list_params(query: Mapping[str, str]) -> ListParams            # non-integer → 400 invalidValue
def project(doc: dict[str, Any], params: ListParams) -> dict[str, Any]   # keeps schemas, id, meta always
def list_response(total: int, resources: list[dict[str, Any]], params: ListParams) -> dict[str, Any]
def service_provider_config(base: str) -> dict[str, Any]
def resource_types(base: str, *, groups: bool) -> list[dict[str, Any]]
def schemas_document() -> list[dict[str, Any]]
def meta(resource_type: str, location: str, created: datetime, changed: datetime, version: int) -> dict[str, Any]
#   {"resourceType", "created": "...Z", "lastModified": "...Z", "location", "version": 'W/"<version>"'}
```

Base URL for `location` and discovery = `identity.urls.base_url(settings)` + `/scim/v2`; when `public_base_url` is empty, `str(request.base_url)` is used (and the admin status reports `base_url = null` with a hint).

### S6. Users (Tasks 8, 9)

```python
# identity/scim/users_map.py
@dataclass(frozen=True, slots=True)
class ScimUserInput:
    user_name: str                  # required, stripped, 1..200 chars, no control chars
    external_id: str | None         # ≤ 255
    active: bool | None             # None = not sent
    given_name: str | None
    family_name: str | None
    display_name: str | None
    email: str | None               # primary → type "work" → first; lower-case domain
    mobile: str | None              # phoneNumbers type "mobile" (else None)
    sent: frozenset[str]            # top-level keys present (for PUT semantics / audit detail)
def parse_user(doc: Mapping[str, Any]) -> ScimUserInput      # ScimError(400, "invalidValue"/"invalidSyntax")
#   accepts booleans as true/false or the strings "True"/"False"/"true"/"false" (Entra);
#   ignores id, meta, groups, password, schemas, and the enterprise extension (logged as field name only)
def to_profile(inp: ScimUserInput) -> MappedProfile
#   login=user_name, email, first_name=given_name or first word of display_name, last_name=family_name or
#   rest of display_name, mobile; customer_id=None (resolve_new_account then uses jit_defaults.customer_id or e-mail)
def user_resource(*, scim_id: str, user_name: str, external_id: str | None, active: bool,
                  first_name: str, last_name: str, email: str | None, mobile: str | None,
                  groups: list[tuple[str, str]], location: str, created: datetime,
                  changed: datetime, version: int) -> dict[str, Any]
#   {"schemas":[SCHEMA_USER], "id", "externalId"?, "userName", "name": {"givenName","familyName","formatted"},
#    "displayName": "first last", "active", "emails": [{"value","type":"work","primary":true}]?,
#    "phoneNumbers": [{"value","type":"mobile"}]?, "groups": [{"value","display","$ref"}], "meta"}
```

```python
# identity/scim/users.py
ScimOperation = Literal[   # defined in identity/scim/audit.py (Task 5), re-exported here
                        "create", "adopt", "revive", "replace", "patch", "deactivate", "reactivate",
                        "delete", "conflict", "rejected", "auth_failed", "rate_limited",
                        "token_create", "token_revoke", "group_create", "group_replace",
                        "group_patch", "group_delete"]
@dataclass(frozen=True, slots=True)
class ScimUserResult:
    resource: dict[str, Any]
    user_id: int
    operation: ScimOperation
    deactivated: bool               # True → router ends sessions after commit
    reactivated: bool
    detail: str | None              # changed field names, "login_not_renamed", …
async def create_user(session, tenant: ScimTenant, doc: Mapping[str, Any], *, base: str) -> ScimUserResult
async def get_user(session, tenant, scim_id: str, *, base: str) -> dict[str, Any]
async def list_users(session, tenant, flt: ScimFilter | None, params: ListParams, *, base: str
                     ) -> tuple[int, list[dict[str, Any]]]
async def replace_user(session, tenant, scim_id: str, doc: Mapping[str, Any], *, base: str) -> ScimUserResult
async def patch_user(session, tenant, scim_id: str, ops: list[PatchOp], *, base: str) -> ScimUserResult
async def delete_user(session, tenant, scim_id: str) -> ScimUserResult
```

All functions flush, never commit (the router commits once, then ends sessions, then writes the audit event in its own transaction — S8). Lookups are per tenant `(provider_id, user_kind)`; a `scim_id` of another tenant, an unknown id or a `deleted` row → `ScimError(404, None, "Resource not found")`.

Create (`POST /Users`):
1. `inp = parse_user(doc)`; `scim:` row for `(provider, kind, user_name_fold)` not deleted → 409 `uniqueness`.
2. Find the Znuny account by login: agents `users.login` with `func.lower(Users.login) == inp.user_name.lower()` (single-table query; prefer the exact-case row on PostgreSQL like wave-1 `_match_login`), customers `customer_user.login` likewise. Any validity.
3. No account → `provision_agent(session, provider, profile)` / `provision_customer(...)` (wave-1 JIT write path incl. `jit_defaults` initial roles/groups and `sso_eligible=True` for agents; unusable password). `ProvisioningError` → `email_invalid`/`email_required`/`name_required`/`jit_missing_fields` 400 `invalidValue`, `email_in_use`/`login_exists` 409 `uniqueness`. Then, when `inp.active is False`, `set_*_validity(valid_id=2)`. Operation `create`.
4. Account exists:
   - agent id `SYSTEM_USER_ID` → 409 `uniqueness` (never adoptable), audit `conflict` detail `system_user`.
   - a `tiqora_scim_user` row for `(kind, user_id)` of **another provider** → 409 `uniqueness`, audit `conflict` detail `other_provider`.
   - a row of this tenant with `deleted=True` → revive: new `scim_id`, `deleted=False`, `user_name` = input, apply profile + validity (`active` absent → active). Operation `revive`.
   - no row: `provider.scim_adopt_existing` false → 409 `uniqueness` detail "An account with this userName exists and is not managed by this provider", audit `conflict`. **Assumption** (open question 1). True → insert the row, apply profile (`sync_profile` with `diff_profile(to_profile(inp), current_profile(...))`) and validity. Operation `adopt`.
5. Insert `tiqora_scim_user` (uuid4 `scim_id`, fold/hash columns), version 1. Agents: `enqueue_role_sync` when the provider uses SCIM rules (group memberships may already exist after a revive).

Replace (`PUT`): `inp = parse_user(doc)`; profile changes via `sync_profile` (fields not sent are left alone; `mobile` sent as absent `phoneNumbers` is not cleared — **Assumption**, IdPs omit unmapped attributes); `user_name` change → update row `user_name`/`user_name_fold` only, never the Znuny login, detail `login_not_renamed` (**Assumption**, open question 2); a new userName that collides with another non-deleted row of the tenant → 409 `uniqueness`; `externalId` updates the row; `active` → validity. Version +1 when anything changed.

Patch: `apply_user_patch(current_doc, ops)` (S6 patch engine, Task 7) on the current resource document, then the result goes through the same path as Replace with `sent` = keys touched by the ops.

Validity: `active False` on an active account → `set_*_validity(valid_id=2)`, `deactivated=True`; `active True` on an invalid account → `set_*_validity(valid_id=1)`, `reactivated=True`, agents: `enqueue_role_sync`. `active` returned = Znuny `valid_id == 1` (**Assumption**, open question 14). The system user is never changed (backstop check in every write path → 409 `mutability`).

Delete: validity 2 (if valid), row `deleted=True`, its `tiqora_scim_group_member` rows removed, version +1, `deactivated=True` when it was valid. Never deletes a Znuny row. **Assumption** (open question 6).

List/filter: only non-deleted rows of the tenant, ordered by `tiqora_scim_user.id`; filter `id` → `scim_id`; `userName` → `user_name_fold == value.casefold()`; `externalId` → `external_id_hash == sha256(value)`; `emails.value` → load candidate accounts by e-mail (agents: `user_preferences` `UserEmail` compared lower-case in Python, wave-1 ruling; customers: `customer_user.email` lower) → their ids → IN-list on the tenant's rows. Profile data for the page is loaded with IN-lists (`users`/`customer_user` by id, `user_preferences` for agents), never a join. `totalResults` = filtered count.

### S6a. PATCH engine (Task 7, pure)

```python
@dataclass(frozen=True, slots=True)
class PatchOp:
    op: Literal["add", "remove", "replace"]        # parsed case-insensitively ("Replace", "Add", "Remove")
    path: str | None
    value: Any
def parse_patch(doc: Mapping[str, Any], *, max_ops: int = 1000) -> list[PatchOp]
#   requires schemas == [SCHEMA_PATCH] (else 400 invalidSyntax) and a non-empty "Operations" list
def apply_user_patch(resource: dict[str, Any], ops: list[PatchOp]) -> tuple[dict[str, Any], frozenset[str]]
#   returns (new document, touched top-level keys); supported paths (case-insensitive):
#   active, userName, externalId, displayName, name.givenName, name.familyName, name.formatted (ignored),
#   emails, emails[type eq "work"].value, emails[primary eq true].value, phoneNumbers,
#   phoneNumbers[type eq "mobile"].value, title (ignored), urn:…:enterprise:2.0:User:* (ignored);
#   no path + dict value → each key applied as replace (Okta/Entra form "value": {"active": false});
#   remove on a required attribute (userName) → 400 mutability; unknown path → 400 invalidPath;
#   filter on a multi-valued attr with no match on replace → adds the value (Entra behaviour);
#   values "True"/"False" for active accepted
@dataclass(frozen=True, slots=True)
class MemberDelta:
    add: frozenset[str]            # scim ids
    remove: frozenset[str]
    replace_all: frozenset[str] | None    # set when "replace" on members (whole list)
    display_name: str | None              # set when displayName replaced
    external_id: str | None | Literal[False]   # False = untouched
def apply_group_patch(ops: list[PatchOp]) -> MemberDelta
#   paths: members (add/remove/replace with list of {"value": id}), members[value eq "<id>"] (remove),
#   displayName, externalId; "remove" with path "members" and a value list removes those (Entra);
#   "remove" with path "members" and no value removes all; > scim_max_members_per_request ids → 400 invalidValue
```

### S7. Groups and role sync (Tasks 10, 11)

```python
# identity/scim/groups.py
@dataclass(frozen=True, slots=True)
class ScimGroupResult:
    resource: dict[str, Any]
    operation: ScimOperation
    affected_user_ids: frozenset[int]     # agents whose scim:groups changed
    ignored_members: int
async def create_group(session, tenant, doc, *, base: str, max_members: int) -> ScimGroupResult
async def get_group(session, tenant, scim_id: str, *, base: str, include_members: bool) -> dict[str, Any]
async def list_groups(session, tenant, flt: ScimFilter | None, params: ListParams, *, base: str
                      ) -> tuple[int, list[dict[str, Any]]]
async def replace_group(session, tenant, scim_id: str, doc, *, base: str, max_members: int) -> ScimGroupResult
async def patch_group(session, tenant, scim_id: str, ops: list[PatchOp], *, base: str,
                      max_members: int) -> ScimGroupResult
async def delete_group(session, tenant, scim_id: str) -> ScimGroupResult
```

Groups belong to agent tenants only. Members are SCIM user ids of the same tenant and not deleted; unknown ids are ignored and counted (`ignored_members`, audit detail `ignored_members=N`). **Assumption** (open question 13). Member writes: load the group's current member row ids (one query), resolve incoming scim ids to `tiqora_scim_user.id`/`user_id` in IN-list chunks of 1000, bulk insert/delete the difference. `displayName` is required on create/replace (400 `invalidValue`); duplicates allowed (Entra allows them). `excludedAttributes=members` (Entra) and `attributes=` projection are honoured; `members` in responses carry `value`, `display` (= the SCIM `userName`), `$ref`.

Affected users: added + removed members (membership changes), all members (displayName/externalId change, delete). After the write, `enqueue_role_sync(session, provider_id, affected_user_ids)` when `rules_use_scim(provider.role_rules)`.

```python
# identity/mapping.py (pure; store.py already imports mapping at module level)
SCIM_GROUPS_ATTR: Final = "scim:groups"        # display names
SCIM_GROUP_IDS_ATTR: Final = "scim:group_ids"  # external ids (Entra objectId by default)
SCIM_ATTRS: Final = frozenset({SCIM_GROUPS_ATTR, SCIM_GROUP_IDS_ATTR})
def rules_use_scim(rules: Sequence[RoleRule]) -> bool          # any rule.attribute.strip() in SCIM_ATTRS

# identity/scim/role_sync.py — imports identity.provisioning → domain.user_provisioning → tiqora.api.v1 →
# identity.http → login → store (the cycle wave 1 notes at the top of login.py): store.update_provider and
# login._plan_assignments import scim_attributes / enqueue_role_sync INSIDE the function, never at module level
async def scim_attributes(session, *, provider_id: int, user_id: int) -> dict[str, tuple[str, ...]]
#   always returns both keys (empty tuples when no membership / not managed) — "present but empty" means
#   "no groups" for the wave-1 fail-safe in _plan_assignments
async def enqueue_role_sync(session, provider_id: int, user_ids: Iterable[int]) -> int
#   delete existing (provider_id, user_id) rows for the ids, then bulk insert with queued_at = utc_now(); flush only
async def run_scim_role_sync_tick(*, batch: int = 200) -> dict[str, int]
#   {"processed", "skipped", "errors"}; for each pending row (oldest first): own session + transaction:
#   provider gone / rules no longer SCIM-based / user not valid / user is SYSTEM_USER_ID → delete row (skipped);
#   else desired = evaluate_rules(scim_attributes(...), provider.role_rules, valid ids …),
#   diff = diff_assignments(desired, load_assignment_state(...), mode=provider.sync_mode, …),
#   apply_assignments(...); delete the pending row WHERE queued_at = <value read> (a newer enqueue survives);
#   commit; an exception → rollback, errors += 1, row stays for the next tick
```

Wave-1 `_plan_assignments` change (SSO login): when `rules_use_scim(provider.role_rules)` the identity attributes are extended with `await scim_attributes(session, provider_id=provider.id, user_id=user_id)` when `user_id is not None`, else with `{SCIM_GROUPS_ATTR: (), SCIM_GROUP_IDS_ATTR: ()}` (JIT preview), before the fail-safe and `evaluate_rules`. So an SSO login computes exactly what the worker computes and never strips SCIM-granted roles.

Worker: `_interval_loop("scim_role_sync", run_scim_role_sync_tick, settings.scim_role_sync_interval_seconds, KEY_SCIM_ROLE_SYNC_INTERVAL_SECONDS, stop)` and `_daily_loop("scim_events_purge", run_scim_events_purge_tick, "04:40", stop)`; `DAEMON_SERVICES` += `DaemonService(slug="scim_role_sync", enabled_key=KEY_SCIM_ROLE_SYNC_ENABLED, default_enabled=True, toggleable=True, schedule_kind="interval", interval_key=KEY_SCIM_ROLE_SYNC_INTERVAL_SECONDS, interval_settings_attr="scim_role_sync_interval_seconds")` and `DaemonService(slug="scim_events_purge", enabled_key=KEY_SCIM_EVENTS_PURGE_ENABLED, default_enabled=True, toggleable=True, schedule_kind="daily", daily_at="04:40")`; keys in `domain/settings_store.py`: `KEY_SCIM_ROLE_SYNC_ENABLED = "daemon.scim_role_sync.enabled"`, `KEY_SCIM_ROLE_SYNC_INTERVAL_SECONDS = "daemon.scim_role_sync.interval_seconds"`, `KEY_SCIM_EVENTS_PURGE_ENABLED = "daemon.scim_events_purge.enabled"`. Follow the existing ticks' enabled-flag check pattern (look at `run_gi_debug_cleanup` / `webchat_sweep_tick` for how a tick honours its `enabled_key`).

### S8. Audit log (Task 5)

```python
# identity/scim/audit.py
async def record_event(session_factory: async_sessionmaker, *, provider_id: int | None, token_id: int | None,
                       method: str, resource: str, operation: ScimOperation, status: int,
                       scim_id: str | None = None, user_kind: str | None = None, user_id: int | None = None,
                       detail: str | None = None, actor_user_id: int | None = None) -> None
#   own session + commit; never raises (logs warning); detail truncated to 500; values never passed in
async def record_event_in(session, **same) -> None        # same row inside the caller's transaction (admin token ops)
async def list_events(session, *, provider_id: int, before_id: int | None, limit: int,
                      failures_only: bool) -> list[TiqoraScimEvent]       # newest first, limit ≤ 200
async def purge_events(session, *, older_than: datetime) -> int
async def run_scim_events_purge_tick() -> dict[str, int]  # {"deleted": n}; retention = settings.scim_event_retention_days
```

Recorded: every SCIM write (POST/PUT/PATCH/DELETE) with its final status (also 4xx), `auth_failed`, `rate_limited`, `conflict`, admin `token_create`/`token_revoke`. Not recorded: successful GETs. **Assumption** (open question 12). The router writes the event after its own commit/rollback, in a separate session, so a failed write is still logged. structlog event `scim.request` with `provider_id`, `token_id`, `operation`, `status`, `duration_ms` (no values).

### S9. Step-up MFA (Tasks 15, 16)

```python
# identity/step_up.py
OIDC_AMR_ATTR: Final = "oidc:amr"
OIDC_ACR_ATTR: Final = "oidc:acr"
SAML_AUTHN_CONTEXT_ATTR: Final = "saml:authn_context"
STEP_UP_ATTRS: Final = frozenset({OIDC_AMR_ATTR, OIDC_ACR_ATTR, SAML_AUTHN_CONTEXT_ATTR})
@dataclass(frozen=True, slots=True)
class MfaCheck:
    required: bool
    satisfied: bool
    evidence: tuple[str, ...]         # the values seen (amr/acr/class refs/attribute values), for the test tab
def check_mfa(provider: ProviderRecord, attributes: Mapping[str, tuple[str, ...]]) -> MfaCheck
#   not provider.require_mfa_claim → MfaCheck(False, True, evidence)
#   oidc: any(v in policy.oidc_amr_values for v in attributes[OIDC_AMR_ATTR])
#         or any(v in policy.oidc_acr_values for v in attributes[OIDC_ACR_ATTR])   (exact, case-sensitive)
#   saml: any class ref in policy.saml_authn_context_classes
#         or any(v in policy.saml_mfa_attribute_values for v in lookup(attributes, policy.saml_mfa_attribute))
```

Adapters inject the pseudo attributes:
- OIDC callback (`sso_routes.py`): `amr` (list of strings) and `acr` (string) **only from `result.id_token_claims`** (verified id_token); the same keys coming from userinfo are removed from `attributes` before injection (`attributes.pop("amr")`, `attributes.pop("acr")`) so they cannot be spoofed through userinfo. **Assumption** (open question 17).
- SAML: `SamlAssertionResult` gains `authn_context_class_refs: tuple[str, ...]` (from every `authn_statement[*].authn_context.authn_context_class_ref` text) and `session_not_on_or_after: datetime | None` (earliest `authn_statement[*].session_not_on_or_after`); the ACS route injects `saml:authn_context`.
- Requests: `OidcService.authorize_url(..., acr_values: str | None = None)` adds `acr_values` when non-empty (`policy.oidc_request_acr_values`); `SamlServiceProvider._login_redirect_sync` passes `requested_authn_context=RequestedAuthnContext(authn_context_class_ref=[AuthnContextClassRef(text=c) …], comparison="minimum")` when `policy.saml_request_authn_context`.

`_complete_login` after `apply_login` (order fixed):

```
check = check_mfa(record, identity.attributes)
if check.satisfied → unchanged path (full session; now with lifetime, S10)
elif record.mfa_policy.fallback == "reject" → _reject_fail("mfa_required")
elif agent:  factors = (TOTPService.is_enabled(uid), WebAuthnService.has_passkey(uid))
             any(factors) → token = auth.create_pending_session(user, sso=sso)         → state pending_2fa
             else         → token = auth.create_enroll_session(user, sso=sso)          → state must_enroll_2fa
elif customer: factors (customer TOTP/passkey services from wave-1 portal security)
             any → customer_auth.create_pending_session(customer, sso=sso)             → state pending_2fa
             else → _reject_fail("mfa_required")                                       (Assumption, open question 8)
response: json_response → LoginResponse(user=None, pending_2fa=…, must_enroll_2fa=…, totp_enrolled, passkey_enrolled)
          browser       → 302 to "/login?sso_step_up=1&next=<next>" (agent) | "/portal/login?sso_step_up=1&next=<next>"
          set_session_cookie(..., token) as today (pending tokens are not resolvable by /me)
admin test (test_id) → never applied (unchanged); TestResultData gains mfa: {"required", "satisfied", "evidence"}
```

`SSO_ERROR_CODES` += `"mfa_required"`; `REJECT_MESSAGES` is unchanged (this is an `_Fail` code, not a plan reject) — `_reject_fail` must accept it (add `"mfa_required"` to the code literal it uses; if `_reject_fail` only takes `RejectCode`, raise `_Fail("mfa_required", http_status=401, detail=...)` directly). German message key `auth.ssoErrors.mfa_required`: "Ihr Anmeldedienst hat keine Anmeldung mit zweitem Faktor bestätigt. Bitte melden Sie sich dort mit zweitem Faktor an oder richten Sie in Tiqora einen zweiten Faktor ein."

Pending state endpoints (read the cookie; no auth dependency; never create anything):

```python
class PendingLoginOut(BaseModel):          # domain/schemas.py
    state: Literal["none", "pending_2fa", "must_enroll_2fa"]
    totp_enrolled: bool = False
    passkey_enrolled: bool = False
GET /api/v1/auth/pending      → agent cookie: get_pending → pending_2fa (+factors); get_enroll → must_enroll_2fa; else none
GET /api/portal/auth/pending  → customer cookie: get_pending → pending_2fa (+factors); else none
```

`SessionStore.create_pending(user_id, login, ttl, *, sso: SsoSessionInfo | None = None)`, `create_enroll(..., sso=None)`, `CustomerSessionStore.create_pending(..., sso=None)` store the SSO info under the pending token (`store_sso(..., ttl=pending ttl)`); `promote_pending` / `promote_enroll` load it (`load_sso`) **before** deleting the pending token and pass it to `create(..., sso=info)`; `AuthService.create_pending_session(user, *, avatar_url=None, sso=None)`, `create_enroll_session(user, *, sso=None)`, `CustomerAuthService.create_pending_session(customer, *, sso=None)` forward it. IdP logout during the pending phase therefore also kills the pending token (index keys).

### S10. Per-IdP session lifetime (Task 17)

```python
# identity/sso_sessions.py — SsoSessionInfo gains (JSON round-trip; ints accepted only for these two)
    idle_ttl: int | None = None       # seconds; already capped to the global value
    absolute_ttl: int | None = None   # seconds; already capped (global, provider, SessionNotOnOrAfter)

# identity/session_lifetime.py
def effective_lifetime(record: ProviderRecord, settings: Settings, *,
                       not_on_or_after: datetime | None, now: datetime) -> tuple[int, int]
#   idle = min(settings.session_ttl_seconds, record.session_idle_seconds or ∞)
#   absolute = min(settings.session_absolute_ttl_seconds, record.session_absolute_seconds or ∞,
#                  (not_on_or_after - now).total_seconds() if given else ∞)
#   absolute = max(SESSION_FLOOR_SECONDS, int(absolute)); idle = max(SESSION_FLOOR_SECONDS, min(idle, absolute))
```

`SsoSessionDraft` gains `session_not_on_or_after: datetime | None = None` (SAML only). `_complete_login` builds `SsoSessionInfo(..., idle_ttl=idle, absolute_ttl=absolute)` with `effective_lifetime(...)`.

`SessionStore.create(..., sso=None)`: `idle = sso.idle_ttl or self._ttl`, `absolute = sso.absolute_ttl or self._absolute_ttl` (both ≤ global by construction, so the per-user and SSO index expiries, which use the global absolute TTL, always outlive the session). The born marker value becomes `f"i={idle}"` (was `"1"`), TTL `absolute`; the auth-at key TTL `absolute`; `store_sso(..., ttl=absolute)`; the main key `ex=idle`. `touch(token)`: read the born value (one GET); `"i=<n>"` with `n ≥ SESSION_FLOOR_SECONDS` → renew with `n`, anything else (legacy `"1"`, garbage) → `self._ttl`; the avatar key the same. `CustomerSessionStore` identical. `get()` treats any non-empty born value as alive (unchanged). Cookies keep `max_age=settings.session_ttl_seconds` (Redis is authoritative).

### S11. Admin API (Task 18)

Provider (existing routes): `IdentityProviderOut` += `require_mfa_claim: bool`, `mfa_policy: MfaPolicyOut` (same fields as `MfaPolicy`), `session_idle_seconds: int | None`, `session_absolute_seconds: int | None`, `scim_enabled: bool`, `scim_adopt_existing: bool`; the PATCH model += the same (all optional, `mfa_policy` as `MfaPolicyIn` with every field optional, merged onto the stored policy); `IdentityCapabilitiesOut` += `session_idle_max_seconds: int`, `session_absolute_max_seconds: int`, `scim_base_url: str | None` (`base_url(settings) + "/scim/v2"`, None without `public_base_url`). `TestResultOut` (whatever `GET …/tests/{test_id}` returns) += `mfa: MfaCheckOut | None` (`required`, `satisfied`, `evidence: list[str]`).

New router `api/v1/admin/identity_scim.py`, prefix `/admin/identity-providers/{provider_id}/scim`, `AdminUser`:

| Route | Body / query | Answer |
|---|---|---|
| `GET ""` | — | `ScimStatusOut {base_url: str \| None, available: bool, reason: str \| None, enabled, adopt_existing, tokens: list[ScimTokenOut], users: int, active_users: int, groups: int, pending_sync: int, last_activity_at: UtcDateTime \| None}`; `available=false` + German `reason` for env/LDAP providers |
| `POST /tokens` | `ScimTokenCreateIn {name: str, user_kind: "agent"\|"customer", expires_in_days: int = 365}` | 201 `ScimTokenCreatedOut {token: str, record: ScimTokenOut}` — the only time the token is returned |
| `DELETE /tokens/{token_id}` | — | 204 (idempotent) |
| `GET /accounts` | `offset=0, limit=50 (≤200), q: str \| None, include_deleted: bool = False` | `ScimAccountPageOut {total, items: list[ScimAccountOut {scim_id, user_kind, user_id, login, user_name, external_id, active, deleted, changed}]}` |
| `GET /groups` | `offset, limit` | `ScimGroupPageOut {total, items: list[ScimGroupOut {scim_id, display_name, external_id, member_count, changed}]}` |
| `GET /events` | `before_id: int \| None, limit=50 (≤200), failures_only: bool = False` | `list[ScimEventOut {id, created, method, resource, operation, status, scim_id, user_kind, user_id, login: str \| None, detail}]` (`login` resolved by IN-list) |

`ScimTokenOut {id, name, user_kind, token_hint, created_at, created_by_name, expires_at, revoked_at, last_used_at, active}`. Errors: `ScimTokenError` → 422 `{"detail": {"errors": {...}}}` (same shape as the existing provider validation errors — check `identity_providers.py` and reuse its error helper).

### S12. Frontend contracts (Tasks 19–21)

- `IdentityProviderTab` = `"connection" | "mapping" | "roles" | "session" | "provisioning" | "test" | "links"`; `TABS` shows `session` and `provisioning` only when `provider.kind !== "ldap"`; for `source === "env"` both tabs render a read-only notice ("Dieser Anmeldedienst ist über Umgebungsvariablen eingerichtet …").
- `identityApi.ts`: `getScimStatus(id)`, `createScimToken(id, body)`, `revokeScimToken(id, tokenId)`, `listScimAccounts(id, params)`, `listScimGroups(id, params)`, `listScimEvents(id, params)`, `getPendingLogin()` (agent), and `portalApi`-based `getPortalPendingLogin()`; query keys `["identity", id, "scim", …]`.
- `AuthContext`: new `refreshPending(): Promise<void>` — calls `GET /auth/pending` and sets `pending2fa` / `pendingFactors` / `mustEnroll2fa` from the answer; `LoginPage` calls it once when `search.sso_step_up` is set and shows the existing 2FA / enrolment step. `CustomerAuthContext` gets the same for the portal (`pending2fa`, factors). `router.tsx`: `sso_step_up?: string` in both login routes' search validation.
- `ssoErrors.ts` `SSO_ERROR_CODES` += `"mfa_required"` (the existing test compares with the backend list).

### S13. i18n (en + de, then propagate)

- `admin.identity.tabs.{session,provisioning}` = "Sitzung & zweiter Faktor", "Bereitstellung (SCIM)"
- `admin.identity.session.{title,intro,requireMfa,requireMfaHelp,fallback.{local_second_factor,reject},fallbackHelp,oidcAmr,oidcAcr,oidcRequestAcr,samlClasses,samlAttribute,samlAttributeValues,samlRequest,valuesHelp,lifetimeTitle,idleMinutes,absoluteHours,useGlobal,globalHint,saved,envReadOnly,ldapUnavailable}`
- `admin.identity.scim.{title,intro,enabled,enabledHelp,adopt,adoptHelp,baseUrl,baseUrlMissing,unavailable,tokens.{title,empty,create,name,userKind,expiresInDays,created,copyHint ("Der Schlüssel wird nur jetzt angezeigt. Bitte kopieren Sie ihn und tragen Sie ihn beim Anmeldedienst ein."),revoke,revokeConfirm,expires,lastUsed,never,revoked},counts.{users,active,groups,pending},accounts.{title,search,empty,active,inactive,deleted,login,userName,externalId,changed},groups.{title,empty,members,rulesHint ("Gruppen wirken über Regeln mit dem Attribut scim:groups im Reiter „Rollen“.")},events.{title,empty,failuresOnly,more,operation.<ScimOperation>,status}}`
- `admin.identity.test.mfa.{title,required,notRequired,satisfied,missing,evidence}`
- `admin.identity.roles.scimHint` = "Für Gruppen aus der Bereitstellung: Attribut scim:groups (Gruppenname) oder scim:group_ids (Kennung beim Anmeldedienst)."
- `auth.ssoErrors.mfa_required`; `auth.stepUp.{title,hint}` ("Ihr Anmeldedienst hat keinen zweiten Faktor bestätigt. Bitte bestätigen Sie die Anmeldung mit Ihrem zweiten Faktor."); `portal.login.stepUp.{title,hint}`
- `admin.daemons.services.scim_role_sync.{name,description}` = "Rollen aus Gruppen der Bereitstellung" / "Überträgt Gruppenänderungen vom Anmeldedienst auf Rollen und Gruppen in Tiqora."; `admin.daemons.services.scim_events_purge.{name,description}` = "Bereitstellungsprotokoll aufräumen" / "Löscht Einträge des SCIM-Protokolls nach Ablauf der Aufbewahrungsfrist."

### S14. Cross-plan contracts

Wave 4 writers in parallel: **itsm1**, **cmdb**, **plugins2a**, **scim** (this plan) in parallel worktrees; **itsm-import** after cmdb+itsm1; **plugins2b** after plugins2a.

| Shared surface | Owner | Contract |
|---|---|---|
| `domain/user_provisioning.set_agent_validity(session, user: Users, *, valid_id: int, actor_id: int) -> bool` and `set_customer_validity(session, cu: CustomerUser, *, valid_id: int, actor_id: int) -> bool` | **scim** | The only way to change `valid_id` of agents/customers from Tiqora code: sets `valid_id`, `change_time`, `change_by`, enqueues `USER_CACHE_TYPES` / `CUSTOMER_USER_CACHE_TYPES`; flush only; returns False and writes nothing when unchanged; Znuny-identical: no system-user guard (Znuny lets an admin invalidate user 1, and the admin routes keep that behaviour); callers that must never touch user 1 (SCIM, S6) check it themselves. Other plans that deactivate accounts call these. |
| `domain/session_revocation.end_agent_sessions(session, redis, settings, user_id: int, *, reason: str) -> int` / `end_customer_sessions(session, redis, settings, logins: Sequence[str], *, reason: str) -> int` | **scim** | Two-phase (Znuny `sessions` rows in own try + commit/rollback, then Redis), never raises; call only after the account change committed. |
| `SessionStore` / `CustomerSessionStore` (`create`, `touch`, `create_pending`, `create_enroll`, `promote_*`), `SsoSessionInfo` | **scim** | Born-marker value format `"i=<idle>"`; legacy `"1"` keeps working. No other wave-4 plan edits these files. |
| SCIM never deletes `users` / `customer_user` rows | **scim** | itsm1/cmdb references to agents/customers (CI owners, incident assignees) stay valid; deprovisioned accounts appear with `valid_id = 2`. ITSM pickers should filter `valid_id = 1` as everywhere else. |
| `worker/__main__.py` loop list, `worker/services.py` `DAEMON_SERVICES`, `domain/settings_store.py` keys, `config.py` fields, `api/app.py` router includes, `db/tiqora/models.py`, `domain/user_delete.py` `OWNED`, `gdpr/erasure.py` | shared, append-only | Each plan appends its own block (no reordering, no reformatting of others' lines); `/scim/v2` is included before `mount_spa`; plugins2a must not add `/scim` to `csrf._PROTECTED_PREFIXES` nor mount plugin routes under `/scim`. |
| Migration numbering | controller | Each plan writes its migration on the head in its worktree; the controller re-chains at merge. This plan's file name suffix: `_identity_scim`. |
| i18n | shared | This plan only adds keys under `admin.identity.session.*`, `admin.identity.scim.*`, `admin.identity.test.mfa.*`, `admin.identity.tabs.{session,provisioning}`, `admin.identity.roles.scimHint`, `auth.ssoErrors.mfa_required`, `auth.stepUp.*`, `portal.login.stepUp.*`, `admin.daemons.services.scim_*`. |
| Seed band | each plan | scim: 63000–63399 (see Global Constraints). |
| Plugin/extension events (plugins2a/2b) | **plugins2b** (if it adds core identity events) | This plan emits no plugin events. The single hook point for a future "account provisioned/deprovisioned" event is `identity/scim/audit.record_event` (signature above, stable); plugins2b may subscribe there without changing SCIM behaviour. |
| MCP tools (itsm-import adds ConfigItem MCP tools) | itsm-import | No SCIM MCP tools in wave 4; SCIM tokens are not API keys and never authorise `/api` or MCP. |

### Deviations from spec

1. **Token-level user kind.** The spec scopes a token to "that provider's audience"; a provider with audience `both` would be ambiguous, so each token carries `user_kind` (agent or customer, within the audience). One provider can have an agent token and a customer token.
2. **`require_mfa_claim` + `mfa_policy`.** The spec names one boolean; IdPs differ (Entra `amr: mfa`, Keycloak `acr` levels, SAML class refs or the Microsoft `authnmethodsreferences` attribute), so the accepted values are configurable with sensible defaults, and the fallback can be "reject".
3. **SCIM groups are mirrored, not mapped 1:1 onto Znuny roles.** They live in `tiqora_scim_group*` and change Znuny roles/groups only through the wave-1 rule engine (`scim:groups` / `scim:group_ids`) with managed assignments; the effect is applied by the `scim_role_sync` worker within ~15 s (keeps every request short).
4. **Own mapping table `tiqora_scim_user`** instead of `tiqora_identity_link`: the SCIM `externalId` (e.g. Entra objectId/mailNickname) is not the OIDC `sub` (pairwise for Entra) nor the SAML NameID, so a link would never match; the first SSO login creates the link as before (match by login/e-mail).
5. **SAML `SessionNotOnOrAfter` is honoured** (shortens the absolute lifetime) — not in the spec, consistent with "per-IdP session lifetime".
6. **Not implemented SCIM features:** `/Bulk`, sorting, ETags/`If-Match`, `/Me`, password change, `POST /.search`, filters other than `eq` on the listed attributes (all announced as unsupported in `ServiceProviderConfig`).
7. **No Keycloak SCIM round trip in CI** (Keycloak has no built-in SCIM client); conformance is pinned by recorded Entra ID and Okta request sequences (made-up data) replayed against the endpoint (Task 13).
8. **Discovery endpoints require the token** (RFC 7644 allows anonymous access; conservative).
9. **Step-up and lifetimes are not available for env providers and LDAP** (open question 9).

---

## Tasks

Order: backend 1 → 18 sequential (each builds on the committed previous ones; the pure Tasks 6, 7, 8 may run in parallel with 3–5; 15 may start after 2). Frontend 19 → 21 need Task 18's regenerated `openapi.json` (21 needs 16 too). Task 22 closes.

Seed sub-bands: T1 63000–63019 · T2 63020–63039 · T3 63040–63079 · T4 63080–63099 · T5 63100–63119 · T9 63120–63189 · T10 63190–63239 · T11 63240–63279 · T12 63280–63319 · T13 63320–63349 · T14 63350–63359 · T16 63360–63379 · T17 63380–63389 · T18 63390–63394 · T22 63395–63399.

### Task 1: Migration, ORM models, constants (backend)

**Files:**
- Create: `backend/alembic/versions_tiqora/<next free number at implementation time>_identity_scim.py`
- Modify: `backend/src/tiqora/db/tiqora/models.py` (S1), `backend/src/tiqora/identity/types.py` (S2 constants), `backend/src/tiqora/identity/scim/__init__.py`, `backend/src/tiqora/identity/scim/constants.py` (S3/S5 constants), `backend/tests/test_migration_gate.py`, `backend/tests/test_migrate_cli.py`
- Test: `backend/tests/test_identity_scim_tables.py` (both dialects, band 63000–63019), `backend/tests/test_tiqora_model_ddl.py` (runs as is)

**Interfaces:**
- Consumes: wave-1 `TiqoraIdentityProvider`.
- Produces: S1 tables/columns and ORM classes `TiqoraScimToken`, `TiqoraScimUser`, `TiqoraScimGroup`, `TiqoraScimGroupMember`, `TiqoraScimPendingSync`, `TiqoraScimEvent`; constants `SCIM_KINDS`, `SESSION_IDLE_MIN`, `SESSION_ABSOLUTE_MIN`, `SESSION_FLOOR_SECONDS`, `TOKEN_PREFIX`, `MAX_ACTIVE_TOKENS`, `TOKEN_DEFAULT_DAYS`, `TOKEN_MAX_DAYS`, `LAST_USED_WRITE_INTERVAL`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  @pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
  async def test_provider_new_columns_default(url_fixture, request) -> None:
      # insert a provider row via make_provider(...) without the new columns →
      # require_mfa_claim False, mfa_policy None, session_* None, scim_enabled False, scim_adopt_existing False
  async def test_scim_user_unique_per_account(url_fixture, request) -> None:
      # two tiqora_scim_user rows (user_kind "agent", user_id 63001) under two providers → IntegrityError
  async def test_scim_member_cascades_with_group_and_user(url_fixture, request) -> None:
      # group + user + member; delete group → member gone; re-add, delete user row → member gone
  async def test_provider_delete_cascades_scim_rows_not_events(url_fixture, request) -> None:
      # token, user, group, pending row, event(provider_id=p.id); delete provider → all gone except the event
  async def test_token_hash_unique(url_fixture, request) -> None: ...
  def test_constants() -> None:
      assert SCIM_KINDS == frozenset({"oidc", "saml"}) and TOKEN_PREFIX == "tqscim_" and SESSION_FLOOR_SECONDS == 60
  ```
- [ ] **Step 2: Run** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_identity_scim_tables.py` → FAIL.
- [ ] **Step 3: Implement** S1: migration with `add_column` (server defaults for NOT NULL booleans), `create_table` for the six tables (FKs with `ondelete="CASCADE"` as listed; `tiqora_scim_event` without FK), indexes/uniques with the exact names; downgrade drops in reverse order. ORM classes produce identical DDL. Update `TIQORA_HEAD` and `test_migrate_cli.py`; `uv run alembic heads` shows one primary head.
- [ ] **Step 4: Run** the test + `tests/test_tiqora_model_ddl.py tests/test_migration_gate.py tests/test_migrate_cli.py tests/test_identity_tables.py` → PASS; lint gates; commit `feat(identity): SCIM, step-up and session lifetime tables`.

### Task 2: Provider record, policy model and validation (backend)

**Files:**
- Modify: `backend/src/tiqora/identity/config_models.py` (`MfaPolicy`, `DEFAULT_SAML_MFA_CLASSES`, `MfaFallback`), `backend/src/tiqora/identity/store.py` (S2), `backend/src/tiqora/identity/mapping.py` (S7 constants), `backend/src/tiqora/api/v1/admin/identity_providers.py` (pass `settings=` to `validate_changes`)
- Create: `backend/src/tiqora/identity/scim/role_sync.py` with `enqueue_role_sync` fully implemented (S7; `update_provider` imports it inside the function); `rules_use_scim` goes into `identity/mapping.py`; `scim_attributes` and the tick are added in Task 11
- Test: `backend/tests/test_identity_config_models.py` (extend), `backend/tests/test_identity_store.py` (extend, both dialects, band 63020–63039)

**Interfaces:**
- Consumes: Task 1.
- Produces: `MfaPolicy`; `ProviderRecord.require_mfa_claim/mfa_policy/session_idle_seconds/session_absolute_seconds/scim_enabled/scim_adopt_existing`; `ProviderChanges` same; `validate_changes(session, changes, *, settings)`; `rules_use_scim(rules) -> bool`; `enqueue_role_sync(session, provider_id, user_ids) -> int`; `SCIM_GROUPS_ATTR`, `SCIM_GROUP_IDS_ATTR`, `SCIM_ATTRS`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  def test_mfa_policy_defaults_and_cleaning() -> None:
      p = MfaPolicy.model_validate({"oidc_amr_values": [" mfa ", "", "mfa", "otp"]})
      assert p.oidc_amr_values == ["mfa", "otp"] and p.fallback == "local_second_factor"
      assert "http://schemas.microsoft.com/claims/multipleauthn" in p.saml_authn_context_classes
      with pytest.raises(ValidationError): MfaPolicy.model_validate({"fallback": "allow"})
      with pytest.raises(ValidationError): MfaPolicy.model_validate({"oidc_amr_values": ["x"] * 21})
  def test_rules_use_scim() -> None:
      assert rules_use_scim([RoleRule(attribute=" scim:groups ", value="Support", role_ids=[1])])
      assert not rules_use_scim([RoleRule(attribute="groups", value="x", role_ids=[1])])
  async def test_update_roundtrip_new_fields(url_fixture, request) -> None:
      # update_provider(... ProviderChanges(require_mfa_claim=True, mfa_policy=MfaPolicy(fallback="reject"),
      #   session_idle_seconds=900, session_absolute_seconds=3600, scim_enabled=True, scim_adopt_existing=True))
      # → get_provider shows them; ProviderChanges(session_idle_seconds=None) explicitly set → back to None
  async def test_lifetime_bounds(url_fixture, request) -> None:
      # settings session_ttl 3600 / absolute 43200: idle 299 → error key session_idle_seconds; idle 3601 → error;
      # absolute 899 → error; idle 3600 + absolute 1800 → error on session_idle_seconds
  async def test_env_provider_refuses_new_fields(url_fixture, request) -> None: ...   # ProviderReadOnly
  async def test_ldap_provider_refuses_new_fields(url_fixture, request) -> None: ...  # ProviderValidationError
  async def test_mixed_scim_rules_refused(url_fixture, request) -> None: ...          # error key role_rules
  async def test_invalid_mfa_policy_json_reads_defaults(url_fixture, request) -> None:
      # write "{oops" into mfa_policy → provider_from_row → MfaPolicy() (no exception)
  async def test_enqueue_role_sync_upserts(url_fixture, request) -> None:
      # enqueue [63021, 63022] then [63022] → two rows, 63022's queued_at is the newer one
  async def test_rule_change_enqueues_managed_agents(url_fixture, request) -> None:
      # provider with scim rules + two tiqora_scim_user agent rows (one deleted) → update role_rules → one pending row
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S2 exactly (frozen dataclass defaults via `field(default_factory=MfaPolicy)`; `provider_from_row` tolerant JSON; `_NULLABLE_FIELDS` += `session_idle_seconds`, `session_absolute_seconds`; `record_with_changes` handles the new fields; `enqueue_role_sync` as S7: delete-then-bulk-insert, flush only, empty ids → 0). Update the call in `api/v1/admin/identity_providers.py` to pass settings (no API model change yet — Task 18).
- [ ] **Step 4: Run** the tests + all `tests/test_identity_*.py` + `tests/test_admin_identity_providers_api.py` → PASS; lint gates; commit `feat(identity): step-up policy, session lifetime and SCIM switches on providers`.

### Task 3: Validity write services and session revocation (backend)

**Files:**
- Modify: `backend/src/tiqora/domain/user_provisioning.py` (`set_agent_validity`, `set_customer_validity`), `backend/src/tiqora/api/v1/admin/users.py` (`deactivate_user`, `update_user` valid_id path, `_revoke_agent_sessions` → `end_agent_sessions`), `backend/src/tiqora/api/v1/admin/customers.py` (`deactivate_customer_user`, update/bulk valid paths, `_revoke_customer_sessions` → `end_customer_sessions`)
- Create: `backend/src/tiqora/domain/session_revocation.py`
- Test: `backend/tests/test_session_revocation.py` (both dialects, band 63040–63079); existing `tests/test_admin_users*.py`, `tests/test_admin_customers*.py` stay green

**Interfaces:**
- Consumes: `delete_agent_sessions`, `delete_customer_sessions`, `SessionStore.revoke_user_sessions`, `revoke_customer_redis_sessions`, `invalidate_znuny_cache_types`.
- Produces (S14 contracts): `set_agent_validity(session, user, *, valid_id, actor_id) -> bool`, `set_customer_validity(session, cu, *, valid_id, actor_id) -> bool`, `end_agent_sessions(session, redis, settings, user_id, *, reason) -> int`, `end_customer_sessions(session, redis, settings, logins, *, reason) -> int`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_set_agent_validity_writes_like_admin(url_fixture, request) -> None:
      # seed agent 63041; set_agent_validity(valid_id=2, actor_id=63040) → True; valid_id 2, change_by 63040,
      # change_time moved; tiqora_cache_invalidation has the USER_CACHE_TYPES rows; second call → False, no new rows
  async def test_set_customer_validity(url_fixture, request) -> None: ...  # CUSTOMER_USER_CACHE_TYPES
  async def test_end_agent_sessions_removes_all_kinds(url_fixture, request) -> None:
      # FakeRedis: full session, pending, enroll token of 63042 + a Znuny sessions row → end_agent_sessions → 3 Redis
      # tokens gone, Znuny row gone, return value 3; another agent's session untouched
  async def test_end_agent_sessions_redis_down_never_raises(url_fixture, request) -> None:
      # redis whose smembers raises → returns 0, Znuny rows still deleted
  async def test_end_customer_sessions_portal_and_gi_tokens(url_fixture, request) -> None: ...
  async def test_admin_deactivate_uses_service(url_fixture, request) -> None:
      # DELETE /api/v1/admin/users/{id} → same rows as before the refactor (valid_id 2, invalidation, sessions ended)
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Validity services: unchanged value → return False without writes; else set fields, `invalidate_znuny_cache_types`, flush. `session_revocation.py` moves the bodies of `_revoke_agent_sessions` / `_revoke_customer_sessions` (keep their log event names `admin.users.sessions_revoked` etc. by passing a `log_prefix`? — no: new names `sessions.ended`, `sessions.znuny_revoke_failed`, `sessions.redis_revoke_failed` with `reason`) and returns the Redis count. Admin routes call the services (behaviour identical; their existing tests are the regression net).
- [ ] **Step 4: Run** the new module + `tests/test_admin_users*.py tests/test_admin_customers*.py tests/test_user_provisioning*.py` → PASS; lint gates; commit `refactor(users): validity write service and one session-ending path`.

### Task 4: Tokens, tenant authentication, rate limiting (backend)

**Files:**
- Create: `backend/src/tiqora/identity/scim/tokens.py`
- Modify: `backend/src/tiqora/security/ratelimit.py` (`ScimRateLimiter`), `backend/src/tiqora/config.py` (S4)
- Test: `backend/tests/test_scim_tokens.py` (both dialects, band 63080–63099), `backend/tests/test_scim_ratelimit.py` (FakeRedis, pure)

**Interfaces:**
- Consumes: Tasks 1–2, `get_provider`.
- Produces: S3 (`ScimTokenRecord`, `ScimTenant`, `ScimTokenError`, `hash_token`, `new_token`, `create_token`, `list_tokens`, `revoke_token`, `authenticate_token`), S4 (`ScimRateLimiter`, settings).

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_create_returns_raw_once_and_stores_hash(url_fixture, request) -> None:
      rec, raw = await create_token(db, provider=p, user_kind="agent", name="Entra", expires_in_days=365, actor_id=63080)
      row = await db.get(TiqoraScimToken, rec.id)
      assert raw.startswith("tqscim_") and len(raw) >= 50 and row.token_hash == hash_token(raw)
      assert raw not in (row.token_hash, row.token_hint) and row.token_hint == raw[-4:]
  async def test_create_rules(url_fixture, request) -> None:
      # customer kind on agent-audience provider → ScimTokenError(errors["user_kind"]); expires 0 / 731 → error;
      # env provider → error; ldap provider → error; 6th active token → error["name"... or "tokens"]
  async def test_authenticate_happy_path_and_last_used_throttle(url_fixture, request) -> None:
      # authenticate twice within 60 s → one last_used_at write (compare value)
  @pytest.mark.parametrize("case", ["unknown", "revoked", "expired", "provider_disabled", "scim_disabled",
                                    "audience_changed", "no_prefix", "non_ascii", "too_long"])
  async def test_authenticate_rejects(url_fixture, request, case) -> None: ...   # None
  async def test_authenticate_uses_compare_digest(monkeypatch, ...) -> None:
      # monkeypatch tiqora.identity.scim.tokens.hmac.compare_digest with a spy → called once on success
  async def test_revoke_is_idempotent_and_audited(url_fixture, request) -> None: ...
  # test_scim_ratelimit.py
  async def test_ip_lockout_after_max_failures() -> None:
      lim = ScimRateLimiter(FakeRedis(), settings_with(scim_auth_fail_max=3, scim_auth_lockout_seconds=900))
      for _ in range(3): await lim.record_auth_failure("192.0.2.7")
      d = await lim.check_ip("192.0.2.7"); assert not d.allowed and d.retry_after > 0
      assert (await lim.check_ip("192.0.2.8")).allowed
  async def test_token_window() -> None: ...                 # max 2 → third check denied with retry_after
  async def test_keys_do_not_collide_with_api_keys() -> None:
      # token id 5 counted → FakeRedis has "tiqora:scim:rl:token:5" and no "tiqora:rl:apikey:5"
  async def test_fail_open_when_redis_raises() -> None: ...
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S3/S4. Admin token events go through `record_event_in` once Task 5 exists — in this task write the event row directly with `TiqoraScimEvent(...)` inside the same flush (Task 5 then replaces it with `record_event_in`; mention in the commit body).
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(scim): hashed provisioning tokens and rate limits`.

### Task 5: Audit log and purge loop (backend)

**Files:**
- Create: `backend/src/tiqora/identity/scim/audit.py`
- Modify: `backend/src/tiqora/identity/scim/tokens.py` (use `record_event_in`), `backend/src/tiqora/worker/__main__.py` (`_daily_loop("scim_events_purge", …, "04:40", stop)`), `backend/src/tiqora/worker/services.py`, `backend/src/tiqora/domain/settings_store.py` (`KEY_SCIM_EVENTS_PURGE_ENABLED`)
- Test: `backend/tests/test_scim_audit.py` (both dialects, band 63100–63119), `backend/tests/test_worker_services.py` (extend if it enumerates the catalog)

**Interfaces:**
- Consumes: Task 1.
- Produces: S8 (`record_event`, `record_event_in`, `list_events`, `purge_events`, `run_scim_events_purge_tick`), `ScimOperation` (defined here, re-exported by `users.py`).

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_record_event_own_transaction(url_fixture, request) -> None:
      # caller session rolls back its own work; record_event(factory, ...) row persists
  async def test_record_event_never_raises(...) -> None:
      # factory whose session.commit raises → no exception, warning logged (caplog / structlog capture)
  async def test_detail_truncated(...) -> None: ...                     # 600 chars → 500
  async def test_list_events_newest_first_and_failures_only(...) -> None:
      # statuses 201, 409, 401 → failures_only returns 409 and 401; before_id pages
  async def test_purge_respects_retention(...) -> None:
      # rows at now-181d and now-10d; run_scim_events_purge_tick with retention 180 → {"deleted": 1}
  def test_daemon_catalog_has_purge() -> None:
      svc = {s.slug: s for s in DAEMON_SERVICES}["scim_events_purge"]
      assert svc.schedule_kind == "daily" and svc.daily_at == "04:40"
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S8 (tick honours `KEY_SCIM_EVENTS_PURGE_ENABLED` like the other daily ticks).
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(scim): provisioning log with retention`.

### Task 6: Protocol layer — errors, filters, lists, discovery (backend, pure)

**Files:**
- Create: `backend/src/tiqora/identity/scim/errors.py`, `backend/src/tiqora/identity/scim/protocol.py`
- Test: `backend/tests/test_scim_protocol.py`

**Interfaces:**
- Consumes: nothing.
- Produces: S5 (`ScimError`, `ScimType`, `scim_error_body`, URNs, `ScimFilter`, `parse_filter`, `ListParams`, `parse_list_params`, `project`, `list_response`, `meta`, `service_provider_config`, `resource_types`, `schemas_document`).

- [ ] **Step 1: Write the failing tests.**
  ```python
  def test_error_body_status_is_string() -> None:
      assert scim_error_body(409, "uniqueness", "dup") == {
          "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"], "status": "409",
          "scimType": "uniqueness", "detail": "dup"}
      assert "scimType" not in scim_error_body(404, None, "x")
  @pytest.mark.parametrize("raw,attr,value", [
      ('userName eq "erika@example.test"', "userName", "erika@example.test"),
      ('USERNAME EQ "Erika"', "userName", "Erika"),
      ('externalId eq "00aa-11"', "externalId", "00aa-11"),
      ('emails[type eq "work"].value eq "a@example.test"', "emails.value", "a@example.test"),
      ('emails.value eq "a@example.test"', "emails.value", "a@example.test"),
      ('userName eq "say \\"hi\\""', "userName", 'say "hi"'),
  ])
  def test_parse_user_filters(raw, attr, value) -> None: ...
  @pytest.mark.parametrize("raw", ['userName co "e"', 'userName eq "a" and active eq true', 'active eq true',
                                   'userName eq a', 'userName eq "a" x', 'members eq "1"', '"x" * 1001'])
  def test_rejects_unsupported(raw) -> None: ...   # ScimError 400 invalidFilter
  def test_group_filter_display_name() -> None: ...
  def test_list_params_bounds() -> None:
      p = parse_list_params({"startIndex": "0", "count": "500"}); assert (p.start_index, p.count) == (1, 200)
      assert parse_list_params({}).count == 100 and parse_list_params({"count": "-3"}).count == 0
      with pytest.raises(ScimError): parse_list_params({"count": "ten"})
  def test_projection_keeps_id_schemas_meta() -> None:
      doc = {"schemas": [...], "id": "x", "meta": {}, "userName": "u", "emails": [], "members": []}
      assert set(project(doc, parse_list_params({"attributes": "userName"}))) == {"schemas", "id", "meta", "userName"}
      assert "members" not in project(doc, parse_list_params({"excludedAttributes": "members"}))
  def test_list_response_shape() -> None:
      r = list_response(3, [{"id": "a"}], parse_list_params({"startIndex": "2", "count": "1"}))
      assert r == {"schemas": [SCHEMA_LIST], "totalResults": 3, "startIndex": 2, "itemsPerPage": 1,
                   "Resources": [{"id": "a"}]}
  def test_service_provider_config_flags() -> None:
      c = service_provider_config("https://helpdesk.example.org/scim/v2")
      assert c["patch"]["supported"] and not c["bulk"]["supported"] and c["filter"]["maxResults"] == 200
      assert not c["etag"]["supported"] and not c["sort"]["supported"] and not c["changePassword"]["supported"]
  def test_resource_types_groups_flag() -> None: ...
  def test_meta_version_is_weak_etag() -> None: ...   # 'W/"3"', datetimes end with "Z"
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S5 (filter: a small hand-written tokenizer — attribute path, operator, JSON string via `json.loads` of the quoted token; no regex backtracking on attacker input; reject input > 1000 chars first).
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(scim): protocol layer (errors, filters, lists, discovery)`.

### Task 7: PATCH engine with Entra/Okta corpus (backend, pure)

**Files:**
- Create: `backend/src/tiqora/identity/scim/patch.py`, `backend/tests/fixtures/scim/patch_corpus.json` (made-up ids/addresses; each case: `name`, `resource`, `ops`, expected result or error)
- Test: `backend/tests/test_scim_patch.py`

**Interfaces:**
- Consumes: Task 6 (`ScimError`, `SCHEMA_PATCH`).
- Produces: S6a (`PatchOp`, `parse_patch`, `apply_user_patch`, `MemberDelta`, `apply_group_patch`).

- [ ] **Step 1: Write the failing tests** (corpus-driven plus explicit cases):
  ```python
  BASE = {"schemas": [SCHEMA_USER], "id": "u1", "userName": "erika@example.test", "active": True,
          "name": {"givenName": "Erika", "familyName": "Muster"},
          "emails": [{"value": "erika@example.test", "type": "work", "primary": True}]}
  def test_entra_replace_active_string_false() -> None:
      ops = parse_patch({"schemas": [SCHEMA_PATCH], "Operations": [{"op": "Replace", "path": "active", "value": "False"}]})
      doc, touched = apply_user_patch(BASE, ops); assert doc["active"] is False and touched == {"active"}
  def test_okta_valueless_path_dict() -> None:
      ops = parse_patch({"schemas": [SCHEMA_PATCH], "Operations": [{"op": "replace", "value": {"active": False}}]})
      assert apply_user_patch(BASE, ops)[0]["active"] is False
  def test_entra_email_filter_path() -> None:
      # Replace emails[type eq "work"].value → "neu@example.test"; primary preserved
  def test_filter_path_without_match_adds() -> None: ...
  def test_name_given_name() -> None: ...
  def test_remove_username_is_mutability_error() -> None: ...
  def test_unknown_path_invalid_path() -> None: ...
  def test_enterprise_extension_paths_ignored() -> None:
      # "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department" → no error, document unchanged
  def test_bad_schema_or_empty_ops_invalid_syntax() -> None: ...
  def test_group_add_and_remove_members() -> None:
      d = apply_group_patch(parse_patch(_p([{"op": "add", "path": "members", "value": [{"value": "a"}, {"value": "b"}]},
                                           {"op": "remove", "path": 'members[value eq "c"]'}])))
      assert d.add == {"a", "b"} and d.remove == {"c"} and d.replace_all is None
  def test_entra_remove_members_with_value_list() -> None: ...   # {"op": "Remove", "path": "members", "value": [{"value": "a"}]}
  def test_remove_all_members() -> None: ...                     # remove "members" without value → replace_all == frozenset()
  def test_replace_display_name() -> None: ...
  def test_member_limit() -> None: ...                           # > max ids → ScimError 400 invalidValue
  @pytest.mark.parametrize("case", _load_corpus(), ids=lambda c: c["name"])
  def test_corpus(case) -> None: ...
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S6a; the corpus holds at least the documented Entra ID request shapes (Replace/Add with capitalised ops, `"False"` strings, `emails[type eq "work"].value`, `name.givenName`, `Remove members` with value list) and Okta shapes (lower-case ops, value-only dicts, `members` add/remove). `apply_group_patch` takes `max_members` (default 5000) as a keyword.
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(scim): PATCH engine with Entra and Okta request shapes`.

### Task 8: User resource mapping (backend, pure)

**Files:**
- Create: `backend/src/tiqora/identity/scim/users_map.py`
- Test: `backend/tests/test_scim_users_map.py`

**Interfaces:**
- Consumes: Task 6, wave-1 `MappedProfile`.
- Produces: S6 (`ScimUserInput`, `parse_user`, `to_profile`, `user_resource`).

- [ ] **Step 1: Write the failing tests.**
  ```python
  def test_parse_minimal_entra_user() -> None:
      inp = parse_user({"schemas": [SCHEMA_USER, SCHEMA_ENTERPRISE], "userName": " sc63.erika@example.test ",
                        "active": "True", "externalId": "erika", "displayName": "Erika Muster",
                        "name": {"givenName": "Erika", "familyName": "Muster"},
                        "emails": [{"primary": True, "type": "work", "value": "Erika@EXAMPLE.test"}],
                        SCHEMA_ENTERPRISE: {"department": "IT"}})
      assert (inp.user_name, inp.active, inp.email, inp.external_id) == (
          "sc63.erika@example.test", True, "Erika@example.test", "erika")
  def test_email_choice_order() -> None: ...           # primary > type work > first
  def test_mobile_only_from_type_mobile() -> None: ...
  @pytest.mark.parametrize("doc", [{}, {"userName": ""}, {"userName": "a\x00b"}, {"userName": "x" * 201},
                                   {"userName": "a", "active": "maybe"}, {"userName": 5}])
  def test_invalid_users(doc) -> None: ...             # ScimError 400 invalidValue/invalidSyntax
  def test_password_and_readonly_ignored() -> None: ...  # "password", "id", "meta", "groups" not in inp.sent semantics
  def test_to_profile_display_name_fallback() -> None:
      p = to_profile(parse_user({"userName": "u@example.test", "displayName": "Max Beispiel Junior"}))
      assert (p.first_name, p.last_name) == ("Max", "Beispiel Junior") and p.customer_id is None
  def test_user_resource_shape() -> None:
      doc = user_resource(scim_id="3f0c…", user_name="u@example.test", external_id=None, active=False,
                          first_name="Max", last_name="Beispiel", email="u@example.test", mobile=None,
                          groups=[("g1", "Support")], location="https://helpdesk.example.org/scim/v2/Users/3f0c…",
                          created=dt, changed=dt, version=2)
      assert doc["active"] is False and "externalId" not in doc and "phoneNumbers" not in doc
      assert doc["groups"][0] == {"value": "g1", "display": "Support",
                                  "$ref": "https://helpdesk.example.org/scim/v2/Groups/g1"}
      assert doc["meta"]["version"] == 'W/"2"'
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S6 mapping (domain lower-casing like `user_provisioning` does; control/bidi characters stripped from names; `ScimError` messages English).
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(scim): user resource mapping`.

### Task 9: User service — create, adopt, update, deactivate, delete (backend)

**Files:**
- Create: `backend/src/tiqora/identity/scim/users.py`
- Test: `backend/tests/test_scim_users_service.py` (both dialects, band 63120–63189; `_scim_helpers.seed_scim_provider`, `make_tenant`)
- Create: `backend/tests/_scim_helpers.py` (`seed_scim_provider(db, *, audience, scim_adopt_existing=False, role_rules=())`, `make_tenant(provider, user_kind)`, cleanup statements for every `tiqora_scim_*` table)

**Interfaces:**
- Consumes: Tasks 2, 3, 6, 7, 8; wave-1 `provision_agent`, `provision_customer`, `current_profile`, `diff_profile`, `sync_profile`; `set_*_validity`; `enqueue_role_sync`.
- Produces: S6 service (`ScimUserResult`, `create_user`, `get_user`, `list_users`, `replace_user`, `patch_user`, `delete_user`).

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_create_agent_writes_through_provisioning(url_fixture, request) -> None:
      r = await create_user(db, tenant, {"userName": "sc63.neu@example.test", "name": {"givenName": "Neu",
                            "familyName": "Agent"}, "emails": [{"value": "sc63.neu@example.test", "primary": True}],
                            "active": True}, base=BASE)
      # users row (login sc63.neu@example.test, pw unusable), UserEmail pref, sso_eligible True, jit initial role
      # granted, cache invalidation rows; tiqora_scim_user row; r.operation == "create"; r.resource["active"] is True
  async def test_create_inactive_creates_invalid(url_fixture, request) -> None: ...   # valid_id 2
  async def test_create_customer_customer_id_fallbacks(url_fixture, request) -> None:
      # jit_defaults.customer_id "sc63-co" → customer_id "sc63-co"; empty → e-mail
  async def test_post_existing_login_conflicts_without_adoption(url_fixture, request) -> None:
      # seed agent sc63.alt → ScimError 409 uniqueness; no scim row; account unchanged
  async def test_adopt_existing_when_enabled(url_fixture, request) -> None:
      # scim_adopt_existing → operation "adopt"; last name updated via sync_profile; login unchanged
  async def test_never_adopts_system_user(url_fixture, request) -> None:
      # userName = login of users.id 1 ("root@localhost") with adoption on → 409; users row 1 unchanged
  async def test_owned_by_other_provider_conflicts(url_fixture, request) -> None: ...
  async def test_duplicate_username_in_tenant_conflicts(url_fixture, request) -> None: ...
  async def test_email_in_use_customer_is_uniqueness(url_fixture, request) -> None: ...
  async def test_invalid_email_is_invalid_value(url_fixture, request) -> None: ...   # "x@example.org" (CheckEmail default)
  async def test_patch_active_false_deactivates(url_fixture, request) -> None:
      r = await patch_user(db, tenant, sid, parse_patch(_replace("active", "False")), base=BASE)
      assert r.deactivated and r.operation == "deactivate"  # valid_id 2 via set_agent_validity
  async def test_patch_active_true_reactivates_and_enqueues(url_fixture, request) -> None: ...
  async def test_username_change_does_not_rename_login(url_fixture, request) -> None:
      # replace userName → users.login unchanged, row user_name new, detail contains "login_not_renamed",
      # filter userName eq <new> finds it
  async def test_put_leaves_unsent_fields(url_fixture, request) -> None: ...  # no phoneNumbers → mobile kept
  async def test_delete_hides_and_deactivates(url_fixture, request) -> None:
      # delete → deactivated True; get_user → 404; list excludes; users row still exists with valid_id 2
  async def test_repost_after_delete_revives(url_fixture, request) -> None:
      # POST same userName → operation "revive", new scim_id, valid_id 1
  async def test_cross_tenant_ids_are_404(url_fixture, request) -> None:
      # scim id of provider B / of customer tenant used with agent tenant of A → 404 for get/put/patch/delete
  async def test_list_shows_only_managed(url_fixture, request) -> None:
      # 2 managed + 1 unmanaged agent with matching userName filter → totalResults counts managed only
  async def test_filter_by_email_and_external_id(url_fixture, request) -> None: ...
  async def test_list_paging(url_fixture, request) -> None: ...  # startIndex 2 count 1
  async def test_active_reflects_znuny(url_fixture, request) -> None:
      # admin sets valid_id 1 by hand after SCIM deactivation → get_user()["active"] is True
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S6. Map `ProvisioningError` codes as specified; IntegrityError on the `tiqora_scim_user` unique (parallel POST) → 409 `uniqueness` after `rollback()`. No joins with Znuny tables (IN-lists). Every write path re-checks `SYSTEM_USER_ID`.
- [ ] **Step 4: Run** → PASS (+ `tests/test_identity_jit.py` unchanged green); lint gates; commit `feat(scim): user provisioning and deprovisioning service`.

### Task 10: Group service and membership (backend)

**Files:**
- Create: `backend/src/tiqora/identity/scim/groups.py`
- Test: `backend/tests/test_scim_groups_service.py` (both dialects, band 63190–63239)

**Interfaces:**
- Consumes: Tasks 6, 7, 9 (`tiqora_scim_user` rows), `enqueue_role_sync`, `rules_use_scim`.
- Produces: S7 group service (`ScimGroupResult`, `create_group`, `get_group`, `list_groups`, `replace_group`, `patch_group`, `delete_group`).

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_create_group_with_members_enqueues(url_fixture, request) -> None:
      # provider with scim rules; 2 managed agents → create_group(displayName "Support", members [a, b, "unknown"])
      # → member rows 2, ignored_members 1, pending rows for both user ids, resource members have display = userName
  async def test_no_enqueue_without_scim_rules(url_fixture, request) -> None: ...
  async def test_patch_add_remove_affects_only_changed(url_fixture, request) -> None:
      # members {a,b} → add c, remove a → affected {a, c}
  async def test_rename_affects_all_members(url_fixture, request) -> None: ...
  async def test_delete_group_affects_all_and_removes_rows(url_fixture, request) -> None: ...
  async def test_put_replaces_member_list(url_fixture, request) -> None: ...
  async def test_deleted_users_are_not_members(url_fixture, request) -> None: ...   # soft-deleted scim user ignored
  async def test_excluded_members(url_fixture, request) -> None:
      # get_group(include_members=False) → no "members" key
  async def test_filter_display_name_case_insensitive(url_fixture, request) -> None: ...
  async def test_put_group_5000_members_is_fast(url_fixture, request) -> None:
      # seed 5000 tiqora_scim_user rows with soft-reference user ids 2_000_000_000–2_000_004_999 (explicit exception
      # to the seed band: never users rows, only tiqora_scim_* rows, removed by this module's own marks/cleanup)
      # replace_group with all 5000 → completes in < 10 s (time.monotonic), member rows 5000, pending rows 5000
      # (mark @pytest.mark.slow only if the module already uses that marker; otherwise keep it in the normal run)
  async def test_member_limit_400(url_fixture, request) -> None: ...                 # 5001 → invalidValue
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S7 group service (bulk `insert(...).values([...])` in chunks of 1000; deletes with IN-lists; `version` +1 on change).
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(scim): groups and membership`.

### Task 11: Role sync from SCIM groups — worker loop and SSO login (backend)

**Files:**
- Modify: `backend/src/tiqora/identity/scim/role_sync.py` (`scim_attributes`, `run_scim_role_sync_tick`), `backend/src/tiqora/identity/login.py` (`_plan_assignments`, S7), `backend/src/tiqora/worker/__main__.py`, `backend/src/tiqora/worker/services.py`, `backend/src/tiqora/domain/settings_store.py` (`KEY_SCIM_ROLE_SYNC_*`)
- Test: `backend/tests/test_scim_role_sync.py` (both dialects, band 63240–63279)

**Interfaces:**
- Consumes: Tasks 2, 9, 10; wave-1 `evaluate_rules`, `diff_assignments`, `load_assignment_state`, `apply_assignments`, `valid_role_ids`, `valid_group_ids`.
- Produces: `scim_attributes(session, *, provider_id, user_id) -> dict[str, tuple[str, ...]]`, `run_scim_role_sync_tick(*, batch=200) -> dict[str, int]`; loop `scim_role_sync`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_tick_grants_role_from_group(url_fixture, request) -> None:
      # role 63241; rule scim:groups == "Support" → role 63241; agent in group "Support" pending →
      # tick → role_user row exists, managed mark recorded, pending row gone, {"processed": 1}
  async def test_group_ids_attribute(url_fixture, request) -> None: ...   # rule on scim:group_ids == externalId
  async def test_manual_role_survives_group_removal(url_fixture, request) -> None:
      # agent has manual role 63242 (no mark) and rule role 63241; remove from group → tick → 63241 revoked, 63242 stays
  async def test_newer_enqueue_survives_tick(url_fixture, request, monkeypatch) -> None:
      # monkeypatch apply_assignments to enqueue again for the same user mid-tick → pending row remains afterwards
  async def test_invalid_or_system_user_skipped(url_fixture, request) -> None: ...
  async def test_error_keeps_row(url_fixture, request, monkeypatch) -> None: ...   # errors 1, row stays
  async def test_sso_login_keeps_scim_roles(url_fixture, request) -> None:
      # agent with SCIM-granted role (mark) and group membership; plan_login with an identity whose attributes
      # lack scim:* (SAML attributes only) → plan.assignments removes nothing (scim attributes injected from DB)
  async def test_jit_preview_with_scim_rules_grants_only_defaults(url_fixture, request) -> None: ...
  def test_daemon_catalog_has_role_sync() -> None: ...
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S7 role sync + the `_plan_assignments` injection (attributes copied into a new dict; `ExternalIdentity` stays frozen). Tick honours `KEY_SCIM_ROLE_SYNC_ENABLED`; each row in its own session from `get_session_factory()`; never `db.begin()` after a read.
- [ ] **Step 4: Run** → PASS (+ `tests/test_identity_role_sync.py tests/test_identity_login_plan.py`); lint gates; commit `feat(scim): roles from provisioned groups via the rule engine`.

### Task 12: SCIM router, authentication dependency, mounting (backend)

**Files:**
- Create: `backend/src/tiqora/api/scim/__init__.py`, `auth.py`, `body.py`, `router.py`
- Modify: `backend/src/tiqora/api/app.py` (include `scim_router` with `prefix=SCIM_PREFIX` before the plugin mount / `mount_spa`)
- Test: `backend/tests/test_scim_api.py` (both dialects, band 63280–63319; full requests through `httpx.ASGITransport` on `create_app()` with FakeRedis via the existing app test pattern of `test_admin_identity_providers_api.py`)

**Interfaces:**
- Consumes: Tasks 3–10.
- Produces: `SCIM_PREFIX = "/scim/v2"`, `require_scim_tenant`, `read_scim_json`, every S5 route.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_discovery_requires_token(url_fixture, request) -> None:
      # GET /scim/v2/ServiceProviderConfig without header → 401, WWW-Authenticate 'Bearer realm="scim"',
      # body schemas Error, status "401", content-type application/scim+json
  async def test_unauthorised_bodies_identical(url_fixture, request) -> None:
      # random token, revoked token, expired token, token of disabled provider → byte-identical bodies
  async def test_query_string_token_ignored(url_fixture, request) -> None:
      # GET /scim/v2/Users?access_token=<valid> → 401
  async def test_bad_tokens_lock_ip(url_fixture, request) -> None:
      # settings scim_auth_fail_max=3: 3 bad requests → 4th (even with a valid token) → 429 + Retry-After
  async def test_token_rate_limit_429(url_fixture, request) -> None: ...
  async def test_user_crud_roundtrip(url_fixture, request) -> None:
      # POST 201 + Location == meta.location; GET 200; PATCH 200 with body; PUT 200; DELETE 204; GET 404
  async def test_deactivate_ends_sessions_before_response(url_fixture, request) -> None:
      # agent has a full session token in FakeRedis + Znuny sessions row → PATCH active False →
      # after the response: SessionStore.get(token) is None, Znuny row gone, AuthService.resolve_session → None
  async def test_deactivate_with_redis_down_still_blocks_session(url_fixture, request) -> None:
      # redis raising on smembers during revoke → 200; resolve_session of the old token → None (valid_id 2)
  async def test_customer_token_has_no_groups(url_fixture, request) -> None:
      # GET /scim/v2/Groups with customer token → 404 SCIM error; ResourceTypes lists only User
  async def test_cross_tenant_ids_are_404(url_fixture, request) -> None: ...
  async def test_errors_are_scim_shaped(url_fixture, request) -> None:
      # wrong content-type 415; invalid JSON 400 invalidSyntax; body > max 413; unknown path /scim/v2/Foo 404;
      # POST /scim/v2/ServiceProviderConfig 405 — all with the SCIM error schema
  async def test_bulk_and_me_not_implemented(url_fixture, request) -> None: ...   # 501
  async def test_events_written_for_writes_and_failures(url_fixture, request) -> None:
      # POST 201 → event create; 409 → event conflict; GET → no event
  async def test_spa_fallback_does_not_shadow_scim(url_fixture, request) -> None:
      # with SPA mounted (TIQORA_SERVE_FRONTEND + a temp dist index.html), GET /scim/v2/Users → SCIM JSON, not HTML;
      # if spa_is_available has no test seam, assert instead that the /scim/v2 routes precede the SPA mount in app.router.routes
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Each endpoint: `tenant = Depends(require_scim_tenant)`, parse with `read_scim_json` / `parse_list_params` / `parse_filter`, call the service, `await session.commit()`; on `ScimError` → `rollback()`; after commit: `result.deactivated` → `end_agent_sessions` / `end_customer_sessions` (await, before returning); then `record_event(...)`; response via a helper `scim_response(status, body, headers=None)` with media type `application/scim+json`. Exception handling local to the router (an `APIRoute` subclass that converts `ScimError`, `HTTPException`, `RequestValidationError` for this router only, plus a catch-all 404/405 route `/{rest:path}` at the end of the router). `request.base_url` fallback for `base` when `public_base_url` is empty.
- [ ] **Step 4: Run** → PASS (+ `tests/test_csrf*.py` unchanged); lint gates; commit `feat(scim): /scim/v2 endpoint`.

### Task 13: Conformance — recorded Entra ID and Okta sequences (backend)

**Files:**
- Create: `backend/tests/fixtures/scim/entra_user_lifecycle.json`, `entra_group_lifecycle.json`, `okta_user_lifecycle.json`, `okta_group_push.json` (made-up data; each step: `method`, `path` with `{id}` placeholders resolved from earlier responses, `body`, expected `status`, expected JSON subset)
- Test: `backend/tests/test_scim_conformance.py` (both dialects, band 63320–63349)

**Interfaces:**
- Consumes: Task 12.
- Produces: the parity artefact referenced by the docs.

- [ ] **Step 1: Write the failing tests** (a small replay runner: substitute `{user_id}`/`{group_id}` from captured `id`s, assert status and that the expected JSON is a subset of the answer):
  ```python
  @pytest.mark.parametrize("sequence", ["entra_user_lifecycle", "entra_group_lifecycle",
                                        "okta_user_lifecycle", "okta_group_push"])
  async def test_sequence(url_fixture, request, sequence) -> None: ...
  ```
  Sequences cover: Entra — `GET /Users?filter=userName eq "…"` (empty) → `POST /Users` → `GET /Users/{id}` → `PATCH` (`Replace` `name.givenName`, `emails[type eq "work"].value`) → `PATCH active "False"` → `GET /Groups?filter=displayName eq "…"&excludedAttributes=members` → `POST /Groups` → `PATCH /Groups/{id}` `Add members` → `PATCH` `Remove members` with value list → `DELETE /Users/{id}`; Okta — `GET /Users?filter=userName eq "…"&startIndex=1&count=100` → `POST /Users` → `PUT /Users/{id}` (full document) → `PATCH {"op":"replace","value":{"active":false}}` → `POST /Groups` with members → `PATCH /Groups/{id}` replace `displayName` → `PUT /Groups/{id}` members → `DELETE /Groups/{id}`; after the group steps run `run_scim_role_sync_tick()` and assert the rule-mapped role arrived.
- [ ] **Step 2: Run** → FAIL only if Task 12 deviates; fix the service, not the fixture, unless the fixture contradicts RFC 7644 (note it in the commit body).
- [ ] **Step 3: Implement** fixes found by the replay.
- [ ] **Step 4: Run** → PASS; lint gates; commit `test(scim): Entra ID and Okta request sequences`.

### Task 14: Account deletion and GDPR erasure purge the SCIM rows (backend)

**Files:**
- Modify: `backend/src/tiqora/domain/user_delete.py` (`OWNED` += `("tiqora_scim_pending_sync", "user_id")`, `("tiqora_scim_user", "user_id")`; `_KIND_SCOPED` += `"tiqora_scim_user"`; group member rows go with the scim user rows by FK cascade — verify on MariaDB and PostgreSQL that the delete path does not bypass cascades (raw `DELETE` statements honour FK `ON DELETE CASCADE`)), `backend/src/tiqora/gdpr/erasure.py` (customers: `DELETE FROM tiqora_scim_user WHERE user_kind = 'customer' AND user_id IN :ids`, guarded by `_table_exists`, both modes)
- Test: `backend/tests/test_scim_purge.py` (both dialects, band 63350–63359)

**Interfaces:**
- Consumes: Task 1.
- Produces: rows owned by deleted/erased accounts are gone; `tiqora_scim_event` rows stay (ids only, no PII).

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_user_delete_removes_scim_rows_agent_only(url_fixture, request) -> None:
      # agent 63351 and customer 63351 both with scim rows; delete agent → agent row gone, customer row stays,
      # membership + pending rows of the agent gone
  async def test_erasure_removes_customer_scim_row(url_fixture, request) -> None: ...   # anonymize + delete modes
  async def test_events_survive(url_fixture, request) -> None: ...
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** (kind-scoped delete as for `tiqora_identity_link`).
- [ ] **Step 4: Run** → PASS (+ `tests/test_user_delete*.py tests/test_gdpr_erasure*.py`); lint gates; commit `feat(scim): purge provisioning rows with deleted or erased accounts`.

### Task 15: MFA evidence from OIDC and SAML, step-up requests (backend)

**Files:**
- Create: `backend/src/tiqora/identity/step_up.py`
- Modify: `backend/src/tiqora/identity/sso_routes.py` (OIDC callback: pop `amr`/`acr` from userinfo attributes, inject from `result.id_token_claims`; SAML ACS: inject `saml:authn_context`, carry `session_not_on_or_after` into `SsoSessionDraft`; login start passes `acr_values` / requested authn context), `backend/src/tiqora/identity/saml_sp.py` (`SamlAssertionResult.authn_context_class_refs`, `session_not_on_or_after`; `_login_redirect_sync` requested context), `backend/src/tiqora/identity/login.py` (`SsoSessionDraft.session_not_on_or_after`), `backend/src/tiqora/domain/oidc.py` (`authorize_url(..., acr_values=None)`)
- Test: `backend/tests/test_identity_step_up.py` (pure), `backend/tests/test_identity_oidc_flow.py` (extend), `backend/tests/test_identity_saml_sp.py` (extend; `pytestmark` saml rules from wave 1)

**Interfaces:**
- Consumes: Task 2 (`MfaPolicy`), wave-1 test IdP (`tests/_saml_idp.py`) and `OidcSigner` (`_identity_helpers.py`).
- Produces: S9 pure part (`MfaCheck`, `check_mfa`, `OIDC_AMR_ATTR`, `OIDC_ACR_ATTR`, `SAML_AUTHN_CONTEXT_ATTR`, `STEP_UP_ATTRS`); adapters populate them.

- [ ] **Step 1: Write the failing tests.**
  ```python
  def test_check_mfa_not_required() -> None: ...           # required False → satisfied True
  def test_oidc_amr_and_acr() -> None:
      rec = _record(kind="oidc", require_mfa_claim=True, mfa_policy=MfaPolicy(oidc_acr_values=["gold"]))
      assert check_mfa(rec, {"oidc:amr": ("pwd", "mfa")}).satisfied
      assert check_mfa(rec, {"oidc:acr": ("gold",)}).satisfied
      assert not check_mfa(rec, {"oidc:amr": ("pwd",)}).satisfied
      assert not check_mfa(rec, {"amr": ("mfa",)}).satisfied          # plain claim name never counts
  def test_saml_class_or_attribute() -> None: ...
  def test_values_are_case_sensitive() -> None: ...        # "MFA" ≠ "mfa"
  async def test_amr_from_userinfo_is_ignored(...) -> None:
      # OIDC callback: userinfo returns {"amr": ["mfa"]}, id_token without amr → identity attributes have no
      # "amr" key and "oidc:amr" == () → with require_mfa_claim the login is not full (asserted in Task 16;
      # here: inspect the ExternalIdentity passed to complete_login via monkeypatch)
  async def test_amr_from_verified_id_token(...) -> None: ...      # OidcSigner id_token amr ["pwd","mfa"]
  async def test_authorize_url_sends_acr_values(...) -> None: ...  # policy.oidc_request_acr_values "gold"
  async def test_saml_result_has_context_and_session_not_after(...) -> None:
      # test IdP issues AuthnContextClassRef MobileTwoFactorContract + SessionNotOnOrAfter → result fields set
  async def test_saml_request_authn_context(...) -> None:
      # policy.saml_request_authn_context → decoded AuthnRequest has RequestedAuthnContext Comparison="minimum"
  async def test_idp_initiated_without_context_is_not_mfa(...) -> None: ...
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S9 extraction and requests (pysaml2 calls stay in the executor via existing `_run`).
- [ ] **Step 4: Run** → PASS (+ all `tests/test_identity_*saml*.py tests/test_identity_oidc_flow.py`); lint gates; commit `feat(identity): read MFA proof from id_token and SAML assertion`.

### Task 16: Step-up in the SSO login, pending state endpoints (backend)

**Files:**
- Modify: `backend/src/tiqora/identity/sso_routes.py` (`_complete_login` per S9; test result `mfa`), `backend/src/tiqora/identity/http.py` (`SSO_ERROR_CODES` += `mfa_required`), `backend/src/tiqora/domain/auth.py` (`create_pending`/`create_enroll`/`promote_*` carry `sso`; `AuthService.create_pending_session(..., sso=None)`, `create_enroll_session(..., sso=None)`), `backend/src/tiqora/domain/customer_auth.py` (same for customers), `backend/src/tiqora/domain/schemas.py` (`PendingLoginOut`), `backend/src/tiqora/api/v1/auth.py` (`GET /auth/pending`), `backend/src/tiqora/api/portal/auth.py` (`GET /auth/pending`)
- Test: `backend/tests/test_identity_step_up_login.py` (both dialects, band 63360–63379)

**Interfaces:**
- Consumes: Task 15; wave-1 TOTP/passkey services for agents and customers.
- Produces: S9 login behaviour; `PendingLoginOut`; `GET /api/v1/auth/pending`, `GET /api/portal/auth/pending`; SSO error code `mfa_required`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_mfa_satisfied_gives_full_session(...) -> None: ...
  async def test_agent_with_totp_gets_pending_and_redirect(...) -> None:
      # OIDC login without amr, require_mfa_claim → 302 Location "/login?sso_step_up=1&next=%2F…";
      # cookie token: SessionStore.get → None, get_pending → (uid, login); GET /api/v1/auth/pending →
      # {"state": "pending_2fa", "totp_enrolled": true, "passkey_enrolled": false}
  async def test_step_up_pending_cookie_is_not_a_session(...) -> None:  # GET /api/v1/auth/me with it → 401
  async def test_agent_without_factor_must_enroll(...) -> None:          # state "must_enroll_2fa"
  async def test_customer_without_factor_rejected(...) -> None:          # 302 to /portal/login?sso_error=mfa_required
  async def test_customer_with_totp_pending(...) -> None: ...
  async def test_fallback_reject(...) -> None: ...                       # agent with TOTP → sso_error=mfa_required
  async def test_promoted_session_keeps_sso_and_lifetime(...) -> None:
      # pending → POST /api/v1/auth/totp/verify with a valid code → new token has SsoSessionInfo (provider id,
      # sid) and the provider's idle TTL (needs Task 17 constants: assert sso info here, lifetime in Task 17)
  async def test_idp_logout_kills_pending_token(...) -> None:            # revoke_sso_sessions by subject → pending gone
  async def test_json_callback_reports_pending(...) -> None:             # env-oidc JSON mode → LoginResponse pending_2fa
  async def test_admin_test_login_reports_mfa(...) -> None:
      # test run → GET …/tests/{id} result.mfa == {"required": true, "satisfied": false, "evidence": ["pwd"]};
      # no session cookie set
  async def test_sso_error_codes_include_mfa_required() -> None: ...
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S9 login flow and endpoints. The pending endpoints never touch the session TTLs (no `touch`).
- [ ] **Step 4: Run** → PASS (+ `tests/test_identity_*` + `tests/test_auth*.py tests/test_portal_auth*.py`); lint gates; regenerate OpenAPI (new `PendingLoginOut`, routes); commit `feat(identity): require MFA from the identity provider with Tiqora fallback`.

### Task 17: Per-provider session lifetime (backend)

**Files:**
- Create: `backend/src/tiqora/identity/session_lifetime.py`
- Modify: `backend/src/tiqora/identity/sso_sessions.py` (S10 fields), `backend/src/tiqora/domain/auth.py` (`SessionStore.create`, `touch`), `backend/src/tiqora/domain/customer_auth.py` (`CustomerSessionStore.create`, `touch`), `backend/src/tiqora/identity/sso_routes.py` (`effective_lifetime` → `SsoSessionInfo`)
- Test: `backend/tests/test_identity_session_lifetime.py` (pure + FakeRedis; one both-dialect login test, band 63380–63389)

**Interfaces:**
- Consumes: Tasks 2, 15, 16.
- Produces: S10 (`effective_lifetime`, `SsoSessionInfo.idle_ttl/absolute_ttl`, born value `"i=<n>"`).

- [ ] **Step 1: Write the failing tests.**
  ```python
  def test_effective_lifetime_caps() -> None:
      s = settings_with(session_ttl_seconds=3600, session_absolute_ttl_seconds=43200)
      assert effective_lifetime(_rec(None, None), s, not_on_or_after=None, now=NOW) == (3600, 43200)
      assert effective_lifetime(_rec(900, 7200), s, not_on_or_after=None, now=NOW) == (900, 7200)
      assert effective_lifetime(_rec(900, 7200), s, not_on_or_after=NOW + timedelta(minutes=30), now=NOW) == (900, 1800)
      assert effective_lifetime(_rec(None, None), s, not_on_or_after=NOW - timedelta(hours=1), now=NOW) == (60, 60)
  def test_sso_info_json_roundtrip_with_ttls() -> None: ...   # ints accepted; bool/str for ttl → from_json None
  async def test_create_uses_sso_ttls() -> None:
      store = SessionStore(FakeRedis(), s); tok = await store.create(5, "sc63", sso=_info(idle_ttl=900, absolute_ttl=7200))
      assert redis.ttl(f"tiqora:session:{tok}") <= 900 and redis.ttl(f"tiqora:session:born:{tok}") <= 7200
  async def test_touch_keeps_provider_idle_ttl() -> None:
      # touch after create with idle 900 → main key TTL reset to ≤ 900 (not 3600)
  async def test_legacy_born_value_uses_global() -> None:
      # born key "1" (pre-upgrade session) → touch renews with 3600
  async def test_customer_store_same() -> None: ...
  async def test_sso_login_applies_provider_lifetime(url_fixture, request) -> None:
      # provider session_idle 900 → session main key TTL ≤ 900 after the OIDC callback
  async def test_promoted_step_up_session_keeps_lifetime(url_fixture, request) -> None: ...
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S10 (no change to cookies; `get()` unchanged except accepting any non-empty born value).
- [ ] **Step 4: Run** → PASS (+ `tests/test_auth*.py tests/test_identity_sso_sessions.py tests/test_identity_logout.py`); lint gates; commit `feat(identity): own session lifetime per identity provider`.

### Task 18: Admin API — provider fields, SCIM administration (backend)

**Files:**
- Create: `backend/src/tiqora/api/v1/admin/identity_scim.py`
- Modify: `backend/src/tiqora/api/v1/admin/identity_schemas.py` (S11 models), `backend/src/tiqora/api/v1/admin/identity_providers.py` (out/patch fields, capabilities, test result `mfa`), `backend/src/tiqora/api/v1/admin/__init__.py` (include router), `packages/api-client/openapi.json`, `docs/api/openapi.json`
- Test: `backend/tests/test_admin_identity_scim_api.py` (both dialects, band 63390–63394), `backend/tests/test_admin_identity_providers_api.py` (extend)

**Interfaces:**
- Consumes: Tasks 2, 4, 5, 9, 10, 16.
- Produces: S11 routes and schemas (`ScimStatusOut`, `ScimTokenOut`, `ScimTokenCreateIn`, `ScimTokenCreatedOut`, `ScimAccountOut`, `ScimAccountPageOut`, `ScimGroupOut`, `ScimGroupPageOut`, `ScimEventOut`, `MfaPolicyOut`, `MfaPolicyIn`, `MfaCheckOut`), regenerated API client.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_patch_provider_step_up_and_lifetime(url_fixture, request) -> None:
      # PATCH {"require_mfa_claim": true, "mfa_policy": {"fallback": "reject"}, "session_idle_seconds": 900}
      # → GET shows them; mfa_policy other fields keep defaults; 422 errors for out-of-range values
  async def test_capabilities_include_limits_and_scim_url(url_fixture, request) -> None: ...
  async def test_status_for_env_provider_unavailable(url_fixture, request) -> None: ...
  async def test_create_token_shows_secret_once(url_fixture, request) -> None:
      # POST tokens → token in body; GET status → tokens[0] has token_hint but no token field anywhere
  async def test_revoke_token(url_fixture, request) -> None: ...
  async def test_accounts_groups_events_lists(url_fixture, request) -> None:
      # seeded scim rows → accounts with login resolved, groups with member_count, events newest first
  async def test_non_admin_forbidden(url_fixture, request) -> None: ...   # 403 for every route
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** S11 (pre-checks committed before writes; logins/names via IN-lists). Regenerate OpenAPI per Global Constraints; update `frontend/src/demo/mockData.ts` provider/capabilities objects and `frontend/src/components/admin/identity/testFixtures.ts` with the new required fields so `pnpm lint` stays green (run it).
- [ ] **Step 4: Run** → PASS + `cd frontend && pnpm lint && pnpm test --run`; lint gates; commit `feat(identity): admin API for provisioning, step-up and session lifetime`.

### Task 19: Admin UI — "Bereitstellung (SCIM)" tab (frontend)

**Files:**
- Create: `frontend/src/components/admin/identity/ProvisioningTab.tsx` (+ `.test.tsx`), `ScimTokenDialog.tsx`, `ScimEventsList.tsx`
- Modify: `frontend/src/routes/admin/IdentityProviderPage.tsx` (`IdentityProviderTab`, `TABS`), `frontend/src/lib/identityApi.ts`, `frontend/src/components/admin/identity/RolesTab.tsx` (hint `admin.identity.roles.scimHint` when a rule uses `scim:`), `frontend/src/demo/mockData.ts`, `frontend/src/demo/handlers.ts`, `frontend/src/i18n/locales/en.json`, `de.json` (+ propagate)

**Interfaces:**
- Consumes: Task 18 types (`Schemas["ScimStatusOut"]` …).
- Produces: tab `provisioning`.

Content: switch "Bereitstellung aktiv" (`scim_enabled`) and "Vorhandene Konten übernehmen" (`scim_adopt_existing`, help: "Ein Konto mit gleichem Anmeldenamen wird beim ersten Abgleich vom Anmeldedienst verwaltet – auch deaktiviert. Das Systemkonto (ID 1) wird nie übernommen.") saved through the provider PATCH; "SCIM-Adresse" with `CopyButton` (or `baseUrlMissing` hint); counts; token table (name, Art Agenten/Kunden, `…1a2b`, gültig bis, zuletzt benutzt, Status) with "Schlüssel erzeugen" → `ScimTokenDialog` (name, Art limited to the provider audience, Gültigkeit in Tagen 1–730) → result view with the token, `CopyButton` and the copy hint; closing clears the token from state; "Widerrufen" with confirm; "Bereitgestellte Konten" table with search + paging; "Gruppen vom Anmeldedienst" table (+ rules hint); `ScimEventsList` with "Nur Fehler" filter and "Weitere laden".

- [ ] **Step 1: Write the failing tests** (Vitest + MSW handlers from the demo set):
  ```tsx
  it("shows the token exactly once and forgets it on close", async () => { /* create → token visible → close → not in DOM */ });
  it("offers only the kinds in the provider audience", ...);   // audience agent → no "Kunden" option
  it("shows the read-only notice for env providers", ...);
  it("revokes after confirmation", ...);
  it("lists failed events only when filtered", ...);
  it("shows base URL missing hint", ...);
  ```
- [ ] **Step 2: Run** `cd frontend && pnpm test --run src/components/admin/identity/ProvisioningTab.test.tsx` → FAIL.
- [ ] **Step 3: Implement**; i18n keys S13 (`admin.identity.scim.*`, `admin.identity.tabs.provisioning`, `admin.identity.roles.scimHint`) in en+de, then `node frontend/scripts/propagate-i18n-keys.mjs`; demo mocks; check with `VITE_DEMO=1` + Playwright (delete `.playwright-mcp`).
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS; commit `feat(admin): provisioning (SCIM) tab for identity providers`.

### Task 20: Admin UI — "Sitzung & zweiter Faktor" tab and MFA line in the test result (frontend)

**Files:**
- Create: `frontend/src/components/admin/identity/SessionTab.tsx` (+ `.test.tsx`)
- Modify: `frontend/src/routes/admin/IdentityProviderPage.tsx`, `frontend/src/components/admin/identity/TestTab.tsx` / `LoginPlanView.tsx` (+ tests), demo mocks, i18n en/de (+ propagate)

**Interfaces:**
- Consumes: Task 18 (`MfaPolicyOut`, `IdentityCapabilitiesOut.session_*_max_seconds`, test result `mfa`).
- Produces: tab `session`.

Content: switch "Zweiten Faktor vom Anmeldedienst verlangen"; radio "Ohne Bestätigung: zweiten Faktor in Tiqora abfragen" / "Anmeldung ablehnen"; per kind the value lists as tag inputs (OIDC: amr-Werte, acr-Werte, "acr_values anfordern"; SAML: Authentifizierungsklassen, MFA-Attribut + Werte, "Authentifizierungsklasse anfordern"), with help texts naming Entra ID / Okta / Keycloak defaults; "Sitzungsdauer": "Allgemeine Einstellung verwenden" checkbox or number inputs "Abmeldung nach Inaktivität (Minuten)" (5 … global) and "Spätestens abmelden nach (Stunden)" (0.25 … global), converted to seconds; server errors shown per field. Test tab: line "Zweiter Faktor vom Anmeldedienst: bestätigt / nicht bestätigt / nicht verlangt" plus the evidence values in the technical table.

- [ ] **Step 1: Write the failing tests.**
  ```tsx
  it("sends only changed policy fields", ...);
  it("limits lifetime inputs to the global maximum", ...);
  it("clears to global with null", ...);                  // PATCH {"session_idle_seconds": null}
  it("hides OIDC fields for SAML providers", ...);
  it("shows the MFA result in the test result", ...);    // LoginPlanView with mfa satisfied false
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; i18n `admin.identity.session.*`, `admin.identity.tabs.session`, `admin.identity.test.mfa.*`; demo mocks; Playwright check in demo mode.
- [ ] **Step 4: Run** lint/test/i18n check → PASS; commit `feat(admin): session lifetime and second-factor requirement per identity provider`.

### Task 21: Login pages — second factor after SSO (frontend)

**Files:**
- Modify: `frontend/src/auth/AuthContext.tsx` (`refreshPending`), `frontend/src/auth/CustomerAuthContext.tsx` (`refreshPending`), `frontend/src/routes/LoginPage.tsx`, `frontend/src/routes/portal/PortalLoginPage.tsx`, `frontend/src/router.tsx` (`sso_step_up` search param on `/login` and `/portal/login`), `frontend/src/lib/identityApi.ts` (`getPendingLogin`, `getPortalPendingLogin`), `frontend/src/lib/ssoErrors.ts` (+ test), tests next to each, demo handlers, i18n en/de (+ propagate)

**Interfaces:**
- Consumes: Task 16 (`PendingLoginOut`, routes, `mfa_required`).
- Produces: S12 login behaviour.

- [ ] **Step 1: Write the failing tests.**
  ```tsx
  it("LoginPage with sso_step_up shows the TOTP step from the pending state", ...);   // GET /auth/pending → pending_2fa
  it("LoginPage with sso_step_up shows enrolment when must_enroll_2fa", ...);
  it("LoginPage with sso_step_up and state none shows the normal form", ...);
  it("PortalLoginPage with sso_step_up shows the second-factor step", ...);
  it("maps mfa_required to its message", ...);                                         // ssoErrors.test.ts list compare
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** (call `refreshPending` once per mount when the param is present; reuse the existing step components; after success navigate to `next` via the existing safe-next logic; the hint text `auth.stepUp.hint` above the step).
- [ ] **Step 4: Run** lint/test/i18n check → PASS; commit `feat(auth): second factor after single sign-on when the provider did not confirm one`.

### Task 22: Golden check, docs, full verification

**Files:**
- Create: `tests/golden/test_identity_scim.py`
- Modify: `docs/guide/identity-providers.md` (sections "Bereitstellung mit SCIM" — Entra ID and Okta step by step with made-up tenant values, token handling, adoption, groups → roles via `scim:groups`, what deactivation does, troubleshooting with the log; "Zweiter Faktor vom Anmeldedienst" — values for Entra ID, Okta, Keycloak (acr/LoA mapper), ADFS; "Sitzungsdauer"), `docs/deployment.md` (nginx: `location /scim/ { proxy_pass …; client_max_body_size 2m; proxy_read_timeout 90s; }` — Puppet change required; the new `TIQORA_SCIM_*` settings; trusted proxies for the IP lockout), `docs/architecture.md` (identity: SCIM package, role-sync loop, session lifetime), `docs/compatibility.md` (SCIM 2.0 supported subset and the deviations), daemon docs if the "Dienste" page is documented
- Test: golden (both peers), full backend + frontend suites

**Interfaces:**
- Consumes: everything.
- Produces: release-ready phase 3.

- [ ] **Step 1: Write the golden test** (pattern `tests/golden/test_identity_jit.py`, band 63395–63399, login `sc63.golden.scim@example.test`): create via `create_user` (agent tenant on a provider seeded in the peer DB), add to a SCIM group whose rule grants a role created with `RoleAdd`, run `run_scim_role_sync_tick()`, read back with Znuny (`GetUserData` → `ValidID 1`, `PermissionUserRoleGet` after `Cache CleanUp(Type => 'DBRoleUserGet')` contains the role); then `patch_user` active false → read back `ValidID 2` (after `Cache CleanUp(Type => 'User')`); cleanup via `delete_user_rows` + `RoleDelete`-equivalent SQL like the JIT test.
- [ ] **Step 2: Run** `GOLDEN=1 GOLDEN_PEER=znuny-6.5 just golden-run tests/golden/test_identity_scim.py` and the same for `znuny-7.3` (or `just golden-all-peers` restricted to the module) → PASS (fix code, not Znuny expectations).
- [ ] **Step 3: Docs** as listed; German admin guide uses "Sie"; example values only `example.org`/`.test`.
- [ ] **Step 4: Full verification:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (all), `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`; `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; OpenAPI diff check; `uv run alembic heads` one primary head. Commit `docs(identity): SCIM provisioning, step-up MFA and session lifetime` and `test(golden): SCIM-provisioned agent in Znuny peers`.

---

## Self-review notes (for the executor)

- Spec coverage: SCIM `/scim/v2/Users` + `/Groups` with per-IdP bearer token (Tasks 4, 6–13), scoped to the provider's audience (S3 token `user_kind`), deprovisioning `active=false` → `valid_id = 2` + immediate session revocation, never delete (Tasks 3, 9, 12), step-up `require_mfa_claim` with `amr` / `AuthnContextClassRef` and Tiqora TOTP/passkey fallback (Tasks 15, 16, 21), per-IdP session lifetime (Task 17, 20), admin UI (Tasks 19, 20), security requirements: hashed tokens + constant-time compare (Task 4), rate limiting (Task 4, 12), audit log (Task 5, 12), docs + golden (Task 22).
- Every Znuny write goes through `user_provisioning` / `identity.provisioning` / `znuny.sessions`; the only new Znuny write functions are `set_agent_validity` / `set_customer_validity` (S14 contract), added to the existing write-service module.
- No endpoint does unbounded work: group pushes ≤ 5000 members and only `tiqora_scim_*` writes; Znuny role writes happen in `scim_role_sync`.
- Names cross-checked: `ScimTenant`, `ScimUserResult.deactivated`, `enqueue_role_sync`, `rules_use_scim`, `scim_attributes`, `check_mfa`, `effective_lifetime`, `end_agent_sessions`, `end_customer_sessions`, `PendingLoginOut`, `mfa_required` are used with the same signatures in every task.
