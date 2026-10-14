# Wave 0 — Cross-cutting Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the six shared foundations every later roadmap area consumes — async jobs with polling, a typed field engine with ACL-filtered options (incl. editable dynamic fields in ticket zoom), one JSON ticket filter language with a permission-scoped compiler, an internal extension registry, DB-stored settings sections with generated admin forms, and the two stale doc rows.

**Architecture:** Four new backend packages (`tiqora.extensions`, `tiqora.jobs`, `tiqora.filters`, `tiqora.settings_sections`) plus two domain modules (`domain/dynamic_field_values.py`, `domain/field_options.py`). One Alembic migration adds `tiqora_job` + `tiqora_job_file`; settings reuse `tiqora_settings`. The worker gets one more loop (`JobRunner`) that claims jobs with a portable conditional `UPDATE` and runs handlers as bounded asyncio tasks. Frontend gets `src/extensions/registry.ts`, `components/fields/*` (field engine + `SchemaForm`), `components/jobs/*` (`useJob`, `JobProgress`, `JobDialog`). First real consumers in this wave: the ticket CSV export as a job (jobs + filter + bridge), the "Kundenbilder" and new "Hintergrundaufgaben" settings (settings sections + `SchemaForm`), phone/new-ticket forms and ticket zoom (field engine).

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic (two-chain setup), Pydantic v2, asyncio worker loops, React 19 + TanStack Router/Query, Tailwind tokens, Vitest + Testing Library, MSW demo mode.

**Spec:** `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` → "Cross-cutting foundations (wave 0)" and Principles 3 + 5. Consumers whose needs shaped the interfaces: `2026-10-08-reporting-analytics-design.md` §2, §4, §8 (jobs, filter model, permission in the compiler), `2026-10-08-dashboard-widgets-design.md` (JSON-schema settings forms over the field engine), `2026-10-08-plugin-system-design.md` §2–4 (registry shape, `register(registry)`, settings schema → admin form), `2026-10-08-process-management-design.md` "Dialog forms", `2026-10-08-webchat-channel-design.md` (pre-chat form), `2026-10-08-generic-interface-design.md` (async via `tiqora_job`), `2026-10-08-identity-saml-design.md` (provider settings).

## Global Constraints

