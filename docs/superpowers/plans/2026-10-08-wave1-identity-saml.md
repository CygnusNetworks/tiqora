# Wave 1 — Identity: SAML, multi-IdP SSO, provisioning (phases 1 + 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Several identity providers side by side — OIDC, SAML 2.0 and LDAP — configured and test-logged-in from the admin UI, for agents **and** portal customers, with stable subject binding, just-in-time provisioning, Znuny-compatible role/group sync (managed assignments), single logout (SP-initiated, IdP-initiated, OIDC back-channel) and TOTP/passkeys for customers; the env-configured OIDC that production uses today keeps working without any change on the operator side.

**Architecture:** A new backend package `tiqora.identity` owns three new tables (`tiqora_identity_provider`, `tiqora_identity_link`, `tiqora_identity_managed_assignment`), the provider store (config JSON + one Fernet-encrypted secrets blob), a pure mapping/rule/diff engine, one login orchestration (`plan_login` → `apply_login`) shared by OIDC, SAML and LDAP for both audiences, the pysaml2 service-provider wrapper (sync library + `xmlsec1` subprocess, always in an executor), Redis flow state and an SSO session index for logout. Env OIDC/LDAP become read-only provider rows (`source="env"`) whose runtime config is always rebuilt from `Settings`. Writes to Znuny tables go through one extracted module (`tiqora.domain.user_provisioning`) that the admin user routes also use. Frontend: provider buttons on both login pages, a new admin area "Identitätsanbieter" (tabs Verbindung / Zuordnung / Rollen / Test / Verknüpfungen) built on wave-0 `SchemaForm`, a portal security page for customer 2FA, and SSO logout.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic (two-chain), Pydantic v2, `pysaml2` 7.5.x + `xmlsec1` binary (new uv extra `saml`), authlib (OIDC + JWT for back-channel logout), ldap3, py_webauthn, pyotp, defusedxml, Redis; React 19 + TanStack Router/Query, Tailwind tokens, Vitest, MSW demo mode.

**Spec:** `docs/superpowers/specs/2026-10-08-identity-saml-design.md` (phases 1 and 2; phase 3 — SCIM, step-up MFA claim, per-IdP session lifetime — is out of scope). Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md`. Builds on `docs/superpowers/plans/2026-10-08-wave0-foundations.md` (must be merged first; uses its `SECRET_MASK`, `validate_section_schema`, `UnsupportedSettingsSchema` from `tiqora.settings_sections.section`, `SchemaForm`/`schemaToFieldDefs`/`FieldsForm` from `frontend/src/components/fields`, and the `DaemonService` catalog pattern). Nothing here needs the job system: every endpoint finishes in seconds (metadata fetch has a 10 s timeout).

## Global Constraints

- Commit directly to `main`; never push (a push deploys production). Never `--no-verify` (PII hooks). Only made-up example data (example.org, example.com, idp.example.org …). Use `rtk git` per repo CLAUDE.md (`rtk git add …`, `rtk git commit …`); after every commit run `rtk git log -1 --oneline` because the pre-commit hook can block a chained commit and chained commands still print success. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (needs Docker; never plain `uv run pytest`). Sync the venv like CI does after Task 1: `cd backend && uv sync --extra dev --extra crypto --extra saml` (skill `tiqora-missing-uv-extra-fakes-suite-collapse`). Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their rows (pattern: `_seed_statements` + module-scoped autouse fixture, see `backend/tests/test_ai_refine.py`); never add entries to `backend/tests/db_leak_baseline.txt`.
- DB test seed band for this plan: agent ids `47000–47999`, customer logins `idp.*@example.org`, group/role ids `47000–47999`. Check with `grep -rn "470[0-9][0-9]" backend/tests` before using a sub-range (skill `pytest-session-container-id-band-collisions`). Each module takes its own 100-block (Task 6: 47000–47099, Task 10: 47100–47199, Task 13: 47200–47299, Task 16: 47300–47349, Task 17: 47350–47399, Task 18: 47400–47499, Task 19: 47500–47599, Task 20: 47600–47699, Task 21: 47700–47799, Task 22: 47800–47899, Task 8: 47900–47999).
- Migrations: Alembic two-chain setup; new migrations in `backend/alembic/versions_tiqora` with `down_revision` = the CURRENT primary head at implementation time (check `ls backend/alembic/versions_tiqora | tail -3` and `cd backend && uv run alembic heads` — today it is `20261007_0060`, wave 0 adds one, other wave-1 plans add more; do not trust any id written here). Follow skill `alembic-two-chain-new-head-forks-graph`; update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version == …` assert in `backend/tests/test_migrate_cli.py`. Tables only `tiqora_*`; never change Znuny tables' DDL; writes to Znuny tables must be behaviourally identical to Znuny (source: `znuny-6.5.22/`), incl. cache invalidation via `tiqora.api.v1.admin.common.invalidate_znuny_cache_types`.
- ORM models in `backend/src/tiqora/db/tiqora/models.py` must produce the same DDL as the migration (`backend/tests/test_tiqora_model_ddl.py`).
- MySQL/MariaDB AND PostgreSQL must both work. An existing Znuny MariaDB can mix collations: never JOIN `tiqora_*` tables with Znuny tables on string columns — load keys, then IN-list. Identity subjects are compared by `subject_hash` (sha256 hex), never by a collated string column.
- Timestamps in `tiqora_identity_*` are app-written naive UTC (`datetime.now(UTC).replace(tzinfo=None)`); API responses use `tiqora.domain.schemas.UtcDateTime` (skill `tiqora-naive-utc-datetimes-display-2h-early`).
- All `pysaml2`, `xmlsec1` and `ldap3` calls run through `asyncio.get_running_loop().run_in_executor(None, …)` — never on the event loop. `pysaml2` is imported lazily (like `gssapi` in `domain/spnego.py`) so the app boots without the `saml` extra.
- Never commit key material. Test certificates/keys are generated at test time with `cryptography` into `tmp_path`; the malicious-assertion corpus is built by mutating a freshly signed response in code (skill `hatch-wheel-gitignore-drops-data-files`; gitleaks would flag committed keys).
- Every outbound HTTP call (OIDC discovery/token/userinfo/JWKS, SAML metadata URL) goes through `tiqora.security.outbound.pin_outbound_url` with `follow_redirects=False`; `allow_private_networks` only when the provider's `allow_private_network` flag is on.
- After any API model/route change regenerate the API schema (skill `tiqora-openapi-regen-or-ci-wipes-schema`): `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`; then `head -c 40 packages/api-client/openapi.json` (must start `{"components"`) and `rtk git diff --stat packages/api-client/openapi.json` (hundreds of lines, not five figures). Never hand-edit `schema.d.ts`. A Pydantic response field WITH a default becomes REQUIRED in the TS type — update demo mocks and test fixtures that build such objects (`frontend/src/demo/mockData.ts` `/auth/methods`, `frontend/src/demo/handlers.ts` `/api/portal/auth/methods`, LoginPage/SecurityPage/usePortalEnabled tests).
- Frontend: `cd frontend && pnpm lint && pnpm test --run`; never run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales); `node frontend/scripts/check-i18n-keys.mjs` must pass. New screens get demo-mode mocks (`frontend/src/demo/mockData.ts` `resolveData`; `VITE_DEMO=1`). Colours only via Tailwind tokens (`bg-surface`, `text-muted`, `border-hairline`, `text-danger`, `bg-accent` …). API wrappers are hand-written `frontend/src/lib/*Api.ts` using `api.request<T>(method, path, {query, body, signal})` / `portalApi.request` with `Schemas["…"]` types; use `apiErrorMessage` (`frontend/src/lib/avatarApi.ts`) for messages (skill `apierror-detail-is-body-not-string`).
- UI copy German first, plain words, named by what admins recognise ("Identitätsanbieter", "Anmeldedienst", "Testanmeldung", "Zuordnung", "Rollen aus dem Anmeldedienst"); no system jargon in UI text ("Assertion", "NameID", "Claim" appear only inside the technical attribute table of the Test tab, labelled "Vom Anmeldedienst übermittelte Werte").
- Znuny reference source (6.5 core) is at `znuny-6.5.22/` (`Kernel/System/User.pm` `UserAdd`, `Kernel/System/Auth/Sync/LDAP.pm`, `Kernel/System/Group.pm` `PermissionRoleUserAdd`/`PermissionGroupUserAdd`).
- Production nginx proxies `/api/` with a 90 s timeout; no endpoint here may run longer than ~15 s.
- SAML tests: `pytestmark = [pytest.mark.saml]` plus module-level `pytest.importorskip("saml2")` and `skipif(shutil.which("xmlsec1") is None, reason="xmlsec1 binary missing (brew install libxmlsec1 / apt install xmlsec1)")`; CI sets `TIQORA_REQUIRE_SAML_TESTS=1`, under which the skip becomes a hard failure (helper in `backend/tests/_saml_idp.py`). Local macOS: `brew install libxmlsec1`.

## Review Focus

1. **Production env OIDC after the upgrade** — same callback URL (`/api/v1/auth/oidc/callback`), same claim (`TIQORA_OIDC_CLAIM`) matched against `users.login`, same per-agent "SSO freigeschaltet" gate, no 2FA prompt, avatar picture kept; a login started before the deploy (old Redis state value `"1"` or a bare code verifier) still completes. Pinned in Task 8 (`test_env_oidc_unchanged_*`, `test_callback_accepts_legacy_state_value`).
2. **A link exists but the linked account is invalid, renamed or points elsewhere than the claim** → the link wins: renamed login still logs in; an invalid account is rejected and the login NEVER falls back to claim/email matching onto a different account. Pinned in Task 6 (`test_link_wins_over_claim`, `test_link_to_invalid_user_rejects_without_fallback`).
3. **Forged or replayed SAML responses** — unsigned assertion, tampered value, signature-wrapping, DOCTYPE, wrong audience, expired, unknown `InResponseTo`, the same response posted twice, a response for the customer ACS posted to the agent ACS, a signature by a key that is not in the current metadata → all rejected with `/login?sso_error=saml_invalid` and no session. Pinned in Task 9 (corpus) and Task 10 (endpoint).
4. **Role sync must never clobber manual work** — in mode "managed" only assignments created by a rule are removed; a role an admin assigned by hand stays even when the rule stops granting it; an assignment an admin removes by hand loses its "managed" mark, and if a rule still grants it the next login adds it again (Znuny parity). Pinned in Task 5 (pure diff) and Task 18 (DB).
5. **Agent and customer second factors on the same browser/authenticator and the same numeric id** — customer passkeys use a distinct WebAuthn user handle, so registering a customer passkey never overwrites an agent's discoverable credential; TOTP replay/pending Redis keys of customer 5 never collide with agent 5. Pinned in Task 22 (`test_customer_user_handle_differs_from_agent`, `test_totp_redis_keys_are_namespaced`).

---

## Design (fixed)

Names below are used verbatim by every task. Do not rename without updating this section in the same commit.

### D0. File map