- Commit directly to `main`; never push (a push deploys production). Commit identity is already configured; never `--no-verify` (PII hooks). Only made-up example data (example.org etc.).
- Use `rtk` in front of git commands (`rtk git add …`, `rtk git commit …`). After every commit run `rtk git log -1 --oneline` — the pre-commit hook can block a commit and chained commands still print success. Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (needs Docker; never plain `uv run pytest`); for a task, run the touched test modules plus the full suite at least at the end of the plan. Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules must clean up their rows (pattern: `_seed_statements` + module-scoped autouse fixture, see `backend/tests/test_ai_refine.py`); never add entries to `backend/tests/db_leak_baseline.txt`.
- New DB test modules pick their own seed-id band and logins; check it is free with `grep -rn "<band>" backend/tests` before using it (skill `pytest-session-container-id-band-collisions`).
- Migrations: Alembic two-chain setup; new migrations go into `backend/alembic/versions_tiqora` with `down_revision` = the CURRENT primary head at implementation time (check with `ls backend/alembic/versions_tiqora | tail -1` and `cd backend && uv run alembic heads` — do not trust any id written in this plan); follow skill `alembic-two-chain-new-head-forks-graph`; update tests that hardcode the head id (`backend/tests/test_migration_gate.py` `TIQORA_HEAD`, `backend/tests/test_migrate_cli.py` `version == …`). Tables only `tiqora_*`; never change Znuny tables' DDL; writes to Znuny tables must be behaviourally identical to Znuny (source: `znuny-6.5.22/` in the repo root).
- ORM models in `backend/src/tiqora/db/tiqora/models.py` must produce the same DDL as the migration (tests build tables with `TiqoraBase.metadata.create_all`, see `backend/tests/test_tiqora_model_ddl.py`).
- MySQL/MariaDB AND PostgreSQL must both work (no dialect-specific SQL without a per-dialect path). An existing Znuny MariaDB can mix collations: never JOIN `tiqora_*` tables with Znuny tables on string columns — load keys, then IN-list.
- Timestamps in `tiqora_job*` are written by the application as naive UTC (`tiqora.jobs.service.utc_now()`), and every comparison uses Python-computed UTC values — never `func.now()`/`current_timestamp` (DB session time zone ≠ UTC on prod; skill `db-session-timezone-vs-app-written-rows`). API responses expose datetimes as `UtcDateTime` (`tiqora.domain.schemas.UtcDateTime`; skill `tiqora-naive-utc-datetimes-display-2h-early`).
- `tiqora.domain.settings_store.set_setting` commits by itself. New code that writes several settings in one transaction must not call it (use `tiqora.settings_sections.store`, which only flushes).
- After any API model/route change: regenerate `packages/api-client/openapi.json` (skill `tiqora-openapi-regen-or-ci-wipes-schema`); never hand-edit `schema.d.ts`. Commands: `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`, then `head -c 40 packages/api-client/openapi.json` (must start `{"components"`) and `rtk git diff --stat packages/api-client/openapi.json` (tens/hundreds of lines, not five figures).
- Frontend: `cd frontend && pnpm lint && pnpm test --run`; never run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then propagate with `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales); `node frontend/scripts/check-i18n-keys.mjs` must pass. New screens get demo-mode mocks (`frontend/src/demo/mockData.ts` `resolveData`, binary responses in `frontend/src/demo/handlers.ts`; `VITE_DEMO=1`). Colour tokens only via the existing Tailwind token setup (`bg-surface`, `text-muted`, `border-hairline`, `text-danger`, `bg-accent`…).
- Frontend API wrappers are hand-written files in `frontend/src/lib/*Api.ts` using `api.request<T>(method, path, {query, body, signal})` with types from `Schemas["…"]` (pattern: `frontend/src/lib/phoneApi.ts`). `ApiError` carries the parsed response body (skill `apierror-detail-is-body-not-string`); use the existing `apiErrorMessage` helper (`frontend/src/lib/avatarApi.ts`) for messages.
- A Pydantic response field WITH a default becomes required in the generated TS type — test fixtures and demo mocks that build such objects must include it.
- UI copy German first, plain words, named by what admins recognise; no system jargon in UI text ("Hintergrundaufgabe", not "Job"; "Einstellungen", not "Settings-Section"; "Filter", not "Prädikat").
- `NewTicketPage` tests: the form sits in `<fieldset disabled>` until a customer is picked — call the test's `pickCustomer()` helper (skill `tiqora-newticket-fieldset-locks-tests`).

## Review Focus

1. **A handler crashes, times out, or the worker restarts mid-job** → the job ends `failed` with a plain German message, the worker loop keeps running, and no job stays `running` forever. Pinned in Task 5 (`test_handler_exception_marks_failed_and_runner_survives`, `test_stale_running_job_is_failed_as_interrupted`, `test_timeout_marks_failed`).
2. **A filter mentions a queue the agent may not read, or an `any` branch that would widen the result** → never returns tickets outside the agent's `ro` queues. Pinned in Task 8 (`test_scope_is_never_widened_by_filter`).
3. **An admin saves a settings form with a secret untouched (mask sent back or field omitted)** → the stored ciphertext is unchanged; no GET ever returns plaintext. Pinned in Task 3 (`test_secret_mask_round_trip_does_not_rotate`).
4. **Invalid dynamic-field input (Date with a time part, Checkbox "yes", two values for a Dropdown) or an unchanged value** → 422 and nothing written; an unchanged value writes no history row and does not bump `change_time` (Znuny `ValueIsDifferent`). Pinned in Task 15.
5. **A DateTime dynamic field entered by an agent in Europe/Berlin** → stored as UTC wall time like Znuny does, shown back in the agent's zone. Pinned in Task 14 (`dynamicFields.test.ts`).

---

## Design (fixed)

Later plans (reporting, identity, process, GI, webchat, dashboards, plugins) are written against the names in this section. Do not rename anything here without updating this document in the same commit.

### D0. File map

Backend (new):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/extensions/__init__.py` | re-exports `ExtensionPoint`, `Registry`, `Registration`, `DuplicateExtensionError`, `RegistryFrozenError`, `get_registry`, `register_core`, `set_registry_for_tests` |
| `backend/src/tiqora/extensions/registry.py` | generic registry |
| `backend/src/tiqora/extensions/core.py` | `register_core`, process-wide `get_registry` |
| `backend/src/tiqora/channels/registry.py` | `ChannelDescriptor`, `CHANNELS`, `CHANNEL_CONFIG_KEYS` (moved from `api/v1/admin/channels.py`) |
| `backend/src/tiqora/settings_sections/__init__.py`, `section.py`, `store.py` | settings sections |
| `backend/src/tiqora/jobs/__init__.py`, `registry.py`, `context.py`, `service.py`, `runner.py`, `schemas.py`, `settings.py` | jobs |
| `backend/src/tiqora/jobs/handlers/__init__.py`, `ticket_export.py` | first job handler |
| `backend/src/tiqora/avatars/settings.py` | `AvatarSettings`, `AVATARS_SECTION` |
| `backend/src/tiqora/filters/__init__.py`, `model.py`, `catalog.py`, `tickets.py`, `ticket_params.py` | filter language + ticket compiler + list-param bridge |
| `backend/src/tiqora/domain/ticket_csv.py` | CSV header/row/writer shared by `GET /tickets/export.csv` and the export job |
| `backend/src/tiqora/domain/dynamic_field_values.py` | Znuny-identical typed dynamic-field ValueSet |
| `backend/src/tiqora/domain/field_options.py` | ACL-filtered option lists for ticket attributes and dynamic fields |
| `backend/src/tiqora/api/v1/jobs.py` | `/api/v1/jobs` |
| `backend/src/tiqora/api/v1/ticket_filters.py` | `/api/v1/ticket-filters` |
| `backend/src/tiqora/api/v1/admin/settings_sections.py` | `/api/v1/admin/settings-sections` |
| `backend/src/tiqora/api/v1/admin/extensions.py` | `/api/v1/admin/extensions` |

Frontend (new):

| Path | Responsibility |
|---|---|
| `frontend/src/extensions/registry.ts` | frontend extension points |
| `frontend/src/components/fields/types.ts` | `FieldKind`, `FieldValue`, `FieldDef`, … |
| `frontend/src/components/fields/kinds.tsx` | built-in kind components + `FIELD_KINDS` point |
| `frontend/src/components/fields/MultiSelectInput.tsx` | chips + menu multi select |
| `frontend/src/components/fields/FieldInput.tsx` | dispatcher by kind |
| `frontend/src/components/fields/FieldsForm.tsx` | labelled grid of fields |
| `frontend/src/components/fields/validate.ts` | required/number/length checks |
| `frontend/src/components/fields/dynamicFields.ts` | Znuny dynamic field ↔ engine adapters |
| `frontend/src/components/fields/DynamicFieldsForm.tsx` | drop-in replacement for `DynamicFieldInputs` |
| `frontend/src/components/fields/useFieldOptions.ts` | ACL-filtered options hook |
| `frontend/src/components/fields/jsonSchema.ts` | JSON schema → `FieldDef[]` |
| `frontend/src/components/fields/SchemaForm.tsx` | generated form for JSON schemas |
| `frontend/src/components/fields/index.ts` | barrel |
| `frontend/src/components/jobs/useJob.ts`, `JobProgress.tsx`, `JobDialog.tsx` | job polling UI |
| `frontend/src/components/agent/TicketDynamicFields.tsx` | zoom: read + edit dynamic fields |
| `frontend/src/routes/admin/SettingsSectionPage.tsx` | `/admin/settings/$section` |
| `frontend/src/lib/jobsApi.ts`, `fieldOptionsApi.ts`, `settingsSectionsApi.ts` | API wrappers |

Deleted: `frontend/src/components/agent/phone/DynamicFieldInputs.tsx` (Task 14).

### D1. Extension registry (backend) — `tiqora.extensions`

```python
# backend/src/tiqora/extensions/registry.py
T = TypeVar("T")

class DuplicateExtensionError(ValueError): ...
class RegistryFrozenError(RuntimeError): ...

@dataclass(frozen=True, slots=True)
class ExtensionPoint(Generic[T]):
    key: str                        # dotted, "<owner>.<area>.<thing>", e.g. "core.jobs.handlers"
    item_key: Callable[[T], str]    # unique key of an item inside this point
    description: str
    sdk_stable: bool = False        # True once part of tiqora-sdk; every point is False in wave 0

@dataclass(frozen=True, slots=True)
class Registration(Generic[T]):
    point_key: str
    key: str
    item: T
    owner: str                      # "core" or a plugin id (reverse-DNS) later

class Registry:
    def __init__(self) -> None: ...
    def add(self, point: ExtensionPoint[T], item: T, *, owner: str = "core") -> None: ...
        # DuplicateExtensionError if point already has item_key(item); RegistryFrozenError after freeze()
    def get(self, point: ExtensionPoint[T], key: str) -> T | None: ...
    def require(self, point: ExtensionPoint[T], key: str) -> T: ...      # KeyError(f"{point.key}: {key}")
    def items(self, point: ExtensionPoint[T]) -> list[T]: ...            # registration order
    def registrations(self, point: ExtensionPoint[T] | None = None) -> list[Registration[Any]]: ...
    def points(self) -> list[ExtensionPoint[Any]]: ...                  # every point that has at least one item
    def freeze(self) -> None: ...
    @property
    def frozen(self) -> bool: ...
```

```python
# backend/src/tiqora/extensions/core.py
def register_core(registry: Registry) -> None
    # the ONLY place core items are registered; imports feature modules inside the function
    # (no import-time side effects). Final content after this plan:
    #   for d in core_channel_descriptors(): registry.add(CHANNELS, d)
    #   registry.add(SETTINGS_SECTIONS, JOBS_SECTION); registry.add(SETTINGS_SECTIONS, AVATARS_SECTION)
    #   registry.add(JOB_HANDLERS, TICKET_EXPORT_JOB)
    #   + validate_section_schema(s) for every settings section
def get_registry() -> Registry       # process-wide; first call builds Registry(), register_core(), freeze(); thread-safe via threading.Lock
def set_registry_for_tests(registry: Registry | None) -> None   # None = drop the cached one
```

Points live next to the feature that owns them (never in `extensions/`), so feature modules never import each other through the registry:

| Point constant | Module | `key` | Item type | Item key |
|---|---|---|---|---|
| `CHANNELS` | `tiqora.channels.registry` | `core.channels` | `ChannelDescriptor` | `.key` |
| `SETTINGS_SECTIONS` | `tiqora.settings_sections.section` | `core.settings.sections` | `SettingsSection` | `.key` |
| `JOB_HANDLERS` | `tiqora.jobs.registry` | `core.jobs.handlers` | `JobHandler` | `.kind` |

Later waves add points the same way (`core.dashboard.widgets`, `core.reporting.sources`, `core.process.actions`, …). Plugins (wave 2) call the same `registry.add(point, item, owner="<plugin id>")` from `register(registry)`.

```python
# backend/src/tiqora/channels/registry.py
CHANNEL_CONFIG_KEYS: Final[dict[str, set[str]]]   # moved verbatim from api/v1/admin/channels.py (which re-imports it)

@dataclass(frozen=True, slots=True)
class ChannelDescriptor:
    key: str                         # "sms" | "whatsapp" | "phone" | "telegram"
    config_keys: frozenset[str]      # tiqora_settings keys channel.<key>.<name>

CHANNELS: ExtensionPoint[ChannelDescriptor]
def core_channel_descriptors() -> list[ChannelDescriptor]   # one per CHANNEL_CONFIG_KEYS entry, sorted by key
```

Admin API: `GET /api/v1/admin/extensions` → `list[ExtensionPointOut]`:

```python
class ExtensionItemOut(BaseModel):
    key: str
    owner: str
class ExtensionPointOut(BaseModel):
    key: str
    description: str
    sdk_stable: bool
    items: list[ExtensionItemOut]
```

### D2. Settings sections — `tiqora.settings_sections`

```python
# backend/src/tiqora/settings_sections/section.py
SECRET_MASK: Final = "********"        # same convention as api/v1/admin/channels.py

class UnsupportedSettingsSchema(ValueError): ...

@dataclass(frozen=True, slots=True)
class SettingsSection:
    key: str                           # [a-z][a-z0-9_]*(\.[a-z0-9_]+)*, also the storage prefix
    model: type[BaseModel]             # every field must have a default
    title_key: str                     # i18n key, e.g. "admin.settings.jobs.title"
    i18n_prefix: str                   # labels: f"{i18n_prefix}.fields.{field}.label" / ".help"
                                       # enum option labels: f"{i18n_prefix}.fields.{field}.options.{value}"
    storage_keys: Mapping[str, str] = field(default_factory=dict)   # field -> explicit legacy key
    def storage_key(self, field_name: str) -> str: ...  # storage_keys.get(f) or f"{key}.{f}"
    def secret_fields(self) -> frozenset[str]: ...     # fields typed SecretStr or SecretStr | None
    def json_schema(self) -> dict[str, Any]: ...       # model.model_json_schema()

SETTINGS_SECTIONS: ExtensionPoint[SettingsSection]
def validate_section_schema(section: SettingsSection) -> None
    # raises UnsupportedSettingsSchema unless every field is one of:
    # bool, int, float, str, SecretStr, Literal[str, ...], StrEnum, list[str], list[Literal[...]],
    # or `X | None` of those; and every field has a default.
```

Supported JSON-schema subset (backend validator and frontend `schemaToFieldDefs` agree on it): `type` boolean/integer/number/string/array; `enum` or `anyOf` of `const`; `$ref` into `#/$defs/*` one level deep (Pydantic enums); `anyOf: [X, {"type": "null"}]` for optional; `format: "password"` (SecretStr) → secret; `format: "date"`/`"date-time"` → date/datetime; `minimum`/`maximum`/`maxLength`; `items` for arrays; extension keyword `"x-widget": "textarea"` via `Field(json_schema_extra={"x-widget": "textarea"})`.

```python
# backend/src/tiqora/settings_sections/store.py
class SettingsValidationError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None: ...   # field -> message
    errors: dict[str, str]

def encode_setting_value(value: Any) -> str      # bool -> "1"/"0"; int/float -> str(); str -> as-is; list -> json.dumps(list)
async def load_section_values(session: AsyncSession, section: SettingsSection, *, secret_key: str) -> BaseModel
    # missing row -> field default; bool rows read with get_setting_bool's truthy set; list rows json;
    # a row that fails validation -> default + structlog warning "settings_value_invalid" (never raises);
    # secrets decrypted with tiqora.crypto.secret.decrypt_secret (undecryptable -> treated as unset + warning)
async def read_section_for_admin(session: AsyncSession, section: SettingsSection) -> tuple[dict[str, Any], list[str]]
    # (values, secrets_set): non-secret values as JSON-able python; secret fields -> SECRET_MASK if a row exists else None
async def save_section_values(session: AsyncSession, section: SettingsSection, changes: Mapping[str, Any], *, secret_key: str) -> None
    # partial update; unknown field -> SettingsValidationError({field: "Unbekanntes Feld"})
    # secret field: absent or == SECRET_MASK -> unchanged; "" or None -> row deleted; other -> encrypt_secret()
    # validates the MERGED model (current values + changes) -> SettingsValidationError(field -> pydantic msg)
    # writes with session.add / delete + flush; NEVER commits
```

Core sections:

```python
# backend/src/tiqora/jobs/settings.py
class JobsSettings(BaseModel):
    max_concurrency: int = Field(2, ge=1, le=8)
    retention_days: int = Field(14, ge=1, le=365)
JOBS_SECTION = SettingsSection(key="jobs", model=JobsSettings,
    title_key="admin.settings.jobs.title", i18n_prefix="admin.settings.jobs")

# backend/src/tiqora/avatars/settings.py
class AvatarSettings(BaseModel):          # moved from api/v1/admin/avatars.py
    gravatar_enabled: bool = True
    favicon_enabled: bool = True
AVATARS_SECTION = SettingsSection(key="avatars", model=AvatarSettings,
    title_key="admin.settings.avatars.title", i18n_prefix="admin.settings.avatars",
    storage_keys={"gravatar_enabled": KEY_AVATARS_GRAVATAR_ENABLED,   # "avatars.gravatar.enabled"
                  "favicon_enabled": KEY_AVATARS_FAVICON_ENABLED})    # "avatars.favicon.enabled"
```

Storage-key rule: a section owns `<section.key>.<field>`; existing `KEY_*` constants in `tiqora.domain.settings_store` are reused only through `storage_keys` (explicit). A test asserts no two registered fields share a storage key and no default storage key equals a `KEY_*` value.

Admin API (`backend/src/tiqora/api/v1/admin/settings_sections.py`, router `prefix="/settings-sections"`, `tags=["admin:settings"]`, all routes `AdminUser`):

```python
class SettingsSectionSummaryOut(BaseModel):
    key: str
    title_key: str
    owner: str
class SettingsSectionOut(BaseModel):
    key: str
    title_key: str
    i18n_prefix: str
    json_schema: dict[str, Any]
    values: dict[str, Any]
    secrets_set: list[str]
class SettingsSectionUpdate(BaseModel):
    values: dict[str, Any]

GET /api/v1/admin/settings-sections            -> list[SettingsSectionSummaryOut]
GET /api/v1/admin/settings-sections/{key}      -> SettingsSectionOut           (404 unknown key)
PUT /api/v1/admin/settings-sections/{key}      -> SettingsSectionOut           (commits)
    422 detail: {"message": "Bitte die markierten Felder prüfen.", "errors": {field: message}}
```

`GET/PUT /api/v1/admin/avatars` stay (same request/response shape) but read/write through `AVATARS_SECTION`.

### D3. Jobs — `tiqora.jobs`

Tables (one migration, Task 4):

```
tiqora_job
  id               BIGINT PK autoincrement
  kind             VARCHAR(100) NOT NULL          -- JobHandler.kind
  status           VARCHAR(20)  NOT NULL          -- queued | running | succeeded | failed | cancelled
  payload          TEXT NOT NULL                  -- JSON of the handler's payload model
  result           TEXT NULL (MEDIUMTEXT on mysql/mariadb)   -- JSON returned by the handler, ≤ 1 MB
  error            TEXT NULL                      -- plain-words message for the UI; never a traceback
  progress_done    INT NOT NULL default 0
  progress_total   INT NULL
  progress_message VARCHAR(500) NULL
  cancel_requested BOOL NOT NULL default false
  dedupe_key       VARCHAR(200) NULL
  created_by       INT NULL                       -- users.id (soft reference, no FK); NULL = system
  worker_id        VARCHAR(100) NULL              -- "<hostname>:<pid>" of the claiming worker
  created_at       DATETIME NOT NULL              -- app-written naive UTC
  started_at       DATETIME NULL
  heartbeat_at     DATETIME NULL
  finished_at      DATETIME NULL
  INDEX ix_tiqora_job_status_created (status, created_at)
  INDEX ix_tiqora_job_created_by (created_by, created_at)
  INDEX ix_tiqora_job_kind_dedupe (kind, dedupe_key)
  INDEX ix_tiqora_job_finished (finished_at)

tiqora_job_file
  id           BIGINT PK autoincrement
  job_id       BIGINT NOT NULL                    -- soft reference to tiqora_job.id
  name         VARCHAR(255) NOT NULL
  content_type VARCHAR(100) NOT NULL
  size_bytes   BIGINT NOT NULL
  data         BYTEA / LONGBLOB (mysql, mariadb) NOT NULL
  created_at   DATETIME NOT NULL
  INDEX ix_tiqora_job_file_job (job_id)
```

ORM classes in `backend/src/tiqora/db/tiqora/models.py`: `TiqoraJob`, `TiqoraJobFile`; module constants `JOB_RESULT_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")`, `JOB_FILE_BLOB = LargeBinary().with_variant(mysql.LONGBLOB(), "mysql", "mariadb")`. `TiqoraJobFile.data` is `deferred()` so listing files never loads blobs.

```python
# backend/src/tiqora/jobs/registry.py
JobRun = Callable[["JobContext", Any], Awaitable[dict[str, Any] | None]]

@dataclass(frozen=True, slots=True)
class JobHandler:
    kind: str                      # "<owner>.<name>", e.g. "core.ticket_export"
    payload_model: type[BaseModel]
    run: JobRun                    # receives the validated payload model instance
    title_key: str                 # frontend i18n key, e.g. "jobs.kinds.core.ticket_export"
    timeout_seconds: int = 3600

JOB_HANDLERS: ExtensionPoint[JobHandler]

# backend/src/tiqora/jobs/context.py
class JobCancelled(Exception): ...
class JobFailed(Exception):
    def __init__(self, message: str) -> None: ...   # message is shown to the user as-is (German, plain words)
    message: str

class JobContext:
    job_id: int
    kind: str
    created_by: int | None
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    async def progress(self, done: int, total: int | None = None, message: str | None = None) -> None
        # writes progress_* + heartbeat_at; throttled to one write per 1.0 s unless done == total;
        # then await self.check_cancelled()
    async def check_cancelled(self) -> None          # raises JobCancelled when cancel_requested is set
    async def add_file(self, name: str, content_type: str, data: bytes) -> int
        # inserts tiqora_job_file (own session, commit); > MAX_JOB_FILE_BYTES (200 MiB) -> JobFailed

# backend/src/tiqora/jobs/service.py
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
ACTIVE_STATUSES: frozenset[str] = frozenset({"queued", "running"})
TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})
MAX_RESULT_CHARS = 1_000_000
class UnknownJobKind(ValueError): ...
class JobNotCancellable(Exception): ...

def utc_now() -> datetime                         # datetime.now(UTC).replace(tzinfo=None)
async def enqueue_job(session: AsyncSession, *, kind: str, payload: BaseModel | Mapping[str, Any],
                      created_by: int | None, dedupe_key: str | None = None,
                      registry: Registry | None = None) -> TiqoraJob
    # registry defaults to get_registry(); unknown kind -> UnknownJobKind; payload validated with
    # handler.payload_model (pydantic.ValidationError propagates); dedupe: an ACTIVE job with the same
    # (kind, dedupe_key) is returned instead of a new one; session.add + flush; NEVER commits
async def get_job(session: AsyncSession, job_id: int) -> TiqoraJob | None
async def list_jobs(session: AsyncSession, *, created_by: int | None, limit: int = 50) -> list[TiqoraJob]
    # created_by None = all jobs (admin); newest first
async def list_job_files(session: AsyncSession, job_ids: Sequence[int]) -> dict[int, list[TiqoraJobFile]]
async def get_job_file(session: AsyncSession, job_id: int, file_id: int) -> TiqoraJobFile | None   # loads data
async def request_cancel(session: AsyncSession, job: TiqoraJob) -> TiqoraJob
    # queued -> status cancelled + finished_at; running -> cancel_requested True;
    # terminal -> JobNotCancellable; flush, NEVER commits
async def purge_finished_jobs(session: AsyncSession, *, older_than: datetime) -> int
    # deletes files of, then the jobs with finished_at < older_than; returns jobs deleted; flush only
def can_view_job(job: TiqoraJob, *, user_id: int, is_admin: bool) -> bool   # creator or admin

# backend/src/tiqora/jobs/runner.py
HEARTBEAT_SECONDS = 30
STALE_AFTER_SECONDS = 180
PURGE_EVERY_SECONDS = 3600
STATUS_EVERY_SECONDS = 30
INTERRUPTED_MESSAGE = ("Die Aufgabe wurde unterbrochen, weil der Hintergrunddienst neu gestartet "
                       "wurde. Bitte erneut starten.")
TIMEOUT_MESSAGE = "Die Aufgabe hat zu lange gedauert und wurde abgebrochen."
FAILED_MESSAGE = ("Die Aufgabe ist mit einem Fehler abgebrochen. Details stehen im Protokoll "
                  "des Hintergrunddienstes.")
UNKNOWN_KIND_MESSAGE = "Diese Aufgabe kennt der Hintergrunddienst nicht (mehr)."
RESULT_TOO_LARGE_MESSAGE = "Das Ergebnis ist zu groß."

class JobRunner:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession], settings: Settings,
                 registry: Registry, worker_id: str | None = None) -> None   # default f"{socket.gethostname()}:{os.getpid()}"
    async def run_once(self) -> dict[str, int]
        # 1. reap: status running AND heartbeat_at < utc_now() - STALE_AFTER -> failed, INTERRUPTED_MESSAGE
        # 2. purge: at most every PURGE_EVERY_SECONDS, purge_finished_jobs(older_than=now - retention_days)
        # 3. free = JobsSettings.max_concurrency - active tasks; candidates: status queued ORDER BY id LIMIT free;
        #    claim each with UPDATE ... SET status='running', worker_id, started_at, heartbeat_at
        #    WHERE id=:id AND status='queued' AND cancel_requested = false; rowcount 1 -> asyncio.create_task(_execute)
        # returns {"reaped": n, "purged": n, "started": n, "running": len(active)}
    async def run_forever(self, stop: asyncio.Event, *, poll_seconds: float) -> None
        # loop run_once; record_tick_status("jobs", ...) at most every STATUS_EVERY_SECONDS;
        # on stop: await self.shutdown(grace_seconds=20)
    async def wait_idle(self) -> None              # await every active task (tests)
    async def shutdown(self, grace_seconds: float) -> None
        # stop claiming; wait up to grace; cancel remaining tasks; mark their rows failed INTERRUPTED_MESSAGE
```

Outcome mapping inside `_execute` (final write is conditional `WHERE id=:id AND status='running' AND worker_id=:me`, so a reaped job is never overwritten):

| Handler outcome | status | error |
|---|---|---|
| returns `dict` / `None` | `succeeded` | — (`result` = JSON or NULL; > `MAX_RESULT_CHARS` → `failed`, `RESULT_TOO_LARGE_MESSAGE`) |
| raises `JobCancelled` | `cancelled` | — |
| raises `JobFailed(msg)` | `failed` | `msg` |
| exceeds `timeout_seconds` | `failed` | `TIMEOUT_MESSAGE` |
| any other exception | `failed` | `FAILED_MESSAGE` (+ `logger.exception("job_failed", job_id=…, kind=…)`) |
| kind not registered | `failed` | `UNKNOWN_KIND_MESSAGE` |

Worker: `backend/src/tiqora/worker/__main__.py` runs `JobRunner(...).run_forever(stop, poll_seconds=settings.jobs_poll_interval_seconds)` inside `_run_all_loops`. Config: `jobs_poll_interval_seconds: int = Field(2, ge=1, le=60)` in `tiqora.config.Settings` (env `TIQORA_JOBS_POLL_INTERVAL_SECONDS`). Catalog: `DaemonService(slug="jobs", enabled_key=None, default_enabled=True, toggleable=False, schedule_kind="interval", interval_key=None, interval_settings_attr="jobs_poll_interval_seconds", tick_timeout_seconds=STATUS_EVERY_SECONDS)`. Jobs run only in the `worker` process (not `ai-worker`, not `api`). Multiple worker replicas are safe (conditional claim).

API (`backend/src/tiqora/api/v1/jobs.py`, `APIRouter(prefix="/jobs", tags=["jobs"])`; schemas in `backend/src/tiqora/jobs/schemas.py`):

```python
class JobFileOut(BaseModel):
    id: int
    name: str
    content_type: str
    size_bytes: int
class JobOut(BaseModel):
    id: int
    kind: str
    title_key: str | None                 # from the registry; None if the kind is unknown
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress_done: int
    progress_total: int | None
    progress_message: str | None
    cancel_requested: bool
    result: dict[str, Any] | None
    error: str | None
    files: list[JobFileOut]
    created_by: int | None
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
class JobAccepted(BaseModel):             # 202 body of every feature endpoint that starts a job
    job_id: int
    status: Literal["queued", "running"]
def job_out(job: TiqoraJob, files: list[TiqoraJobFile], registry: Registry) -> JobOut

GET  /api/v1/jobs?all=false&limit=50              -> list[JobOut]   (all=true: admin only, else 403)
GET  /api/v1/jobs/{job_id}                        -> JobOut         (404 if missing OR not visible)
POST /api/v1/jobs/{job_id}/cancel                 -> JobOut         (409 {"message": "Die Aufgabe ist bereits beendet."})
GET  /api/v1/jobs/{job_id}/files/{file_id}        -> bytes, Content-Type from row,
                                                     Content-Disposition: attachment; filename*=UTF-8''<quoted name>
```

There is deliberately no generic "create job" endpoint: features start jobs from their own permission-checked routes and return `202 JobAccepted`.

First handler (Task 10):

```python
# backend/src/tiqora/jobs/handlers/ticket_export.py
class TicketExportPayload(BaseModel):
    user_id: int
    filter: Filter
    include_archived: bool = False
    sort: str = "age"
    order: Literal["asc", "desc"] = "desc"
async def run_ticket_export(ctx: JobContext, payload: TicketExportPayload) -> dict[str, Any]   # {"rows": n}
TICKET_EXPORT_JOB = JobHandler(kind="core.ticket_export", payload_model=TicketExportPayload,
    run=run_ticket_export, title_key="jobs.kinds.core.ticket_export", timeout_seconds=1800)

POST /api/v1/tickets/export-jobs   body TicketExportJobIn -> 202 JobAccepted
class TicketExportJobIn(BaseModel):
    params: TicketListParams
    sort: str = "age"
    order: Literal["asc", "desc"] = "desc"
```

### D4. Filter language — `tiqora.filters`

JSON shape (version 1):

```json
{"version": 1,
 "where": {"all": [
   {"field": "queue_id", "op": "in", "value": [3, 7]},
   {"any": [
     {"field": "state_type", "op": "in", "value": ["new", "open"]},
     {"field": "escalated", "op": "is_true"}
   ]},
   {"not": {"field": "df.Kundennummer", "op": "is_empty"}},
   {"field": "created", "op": "within_last", "value": {"amount": 7, "unit": "day"}}
 ]}}
```

```python
# backend/src/tiqora/filters/model.py
ConditionOp = Literal["eq", "neq", "in", "not_in", "contains", "starts_with", "is_empty",
                      "is_not_empty", "lt", "lte", "gt", "gte", "between", "before", "after",
                      "within_last", "older_than", "is_true", "is_false"]
class Condition(BaseModel):          # model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=120)
    op: ConditionOp
    value: Any = None
class AllOf(BaseModel):              # extra="forbid"
    all: list[FilterNode] = Field(min_length=1)
class AnyOf(BaseModel):              # extra="forbid"
    any: list[FilterNode] = Field(min_length=1)
class Not(BaseModel):                # extra="forbid", populate_by_name=True
    not_: FilterNode = Field(alias="not")
FilterNode = AllOf | AnyOf | Not | Condition
class Filter(BaseModel):             # extra="forbid"
    version: Literal[1] = 1
    where: FilterNode | None = None   # None = no condition (scope only)
    # serialise with model_dump(by_alias=True, exclude_none=True)
```

Value shapes per op: scalar for `eq neq contains starts_with lt lte gt gte before after`; non-empty list (≤ `MAX_IN_VALUES`) for `in not_in`; `None` for `is_empty is_not_empty is_true is_false`; `{"from": X, "to": Y}` for `between` (numbers: `from <= v <= to`; datetimes: `from <= v < to`); `{"amount": int ≥ 1, "unit": "minute"|"hour"|"day"|"week"}` for `within_last` (`v >= now - d`) and `older_than` (`v < now - d`). Datetimes are ISO-8601 strings; a value with an offset is converted to naive UTC, a naive value is taken as UTC. `neq` / `not_in` include rows where the column is NULL (`col IS NULL OR col <> v`) — on dynamic fields they mean "no row with that value" (`NOT EXISTS`).

```python
# backend/src/tiqora/filters/catalog.py
FieldType = Literal["id", "string", "int", "datetime", "bool", "enum"]
OPS_BY_TYPE: dict[FieldType, frozenset[str]] = {
    "id": frozenset({"eq", "neq", "in", "not_in"}),
    "string": frozenset({"eq", "neq", "in", "not_in", "contains", "starts_with", "is_empty", "is_not_empty"}),
    "int": frozenset({"eq", "neq", "lt", "lte", "gt", "gte", "between", "in"}),
    "datetime": frozenset({"before", "after", "between", "within_last", "older_than", "is_empty", "is_not_empty"}),
    "bool": frozenset({"is_true", "is_false"}),
    "enum": frozenset({"eq", "neq", "in", "not_in"}),
}
MAX_DEPTH = 6
MAX_CONDITIONS = 100
MAX_IN_VALUES = 1000

@dataclass(frozen=True, slots=True)
class FilterFieldSpec:
    key: str
    type: FieldType
    label_key: str | None = None       # frontend i18n key ("ticketFilter.fields.queue_id")
    label: str | None = None           # literal label (dynamic fields use the DF label)
    ops: frozenset[str] | None = None  # None -> OPS_BY_TYPE[type]
    enum_values: tuple[str, ...] = ()
    options_source: str | None = None  # key for POST /reference/field-options ("queue", "DynamicField_Area")
    def allowed_ops(self) -> frozenset[str]: ...

class FilterError(ValueError):
    def __init__(self, message: str, *, path: str) -> None: ...   # path like "where.all[2].value"
    message: str
    path: str

def validate_filter(flt: Filter, resolve_field: Callable[[str], FilterFieldSpec | None]) -> None
def iter_conditions(node: FilterNode | None) -> Iterator[tuple[str, Condition]]   # (path, condition)
def relative_delta(value: Any, *, path: str) -> timedelta
def parse_datetime_value(value: Any, *, path: str) -> datetime                     # naive UTC
```

```python
# backend/src/tiqora/filters/tickets.py
@dataclass(frozen=True, slots=True)
class TicketQueryScope:
    user_id: int
    queue_ids: frozenset[int]          # valid queues the user holds `permission` on
    permission: str = "ro"
    include_archived: bool = False

async def ticket_query_scope(session: AsyncSession, user_id: int, *, permission: str = "ro",
                             include_archived: bool = False) -> TicketQueryScope
TICKET_FILTER_FIELDS: dict[str, FilterFieldSpec]               # static fields below
DF_FIELD_PREFIX = "df."
async def ticket_filter_fields(session: AsyncSession) -> list[FilterFieldSpec]   # static + one per valid ticket DF
async def compile_ticket_filter(session: AsyncSession, flt: Filter, scope: TicketQueryScope,
                                *, now: datetime | None = None) -> Select[tuple[Ticket]] | None
    # None = guaranteed empty (empty scope). Scope (queue IN-list, archive_flag) is ANDed outside the
    # user's expression, so no `any`/`not` can widen it. Raises FilterError (callers -> 422).
async def count_tickets(session: AsyncSession, flt: Filter, scope: TicketQueryScope,
                        *, now: datetime | None = None) -> int
```

Static ticket fields:

| key | type | column / meaning | options_source |
|---|---|---|---|
| `ticket_id` | id | `ticket.id` | — |
| `tn` | string | `ticket.tn` | — |
| `title` | string | `ticket.title` | — |
| `queue_id` | id | `ticket.queue_id` | `queue` |
| `state_id` | id | `ticket.ticket_state_id` | `state` |
| `state_type` | string, ops `eq neq in not_in` | `ticket_state_type.name` via preloaded state ids | — |
| `priority_id` | id | `ticket.ticket_priority_id` | `priority` |
| `type_id` | id | `ticket.type_id` | `type` |
| `service_id` | id | `ticket.service_id` | `service` |
| `sla_id` | id | `ticket.sla_id` | `sla` |
| `owner_id` | id | `ticket.user_id` | — |
| `responsible_id` | id | `ticket.responsible_user_id` | — |
| `customer_id` | string | `ticket.customer_id` | — |
| `customer_user_id` | string | `ticket.customer_user_id` | — |
| `locked` | bool | `is_true`: lock type name in {lock, tmp_lock}; `is_false`: lock type name == unlock (same as `_filtered_ticket_stmt`) | — |
| `watched_by` | id, ops `eq in` | `ticket_watcher.user_id` (preloaded ticket ids → IN-list) | — |
| `unassigned` | bool | `ticket.user_id == 1` | — |
| `escalated` | bool | any `escalation_*` epoch in the past OR AI handoff (`tiqora_ai_ticket_state`, preloaded ids → IN-list) | — |
| `ai_escalated` | bool | AI handoff only | — |
| `escalation_due_within` | int (seconds), ops `lte` | any `escalation_*` epoch set and `< now + value` | — |
| `channel` | enum `email telegram webchat phone`, ops `eq in` | `ticket_service._channel_condition` | — |
| `archived` | bool | `ticket.archive_flag == 1` | — |
| `created` | datetime | `ticket.create_time` | — |
| `changed` | datetime | `ticket.change_time` | — |
| `pending_until` | datetime | `ticket.until_time` (epoch; 0 = empty) | — |
| `df.<Name>` | Text/TextArea/Dropdown/Multiselect → string, Checkbox → bool, Date/DateTime → datetime | `EXISTS` on `dynamic_field_value` (`value_text` / `value_int` / `value_date`) | `DynamicField_<Name>` for Dropdown/Multiselect |

```python
# backend/src/tiqora/filters/ticket_params.py — bridge from today's list query params
class TicketListParams(BaseModel):
    queue_id: int | None = None
    state_id: int | None = None
    state_type: str | None = None          # incl. view names from ticket_service.VIEW_STATE_TYPES
    owner_id: int | None = None
    customer_id: str | None = None
    customer_user_id: str | None = None
    responsible_id: int | None = None
    service_id: int | None = None
    locked: bool | None = None
    watcher_user_id: int | None = None
    escalated: bool | None = None          # only True filters (route semantics)
    ai_escalated: bool | None = None       # only True filters
    unassigned: bool | None = None
    escalating_within: int | None = Field(None, ge=0)
    channel: list[Literal["email", "telegram", "webchat", "phone"]] | None = None
    include_archived: bool = False
def filter_from_list_params(params: TicketListParams) -> Filter
```

API (`backend/src/tiqora/api/v1/ticket_filters.py`, `APIRouter(prefix="/ticket-filters", tags=["tickets"])`):

```python
class FilterFieldOut(BaseModel):
    key: str
    type: Literal["id", "string", "int", "datetime", "bool", "enum"]
    ops: list[str]
    label_key: str | None
    label: str | None
    enum_values: list[str]
    options_source: str | None
class TicketFilterCountIn(BaseModel):
    filter: Filter
    include_archived: bool = False        # honoured for admins only (like GET /tickets)
class TicketFilterCountOut(BaseModel):
    count: int

GET  /api/v1/ticket-filters/fields  -> list[FilterFieldOut]
POST /api/v1/ticket-filters/count   -> TicketFilterCountOut
     422 detail: {"message": FilterError.message, "path": FilterError.path}
```

Existing `GET /api/v1/tickets` and `TicketService._filtered_ticket_stmt` are NOT rewritten in this wave; the parity test in Task 9 is the gate for moving them later.

### D5. Typed field engine

Backend — Znuny-identical dynamic-field writes (`backend/src/tiqora/domain/dynamic_field_values.py`):

```python
DF_TEXT_TYPES = frozenset({"Text", "TextArea", "Dropdown", "Multiselect"})
DF_EDITABLE_TYPES = frozenset({"Text", "TextArea", "Dropdown", "Multiselect", "Checkbox", "Date", "DateTime"})
HISTORY_MAX_CHARS = 166

class DynamicFieldValueError(ValueError): ...          # plain German message

@dataclass(frozen=True, slots=True)
class DynamicFieldConfig:
    id: int
    name: str
    field_type: str

async def load_ticket_dynamic_field(session: AsyncSession, name: str) -> DynamicFieldConfig | None   # valid, object_type Ticket
def normalize_df_values(field_type: str, values: Sequence[str]) -> list[str]
    # drops "" ; Date: "YYYY-MM-DD" or "YYYY-MM-DD 00:00:00" -> "YYYY-MM-DD 00:00:00", other time -> error;
    # DateTime: "YYYY-MM-DD HH:MM[:SS]" or "YYYY-MM-DDTHH:MM[:SS]" -> "YYYY-MM-DD HH:MM:SS";
    # Checkbox: "0"/"1" (also "" -> []), anything else -> error; Text/TextArea/Dropdown/Date/DateTime/Checkbox:
    # more than one value -> error; Multiselect: many
async def get_df_values(session: AsyncSession, field: DynamicFieldConfig, object_id: int) -> list[str]
    # value_text | value_int (str) | value_date ("YYYY-MM-DD HH:MM:SS"), ORDER BY id
def df_values_differ(old: Sequence[str], new: Sequence[str]) -> bool   # both empty -> False; else list inequality
def readable_history_value(field_type: str, values: Sequence[str]) -> str
    # Date -> first 10 chars; DateTime -> first 16 chars; Multiselect -> ", ".join; else first value or ""
def truncate_history_parts(field_name: str, value: str, old_value: str) -> tuple[str, str, str]
    # exact port of the 166-char rule in Kernel/System/DynamicField/ObjectType/Ticket.pm PostValueSet
async def set_ticket_dynamic_field(session: AsyncSession, *, ticket_id: int, field: DynamicFieldConfig,
                                   values: Sequence[str], user_id: int) -> bool
    # normalize -> old = get -> not differ: return False (no writes) -> DELETE rows -> INSERT one row per value
    # into value_text | value_date | value_int -> UPDATE ticket SET change_time = current_timestamp, change_by = :uid
    # -> add_dynamic_field_update(...truncate_history_parts(name, readable(new), readable(old)))
    # -> invalidate_ticket_cache -> _emit_event("TicketDynamicFieldUpdate", ...) ; returns True
```

`tiqora.domain.ticket_write_service.update_dynamic_field(...)` keeps its signature and delegates (unknown field → silently ignored as today; `DynamicFieldValueError` → `InvalidInput`). `MutationRequest` gains `dynamic_fields: dict[str, list[str]] | None = None` (several fields, one transaction).

Backend — options (`backend/src/tiqora/domain/field_options.py`):

```python
ATTRIBUTE_OPTION_FIELDS: tuple[str, ...] = ("state", "queue", "priority", "type", "service", "sla")
DF_OPTION_PREFIX = "DynamicField_"
class FieldOptionsContext(BaseModel):
    action: str = "AgentTicketZoom"        # Znuny frontend Action for ACL Properties
    ticket_id: int | None = None
    queue_id: int | None = None
    service_id: int | None = None
    type_id: int | None = None
    state_id: int | None = None
    priority_id: int | None = None
    sla_id: int | None = None
    customer_user_id: str | None = None
    dynamic_fields: dict[str, list[str]] = Field(default_factory=dict)   # current form values by DF name
class FieldOptionsRequest(BaseModel):
    fields: list[str] = Field(min_length=1, max_length=50)   # "queue", "state", …, "DynamicField_<Name>"
    context: FieldOptionsContext = Field(default_factory=FieldOptionsContext)
class FieldOptionOut(BaseModel):
    value: str
    label: str
class FieldOptionsOut(BaseModel):
    options: dict[str, list[FieldOptionOut]]   # ordered lists; unknown / non-option fields are omitted
async def collect_field_options(session: AsyncSession, user: AuthenticatedUser,
                                req: FieldOptionsRequest) -> FieldOptionsOut
```

Attribute fields reuse `api/v1/reference.collect_ticket_field_options` (order: as that function returns). Dynamic fields: Dropdown/Multiselect `PossibleValues` from the DF config, sorted by label like Znuny's `BuildSelection` default (verify in `znuny-6.5.22/Kernel/Output/HTML/Layout.pm` `_BuildSelectionDataRefCreate`), filtered with `ticket_acl.apply_ticket_acl(return_type="Ticket", return_sub_type=f"DynamicField_{name}", …)` — NOT `filter_id_name_map` (it casts keys to int). ACL checks include `DynamicField_<name>` from `context.dynamic_fields`.

Endpoint: `POST /api/v1/reference/field-options` body `FieldOptionsRequest` → `FieldOptionsOut` (404/403 like `GET /tickets/{id}` when `context.ticket_id` is not readable). `DynamicFieldScreen` in `api/v1/reference.py` gains `"AgentTicketFreeText"` (Znuny's "Freie Felder" screen = which fields are editable in zoom; level 1 shown, 2 required). Existing `GET /reference/ticket-field-options` and `GET /tickets/{id}/field-options` stay unchanged.

Frontend (`frontend/src/components/fields/`):

```ts
// types.ts
export type FieldKind = "text" | "textarea" | "number" | "boolean" | "select" | "multiselect"
  | "date" | "datetime" | "secret" | "tags";
export type FieldValue = string | number | boolean | string[] | null;
// date: "YYYY-MM-DD"; datetime: "YYYY-MM-DDTHH:MM" (wall time in the agent's display zone)
export type FieldValues = Record<string, FieldValue>;
export type FieldErrors = Record<string, string>;
export type FieldOption = { value: string; label: string; disabled?: boolean; hint?: string };
export type FieldDef = {
  key: string;
  kind: FieldKind;
  label: string;                 // already translated
  help?: string;
  required?: boolean;
  readOnly?: boolean;
  placeholder?: string;
  options?: FieldOption[];       // static options / tag suggestions
  optionsKey?: string;           // key for POST /reference/field-options; loaded options win over `options`
  allowEmpty?: boolean;          // select: offers the "–" entry
  min?: number;
  max?: number;
  step?: number;
  maxLength?: number;
  secretSet?: boolean;           // secret: a value is stored on the server
};
export type FieldKindProps = {
  def: FieldDef; value: FieldValue; onChange: (value: FieldValue) => void;
  id: string; testId: string; disabled?: boolean; invalid?: boolean; options: FieldOption[];
};
export type FieldKindRegistration = { kind: string; component: React.ComponentType<FieldKindProps> };

// kinds.tsx
export const FIELD_KINDS: ExtensionPoint<FieldKindRegistration>;   // key "core.fields.kinds"
export function registerCoreFieldKinds(): void;                     // idempotent; called by FieldInput module

// FieldInput.tsx
export function FieldInput(props: { def: FieldDef; value: FieldValue; onChange: (v: FieldValue) => void;
  testId: string; options?: FieldOption[]; disabled?: boolean; invalid?: boolean }): JSX.Element;

// FieldsForm.tsx
export function FieldsForm(props: { fields: FieldDef[]; values: FieldValues;
  onChange: (key: string, value: FieldValue) => void; errors?: FieldErrors;
  optionsByKey?: Record<string, FieldOption[]>; disabled?: boolean; columns?: 1 | 2;
  testId?: string /* default "fields" */ }): JSX.Element | null;
// test ids: control `${testId}-${key}`, error `${testId}-${key}-error`,
//           select options `${testId}-${key}-menu-option-${value}` (SelectField convention)

// validate.ts
export function isEmptyFieldValue(value: FieldValue | undefined): boolean;
export function validateFieldValues(fields: FieldDef[], values: FieldValues, t: TFunction): FieldErrors;

// dynamicFields.ts
export const DF_KIND: Record<string, FieldKind>; // Text→text, TextArea→textarea, Dropdown→select,
  // Multiselect→multiselect, Checkbox→boolean, Date→date, DateTime→datetime
export function dynamicFieldToDef(df: DynamicFieldDef): FieldDef;   // optionsKey `DynamicField_${name}`, allowEmpty true
export function dfToFieldValue(kind: FieldKind, raw: readonly unknown[] | undefined, zone?: string): FieldValue;
export function fieldValueToDf(kind: FieldKind, value: FieldValue, zone?: string): string[];
// datetime: Znuny stores UTC ("YYYY-MM-DD HH:MM:SS"); engine value is wall time in `zone`
// (default displayTimeZone()) via lib/timeZone toZonedInputValue / fromZonedInputValue.

// DynamicFieldsForm.tsx — same props as the deleted DynamicFieldInputs
export type DynamicFieldValues = Record<string, string[]>;
export function DynamicFieldsForm(props: { fields: DynamicFieldDef[]; values: DynamicFieldValues;
  onChange: (name: string, values: string[]) => void; testId?: string /* default "df" */;
  optionsByKey?: Record<string, FieldOption[]>; errors?: FieldErrors; disabled?: boolean }): JSX.Element | null;

// useFieldOptions.ts
export type FieldOptionsContext = Schemas["FieldOptionsContext"];
export function useFieldOptions(keys: string[], context: FieldOptionsContext,
  opts?: { enabled?: boolean }): { optionsByKey: Record<string, FieldOption[]>; isLoading: boolean; error: unknown };

// jsonSchema.ts
export type JsonSchema = { type?: string | string[]; enum?: unknown[]; const?: unknown; format?: string;
  title?: string; description?: string; default?: unknown; minimum?: number; maximum?: number;
  maxLength?: number; items?: JsonSchema; anyOf?: JsonSchema[]; oneOf?: JsonSchema[]; $ref?: string;
  properties?: Record<string, JsonSchema>; required?: string[]; $defs?: Record<string, JsonSchema>;
  writeOnly?: boolean; "x-widget"?: "textarea" };
export function schemaToFieldDefs(schema: JsonSchema, opts: { t: TFunction; i18nPrefix?: string;
  secretsSet?: string[] }): { fields: FieldDef[]; unsupported: string[] };
export function schemaDefaults(schema: JsonSchema): FieldValues;

// SchemaForm.tsx
export function SchemaForm(props: { schema: JsonSchema; values: FieldValues;
  onChange: (next: FieldValues, changedKey: string) => void; errors?: FieldErrors; i18nPrefix?: string;
  secretsSet?: string[]; disabled?: boolean; columns?: 1 | 2;
  testId?: string /* default "schema-form" */ }): JSX.Element;