Backend (new):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/identity/__init__.py` | package docstring only |
| `backend/src/tiqora/identity/types.py` | literals, slugs, constants |
| `backend/src/tiqora/identity/config_models.py` | Pydantic config per kind + attribute map / JIT / rules models |
| `backend/src/tiqora/identity/presets.py` | admin presets (Entra ID, Google Workspace, …) |
| `backend/src/tiqora/identity/store.py` | provider rows ⇄ `ProviderRecord` (secrets encrypted), CRUD |
| `backend/src/tiqora/identity/env_providers.py` | env OIDC/LDAP → read-only rows, lazy ensure |
| `backend/src/tiqora/identity/mapping.py` | pure: attributes → profile, rules → desired assignments, diff |
| `backend/src/tiqora/identity/links.py` | `tiqora_identity_link` reads/writes |
| `backend/src/tiqora/identity/login.py` | `plan_login` / `apply_login` (match, link, JIT, profile, roles) |
| `backend/src/tiqora/identity/provisioning.py` | applying JIT/profile/assignment plans through `domain/user_provisioning.py` |
| `backend/src/tiqora/identity/sso_sessions.py` | `SsoSessionInfo`, Redis index, revocation |
| `backend/src/tiqora/identity/flow_state.py` | Redis state for OIDC/SAML/test logins/logout requests/replay caches |
| `backend/src/tiqora/identity/http.py` | `safe_next`, error redirects, HTML landing page, cookie helpers |
| `backend/src/tiqora/identity/urls.py` | absolute SP/RP URLs from `public_base_url` |
| `backend/src/tiqora/identity/saml_runtime.py` | `saml_availability()` (package + `xmlsec1`) |
| `backend/src/tiqora/identity/saml_keys.py` | SP key/cert generation, temp key files |
| `backend/src/tiqora/identity/saml_metadata.py` | parse/validate IdP metadata, fetch via outbound guard |
| `backend/src/tiqora/identity/saml_sp.py` | pysaml2 SP wrapper (login, ACS, metadata, logout) |
| `backend/src/tiqora/identity/oidc_backchannel.py` | logout-token verification (JWKS) |
| `backend/src/tiqora/identity/ldap_login.py` | LDAP provider chain + AuthSync attribute/group collection |
| `backend/src/tiqora/identity/metadata_refresh.py` | daily metadata refresh tick |
| `backend/src/tiqora/identity/sso_routes.py` | audience-neutral route bodies used by agent and portal routers |
| `backend/src/tiqora/domain/user_provisioning.py` | Znuny-identical writes: agent/customer create, profile update, role_user/group_user |
| `backend/src/tiqora/domain/two_factor_subject.py` | `TwoFactorSubject`, `AGENT_2FA`, `CUSTOMER_2FA` |
| `backend/src/tiqora/api/v1/auth_sso.py` | `/api/v1/auth/sso/*`, `/auth/oidc/{slug}/*`, `/auth/saml/{slug}/*` |
| `backend/src/tiqora/api/v1/admin/identity_providers.py` | `/api/v1/admin/identity-providers` |
| `backend/src/tiqora/api/v1/admin/identity_schemas.py` | admin request/response models |
| `backend/src/tiqora/api/portal/auth_sso.py` | `/api/portal/auth/methods`, `/sso/*`, `/oidc/{slug}/callback`, `/saml/{slug}/acs` |
| `backend/src/tiqora/api/portal/security.py` | customer TOTP + passkeys |
| `backend/tests/_identity_helpers.py` | `FakeRedis`, seed helpers, `make_identity_app` |
| `backend/tests/_saml_idp.py` | in-process pysaml2 test IdP + skip/require helper |

Backend (modified): `pyproject.toml` (extra `saml`, mypy overrides, marker `saml`, marker `keycloak`), `Dockerfile`, `.github/workflows/ci.yml`, `.github/workflows/nightly-random-order.yml`, `db/tiqora/models.py`, `domain/oidc.py`, `domain/auth.py` (`SessionStore`, `AuthService.create_session`), `domain/customer_auth.py` (`CustomerSessionStore`), `domain/_ldap_core.py` (entry + group lookup), `domain/totp.py`, `domain/passkey.py`, `domain/schemas.py` (`AuthMethodsOut`, `UserMe`, `CustomerMe`, `CustomerLoginResponse`), `api/v1/auth.py` (login LDAP chain, `/methods`, OIDC alias routes delegate, `/me`), `api/portal/auth.py` (login LDAP chain + 2FA pending), `api/v1/__init__.py`, `api/v1/admin/__init__.py`, `api/portal/__init__.py`, `api/app.py` (lifespan env-provider sync), `security/csrf.py`, `api/v1/admin/users.py` + `api/v1/admin/customers.py` (use `user_provisioning`), `worker/__main__.py`, `worker/services.py`, `domain/settings_store.py` (one key).

Frontend (new): `frontend/src/lib/identityApi.ts`, `frontend/src/lib/ssoLogout.ts`, `frontend/src/lib/ssoErrors.ts`, `frontend/src/components/auth/SsoProviderButtons.tsx`, `frontend/src/components/auth/ProviderIcon.tsx`, `frontend/src/routes/admin/IdentityProvidersPage.tsx`, `frontend/src/routes/admin/IdentityProviderPage.tsx`, `frontend/src/components/admin/identity/{PresetDialog,ConnectionTab,EndpointsBox,TestTab,LoginPlanView,MappingTab,RolesTab,LinksTab}.tsx`, `frontend/src/components/security/{TwoFactorPanel.tsx,twoFactorApi.ts}`, `frontend/src/routes/portal/SecurityPage.tsx`, `frontend/src/components/portal/PortalTwoFactorStep.tsx` (+ tests next to each).

Frontend (modified): `routes/LoginPage.tsx`, `routes/portal/PortalLoginPage.tsx`, `routes/agent/SecurityPage.tsx` (uses `TwoFactorPanel`), `routes/admin/AuthConfigPage.tsx` (link), `components/agent/AccountMenu.tsx` + `auth/AuthContext.tsx` (SSO logout), `auth/CustomerAuthContext.tsx` (pending 2FA, SSO logout), `router.tsx`, `lib/adminSearch.ts`, `demo/mockData.ts`, `demo/handlers.ts`, `i18n/locales/*.json`.

### D1. Types and constants — `tiqora.identity.types`

```python
ProviderKind = Literal["oidc", "saml", "ldap"]
Audience = Literal["agent", "customer", "both"]
UserKind = Literal["agent", "customer"]
ProviderSource = Literal["db", "env"]
SyncMode = Literal["managed", "authoritative"]
MatchBy = Literal["login", "email"]
GroupPermission = Literal["ro", "move_into", "create", "note", "owner", "priority", "rw"]

SSO_KINDS: Final = frozenset({"oidc", "saml"})
SLUG_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
RESERVED_SLUGS: Final = frozenset({"env-oidc", "env-ldap", "env-customer-ldap", "logout", "login"})
ENV_OIDC_SLUG: Final = "env-oidc"
ENV_LDAP_SLUG: Final = "env-ldap"
ENV_CUSTOMER_LDAP_SLUG: Final = "env-customer-ldap"
SYSTEM_USER_ID: Final = 1          # change_by for sync writes (Znuny AuthSync uses ChangeUserID => 1);
                                   # role/group sync never touches this user's assignments

def audience_includes(audience: Audience, user_kind: UserKind) -> bool   # "both" includes both
def subject_hash(subject: str) -> str                                     # sha256(subject.encode()).hexdigest()
def utc_now() -> datetime                                                 # naive UTC
```

### D2. Tables (one migration, Task 2; customer 2FA tables in a second migration, Task 22)

```
tiqora_identity_provider
  id                   INT PK autoincrement
  slug                 VARCHAR(64)  NOT NULL UNIQUE (uq_tiqora_identity_provider_slug)
  kind                 VARCHAR(16)  NOT NULL            -- oidc | saml | ldap
  source               VARCHAR(16)  NOT NULL            -- db | env
  name                 VARCHAR(200) NOT NULL
  audience             VARCHAR(16)  NOT NULL            -- agent | customer | both
  enabled              BOOL NOT NULL                    -- app writes; no server default
  sort                 INT  NOT NULL
  button_label         VARCHAR(200) NULL                -- NULL → name
  button_icon          VARCHAR(32)  NULL                -- microsoft|google|key|shield|building|directory
  preset               VARCHAR(40)  NULL
  config               TEXT NOT NULL                    -- JSON, non-secret fields of the kind's config model
  secrets_enc          TEXT NULL                        -- encrypt_secret(settings.secret_key, json.dumps({field: value}))
  match_by             VARCHAR(16)  NOT NULL            -- login | email
  require_sso_opt_in   BOOL NOT NULL                    -- agents: per-agent "SSO freigeschaltet" required (oidc/saml only)
  attribute_map        TEXT NOT NULL                    -- JSON AttributeMap
  jit_enabled          BOOL NOT NULL
  jit_defaults         TEXT NOT NULL                    -- JSON JitDefaults
  role_rules           TEXT NOT NULL                    -- JSON list[RoleRule]
  sync_mode            VARCHAR(16)  NOT NULL            -- managed | authoritative
  metadata_xml         TEXT NULL (MEDIUMTEXT on mysql/mariadb)   -- last fetched SAML IdP metadata
  metadata_fetched_at  DATETIME NULL
  metadata_error       VARCHAR(500) NULL
  create_by INT NOT NULL, create_time DATETIME NOT NULL, change_by INT NOT NULL, change_time DATETIME NOT NULL
  INDEX ix_tiqora_identity_provider_enabled (enabled, sort)

tiqora_identity_link
  id            BIGINT PK autoincrement
  provider_id   INT NOT NULL FK → tiqora_identity_provider.id ON DELETE CASCADE
  user_kind     VARCHAR(16)  NOT NULL                   -- agent | customer
  subject_hash  CHAR(64)     NOT NULL                   -- sha256 hex of the exact subject
  subject       VARCHAR(512) NOT NULL                   -- display only (truncated to 512)
  user_id       INT NOT NULL                            -- users.id or customer_user.id (soft reference)
  user_login    VARCHAR(200) NOT NULL                   -- snapshot for the admin list
  first_seen    DATETIME NOT NULL
  last_seen     DATETIME NOT NULL
  UNIQUE uq_tiqora_identity_link_subject (provider_id, user_kind, subject_hash)
  INDEX  ix_tiqora_identity_link_user (user_kind, user_id)

tiqora_identity_managed_assignment
  id              BIGINT PK autoincrement
  provider_id     INT NOT NULL FK → tiqora_identity_provider.id ON DELETE CASCADE
  user_id         INT NOT NULL                          -- users.id
  kind            VARCHAR(8)  NOT NULL                  -- role | group
  target_id       INT NOT NULL                          -- roles.id | groups.id
  permission_key  VARCHAR(20) NOT NULL                  -- '' for roles; group_user.permission_key for groups
  created         DATETIME NOT NULL
  UNIQUE uq_tiqora_identity_managed (provider_id, user_id, kind, target_id, permission_key)
  INDEX  ix_tiqora_identity_managed_user (user_id)

-- Task 22 (second migration)
tiqora_customer_totp
  customer_user_id INT PK (autoincrement=False)  -- customer_user.id
  secret VARCHAR(255) NOT NULL, enabled BOOL NOT NULL, created DATETIME NOT NULL server_default now()
tiqora_customer_passkey
  id BIGINT PK autoincrement, customer_user_id INT NOT NULL (index ix_tiqora_customer_passkey_user),
  credential_id VARCHAR(255) NOT NULL UNIQUE, public_key BLOB/BYTEA NOT NULL, sign_count INT NOT NULL,
  transports TEXT NULL, aaguid VARCHAR(64) NULL, name VARCHAR(120) NOT NULL,
  created DATETIME NOT NULL server_default now(), last_used_at DATETIME NULL
```

ORM classes in `db/tiqora/models.py`: `TiqoraIdentityProvider`, `TiqoraIdentityLink`, `TiqoraIdentityManagedAssignment`, `TiqoraCustomerTotp`, `TiqoraCustomerPasskey`. Constant `IDENTITY_METADATA_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")`. The customer 2FA ORM classes map the DB column `customer_user_id` to the Python attribute `user_id` (`user_id: Mapped[int] = mapped_column("customer_user_id", Integer, …)`) so `TOTPService`/`WebAuthnService` work on both models unchanged (D13).

### D3. Config models — `tiqora.identity.config_models`

Every kind model satisfies wave-0 `validate_section_schema` rules (flat, every field has a default, supported types only) so the admin "Verbindung" tab is a generated `SchemaForm`. Secrets are `SecretStr | None` (→ `format: "password"`).

```python
class OidcProviderConfig(BaseModel):
    issuer: str = ""                                   # https URL; "/.well-known/openid-configuration" is appended
    client_id: str = ""
    client_secret: SecretStr | None = None
    scopes: str = "openid profile email"
    redirect_uri: str = ""                             # empty → computed (D9); env provider: TIQORA_OIDC_REDIRECT_URI
    pkce: Literal["auto", "always", "never"] = "auto"  # auto = only when discovery advertises S256 (today's behaviour)
    email_must_be_verified: bool = True                # match_by=email / JIT email requires email_verified=true (OIDC)
    allow_private_network: bool = False                # self-hosted Keycloak in RFC1918
    backchannel_logout: bool = True

NameIdFormat = Literal["unspecified", "emailAddress", "persistent", "transient"]

class SamlProviderConfig(BaseModel):
    idp_metadata_url: str = ""
    idp_metadata_xml: str = Field("", json_schema_extra={"x-widget": "textarea"})   # upload/paste wins over URL
    idp_entity_id: str = ""                            # needed when the metadata holds several IdPs
    name_id_format: NameIdFormat = "unspecified"
    sign_requests: bool = True
    want_response_signed: bool = False                 # assertions are ALWAYS required to be signed
    allow_idp_initiated: bool = False
    clock_skew_seconds: int = Field(120, ge=0, le=600)
    allow_private_network: bool = False
    sp_certificate: str = Field("", json_schema_extra={"x-widget": "textarea"})       # generated, read-only in UI
    sp_private_key: SecretStr | None = None                                          # generated

class LdapProviderConfig(BaseModel):
    host: str = ""
    port: int = Field(389, ge=1, le=65535)
    use_ssl: bool = False
    use_starttls: bool = False
    base_dn: str = ""
    bind_dn: str = ""
    bind_password: SecretStr | None = None
    uid_attr: str = "uid"
    always_filter: str = ""
    group_dn: str = ""
    access_attr: str = "memberUid"
    user_attr: Literal["DN", "UID"] = "DN"
    subject_attr: str = "entryUUID"                    # AD: objectGUID; empty/missing value → entry DN

CONFIG_MODELS: Final[dict[ProviderKind, type[BaseModel]]] = {"oidc": OidcProviderConfig, "saml": SamlProviderConfig, "ldap": LdapProviderConfig}
READ_ONLY_CONFIG_FIELDS: Final[dict[ProviderKind, frozenset[str]]] = {"saml": frozenset({"sp_certificate", "sp_private_key"}), "oidc": frozenset(), "ldap": frozenset()}
HIDDEN_CONFIG_FIELDS: Final[dict[ProviderKind, frozenset[str]]] = {"saml": frozenset({"sp_private_key"}), "oidc": frozenset(), "ldap": frozenset()}
# read-only fields are ignored by merge_config; hidden fields are never rendered in the admin form

class AttributeMap(BaseModel):          # each: ordered source attribute names, first non-empty wins;
    login: list[str] = Field(default_factory=list)       # "_text" = fixed value "text" (Znuny UserSyncMap convention)
    email: list[str] = Field(default_factory=list)
    first_name: list[str] = Field(default_factory=list)
    last_name: list[str] = Field(default_factory=list)
    customer_id: list[str] = Field(default_factory=list)
    mobile: list[str] = Field(default_factory=list)

class GroupGrant(BaseModel):
    group_id: int
    permissions: list[GroupPermission] = Field(default_factory=lambda: ["rw"])

class RoleRule(BaseModel):
    attribute: str = Field(min_length=1, max_length=200)    # e.g. "groups", "memberOf", "ldap:group_dn"
    value: str = Field(min_length=1, max_length=500)        # compared case-insensitively after strip
    role_ids: list[int] = Field(default_factory=list)
    groups: list[GroupGrant] = Field(default_factory=list)

class JitDefaults(BaseModel):
    initial_role_ids: list[int] = Field(default_factory=list)      # agents, first login only
    initial_groups: list[GroupGrant] = Field(default_factory=list) # agents, first login only (Znuny UserSyncInitialGroups)
    customer_id: str = ""                                          # customers: fixed CustomerID when not mapped
    update_profile_on_login: bool = True                           # Znuny "update user attributes (only if changed)"
```

Pseudo attributes injected by the adapters (usable in `AttributeMap` and `RoleRule.attribute`): `saml:name_id` (SAML NameID value), `oidc:sub`, `ldap:dn`, `ldap:group_dn` (every rule-referenced group DN the LDAP user is a member of, see D14).

Default attribute maps (used by presets and env rows):

| kind | login | email | first_name | last_name |
|---|---|---|---|---|
| oidc | `preferred_username` | `email` | `given_name` | `family_name` |
| saml | `saml:name_id` | `mail`, `email`, `urn:oid:0.9.2342.19200300.100.1.3`, `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress` | `givenName`, `urn:oid:2.5.4.42`, `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname` | `sn`, `urn:oid:2.5.4.4`, `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname` |
| ldap | `<uid_attr>` (`uid`) | `mail` | `givenName` | `sn` |
| env-oidc | `[settings.oidc_claim]` | — | — | — |

### D4. Provider store — `tiqora.identity.store`

```python
@dataclass(frozen=True, slots=True)
class ProviderRecord:
    id: int
    slug: str
    kind: ProviderKind
    source: ProviderSource
    name: str
    audience: Audience
    enabled: bool
    sort: int
    button_label: str | None
    button_icon: str | None
    preset: str | None
    config: BaseModel                  # instance of CONFIG_MODELS[kind], secrets decrypted
    secrets_set: frozenset[str]
    match_by: MatchBy
    require_sso_opt_in: bool
    attribute_map: AttributeMap
    jit_enabled: bool
    jit_defaults: JitDefaults
    role_rules: tuple[RoleRule, ...]
    sync_mode: SyncMode
    metadata_xml: str | None
    metadata_fetched_at: datetime | None
    metadata_error: str | None
    @property
    def read_only(self) -> bool: ...   # source == "env"
    @property
    def label(self) -> str: ...        # button_label or name

class ProviderValidationError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None: ...   # field -> German message
    errors: dict[str, str]
class ProviderReadOnly(Exception): ...                         # env rows

def provider_from_row(row: TiqoraIdentityProvider, *, secret_key: str) -> ProviderRecord
    # undecryptable secrets -> treated as unset + structlog warning "identity_secret_undecryptable"
async def get_provider(session, provider_id: int, *, secret_key: str) -> ProviderRecord | None
async def get_provider_by_slug(session, slug: str, *, secret_key: str) -> ProviderRecord | None
async def list_providers(session, *, secret_key: str) -> list[ProviderRecord]          # sort, id
async def list_enabled(session, *, secret_key: str, kind: ProviderKind | None = None,
                       user_kind: UserKind | None = None) -> list[ProviderRecord]       # enabled + audience_includes
async def create_provider(session, *, secret_key: str, actor_id: int, preset: str, name: str,
                          slug: str, audience: Audience) -> ProviderRecord
    # validates slug (SLUG_RE, not RESERVED_SLUGS, unique), applies preset defaults; SAML: generates SP key/cert
    # (saml_keys.generate_sp_keypair) into config; enabled=False; sort=100; flush only
async def update_provider(session, provider_id: int, changes: ProviderChanges, *, secret_key: str,
                          actor_id: int) -> ProviderRecord
    # ProviderReadOnly for env rows; config change merging: secret absent or == SECRET_MASK -> unchanged,
    # "" or None -> removed, other -> stored; READ_ONLY_CONFIG_FIELDS ignored; validates merged config with
    # CONFIG_MODELS[kind] (pydantic errors -> ProviderValidationError); role_rules/jit_defaults role/group ids must
    # exist in roles / groups table (ProviderValidationError({"role_rules": "Unbekannte Rolle: 7"})); flush only
async def delete_provider(session, provider_id: int) -> None     # ProviderReadOnly for env rows; links cascade
def merge_config(kind: ProviderKind, current: BaseModel, changes: Mapping[str, Any]) -> BaseModel   # pure, used by update + test drafts

class ProviderChanges(BaseModel):            # admin PATCH body (also the "draft" of a test login)
    name: str | None = None
    slug: str | None = None
    audience: Audience | None = None
    enabled: bool | None = None
    sort: int | None = None
    button_label: str | None = None
    button_icon: str | None = None
    config: dict[str, Any] | None = None
    match_by: MatchBy | None = None
    require_sso_opt_in: bool | None = None
    attribute_map: AttributeMap | None = None
    jit_enabled: bool | None = None
    jit_defaults: JitDefaults | None = None
    role_rules: list[RoleRule] | None = None
    sync_mode: SyncMode | None = None
```

`SECRET_MASK` and `validate_section_schema` are imported from `tiqora.settings_sections.section` (wave 0). A module-level test calls `validate_section_schema` on a throwaway `SettingsSection` for each config model so they stay `SchemaForm`-compatible.

### D5. Env providers — `tiqora.identity.env_providers`

```python
async def sync_env_providers(session, settings) -> list[str]       # returns slugs touched; commits
    # TIQORA_OIDC_ENABLED      -> row env-oidc (kind oidc, audience agent, enabled true, sort 0, name "SSO",
    #                              button_label NULL (frontend shows "Mit SSO anmelden"), match_by login,
    #                              require_sso_opt_in true, attribute_map {login: [settings.oidc_claim]},
    #                              jit false, rules [], config {} (non-secret fields NOT stored))
    # TIQORA_LDAP_ENABLED      -> row env-ldap (kind ldap, audience agent, sort 1000)
    # TIQORA_CUSTOMER_LDAP_ENABLED -> row env-customer-ldap (kind ldap, audience customer, sort 1000)
    # env flag off but row exists -> enabled=false (row kept: links reference it)
    # IntegrityError on insert (another replica won) -> rollback and re-read; never raises on races
async def ensure_env_providers(session, settings) -> None
    # process-wide once: first successful call caches a flag; called by lifespan (wrapped in try/except,
    # warning "identity_env_sync_skipped" when the DB is unreachable) AND at the top of every login/discovery
    # path, so a failed startup sync can never disable production SSO
def env_runtime_config(record: ProviderRecord, settings) -> BaseModel
    # source=="env": ALWAYS rebuilds config from Settings (oidc_* / ldap_* / customer_ldap_*), ignoring row JSON
def runtime_config(record: ProviderRecord, settings) -> BaseModel   # env -> env_runtime_config; db -> record.config
```

### D6. Mapping engine (pure) — `tiqora.identity.mapping`

```python
Attributes = Mapping[str, tuple[str, ...]]

def normalize_attributes(raw: Mapping[str, Any]) -> dict[str, tuple[str, ...]]
    # str -> (s.strip(),) if non-empty; list/tuple -> stripped non-empty str() of scalars; bool/int/float -> (str(v),);
    # dict/None -> dropped; values longer than 2000 chars truncated
def lookup(attributes: Attributes, name: str) -> tuple[str, ...]      # exact key, else unique case-insensitive key

@dataclass(frozen=True, slots=True)
class MappedProfile:
    login: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    customer_id: str | None = None
    mobile: str | None = None

def map_profile(attributes: Attributes, attribute_map: AttributeMap) -> MappedProfile

def normalize_dn(value: str) -> str                                   # lower, strip spaces around "," and "="
def rule_matches(attributes: Attributes, rule: RoleRule) -> bool       # "ldap:group_dn" compares normalize_dn()

@dataclass(frozen=True, slots=True)
class DesiredAssignments:
    role_ids: frozenset[int]
    group_perms: frozenset[tuple[int, str]]
    has_role_rules: bool
    has_group_rules: bool

def evaluate_rules(attributes: Attributes, rules: Sequence[RoleRule], *, valid_role_ids: AbstractSet[int],
                   valid_group_ids: AbstractSet[int]) -> DesiredAssignments
    # union over matching rules; ids not in valid_* are dropped (Znuny: "only for valid groups");
    # a GroupGrant containing "rw" contributes only (gid, "rw") (Znuny: "if rw permission exists, discard all other")

@dataclass(frozen=True, slots=True)
class AssignmentState:
    current_roles: frozenset[int]
    current_group_perms: frozenset[tuple[int, str]]
    managed_roles: frozenset[int]
    managed_group_perms: frozenset[tuple[int, str]]

@dataclass(frozen=True, slots=True)
class AssignmentDiff:
    add_roles: frozenset[int] = frozenset()
    remove_roles: frozenset[int] = frozenset()
    add_groups: frozenset[tuple[int, str]] = frozenset()
    remove_groups: frozenset[tuple[int, str]] = frozenset()
    record_managed_roles: frozenset[int] = frozenset()
    drop_managed_roles: frozenset[int] = frozenset()
    record_managed_groups: frozenset[tuple[int, str]] = frozenset()
    drop_managed_groups: frozenset[tuple[int, str]] = frozenset()
    @property
    def empty(self) -> bool: ...

def diff_assignments(desired: DesiredAssignments, state: AssignmentState, *, mode: SyncMode) -> AssignmentDiff
```

Diff semantics (per kind; a kind without rules — `has_role_rules`/`has_group_rules` false — produces no changes for that kind in either mode):

| mode | add | remove | record managed | drop managed |
|---|---|---|---|---|
| managed | desired − current | (managed − desired) ∩ current | add | managed − desired |
| authoritative (Znuny AuthSync) | desired − current | current − desired | desired − managed | managed − desired |

### D7. Identity links and login orchestration — `tiqora.identity.links`, `tiqora.identity.login`

```python
# links.py
@dataclass(frozen=True, slots=True)
class IdentityLink:
    id: int; provider_id: int; user_kind: UserKind; subject: str; user_id: int; user_login: str
    first_seen: datetime; last_seen: datetime
async def find_link(session, *, provider_id: int, user_kind: UserKind, subject: str) -> IdentityLink | None
async def upsert_link(session, *, provider_id: int, user_kind: UserKind, subject: str, user_id: int,
                      user_login: str) -> IdentityLink      # insert or update user_login/last_seen; flush only
async def list_links(session, *, provider_id: int, limit: int = 200) -> list[IdentityLink]   # last_seen desc
async def delete_link(session, *, provider_id: int, link_id: int) -> bool

# login.py
@dataclass(frozen=True, slots=True)
class SsoSessionDraft:                  # what the adapter learned for logout
    session_index: str | None = None    # SAML
    name_id: str | None = None
    name_id_format: str | None = None
    name_qualifier: str | None = None
    sp_name_qualifier: str | None = None
    sid: str | None = None              # OIDC
    id_token_hint: str | None = None    # OIDC, for end_session

@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    provider_id: int
    subject: str                        # OIDC sub, SAML NameID (persistent/unspecified/email), LDAP subject_attr or DN
    attributes: Mapping[str, tuple[str, ...]]
    email_verified: bool | None = None  # OIDC only; None = not asserted
    sso: SsoSessionDraft | None = None

RejectCode = Literal["no_subject", "no_match", "user_invalid", "sso_not_enabled", "email_unverified",
                     "ambiguous_email", "login_taken", "jit_missing_fields", "provider_disabled"]
REJECT_MESSAGES: Final[dict[str, str]]   # German, shown in the Test tab; login pages use i18n auth.ssoErrors.<code>

@dataclass(frozen=True, slots=True)
class LoginPlan:
    user_kind: UserKind
    decision: Literal["login", "create", "reject"]
    matched_by: Literal["link", "login", "email", "jit"] | None
    user_id: int | None
    login: str | None
    reject_code: RejectCode | None
    profile: MappedProfile
    profile_changes: Mapping[str, tuple[str | None, str]]     # field -> (old, new); agents/customers
    assignments: AssignmentDiff                                # agents only; empty otherwise
    link_exists: bool

async def plan_login(session, provider: ProviderRecord, identity: ExternalIdentity, *,
                     user_kind: UserKind, settings: Settings) -> LoginPlan          # READS ONLY (Test tab, preview)
async def apply_login(session, provider: ProviderRecord, identity: ExternalIdentity, plan: LoginPlan, *,
                      settings: Settings) -> tuple[int, str]                      # (user_id, login); commits
def plan_to_dict(plan: LoginPlan) -> dict[str, Any]                                # JSON-able, ids only (Test tab storage)
```

Matching order in `plan_login` (agents: `users`, `valid_id = 1`; customers: `customer_user`, `valid_id = 1`):

1. Empty `subject` → reject `no_subject`.
2. Link for `(provider, user_kind, subject_hash)` exists → load that user by id. Missing or `valid_id != 1` → reject `user_invalid` (**no fallback**). Else `matched_by="link"`.
3. No link, `match_by="login"`: `profile.login` empty → `no_match`; exact `users.login == v`; if none, case-insensitive (`func.lower`) and exactly one valid hit. (Exact-first keeps env-OIDC behaviour identical on PostgreSQL.)
4. No link, `match_by="email"`: OIDC with `email_must_be_verified` and `email_verified is not True` → `email_unverified`. Agents: `user_preferences` key `UserEmail`, `preferences_value IN (:exact_bytes, :lower_bytes)`, decoded + lowercased in Python, valid users only; >1 distinct user → `ambiguous_email`. Customers: `func.lower(customer_user.email) == email.lower()`, >1 → `ambiguous_email`.
5. No match: `jit_enabled` false → `no_match`. JIT (phase 2, Task 17): required `login`, `email`, `first_name`, `last_name` (customers also `customer_id` from map or `jit_defaults.customer_id`, else the email) → missing → `jit_missing_fields`; a valid OR invalid user with that login (case-insensitive) exists → `login_taken`; else `decision="create"`, `matched_by="jit"`.
6. Agents + `kind in SSO_KINDS` + `require_sso_opt_in` + matched user without `tiqora_user_auth_config.sso_eligible` → `sso_not_enabled` (JIT-created agents get `sso_eligible=True`, Task 17).
7. `profile_changes` (phase 2): only when `jit_defaults.update_profile_on_login` and the field is mapped and differs (Znuny: treat None and "" as equal).
8. `assignments` (phase 2, agents): `diff_assignments(evaluate_rules(...), state, mode=provider.sync_mode)`; user id `SYSTEM_USER_ID` → always empty.

`apply_login` writes (one transaction, then commit): JIT create (Task 17) → profile update (Task 17) → assignments (Task 18) → `upsert_link` → returns `(user_id, login)`. A `reject` plan raises `ValueError`.

### D8. SSO session index — `tiqora.identity.sso_sessions`

```python
@dataclass(frozen=True, slots=True)
class SsoSessionInfo:
    provider_id: int
    slug: str
    kind: ProviderKind
    subject_hash: str
    session_index: str | None = None
    name_id: str | None = None
    name_id_format: str | None = None
    name_qualifier: str | None = None
    sp_name_qualifier: str | None = None
    sid: str | None = None
    id_token_hint: str | None = None
    def to_json(self) -> str: ...
    @classmethod
    def from_json(cls, raw: str) -> "SsoSessionInfo | None": ...   # None on garbage

AudienceKey = Literal["a", "c"]           # agent / customer key space
def sso_key(audience: AudienceKey, token: str) -> str
    # "tiqora:session:sso:{token}" (agent) | "tiqora:csession:sso:{token}" (customer)
async def store_sso(redis, audience: AudienceKey, token: str, info: SsoSessionInfo, ttl: int) -> None
    # value + index sets: tiqora:sso:idx:{a|c}:{provider_id}:sub:{subject_hash},
    #   ...:sid:{sha256(sid)} when sid, ...:si:{sha256(session_index)} when session_index; all expire at ttl
async def load_sso(redis, audience: AudienceKey, token: str) -> SsoSessionInfo | None
async def drop_sso(redis, audience: AudienceKey, token: str) -> None
async def tokens_for(redis, audience: AudienceKey, provider_id: int, *, subject_hash: str | None = None,
                     sid: str | None = None, session_indexes: Sequence[str] = ()) -> set[str]
    # union of the matching index sets; when both subject_hash and session_indexes are given, only tokens in BOTH
```

`SessionStore.create(user_id, login, *, avatar_url=None, sso: SsoSessionInfo | None = None)` stores it with the absolute TTL; `SessionStore.delete` also calls `drop_sso`; new `SessionStore.get_sso(token)`. `AuthService.create_session(user, *, avatar_url=None, sso=None)`. `CustomerSessionStore` gains the same `sso` parameter + `get_sso`, plus a per-customer index `tiqora:csession:user:{id}` and `revoke_customer_sessions(customer_id, *, keep_token=None) -> int` (mirrors `SessionStore.revoke_user_sessions`). Revocation helper:

```python
async def revoke_sso_sessions(redis, settings, *, provider_id: int, subject_hash: str | None = None,
                              sid: str | None = None, session_indexes: Sequence[str] = ()) -> int
    # both audiences; agent tokens via SessionStore.delete, customer tokens via CustomerSessionStore.delete
```

### D9. URLs — `tiqora.identity.urls`

`TIQORA_PUBLIC_BASE_URL` (existing `settings.public_base_url`) is required for SAML and for DB-configured OIDC. Empty → `base_url(settings)` falls back to the first CORS origin (like the OAuth2 mail redirect); if that is empty too, `IdentityUrlsUnavailable` is raised, the login start answers `/login?sso_error=config`, and the admin endpoints list shows `base_url_missing: true` ("Bitte TIQORA_PUBLIC_BASE_URL setzen").

```python
@dataclass(frozen=True, slots=True)
class ProviderUrls:
    agent_redirect_uri: str        # {base}/api/v1/auth/oidc/{slug}/callback   (env-oidc: settings.oidc_redirect_uri)
    customer_redirect_uri: str     # {base}/api/portal/auth/oidc/{slug}/callback
    agent_acs_url: str             # {base}/api/v1/auth/saml/{slug}/acs
    customer_acs_url: str          # {base}/api/portal/auth/saml/{slug}/acs
    sp_entity_id: str              # {base}/api/v1/auth/saml/{slug}/metadata
    sp_metadata_url: str           # same as sp_entity_id
    slo_url: str                   # {base}/api/v1/auth/saml/{slug}/sls   (one per SP entity, both audiences)
    backchannel_logout_url: str    # {base}/api/v1/auth/oidc/{slug}/backchannel-logout (both audiences)
    agent_post_logout_url: str     # {base}/login?logged_out=1
    customer_post_logout_url: str  # {base}/portal/login?logged_out=1
def base_url(settings) -> str
def provider_urls(settings, record: ProviderRecord) -> ProviderUrls
```

### D10. Flow state — `tiqora.identity.flow_state`

All values JSON; all reads of one-shot keys use GET + DELETE (pop).

| Key | Value | TTL |
|---|---|---|
| `tiqora:oidc:state:{state}` (existing prefix) | `{"v":1,"p":provider_id,"k":verifier\|null,"n":next,"a":"agent"\|"customer","t":test_id\|null,"nonce":str}` — a non-JSON value (`"1"` or a bare verifier) is a pre-upgrade env-oidc state | 300 |
| `tiqora:saml:req:{request_id}` | `{"p","n","a","t"}` | 600 |
| `tiqora:saml:seen:{provider_id}:{sha256(assertion_id)}` | `"1"`, `SET NX` | until NotOnOrAfter + skew, min 60 |
| `tiqora:saml:logout:{request_id}` | `{"p","a"}` | 600 |
| `tiqora:idp:test:{test_id}` | `{"provider_id","admin_id","audience","draft":ProviderChanges\|null,"status":"started"\|"done"\|"failed","result":…}` | 900 |
| `tiqora:oidc:jti:{provider_id}:{sha256(jti)}` | `"1"`, `SET NX` | 600 |

```python
@dataclass(frozen=True, slots=True)
class LoginState:
    provider_id: int
    next_path: str
    user_kind: UserKind
    test_id: str | None = None
    code_verifier: str | None = None
    nonce: str | None = None
    legacy: bool = False              # pre-upgrade env-oidc value
async def put_oidc_state(redis, state: str, st: LoginState) -> None
async def pop_oidc_state(redis, state: str, *, env_oidc_provider_id: int | None) -> LoginState | None
async def put_saml_request(redis, request_id: str, st: LoginState) -> None
async def pop_saml_request(redis, request_id: str) -> LoginState | None
async def mark_assertion_seen(redis, provider_id: int, assertion_id: str, ttl: int) -> bool   # False = replay
async def put_logout_request(redis, request_id: str, provider_id: int, user_kind: UserKind) -> None
async def pop_logout_request(redis, request_id: str) -> tuple[int, UserKind] | None
async def put_test(redis, test_id: str, data: TestRun) -> None
async def get_test(redis, test_id: str) -> TestRun | None
async def mark_jti_seen(redis, provider_id: int, jti: str) -> bool
```

`TestRun` is a Pydantic model: `provider_id: int, admin_id: int, audience: UserKind, draft: ProviderChanges | None, status: Literal["started","done","failed"], error: str | None, result: TestResultData | None` with `TestResultData(subject: str, attributes: dict[str, list[str]], plan: dict[str, Any])` where `plan = login.plan_to_dict(plan)` (ids only; the admin API resolves names when reading, D16 `plan_to_out`).

### D11. OIDC — `tiqora.domain.oidc` (generalised)

```python
@dataclass(frozen=True, slots=True)
class OidcClientConfig:
    issuer: str
    client_id: str
    client_secret: str
    scopes: str
    redirect_uri: str
    pkce: Literal["auto", "always", "never"] = "auto"
    allow_private_network: bool = False
def oidc_client_config_from_settings(settings: Settings) -> OidcClientConfig       # env provider
def oidc_client_config(cfg: OidcProviderConfig, *, redirect_uri: str) -> OidcClientConfig

class OIDCService:
    def __init__(self, config: OidcClientConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None
    async def discover(self) -> dict[str, Any]          # per-issuer module cache, 1 h TTL (dict keyed by issuer)
    async def supports_pkce(self) -> bool
    async def use_pkce(self) -> bool                    # "always" -> True, "never" -> False, "auto" -> supports_pkce()
    async def authorize_url(self, state: str, *, code_verifier: str | None = None, nonce: str | None = None) -> str
    async def fetch_tokens_and_claims(self, code: str, *, code_verifier: str | None = None) -> OidcLoginResult
    async def fetch_claims(self, code: str, *, code_verifier: str | None = None) -> dict[str, Any]  # kept: returns .claims
    async def jwks(self) -> dict[str, Any]              # Task 21
    async def end_session_endpoint(self) -> str | None

@dataclass(frozen=True, slots=True)
class OidcLoginResult:
    claims: dict[str, Any]           # userinfo (or token "userinfo" fallback as today)
    id_token: str | None             # raw, kept only as id_token_hint for logout
    id_token_claims: dict[str, Any] | None   # Task 21: verified via JWKS when an id_token is present; sid comes from here
```

`_safe_get_url` passes `allow_private_networks=config.allow_private_network`. `backend/tests/test_oidc.py` switches to `OIDCService(oidc_client_config_from_settings(settings))`.

### D12. SAML SP — `tiqora.identity.saml_runtime`, `saml_keys`, `saml_metadata`, `saml_sp`

`pysaml2` 7.5.x (verified 7.5.5): `Saml2Client.prepare_for_authenticate(entityid, relay_state, binding, nameid_format, sign, …) -> (reqid, info)`, `parse_authn_request_response(xmlstr, binding, outstanding)`, `create_logout_request(destination, issuer_entity_id, name_id=, session_indexes=, sign=)`, `parse_logout_request(xmlstr, binding, relay_state, sigalg, signature)`, `parse_logout_request_response(xmlstr, binding)`, `create_logout_response(request, bindings, status, sign)`, `apply_binding(binding, msg_str, destination, relay_state, response, sign, sigalg)`; IdP side `saml2.server.Server.create_authn_response(identity, in_response_to, destination, sp_entity_id, name_id=, sign_response=, sign_assertion=, encrypt_assertion=, encrypt_cert_assertion=)` and `parse_authn_request`; metadata `saml2.metadata.create_metadata_string(None, config=…)`; config `metadata: {"inline": [xml]}`.

```python
# saml_runtime.py
@dataclass(frozen=True, slots=True)
class SamlAvailability:
    available: bool
    missing: Literal["package", "xmlsec1"] | None
    xmlsec_binary: str | None
def saml_availability() -> SamlAvailability          # lru_cache; import saml2 + shutil.which("xmlsec1")
class SamlUnavailable(Exception): ...
def import_saml2() -> Any                             # raises SamlUnavailable (patchable in tests)

# saml_keys.py
def generate_sp_keypair(common_name: str, *, days: int = 3650) -> tuple[str, str]   # (private_key_pem, cert_pem), RSA 3072, SHA-256
@dataclass(frozen=True, slots=True)
class KeyFiles:
    key_path: str
    cert_path: str
def materialize_keypair(private_key_pem: str, cert_pem: str) -> KeyFiles
    # writes into one process-wide tempfile.mkdtemp(prefix="tiqora-saml-") dir (0700), files 0600, named by
    # sha256(cert_pem)[:16]; reused across calls; removed atexit

# saml_metadata.py
class SamlMetadataError(ValueError): ...                 # German message for the admin UI
@dataclass(frozen=True, slots=True)
class IdpMetadataInfo:
    entity_ids: tuple[str, ...]
    signing_certs: tuple[str, ...]                        # base64 DER as in the XML
    sso_redirect_url: str | None
    slo_redirect_url: str | None
    slo_post_url: str | None
    valid_until: datetime | None
def parse_idp_metadata(xml: str, *, entity_id: str | None) -> IdpMetadataInfo
    # defusedxml.ElementTree.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True);
    # needs an IDPSSODescriptor with ≥1 signing cert; several entities and no entity_id -> SamlMetadataError
    # ("Die Metadaten enthalten mehrere Anmeldedienste – bitte die Entity-ID angeben."); expired validUntil -> error
async def fetch_idp_metadata(url: str, *, allow_private_network: bool, timeout: float = 10.0,
                             transport: httpx.AsyncBaseTransport | None = None) -> str
    # https only, pin_outbound_url, follow_redirects False, max 5 MB, SamlMetadataError on any failure
def effective_metadata_xml(record: ProviderRecord) -> str | None     # config.idp_metadata_xml or record.metadata_xml

# saml_sp.py
class SamlResponseInvalid(Exception):
    def __init__(self, reason: str) -> None: ...          # reason is logged, never shown
@dataclass(frozen=True, slots=True)
class SamlRedirect:
    request_id: str
    url: str
@dataclass(frozen=True, slots=True)
class SamlAssertionResult:
    in_response_to: str | None
    assertion_id: str
    not_on_or_after: datetime | None
    name_id: str
    name_id_format: str | None
    name_qualifier: str | None
    sp_name_qualifier: str | None
    session_index: str | None
    attributes: dict[str, tuple[str, ...]]               # normalize_attributes(response.ava) + {"saml:name_id": (name_id,)}
    destination: str | None

class SamlServiceProvider:
    def __init__(self, record: ProviderRecord, *, settings: Settings) -> None
        # builds the pysaml2 SPConfig (below) lazily on first use; SamlUnavailable when missing
    async def login_redirect(self, *, relay_state: str, user_kind: UserKind) -> SamlRedirect
        # AssertionConsumerServiceURL = agent/customer ACS; NameIDPolicy from name_id_format; signed if sign_requests
    async def parse_login_response(self, saml_response_b64: str, *, outstanding: Mapping[str, str],
                                   expected_acs: str) -> SamlAssertionResult
        # 1. base64-decode (max 2 MB), defusedxml forbid_dtd pre-check -> SamlResponseInvalid("dtd")
        # 2. pysaml2 parse with outstanding={request_id: expected_acs} (empty dict for IdP-initiated)
        # 3. response.assertion must be signed (pysaml2 want_assertions_signed) — any pysaml2 exception -> SamlResponseInvalid
        # 4. destination must equal expected_acs when present (stops agent⇄customer ACS swaps)
    def sp_metadata_xml(self) -> str
    async def logout_redirect(self, info: SsoSessionInfo, *, relay_state: str) -> SamlRedirect | None
        # None when the IdP metadata has no SingleLogoutService
    async def parse_logout_response(self, saml_response: str, *, binding: str) -> str | None   # returns in_response_to
    async def handle_logout_request(self, saml_request: str, *, binding: str, relay_state: str | None,
                                    sigalg: str | None, signature: str | None) -> tuple[LogoutRequestData, str]
        # verifies signature, returns (name_id, session_indexes) and the redirect URL carrying a signed LogoutResponse

@dataclass(frozen=True, slots=True)
class LogoutRequestData:
    name_id: str
    session_indexes: tuple[str, ...]
```

SP config (built inside the executor):

```python
{
  "entityid": urls.sp_entity_id,
  "xmlsec_binary": availability.xmlsec_binary,
  "key_file": kf.key_path, "cert_file": kf.cert_path,
  "encryption_keypairs": [{"key_file": kf.key_path, "cert_file": kf.cert_path}],
  "metadata": {"inline": [idp_metadata_xml]},
  "accepted_time_diff": cfg.clock_skew_seconds,
  "allow_unknown_attributes": True,
  "only_use_keys_in_metadata": True,
  "service": {"sp": {
      "endpoints": {
          "assertion_consumer_service": [(urls.agent_acs_url, BINDING_HTTP_POST), (urls.customer_acs_url, BINDING_HTTP_POST)],
          "single_logout_service": [(urls.slo_url, BINDING_HTTP_REDIRECT), (urls.slo_url, BINDING_HTTP_POST)],
      },
      "want_assertions_signed": True,
      "want_response_signed": cfg.want_response_signed,
      "authn_requests_signed": cfg.sign_requests,
      "logout_requests_signed": True,
      "allow_unsolicited": cfg.allow_idp_initiated,
      "name_id_policy_format": NAME_ID_FORMATS[cfg.name_id_format],
  }},
}
```

`NAME_ID_FORMATS = {"unspecified": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified", "emailAddress": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress", "persistent": "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent", "transient": "urn:oasis:names:tc:SAML:2.0:nameid-format:transient"}`. Subject = NameID value; with `transient` the subject is not stable → `plan_login` never stores a link for a transient NameID (`identity.subject` stays the NameID for this login only) and `match_by` decides every time. Certificate rollover: every signing cert in the current metadata is accepted (pysaml2 does that with `only_use_keys_in_metadata`).

Browser binding of the SAML flow (decision): login start sets cookie `tiqora_saml_req=<request_id>` (`HttpOnly`, `Path=/`, `Max-Age=600`, `SameSite=None; Secure` when `settings.session_cookie_secure`, else `SameSite=Lax` + structlog warning once "saml_cookie_binding_disabled_http"). The ACS requires cookie == `InResponseTo` **only when `session_cookie_secure`** (cross-site POST never carries a Lax cookie; plain-HTTP dev cannot use `SameSite=None`). `InResponseTo` → Redis request cache + assertion-id replay cache are always enforced.

After a successful ACS the response is a 200 HTML landing page (`identity/http.py::login_landing_response(target)`: `<meta http-equiv="refresh" content="0;url=…">` + a link, no script, CSP-safe) instead of a 302, so a `SameSite=Strict` session cookie set on the cross-site POST is sent on the following same-site navigation.

### D13. Customer second factors — `tiqora.domain.two_factor_subject`

```python
@dataclass(frozen=True, slots=True)
class TwoFactorSubject:
    kind: Literal["agent", "customer"]
    totp_model: Any                 # TiqoraUserTotp | TiqoraCustomerTotp (both expose .user_id)
    passkey_model: Any              # TiqoraUserPasskey | TiqoraCustomerPasskey
    redis_ns: str                   # "" (agent: existing keys unchanged) | "customer:"
    user_handle_prefix: bytes       # b"" (agent: user_id.to_bytes(8,"big") unchanged) | b"tiqora-customer:"
AGENT_2FA: Final[TwoFactorSubject]
CUSTOMER_2FA: Final[TwoFactorSubject]
def webauthn_user_handle(subject: TwoFactorSubject, user_id: int) -> bytes   # prefix + user_id.to_bytes(8, "big")
```

`TOTPService(session, settings, redis_client=None, *, subject: TwoFactorSubject = AGENT_2FA)`; Redis keys become `f"tiqora:totp:{subject.redis_ns}used:{id}"` / `f"tiqora:totp:{subject.redis_ns}pending:{id}"` (agent strings unchanged). `WebAuthnService(session, redis_client, settings, *, subject: TwoFactorSubject = AGENT_2FA)`; challenge keys `f"tiqora:webauthn:{subject.redis_ns}reg:{token}"`. All `select(TiqoraUserTotp)…` become `select(self._subject.totp_model)…`. Customers: 2FA is opt-in (no enforcement); SSO logins skip it (same rule as agents); password and LDAP logins with an enrolled factor get a pending session.

`CustomerSessionStore` gains `create_pending(customer_id, login, ttl)`, `get_pending(token)`, `promote_pending(token)` (payload prefix `PENDING:` like agents), an `authat` marker (`tiqora:csession:authat:{token}`, absolute TTL) and `seconds_since_auth(token)`.

### D14. LDAP providers and AuthSync — `tiqora.identity.ldap_login`, `domain/_ldap_core.py`

```python
# _ldap_core.py (added; ldap_authenticate keeps its signature and returns entry.uid)
@dataclass(frozen=True, slots=True)
class LdapEntry:
    uid: str
    dn: str
    attributes: dict[str, tuple[str, ...]]
    member_group_dns: tuple[str, ...]                 # subset of the requested group DNs the user is a member of
async def ldap_authenticate_entry(config: LdapConfig, login: str, password: str, *,
                                  attributes: Sequence[str] = (), member_of_group_dns: Sequence[str] = ()) -> LdapEntry | None
    # same bind-search-bind; the user search requests uid_attr + attributes (+ subject attr);
    # membership per DN uses Znuny's filter: (access_attr=<user DN or login per user_attr>) under base=<group DN>

# ldap_login.py
def ldap_core_config(cfg: LdapProviderConfig) -> LdapConfig
def requested_attributes(record: ProviderRecord) -> list[str]        # every non-literal, non-pseudo source in attribute_map + subject_attr
def rule_group_dns(record: ProviderRecord) -> list[str]              # values of rules with attribute "ldap:group_dn"
def identity_from_entry(record: ProviderRecord, entry: LdapEntry) -> ExternalIdentity
    # subject = first value of subject_attr (objectGUID bytes -> hex) else entry.dn; attributes = entry.attributes +
    # {"ldap:dn": (dn,), "ldap:group_dn": member_group_dns, uid_attr: (uid,)}
async def ldap_password_login(session, settings, *, login: str, password: str,
                              user_kind: UserKind) -> tuple[ProviderRecord, ExternalIdentity] | None
    # enabled ldap providers for the audience, sort order; first successful bind wins (Znuny tries AuthModule1..n)
```

Password login (`/api/v1/auth/login`, `/api/portal/auth/login`): local password first (unchanged), then `ldap_password_login`; on success `plan_login` + `apply_login` (env-ldap: `attribute_map = {login: [uid_attr]}`, no JIT, no rules → same as today: the LDAP UID must be an existing valid login). `require_sso_opt_in` never applies to LDAP. Znuny `NestedGroupSearch` is not ported (deviation).

### D15. Agent/customer SSO routes

Agent (`backend/src/tiqora/api/v1/auth_sso.py`, `APIRouter(prefix="/auth", tags=["auth"])`, included right after `auth.router`):

```
GET  /api/v1/auth/sso/{slug}/login?next=/…&test=<test_id>          -> 302 to IdP | 302 /login?sso_error=<code>
GET  /api/v1/auth/oidc/{slug}/callback?code&state                   -> 302 next | 302 /login?sso_error=<code>
POST /api/v1/auth/saml/{slug}/acs   form SAMLResponse, RelayState   -> 200 landing HTML | 302 /login?sso_error=<code>
GET  /api/v1/auth/saml/{slug}/metadata                              -> 200 application/samlmetadata+xml
GET  /api/v1/auth/saml/{slug}/sls   (SAMLResponse|SAMLRequest, RelayState, SigAlg, Signature)
POST /api/v1/auth/saml/{slug}/sls   form (same fields)              -> 302 / 303
POST /api/v1/auth/sso/logout                                        -> 303 IdP logout URL | 303 /login?logged_out=1
POST /api/v1/auth/oidc/{slug}/backchannel-logout  form logout_token -> 200 (Cache-Control: no-store) | 400
```

Existing aliases in `api/v1/auth.py` stay and delegate: `GET /auth/oidc/login` → `sso_login(slug="env-oidc")`; `GET /auth/oidc/callback` → the generic callback for `env-oidc` with `json_response = not _wants_html(request)` (JSON `LoginResponse` as today for API clients and existing tests; browsers now get a 302 to `next`/"/" instead of a JSON page — deviation). Both 404 when `settings.oidc_enabled` is false (as today).

Portal (`backend/src/tiqora/api/portal/auth_sso.py`, under `/api/portal` → gated by `require_portal_enabled`):

```
GET  /api/portal/auth/methods                     -> PortalAuthMethodsOut
GET  /api/portal/auth/sso/{slug}/login?next=/portal/…
GET  /api/portal/auth/oidc/{slug}/callback
POST /api/portal/auth/saml/{slug}/acs
POST /api/portal/auth/sso/logout
```

SAML metadata, SLS and OIDC back-channel are shared under `/api/v1` (one SP entity / one OIDC client per provider; deviation from the spec's "same under /api/portal").

Error codes in `sso_error` (agent `/login?sso_error=…&next=…`, portal `/portal/login?sso_error=…`): every `RejectCode` plus `unknown_provider`, `config`, `saml_unavailable`, `state`, `saml_invalid`, `oidc_failed`, `idp_error`. The legacy value `1` keeps working (generic message).

Discovery:

```python
class LoginProviderOut(BaseModel):
    slug: str
    kind: Literal["oidc", "saml"]
    label: str            # button_label or name; env rows without button_label -> "" (frontend: auth.providers.defaultLabel)
    icon: str | None
class AuthMethodsOut(BaseModel):         # existing fields unchanged; new ones appended
    password: bool = True
    oidc: bool = False                   # legacy meaning: env OIDC enabled
    spnego: bool = False
    ldap: bool = False                   # legacy meaning: env LDAP enabled, now: any enabled agent LDAP provider
    webauthn: bool = False
    portal_enabled: bool = True
    providers: list[LoginProviderOut] = Field(default_factory=list)   # enabled oidc/saml with agent audience
    saml: bool = False                   # saml_availability().available
class PortalAuthMethodsOut(BaseModel):
    password: bool = True
    providers: list[LoginProviderOut] = Field(default_factory=list)
    webauthn: bool = False
```

`UserMe` and `CustomerMe` gain `sso_logout: bool = False` (session carries `SsoSessionInfo` whose provider supports a logout redirect). `CustomerLoginResponse` becomes `customer: CustomerMe | None = None, pending_2fa: bool = False, totp_enrolled: bool = False, passkey_enrolled: bool = False`.

CSRF (`security/csrf.py`): new `_CSRF_EXEMPT = (re.compile(r"^/api/v1/auth/saml/[a-z0-9-]+/(acs|sls)$"), re.compile(r"^/api/portal/auth/saml/[a-z0-9-]+/acs$"))` — only the SAML POST endpoints, as the spec says; `csrf_check_required` returns False for these paths. Needed because `request_has_session_cookie` is true when EITHER cookie exists (a portal customer doing an agent SAML login would get 403). The OIDC back-channel POST is server-to-server and carries no cookie, so the existing rule already lets it through.

### D16. Admin API — `/api/v1/admin/identity-providers`

Router `APIRouter(prefix="/identity-providers", tags=["admin:identity"])`, every route `AdminUser`; static paths are declared before `/{provider_id}`.

```python
# api/v1/admin/identity_schemas.py
class ProviderEndpointsOut(BaseModel):
    agent_redirect_uri: str | None; customer_redirect_uri: str | None
    agent_acs_url: str | None; customer_acs_url: str | None
    sp_entity_id: str | None; sp_metadata_url: str | None; slo_url: str | None
    backchannel_logout_url: str | None
    base_url_missing: bool
class IdentityProviderSummaryOut(BaseModel):
    id: int; slug: str; kind: Literal["oidc", "saml", "ldap"]; source: Literal["db", "env"]
    name: str; audience: Literal["agent", "customer", "both"]; enabled: bool; sort: int
    button_label: str | None; button_icon: str | None; preset: str | None
    jit_enabled: bool; read_only: bool; metadata_error: str | None; link_count: int
class IdentityProviderOut(IdentityProviderSummaryOut):
    config: dict[str, Any]              # secrets -> SECRET_MASK when set, else None; env rows: env values, secrets masked
    secrets_set: list[str]
    match_by: Literal["login", "email"]; require_sso_opt_in: bool
    attribute_map: AttributeMap; jit_defaults: JitDefaults; role_rules: list[RoleRule]
    sync_mode: Literal["managed", "authoritative"]
    metadata_fetched_at: UtcDateTime | None
    endpoints: ProviderEndpointsOut
class IdentityProviderCreate(BaseModel):
    preset: str; name: str = Field(min_length=1, max_length=200); slug: str
    audience: Literal["agent", "customer", "both"] = "agent"
class IdentityKindOut(BaseModel):
    kind: Literal["oidc", "saml", "ldap"]; json_schema: dict[str, Any]; i18n_prefix: str   # "admin.identity.config.<kind>"
    read_only_fields: list[str]
    hidden_fields: list[str]
class IdentityPresetOut(BaseModel):
    key: str; kind: Literal["oidc", "saml", "ldap"]; name_key: str; button_icon: str | None
    audience_default: Literal["agent", "customer", "both"]
class IdentityCapabilitiesOut(BaseModel):
    saml_available: bool; saml_missing: Literal["package", "xmlsec1"] | None
    public_base_url_set: bool; webauthn: bool
class NamedRefOut(BaseModel):
    id: int; name: str
class GroupPermRefOut(BaseModel):
    group_id: int; group_name: str; permission_key: str
class LoginPlanOut(BaseModel):
    user_kind: Literal["agent", "customer"]
    decision: Literal["login", "create", "reject"]
    matched_by: Literal["link", "login", "email", "jit"] | None
    user_id: int | None; login: str | None
    reject_code: str | None; reject_message: str | None
    profile: dict[str, str | None]
    profile_changes: dict[str, list[str | None]]     # field -> [old, new]
    roles_add: list[NamedRefOut]; roles_remove: list[NamedRefOut]
    groups_add: list[GroupPermRefOut]; groups_remove: list[GroupPermRefOut]
class IdentityTestStartIn(BaseModel):
    audience: Literal["agent", "customer"] = "agent"
    draft: ProviderChanges | None = None
class IdentityTestStartOut(BaseModel):
    test_id: str; login_url: str
class IdentityTestResultOut(BaseModel):
    status: Literal["started", "done", "failed"]; error: str | None
    subject: str | None; attributes: dict[str, list[str]]; plan: LoginPlanOut | None
class MappingPreviewIn(BaseModel):
    audience: Literal["agent", "customer"] = "agent"
    subject: str = "preview"
    attributes: dict[str, list[str]]
    draft: ProviderChanges | None = None
class IdentityLinkOut(BaseModel):
    id: int; user_kind: Literal["agent", "customer"]; subject: str; user_id: int; user_login: str
    first_seen: UtcDateTime; last_seen: UtcDateTime
class ManagedAssignmentOut(BaseModel):
    user_id: int; login: str; kind: Literal["role", "group"]; target_id: int; target_name: str
    permission_key: str; created: UtcDateTime
```

```
GET    /api/v1/admin/identity-providers                         -> list[IdentityProviderSummaryOut]
GET    /api/v1/admin/identity-providers/kinds                   -> list[IdentityKindOut]
GET    /api/v1/admin/identity-providers/presets                 -> list[IdentityPresetOut]
GET    /api/v1/admin/identity-providers/capabilities            -> IdentityCapabilitiesOut
POST   /api/v1/admin/identity-providers                         -> 201 IdentityProviderOut  (422 {"message", "errors"})
GET    /api/v1/admin/identity-providers/{id}                    -> IdentityProviderOut
PATCH  /api/v1/admin/identity-providers/{id}   ProviderChanges  -> IdentityProviderOut  (409 env row; 422 {"message": "Bitte die markierten Felder prüfen.", "errors": {...}})
DELETE /api/v1/admin/identity-providers/{id}                    -> 204 (409 env row)
POST   /api/v1/admin/identity-providers/{id}/refresh-metadata   -> IdentityProviderOut (SAML; 422 {"message": <SamlMetadataError>})
POST   /api/v1/admin/identity-providers/{id}/tests  IdentityTestStartIn -> IdentityTestStartOut
GET    /api/v1/admin/identity-providers/{id}/tests/{test_id}    -> IdentityTestResultOut (404 unknown/foreign admin)
POST   /api/v1/admin/identity-providers/{id}/preview  MappingPreviewIn -> LoginPlanOut
GET    /api/v1/admin/identity-providers/{id}/links?limit=200    -> list[IdentityLinkOut]
DELETE /api/v1/admin/identity-providers/{id}/links/{link_id}    -> 204
GET    /api/v1/admin/identity-providers/{id}/managed-assignments -> list[ManagedAssignmentOut]
```

Test login: `POST …/tests` stores a `TestRun` and returns `login_url = "/api/v1/auth/sso/{slug}/login?test={test_id}"` (portal audience: `/api/portal/auth/sso/{slug}/login?test=…`). The login start accepts a disabled provider only with a valid `test` whose `admin_id` equals the current agent session's user (the agent cookie is sent on that top-level GET); it applies `draft` via `merge_config` in memory. The callback/ACS then runs `plan_login` only (no session, no link, no JIT, no sync), stores `plan_to_dict(plan)` in the run, and redirects to `/admin/identity-providers/{id}?test={test_id}`. `GET …/tests/{test_id}` converts with `plan_to_out(session, plan_dict) -> LoginPlanOut` (role/group names by IN-list). `POST …/tests` on an LDAP provider → 422 `{"message": "Für LDAP gibt es keine Testanmeldung – bitte die Vorschau mit Beispielwerten nutzen."}` (deviation 11).

Presets (`identity/presets.py`): `entra_id` (oidc, issuer `https://login.microsoftonline.com/<Verzeichnis-ID>/v2.0`, icon microsoft, groups claim `groups`), `google_workspace` (oidc, issuer `https://accounts.google.com`, login/email `email`, icon google), `keycloak` (oidc, icon key), `authentik` (oidc, icon shield), `adfs` (saml, claims URIs, icon building), `generic_saml` (saml, icon shield), `generic_oidc` (oidc, icon key), `ldap` (ldap, OpenLDAP defaults, icon directory), `active_directory` (ldap, `uid_attr="sAMAccountName"`, `access_attr="member"`, `subject_attr="objectGUID"`, icon directory).

### D17. Writes to Znuny tables — `tiqora.domain.user_provisioning`

```python
async def create_agent(session, *, login: str, first_name: str, last_name: str, email: str | None,
                       mobile: str | None, title: str | None, pw_hash: str, valid_id: int, actor_id: int,
                       phone_extension: str | None = None) -> Users
    # exactly the current admin create_user body (users row + UserEmail/UserMobile/extension prefs) + USER_CACHE_TYPES; flush
async def update_agent_profile(session, user: Users, *, first_name: str | None, last_name: str | None,
                               email: str | None, mobile: str | None, actor_id: int) -> bool    # True if changed
async def grant_role(session, *, user_id: int, role_id: int, actor_id: int) -> bool          # False if present
async def revoke_role(session, *, user_id: int, role_id: int) -> bool
async def grant_group_permission(session, *, user_id: int, group_id: int, permission_key: str, actor_id: int) -> bool
async def revoke_group_permission(session, *, user_id: int, group_id: int, permission_key: str) -> bool
    # role/group functions invalidate USER_ROLE_CACHE_TYPES / USER_GROUP_CACHE_TYPES (same as the admin routes;
    # verified equal to Znuny PermissionRoleUserAdd / PermissionGroupUserAdd cleanup); flush only
async def current_roles(session, user_id: int) -> frozenset[int]
async def current_group_perms(session, user_id: int) -> frozenset[tuple[int, str]]
async def valid_role_ids(session) -> frozenset[int]
async def valid_group_ids(session) -> frozenset[int]
async def create_customer_user(session, *, login: str, email: str, customer_id: str, first_name: str,
                               last_name: str, pw_hash: str | None, actor_id: int, **optional: str | None) -> CustomerUser
async def update_customer_profile(session, cu: CustomerUser, *, first_name, last_name, email, mobile,
                                  actor_id: int) -> bool
```

Znuny check (recorded): `Kernel::System::User::UserAdd` writes only the `User*` preferences passed in (AdminUser passes `UserEmail`, `UserMobile`, `UserTitle`); there are no `UserLanguage`/`UserTheme` default rows — defaults come from SysConfig at read time. `UserAdd` requires `UserEmail` → JIT requires email. JIT password: `tiqora.domain.password_setup.unusable_password_hash()`; JIT customers likewise. JIT agents also get a `tiqora_user_auth_config` row with `sso_eligible=True` (`AuthConfigService.set(user_id, sso_eligible=True)` — note it commits; call it after the main write inside `apply_login`'s final commit sequence).

### D18. Worker

`KEY_IDENTITY_METADATA_REFRESH_ENABLED = "daemon.identity_metadata.enabled"` in `domain/settings_store.py`; `DaemonService(slug="identity_metadata", enabled_key=KEY_IDENTITY_METADATA_REFRESH_ENABLED, default_enabled=True, toggleable=True, schedule_kind="daily", daily_at="03:15")`; `_daily_loop("identity_metadata", run_identity_metadata_refresh_tick, "03:15", stop)` in `worker/__main__.py`. `run_identity_metadata_refresh_tick() -> dict[str, int]` → `{"refreshed": n, "failed": n, "skipped": n}`: every SAML provider (enabled or not) with `idp_metadata_url` and no uploaded XML; success writes `metadata_xml`, `metadata_fetched_at`, clears `metadata_error`; failure keeps the old XML and sets `metadata_error` (German).

### D19. Frontend API and components

```ts
// frontend/src/lib/identityApi.ts
export type IdentityProviderSummary = Schemas["IdentityProviderSummaryOut"];
export type IdentityProvider = Schemas["IdentityProviderOut"];
export type IdentityKind = Schemas["IdentityKindOut"];
export type IdentityPreset = Schemas["IdentityPresetOut"];
export type IdentityCapabilities = Schemas["IdentityCapabilitiesOut"];
export type ProviderChanges = Schemas["ProviderChanges"];
export type LoginPlan = Schemas["LoginPlanOut"];
export type IdentityTestResult = Schemas["IdentityTestResultOut"];
export type IdentityLink = Schemas["IdentityLinkOut"];
export type ManagedAssignment = Schemas["ManagedAssignmentOut"];
export type LoginProvider = Schemas["LoginProviderOut"];
export const identityApi = {
  list(signal?: AbortSignal): Promise<IdentityProviderSummary[]>,
  get(id: number, signal?: AbortSignal): Promise<IdentityProvider>,
  kinds(signal?: AbortSignal): Promise<IdentityKind[]>,
  presets(signal?: AbortSignal): Promise<IdentityPreset[]>,
  capabilities(signal?: AbortSignal): Promise<IdentityCapabilities>,
  create(body: Schemas["IdentityProviderCreate"]): Promise<IdentityProvider>,
  update(id: number, changes: ProviderChanges): Promise<IdentityProvider>,
  remove(id: number): Promise<void>,
  refreshMetadata(id: number): Promise<IdentityProvider>,
  startTest(id: number, body: Schemas["IdentityTestStartIn"]): Promise<Schemas["IdentityTestStartOut"]>,
  testResult(id: number, testId: string, signal?: AbortSignal): Promise<IdentityTestResult>,
  preview(id: number, body: Schemas["MappingPreviewIn"]): Promise<LoginPlan>,
  links(id: number, signal?: AbortSignal): Promise<IdentityLink[]>,
  removeLink(id: number, linkId: number): Promise<void>,
  managedAssignments(id: number, signal?: AbortSignal): Promise<ManagedAssignment[]>,
};
export function agentSsoLoginUrl(slug: string, next?: string): string    // "/api/v1/auth/sso/{slug}/login?next=…"
export function portalSsoLoginUrl(slug: string, next?: string): string   // "/api/portal/auth/sso/{slug}/login?next=…"
export function providerFieldErrors(err: unknown): FieldErrors           // detail.errors of a 422

// frontend/src/lib/ssoLogout.ts
export function submitSsoLogout(action: "/api/v1/auth/sso/logout" | "/api/portal/auth/sso/logout"): void
  // builds <form method="post" action=…> in document.body and submits (top-level navigation, same-origin → CSRF ok)

// frontend/src/lib/ssoErrors.ts
export const SSO_ERROR_CODES: readonly string[];
export function ssoErrorKey(code: string | undefined | null): string | null   // "1"/unknown -> "auth.ssoFailed"; known -> `auth.ssoErrors.${code}`

// frontend/src/components/auth/SsoProviderButtons.tsx
export function SsoProviderButtons(props: { providers: LoginProvider[]; hrefFor: (slug: string) => string;
  onBeforeNavigate?: (p: LoginProvider) => void; testIdPrefix?: string /* default "sso-provider" */ }): JSX.Element | null
  // one full-width secondary Button per provider: ProviderIcon + label; testid `${prefix}-${slug}`