// unsupported properties render "Dieses Feld kann hier nicht bearbeitet werden." with testid
// `${testId}-unsupported-${key}` — never crash, never silently drop.
```

Frontend extension registry (`frontend/src/extensions/registry.ts`):

```ts
export type ExtensionOwner = string;     // "core" or a plugin id
export interface ExtensionPoint<T> {
  readonly key: string;
  register(item: T, owner?: ExtensionOwner): void;   // duplicate key -> throws Error
  get(key: string): T | undefined;
  list(): T[];
  owner(key: string): ExtensionOwner | undefined;
  clearForTests(): void;
}
export function createExtensionPoint<T>(key: string, itemKey: (item: T) => string): ExtensionPoint<T>;
export function listExtensionPoints(): { key: string; items: { key: string; owner: ExtensionOwner }[] }[];
```

Ticket zoom: `frontend/src/components/agent/TicketDynamicFields.tsx`:

```ts
export function TicketDynamicFields(props: { ticket: TicketDetail }): JSX.Element | null;
// read mode: valued fields (ticket.dynamic_fields), DateTime shown in the display zone;
// "Bearbeiten" (testid "ticket-df-edit") when ticket.permissions.rw AND the AgentTicketFreeText screen
// enables >= 1 field; edit mode: DynamicFieldsForm (testId "ticket-df") over every enabled field
// (empty ones included) with ACL options from useFieldOptions(context {ticket_id, action: "AgentTicketFreeText"});
// "Speichern" (testid "ticket-df-save") sends PATCH /tickets/{id} {dynamic_fields: {changed only}}.
```

### D6. Jobs frontend

```ts
// frontend/src/lib/jobsApi.ts
export type Job = Schemas["JobOut"];
export type JobAccepted = Schemas["JobAccepted"];
export type TicketExportJobIn = Schemas["TicketExportJobIn"];
export const TERMINAL_JOB_STATUSES: ReadonlySet<Job["status"]>;
export const jobsApi = {
  get(jobId: number, signal?: AbortSignal): Promise<Job>,
  cancel(jobId: number): Promise<Job>,
  list(opts?: { all?: boolean }, signal?: AbortSignal): Promise<Job[]>,
  fileUrl(jobId: number, fileId: number): string,          // "/api/v1/jobs/{id}/files/{fileId}"
  startTicketExport(body: TicketExportJobIn): Promise<JobAccepted>,
};

// frontend/src/components/jobs/useJob.ts
export function jobPollInterval(job: Job | undefined, firstSeenMs: number, nowMs: number): number | false;
  // false when terminal; 1000 ms during the first 30 s; 3000 ms after
export function useJob(jobId: number | null | undefined): {
  job: Job | undefined; isTerminal: boolean; error: unknown;
  cancel: () => Promise<void>; isCancelling: boolean };
  // react-query key ["jobs", jobId]

// frontend/src/components/jobs/JobProgress.tsx
export function JobProgress(props: { job: Job; onCancel?: () => void; cancelling?: boolean;
  testId?: string /* default "job-progress" */ }): JSX.Element;

// frontend/src/components/jobs/JobDialog.tsx
export function JobDialog(props: { jobId: number | null; title: string; onClose: () => void;
  onSucceeded?: (job: Job) => void; testId?: string /* default "job-dialog" */ }): JSX.Element | null;
```

### D7. Settings frontend

```ts
// frontend/src/lib/settingsSectionsApi.ts
export type SettingsSection = Schemas["SettingsSectionOut"];
export type SettingsSectionSummary = Schemas["SettingsSectionSummaryOut"];
export const settingsSectionsApi = {
  list(signal?: AbortSignal): Promise<SettingsSectionSummary[]>,
  get(key: string, signal?: AbortSignal): Promise<SettingsSection>,
  put(key: string, values: Record<string, unknown>): Promise<SettingsSection>,
};
export function settingsFieldErrors(err: unknown): FieldErrors;   // reads detail.errors of a 422
```

Route `/admin/settings/$section` → `frontend/src/routes/admin/SettingsSectionPage.tsx` (generic page: title from `title_key`, `SchemaForm` with `i18nPrefix`, sends only changed keys; secrets untouched are omitted). `AvatarSettingsDialog` renders `SchemaForm` for section `avatars` with `testId="admin-avatars"` (keeps the existing `admin-avatars-gravatar_enabled` / `admin-avatars-favicon_enabled` test ids). The Daemons page row "Hintergrundaufgaben" links to `/admin/settings/jobs`.

### D8. i18n namespaces (new keys; German first)

- `jobs.status.{queued,running,succeeded,failed,cancelled}` = Wartet / Läuft / Fertig / Fehlgeschlagen / Abgebrochen
- `jobs.{cancel,close,download,progressOf,elapsed,cancelRequested,loadError}`; `jobs.kinds.core.ticket_export` = "Ticket-Export"; `jobs.ticketExport.title` = "Tickets als CSV exportieren"
- `fields.{none,required,invalidNumber,tooLong,unsupported,secretStored,secretRemove,addValue,removeValue}`
- `admin.settings.{save,saved,loadError,saveError,notFound}`; `admin.settings.jobs.{title,fields.max_concurrency.label,…help,fields.retention_days.label,…help}`; `admin.settings.avatars.{title,fields.gravatar_enabled.label,…help,fields.favicon_enabled.label,…help}` (texts copied from `admin.avatars.*`)
- `admin.daemons.services.jobs.{name,description}` = "Hintergrundaufgaben" / "Führt lange Aufgaben wie Exporte im Hintergrund aus, damit die Oberfläche nicht wartet."
- `ticket.dynamicFieldsEdit.{edit,save,cancel,saved,saveError,noneEditable}`
- `ticketFilter.fields.<key>` for every static filter field key (labels for later builders)

---

## Tasks

Order: Task 1 is independent. Backend Tasks 2→10 are sequential. Frontend Tasks 11→14 need only the openapi.json of earlier backend tasks (11 needs nothing; 12 needs Task 6 + 10; 13 needs Task 3; 14 needs nothing). Tasks 15→17 are the zoom-editing chain. Task 18 closes.

### Task 1: Doc cleanup (`ArticleSend`, "Deferred" row, ACL note)

**Files:**
- Modify: `docs/compatibility.md` (rows at ~245 and ~427)
- Modify: `docs/specs/2026-07-19-tiqora-design.md` (row "Deferred" ~44, PermissionEngine note ~130-136)

**Interfaces:** none.

- [ ] **Step 1: Verify current state before editing.** Run and read:
  ```bash
  grep -n "_wants_article_send\|_deliver_article\|TimeUnit\|NoAgentNotify" backend/src/tiqora/api/compat/operations.py
  grep -n "ArticleSend" docs/crypto.md docs/process-management.md
  grep -rn "apply_ticket_acl\|filter_id_name_map" backend/src/tiqora --include='*.py' | grep -v "def "
  ls backend/src/tiqora/process backend/src/tiqora/calendar backend/src/tiqora/stats backend/src/tiqora/crypto
  grep -n "## SOAP transport" docs/compatibility.md
  ```
  Expected: `ArticleSend` is implemented for TicketCreate and TicketUpdate (agents; `_wants_article_send` + `_deliver_article`); `TimeUnit` is only read in TimeAccounting (so it stays "partial"); ACL rows are applied to option lists.
- [ ] **Step 2: `docs/compatibility.md`.** Remove the `ArticleSend` row from "TicketCreate / TicketUpdate — deferred side-effects". Add, directly above that table, one sentence: "`ArticleSend: 1` (agents, TicketCreate and TicketUpdate) sends the article as e-mail through the same outbound path as agent replies (`deliver_agent_email_reply`), including `EmailSecurity` — see [crypto.md](crypto.md)." Only add further claims (signature, loop protection, auto-response) if you confirmed them in `channels/email/outbound_reply.py` during Step 1. Change the GenericInterface row "`ArticleSend`, `TimeUnit`, multiple articles in TicketCreate/Update | partial" to "`TimeUnit`, multiple articles in TicketCreate/Update | partial" and add a row "`ArticleSend` (outbound e-mail on create/update) | yes | Agents only, like Znuny".
- [ ] **Step 3: `docs/specs/2026-07-19-tiqora-design.md`.** Replace the "Deferred" row with two rows: "Shipped since V1 | Process management (import + execution, no designer), calendar, stats (fixed reports + CSV), PGP/S-MIME, SOAP transport" and "Still open | Package/plugin system, process designer, reporting builder — planned in `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md`". In the PermissionEngine note, replace the sentence claiming Znuny `acl` rows are not evaluated with the verified behaviour from Step 1 (ACL rows filter selectable options — state, queue, priority, type, service, SLA, and dynamic-field options from wave 0 — via `domain/ticket_acl.py`; queue permissions stay group/role based like Znuny).
- [ ] **Step 4: Commit.**
  ```bash
  rtk git add docs/compatibility.md docs/specs/2026-07-19-tiqora-design.md
  rtk git commit -m "$(printf 'docs: ArticleSend is implemented; update deferred list and ACL note\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
  rtk git log -1 --oneline
  ```

### Task 2: Backend extension registry + channels point + admin listing

**Files:**
- Create: `backend/src/tiqora/extensions/__init__.py`, `backend/src/tiqora/extensions/registry.py`, `backend/src/tiqora/extensions/core.py`
- Create: `backend/src/tiqora/channels/registry.py`
- Create: `backend/src/tiqora/api/v1/admin/extensions.py`
- Modify: `backend/src/tiqora/api/v1/admin/channels.py` (import `CHANNEL_CONFIG_KEYS` from `tiqora.channels.registry`; keep the name importable from the old module), `backend/src/tiqora/api/v1/admin/__init__.py` (include router), `backend/src/tiqora/api/app.py` (call `get_registry()` once in `lifespan` startup so a broken registration fails at boot), `backend/src/tiqora/worker/__main__.py` (call `get_registry()` in `run_worker` before the loops)
- Test: `backend/tests/test_extensions_registry.py` (pure), `backend/tests/test_admin_extensions_api.py` (HTTP, no DB rows)
- Regenerate: `packages/api-client/openapi.json`, `docs/api/openapi.json`, `packages/api-client/src/schema.d.ts`

**Interfaces:**
- Produces: everything in Design D1 (`ExtensionPoint`, `Registry`, `Registration`, `DuplicateExtensionError`, `RegistryFrozenError`, `register_core`, `get_registry`, `set_registry_for_tests`, `ChannelDescriptor`, `CHANNELS`, `core_channel_descriptors`, `GET /api/v1/admin/extensions`).

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_extensions_registry.py`):
  ```python
  from __future__ import annotations

  from dataclasses import dataclass

  import pytest

  from tiqora.extensions import (
      DuplicateExtensionError,
      ExtensionPoint,
      Registry,
      RegistryFrozenError,
      get_registry,
      register_core,
      set_registry_for_tests,
  )


  @dataclass(frozen=True)
  class _Item:
      key: str
      value: int = 0


  POINT: ExtensionPoint[_Item] = ExtensionPoint(
      key="test.items", item_key=lambda i: i.key, description="test point"
  )


  def test_add_get_items_keep_registration_order() -> None:
      reg = Registry()
      reg.add(POINT, _Item("b"))
      reg.add(POINT, _Item("a"), owner="com.example.plugin")
      assert [i.key for i in reg.items(POINT)] == ["b", "a"]
      assert reg.get(POINT, "a") == _Item("a")
      assert reg.get(POINT, "missing") is None
      owners = {r.key: r.owner for r in reg.registrations(POINT)}
      assert owners == {"b": "core", "a": "com.example.plugin"}


  def test_duplicate_key_is_rejected_even_from_another_owner() -> None:
      reg = Registry()
      reg.add(POINT, _Item("a"))
      with pytest.raises(DuplicateExtensionError, match="test.items"):
          reg.add(POINT, _Item("a", 1), owner="com.example.plugin")


  def test_require_names_point_and_key() -> None:
      with pytest.raises(KeyError, match="test.items: nope"):
          Registry().require(POINT, "nope")


  def test_frozen_registry_refuses_additions() -> None:
      reg = Registry()
      reg.freeze()
      with pytest.raises(RegistryFrozenError):
          reg.add(POINT, _Item("a"))


  def test_core_registry_has_channels_and_is_frozen() -> None:
      from tiqora.api.v1.admin.channels import CHANNEL_CONFIG_KEYS
      from tiqora.channels.registry import CHANNELS

      set_registry_for_tests(None)
      reg = get_registry()
      assert reg.frozen
      assert {c.key for c in reg.items(CHANNELS)} == set(CHANNEL_CONFIG_KEYS)
      assert get_registry() is reg  # cached


  def test_register_core_twice_on_one_registry_fails_loudly() -> None:
      reg = Registry()
      register_core(reg)
      with pytest.raises(DuplicateExtensionError):
          register_core(reg)
  ```
  And `backend/tests/test_admin_extensions_api.py`: build the app like `test_agent_ticket_zoom_apis._client_for` but override `get_admin_user` (from `tiqora.api.v1.admin.deps`) with a fake root user and `get_db` with a dummy (the route does not touch the DB); assert `GET /api/v1/admin/extensions` returns 200, contains an entry `key == "core.channels"` whose items include `{"key": "telegram", "owner": "core"}`, and `sdk_stable` is false.
- [ ] **Step 2: Run to verify failure.** `cd backend && uv run python -m pytest -q tests/test_extensions_registry.py tests/test_admin_extensions_api.py` → FAIL (`ModuleNotFoundError: tiqora.extensions`).
- [ ] **Step 3: Implement** per Design D1. `Registry` stores `dict[str, ExtensionPoint]` and `dict[str, dict[str, Registration]]` (insertion ordered). `get_registry()` uses a module-level `_REGISTRY: Registry | None` + `threading.Lock`. `register_core` (for now) only adds `core_channel_descriptors()`. Move `CHANNEL_CONFIG_KEYS` verbatim into `tiqora/channels/registry.py`; `api/v1/admin/channels.py` does `from tiqora.channels.registry import CHANNEL_CONFIG_KEYS` (behaviour unchanged). Admin route builds `ExtensionPointOut` from `get_registry().points()` / `registrations(point)`.
- [ ] **Step 4: Run to verify pass.** Same command → PASS. Also `uv run python -m pytest -q tests/test_admin_channels.py`.
- [ ] **Step 5: Regenerate openapi** (Global Constraints command), lint gates, commit:
  ```bash
  rtk git add backend/src/tiqora/extensions backend/src/tiqora/channels/registry.py backend/src/tiqora/api/v1/admin/extensions.py backend/src/tiqora/api/v1/admin/channels.py backend/src/tiqora/api/v1/admin/__init__.py backend/src/tiqora/api/app.py backend/src/tiqora/worker/__main__.py backend/tests/test_extensions_registry.py backend/tests/test_admin_extensions_api.py packages/api-client/openapi.json packages/api-client/src/schema.d.ts docs/api/openapi.json
  rtk git commit -m "$(printf 'feat(extensions): internal extension registry with channels point\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
  rtk git log -1 --oneline
  ```

### Task 3: Settings sections — store, core sections, admin API, avatars on top

**Files:**
- Create: `backend/src/tiqora/settings_sections/__init__.py`, `section.py`, `store.py`
- Create: `backend/src/tiqora/jobs/__init__.py` (empty docstring module for now), `backend/src/tiqora/jobs/settings.py`
- Create: `backend/src/tiqora/avatars/settings.py`
- Create: `backend/src/tiqora/api/v1/admin/settings_sections.py`
- Modify: `backend/src/tiqora/extensions/core.py` (register `JOBS_SECTION`, `AVATARS_SECTION`, call `validate_section_schema` for each), `backend/src/tiqora/api/v1/admin/__init__.py`, `backend/src/tiqora/api/v1/admin/avatars.py` (GET/PUT via the store; `AvatarSettings` imported from `tiqora.avatars.settings`; PUT commits)
- Test: `backend/tests/test_settings_sections.py` (pure + DB), `backend/tests/test_admin_settings_sections_api.py` (HTTP + DB)
- Regenerate openapi files.

**Interfaces:**
- Consumes: `ExtensionPoint`, `Registry`, `get_registry` (Task 2); `tiqora.crypto.secret.encrypt_secret/decrypt_secret`; `tiqora.db.tiqora.models.TiqoraSettings`.
- Produces: Design D2 verbatim (`SettingsSection`, `SETTINGS_SECTIONS`, `SECRET_MASK`, `UnsupportedSettingsSchema`, `validate_section_schema`, `SettingsValidationError`, `encode_setting_value`, `load_section_values`, `read_section_for_admin`, `save_section_values`, `JobsSettings`, `JOBS_SECTION`, `AvatarSettings`, `AVATARS_SECTION`, the three admin routes).

- [ ] **Step 1: Write the failing tests.** Pure tests in `test_settings_sections.py`:
  ```python
  class _Demo(BaseModel):
      enabled: bool = False
      limit: int = Field(5, ge=1, le=10)
      mode: Literal["a", "b"] = "a"
      hosts: list[str] = Field(default_factory=list)
      token: SecretStr | None = None

  DEMO = SettingsSection(key="test.demo", model=_Demo, title_key="t", i18n_prefix="p")

  def test_storage_keys_and_secret_fields() -> None:
      assert DEMO.storage_key("limit") == "test.demo.limit"
      assert DEMO.secret_fields() == frozenset({"token"})

  def test_unsupported_field_type_is_rejected() -> None:
      class _Bad(BaseModel):
          nested: dict[str, int] = Field(default_factory=dict)
      with pytest.raises(UnsupportedSettingsSchema, match="nested"):
          validate_section_schema(SettingsSection(key="x", model=_Bad, title_key="t", i18n_prefix="p"))

  def test_field_without_default_is_rejected() -> None:
      class _NoDefault(BaseModel):
          name: str
      with pytest.raises(UnsupportedSettingsSchema, match="name"):
          validate_section_schema(SettingsSection(key="x", model=_NoDefault, title_key="t", i18n_prefix="p"))

  def test_every_core_section_fits_the_supported_schema_subset() -> None:
      reg = Registry(); register_core(reg)
      for section in reg.items(SETTINGS_SECTIONS):
          validate_section_schema(section)

  def test_storage_keys_never_collide() -> None:
      import tiqora.domain.settings_store as legacy
      legacy_values = {v for k, v in vars(legacy).items() if k.startswith("KEY_")}
      reg = Registry(); register_core(reg)
      seen: dict[str, str] = {}
      for section in reg.items(SETTINGS_SECTIONS):
          for name in section.model.model_fields:
              key = section.storage_key(name)
              assert key not in seen, (key, seen.get(key))
              seen[key] = f"{section.key}.{name}"
              if name not in section.storage_keys:
                  assert key not in legacy_values, key

  def test_encode_setting_value() -> None:
      assert encode_setting_value(True) == "1" and encode_setting_value(False) == "0"
      assert encode_setting_value(7) == "7"
      assert encode_setting_value(["a", "b"]) == '["a", "b"]'
  ```
  DB tests (parametrize `url_fixture` over `mariadb_znuny_url`, `postgres_znuny_url` like `tests/test_admin_api.py`; delete `tiqora_settings` rows `WHERE key LIKE 'test.demo.%'` in a module autouse fixture):
  ```python
  async def test_secret_mask_round_trip_does_not_rotate(url_fixture, request) -> None:
      # save token "s3cret" -> row is a Fernet token (startswith "gAAAA"), not plaintext
      # read_section_for_admin -> values["token"] == SECRET_MASK, "token" in secrets_set
      # save {"token": SECRET_MASK, "limit": 6} -> token row byte-identical to before; limit row "6"
      # save {"limit": 7} (token omitted) -> token row unchanged
      # load_section_values(...).token.get_secret_value() == "s3cret"
      # save {"token": ""} -> token row deleted; secrets_set == []
  async def test_invalid_value_raises_and_writes_nothing(...) -> None:
      # save {"limit": 99} -> SettingsValidationError with errors["limit"]; no row for test.demo.limit
  async def test_unknown_field_is_rejected(...) -> None
  async def test_garbage_row_falls_back_to_default(...) -> None:
      # insert test.demo.limit = "abc" directly -> load_section_values().limit == 5
  async def test_save_does_not_commit(...) -> None:
      # save in a session, then session.rollback() -> no row persisted
  async def test_avatars_section_reads_legacy_keys(...) -> None:
      # set_setting(KEY_AVATARS_GRAVATAR_ENABLED, "0") -> load_section_values(AVATARS_SECTION).gravatar_enabled is False
      # save {"favicon_enabled": False} + commit -> get_setting_bool(session, KEY_AVATARS_FAVICON_ENABLED, True) is False
      # cleanup: delete both avatar keys in the test (they are global keys!)
  ```
  API tests (`test_admin_settings_sections_api.py`, MariaDB only is fine): override `get_admin_user`; `GET /api/v1/admin/settings-sections` lists `jobs` and `avatars`; `GET …/jobs` returns `json_schema.properties.max_concurrency.maximum == 8` and `values == {"max_concurrency": 2, "retention_days": 14}`; `PUT …/jobs {"values": {"max_concurrency": 99}}` → 422 with `detail.errors.max_concurrency`; `PUT …/jobs {"values": {"max_concurrency": 3}}` → 200 and a second GET shows 3; `GET …/nope` → 404; `GET /api/v1/admin/avatars` still returns `{"gravatar_enabled": …, "favicon_enabled": …}`. Clean up `jobs.%` rows and the two avatar keys afterwards.