// frontend/src/components/auth/ProviderIcon.tsx
export function ProviderIcon(props: { icon: string | null | undefined; className?: string }): JSX.Element
  // microsoft/google: small inline SVG marks; key/shield/building/directory: lucide-react KeyRound/ShieldCheck/Building2/FolderTree; default KeyRound

// frontend/src/components/security/twoFactorApi.ts
export interface TwoFactorApi {
  status(): Promise<{ totp_enabled: boolean }>;
  enroll(currentCode?: string): Promise<{ secret: string; otpauth_uri: string }>;
  enrollQrUrl(): string;
  confirm(code: string): Promise<{ totp_enabled: boolean }>;
  disable(code: string): Promise<{ totp_enabled: boolean }>;
  listPasskeys(): Promise<Schemas["PasskeyOut"][]>;
  registerBegin(): Promise<unknown>;
  registerFinish(credential: unknown, name: string): Promise<unknown>;
  deletePasskey(id: number): Promise<void>;
  webauthnEnabled(): Promise<boolean>;
}
export const agentTwoFactorApi: TwoFactorApi;      // wraps today's api.* calls used by SecurityPage
export const customerTwoFactorApi: TwoFactorApi;   // /api/portal/auth/totp/* + /passkey/*

// frontend/src/components/security/TwoFactorPanel.tsx
export function TwoFactorPanel(props: { api: TwoFactorApi; testIdPrefix?: string /* "security" keeps agent test ids */ }): JSX.Element
```

### D20. i18n (new keys; German first)

- `auth.providers.defaultLabel` = "Mit SSO anmelden"; `auth.providers.or` = "oder"
- `auth.ssoErrors.{no_subject,no_match,user_invalid,sso_not_enabled,email_unverified,ambiguous_email,login_taken,jit_missing_fields,provider_disabled,unknown_provider,config,saml_unavailable,state,saml_invalid,oidc_failed,idp_error}` — e.g. `no_match` = "Zu diesem Konto gibt es hier keinen Zugang. Bitte wenden Sie sich an die Administration.", `sso_not_enabled` = "Für Ihr Konto ist die Anmeldung über den Anmeldedienst nicht freigeschaltet.", `state` = "Die Anmeldung hat zu lange gedauert oder wurde in einem anderen Fenster begonnen. Bitte erneut versuchen.", `saml_invalid` = "Die Antwort des Anmeldedienstes war ungültig. Bitte erneut versuchen.", `config` = "Die Anmeldung über diesen Anmeldedienst ist noch nicht vollständig eingerichtet."
- `auth.loggedOut` = "Sie wurden abgemeldet."
- `admin.nav.identityProviders` = "Identitätsanbieter (SSO)"; `admin.pageDescriptions.identityProviders` = "Anmeldung über Microsoft, Google, Keycloak, SAML oder LDAP – für Agenten und Kunden."
- `admin.identity.{title,add,empty,envBadge ("aus Umgebungsvariablen, hier nicht änderbar"),enabled,disabled,audience.{agent,customer,both} ("Agenten","Kunden","Agenten und Kunden"),kind.{oidc,saml,ldap} ("OpenID Connect","SAML","LDAP / Active Directory"),tabs.{connection,mapping,roles,test,links} ("Verbindung","Zuordnung","Rollen","Testanmeldung","Verknüpfungen"),preset.title ("Identitätsanbieter hinzufügen"),preset.<key> (names),fields.{name,slug,slugHelp,audience,buttonLabel,buttonIcon,sort},endpoints.{title ("Diese Adressen beim Anmeldedienst eintragen"),agentRedirect,customerRedirect,agentAcs,customerAcs,entityId,metadata,slo,backchannel,baseUrlMissing},metadata.{refresh,fetchedAt,error},saml.unavailable.{package,xmlsec1},test.{start,hint,waiting,received,plan,decision.{login,create,reject},matchedBy.{link,login,email,jit}},mapping.{title,matchBy.{login,email},field.{login,email,first_name,last_name,customer_id,mobile},sourceHelp,jit,jitHelp,updateProfile,initialRoles,initialGroups,customerId,preview,previewHint},roles.{title,attribute,value,roles,groups,permissions,addRule,syncMode.{managed,authoritative},syncModeHelp.{managed,authoritative},managedTitle,managedEmpty},links.{title,empty,remove,removeConfirm},delete,deleteConfirm ("Identitätsanbieter löschen? Die Verknüpfungen der Konten werden entfernt. Rollen und Gruppen, die er vergeben hat, bleiben als manuelle Zuordnungen bestehen."),saved,saveError,loadError}`
- `admin.identity.config.<kind>.fields.<field>.{label,help}` for every field of the three config models (SchemaForm `i18nPrefix`)
- `admin.daemons.services.identity_metadata.{name,description}` = "Metadaten der Anmeldedienste" / "Lädt täglich die SAML-Metadaten der Identitätsanbieter neu, damit neue Zertifikate rechtzeitig gelten."
- `portal.security.{title,nav}` = "Sicherheit"; `portal.login.{ssoDivider,twoFactorTitle,totpLabel,passkeyButton,verify}`; customer reuse of `security.*` keys through `TwoFactorPanel`

### Deviations from spec

1. **Link table keys by `user_id` + `subject_hash`, not `user_login` + `subject`.** Logins can be renamed in Znuny; ids cannot. The subject is compared by sha256 because MariaDB installs typically use case-insensitive collations (a case-only different NameID would hit another account). `user_login` is kept as a display snapshot.
2. **Unique per (provider, user_kind, subject)** instead of (provider, subject): a provider with audience "both" may legitimately map one IdP account to an agent AND a customer.
3. **Customer 2FA tables key by `customer_user.id`** (`customer_user_id`), not `customer_user.login` — same reason as 1; the ORM attribute is `user_id` so the agent services are reused.
4. **SAML metadata, SLS and OIDC back-channel logout exist only under `/api/v1`** (one SP entity / one OIDC client per provider serves both audiences); only login start, OIDC callback and ACS are duplicated under `/api/portal`.
5. **Env OIDC callback answers browsers with a redirect** (JSON stays for `Accept: application/json`). Today a browser ends on a raw JSON page.
6. **No `require_mfa_claim` column** — step-up is phase 3; the column is added there.
7. **Deprovisioning ("IdP says user gone") is not in this plan** — Znuny's AuthSync only syncs at login; deprovisioning arrives with SCIM in phase 3. LDAP `NestedGroupSearch` is not ported.
8. **Customer role/group sync is not built** — Znuny 7.3 does not document customer attribute/group sync either; customers get attribute (profile) sync and JIT only.
9. **Role-sync mode "authoritative"** is added next to the spec's managed mode so sites migrating from Znuny `AuthSyncModule` can keep Znuny's exact behaviour (all roles/groups of a synced kind are replaced).
10. **SP certificate rotation UI** is not built (10-year self-signed cert generated per provider; replace by deleting and re-adding the provider).
11. **LDAP test login** is a mapping preview with pasted attributes, not a live bind.
12. **Keycloak round trip is driven with httpx, not Playwright** (Task 25, opt-in marker) — the browser leg is two form posts; a Playwright stack against a Keycloak container adds little.
13. **`AuthMethodsOut.ldap` changes meaning** from "env LDAP enabled" to "any enabled agent LDAP provider" (identical for env-only setups).

---
## Tasks

Order: Phase 1 = Tasks 1–15, Phase 2 = Tasks 16–24, Task 25 optional integration test, Task 26 closes. Backend tasks are sequential (each builds on the previous commits). Frontend Task 14 needs Task 8 + 11 (openapi), Task 15 needs Task 13, Task 23 needs Tasks 18 + 19, Task 24 needs Tasks 20 + 22. Backend-only: 1–13, 16–22, 25. Frontend-only: 14, 15, 23, 24. Task 26: docs + full run.

### Phase 1

### Task 1: SAML runtime dependency, image, CI, availability probe (backend-only)

**Files:**
- Modify: `backend/pyproject.toml` (extra `saml = ["pysaml2>=7.5.5,<8"]`; mypy override modules `"saml2"`, `"saml2.*"`; pytest markers `"saml: SAML tests that need pysaml2 + the xmlsec1 binary"` and `"keycloak: optional Keycloak integration tests (TIQORA_KEYCLOAK_TESTS=1, needs Docker)"`), `uv.lock` (`uv lock` at the repo root)
- Modify: `Dockerfile` (python-deps stage: both `uv sync` lines get `--extra saml`; runtime stage apt list gets `xmlsec1` and `libxmlsec1-openssl`)
- Modify: `.github/workflows/ci.yml` (lint job `uv sync --extra dev --extra saml`; test job apt line `gnupg xmlsec1`, sync `--extra dev --extra crypto --extra saml`, env `TIQORA_REQUIRE_SAML_TESTS: "1"`), `.github/workflows/nightly-random-order.yml` (same apt + sync + env)
- Create: `backend/src/tiqora/identity/__init__.py`, `backend/src/tiqora/identity/types.py` (D1 verbatim), `backend/src/tiqora/identity/saml_runtime.py` (D12 runtime part)
- Create: `backend/tests/_saml_idp.py` (for now only `require_saml()` — Task 9 adds the IdP)
- Test: `backend/tests/test_identity_types.py`, `backend/tests/test_identity_saml_runtime.py`

**Interfaces:**
- Consumes: nothing.
- Produces: D1 names; `saml_availability()`, `SamlAvailability`, `SamlUnavailable`, `import_saml2()`; `_saml_idp.require_saml() -> None` (pytest skip, or fail under `TIQORA_REQUIRE_SAML_TESTS=1`).

- [ ] **Step 1: Write the failing tests.**
  ```python
  # backend/tests/test_identity_types.py
  from tiqora.identity.types import SLUG_RE, audience_includes, subject_hash, RESERVED_SLUGS

  def test_slug_rules() -> None:
      assert SLUG_RE.match("entra-id")
      assert not SLUG_RE.match("-x") and not SLUG_RE.match("A") and not SLUG_RE.match("a")
      assert "env-oidc" in RESERVED_SLUGS

  def test_audience_includes() -> None:
      assert audience_includes("both", "agent") and audience_includes("both", "customer")
      assert audience_includes("agent", "agent") and not audience_includes("agent", "customer")

  def test_subject_hash_is_case_sensitive_sha256() -> None:
      assert subject_hash("Abc") != subject_hash("abc")
      assert len(subject_hash("x")) == 64

  # backend/tests/test_identity_saml_runtime.py
  import tiqora.identity.saml_runtime as rt

  def test_missing_binary_reports_xmlsec1(monkeypatch) -> None:
      rt.saml_availability.cache_clear()
      monkeypatch.setattr(rt.shutil, "which", lambda name: None)
      monkeypatch.setattr(rt, "_package_present", lambda: True)
      av = rt.saml_availability()
      assert av.available is False and av.missing == "xmlsec1"
      rt.saml_availability.cache_clear()

  def test_missing_package_reports_package(monkeypatch) -> None:
      rt.saml_availability.cache_clear()
      monkeypatch.setattr(rt, "_package_present", lambda: False)
      assert rt.saml_availability().missing == "package"
      rt.saml_availability.cache_clear()

  def test_import_saml2_raises_unavailable_when_missing(monkeypatch) -> None:
      monkeypatch.setattr(rt, "_package_present", lambda: False)
      with pytest.raises(rt.SamlUnavailable):
          rt.import_saml2()
  ```
- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError: tiqora.identity`). `cd backend && uv run python -m pytest tests/test_identity_types.py tests/test_identity_saml_runtime.py -q`
- [ ] **Step 3: Implement.** `saml_runtime.py`:
  ```python
  def _package_present() -> bool:
      return importlib.util.find_spec("saml2") is not None

  @functools.lru_cache(maxsize=1)
  def saml_availability() -> SamlAvailability:
      if not _package_present():
          return SamlAvailability(False, "package", None)
      binary = shutil.which("xmlsec1")
      if binary is None:
          return SamlAvailability(False, "xmlsec1", None)
      return SamlAvailability(True, None, binary)

  def import_saml2() -> Any:
      if not _package_present():
          raise SamlUnavailable("pysaml2 is not installed; install the 'saml' extra (uv sync --extra saml)")
      import saml2  # noqa: PLC0415 — optional dependency
      return saml2
  ```
  `_saml_idp.require_saml()`:
  ```python
  def require_saml() -> None:
      msg = None
      if importlib.util.find_spec("saml2") is None:
          msg = "pysaml2 missing (uv sync --extra saml)"
      elif shutil.which("xmlsec1") is None:
          msg = "xmlsec1 binary missing (brew install libxmlsec1 / apt install xmlsec1)"
      if msg is None:
          return
      if os.environ.get("TIQORA_REQUIRE_SAML_TESTS") == "1":
          pytest.fail(msg, pytrace=False)
      pytest.skip(msg, allow_module_level=True)
  ```
  Dependency + image + CI edits as listed under Files. Run `uv lock` at the repo root; `cd backend && uv sync --extra dev --extra crypto --extra saml`; locally `brew install libxmlsec1` (prints `xmlsec1 --version`). Check `osv-scanner.toml` stays without new entries (`osv-scanner --lockfile uv.lock` if installed; otherwise note in the commit that CI's supply-chain job checks it).
- [ ] **Step 4: Run → PASS**, then `docker build --target python-deps -t tiqora-deps-check .` is NOT required (CI builds the image); instead verify the Dockerfile diff by eye: both `uv sync` invocations carry `--extra saml`, runtime apt list has `xmlsec1 libxmlsec1-openssl`. Lint gates.
- [ ] **Step 5: Commit** `build(identity): saml extra (pysaml2), xmlsec1 in image and CI, availability probe`.

### Task 2: Tables, ORM, migration (backend-only)

**Files:**
- Modify: `backend/src/tiqora/db/tiqora/models.py` (`TiqoraIdentityProvider`, `TiqoraIdentityLink`, `TiqoraIdentityManagedAssignment`, `IDENTITY_METADATA_TEXT`)
- Create: `backend/alembic/versions_tiqora/<date>_<NNNN>_identity_providers.py` (revision id pattern `YYYYMMDD_NNNN`, `down_revision` = current primary head)
- Modify: `backend/tests/test_migration_gate.py` (`TIQORA_HEAD`), `backend/tests/test_migrate_cli.py`
- Test: `backend/tests/test_tiqora_model_ddl.py` (extend if it lists tables explicitly), `backend/tests/test_identity_tables.py`

**Interfaces:**
- Consumes: D2.
- Produces: ORM classes with attributes named exactly as the D2 columns.

- [ ] **Step 1: Failing test** `test_identity_tables.py` (DB, both dialects): create via `TiqoraBase.metadata.create_all`, insert a provider + link + managed row, assert (a) a second link with the same `(provider_id, user_kind, subject_hash)` raises `IntegrityError`, (b) the same subject_hash with `user_kind="customer"` is accepted, (c) deleting the provider cascades links and managed rows (enable FK on SQLite not needed — both real DBs), (d) `metadata_xml` stores 3 MB on MariaDB (MEDIUMTEXT). Clean-up: module autouse fixture deletes `tiqora_identity_provider WHERE slug LIKE 'idptest-%'` (links cascade).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** ORM + migration. Migration `upgrade()` creates the three tables with the D2 columns, constraints and indexes (FKs `ondelete="CASCADE"` named `fk_tiqora_identity_link_provider`, `fk_tiqora_identity_managed_provider`); `downgrade()` drops managed → link → provider. No server defaults on TEXT columns (MariaDB). Check head first: `ls backend/alembic/versions_tiqora | tail -3 && cd backend && uv run alembic heads`.
- [ ] **Step 4: Run →** `uv run python -m pytest tests/test_identity_tables.py tests/test_tiqora_model_ddl.py tests/test_migration_gate.py tests/test_migrate_cli.py -q` PASS; lint gates.
- [ ] **Step 5: Commit** `feat(identity): provider, link and managed-assignment tables`.

### Task 3: Config models, presets, provider store (backend-only)

**Files:**
- Create: `backend/src/tiqora/identity/config_models.py` (D3), `backend/src/tiqora/identity/presets.py` (D16 presets), `backend/src/tiqora/identity/store.py` (D4), `backend/src/tiqora/identity/saml_keys.py` (D12 `generate_sp_keypair`, `materialize_keypair`, `KeyFiles`)
- Test: `backend/tests/test_identity_config_models.py` (pure), `backend/tests/test_identity_store.py` (DB)

**Interfaces:**
- Consumes: Task 2 ORM; wave 0 `SECRET_MASK`, `SettingsSection`, `validate_section_schema`; `tiqora.crypto.secret.encrypt_secret/decrypt_secret`.
- Produces: D3, D4, D16 presets (`PRESETS: tuple[Preset, ...]`, `get_preset(key) -> Preset`, `@dataclass Preset(key, kind, name_key, button_icon, audience_default, config: dict[str, Any], attribute_map: AttributeMap, match_by: MatchBy)`), `generate_sp_keypair`, `materialize_keypair`.

- [ ] **Step 1: Failing tests.** Pure:
  ```python
  @pytest.mark.parametrize("model", [OidcProviderConfig, SamlProviderConfig, LdapProviderConfig])
  def test_config_models_fit_schema_form(model) -> None:
      validate_section_schema(SettingsSection(key="x", model=model, title_key="t", i18n_prefix="p"))

  def test_merge_config_secret_mask_keeps_value() -> None:
      cur = OidcProviderConfig(issuer="https://idp.example.org", client_secret=SecretStr("s1"))
      out = merge_config("oidc", cur, {"client_secret": SECRET_MASK, "scopes": "openid"})
      assert out.client_secret.get_secret_value() == "s1" and out.scopes == "openid"
      assert merge_config("oidc", cur, {"client_secret": ""}).client_secret is None

  def test_merge_config_ignores_read_only_sp_keypair() -> None:
      cur = SamlProviderConfig(sp_certificate="CERT", sp_private_key=SecretStr("KEY"))
      out = merge_config("saml", cur, {"sp_certificate": "EVIL", "sp_private_key": "EVILKEY"})
      assert out.sp_certificate == "CERT" and out.sp_private_key.get_secret_value() == "KEY"

  def test_every_preset_builds_its_config_model() -> None:
      for p in PRESETS:
          CONFIG_MODELS[p.kind].model_validate(p.config)

  def test_generate_sp_keypair_roundtrip(tmp_path) -> None:
      key_pem, cert_pem = generate_sp_keypair("idp-test.example.org", days=30)
      assert "BEGIN PRIVATE KEY" in key_pem and "BEGIN CERTIFICATE" in cert_pem
      kf = materialize_keypair(key_pem, cert_pem)
      assert oct(os.stat(kf.key_path).st_mode & 0o777) == "0o600"
      assert materialize_keypair(key_pem, cert_pem) == kf     # reused
  ```
  DB (`test_identity_store.py`, both dialects, slugs `idptest-*`): `create_provider(preset="generic_saml", …)` stores an SP keypair with the private key only in `secrets_enc` (raw row `config` JSON has no `sp_private_key`; `secrets_enc` decrypts to JSON containing it); `get_provider_by_slug` returns `config.sp_private_key` decrypted; `update_provider` with `config={"client_secret": SECRET_MASK}` leaves `secrets_enc` byte-identical; unknown role id in `role_rules` → `ProviderValidationError` with key `role_rules`; slug `env-oidc` or duplicate → `ProviderValidationError({"slug": …})`; `update_provider`/`delete_provider` on a row with `source="env"` → `ProviderReadOnly`; a row whose `secrets_enc` was encrypted with another key → record with `secrets_set == frozenset()` and no exception.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Store layout: `config` column = `json.dumps(model.model_dump(mode="json", exclude=secret_fields))`; `secrets_enc` = `encrypt_secret(secret_key, json.dumps({f: v.get_secret_value()}))` or NULL when no secret is set. `secret_fields(kind)` = fields typed `SecretStr | None`. Slug check uses `SLUG_RE`, `RESERVED_SLUGS` (except when `source="env"` is written by Task 4 through a private `_insert_env_row`). Role/group id validation loads `valid` and invalid ids from `roles` and `PermissionGroups` (IN-list, no joins). `generate_sp_keypair` with `cryptography` (RSA 3072, self-signed, `CN=<common_name>`, SHA-256, `not_valid_before = now - 1 day`). German validation messages: "Dieser Kurzname ist ungültig (nur a–z, 0–9 und Bindestrich, 2–63 Zeichen).", "Dieser Kurzname ist bereits vergeben.", "Unbekannte Rolle: {id}", "Unbekannte Gruppe: {id}".
- [ ] **Step 4: Run → PASS**; lint gates.
- [ ] **Step 5: Commit** `feat(identity): provider config models, presets and encrypted provider store`.

### Task 4: Env providers as read-only rows (backend-only)

**Files:**
- Create: `backend/src/tiqora/identity/env_providers.py` (D5)
- Modify: `backend/src/tiqora/api/app.py` (lifespan: after the compat mount, `try: async with app.state.session_factory() as s: await ensure_env_providers(s, settings)` `except Exception as exc: logger.warning("identity_env_sync_skipped", error=str(exc))`)
- Test: `backend/tests/test_identity_env_providers.py` (DB)

**Interfaces:**
- Consumes: Task 3 store.
- Produces: `sync_env_providers`, `ensure_env_providers`, `env_runtime_config`, `runtime_config`, `reset_env_sync_cache_for_tests()`.

- [ ] **Step 1: Failing tests** (both dialects; each test deletes `env-*` rows it created in a `finally` and calls `reset_env_sync_cache_for_tests()`):
  - `Settings(oidc_enabled=True, oidc_issuer="https://idp.example.org/realms/x", oidc_client_id="tiqora", oidc_client_secret="s", oidc_claim="email", oidc_redirect_uri="https://helpdesk.example.org/api/v1/auth/oidc/callback")` → one row `env-oidc`, `source="env"`, `enabled=True`, `audience="agent"`, `attribute_map.login == ["email"]`, `require_sso_opt_in=True`, `jit_enabled=False`, raw `config == "{}"`, `secrets_enc IS NULL`.
  - `runtime_config(record, settings)` returns an `OidcProviderConfig` with `client_secret == "s"` and `redirect_uri` = the env value.
  - Running `sync_env_providers` twice → still one row; flipping `oidc_enabled=False` → row stays, `enabled=False`.
  - `ldap_enabled=True` → `env-ldap` (agent); `customer_ldap_enabled=True` → `env-customer-ldap` (customer).
  - Race: call `sync_env_providers` concurrently from two sessions (`asyncio.gather`) → exactly one `env-oidc` row and no exception.
  - `ensure_env_providers` second call issues no SQL (patch `sync_env_providers` with a counter → called once).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** per D5 (insert via `store._insert_env_row`, catch `IntegrityError` → `await session.rollback()` → re-read). `ensure_env_providers` keeps a module-level `_SYNCED: bool` guarded by an `asyncio.Lock`.
- [ ] **Step 4: Run → PASS**; also `uv run python -m pytest tests/test_spnego.py tests/test_auth_me_is_admin.py -q` (apps booting without lifespan still work). Lint gates.
- [ ] **Step 5: Commit** `feat(identity): env OIDC/LDAP appear as read-only provider rows`.

### Task 5: Mapping, rule and assignment-diff engine (pure, backend-only)

**Files:**
- Create: `backend/src/tiqora/identity/mapping.py` (D6)
- Test: `backend/tests/test_identity_mapping.py`

**Interfaces:**
- Consumes: D3 models.
- Produces: D6 verbatim.

- [ ] **Step 1: Failing tests** (pure, no DB):
  ```python
  def test_normalize_attributes() -> None:
      raw = {"email": " a@example.org ", "groups": ["g1", "", 7], "flag": True, "nested": {"x": 1}, "none": None}
      assert normalize_attributes(raw) == {"email": ("a@example.org",), "groups": ("g1", "7"), "flag": ("True",)}

  def test_lookup_case_insensitive_only_when_unique() -> None:
      attrs = {"Mail": ("a@example.org",)}
      assert lookup(attrs, "mail") == ("a@example.org",)
      assert lookup({"Mail": ("a",), "MAIL": ("b",)}, "mail") == ()

  def test_map_profile_first_non_empty_and_literal() -> None:
      am = AttributeMap(login=["upn", "preferred_username"], email=["mail"], customer_id=["_Example GmbH"])
      p = map_profile({"preferred_username": ("max",), "mail": ("max@example.org",)}, am)
      assert (p.login, p.email, p.customer_id) == ("max", "max@example.org", "Example GmbH")

  def test_rules_union_and_valid_filter_and_rw_wins() -> None:
      rules = [RoleRule(attribute="groups", value="Support", role_ids=[3, 99]),
               RoleRule(attribute="groups", value="admins", groups=[GroupGrant(group_id=2, permissions=["ro", "rw"])])]
      d = evaluate_rules({"groups": ("support", "ADMINS")}, rules, valid_role_ids={3}, valid_group_ids={2})
      assert d.role_ids == {3} and d.group_perms == {(2, "rw")} and d.has_role_rules and d.has_group_rules

  def test_ldap_group_dn_compare_is_normalised() -> None:
      r = RoleRule(attribute="ldap:group_dn", value="CN=Support, OU=Groups,DC=example,DC=org", role_ids=[1])
      assert rule_matches({"ldap:group_dn": ("cn=support,ou=groups,dc=example,dc=org",)}, r)

  def test_managed_diff_keeps_manual_assignments() -> None:
      desired = DesiredAssignments(frozenset({1}), frozenset(), True, False)
      state = AssignmentState(current_roles=frozenset({1, 2, 5}), current_group_perms=frozenset(),
                              managed_roles=frozenset({5}), managed_group_perms=frozenset())
      d = diff_assignments(desired, state, mode="managed")
      assert d.add_roles == frozenset() and d.remove_roles == {5} and d.record_managed_roles == frozenset()
      assert d.drop_managed_roles == {5}          # role 2 (manual) untouched; role 1 already present manually → not recorded

  def test_managed_diff_adds_and_records() -> None:
      d = diff_assignments(DesiredAssignments(frozenset({1}), frozenset(), True, False),
                           AssignmentState(frozenset(), frozenset(), frozenset(), frozenset()), mode="managed")
      assert d.add_roles == {1} and d.record_managed_roles == {1}

  def test_managed_record_dropped_when_row_vanished() -> None:
      d = diff_assignments(DesiredAssignments(frozenset(), frozenset(), True, False),
                           AssignmentState(frozenset(), frozenset(), frozenset({4}), frozenset()), mode="managed")
      assert d.remove_roles == frozenset() and d.drop_managed_roles == {4}

  def test_authoritative_replaces_kind_with_rules_only() -> None:
      desired = DesiredAssignments(frozenset({1}), frozenset(), True, False)   # no group rules
      state = AssignmentState(frozenset({1, 2}), frozenset({(9, "rw")}), frozenset(), frozenset())
      d = diff_assignments(desired, state, mode="authoritative")
      assert d.remove_roles == {2} and d.remove_groups == frozenset() and d.record_managed_roles == {1}

  def test_no_rules_no_changes() -> None:
      d = diff_assignments(DesiredAssignments(frozenset(), frozenset(), False, False),
                           AssignmentState(frozenset({1}), frozenset({(2, "ro")}), frozenset({1}), frozenset()), mode="managed")
      assert d.empty
  ```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** per D6 tables (keep functions total: never raise on odd input).
- [ ] **Step 4: Run → PASS**; lint gates.
- [ ] **Step 5: Commit** `feat(identity): attribute mapping, role rules and managed-assignment diff`.

### Task 6: Identity links and login planning (match + link; backend-only)

**Files:**
- Create: `backend/src/tiqora/identity/links.py`, `backend/src/tiqora/identity/login.py` (D7 — phase-1 subset: steps 1–4 and 6; step 5 always answers `no_match` until Task 17 adds JIT; steps 7–8 return empty changes until Tasks 17/18)
- Create: `backend/tests/_identity_helpers.py` (`FakeRedis`, `seed_agent`, `seed_customer`, `make_provider`, `cleanup_statements`)
- Test: `backend/tests/test_identity_login_plan.py` (DB, band 47000–47099)

**Interfaces:**
- Consumes: Tasks 3–5; `TiqoraUserAuthConfig`; `Users`, `UserPreferences`, `CustomerUser`.
- Produces: D7 verbatim (`ExternalIdentity`, `SsoSessionDraft`, `LoginPlan`, `RejectCode`, `REJECT_MESSAGES`, `plan_login`, `apply_login`, link functions).
  `FakeRedis` supports `get, set(key, value, ex=None, nx=False), delete(*keys), getdel, expire, sadd, srem, smembers, exists, ttl` (store-only TTL bookkeeping).
  `make_provider(session, *, slug, kind="oidc", audience="agent", match_by="login", attribute_map=None, jit_enabled=False, require_sso_opt_in=False, enabled=True, settings) -> ProviderRecord` (creates via `store.create_provider` with the matching `generic_*` preset then `update_provider`).

- [ ] **Step 1: Failing tests** (both dialects):
  - `test_match_by_login_then_link_created`: agent `idp.anna.47001` exists; identity subject `sub-1`, attributes `{"preferred_username": ("idp.anna.47001",)}` → plan `decision="login"`, `matched_by="login"`; `apply_login` returns `(47001, "idp.anna.47001")` and a link row exists.
  - `test_link_wins_over_claim`: link `sub-1 → 47001`; second login with `preferred_username = "idp.bert.47002"` (another existing agent) → `matched_by="link"`, `user_id=47001`.
  - `test_renamed_login_still_logs_in`: rename `users.login` of 47001 → plan via link still `login`, `apply_login` updates `user_login` snapshot.
  - `test_link_to_invalid_user_rejects_without_fallback`: set 47001 `valid_id=2`; claim points to valid 47002 → `reject_code="user_invalid"`.
  - `test_exact_login_before_case_insensitive`: agents `Idp.Case.47003` and `idp.case.47004`; claim `idp.case.47004` → user 47004; claim `IDP.CASE.47005-unknown` → `no_match`.
  - `test_match_by_email_agent`: `UserEmail` pref `Mia@Example.org` stored for 47006; identity email `mia@example.org`, `email_verified=True` → user 47006; with `email_verified=False` and `email_must_be_verified` → `email_unverified`; two agents with the same email → `ambiguous_email`.
  - `test_match_by_email_customer`: customer `idp.kunde.47007@example.org` matched case-insensitively; `user_kind="customer"`.
  - `test_sso_opt_in_gate`: provider `require_sso_opt_in=True`, agent without `tiqora_user_auth_config` → `sso_not_enabled`; after `AuthConfigService.set(uid, sso_eligible=True)` → `login`; the gate is ignored for `kind="ldap"`.
  - `test_transient_name_id_never_links`: SAML provider with `name_id_format="transient"` → `apply_login` stores no link.
  - `test_empty_subject_rejects`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `plan_login` loads only by ids/logins (no cross-table string JOINs). Email lookup per D7 step 4 (two byte variants, Python-side compare). `apply_login`: for `decision="login"` → `upsert_link` (skip when `kind == "saml"` and the provider config `name_id_format == "transient"`), `await session.commit()`.
- [ ] **Step 4: Run → PASS**; lint gates.
- [ ] **Step 5: Commit** `feat(identity): stable subject links and login matching`.

### Task 7: SSO session info, index and revocation (backend-only)

**Files:**
- Create: `backend/src/tiqora/identity/sso_sessions.py` (D8)
- Modify: `backend/src/tiqora/domain/auth.py` (`SessionStore.create(..., sso=None)`, `get_sso`, `delete` drops sso; `AuthService.create_session(..., sso=None)`), `backend/src/tiqora/domain/customer_auth.py` (`CustomerSessionStore.create(..., sso=None)`, `get_sso`, user index, `revoke_customer_sessions`; `CustomerAuthService.create_session(customer, *, sso=None)`)
- Test: `backend/tests/test_identity_sso_sessions.py` (pure, `FakeRedis`), extend `backend/tests/test_session_revocation.py` with one customer case

**Interfaces:**
- Consumes: Task 6 `FakeRedis`.
- Produces: D8 verbatim; `CustomerSessionStore.revoke_customer_sessions(customer_id: int, *, keep_token: str | None = None) -> int`.

- [ ] **Step 1: Failing tests:**
  - store two agent sessions for provider 7 with `subject_hash="h1"` (session indexes `si-a`, `si-b`) and one customer session `h1` → `tokens_for(redis, "a", 7, subject_hash="h1")` has 2 tokens; with `session_indexes=["si-a"]` → 1; `revoke_sso_sessions(redis, settings, provider_id=7, subject_hash="h1")` → 3 and all three `SessionStore.get`/`CustomerSessionStore.get` return None.
  - `sid` index: `tokens_for(..., sid="abc")` finds only that token.
  - `SessionStore.delete(token)` removes `tiqora:session:sso:{token}`.
  - `SsoSessionInfo.from_json("garbage") is None`.
  - `CustomerSessionStore.revoke_customer_sessions(5)` removes all sessions of customer 5 and none of customer 6.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** per D8 (index sets `expire` to the absolute TTL; `suppress(Exception)` around index writes like `SessionStore.create`).
- [ ] **Step 4: Run → PASS** (`tests/test_identity_sso_sessions.py tests/test_session_revocation.py tests/test_portal_auth.py`); lint gates.
- [ ] **Step 5: Commit** `feat(identity): SSO session index for single logout`.

### Task 8: Several OIDC providers, generic SSO routes, env aliases (backend-only)

**Files:**
- Modify: `backend/src/tiqora/domain/oidc.py` (D11), `backend/tests/test_oidc.py` (constructor)
- Create: `backend/src/tiqora/identity/urls.py` (D9), `backend/src/tiqora/identity/flow_state.py` (D10; OIDC + test-run parts), `backend/src/tiqora/identity/http.py`, `backend/src/tiqora/identity/sso_routes.py`, `backend/src/tiqora/api/v1/auth_sso.py`
- Modify: `backend/src/tiqora/api/v1/auth.py` (`/methods` adds `providers`, `saml`; `/oidc/login` + `/oidc/callback` delegate to `sso_routes`; `_safe_next` moves to `identity/http.py::safe_next` and is re-imported), `backend/src/tiqora/api/v1/__init__.py` (include `auth_sso.router` after `auth.router`), `backend/src/tiqora/domain/schemas.py` (`LoginProviderOut`, `AuthMethodsOut` fields per D15)
- Regenerate openapi files.
- Test: `backend/tests/test_identity_oidc_flow.py` (ASGI + DB, band 47900–47999)

**Interfaces:**
- Consumes: Tasks 3–7.
- Produces:
  ```python
  # identity/http.py
  def safe_next(next_path: str | None, *, default: str = "/") -> str
  def safe_portal_next(next_path: str | None) -> str            # must start with "/portal", else "/portal"
  def login_error_redirect(user_kind: UserKind, code: str, next_path: str) -> RedirectResponse
      # agent: /login?sso_error=<code>&next=<quoted>; customer: /portal/login?sso_error=<code>&next=<quoted>
  def login_landing_response(target: str) -> HTMLResponse
  def set_session_cookie(response, settings, token: str, *, user_kind: UserKind) -> None
  # identity/sso_routes.py
  async def start_sso_login(request, *, slug: str, user_kind: UserKind, next_path: str | None, test_id: str | None,
                            session, settings, current_admin_id: int | None) -> Response
  async def finish_oidc_callback(request, *, slug: str, user_kind: UserKind, code: str | None, state: str | None,
                                 session, settings, json_response: bool = False) -> Response
  async def complete_login(request, *, record: ProviderRecord, identity: ExternalIdentity, user_kind: UserKind,
                           next_path: str, test_id: str | None, session, settings,
                           avatar_url: str | None = None, landing_html: bool = False,
                           json_response: bool = False) -> Response
      # test_id -> plan only, store TestRun result, 302 /admin/identity-providers/{id}?test=<id>
      # reject -> login_error_redirect(code); ok -> apply_login, create session with SsoSessionInfo
      #   (AuthService.create_session / CustomerAuthService.create_session), set cookie, redirect/landing/JSON
  ```

- [ ] **Step 1: Failing tests.** Fake IdP via `httpx.MockTransport` serving discovery, token and userinfo (pattern from `test_oidc.py`); monkeypatch `pin_outbound_url` resolver like the existing OIDC tests; `app.state.redis = FakeRedis()`; `Settings(environment="test", public_base_url="https://helpdesk.example.org", …)`; override `get_db`.
  - `test_env_oidc_unchanged_login_via_alias`: env settings as Task 4; agent `idp.env.47901` with `sso_eligible`; `GET /api/v1/auth/oidc/login` → 302 to the IdP with `state`, cookie `tiqora_oidc_state`; `GET /api/v1/auth/oidc/callback?code=c&state=<s>` with `Accept: application/json` → 200 JSON `LoginResponse.user.login == "idp.env.47901"`, `tiqora_session` cookie set; same flow with `Accept: text/html` → 302 to `/`.
  - `test_env_oidc_unchanged_rejections`: unknown claim value → 403 JSON on the alias with Accept json (as today: detail mentions "No local user matches SSO identity"), agent without `sso_eligible` → 403 "SSO not enabled for this account"; with Accept html → 302 `/login?sso_error=no_match` / `sso_not_enabled`.
  - `test_callback_accepts_legacy_state_value`: put `tiqora:oidc:state:<s>` = `"1"` directly + cookie → callback logs in through env-oidc.
  - `test_db_oidc_provider_login_redirects_to_next`: provider `idptest-kc` (generic_oidc, enabled) → `GET /api/v1/auth/sso/idptest-kc/login?next=/tickets/5` → IdP redirect whose `redirect_uri` is `https://helpdesk.example.org/api/v1/auth/oidc/idptest-kc/callback`; callback → 302 `/tickets/5`; `SessionStore.get_sso(token).slug == "idptest-kc"`.
  - `test_state_bound_to_provider`: state minted for provider A presented at `/oidc/<B>/callback` → 302 `/login?sso_error=state`.
  - `test_next_is_sanitised`: `next=//evil.example.com` → final redirect `/`.
  - `test_disabled_provider_404_without_test`: disabled provider → `/sso/<slug>/login` 302 `/login?sso_error=provider_disabled`.
  - `test_methods_lists_enabled_agent_sso_providers`: env-oidc (sort 0) + `idptest-kc` enabled (agent, default sort 100) + a customer-only provider → `providers` slugs == `["env-oidc", "idptest-kc"]` (order: sort, id), env-oidc `label == ""`, `oidc` true (legacy), customer-only absent.
  - `test_private_network_issuer_blocked_unless_allowed`: issuer resolving to `10.0.0.5` → `/sso/<slug>/login` 302 `sso_error=idp_error`; with `allow_private_network=True` → 302 to the IdP.
  - `test_avatar_picture_kept`: userinfo `picture=https://cdn.example.org/a.png` → `SessionStore.get_avatar_url` returns it.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `start_sso_login`: `ensure_env_providers`; load provider by slug (404 → `unknown_provider`); audience check; disabled → only with a valid test whose `admin_id == current_admin_id` (agents: resolve the agent session cookie via `AuthService.resolve_session`; portal tests: the admin's agent cookie is also sent, resolve it the same way); OIDC: `put_oidc_state` with nonce + verifier (`use_pkce()`), set `tiqora_oidc_state` cookie exactly as today (`SameSite=lax`, 300 s); SAML: Task 10. `finish_oidc_callback`: cookie/state check as today, `pop_oidc_state` (legacy → env-oidc), provider id must match the slug's provider, `fetch_tokens_and_claims`, `ExternalIdentity(subject=str(claims["sub"]) (env-oidc: if "sub" missing use the login claim value), attributes=normalize_attributes(claims) | {"oidc:sub": (sub,)}, email_verified=claims.get("email_verified") if bool, sso=SsoSessionDraft(sid=None, id_token_hint=result.id_token))`, then `complete_login`. Map `OIDCError`/`httpx.HTTPError` → `oidc_failed`, `OutboundURLError` → `idp_error`. JSON mode (alias + `Accept` not html) keeps today's HTTP codes and detail strings: 401 `"OIDC claim '<claim>' missing from userinfo response"`, 403 `"No local user matches SSO identity '<value>'"`, 403 `"SSO not enabled for this account"`, 400 `"Invalid or expired state"`. SSO skips 2FA (as today). `/methods` builds `providers` from `list_enabled(kind in SSO_KINDS, user_kind="agent")` (SAML only when `saml_availability().available`).
- [ ] **Step 4: Run → PASS** (`tests/test_identity_oidc_flow.py tests/test_oidc.py tests/test_auth_hardening.py`); regenerate openapi; lint gates.
- [ ] **Step 5: Commit** `feat(identity): several OIDC providers with stable links; env OIDC keeps its URLs`.

### Task 9: SAML service-provider core, in-process test IdP, malicious-response corpus (backend-only)

**Files:**
- Create: `backend/src/tiqora/identity/saml_metadata.py` (D12 parse + fetch), `backend/src/tiqora/identity/saml_sp.py` (D12 SP wrapper)
- Modify: `backend/tests/_saml_idp.py` (add `SamlTestIdp`)
- Test: `backend/tests/test_identity_saml_metadata.py` (pure), `backend/tests/test_identity_saml_sp.py` (pure, `pytestmark = [pytest.mark.saml]`, `require_saml()` at module top)

**Interfaces:**
- Consumes: Task 1 runtime, Task 3 keys/store records (tests build `ProviderRecord` directly via a helper `saml_record(...)` in `_saml_idp.py`), D8 `SsoSessionInfo`.
- Produces: D12 verbatim; test helper:
  ```python
  IDP_ENTITY_ID = "https://idp.example.org/saml"
  class SamlTestIdp:
      def __init__(self, tmp_path: Path, *, entity_id: str = IDP_ENTITY_ID) -> None   # fresh RSA key + cert (cryptography)
      def metadata_xml(self) -> str                       # saml2.metadata.create_metadata_string(None, config=…)
      def trust_sp(self, sp_metadata_xml: str) -> None    # rebuilds saml2.server.Server with the SP metadata inline
      def parse_login_redirect(self, url: str) -> tuple[str, str, str]   # (request_id, acs_url, relay_state)
      def login_response(self, *, in_response_to: str | None, acs_url: str, sp_entity_id: str,
                         name_id: str = "u-1001", name_id_format: str = NAMEID_FORMAT_PERSISTENT,
                         attributes: dict[str, list[str]] | None = None, sign_assertion: bool = True,
                         sign_response: bool = False, encrypt_for_cert: str | None = None) -> str   # base64
      def logout_request_redirect(self, *, sp_slo_url: str, name_id: str, session_index: str) -> str      # URL
      def logout_response_redirect(self, *, sp_slo_url: str, in_response_to: str) -> str                 # URL
  def saml_record(*, slug: str, idp: SamlTestIdp, settings: Settings, **cfg: Any) -> ProviderRecord
      # SamlProviderConfig(idp_metadata_xml=idp.metadata_xml(), sp keypair via generate_sp_keypair, **cfg)
  ```

- [ ] **Step 1: Failing tests.** Metadata (pure, no xmlsec needed): `parse_idp_metadata(idp_xml)` returns the entity id, ≥1 signing cert and the SSO redirect URL; aggregate with two `EntityDescriptor`s and no `entity_id` → `SamlMetadataError`; `<!DOCTYPE …>` → `SamlMetadataError`; `validUntil` in the past → error; `fetch_idp_metadata("http://…")` → error (https only); `fetch_idp_metadata` against a `MockTransport` returning 6 MB → error; resolver returning `127.0.0.1` → error.
  SP (`test_identity_saml_sp.py`), fixture `idp` + `sp = SamlServiceProvider(saml_record(slug="idptest-saml", idp=idp, settings=S), settings=S)`, `idp.trust_sp(sp.sp_metadata_xml())`:
  ```python
  async def test_round_trip_agent(sp, idp) -> None:
      redir = await sp.login_redirect(relay_state="rs1", user_kind="agent")
      req_id, acs, _ = idp.parse_login_redirect(redir.url)
      assert acs == "https://helpdesk.example.org/api/v1/auth/saml/idptest-saml/acs"
      b64 = idp.login_response(in_response_to=req_id, acs_url=acs, sp_entity_id=SP_ENTITY,
                               attributes={"mail": ["anna@example.org"], "groups": ["Support"]})
      res = await sp.parse_login_response(b64, outstanding={req_id: acs}, expected_acs=acs)
      assert res.name_id == "u-1001" and res.attributes["mail"] == ("anna@example.org",)
      assert res.attributes["saml:name_id"] == ("u-1001",) and res.session_index
  ```
  Corpus — each must raise `SamlResponseInvalid` (build from a fresh good response `good = idp.login_response(...)`; mutate the base64-decoded XML **as a string** with `re`/`str.replace`, never re-serialise through ElementTree):
  1. `test_unsigned_assertion_rejected` (`sign_assertion=False`), 2. `test_signed_response_unsigned_assertion_rejected` (`sign_response=True, sign_assertion=False`), 3. `test_tampered_attribute_rejected` (replace `anna@example.org` by `boss@example.org`), 4. `test_xsw_evil_assertion_before_signed_rejected` (copy the signed `<saml:Assertion …>…</saml:Assertion>` substring, change its `ID` and NameID, strip its `<ds:Signature>`, insert before the original), 5. `test_xsw_signed_assertion_wrapped_in_extensions_rejected` (move signed assertion into `<samlp:Extensions>` and put the evil one in its place), 6. `test_doctype_rejected` (prepend `<!DOCTYPE r [<!ENTITY x "y">]>`), 7. `test_wrong_audience_rejected` (`sp_entity_id="https://other.example.org/sp"`), 8. `test_expired_rejected` (monkeypatch `saml2.time_util.utc_now` to now + 3600), 9. `test_unknown_in_response_to_rejected` (`outstanding={}` with `allow_idp_initiated=False`), 10. `test_customer_acs_response_posted_to_agent_acs_rejected` (destination = customer ACS, `expected_acs` = agent ACS), 11. `test_signature_by_unknown_key_rejected` (a second `SamlTestIdp` with the same entity id signs), 12. `test_more_than_one_assertion_rejected`.
  Plus: `test_encrypted_assertion_accepted` (`encrypt_for_cert=sp cert`), `test_idp_initiated_accepted_only_when_enabled` (`in_response_to=None`, `outstanding={}`), `test_sp_metadata_lists_both_acs_and_slo` (parse `sp_metadata_xml()` with defusedxml), `test_logout_redirect_none_without_idp_slo`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `SamlServiceProvider`: build the D12 config dict in a helper `_sp_conf(record, settings, availability, key_files)`; `SPConfig().load(conf)`; `Saml2Client(config=conf)`; every pysaml2 call via `run_in_executor`. `parse_login_response`: size limit 2 MB, `defusedxml.ElementTree.fromstring(xml, forbid_dtd=True)`, count `{urn:oasis:names:tc:SAML:2.0:assertion}Assertion` + `EncryptedAssertion` anywhere == 1 else `SamlResponseInvalid("assertion_count")`; then `client.parse_authn_request_response(b64, BINDING_HTTP_POST, outstanding=dict(outstanding))`; `None` result or any exception → `SamlResponseInvalid(type(exc).__name__)`; require `authn_response.assertion` and `authn_response.assertion.signature` **or** (`cfg.want_response_signed` and response signature) with the assertion signed — practically: `want_assertions_signed=True` makes pysaml2 enforce it; assert it again defensively (`getattr(resp.assertion, "signature", None)` non-empty; for encrypted assertions check after decryption). Read `name_id` from `resp.name_id`, `session_index` from `resp.assertion.authn_statement[0].session_index`, `not_on_or_after` from `resp.assertion.conditions.not_on_or_after`, attributes from `resp.ava`. Destination check per D12. Logout helpers wrap `create_logout_request` + `apply_binding(BINDING_HTTP_REDIRECT, …, sign=True)`; when the IdP metadata has no `SingleLogoutService` → `None`.
- [ ] **Step 4: Run → PASS** with `xmlsec1` installed (`brew install libxmlsec1`); confirm the module skips cleanly when `PATH` hides xmlsec1 (`PATH=/usr/bin:/bin uv run python -m pytest tests/test_identity_saml_sp.py -q` → skipped) and fails under `TIQORA_REQUIRE_SAML_TESTS=1` with that PATH. Lint gates.
- [ ] **Step 5: Commit** `feat(identity): SAML service provider with signature, replay and wrapping checks`.

### Task 10: SAML login for agents — start, ACS, SP metadata, CSRF exemption (backend-only)

**Files:**
- Modify: `backend/src/tiqora/identity/flow_state.py` (SAML request + assertion replay parts of D10), `backend/src/tiqora/identity/sso_routes.py` (`start_sso_login` SAML branch, `finish_saml_acs`, `saml_metadata_response`), `backend/src/tiqora/api/v1/auth_sso.py` (ACS + metadata routes), `backend/src/tiqora/security/csrf.py` (D15 exemptions)
- Regenerate openapi files.
- Test: `backend/tests/test_identity_saml_flow.py` (ASGI + DB, saml marker, band 47100–47199), extend `backend/tests/test_auth_hardening.py` or a new `backend/tests/test_csrf_identity_exempt.py` (pure)

**Interfaces:**
- Consumes: Tasks 6–9.
- Produces:
  ```python
  async def finish_saml_acs(request, *, slug: str, user_kind: UserKind, saml_response: str, relay_state: str | None,
                            session, settings) -> Response
  def saml_metadata_response(record: ProviderRecord, settings) -> Response
  SAML_REQUEST_COOKIE: Final = "tiqora_saml_req"
  ```

- [ ] **Step 1: Failing tests.**
  - `test_saml_agent_login_round_trip`: provider `idptest-saml-a` enabled for agents with the test IdP; agent `idp.saml.47101` (`sso_eligible`); `GET /api/v1/auth/sso/idptest-saml-a/login?next=/tickets` → 302 to the IdP SSO URL, cookie `tiqora_saml_req`; IdP response → `POST /api/v1/auth/saml/idptest-saml-a/acs` (form `SAMLResponse`, `RelayState`, header `Origin: https://idp.example.org`, cookie jar containing BOTH `tiqora_session=x` and `tiqora_customer_session=y` to prove the CSRF exemption) → 200 HTML containing `url=/tickets`, `Set-Cookie: tiqora_session=…`; `SessionStore.get_sso(token)` has `session_index` and `name_id`.
  - `test_saml_replay_rejected`: posting the same `SAMLResponse` twice → second is 302 `/login?sso_error=saml_invalid` (request cache popped AND assertion id seen).
  - `test_saml_cookie_binding_enforced_when_secure`: `session_cookie_secure=True`, ACS without the `tiqora_saml_req` cookie → `sso_error=state`; with `session_cookie_secure=False` → login succeeds (binding skipped, documented).
  - `test_saml_invalid_response_lands_on_login`: unsigned assertion → 302 `/login?sso_error=saml_invalid&next=…` and no session cookie.
  - `test_saml_unavailable`: monkeypatch `saml_availability` → `missing="xmlsec1"` → login start 302 `sso_error=saml_unavailable`; `/api/v1/auth/methods` → `saml: false` and no SAML providers listed.
  - `test_sp_metadata_endpoint`: `GET /api/v1/auth/saml/idptest-saml-a/metadata` → 200, `content-type` `application/samlmetadata+xml`, body contains both ACS URLs and the SP certificate; unknown slug → 404; LDAP slug → 404.
  - `test_base_url_missing`: `public_base_url=""` and no CORS origins → login start 302 `sso_error=config`.
  - CSRF pure: with both session cookies present and a foreign `Origin`, `csrf_check_required` is false for `POST /api/v1/auth/saml/x/acs`, `/api/v1/auth/saml/x/sls`, `/api/portal/auth/saml/x/acs`; still true for `POST /api/v1/auth/saml/x/acs/extra`, `POST /api/v1/auth/oidc/x/backchannel-logout` and `POST /api/v1/auth/logout` with a cookie; false for the back-channel route WITHOUT cookies (server-to-server call, needs no exemption).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Login start SAML branch: `SamlServiceProvider.login_redirect(relay_state=<opaque random>, user_kind)`; `put_saml_request(request_id, LoginState(...))`; RelayState is the opaque token, NOT the next path (next lives in Redis). ACS: form parsing via `await request.form()` with `max_part_size` default; `RelayState` used only for IdP-initiated (`safe_next`); look up `InResponseTo` by parsing with `outstanding` built from the cookie value / or, when binding is off, from a peek of `InResponseTo` in the XML (defusedxml) → `pop_saml_request`; state's provider must equal the slug's provider and `user_kind` must equal the route's audience; `mark_assertion_seen(provider_id, assertion_id, ttl)` False → `saml_invalid`; build `ExternalIdentity(subject=name_id, attributes=res.attributes, sso=SsoSessionDraft(session_index, name_id, name_id_format, name_qualifier, sp_name_qualifier))`; `complete_login(..., landing_html=True)`. Delete the `tiqora_saml_req` cookie on every ACS response.
- [ ] **Step 4: Run → PASS**; regenerate openapi; lint gates.
- [ ] **Step 5: Commit** `feat(identity): SAML login for agents (ACS, SP metadata, replay protection)`.

### Task 11: SP-initiated single logout (SAML + OIDC) (backend-only)

**Files:**
- Modify: `backend/src/tiqora/identity/sso_routes.py` (`sso_logout`, `handle_sls`), `backend/src/tiqora/api/v1/auth_sso.py` (`POST /auth/sso/logout`, `GET|POST /auth/saml/{slug}/sls`), `backend/src/tiqora/api/v1/auth.py` (`_user_me` sets `sso_logout`), `backend/src/tiqora/domain/schemas.py` (`UserMe.sso_logout`, `CustomerMe.sso_logout`)
- Regenerate openapi files.
- Test: `backend/tests/test_identity_logout.py` (ASGI; SAML parts with saml marker)

**Interfaces:**
- Consumes: Tasks 7–10.
- Produces:
  ```python
  async def sso_logout(request, *, user_kind: UserKind, session, settings) -> Response
      # reads the session cookie of that audience; no session -> 303 login page;
      # deletes the local session (+ sso key) and clears the cookie FIRST, then:
      # SAML with IdP SLO -> put_logout_request + 303 SamlServiceProvider.logout_redirect(...)
      # OIDC with end_session_endpoint -> 303 {end_session}?id_token_hint=…&post_logout_redirect_uri=<post logout url>&client_id=…
      # otherwise -> 303 /login?logged_out=1 (portal: /portal/login?logged_out=1)
  async def handle_sls(request, *, slug: str, session, settings) -> Response
      # SAMLResponse -> parse_logout_response, pop_logout_request(in_response_to) -> 303 matching login page ?logged_out=1
      # (unknown/invalid -> still 303 login page ?logged_out=1: the local session is gone anyway)
      # SAMLRequest -> Task 21 (until then: 400 "IdP-initiated logout not supported yet")
  def sso_logout_available(info: SsoSessionInfo | None, record: ProviderRecord | None) -> bool
  ```

- [ ] **Step 1: Failing tests:**
  - `test_logout_without_sso_session_goes_to_login`: password session → `POST /api/v1/auth/sso/logout` (Origin same host) → 303 `/login?logged_out=1`, session gone.
  - `test_saml_sp_initiated_logout`: SAML session from Task 10 flow → 303 to the IdP SLO URL carrying a signed `SAMLRequest` (IdP helper parses it: NameID and SessionIndex match); IdP `logout_response_redirect` → `GET /api/v1/auth/saml/<slug>/sls?SAMLResponse=…` → 303 `/login?logged_out=1`.
  - `test_oidc_end_session`: discovery has `end_session_endpoint` → 303 URL contains `id_token_hint` and `post_logout_redirect_uri=https%3A%2F%2Fhelpdesk.example.org%2Flogin%3Flogged_out%3D1`.
  - `test_me_reports_sso_logout`: `/api/v1/auth/me` → `sso_logout: true` for the SAML session, `false` for a password session.
  - `test_existing_logout_endpoint_unchanged`: `POST /api/v1/auth/logout` still 204.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** per Interfaces. The logout POST is a normal form post from the SPA (same origin, CSRF passes). `post_logout_redirect_uri` uses `ProviderUrls.agent_post_logout_url`/`customer_post_logout_url`.
- [ ] **Step 4: Run → PASS**; regenerate openapi; lint gates.
- [ ] **Step 5: Commit** `feat(identity): single logout started from Tiqora (SAML, OIDC)`.

### Task 12: SAML metadata fetch on save, daily refresh (backend-only)

**Files:**
- Create: `backend/src/tiqora/identity/metadata_refresh.py` (D18)
- Modify: `backend/src/tiqora/domain/settings_store.py` (`KEY_IDENTITY_METADATA_REFRESH_ENABLED`), `backend/src/tiqora/worker/services.py` (catalog entry), `backend/src/tiqora/worker/__main__.py` (`_daily_loop`), `frontend/src/i18n/locales/en.json` + `de.json` (`admin.daemons.services.identity_metadata.*` only — the Dienste page reads the catalog; propagate)
- Test: `backend/tests/test_identity_metadata_refresh.py` (DB), existing daemon catalog tests (`grep -rn "DAEMON_SERVICES" backend/tests`) updated for the new slug

**Interfaces:**
- Consumes: Tasks 3, 9.
- Produces: `async def refresh_provider_metadata(session, record: ProviderRecord, *, settings, transport=None) -> ProviderRecord` (fetch + `parse_idp_metadata` + store; raises `SamlMetadataError`), `async def run_identity_metadata_refresh_tick(*, settings: Settings | None = None, transport=None) -> dict[str, int]`.

- [ ] **Step 1: Failing tests:** `MockTransport` serving the test IdP metadata (generate with `SamlTestIdp` when saml is available, otherwise a static minimal metadata string built in the test with a dummy base64 cert — parsing does not need xmlsec1); provider with `idp_metadata_url` → tick returns `{"refreshed": 1, …}`, row has `metadata_xml`, `metadata_fetched_at`; second provider whose URL returns 500 keeps its old XML and gets `metadata_error` "Die Metadaten konnten nicht geladen werden (HTTP 500)."; provider with uploaded `idp_metadata_xml` → `skipped`; setting `daemon.identity_metadata.enabled=0` → `{"refreshed":0,"failed":0,"skipped":0}`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**; catalog/test updates; i18n keys + propagate.
- [ ] **Step 4: Run → PASS**; `node frontend/scripts/check-i18n-keys.mjs`; lint gates.
- [ ] **Step 5: Commit** `feat(identity): daily refresh of SAML IdP metadata`.

### Task 13: Admin API for identity providers incl. test login and preview (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/v1/admin/identity_schemas.py`, `backend/src/tiqora/api/v1/admin/identity_providers.py` (D16)
- Modify: `backend/src/tiqora/api/v1/admin/__init__.py` (include router), `backend/src/tiqora/identity/sso_routes.py` (test-run result storage already used by `complete_login`); `plan_to_out(session, plan_dict: Mapping[str, Any]) -> LoginPlanOut` lives in `identity_providers.py` and resolves role/group names by IN-list; `preview` = `plan_login` → `plan_to_dict` → `plan_to_out`
- Regenerate openapi files.
- Test: `backend/tests/test_admin_identity_providers_api.py` (ASGI + DB, band 47200–47299)

**Interfaces:**
- Consumes: Tasks 3–12.
- Produces: D16 routes and schemas verbatim.

- [ ] **Step 1: Failing tests** (admin via `get_current_user` override + seeded admin group membership like `test_auth_me_is_admin.py`; a non-admin gets 403 on every route):
  - create from preset `generic_oidc` → 201, `enabled false`, `endpoints.agent_redirect_uri == "https://helpdesk.example.org/api/v1/auth/oidc/<slug>/callback"`; from `generic_saml` → `config.sp_certificate` non-empty, `config.sp_private_key == "********"`, `secrets_set == ["sp_private_key"]`.
  - `GET /kinds` → three kinds; each `json_schema` has `properties`; SAML `read_only_fields == ["sp_certificate", "sp_private_key"]` and `hidden_fields == ["sp_private_key"]`.
  - `GET /capabilities` reflects a monkeypatched `saml_availability`.
  - PATCH config with `client_secret: "********"` keeps the secret; PATCH with an unknown role id → 422 `{"message": "Bitte die markierten Felder prüfen.", "errors": {"role_rules": "Unbekannte Rolle: 999999"}}`; PATCH/DELETE env row → 409 `{"message": "Dieser Anmeldedienst kommt aus den Umgebungsvariablen und kann hier nicht geändert werden."}`.
  - `GET /` lists env rows with `read_only: true` and `link_count`.
  - `POST /{id}/refresh-metadata` with a failing URL → 422 with the German message.
  - Test login (OIDC fake IdP from Task 8): `POST /{id}/tests {"audience":"agent","draft":{"config":{"scopes":"openid email"}}}` → `login_url`; `GET login_url` with the admin's session cookie on a DISABLED provider → 302 to the IdP with `scope=openid+email`; callback → 302 `/admin/identity-providers/<id>?test=<tid>`; `GET /{id}/tests/<tid>` → `status: "done"`, `attributes.preferred_username`, `plan.decision`; no `tiqora_identity_link` row, no session cookie set. Another admin's `GET /tests/<tid>` → 404. `GET login_url` without the admin cookie → 302 `/login?sso_error=provider_disabled`.
  - `POST /{id}/preview {"attributes": {"preferred_username": ["idp.adm.47201"]}}` → `decision "login"`, `matched_by "login"`.
  - Links: after a real login, `GET /{id}/links` returns one; `DELETE /{id}/links/{lid}` → 204.
  - `GET /{id}/managed-assignments` → `[]`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Static routes before `/{provider_id}`. Responses mask secrets with `SECRET_MASK`. `ProviderValidationError` → 422 `{"message", "errors"}`; `ProviderReadOnly` → 409. Env rows show `config` = `runtime_config(record, settings)` dumped with secrets masked. `kinds` uses `CONFIG_MODELS[k].model_json_schema()`. The test-run admin check reuses `AuthService.resolve_session` on the agent cookie inside `start_sso_login` (Task 8 hook). Commit after write operations.
- [ ] **Step 4: Run → PASS**; regenerate openapi; lint gates.
- [ ] **Step 5: Commit** `feat(identity): admin API for identity providers with test login`.

### Task 14: Agent login page — provider buttons, SSO errors, SSO logout (frontend-only)

**Files:**
- Create: `frontend/src/lib/identityApi.ts` (public helpers `agentSsoLoginUrl`, `portalSsoLoginUrl`, types; admin `identityApi` object may be added here already or in Task 15), `frontend/src/lib/ssoLogout.ts`, `frontend/src/lib/ssoErrors.ts`, `frontend/src/components/auth/SsoProviderButtons.tsx`, `frontend/src/components/auth/ProviderIcon.tsx`
- Modify: `frontend/src/routes/LoginPage.tsx`, `frontend/src/auth/AuthContext.tsx` (logout uses SSO logout when `user.sso_logout`), `frontend/src/demo/mockData.ts` (`/auth/methods` gains `providers: [{slug: "demo-entra", kind: "oidc", label: "Mit Microsoft anmelden", icon: "microsoft"}], saml: true`; `/auth/me` gains `sso_logout: false`), `frontend/src/lib/usePortalEnabled.ts` tests' fixtures, `frontend/src/i18n/locales/en.json` + `de.json` (`auth.providers.*`, `auth.ssoErrors.*`, `auth.loggedOut`) + propagate
- Test: `frontend/src/components/auth/SsoProviderButtons.test.tsx`, `frontend/src/lib/ssoErrors.test.ts`, `frontend/src/lib/ssoLogout.test.ts`, extend `frontend/src/routes/LoginPage.test.tsx`, `frontend/src/auth/AuthContext.test.tsx`

**Interfaces:**
- Consumes: openapi `AuthMethodsOut.providers`, `LoginProviderOut`, `UserMe.sso_logout` (Tasks 8, 11).
- Produces: D19 `SsoProviderButtons`, `ProviderIcon`, `submitSsoLogout`, `ssoErrorKey`, `SSO_ERROR_CODES`, `agentSsoLoginUrl`, `portalSsoLoginUrl`.

- [ ] **Step 1: Failing tests.**
  ```tsx
  it("renders one button per provider and navigates to its login URL", async () => {
    const assign = vi.fn();
    render(<SsoProviderButtons providers={[{ slug: "entra", kind: "oidc", label: "Mit Microsoft anmelden", icon: "microsoft" },
      { slug: "adfs", kind: "saml", label: "Firmenkonto", icon: "building" }]} hrefFor={(s) => `/x/${s}`}
      onBeforeNavigate={assign} />);
    expect(screen.getByTestId("sso-provider-entra")).toHaveTextContent("Mit Microsoft anmelden");
    await userEvent.click(screen.getByTestId("sso-provider-adfs"));
    expect(assign).toHaveBeenCalledWith(expect.objectContaining({ slug: "adfs" }));
  });
  it("ssoErrorKey maps known codes and falls back", () => {
    expect(ssoErrorKey("no_match")).toBe("auth.ssoErrors.no_match");
    expect(ssoErrorKey("1")).toBe("auth.ssoFailed");
    expect(ssoErrorKey("nonsense")).toBe("auth.ssoFailed");
    expect(ssoErrorKey(undefined)).toBeNull();
  });
  it("submitSsoLogout posts a form", () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => {});
    submitSsoLogout("/api/v1/auth/sso/logout");
    const form = document.querySelector("form[action='/api/v1/auth/sso/logout']") as HTMLFormElement;
    expect(form.method).toBe("post"); expect(submit).toHaveBeenCalled();
  });
  ```
  LoginPage: with `providers: [{slug: "entra", …}]` the button `sso-provider-entra` shows; clicking calls `window.location.assign("/api/v1/auth/sso/entra/login?next=%2F")` and `rememberLoginMethod("oidc")`; legacy `oidc: true` with `providers` containing `env-oidc` shows exactly ONE SSO button (`sso-provider-env-oidc` labelled "Mit SSO anmelden", the old `sso-login` button is gone — update the existing test that queried `sso-login` to query `sso-provider-env-oidc`); `?sso_error=no_match` shows the `auth.ssoErrors.no_match` text in `sso-error`; `?sso_error=1` keeps today's generic text and still suppresses the SPNEGO auto-start; `?logged_out=1` shows `auth.loggedOut` in `logged-out-note`. AuthContext: when `/me` has `sso_logout: true`, `logout()` calls `submitSsoLogout("/api/v1/auth/sso/logout")` and not `api.logout`.
- [ ] **Step 2: Run → FAIL.** `cd frontend && pnpm vitest run src/components/auth src/lib/ssoErrors.test.ts src/lib/ssoLogout.test.ts src/routes/LoginPage.test.tsx src/auth/AuthContext.test.tsx`
- [ ] **Step 3: Implement.** In `LoginPage`: `providers` state from `methods.providers ?? []`; `showSsoDivider = providers.length > 0 || spnegoEnabled`; the divider block renders Kerberos first, then `<SsoProviderButtons providers={providers} hrefFor={(s) => agentSsoLoginUrl(s, safeNext)} onBeforeNavigate={() => rememberLoginMethod("oidc")} />` and the button click does `window.location.assign(href)`. An empty `label` (env rows without a button label) shows `t("auth.providers.defaultLabel")` ("Mit SSO anmelden"). German strings per D20.
- [ ] **Step 4: Run → PASS**; `pnpm lint && pnpm test --run`; i18n propagate + check.
- [ ] **Step 5: Commit** `feat(login): sign-in buttons for every identity provider, SSO logout`.

### Task 15: Admin UI — provider list, preset dialog, connection and test tabs (frontend-only)

**Files:**
- Modify: `frontend/src/lib/identityApi.ts` (admin `identityApi` per D19, `providerFieldErrors`)
- Create: `frontend/src/routes/admin/IdentityProvidersPage.tsx`, `frontend/src/routes/admin/IdentityProviderPage.tsx`, `frontend/src/components/admin/identity/PresetDialog.tsx`, `ConnectionTab.tsx`, `EndpointsBox.tsx`, `TestTab.tsx`, `LoginPlanView.tsx`
- Modify: `frontend/src/router.tsx` (`/admin/identity-providers`, `/admin/identity-providers/$providerId` with search `{ tab?: "connection"|"mapping"|"roles"|"test"|"links"; test?: string }`), `frontend/src/lib/adminSearch.ts` (entry slug `identity-providers`, group `access`, keywords `["sso","saml","oidc","ldap","active directory","entra","azure","google","keycloak","authentik","adfs","identitätsanbieter","anmeldung","single sign-on"]`), `frontend/src/routes/admin/AuthConfigPage.tsx` (link box "Anmeldung über Microsoft, Google, SAML oder LDAP einrichten" → `/admin/identity-providers`, testid `auth-config-identity-link`), `frontend/src/demo/mockData.ts` (list/get/kinds/presets/capabilities/test result mocks), i18n `admin.identity.*`, `admin.identity.config.<kind>.fields.*`, `admin.nav.identityProviders`, `admin.pageDescriptions.identityProviders` + propagate
- Test: `frontend/src/routes/admin/IdentityProvidersPage.test.tsx`, `frontend/src/routes/admin/IdentityProviderPage.test.tsx`, `frontend/src/components/admin/identity/TestTab.test.tsx`

**Interfaces:**
- Consumes: Task 13 openapi; wave-0 `SchemaForm`, `schemaToFieldDefs`, `FieldsForm`, `FieldValues`.
- Produces:
  ```ts
  export function IdentityProvidersPage(): JSX.Element
  export function IdentityProviderPage(): JSX.Element                  // tabs; Mapping/Roles/Links tabs render placeholders until Task 23
  export function PresetDialog(props: { open: boolean; onClose: () => void; onCreated: (p: IdentityProvider) => void }): JSX.Element | null
  export function ConnectionTab(props: { provider: IdentityProvider; kinds: IdentityKind[]; capabilities: IdentityCapabilities;
    onSaved: (p: IdentityProvider) => void }): JSX.Element
  export function EndpointsBox(props: { provider: IdentityProvider }): JSX.Element      // copy buttons per URL
  export function TestTab(props: { provider: IdentityProvider; testId?: string; draft?: ProviderChanges }): JSX.Element
  export function LoginPlanView(props: { plan: LoginPlan }): JSX.Element
  ```

- [ ] **Step 1: Failing tests** (mock `identityApi`):
  - List: rows show name, kind label ("OpenID Connect"/"SAML"/"LDAP / Active Directory"), audience ("Agenten und Kunden"), state badge; env rows show `identity-env-badge` "aus Umgebungsvariablen, hier nicht änderbar" and no delete button; "Hinzufügen" opens `PresetDialog`.
  - PresetDialog: choosing "Microsoft Entra ID", entering name "Microsoft" → slug suggestion `microsoft` (lowercase, umlauts transliterated `ä→ae`, spaces → `-`), submit calls `create({preset: "entra_id", name: "Microsoft", slug: "microsoft", audience: "agent"})` and navigates to the detail page.
  - ConnectionTab: builds the field list with wave-0 `schemaToFieldDefs(kinds[kind].json_schema, {t, i18nPrefix: "admin.identity.config.<kind>", secretsSet})`, then post-processes it (drop `hidden_fields`, set `readOnly: true` on `read_only_fields`) and renders `FieldsForm` (wave-0 `SchemaForm` has no read-only/hidden props); unsupported keys are listed like `SchemaForm` does; `sp_certificate` read-only with a copy button, `sp_private_key` never shown; common fields (name, slug, audience select, enabled switch, button label, icon select, sort) via `FieldsForm`; Save sends only changed keys (`update(id, {config: {issuer: "…"}})`); a 422 maps `errors.config` / `errors.slug` to field errors; env provider → everything disabled + badge; SAML + `capabilities.saml_available=false` → warning box `identity-saml-unavailable` with `admin.identity.saml.unavailable.xmlsec1` text; `EndpointsBox` lists the redirect/ACS/metadata/SLO URLs for the provider's kind+audience and shows `admin.identity.endpoints.baseUrlMissing` when `base_url_missing`; SAML "Metadaten neu laden" calls `refreshMetadata` and shows `metadata_error`.
  - TestTab: "Testanmeldung starten" calls `startTest(id, {audience: "agent", draft})` then `window.location.assign(login_url)`; with `testId` prop it polls `testResult` every 1 s until `status !== "started"` (stop after 2 min) and renders the attribute table (`identity-test-attributes`, heading "Vom Anmeldedienst übermittelte Werte") and `LoginPlanView` ("Anmeldung als idp.anna", "Wird beim ersten Anmelden angelegt", reject message, role adds/removes); LDAP providers show the hint `admin.identity.test.ldapHint` instead of the start button.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** The detail page loads provider, kinds, capabilities in parallel (react-query keys `["admin","identity", id]`, `["admin","identity","kinds"]`, `["admin","identity","capabilities"]`); `?test=<id>` opens the Test tab. Icons select offers microsoft/google/key/shield/building/directory with `ProviderIcon` previews. Demo mocks for every GET and an echoing PATCH.
- [ ] **Step 4: Run → PASS**; `pnpm lint && pnpm test --run`; i18n propagate + check; visual check in demo mode (`VITE_DEMO=1 pnpm dev`, skill/memory "UI-Check via Demo-Modus"; delete `.playwright-mcp` before committing).
- [ ] **Step 5: Commit** `feat(admin): identity providers — list, presets, connection and test login`.

### Phase 2

### Task 16: One write path for Znuny users, roles, groups and customers (backend-only)

**Files:**
- Create: `backend/src/tiqora/domain/user_provisioning.py` (D17)
- Modify: `backend/src/tiqora/api/v1/admin/users.py` (`create_user`, `update_user` name/email/mobile part, `assign_group`, `revoke_group`, `assign_role`, `revoke_role` call the new functions; HTTP behaviour unchanged), `backend/src/tiqora/api/v1/admin/customers.py` (`create_customer_user` calls `create_customer_user`)
- Test: `backend/tests/test_user_provisioning.py` (DB, band 47300–47349); existing admin user/customer tests must stay green unchanged

**Interfaces:**
- Consumes: `Users`, `RoleUser`, `GroupUser`, `Roles`, `PermissionGroups`, `CustomerUser`, `set_preference`, `invalidate_znuny_cache_types`, `USER_CACHE_TYPES`, `USER_ROLE_CACHE_TYPES`, `USER_GROUP_CACHE_TYPES`, `CUSTOMER_USER_CACHE_TYPES`.
- Produces: D17 verbatim.

- [ ] **Step 1: Failing tests** (both dialects): `create_agent` writes `users` (create/change time naive UTC, create_by/change_by = actor) and `UserEmail`; enqueues one `tiqora_cache_invalidation` row per `USER_CACHE_TYPES` entry (assert by counting rows with those types before/after, then delete them in cleanup); `grant_role` twice → second returns False and only one `role_user` row (works on the testcontainer schema; on 7.1+ the surrogate `id` is DB-generated — assert by reading `role_user` with `SELECT user_id, role_id` only); `revoke_role` on a missing row → False and no invalidation row; `grant_group_permission(…, "rw")` + `current_group_perms` → `{(gid, "rw")}`; `valid_role_ids` excludes a role with `valid_id=2`; `update_agent_profile` with identical values → False and `change_time` unchanged; `create_customer_user` writes `customer_user` and `CUSTOMER_USER_CACHE_TYPES` invalidations.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** by MOVING the bodies out of the routers (no behaviour change); routers keep their HTTP checks (404s, commits).
- [ ] **Step 4: Run →** `uv run python -m pytest tests/test_user_provisioning.py $(grep -rl "admin.users\|admin import users\|/admin/users\|admin.customers" tests | tr '\n' ' ') -q` PASS; lint gates.
- [ ] **Step 5: Commit** `refactor(admin): one Znuny-identical write path for agents, roles, groups, customers`.

### Task 17: Just-in-time provisioning and profile sync (backend-only)

**Files:**
- Create: `backend/src/tiqora/identity/provisioning.py`
- Modify: `backend/src/tiqora/identity/login.py` (D7 step 5 JIT branch, step 7 profile changes; `apply_login` create + update)
- Create: `tests/golden/test_identity_jit.py` (golden, peer-gated)
- Test: `backend/tests/test_identity_jit.py` (DB, band 47350–47399)

**Interfaces:**
- Consumes: Tasks 6, 16.
- Produces:
  ```python
  async def provision_agent(session, provider: ProviderRecord, profile: MappedProfile) -> Users
      # create_agent(pw_hash=unusable_password_hash(), valid_id=1, actor_id=SYSTEM_USER_ID, title=None, mobile=profile.mobile);
      # initial roles/groups from jit_defaults (only valid ids); TiqoraUserAuthConfig sso_eligible=True (row insert, no commit)
  async def provision_customer(session, provider: ProviderRecord, profile: MappedProfile) -> CustomerUser
      # customer_id = profile.customer_id or jit_defaults.customer_id or profile.email
  async def sync_profile(session, *, user_kind: UserKind, user_id: int, changes: Mapping[str, tuple[str | None, str]]) -> None
  ```

- [ ] **Step 1: Failing tests:**
  - `test_jit_creates_agent_with_unusable_password`: provider `jit_enabled`, map from Task 5 defaults; identity with login `idp.neu.47351`, email, names → `plan.decision == "create"`; `apply_login` creates the user (`valid_id 1`, `pw` not verifiable with any password via `verify_password`, `UserEmail` pref), `tiqora_user_auth_config.sso_eligible` true, link stored, `initial_role_ids` granted.
  - `test_jit_requires_email_and_names`: missing email → `jit_missing_fields` (German message names the missing fields: "Es fehlen: E-Mail").
  - `test_jit_login_taken_rejects`: `match_by="email"`, no email match, login equals an existing (even invalid) user → `login_taken`; nothing written.
  - `test_jit_customer`: customer provider → `customer_user` row with `customer_id` from `jit_defaults.customer_id`, `pw` unusable.
  - `test_profile_sync_only_when_changed`: existing agent; IdP sends a new last name → `profile_changes == {"last_name": ("Alt", "Neu")}`, applied, `change_by == 1`; same values again → no change and `change_time` unchanged; `update_profile_on_login=False` → no changes; env-oidc provider (no map entries besides login) → never changes anything.
  - `test_plan_is_read_only`: `plan_login` for a JIT case leaves row counts of `users`, `user_preferences`, `tiqora_identity_link` unchanged.
  - Golden (`tests/golden/test_identity_jit.py`, `pytestmark = pytest.mark.golden`): call `provision_agent` + one role grant against `golden_db_url`, then `znuny_perl_eval` on the peer: `my %U = $Kernel::OM->Get('Kernel::System::User')->GetUserData(User => 'idp.golden.jit'); print "$U{UserEmail}|$U{ValidID}";` → `jit@example.org|1`, and `PermissionUserRoleGet(UserID => $id)` contains the role; clean up with `user_delete.delete_user_rows`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `apply_login` order: create (if `create`) → `flush` → profile sync → (assignments, Task 18) → link → `commit`. The `TiqoraUserAuthConfig` row is inserted directly (`session.add`) instead of `AuthConfigService.set` (which commits).
- [ ] **Step 4: Run → PASS** (`tests/test_identity_jit.py tests/test_identity_login_plan.py`); golden test runs with `GOLDEN=1 just golden-run` when a peer is up (document the command in the commit body if not run locally); lint gates.
- [ ] **Step 5: Commit** `feat(identity): create agents and customers on first sign-in, keep names and e-mail in sync`.

### Task 18: Role and group sync with managed assignments (backend-only)

**Files:**
- Modify: `backend/src/tiqora/identity/login.py` (step 8), `backend/src/tiqora/identity/provisioning.py` (`apply_assignments`), `backend/src/tiqora/api/v1/admin/identity_providers.py` (`managed-assignments` filled), `backend/src/tiqora/api/v1/admin/users.py` (`revoke_role` / `revoke_group` also delete matching `tiqora_identity_managed_assignment` rows so a hand-removed managed role is treated as manual from then on)
- Test: `backend/tests/test_identity_role_sync.py` (DB, band 47400–47499)

**Interfaces:**
- Consumes: Tasks 5, 16, 17.
- Produces:
  ```python
  async def load_assignment_state(session, *, provider_id: int, user_id: int) -> AssignmentState
  async def apply_assignments(session, *, provider_id: int, user_id: int, diff: AssignmentDiff) -> None
      # grant/revoke via user_provisioning (actor SYSTEM_USER_ID), then managed rows insert/delete; flush only
  ```

- [ ] **Step 1: Failing tests** (roles/groups seeded in band, rules on attribute `groups`):
  - `test_rule_grants_role_and_records_managed`: login with `groups=["support"]` → `role_user` row + managed row.
  - `test_manual_role_survives`: admin-assigned role 47402 also granted by a rule → no managed row; next login without the group → role 47402 still there.
  - `test_managed_role_removed_when_group_gone`: role granted by rule in login 1; login 2 without the group → `role_user` row and managed row deleted; `tiqora_cache_invalidation` got `USER_ROLE_CACHE_TYPES`.
  - `test_hand_removed_managed_role_not_resurrected_as_manual`: admin revokes a managed role via `DELETE /api/v1/admin/users/{id}/roles/{rid}` → managed row gone; next login with the group re-adds it as managed (Znuny parity: sync re-applies at login).
  - `test_group_permissions_rw_wins`: rule `GroupGrant(permissions=["ro","rw"])` → only `rw` row.
  - `test_authoritative_mode_replaces`: `sync_mode="authoritative"`, manual role present → removed; groups untouched when there are no group rules.
  - `test_invalid_role_in_rule_skipped`: rule targets a role with `valid_id=2` → not granted, no error.
  - `test_root_user_never_synced`: identity matched to user id 1 → no changes.
  - `test_two_providers_do_not_steal_management`: provider A manages role R; provider B's rule also grants R → B records nothing; A's removal removes R (documented; B re-adds on its next login).
  - Admin API: `GET …/managed-assignments` lists the managed rows with role/group names.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** per D6 + Interfaces; `plan_login` for agents loads `valid_role_ids`, `valid_group_ids`, state, then `diff_assignments`.
- [ ] **Step 4: Run → PASS**; lint gates.
- [ ] **Step 5: Commit** `feat(identity): role and group sync from the identity provider without overwriting manual assignments`.

### Task 19: LDAP directories as provider rows with Znuny AuthSync parity (backend-only)

**Files:**
- Modify: `backend/src/tiqora/domain/_ldap_core.py` (D14 `LdapEntry`, `ldap_authenticate_entry`; `ldap_authenticate` delegates), `backend/src/tiqora/api/v1/auth.py` (`login`: replace `if user is None and settings.ldap_enabled:` block with `ldap_password_login(..., user_kind="agent")` → `plan_login`/`apply_login` → `auth.get_user_by_login(login, auth_method="ldap")` — keeps today's `auth_method`), `backend/src/tiqora/api/portal/auth.py` (same for customers)
- Create: `backend/src/tiqora/identity/ldap_login.py` (D14)
- Test: extend `backend/tests/test_ldap_auth.py` (MOCK_SYNC), new `backend/tests/test_identity_ldap_sync.py` (DB + ldap3 MOCK_SYNC, band 47500–47599)

**Interfaces:**
- Consumes: Tasks 4, 6, 17, 18.
- Produces: D14 verbatim.

- [ ] **Step 1: Failing tests:**
  - `test_entry_returns_attributes_and_group_membership` (MOCK_SYNC directory with `uid=anna,ou=people,dc=example,dc=org` having `mail`, `givenName`, `sn`, `entryUUID`; group `cn=support,ou=groups,…` with `memberUid: anna` and access_attr `memberUid`, `user_attr="UID"`) → `LdapEntry.member_group_dns == ("cn=support,ou=groups,dc=example,dc=org",)`.
  - `test_env_ldap_login_unchanged`: `TIQORA_LDAP_*` settings; existing agent `anna` → `/api/v1/auth/login` succeeds exactly like before (response shape, 2FA pending when enrolled); unknown uid → 401 "Invalid credentials".
  - `test_two_ldap_providers_in_order`: provider 1 rejects the password, provider 2 binds → login via provider 2 (Znuny AuthModule chain).
  - `test_ldap_jit_and_role_sync`: DB provider (preset `ldap`) with `jit_enabled`, rule `{"attribute": "ldap:group_dn", "value": "cn=support,ou=groups,dc=example,dc=org", "role_ids": [47501]}` and `initial_groups` → first login creates the agent (`UserEmail` from `mail`), grants role + initial group; second login with changed `sn` updates `last_name`.
  - `test_customer_ldap_provider`: env customer LDAP → portal login as before; DB customer LDAP provider with JIT → `customer_user` created.
  - `test_ldap_subject_prefers_entry_uuid_then_dn`.
  - `test_require_sso_opt_in_ignored_for_ldap`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Keep the rate limiter flow identical (failure accounting happens before/after the chain exactly as today). A rejected plan (`no_match`, `user_invalid`, …) counts as a failed login → 401 "Invalid credentials" (do not leak reasons on the password form).
- [ ] **Step 4: Run → PASS** (`tests/test_ldap_auth.py tests/test_identity_ldap_sync.py tests/test_portal_auth.py tests/test_auth_hardening.py`); lint gates.
- [ ] **Step 5: Commit** `feat(identity): LDAP directories as identity providers with Znuny AuthSync parity`.

### Task 20: Customer SSO in the portal (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/portal/auth_sso.py` (D15 portal routes)
- Modify: `backend/src/tiqora/api/portal/__init__.py` (include router), `backend/src/tiqora/identity/sso_routes.py` (customer branches: cookie `customer_session_cookie_name`, `CustomerAuthService.create_session(..., sso=…)`, error redirects to `/portal/login`, `safe_portal_next`), `backend/src/tiqora/api/portal/auth.py` (`/me` sets `sso_logout`), `backend/src/tiqora/domain/schemas.py` (`PortalAuthMethodsOut`)
- Regenerate openapi files.
- Test: `backend/tests/test_identity_portal_sso.py` (ASGI + DB, band 47600–47699; SAML part saml marker)

**Interfaces:**
- Consumes: Tasks 7–11, 17.
- Produces: portal routes per D15.

- [ ] **Step 1: Failing tests** (`TIQORA_PORTAL_ENABLED=true` + `portal.enabled` setting on):
  - `test_portal_methods`: providers with audience `customer`/`both` listed, agent-only absent; `password: true`; portal disabled → 404.
  - `test_portal_oidc_login`: customer `idp.kunde.47601@example.org`, provider `match_by="email"` → `GET /api/portal/auth/sso/<slug>/login?next=/portal/tickets` → IdP with `redirect_uri=…/api/portal/auth/oidc/<slug>/callback`; callback → 302 `/portal/tickets`, cookie `tiqora_customer_session`, no agent cookie.
  - `test_portal_saml_login`: same provider kind SAML with audience `both`: ACS `/api/portal/auth/saml/<slug>/acs` → landing HTML to `/portal`; response targeted at the agent ACS posted to the portal ACS → `sso_error=saml_invalid`.
  - `test_agent_state_cannot_finish_as_customer`: state minted by the agent login start used at the portal callback → `sso_error=state`.
  - `test_portal_next_must_stay_in_portal`: `next=/admin` → `/portal`.
  - `test_portal_sso_logout`: `POST /api/portal/auth/sso/logout` → 303 IdP (SAML) / `/portal/login?logged_out=1`; LogoutResponse at the shared SLS → 303 `/portal/login?logged_out=1` (audience from the stored logout request).
  - `test_customer_jit_only_with_db_backend`: JIT customer created (Tiqora resolves customers only from `customer_user`; documented).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** by passing `user_kind="customer"` through the shared bodies in `sso_routes.py`.
- [ ] **Step 4: Run → PASS**; regenerate openapi; lint gates.
- [ ] **Step 5: Commit** `feat(portal): customers sign in through OIDC and SAML identity providers`.

### Task 21: Logout started by the identity provider; IdP-initiated SAML login (backend-only)

**Files:**
- Create: `backend/src/tiqora/identity/oidc_backchannel.py`
- Modify: `backend/src/tiqora/domain/oidc.py` (`jwks()`, id-token verification in `fetch_tokens_and_claims` → `id_token_claims` incl. `sid`), `backend/src/tiqora/identity/sso_routes.py` (`handle_sls` SAMLRequest branch; ACS IdP-initiated branch; OIDC callback stores `sid`), `backend/src/tiqora/api/v1/auth_sso.py` (`POST /auth/oidc/{slug}/backchannel-logout`)
- Regenerate openapi files.
- Test: `backend/tests/test_identity_idp_logout.py` (band 47700–47799)

**Interfaces:**
- Consumes: Tasks 7–11, 20.
- Produces:
  ```python
  class LogoutTokenInvalid(ValueError): ...
  @dataclass(frozen=True, slots=True)
  class LogoutToken:
      sub: str | None
      sid: str | None
      jti: str
  async def verify_logout_token(token: str, *, service: OIDCService, client_id: str, issuer: str,
                                max_age_seconds: int = 300) -> LogoutToken
      # authlib.jose JsonWebToken(["RS256","ES256","PS256"]) with service.jwks(); checks iss, aud == client_id,
      # iat within max_age, events has "http://schemas.openid.net/event/backchannel-logout", no "nonce",
      # sub or sid present; unknown kid -> refetch JWKS once
  ```

- [ ] **Step 1: Failing tests.** OIDC: build an RSA JWK pair in the test (`authlib.jose.JsonWebKey.generate_key("RSA", 2048, is_private=True)`), serve JWKS from the `MockTransport`, sign logout tokens with `authlib.jose.jwt.encode`.
  - `test_backchannel_logout_by_sid`: login stores `sid` from a signed id_token; logout token with that `sid` → 200 `Cache-Control: no-store`; the agent AND a customer session with the same sid/provider revoked; other sessions untouched.
  - `test_backchannel_logout_by_sub`: token with `sub` only → all sessions of that subject revoked.
  - `test_backchannel_rejects`: wrong `aud`, wrong `iss`, missing `events`, `nonce` present, expired `iat`, bad signature, replayed `jti` → 400 and nothing revoked; provider with `backchannel_logout=False` → 404.
  - SAML (saml marker): `test_idp_initiated_logout_request`: logged-in agent session; IdP `logout_request_redirect(name_id, session_index)` → `GET /api/v1/auth/saml/<slug>/sls?SAMLRequest=…&RelayState=…&SigAlg=…&Signature=…` → 302 to the IdP SLO URL with a signed `SAMLResponse` (IdP helper parses: status Success); session revoked. Unsigned/foreign-signed LogoutRequest → 400, nothing revoked.
  - `test_idp_initiated_login_off_by_default`: unsolicited response at ACS → `sso_error=saml_invalid`; with `allow_idp_initiated=True` → logged in, `RelayState=/tickets/7` honoured through `safe_next`, `RelayState=https://evil.example.com` → `/`; replay of the same unsolicited response → rejected (assertion-id cache).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Back-channel route parses `application/x-www-form-urlencoded` `logout_token`, `mark_jti_seen`, `revoke_sso_sessions(provider_id, sid=… or subject_hash=subject_hash(sub))`. The OIDC callback verifies the id_token with JWKS (`aud`, `iss`, `nonce` == stored nonce, `exp`) when present; failure → `oidc_failed` (env-oidc: a provider that returns no id_token keeps working as today).
- [ ] **Step 4: Run → PASS**; regenerate openapi; lint gates.
- [ ] **Step 5: Commit** `feat(identity): logout from the identity provider (SAML SLO, OIDC back-channel) and opt-in IdP-initiated SAML login`.

### Task 22: Customer TOTP and passkeys (backend-only)

**Files:**
- Modify: `backend/src/tiqora/db/tiqora/models.py` (`TiqoraCustomerTotp`, `TiqoraCustomerPasskey`), `backend/src/tiqora/domain/totp.py`, `backend/src/tiqora/domain/passkey.py`, `backend/src/tiqora/domain/customer_auth.py` (D13 pending + authat), `backend/src/tiqora/api/portal/auth.py` (login → pending when a factor is enrolled; `/totp/verify`, `/passkey/authenticate/begin|finish` promote), `backend/src/tiqora/api/portal/deps.py` (`get_pending_customer_token`, `CustomerTOTPServiceDep`, `CustomerWebAuthnServiceDep`), `backend/src/tiqora/domain/schemas.py` (`CustomerLoginResponse` per D15)
- Create: `backend/src/tiqora/domain/two_factor_subject.py` (D13), `backend/src/tiqora/api/portal/security.py`, `backend/alembic/versions_tiqora/<date>_<NNNN>_customer_two_factor.py` (down_revision = current head), update head tests
- Regenerate openapi files.
- Test: `backend/tests/test_customer_two_factor.py` (DB + ASGI, band 47800–47899); existing `test_totp.py`, `test_passkey.py`, `test_admin_auth_config.py` stay green

**Interfaces:**
- Consumes: D13.
- Produces: portal routes (all under `/api/portal/auth`, mirroring the agent ones; bodies/response models reuse `TOTPCodeIn`, `TOTPEnrollIn`, `TOTPEnrollOut`, `TOTPStatusOut`, `PasskeyOut`, `PasskeyStatusOut`, `PasskeyRegisterFinishIn`, `PasskeyAuthenticateFinishIn`):
  ```
  GET    /totp/status            POST /totp/enroll        GET /totp/enroll/qr      POST /totp/confirm
  DELETE /totp                   POST /totp/verify (pending -> CustomerLoginResponse)
  GET    /passkey                POST /passkey/register/begin   POST /passkey/register/finish
  DELETE /passkey/{passkey_id}   POST /passkey/authenticate/begin   POST /passkey/authenticate/finish
  ```
  Recent-auth rule for register/delete when a factor exists: `CustomerSessionStore.seconds_since_auth(token) <= settings.step_up_max_age_seconds`, else 403 `{"detail": "reauth_required"}` (same contract as agents).

- [ ] **Step 1: Failing tests:**
  - `test_totp_redis_keys_are_namespaced`: agent 47801 and customer with `customer_user.id == 47801` enroll at the same time → Redis has `tiqora:totp:pending:47801` and `tiqora:totp:customer:pending:47801`; confirming the customer code does not enable the agent factor; replay key of one does not block the other.
  - `test_customer_user_handle_differs_from_agent`: `WebAuthnService(..., subject=CUSTOMER_2FA).begin_registration(...)` options `user.id` == `b"tiqora-customer:" + (47801).to_bytes(8, "big")` (base64url); agent options unchanged (`(47801).to_bytes(8, "big")`).
  - `test_portal_login_pending_when_totp_enrolled`: password login → `{"customer": null, "pending_2fa": true, "totp_enrolled": true}` and the cookie holds a pending token; `/api/portal/auth/me` → 401; `/totp/verify` with a valid code → `customer` set, full session.
  - `test_portal_sso_skips_customer_2fa`: SSO login of a customer with TOTP → full session immediately.
  - `test_passkey_register_requires_recent_auth_when_factor_exists`.
  - `test_customer_totp_row_keyed_by_customer_user_id`: raw SQL shows column `customer_user_id`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** D13 (agent Redis keys and user handle must stay byte-identical — the existing agent tests prove it); migration creates the two tables; ORM attribute `user_id` mapped to column `customer_user_id`.
- [ ] **Step 4: Run → PASS** (`tests/test_customer_two_factor.py tests/test_totp.py tests/test_passkey.py tests/test_portal_auth.py tests/test_migration_gate.py tests/test_migrate_cli.py tests/test_tiqora_model_ddl.py`); regenerate openapi; lint gates.
- [ ] **Step 5: Commit** `feat(portal): two-factor sign-in with TOTP and passkeys for customers`.

### Task 23: Admin UI — Zuordnung, Rollen, Verknüpfungen tabs (frontend-only)

**Files:**
- Create: `frontend/src/components/admin/identity/MappingTab.tsx`, `RolesTab.tsx`, `LinksTab.tsx`
- Modify: `frontend/src/routes/admin/IdentityProviderPage.tsx` (replace placeholders), `frontend/src/components/admin/identity/TestTab.tsx` ("Werte für die Vorschau übernehmen" stores the last test's attributes in component state shared with `MappingTab` through the page), `frontend/src/demo/mockData.ts`, i18n `admin.identity.mapping.*`, `admin.identity.roles.*`, `admin.identity.links.*` + propagate
- Test: `frontend/src/components/admin/identity/MappingTab.test.tsx`, `RolesTab.test.tsx`, `LinksTab.test.tsx`

**Interfaces:**
- Consumes: Tasks 13, 17, 18 openapi (`AttributeMap`, `JitDefaults`, `RoleRule`, `GroupGrant`, `LoginPlanOut`, `ManagedAssignmentOut`, `IdentityLinkOut`); existing role/group list APIs used by `RoleGroupsPage` (`api.request("GET", "/api/v1/admin/roles", …)` / `/groups` with `page_size=500`).
- Produces:
  ```ts
  export function MappingTab(props: { provider: IdentityProvider; sampleAttributes: Record<string, string[]> | null;
    onSaved: (p: IdentityProvider) => void }): JSX.Element
  export function RolesTab(props: { provider: IdentityProvider; onSaved: (p: IdentityProvider) => void }): JSX.Element
  export function LinksTab(props: { provider: IdentityProvider }): JSX.Element
  ```

- [ ] **Step 1: Failing tests:**
  - MappingTab: one row per Tiqora field ("Anmeldename", "E-Mail", "Vorname", "Nachname", "Kundennummer" (only for audience customer/both), "Mobilnummer"); each row is a tags input of source attribute names; "Anmeldung zuordnen über" select (Anmeldename / E-Mail); JIT switch "Beim ersten Anmelden automatisch anlegen" reveals initial roles/groups (agents) and fixed Kundennummer (customers); "Namen und E-Mail bei jeder Anmeldung aktualisieren" switch; Save sends `{attribute_map, match_by, jit_enabled, jit_defaults}`; with `sampleAttributes` the live preview calls `preview(id, {attributes, draft: <unsaved changes>})` (debounced 400 ms) and renders `LoginPlanView`; env provider → read-only.
  - RolesTab: rules table (attribute, value, roles multi-select, groups + permission checkboxes ro/move_into/create/note/owner/priority/rw with rw hint "rw enthält alle Rechte"); add/remove rule; sync mode radio "Nur selbst vergebene Zuordnungen pflegen (empfohlen)" / "Wie Znuny: alle Rollen und Gruppen ersetzen" with help texts; Save sends `{role_rules, sync_mode}`; the 422 `errors.role_rules` shows under the table; "Verwaltete Zuordnungen" list from `managedAssignments`; `ldap` providers get the attribute suggestion "ldap:group_dn" with the label "Mitglied der LDAP-Gruppe (DN)".
  - LinksTab: list (Konto, Art Agent/Kunde, Kennung beim Anmeldedienst, zuerst/zuletzt gesehen in the agent's zone), "Verknüpfung lösen" with `useConfirm` → `removeLink`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**; demo mocks for preview/managed/links.
- [ ] **Step 4: Run → PASS**; `pnpm lint && pnpm test --run`; i18n propagate + check; demo-mode visual check.
- [ ] **Step 5: Commit** `feat(admin): attribute mapping, role rules and account links for identity providers`.

### Task 24: Portal — SSO buttons, two-factor step, security page, SSO logout (frontend-only)

**Files:**
- Create: `frontend/src/components/security/twoFactorApi.ts`, `frontend/src/components/security/TwoFactorPanel.tsx` (extracted from `routes/agent/SecurityPage.tsx` without changing agent test ids), `frontend/src/routes/portal/SecurityPage.tsx`, `frontend/src/components/portal/PortalTwoFactorStep.tsx`, `frontend/src/lib/portalSecurityApi.ts` (wrappers behind `customerTwoFactorApi`)
- Modify: `frontend/src/routes/agent/SecurityPage.tsx` (renders `<TwoFactorPanel api={agentTwoFactorApi} testIdPrefix="security" />` plus whatever non-2FA content it has), `frontend/src/routes/portal/PortalLoginPage.tsx` (methods query `GET /api/portal/auth/methods`, `SsoProviderButtons` with `portalSsoLoginUrl`, `sso_error`/`logged_out` notes, pending-2FA step), `frontend/src/auth/CustomerAuthContext.tsx` (`login` returns `CustomerLoginResponse`; `pending2fa`, `pendingFactors`, `verifyTotp(code)`, `verifyPasskey()`; `logout` uses `submitSsoLogout("/api/portal/auth/sso/logout")` when `customer.sso_logout`), `frontend/src/router.tsx` (`/portal/security` under `portalLayoutRoute`), portal navigation component (link "Sicherheit"; find with `grep -rn "portal/preferences" frontend/src/components/portal`), `frontend/src/demo/handlers.ts` (`/api/portal/auth/methods` → `{password: true, providers: [], webauthn: false}`), `frontend/src/demo/mockData.ts`, i18n `portal.security.*`, `portal.login.*` + propagate
- Test: `frontend/src/components/security/TwoFactorPanel.test.tsx`, `frontend/src/routes/portal/SecurityPage.test.tsx`, `frontend/src/routes/portal/PortalLoginPage.test.tsx` (new), `frontend/src/auth/CustomerAuthContext.test.tsx` (extend); existing `frontend/src/routes/agent/SecurityPage.test.tsx` must pass unchanged

**Interfaces:**
- Consumes: Tasks 20, 22 openapi; D19 `TwoFactorApi`, `TwoFactorPanel`, `SsoProviderButtons`.
- Produces: D19 security components; `PortalTwoFactorStep(props: { factors: { totp: boolean; passkey: boolean }; onDone: () => void }): JSX.Element`.

- [ ] **Step 1: Failing tests:**
  - TwoFactorPanel with a fake `TwoFactorApi`: enrol TOTP (QR from `enrollQrUrl()`, confirm code), disable with code, list/add/delete passkey, reauth error shows `security.passkeyReauthRequired`.
  - Portal SecurityPage renders `TwoFactorPanel` with `customerTwoFactorApi` (calls go to `/api/portal/auth/totp/status` etc.).
  - PortalLoginPage: providers from `/api/portal/auth/methods` render `sso-provider-<slug>` buttons linking to `/api/portal/auth/sso/<slug>/login?next=%2Fportal`; `?sso_error=no_match` shows the message; a password login answering `pending_2fa: true, totp_enrolled: true` shows `PortalTwoFactorStep`; a correct code navigates to `/portal`.
  - CustomerAuthContext: `logout` with `sso_logout: true` submits the SSO logout form.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Move code, do not rewrite it: the agent SecurityPage tests are the safety net. Portal passkey sign-in uses `@simplewebauthn/browser` exactly as the agent `LoginPage` does (`grep -n "startAuthentication" frontend/src/routes/LoginPage.tsx`).
- [ ] **Step 4: Run → PASS**; `pnpm lint && pnpm test --run`; i18n propagate + check; demo-mode visual check of `/portal/login` and `/portal/security`.
- [ ] **Step 5: Commit** `feat(portal): sign in with identity providers, two-factor step and security page for customers`.

### Task 25: Optional Keycloak round trip (backend-only, marker `keycloak`)

**Files:**
- Create: `backend/tests/test_identity_keycloak.py`
- Modify: `docs/testing.md` (how to run it)

**Interfaces:**
- Consumes: everything above.
- Produces: an opt-in integration test; skipped unless `TIQORA_KEYCLOAK_TESTS=1` (and Docker available). Not part of CI.

- [ ] **Step 1: Write the test.** `testcontainers.core.container.DockerContainer("quay.io/keycloak/keycloak:26.0.7").with_command("start-dev").with_env("KC_BOOTSTRAP_ADMIN_USERNAME","admin").with_env("KC_BOOTSTRAP_ADMIN_PASSWORD","admin")`; wait for `/realms/master`; create via the admin REST API a realm `tiqora`, a confidential OIDC client (`redirectUris` = agent callback), a SAML client (entity id = SP entity id, ACS, signing on, `saml.force.name.id.format=true`), a user `kc.anna` with email and group `support`; Tiqora provider rows point at it with `allow_private_network=True`. Drive the browser leg with `httpx` (follow the Keycloak login form: GET auth URL → parse `<form id="kc-form-login" action=…>` → POST username/password with the Keycloak cookies → capture the redirect to the Tiqora callback / the auto-POST form to the ACS) and assert a Tiqora session for agent `kc.anna` (JIT) with the role from the `groups` rule, for both OIDC and SAML; then SP-initiated logout ends at `/login?logged_out=1`.
- [ ] **Step 2: Run** `cd backend && TIQORA_KEYCLOAK_TESTS=1 uv run python -m pytest tests/test_identity_keycloak.py -q -m keycloak` → PASS (takes ~1–2 min). Without the env var → skipped.
- [ ] **Step 3: Lint gates; commit** `test(identity): optional Keycloak round trip for OIDC and SAML`.

### Task 26: Documentation and full verification

**Files:**
- Create: `docs/guide/identity-providers.md` (admin guide, German headings first then English summary like the other guides; sections: Überblick, Microsoft Entra ID (OIDC, which redirect URI to register, groups claim), Google Workspace, Keycloak/Authentik, ADFS/SAML (SP metadata URL, signing, NameID), LDAP / Active Directory (Znuny `AuthSyncModule` mapping table: `UserSyncMap` → Zuordnung, `UserSyncInitialGroups` → Erstanmeldung, `UserSyncGroupsDefinition`/`UserSyncRolesDefinition` → Rollen with `ldap:group_dn`, `UserSync*AttributeDefinition` → Rollen with an attribute), Testanmeldung, Verwaltete Zuordnungen vs. "Wie Znuny", Abmeldung (SLO, back-channel), Kunden-Portal (SSO, 2FA), Fehlercodes `sso_error=…`)
- Modify: `docs/deployment.md` + `docs/deploy/docker-compose.md` (env OIDC/LDAP still supported and shown read-only in the UI; `TIQORA_PUBLIC_BASE_URL` required for SAML and UI-configured OIDC; `xmlsec1` is in the image; reverse proxy must allow ACS POST bodies ≥ 1 MB — nginx `client_max_body_size 2m` on `/api/` — and must not buffer-reject large headers; back-channel logout URL must be reachable from the IdP), `docs/architecture.md` (identity package, tables, login orchestration diagram, SSO session index, flow-state keys), `docs/compatibility.md` (SAML agents + customers, several IdPs, JIT, role/group sync = Znuny 7.3 `Auth::SAML`/`AuthSync` parity incl. differences: managed mode default, no NestedGroupSearch, no customer group sync), `docs/security-review-znuny-6.5.22.md` only if it lists SSO gaps (check with `grep -n "SAML\|SSO" docs/security-review-znuny-6.5.22.md`), `docs/testing.md` (saml marker, `xmlsec1` locally, `TIQORA_REQUIRE_SAML_TESTS`, Keycloak test), `docs/api/rest-v1.md` (new auth + admin endpoints)
- Do NOT edit `site/compare.html` or README claims.

- [ ] **Step 1:** Write the docs. Every example uses `example.org`/`example.com` hosts and made-up names.
- [ ] **Step 2:** Full backend run: `cd backend && uv sync --extra dev --extra crypto --extra saml && TIQORA_STRICT_DB_LEAKS=1 TIQORA_REQUIRE_SAML_TESTS=1 uv run python -m pytest -q` → green (no new skips except Keycloak/golden); `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`.
- [ ] **Step 3:** Full frontend run: `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`.
- [ ] **Step 4:** Schema drift check: regenerate openapi (Global Constraints command) → `rtk git status` shows no diff.
- [ ] **Step 5:** Manual smoke in demo mode (`VITE_DEMO=1`): `/login` shows provider buttons, `/admin/identity-providers` list/detail tabs render, `/portal/login` + `/portal/security` render; delete `.playwright-mcp` if used.
- [ ] **Step 6: Commit** `docs(identity): identity providers, SAML, customer SSO and 2FA`.

---

## Self-review notes (for the executor)

- Spec coverage: provider table + env migration (Tasks 2–4), multi-OIDC (8), SAML agents incl. SP metadata and SP-initiated SLO (9–11), metadata by URL/upload + daily refresh (9, 12), admin UI with test login (13, 15, 23), Dockerfile `xmlsec1` + extra `saml` (1), customer SSO SAML+OIDC (20), customer TOTP/passkeys (22, 24), JIT (17), role/group sync with managed assignments (5, 18), IdP-initiated SLO + back-channel logout (21), LDAP rows with AuthSync parity (19), security corpus (9–10, 21), CSRF exemption (10), nginx body-size note (26), golden JIT test (17), optional Keycloak (25). Phase 3 (SCIM, step-up, per-IdP session lifetime) deliberately absent.
- Production safety net: Task 4 (env rows, lazy ensure) + Task 8 (`test_env_oidc_unchanged_*`, legacy state) + Task 19 (`test_env_ldap_login_unchanged`). Run these three modules after every later backend task that touches `sso_routes.py`, `login.py` or `api/v1/auth.py`.
- Files other wave-1 plans may also touch: Alembic head (two migrations here: Tasks 2 and 22), `api/v1/admin/__init__.py`, `api/v1/__init__.py`, `api/portal/__init__.py`, `api/app.py` lifespan, `worker/__main__.py` + `worker/services.py` (one daily loop), `domain/settings_store.py` (one key), `security/csrf.py`, `domain/schemas.py`, `router.tsx`, `lib/adminSearch.ts`, `demo/mockData.ts`, all locale files. Rebase-friendly: append, never reorder existing entries.