- [ ] **Step 2: Run to verify failure.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_settings_sections.py tests/test_admin_settings_sections_api.py` → FAIL (import errors).
- [ ] **Step 3: Implement** Design D2. Notes: detect secret fields by checking the annotation (`SecretStr` or `Optional[SecretStr]`) with `typing.get_args`; `validate_section_schema` walks `model.model_fields` (annotation + `is_required()`); bool rows decode with the same truthy set as `get_setting_bool` (`"1","true","yes","on"`); `load_section_values` validates the decoded dict once with `model.model_validate`; on `ValidationError` it drops every field named in `exc.errors()` (falls back to its default, logs `settings_value_invalid`) and validates again, so one garbage row never resets the other fields; `read_section_for_admin` uses `model.model_dump(mode="json")` for non-secret values; store writes via ORM `TiqoraSettings` rows (`session.get` by key, update/add/delete, `await session.flush()`).
- [ ] **Step 4: Run to verify pass.** Same command → PASS. Then `uv run python -m pytest -q tests/test_admin_channels.py tests/test_avatars_api.py` (avatars endpoints unchanged).
- [ ] **Step 5: Regenerate openapi**, lint gates, commit `feat(settings): DB settings sections with schema, secrets and admin API`.

### Task 4: Jobs — tables, ORM, migration, service

**Files:**
- Create: `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_jobs.py` (revision = next number after the current head; `down_revision` = current head)
- Modify: `backend/src/tiqora/db/tiqora/models.py` (`TiqoraJob`, `TiqoraJobFile`, `JOB_RESULT_TEXT`, `JOB_FILE_BLOB`)
- Create: `backend/src/tiqora/jobs/registry.py`, `backend/src/tiqora/jobs/context.py`, `backend/src/tiqora/jobs/service.py`
- Modify: `backend/src/tiqora/jobs/__init__.py` (re-exports), `backend/tests/test_migration_gate.py` (`TIQORA_HEAD`), `backend/tests/test_migrate_cli.py` (`version ==`), `backend/tests/test_tiqora_model_ddl.py` (new test)
- Test: `backend/tests/test_jobs_service.py`

**Interfaces:**
- Consumes: `Registry`, `get_registry` (Task 2).
- Produces: tables + ORM of Design D3; `JobHandler`, `JOB_HANDLERS`, `JobContext`, `JobCancelled`, `JobFailed`, `utc_now`, `enqueue_job`, `get_job`, `list_jobs`, `list_job_files`, `get_job_file`, `request_cancel`, `purge_finished_jobs`, `can_view_job`, `ACTIVE_STATUSES`, `TERMINAL_STATUSES`, `MAX_RESULT_CHARS`, `UnknownJobKind`, `JobNotCancellable`.

- [ ] **Step 1: Write the failing tests.** DDL test (append to `test_tiqora_model_ddl.py`):
  ```python
  def test_job_file_data_and_result_fit_large_payloads_on_mariadb() -> None:
      from tiqora.db.tiqora.models import TiqoraJob, TiqoraJobFile
      assert "LONGBLOB" in _column_ddl(TiqoraJobFile.__table__, "data", mysql.dialect())
      assert "BYTEA" in _column_ddl(TiqoraJobFile.__table__, "data", postgresql.dialect())
      assert "MEDIUMTEXT" in _column_ddl(TiqoraJob.__table__, "result", mysql.dialect())
  ```
  `test_jobs_service.py` (both dialects; module autouse fixture deletes `tiqora_job_file`/`tiqora_job` rows with `kind LIKE 'test.%'` — file rows first, by `job_id IN (SELECT id …)` loaded as a list):
  ```python
  class _Payload(BaseModel):
      n: int

  async def _noop(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
      return {"n": payload.n}

  def _registry() -> Registry:
      reg = Registry()
      reg.add(JOB_HANDLERS, JobHandler(kind="test.noop", payload_model=_Payload, run=_noop, title_key="x"))
      return reg

  async def test_enqueue_validates_kind_and_payload(...) -> None:
      # enqueue_job(kind="test.unknown") -> UnknownJobKind
      # enqueue_job(kind="test.noop", payload={"n": "x"}) -> pydantic.ValidationError
      # enqueue_job(kind="test.noop", payload=_Payload(n=1), created_by=1, registry=reg) -> status "queued",
      #   payload JSON '{"n":1}' (json.loads equal), created_at naive and within 5 s of utc_now()
  async def test_enqueue_does_not_commit(...) -> None   # rollback -> row gone
  async def test_dedupe_returns_active_job_but_not_finished_one(...) -> None
      # two enqueues same (kind, dedupe_key) while queued -> same id; mark first succeeded -> third enqueue new id
  async def test_request_cancel(...) -> None
      # queued -> cancelled + finished_at set; running -> cancel_requested True, status running;
      # succeeded -> JobNotCancellable
  async def test_purge_finished_jobs_deletes_files_first(...) -> None
      # job finished 20 days ago with a file, job finished 1 day ago, running job -> purge(older_than=now-14d) == 1,
      # its file row gone, other two jobs remain
  def test_can_view_job() -> None   # creator yes, other user no, admin yes, system job (created_by None) admin only
  ```
- [ ] **Step 2: Run to verify failure.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_jobs_service.py tests/test_tiqora_model_ddl.py` → FAIL.
- [ ] **Step 3: Write the migration** (`op.create_table` for both tables exactly as Design D3; `sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")`, `sa.LargeBinary().with_variant(mysql.LONGBLOB(), "mysql", "mariadb")`; booleans with `server_default=sa.false()`; the four + one indexes; `downgrade` drops `tiqora_job_file` then `tiqora_job`). Update both hardcoded head ids in tests. Run `uv run python -m pytest -q tests/test_migration_gate.py tests/test_migrate_cli.py`.
- [ ] **Step 4: Implement ORM + `registry.py` + `context.py` + `service.py`** per Design D3. `JobContext` is a plain class constructed by the runner (Task 5); in this task implement `progress`, `check_cancelled`, `add_file` against `session_factory` (each opens its own short session and commits) — test them here with a hand-built `JobContext` (progress writes `progress_done/total/message` + `heartbeat_at`; second call within 1 s with `done < total` writes nothing; `check_cancelled` raises after `cancel_requested` is set; `add_file` stores bytes and size, > `MAX_JOB_FILE_BYTES` raises `JobFailed`). Keep `MAX_JOB_FILE_BYTES = 200 * 1024 * 1024` in `context.py`.
- [ ] **Step 5: Run to verify pass**, lint gates, commit `feat(jobs): tiqora_job tables, handler registry and job service`.

### Task 5: Jobs — runner + worker loop + daemon catalog

**Files:**
- Create: `backend/src/tiqora/jobs/runner.py`
- Modify: `backend/src/tiqora/config.py` (`jobs_poll_interval_seconds`), `backend/src/tiqora/worker/__main__.py` (start the runner in `_run_all_loops`), `backend/src/tiqora/worker/services.py` (`DaemonService(slug="jobs", …)` as Design D3), `backend/tests/test_admin_daemons.py` (`len(out.services) == 15`), `frontend/src/i18n/locales/en.json` + `de.json` (`admin.daemons.services.jobs.{name,description}`) + propagate
- Test: `backend/tests/test_jobs_runner.py`

**Interfaces:**
- Consumes: Task 3 (`load_section_values`, `JOBS_SECTION`), Task 4 (service, context, registry), `tiqora.worker.status.record_tick_status`.
- Produces: `JobRunner` and the constants of Design D3.

- [ ] **Step 1: Write the failing tests** (`test_jobs_runner.py`, MariaDB + PostgreSQL; handlers registered in a fresh `Registry`; runner built with `async_sessionmaker` of the test engine and `Settings(environment="test")`; cleanup as in Task 4). Read skill `asyncio-daemon-graceful-shutdown-executor-join` first.
  ```python
  async def test_runner_executes_job_and_stores_result(...) -> None:
      # enqueue test.noop n=3, commit; await runner.run_once(); await runner.wait_idle()
      # job.status == "succeeded", json.loads(job.result) == {"n": 3}, started_at/finished_at set, worker_id set
  async def test_handler_exception_marks_failed_and_runner_survives(...) -> None:
      # handler raises RuntimeError("boom") -> failed, error == FAILED_MESSAGE ("boom" not in error);
      # a second queued test.noop job in the same run_once still succeeds
  async def test_job_failed_message_is_shown(...) -> None   # JobFailed("Keine Tickets gefunden.") -> error equals it
  async def test_timeout_marks_failed(...) -> None          # handler timeout_seconds=1 sleeping 5 s -> TIMEOUT_MESSAGE
  async def test_cancel_while_running(...) -> None
      # handler loops: await ctx.progress(i, 100) + asyncio.sleep(0.05); test sets cancel via request_cancel+commit
      # after first progress row appears -> final status "cancelled"
  async def test_stale_running_job_is_failed_as_interrupted(...) -> None
      # insert status running, worker_id "other:1", heartbeat_at = utc_now()-10 min -> run_once() -> failed, INTERRUPTED_MESSAGE
  async def test_reaped_job_is_not_overwritten_by_late_finish(...) -> None
      # claim with runner A, force heartbeat_at old, runner B run_once reaps; let A's handler finish -> stays failed
  async def test_concurrency_limit_from_settings(...) -> None
      # save JOBS_SECTION max_concurrency=1 (commit); enqueue 3 blocking jobs (asyncio.Event) -> run_once() started == 1;
      # release, wait_idle, run_once again -> next one starts. Clean jobs.max_concurrency row afterwards.
  async def test_unknown_kind_fails_cleanly(...) -> None   # row with kind "test.gone" -> UNKNOWN_KIND_MESSAGE
  async def test_result_too_large(...) -> None             # handler returns {"x": "a" * (MAX_RESULT_CHARS + 1)}
  async def test_shutdown_marks_unfinished_jobs_interrupted(...) -> None
      # blocking job running; await runner.shutdown(grace_seconds=0.1) -> failed, INTERRUPTED_MESSAGE; no pending tasks
  async def test_purge_runs_from_run_once(...) -> None
      # retention_days default 14; a job finished 30 days ago disappears after run_once(); purge counter 1
  ```
- [ ] **Step 2: Run to verify failure.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_jobs_runner.py` → FAIL.
- [ ] **Step 3: Implement `runner.py`.** Key points: candidates `select(TiqoraJob.id).where(TiqoraJob.status == "queued", TiqoraJob.cancel_requested.is_(False)).order_by(TiqoraJob.id).limit(free)`; claim with `update(TiqoraJob).where(TiqoraJob.id == jid, TiqoraJob.status == "queued", TiqoraJob.cancel_requested.is_(False)).values(...)` and `result.rowcount == 1`; each task gets its own heartbeat sub-task (`HEARTBEAT_SECONDS`) cancelled in `finally`; `asyncio.wait_for(handler.run(ctx, payload), timeout=handler.timeout_seconds)`; track tasks in `self._active: dict[int, asyncio.Task]`; `shutdown` per the skill (stop flag, `asyncio.wait(tasks, timeout=grace)`, cancel + await the rest, then conditional UPDATE to failed). Payload validation failure → failed `FAILED_MESSAGE`.
- [ ] **Step 4: Worker wiring.** In `_run_all_loops` add `JobRunner(session_factory=get_session_factory(), settings=settings, registry=get_registry()).run_forever(stop, poll_seconds=settings.jobs_poll_interval_seconds)` to the `asyncio.gather`. Add the config field and the `DaemonService` entry (verify the stale formula in `backend/src/tiqora/api/v1/admin/daemons.py` / `frontend/src/lib/daemonStatus.ts` honours `tick_timeout_seconds`, so a 30 s status cadence never shows "stale"). Update `test_admin_daemons.py` count. Add the two German/English i18n strings (Design D8) and run `node frontend/scripts/propagate-i18n-keys.mjs && node frontend/scripts/check-i18n-keys.mjs`.
- [ ] **Step 5: Run to verify pass**: `uv run python -m pytest -q tests/test_jobs_runner.py tests/test_admin_daemons.py tests/test_admin_system.py`; `cd frontend && pnpm test --run src/routes/admin/DaemonsPage.test.tsx`. Lint gates, commit `feat(jobs): worker job runner with heartbeat, cancel, timeout and retention`.

### Task 6: Jobs — polling API

**Files:**
- Create: `backend/src/tiqora/jobs/schemas.py`, `backend/src/tiqora/api/v1/jobs.py`
- Modify: `backend/src/tiqora/api/v1/__init__.py` (include `jobs.router`)
- Test: `backend/tests/test_jobs_api.py`
- Regenerate openapi files.

**Interfaces:**
- Consumes: Task 4 service; `PermissionEngine.is_admin`; `UtcDateTime`.
- Produces: `JobOut`, `JobFileOut`, `JobAccepted`, `job_out`, the four routes of Design D3.

- [ ] **Step 1: Write the failing tests** (HTTP via `httpx.ASGITransport`, overriding `get_current_user` and `get_db` like `tests/test_agent_ticket_zoom_apis.py::_client_for`; seed one non-admin agent in your own id band, root = admin):
  ```python
  async def test_owner_polls_job_and_sees_progress_and_files(...) -> None
      # job created_by=agent with progress 3/10 and a file -> GET /api/v1/jobs/{id} 200;
      # body["status"] == "running", body["progress_total"] == 10, body["files"][0]["name"] == "a.csv",
      # created_at ends with "Z" or "+00:00"
  async def test_other_agent_gets_404_admin_gets_200(...) -> None
  async def test_cancel_queued_then_terminal_409(...) -> None
      # POST cancel on queued -> 200 status cancelled; again -> 409, detail.message present
  async def test_download_file(...) -> None
      # GET /api/v1/jobs/{id}/files/{fid} -> bytes equal, content-type "text/csv; charset=utf-8",
      # content-disposition contains "filename*=UTF-8''Tickets%20%C3%BCbersicht.csv" for name "Tickets übersicht.csv"
  async def test_file_of_foreign_job_is_404(...) -> None     # file id of job A requested under job B -> 404
  async def test_list_all_requires_admin(...) -> None         # ?all=true as agent -> 403; as admin -> 200
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_jobs_api.py`
- [ ] **Step 3: Implement** routes + `job_out` (title_key from `get_registry().get(JOB_HANDLERS, job.kind)`; `result` parsed with `json.loads`, invalid → `None`). Use `urllib.parse.quote(name)` for `filename*`. Mutating cancel commits.
- [ ] **Step 4: Run → PASS**, regenerate openapi, lint gates, commit `feat(jobs): job polling, cancel and file download API`.

### Task 7: Filter language (pure)

**Files:**
- Create: `backend/src/tiqora/filters/__init__.py`, `backend/src/tiqora/filters/model.py`, `backend/src/tiqora/filters/catalog.py`
- Test: `backend/tests/test_filters_model.py` (no DB)

**Interfaces:**
- Produces: Design D4 `model.py` + `catalog.py` names (`Condition`, `AllOf`, `AnyOf`, `Not`, `FilterNode`, `Filter`, `ConditionOp`, `FieldType`, `OPS_BY_TYPE`, `FilterFieldSpec`, `FilterError`, `validate_filter`, `iter_conditions`, `relative_delta`, `parse_datetime_value`, `MAX_DEPTH`, `MAX_CONDITIONS`, `MAX_IN_VALUES`).

- [ ] **Step 1: Write the failing tests:**
  ```python
  FIELDS = {
      "queue_id": FilterFieldSpec(key="queue_id", type="id"),
      "title": FilterFieldSpec(key="title", type="string"),
      "created": FilterFieldSpec(key="created", type="datetime"),
      "locked": FilterFieldSpec(key="locked", type="bool"),
      "channel": FilterFieldSpec(key="channel", type="enum", enum_values=("email", "phone")),
  }

  def _validate(raw: dict[str, Any]) -> Filter:
      flt = Filter.model_validate(raw)
      validate_filter(flt, FIELDS.get)
      return flt

  def test_round_trip_with_not_alias() -> None:
      raw = {"version": 1, "where": {"all": [
          {"field": "queue_id", "op": "in", "value": [1, 2]},
          {"not": {"field": "locked", "op": "is_true"}},
          {"any": [{"field": "title", "op": "contains", "value": "Drucker"}]},
      ]}}
      flt = _validate(raw)
      assert flt.model_dump(by_alias=True, exclude_none=True) == raw

  def test_condition_with_group_keys_is_rejected() -> None:
      with pytest.raises(ValidationError):
          Filter.model_validate({"where": {"field": "title", "op": "eq", "value": "x", "all": []}})

  @pytest.mark.parametrize(("cond", "path_suffix"), [
      ({"field": "nope", "op": "eq", "value": 1}, "field"),
      ({"field": "queue_id", "op": "contains", "value": 1}, "op"),
      ({"field": "queue_id", "op": "in", "value": []}, "value"),
      ({"field": "queue_id", "op": "in", "value": 3}, "value"),
      ({"field": "locked", "op": "is_true", "value": 1}, "value"),
      ({"field": "channel", "op": "eq", "value": "fax"}, "value"),
      ({"field": "created", "op": "within_last", "value": {"amount": 0, "unit": "day"}}, "value"),
      ({"field": "created", "op": "between", "value": {"from": "2026-01-01"}}, "value"),
      ({"field": "created", "op": "before", "value": "gestern"}, "value"),
  ])
  def test_invalid_conditions_name_the_path(cond: dict[str, Any], path_suffix: str) -> None:
      with pytest.raises(FilterError) as exc:
          _validate({"where": {"all": [cond]}})
      assert exc.value.path == f"where.all[0].{path_suffix}"

  def test_depth_and_size_limits() -> None:
      node: dict[str, Any] = {"field": "locked", "op": "is_true"}
      for _ in range(MAX_DEPTH + 1):
          node = {"not": node}
      with pytest.raises(FilterError, match="tief"):
          _validate({"where": node})
      many = [{"field": "locked", "op": "is_true"}] * (MAX_CONDITIONS + 1)
      with pytest.raises(FilterError):
          _validate({"where": {"all": many}})

  def test_datetime_values_become_naive_utc() -> None:
      assert parse_datetime_value("2026-03-29T03:30:00+02:00", path="p") == datetime(2026, 3, 29, 1, 30)
      assert parse_datetime_value("2026-03-29", path="p") == datetime(2026, 3, 29)

  def test_relative_delta_units() -> None:
      assert relative_delta({"amount": 2, "unit": "week"}, path="p") == timedelta(days=14)
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && uv run python -m pytest -q tests/test_filters_model.py`
- [ ] **Step 3: Implement.** Error messages German, plain ("Unbekanntes Feld „nope“.", "Der Filter ist zu tief verschachtelt.", "Für dieses Feld ist „contains“ nicht möglich."). `FilterNode` recursive type: define with `from __future__ import annotations` + `model_rebuild()` on the three group models.
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(filters): JSON filter language with validation`.

### Task 8: Ticket filter compiler (permission-scoped)

**Files:**
- Create: `backend/src/tiqora/filters/tickets.py`
- Modify: `backend/src/tiqora/filters/__init__.py`
- Test: `backend/tests/test_filters_tickets.py` (DB, MariaDB + PostgreSQL)

**Interfaces:**
- Consumes: Task 7; `PermissionEngine.groups_for_permission`; `tiqora.domain.ticket_service` helpers `_channel_condition`, `_escalation_due_before`, `UNASSIGNED_OWNER_ID`, `VIEW_STATE_TYPES` (import them; do not copy); `TiqoraAiTicketState` (preload ids, IN-list).
- Produces: `TicketQueryScope`, `ticket_query_scope`, `TICKET_FILTER_FIELDS`, `DF_FIELD_PREFIX`, `ticket_filter_fields`, `compile_ticket_filter`, `count_tickets` (Design D4).

- [ ] **Step 1: Seed + failing tests.** Seed (own band; verify free with grep): two groups G1/G2, agent A with `ro` on G1 only, queues Q1 (G1), Q2 (G1), Q3 (G2); tickets T1 (Q1, new, owner A, created 2026-01-10, DF `WaveZeroArea` Dropdown value "net", DF `WaveZeroDue` Date "2026-02-01 00:00:00" in `value_date`), T2 (Q2, open, owner root, locked, created 2026-01-20, `escalation_time` 1 h in the past), T3 (Q3, new — invisible to A), T4 (Q1, closed, archived). Two dynamic fields created in `dynamic_field` (object_type Ticket, valid) and deleted by the cleanup. Tests:
  ```python
  async def _ids(session, raw, scope, now=NOW) -> set[int]:
      stmt = await compile_ticket_filter(session, Filter.model_validate(raw), scope, now=now)
      return set() if stmt is None else {t.id for t in (await session.execute(stmt)).scalars()}

  async def test_scope_is_never_widened_by_filter(...) -> None:
      scope = await ticket_query_scope(session, AGENT_A)
      assert await _ids(session, {"where": {"field": "queue_id", "op": "eq", "value": Q3}}, scope) == set()
      assert await _ids(session, {"where": {"any": [
          {"field": "queue_id", "op": "eq", "value": Q3},
          {"not": {"field": "queue_id", "op": "eq", "value": Q3}}]}}, scope) == {T1, T2}
      assert await _ids(session, {}, scope) == {T1, T2}               # archived T4 excluded by scope

  async def test_include_archived_scope(...) -> None   # include_archived=True -> {T1, T2, T4}
  async def test_empty_scope_returns_none(...) -> None  # agent without groups -> compile returns None
  async def test_state_type_and_locked_and_unassigned(...) -> None
      # state_type in ["new"] -> {T1}; locked is_true -> {T2}; unassigned is_true -> {T2}
  async def test_escalated_and_within(...) -> None
      # escalated is_true -> {T2}; escalation_due_within lte 7200 -> {T2}
  async def test_created_between_and_relative(...) -> None
      # created between 2026-01-15..2026-02-01 -> {T2}; with now=2026-01-21T00:00 within_last 2 day -> {T2}
  async def test_neq_includes_nulls(...) -> None   # service_id neq 999 -> {T1, T2} (both NULL)
  async def test_dynamic_fields(...) -> None
      # df.WaveZeroArea eq "net" -> {T1}; df.WaveZeroArea neq "net" -> {T2};
      # df.WaveZeroArea is_empty -> {T2}; df.WaveZeroDue before "2026-03-01" -> {T1}
  async def test_unknown_df_is_filter_error(...) -> None    # df.Nope -> FilterError path "where.field"
  async def test_count_tickets(...) -> None                  # {} -> 2
  async def test_ticket_filter_fields_lists_static_and_dfs(...) -> None
      # keys include "queue_id", "df.WaveZeroArea" (type string, options_source "DynamicField_WaveZeroArea"),
      # "df.WaveZeroDue" (type datetime)
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_filters_tickets.py`
- [ ] **Step 3: Implement.** Structure: `ticket_query_scope` = valid queues (`Queue.valid_id == 1`) in `groups_for_permission(user_id, permission)`. `compile_ticket_filter`: validate with a resolver that knows static fields + DFs loaded once (`dynamic_field` rows, object_type Ticket, valid); preload lookups lazily only when a condition needs them (lock ids, state ids per type name, watcher ticket ids, AI-escalated ids); compile the tree recursively into a `ColumnElement[bool]` (`and_`, `or_`, `not_`); final `select(Ticket).where(Ticket.queue_id.in_(scope.queue_ids), archive rule, expr)`. DF condition: `exists().where(DynamicFieldValue.object_id == Ticket.id, DynamicFieldValue.field_id == fid, <value predicate>)`; `is_empty` = `~exists(non-empty value)`. Relative datetimes via `now or utc_now()`. `pending_until` compares epoch ints (`int(dt.replace(tzinfo=UTC).timestamp())`). No dialect-specific SQL (use `ilike` for `contains`/`starts_with` on strings; escape `%`/`_` with `escape="\\"`).
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(filters): permission-scoped ticket filter compiler`.

### Task 9: List-params bridge, parity test, ticket-filter API

**Files:**
- Create: `backend/src/tiqora/filters/ticket_params.py`, `backend/src/tiqora/api/v1/ticket_filters.py`
- Modify: `backend/src/tiqora/api/v1/__init__.py`, `frontend/src/i18n/locales/en.json` + `de.json` (`ticketFilter.fields.*`, Design D8) + propagate
- Test: `backend/tests/test_filters_ticket_params.py` (DB parity), `backend/tests/test_ticket_filters_api.py`
- Regenerate openapi files.

**Interfaces:**
- Consumes: Tasks 7–8; `TicketService._filtered_ticket_stmt` (read-only use in the test).
- Produces: `TicketListParams`, `filter_from_list_params`, `FilterFieldOut`, `TicketFilterCountIn`, `TicketFilterCountOut`, `GET /api/v1/ticket-filters/fields`, `POST /api/v1/ticket-filters/count`.

- [ ] **Step 1: Failing tests.** Parity (reuse the seed shape of Task 8 in its own band, plus a watcher row, a Telegram-channel article on one ticket, a responsible, a customer user): for each parameter set below assert the compiled ids equal `_filtered_ticket_stmt` ids:
  ```python
  PARAM_SETS = [
      {}, {"queue_id": Q1}, {"queue_id": Q3}, {"state_type": "open"}, {"state_type": "todo"},
      {"state_type": "closed"}, {"state_id": STATE_NEW}, {"owner_id": AGENT_A}, {"locked": True},
      {"locked": False}, {"watcher_user_id": AGENT_A}, {"escalated": True}, {"ai_escalated": True},
      {"unassigned": True}, {"unassigned": False}, {"escalating_within": 7200},
      {"channel": ["telegram"]}, {"channel": ["email", "phone"]}, {"customer_user_id": CUSTOMER},
      {"responsible_id": AGENT_A}, {"include_archived": True},
  ]
  @pytest.mark.parametrize("params", PARAM_SETS)
  async def test_bridge_matches_list_endpoint(params, ...) -> None:
      p = TicketListParams.model_validate(params)
      legacy = await TicketService(session)._filtered_ticket_stmt(
          AGENT_A, queue_id=p.queue_id, state_id=p.state_id, state_type=p.state_type,
          owner_id=p.owner_id, customer_id=p.customer_id, customer_user_id=p.customer_user_id,
          responsible_id=p.responsible_id, service_id=p.service_id, locked=p.locked,
          watcher_user_id=p.watcher_user_id, escalated=True if p.escalated else None,
          ai_escalated=True if p.ai_escalated else None, unassigned=p.unassigned,
          escalating_within=p.escalating_within, channel=p.channel,
          include_archived=p.include_archived)
      scope = await ticket_query_scope(session, AGENT_A, include_archived=p.include_archived)
      compiled = await compile_ticket_filter(session, filter_from_list_params(p), scope)
      assert _ids(legacy) == _ids(compiled)
  ```
  API tests: `GET /api/v1/ticket-filters/fields` contains `queue_id` with `options_source == "queue"`; `POST /api/v1/ticket-filters/count` with `{"filter": {"where": {"field": "queue_id", "op": "eq", "value": Q1}}}` → `{"count": 1}` for agent A; invalid field → 422 with `detail.path == "where.field"`; `include_archived: true` as non-admin is ignored.
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_filters_ticket_params.py tests/test_ticket_filters_api.py`
- [ ] **Step 3: Implement** `filter_from_list_params` (one condition per set param under `all`; `state_type` view names → `in VIEW_STATE_TYPES[name]`, other strings → `eq`; `escalated`/`ai_escalated` only when True; `escalating_within` → `escalation_due_within lte`; `watcher_user_id` → `watched_by eq`; `include_archived` is NOT a condition — it goes into the scope by the caller). Routes per Design D4. Add `ticketFilter.fields.<key>` labels (German first: "Queue", "Status", "Statusart", "Priorität", "Typ", "Service", "SLA", "Besitzer", "Verantwortlich", "Kunde", "Kundenbenutzer", "Gesperrt", "Beobachtet von", "Nicht zugewiesen", "Eskaliert", "Von der KI übergeben", "Eskaliert innerhalb", "Kanal", "Archiviert", "Erstellt", "Geändert", "Erinnerung am", "Ticket-ID", "Ticketnummer", "Titel"), propagate, check.
- [ ] **Step 4: Run → PASS** (if a parity case fails, fix the compiler/bridge, never the legacy code). Regenerate openapi, lint gates, `node frontend/scripts/check-i18n-keys.mjs`, commit `feat(filters): list-params bridge with parity test and ticket-filter API`.

### Task 10: Ticket export as a background job (first consumer)

**Files:**
- Create: `backend/src/tiqora/domain/ticket_csv.py` (move `_CSV_HEADER`, `_ticket_csv_row`, `_EchoWriter` from `api/v1/tickets.py` as `CSV_HEADER`, `ticket_csv_row`, `EchoWriter`; `tickets.py` imports them — `GET /tickets/export.csv` output byte-identical)
- Create: `backend/src/tiqora/jobs/handlers/__init__.py`, `backend/src/tiqora/jobs/handlers/ticket_export.py`
- Modify: `backend/src/tiqora/domain/ticket_service.py` (add `iter_ticket_items(self, stmt: Select[tuple[Ticket]], *, sort: str, order: str, batch_size: int = 500) -> AsyncGenerator[TicketListItem, None]`; `iter_tickets_for_export` calls it), `backend/src/tiqora/api/v1/tickets.py` (`POST /export-jobs`, registered before `/{ticket_id}` routes), `backend/src/tiqora/extensions/core.py` (register `TICKET_EXPORT_JOB`)
- Test: `backend/tests/test_ticket_export_job.py`; existing export test (find with `grep -ln "export.csv" backend/tests`) must stay green.
- Regenerate openapi files.

**Interfaces:**
- Consumes: Tasks 4–9.
- Produces: `TicketExportPayload`, `run_ticket_export`, `TICKET_EXPORT_JOB`, `TicketExportJobIn`, `POST /api/v1/tickets/export-jobs` → 202 `JobAccepted`; `TicketService.iter_ticket_items`.

- [ ] **Step 1: Failing tests** (seed two visible tickets + one invisible, own band):
  ```python
  async def test_export_job_end_to_end(...) -> None:
      # POST /api/v1/tickets/export-jobs {"params": {"queue_id": Q1}} as agent -> 202, body["status"] == "queued"
      # runner = JobRunner(session_factory=factory, settings=Settings(environment="test"), registry=get_registry())
      # await runner.run_once(); await runner.wait_idle()
      # GET /api/v1/jobs/{id} -> succeeded, result == {"rows": 1}, one file "tickets.csv"
      # file bytes start with b"\xef\xbb\xbf" and equal the body of GET /api/v1/tickets/export.csv?queue_id=Q1
  async def test_export_job_never_contains_foreign_queue(...) -> None   # params {} -> rows == visible count only
  async def test_export_job_reports_progress(...) -> None               # progress_total == rows, progress_done == rows
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_ticket_export_job.py`
- [ ] **Step 3: Implement.** Route: `include_archived` only for admins (same rule as `GET /tickets`), build `TicketExportPayload(user_id=user.id, filter=filter_from_list_params(body.params), include_archived=..., sort=..., order=...)`, `enqueue_job(..., created_by=user.id)`, commit, return 202. Handler: open a session from `ctx.session_factory`, `scope = await ticket_query_scope(...)` (permission re-evaluated at run time), `total = await count_tickets(...)`, `await ctx.progress(0, total, "Tickets werden gelesen …")`, write CSV into a `io.BytesIO` (BOM + header + rows, `;` delimiter, via `EchoWriter`), progress every 500 rows, `await ctx.add_file("tickets.csv", "text/csv; charset=utf-8", data)`, return `{"rows": n}`. `stmt is None` → zero-row CSV (header only), not an error.
- [ ] **Step 4: Run → PASS** (+ the existing export test), regenerate openapi, lint gates, commit `feat(tickets): CSV export as background job`.

### Task 11: Frontend extension registry + field engine core

**Files:**
- Create: `frontend/src/extensions/registry.ts`, `frontend/src/extensions/registry.test.ts`
- Create: `frontend/src/components/fields/types.ts`, `kinds.tsx`, `MultiSelectInput.tsx`, `FieldInput.tsx`, `FieldsForm.tsx`, `validate.ts`, `index.ts`
- Test: `frontend/src/components/fields/FieldsForm.test.tsx`, `frontend/src/components/fields/validate.test.ts`
- Modify: `frontend/src/i18n/locales/en.json` + `de.json` (`fields.*`) + propagate

**Interfaces:**
- Produces: Design D5 frontend `types.ts`, `FIELD_KINDS`, `registerCoreFieldKinds`, `FieldInput`, `FieldsForm`, `isEmptyFieldValue`, `validateFieldValues`; Design D5 registry (`createExtensionPoint`, `listExtensionPoints`, `ExtensionPoint`).
- Kind rendering: text/secret/number/date/datetime → `<input>` (types text/password/number/date/datetime-local) styled like the old `inputCls` (`w-full rounded border border-hairline bg-surface px-2 py-1 text-sm text-ink focus:outline-none focus:ring-1 focus:ring-accent`); textarea → `<textarea rows={3}>`; boolean → `ui/Switch`; select → `ui/SelectField` (items from options, plus `{value: "", label: t("fields.none")}` first when `allowEmpty`); multiselect → `MultiSelectInput` (removable chips + `SelectMenu` listing unselected options; chip remove button testid `${testId}-remove-${value}`, menu trigger `${testId}-add`); tags → `ui/TagInput` (suggestions from options). `secret` with `secretSet` shows placeholder `t("fields.secretStored")` and an "Entfernen" button (testid `${testId}-clear`) that sets the value to `""`.

- [ ] **Step 1: Failing tests:**
  ```tsx
  // registry.test.ts
  it("registers, lists in order and rejects duplicates", () => {
    const p = createExtensionPoint<{ id: string }>("test.point", (x) => x.id);
    p.register({ id: "b" }); p.register({ id: "a" }, "com.example.plugin");
    expect(p.list().map((x) => x.id)).toEqual(["b", "a"]);
    expect(p.owner("a")).toBe("com.example.plugin");
    expect(() => p.register({ id: "a" })).toThrow(/test.point/);
    expect(listExtensionPoints().find((e) => e.key === "test.point")?.items).toHaveLength(2);
  });

  // FieldsForm.test.tsx (wrap in I18nextProvider like TicketHeader.test.tsx)
  const fields: FieldDef[] = [
    { key: "topic", kind: "text", label: "Thema", required: true },
    { key: "area", kind: "select", label: "Bereich", allowEmpty: true,
      options: [{ value: "net", label: "Netz" }, { value: "tv", label: "TV" }] },
    { key: "urgent", kind: "boolean", label: "Dringend" },
    { key: "tags", kind: "multiselect", label: "Themen",
      options: [{ value: "a", label: "A" }, { value: "b", label: "B" }] },
  ];
  it("renders every kind and reports typed values", () => {
    const onChange = vi.fn();
    render(<FieldsForm fields={fields} values={{ topic: "", area: null, urgent: false, tags: ["a"] }}
      onChange={onChange} testId="f" />);
    fireEvent.change(screen.getByTestId("f-topic"), { target: { value: "VPN" } });
    expect(onChange).toHaveBeenLastCalledWith("topic", "VPN");
    fireEvent.click(screen.getByTestId("f-area"));
    fireEvent.click(screen.getByTestId("f-area-menu-option-tv"));
    expect(onChange).toHaveBeenLastCalledWith("area", "tv");
    fireEvent.click(screen.getByTestId("f-urgent"));
    expect(onChange).toHaveBeenLastCalledWith("urgent", true);
    fireEvent.click(screen.getByTestId("f-tags-remove-a"));
    expect(onChange).toHaveBeenLastCalledWith("tags", []);
  });
  it("shows errors and the required marker", () => {
    render(<FieldsForm fields={fields} values={{}} onChange={() => {}} errors={{ topic: "Pflichtfeld" }} testId="f" />);
    expect(screen.getByTestId("f-topic-error")).toHaveTextContent("Pflichtfeld");
  });
  it("options from optionsByKey win over static options", () => { /* optionsByKey {area: [{value:"x",label:"X"}]} -> only X offered */ });
  it("unknown kind renders a hint instead of crashing", () => { /* kind "nope" as never -> text t("fields.unsupported") */ });

  // validate.test.ts
  it("flags empty required values, numbers out of range, too long text", () => {
    const t = ((k: string) => k) as TFunction;
    expect(validateFieldValues([{ key: "a", kind: "text", label: "A", required: true }], { a: " " }, t))
      .toEqual({ a: "fields.required" });
    expect(validateFieldValues([{ key: "n", kind: "number", label: "N", min: 1, max: 8 }], { n: 9 }, t))
      .toEqual({ n: "fields.invalidNumber" });
    expect(validateFieldValues([{ key: "b", kind: "boolean", label: "B", required: true }], { b: false }, t))
      .toEqual({ b: "fields.required" });     // Znuny: a required checkbox must be ticked
  });
  ```
- [ ] **Step 2: Run → FAIL.** `cd frontend && pnpm vitest run src/extensions src/components/fields`
- [ ] **Step 3: Implement** per Design D5. Add i18n keys `fields.*` (de: none "–", required "Pflichtfeld", invalidNumber "Bitte eine Zahl im erlaubten Bereich eingeben.", tooLong "Der Text ist zu lang.", unsupported "Dieses Feld kann hier nicht bearbeitet werden.", secretStored "Gespeichert – leer lassen, um nichts zu ändern", secretRemove "Entfernen", addValue "Hinzufügen", removeValue "{{label}} entfernen"); propagate + check.
- [ ] **Step 4: Run → PASS**, `cd frontend && pnpm lint && pnpm test --run`, commit `feat(fields): field engine core and frontend extension registry`.

### Task 12: Frontend jobs — `useJob`, `JobProgress`, `JobDialog`, CSV export via job

**Files:**
- Create: `frontend/src/lib/jobsApi.ts`, `frontend/src/components/jobs/useJob.ts`, `JobProgress.tsx`, `JobDialog.tsx`
- Test: `frontend/src/components/jobs/useJob.test.ts`, `frontend/src/components/jobs/JobDialog.test.tsx`
- Modify: `frontend/src/routes/agent/QueuesPage.tsx` (`exportCsv` starts the job and opens `JobDialog`), `frontend/src/routes/agent/QueuesPage.test.tsx` (export test now asserts `startTicketExport` called with `{params: {...filterParams}, sort, order}` and the dialog opens), `frontend/src/demo/mockData.ts`, `frontend/src/demo/handlers.ts`, `en.json`/`de.json` (`jobs.*`) + propagate

**Interfaces:**
- Consumes: openapi types `JobOut`, `JobAccepted`, `TicketExportJobIn` (Tasks 6, 10).
- Produces: Design D6 verbatim.

- [ ] **Step 1: Failing tests:**
  ```ts
  // useJob.test.ts
  const job = (status: Job["status"]) => ({ status } as Job);
  it("polls fast first, then slower, and stops when finished", () => {
    expect(jobPollInterval(job("queued"), 0, 5_000)).toBe(1000);
    expect(jobPollInterval(job("running"), 0, 31_000)).toBe(3000);
    expect(jobPollInterval(job("succeeded"), 0, 1_000)).toBe(false);
    expect(jobPollInterval(undefined, 0, 1_000)).toBe(1000);
  });
  ```
  ```tsx
  // JobDialog.test.tsx — mock jobsApi.get to return running (3/10) then succeeded with one file
  it("shows progress, then a download link", async () => {
    render(<Providers><JobDialog jobId={7} title="Export" onClose={() => {}} /></Providers>);
    expect(await screen.findByTestId("job-dialog-progress")).toHaveTextContent("3 von 10");
    expect(await screen.findByTestId("job-dialog-file-1")).toHaveAttribute("href", "/api/v1/jobs/7/files/1");
  });
  it("cancel calls the API while running", async () => { /* click job-dialog-cancel -> jobsApi.cancel(7) */ });
  it("shows the server's error text on failure", async () => { /* status failed, error "Zeitlimit" -> job-dialog-error */ });
  ```
- [ ] **Step 2: Run → FAIL.** `cd frontend && pnpm vitest run src/components/jobs src/routes/agent/QueuesPage.test.tsx`
- [ ] **Step 3: Implement.** `useJob` uses `useQuery({queryKey: ["jobs", jobId], queryFn, enabled: jobId != null, refetchInterval: (q) => jobPollInterval(q.state.data, firstSeenRef.current, Date.now())})`. `JobProgress`: determinate bar (`bg-accent` on `bg-surface-subtle`, `role="progressbar"` with `aria-valuenow`) when `progress_total` set, else an indeterminate bar; status text `t("jobs.status.<status>")`; message line; error line `text-danger`; file links `<a href={jobsApi.fileUrl(...)} download>`. `JobDialog` = `ui/Dialog` + `JobProgress` + footer (Abbrechen while active, Schließen otherwise; testids `${testId}-cancel`, `${testId}-close`, `${testId}-progress`, `${testId}-file-<id>`, `${testId}-error`). QueuesPage: keep the menu item testid `queue-export-csv`. Demo: `POST …/tickets/export-jobs` → `{job_id: 9101, status: "queued"}`; `GET …/jobs/9101` → succeeded job with file `{id: 1, name: "tickets.csv", content_type: "text/csv; charset=utf-8", size_bytes: 64}` (include every `JobOut` field); `GET …/jobs` → `[]`; in `handlers.ts` an `http.get("*/api/v1/jobs/*/files/*")` returning a small CSV body with `content-type: text/csv`.
- [ ] **Step 4: Run → PASS**, `pnpm lint && pnpm test --run`, i18n check, commit `feat(jobs): job progress dialog; ticket CSV export runs as background job`.

### Task 13: `SchemaForm`, settings page, avatars dialog on settings sections

**Files:**
- Create: `frontend/src/components/fields/jsonSchema.ts`, `frontend/src/components/fields/SchemaForm.tsx`, `frontend/src/lib/settingsSectionsApi.ts`, `frontend/src/routes/admin/SettingsSectionPage.tsx`
- Test: `frontend/src/components/fields/jsonSchema.test.ts`, `frontend/src/components/fields/SchemaForm.test.tsx`, `frontend/src/routes/admin/SettingsSectionPage.test.tsx`
- Modify: `frontend/src/router.tsx` (route `/settings/$section` under `adminLayoutRoute`), `frontend/src/components/admin/AvatarSettingsDialog.tsx` (+ its test if any), `frontend/src/routes/admin/DaemonsPage.tsx` (row `jobs` links "Einstellungen" → `/admin/settings/jobs`, testid `daemon-jobs-settings`), `frontend/src/lib/adminSearch.ts` (entry slug `settings-jobs`, route `/admin/settings/jobs`, nameKey `admin.settings.jobs.title`), `frontend/src/demo/mockData.ts`, `en.json`/`de.json` (`admin.settings.*`) + propagate

**Interfaces:**
- Consumes: Task 11 (`FieldsForm`, types); openapi `SettingsSectionOut` etc. (Task 3).
- Produces: Design D5 `jsonSchema.ts` + `SchemaForm`; Design D7 verbatim.

- [ ] **Step 1: Failing tests** — use the exact schema the backend produces for the test model (generate once with `cd backend && uv run python -c "…model_json_schema()"` and paste it as a fixture), e.g. for `JobsSettings` plus a synthetic schema covering every supported keyword:
  ```ts
  const schema: JsonSchema = {
    type: "object", title: "Demo", required: [],
    properties: {
      enabled: { type: "boolean", default: false, title: "Enabled" },
      limit: { type: "integer", minimum: 1, maximum: 10, default: 5, title: "Limit" },
      mode: { enum: ["a", "b"], type: "string", default: "a", title: "Mode" },
      level: { $ref: "#/$defs/Level", default: "low" },
      hosts: { type: "array", items: { type: "string" }, title: "Hosts" },
      token: { anyOf: [{ type: "string", format: "password", writeOnly: true }, { type: "null" }], default: null },
      note: { type: "string", "x-widget": "textarea", default: "" },
      blob: { type: "object", title: "Blob" },
    },
    $defs: { Level: { enum: ["low", "high"], type: "string", title: "Level" } },
  };
  it("maps the supported subset and lists the rest as unsupported", () => {
    const t = ((k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k) as TFunction;
    const { fields, unsupported } = schemaToFieldDefs(schema, { t, i18nPrefix: "p", secretsSet: ["token"] });
    expect(Object.fromEntries(fields.map((f) => [f.key, f.kind]))).toEqual({
      enabled: "boolean", limit: "number", mode: "select", level: "select", hosts: "tags",
      token: "secret", note: "textarea" });
    expect(fields.find((f) => f.key === "limit")).toMatchObject({ min: 1, max: 10 });
    expect(fields.find((f) => f.key === "token")).toMatchObject({ secretSet: true });
    expect(fields.find((f) => f.key === "level")?.options?.map((o) => o.value)).toEqual(["low", "high"]);
    expect(unsupported).toEqual(["blob"]);
  });
  it("labels come from i18n keys with the schema title as fallback", () => { /* t called with "p.fields.limit.label" */ });
  it("schemaDefaults reads defaults", () => { expect(schemaDefaults(schema)).toMatchObject({ limit: 5, mode: "a" }); });
  ```
  `SchemaForm.test.tsx`: changing `limit` calls `onChange({...values, limit: 6}, "limit")`; the unsupported `blob` shows `schema-form-unsupported-blob`. `SettingsSectionPage.test.tsx` (mock `settingsSectionsApi`): renders `jobs` values, edit `max_concurrency`, Save sends `put("jobs", {max_concurrency: 3})` (changed keys only); a 422 mock `ApiError` with body `{detail: {errors: {max_concurrency: "zu groß"}}}` shows `schema-form-max_concurrency-error`; untouched secret fields are never sent.
- [ ] **Step 2: Run → FAIL.** `cd frontend && pnpm vitest run src/components/fields src/routes/admin/SettingsSectionPage.test.tsx`
- [ ] **Step 3: Implement.** `AvatarSettingsDialog`: load `settingsSectionsApi.get("avatars")`, render `SchemaForm` with `testId="admin-avatars"`, `i18nPrefix` from the response, save via `settingsSectionsApi.put` and invalidate `["admin", "avatars"]` + `["settings-sections", "avatars"]`; keep `admin-avatars-save`, `admin-avatars-error`, `admin-avatars-load-error`. i18n: `admin.settings.avatars.fields.gravatar_enabled.label` = existing `admin.avatars.gravatar`, `.help` = `admin.avatars.gravatarHint` (same for favicon); jobs labels de: "Gleichzeitige Aufgaben" / "Wie viele Hintergrundaufgaben gleichzeitig laufen dürfen. Mehr ist schneller, belastet aber die Datenbank stärker.", "Aufbewahrung in Tagen" / "Nach so vielen Tagen werden fertige Aufgaben und ihre Dateien gelöscht."; page strings: save "Speichern", saved "Gespeichert", loadError "Die Einstellungen konnten nicht geladen werden.", saveError "Speichern fehlgeschlagen.", notFound "Diese Einstellungen gibt es nicht.". Demo: `GET …/admin/settings-sections/jobs` and `/avatars` with the real backend schema (paste from Step 1), `PUT` echo; list endpoint.
- [ ] **Step 4: Run → PASS**, `pnpm lint && pnpm test --run`, i18n check, commit `feat(settings): SchemaForm, generic settings page, Kundenbilder on settings sections`.

### Task 14: Dynamic-field adapter; replace `DynamicFieldInputs`

**Files:**
- Create: `frontend/src/components/fields/dynamicFields.ts`, `frontend/src/components/fields/DynamicFieldsForm.tsx`
- Test: `frontend/src/components/fields/dynamicFields.test.ts`
- Modify: `frontend/src/routes/agent/NewTicketPage.tsx`, `frontend/src/components/agent/phone/PhoneCallDialog.tsx`, `frontend/src/components/agent/phone/PhoneTicketFields.tsx` (import `DynamicFieldsForm`/`DynamicFieldValues` from `@/components/fields`), tests `PhoneCallDialog.test.tsx` (Dropdown now via `SelectField`: click `phone-df-Area`, then `phone-df-Area-menu-option-tv`) and `NewTicketPage.test.tsx` (same pattern if a select is used; Text inputs unchanged)
- Delete: `frontend/src/components/agent/phone/DynamicFieldInputs.tsx`

**Interfaces:**
- Consumes: Task 11; `DynamicFieldDef` (`frontend/src/lib/phoneApi.ts`); `toZonedInputValue`, `fromZonedInputValue`, `displayTimeZone` (`frontend/src/lib/timeZone.ts`).
- Produces: `DF_KIND`, `dynamicFieldToDef`, `dfToFieldValue`, `fieldValueToDf`, `DynamicFieldsForm`, `DynamicFieldValues` (Design D5).

- [ ] **Step 1: Failing tests:**
  ```ts
  it("maps Znuny field types", () => {
    expect(dynamicFieldToDef({ name: "Area", label: "Bereich", field_type: "Dropdown", required: true,
      possible_values: { net: "Netz" } })).toMatchObject({ key: "Area", kind: "select", required: true,
      optionsKey: "DynamicField_Area", allowEmpty: true, options: [{ value: "net", label: "Netz" }] });
  });
  it("checkbox and date round trips", () => {
    expect(dfToFieldValue("boolean", [1])).toBe(true);
    expect(fieldValueToDf("boolean", false)).toEqual(["0"]);
    expect(dfToFieldValue("date", ["2026-02-01T00:00:00"])).toBe("2026-02-01");
    expect(fieldValueToDf("date", "2026-02-01")).toEqual(["2026-02-01 00:00:00"]);
    expect(fieldValueToDf("text", "")).toEqual([]);
    expect(fieldValueToDf("multiselect", ["a", "b"])).toEqual(["a", "b"]);
  });
  it("datetime is stored as UTC and shown in the agent's zone", () => {
    expect(fieldValueToDf("datetime", "2026-07-01T10:30", "Europe/Berlin")).toEqual(["2026-07-01 08:30:00"]);
    expect(dfToFieldValue("datetime", ["2026-07-01 08:30:00"], "Europe/Berlin")).toBe("2026-07-01T10:30");
    expect(dfToFieldValue("datetime", ["2026-01-15T08:30:00"], "Europe/Berlin")).toBe("2026-01-15T09:30");
  });
  ```
- [ ] **Step 2: Run → FAIL.** `cd frontend && pnpm vitest run src/components/fields/dynamicFields.test.ts`
- [ ] **Step 3: Implement**, swap the three call sites (keep their `testId` values `df`, `phone-df`, `new-ticket-df`, `new-ticket-df-required` so control test ids stay), delete the old file, update the two tests' Dropdown interactions. Note in the commit message that DateTime fields from phone/new-ticket forms are now converted to UTC like Znuny.
- [ ] **Step 4: Run → PASS**, `pnpm lint && pnpm test --run`, commit `feat(fields): dynamic-field adapter; phone and new-ticket forms use the field engine`.

### Task 15: Znuny-identical dynamic-field writes + multi-field PATCH

**Files:**
- Create: `backend/src/tiqora/domain/dynamic_field_values.py`
- Modify: `backend/src/tiqora/domain/ticket_write_service.py` (`update_dynamic_field` delegates; `TicketWriteService.update_dynamic_fields(user_id, ticket_id, values: Mapping[str, list[str]])` new, asserts `rw` once), `backend/src/tiqora/api/v1/tickets.py` (`MutationRequest.dynamic_fields`; handled inside the existing `session.begin()` block), `backend/src/tiqora/worker/generic_agent.py` (catch `InvalidInput` from `update_dynamic_field`: structlog warning `generic_agent_df_invalid`, continue with the next field — a bad job config must not stop the job), check `backend/src/tiqora/api/compat/operations.py` and `backend/src/tiqora/mcp_server/server.py` call sites map `InvalidInput` to their existing error responses (add a test only if a mapping is missing)
- Test: `backend/tests/test_dynamic_field_values.py` (pure + DB both dialects)
- Regenerate openapi files.

**Interfaces:**
- Consumes: `tiqora.znuny.history.add_dynamic_field_update`, `invalidate_ticket_cache`, `_emit_event` (as today in `update_dynamic_field`).
- Produces: Design D5 backend names; `MutationRequest.dynamic_fields: dict[str, list[str]] | None`.

- [ ] **Step 1: Read the Perl.** `znuny-6.5.22/Kernel/System/DynamicField/Backend.pm` (`ValueSet`, `ValueIsDifferent`), `Driver/Date.pm`, `Driver/DateTime.pm`, `Driver/Checkbox.pm`, `Driver/BaseText.pm`, `Driver/BaseSelect.pm`, `Driver/Multiselect.pm` (`ValueSet`, `ReadableValueRender`), `Kernel/System/DynamicFieldValue.pm` (`ValueSet`), `Kernel/System/DynamicField/ObjectType/Ticket.pm` (`PostValueSet`: change_time, 166-char rule).
- [ ] **Step 2: Failing tests.** Pure:
  ```python
  @pytest.mark.parametrize(("ftype", "raw", "expected"), [
      ("Date", ["2026-02-01"], ["2026-02-01 00:00:00"]),
      ("Date", ["2026-02-01 00:00:00"], ["2026-02-01 00:00:00"]),
      ("DateTime", ["2026-02-01T10:30"], ["2026-02-01 10:30:00"]),
      ("Checkbox", ["1"], ["1"]), ("Checkbox", [""], []),
      ("Multiselect", ["a", "", "b"], ["a", "b"]), ("Text", [""], []),
  ])
  def test_normalize_ok(ftype, raw, expected) -> None:
      assert normalize_df_values(ftype, raw) == expected

  @pytest.mark.parametrize(("ftype", "raw"), [
      ("Date", ["2026-02-01 10:00:00"]), ("Date", ["01.02.2026"]), ("Checkbox", ["yes"]),
      ("Dropdown", ["a", "b"]), ("DateTime", ["morgen"]),
  ])
  def test_normalize_rejects(ftype, raw) -> None:
      with pytest.raises(DynamicFieldValueError):
          normalize_df_values(ftype, raw)

  def test_readable_history_value() -> None:
      assert readable_history_value("Date", ["2026-02-01 00:00:00"]) == "2026-02-01"
      assert readable_history_value("DateTime", ["2026-02-01 10:30:00"]) == "2026-02-01 10:30"
      assert readable_history_value("Multiselect", ["a", "b"]) == "a, b"

  # Expected values computed by hand from ObjectType/Ticket.pm PostValueSet (NoCharacters = 166):
  @pytest.mark.parametrize(("parts", "expected"), [
      # old value > 55 -> 50 + "[...]"; name fits -> value cut to 166 - 55 - 20 - 5 = 86
      (("F" * 20, "v" * 200, "o" * 80), ("F" * 20, "v" * 86 + "[...]", "o" * 50 + "[...]")),
      # name and value both too long -> both cut to int(166 / 2) - 5 = 78
      (("F" * 100, "v" * 100, ""), ("F" * 78 + "[...]", "v" * 78 + "[...]", "")),
      # name too long, short value -> name cut to 166 - 0 - 1 - 5 = 160
      (("F" * 170, "v", ""), ("F" * 160 + "[...]", "v", "")),
      # within the limit -> unchanged
      (("Feld", "neu", "alt"), ("Feld", "neu", "alt")),
  ])
  def test_truncate_history_parts_matches_perl(parts, expected) -> None:
      assert truncate_history_parts(*parts) == expected
  ```
  DB (both dialects; own band; seed one ticket and DFs of type Date, DateTime, Checkbox, Dropdown, Multiselect, Text):
  ```python
  async def test_date_is_written_to_value_date(...) -> None
      # set Date ["2026-02-01"] -> row value_date == datetime(2026,2,1), value_text IS NULL, value_int IS NULL
  async def test_checkbox_is_written_to_value_int(...) -> None    # ["0"] -> value_int 0
  async def test_unchanged_value_writes_nothing(...) -> None
      # set Text ["a"]; record max(ticket_history.id) and ticket.change_time; set ["a"] again -> returns False,
      # no new history row, change_time unchanged
  async def test_change_bumps_change_time_and_writes_history(...) -> None
      # history name == "%%FieldName%%WaveDue%%Value%%2026-02-01%%OldValue%%" for first Date set
  async def test_empty_value_deletes_rows(...) -> None     # set [] after ["a"] -> no rows, history Value ""
  async def test_patch_dynamic_fields_atomic_422(...) -> None
      # PATCH /api/v1/tickets/{id} {"dynamic_fields": {"WaveText": ["neu"], "WaveDate": ["2026-02-01 10:00:00"]}}
      # -> 422; WaveText unchanged (transaction rolled back)
  async def test_patch_dynamic_fields_needs_rw(...) -> None   # agent with ro only -> 403
  ```
- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_dynamic_field_values.py`
- [ ] **Step 4: Implement** Design D5 backend. Value writes via `text()` INSERT with explicit columns (`value_text`, `value_date`, `value_int`) — bind a `datetime` for `value_date` (parse the normalized string), `int` for `value_int`. Keep event name `TicketDynamicFieldUpdate` and payload `{"field": name, "values": values}`. Run the existing DF-related suites: `uv run python -m pytest -q $(grep -ln "update_dynamic_field\|dynamic_field_value" tests/*.py)`.
- [ ] **Step 5: Run → PASS**, regenerate openapi, lint gates, commit `fix(tickets): write dynamic fields like Znuny (typed columns, no-op, change_time); PATCH several fields at once`.

### Task 16: ACL-filtered field options API + "Freie Felder" screen

**Files:**
- Create: `backend/src/tiqora/domain/field_options.py`
- Modify: `backend/src/tiqora/api/v1/reference.py` (`POST /field-options`; `DynamicFieldScreen` gains `"AgentTicketFreeText"`)
- Test: `backend/tests/test_field_options.py` (DB, MariaDB; ACL rows seeded into Znuny's `acl` table and deleted afterwards)
- Regenerate openapi files.

**Interfaces:**
- Consumes: `collect_ticket_field_options`, `ticket_acl.apply_ticket_acl`, `admin.dynamic_fields.config_from_yaml`, `TicketService.get_ticket` (access check).
- Produces: `ATTRIBUTE_OPTION_FIELDS`, `DF_OPTION_PREFIX`, `FieldOptionsContext`, `FieldOptionsRequest`, `FieldOptionOut`, `FieldOptionsOut`, `collect_field_options`, `POST /api/v1/reference/field-options`.

- [ ] **Step 1: Failing tests.** Seed: DF `WaveArea` Dropdown with `PossibleValues {tv: "TV", net: "Netz", phone: "Telefon"}`; an ACL (valid) whose `config_match` is `{"Properties": {"Ticket": {"Queue": ["<Q1 name>"]}}}` and `config_change` `{"Possible": {"Ticket": {"DynamicField_WaveArea": ["net", "tv"]}}}` (YAML, same format the existing ACL tests use — find them with `grep -ln "config_change" backend/tests`); ticket in Q1 and one in Q2; agent with `ro` on both queues' group.
  ```python
  async def test_df_options_sorted_and_acl_filtered(...) -> None
      # fields ["DynamicField_WaveArea"], context {ticket_id: T_Q1, action: "AgentTicketFreeText"}
      # -> [{"value": "net", "label": "Netz"}, {"value": "tv", "label": "TV"}]
  async def test_df_options_without_matching_acl(...) -> None
      # ticket in Q2 -> Netz, Telefon, TV (sorted by label)
  async def test_form_context_queue_drives_acl(...) -> None
      # no ticket_id, context.queue_id Q1 -> filtered like the first test
  async def test_attribute_fields_are_lists(...) -> None
      # fields ["queue", "state"] -> options["queue"] is a list of {"value","label"}
  async def test_unknown_and_text_fields_are_omitted(...) -> None   # "DynamicField_WaveText", "nope" -> not in options
  async def test_unreadable_ticket_is_404(...) -> None
  async def test_free_text_screen_lists_configured_fields(...) -> None
      # sysconfig Ticket::Frontend::AgentTicketFreeText###DynamicField = {WaveArea: 1}
      # (set it via the helper existing reference/DF tests use — grep "###DynamicField" backend/tests)
      # -> GET /api/v1/reference/dynamic-fields?screen=AgentTicketFreeText returns WaveArea
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_field_options.py`
- [ ] **Step 3: Implement** per Design D5. Build ACL `checks` like `collect_ticket_field_options` does and add `checks["Ticket"][f"DynamicField_{n}"] = values` from `context.dynamic_fields`. Verify the sort rule in `znuny-6.5.22/Kernel/Output/HTML/Layout.pm` and note the line in a code comment.
- [ ] **Step 4: Run → PASS**, regenerate openapi, lint gates, commit `feat(reference): ACL-filtered field options incl. dynamic fields`.

### Task 17: Editable dynamic fields in ticket zoom

**Files:**
- Create: `frontend/src/components/fields/useFieldOptions.ts`, `frontend/src/lib/fieldOptionsApi.ts`, `frontend/src/components/agent/TicketDynamicFields.tsx`
- Test: `frontend/src/components/agent/TicketDynamicFields.test.tsx`
- Modify: `frontend/src/components/agent/TicketHeader.tsx` (replace the read-only `<details>` block with `<TicketDynamicFields ticket={ticket} />`), `frontend/src/components/agent/TicketHeader.test.tsx` (mocks), `frontend/src/lib/phoneApi.ts` (`PhoneScreen` → add a separate `DynamicFieldScreen` type including `"AgentTicketFreeText"`; `screenDynamicFields` accepts it), `frontend/src/demo/mockData.ts` (`/reference/dynamic-fields` for AgentTicketFreeText, `POST /reference/field-options`, demo ticket with two DFs), `en.json`/`de.json` (`ticket.dynamicFieldsEdit.*`) + propagate

**Interfaces:**
- Consumes: Tasks 11, 14, 15, 16.
- Produces: `useFieldOptions`, `fieldOptionsApi.fetch(body: FieldOptionsRequest, signal?) -> Promise<FieldOptionsOut>`, `TicketDynamicFields` (Design D5).

- [ ] **Step 1: Failing tests** (mock `phoneApi.screenDynamicFields`, `fieldOptionsApi.fetch`, `api.patchTicket` — check the exact client method name used for `PATCH /tickets/{id}` in `TicketHeaderActions.tsx`):
  ```tsx
  it("shows values read-only and hides Bearbeiten without rw", async () => {
    render(<Providers><TicketDynamicFields ticket={makeTicket({ permissions: { ...perms, rw: false },
      dynamic_fields: [{ name: "Area", label: "Bereich", field_type: "Dropdown", values: ["net"] }] })} /></Providers>);
    expect(await screen.findByText("net")).toBeInTheDocument();
    expect(screen.queryByTestId("ticket-df-edit")).not.toBeInTheDocument();
  });
  it("edits configured fields incl. empty ones and saves only changes", async () => {
    screenDynamicFields.mockResolvedValue([
      { name: "Area", label: "Bereich", field_type: "Dropdown", required: false, possible_values: { net: "Netz", tv: "TV" } },
      { name: "Due", label: "Fällig", field_type: "Date", required: false, possible_values: null } ]);
    fetchOptions.mockResolvedValue({ options: { DynamicField_Area: [{ value: "tv", label: "TV" }] } });
    render(/* rw ticket with Area = net */);
    fireEvent.click(await screen.findByTestId("ticket-df-edit"));
    fireEvent.change(screen.getByTestId("ticket-df-Due"), { target: { value: "2026-02-01" } });
    fireEvent.click(screen.getByTestId("ticket-df-save"));
    await waitFor(() => expect(patchTicket).toHaveBeenCalledWith(7, { dynamic_fields: { Due: ["2026-02-01 00:00:00"] } }));
  });
  it("ACL options replace the static ones", async () => { /* open Area menu -> only "TV" offered */ });
  it("shows the server error and stays in edit mode", async () => { /* patchTicket rejects 422 -> ticket-df-error */ });
  ```
- [ ] **Step 2: Run → FAIL.** `cd frontend && pnpm vitest run src/components/agent/TicketDynamicFields.test.tsx src/components/agent/TicketHeader.test.tsx`
- [ ] **Step 3: Implement.** Query keys: `["reference", "dynamic-fields", "AgentTicketFreeText"]` (staleTime 5 min), options via `useFieldOptions(keys, {ticket_id, action: "AgentTicketFreeText", dynamic_fields: currentDraftAsDf})` enabled only in edit mode; after save invalidate the ticket query used by the zoom page (find its key in `frontend/src/routes/agent/TicketPage.tsx` or equivalent) and the history query. Required fields validated with `validateFieldValues` before sending. Read-only rendering: Date → `YYYY-MM-DD`, DateTime → zone-converted `YYYY-MM-DD HH:MM`, Checkbox → "Ja"/"Nein" (`common.yes`/`common.no` if present, else new keys), Dropdown → label from `possible_values` when known. i18n de: edit "Bearbeiten", save "Speichern", cancel "Abbrechen", saved "Gespeichert", saveError "Die Felder konnten nicht gespeichert werden.", noneEditable "".
- [ ] **Step 4: Run → PASS**, `pnpm lint && pnpm test --run`, i18n check, commit `feat(ticket): edit dynamic fields in ticket zoom (Znuny "Freie Felder" config)`.

### Task 18: Architecture docs + final sweep

**Files:**
- Modify: `docs/architecture.md` (new section "Shared foundations": jobs, filter model, field engine, extension registry, settings sections — each 5–10 lines with module paths and the rules from Design D1–D7), `docs/compatibility.md` (dynamic fields: editable in zoom via `AgentTicketFreeText`, typed writes; ticket export as job), `docs/parallel-operation.md` or `docs/deployment.md` (where daemons are listed: the new "jobs" loop and `TIQORA_JOBS_POLL_INTERVAL_SECONDS`)
- No code changes except fixes found by the sweep.

- [ ] **Step 1: Write the docs** (English, like the existing docs; no German UI terms without the English meaning).
- [ ] **Step 2: Full backend suite:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` → all green, no new leak-baseline entries (`rtk git diff --stat backend/tests/db_leak_baseline.txt` empty). Lint gates.
- [ ] **Step 3: Frontend:** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`.
- [ ] **Step 4: OpenAPI freshness:** regenerate both openapi files; `rtk git status` must show no diff afterwards (if it does, an earlier task forgot to regenerate — commit it here).
- [ ] **Step 5: Demo smoke:** `cd frontend && VITE_DEMO=1 pnpm vite --port 5179` and open `/admin/settings/jobs`, `/admin/daemons`, a ticket zoom with dynamic fields, and the queue view's "CSV exportieren" (skill-free manual check or Playwright per memory "UI-Check via Demo-Modus"; delete `.playwright-mcp` before committing).
- [ ] **Step 6: Commit** `docs(architecture): wave 0 shared foundations`.

---

## Self-review notes (for the executor)

- Spec coverage: async jobs (Tasks 4–6, 10, 12), field engine + reference options + DynamicFieldInputs replacement + editable zoom DFs (Tasks 11, 14–17), filter model (Tasks 7–9, used in 10), extension registry (Tasks 2, 11; used by 3, 4, 10, 11), settings in DB with schema forms (Tasks 3, 13), doc cleanup (Task 1). Saved searches, reports, widgets, bulk actions and plugin loading consume these in later waves — not built here.
- Not in this wave (deliberate): moving `GET /api/v1/tickets` / `_filtered_ticket_stmt` onto the compiler (gate: Task 9 parity test), ACL-filtered DF options on the new-ticket/phone forms (they keep static `possible_values`), SSE push for job progress (polling per spec), per-queue/per-group settings scopes, job retries/scheduling (`run_after`), a filter-builder UI.
- Editable zoom fields follow Znuny: only fields enabled in `Ticket::Frontend::AgentTicketFreeText###DynamicField` are editable, and stock Znuny enables none — sites must configure it (SysConfig) to see the "Bearbeiten" button.
