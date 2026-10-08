# Wave 1 — Process Management Phase 1 "Correctness" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Tiqora run exactly the processes and ACLs Znuny runs — the deployed state, gated by process state (Active / Inactive / FadeAway), with ACLs on every process surface, Znuny-identical dialog field semantics, smart tags, dialog `PendingTime`, and the Znuny "new process ticket" flow for agents and customers.

**Architecture:** Two new `tiqora_*` revision tables hold deployed snapshots of the process set (Znuny `ProcessDump` shape) and the ACL set (Znuny `ACLDump` shape). A worker loop (`deploy_watch`) detects a Znuny deploy (sync table empty, content changed) and records a revision; the engine and the ACL evaluator read the newest revision (in-process cache keyed by revision id) and fall back to the live `pm_*`/`acl` rows only while no revision exists. One availability module (`tiqora.process.availability`) applies the state gate, the dialog `Interface` and the ACLs (through one function `ticket_acl.filter_process_options`) for agents and customers. New engine entry points create a ticket from a start dialog and enroll a ticket through its start dialog; a server-built dialog form (fields + ACL-filtered options) feeds one shared frontend form built on the wave-0 field engine.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic (two-chain setup), Pydantic v2, PyYAML, asyncio worker loops, React 19 + TanStack Router/Query, Tailwind tokens, wave-0 `components/fields/*`, Vitest + Testing Library, MSW demo mode.

**Spec:** `docs/superpowers/specs/2026-10-08-process-management-design.md` → "Current state", "Parity checklist" (Engine items 1–4, 6 and "New process ticket"), "Fixed decisions → Deploy model / Engine changes", "Phases → 1 Correctness". Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` (principles, definition of done). Builds on `docs/superpowers/plans/2026-10-08-wave0-foundations.md` (must be merged first; names used verbatim).

## Global Constraints

- Commit directly to `main`; never push (a push deploys production). Never `--no-verify` (PII hooks). Only made-up example data (example.org etc.). Use `rtk git` per repo CLAUDE.md (`rtk git add …`, `rtk git commit …`); after every commit run `rtk git log -1 --oneline` because the pre-commit hook can block a chained commit. Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (needs Docker; never plain `uv run pytest`). For a task, run the touched test modules; the full suite at least in the last task. Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their rows (pattern: `_seed_statements` + module-scoped autouse fixture, see `backend/tests/test_ai_refine.py`); never add entries to `backend/tests/db_leak_baseline.txt`.
- New DB test modules pick their own seed-id band; check it is free with `grep -rn "<band>" backend/tests` before use (skill `pytest-session-container-id-band-collisions`). Suggested bands (free on 2026-10-08, re-check): `881000–881999` deployed/watch, `882000–882999` acl_sync, `884000–884999` ACL principal, `886000–886999` availability, `887100–887999` smart tags, `888000–888999` process tickets, `889000–889999` dialog form.
- Migrations: Alembic two-chain setup; new migrations in `backend/alembic/versions_tiqora` with `down_revision` = the CURRENT primary head at implementation time (check `ls backend/alembic/versions_tiqora | tail -1` and `cd backend && uv run alembic heads` — today `20261007_0060`, wave 0 adds its jobs migration on top; do not hardcode); follow skill `alembic-two-chain-new-head-forks-graph`; update tests that hardcode the head id (`backend/tests/test_migration_gate.py` `TIQORA_HEAD`, `backend/tests/test_migrate_cli.py` `version == …`). Tables only `tiqora_*`; never change Znuny tables' DDL; writes to Znuny tables must be behaviourally identical to Znuny (history rows, change_time, cache invalidation via TiqoraSync signals). Reference source: `znuny-6.5.22/` in the repo root.
- ORM models in `backend/src/tiqora/db/tiqora/models.py` must produce the same DDL as the migration (`backend/tests/test_tiqora_model_ddl.py`).
- MySQL/MariaDB AND PostgreSQL must both work. Prod MariaDB mixes collations: never JOIN `tiqora_*` tables with Znuny tables on string columns — load keys, then IN-list.
- Timestamps in `tiqora_*_revision` are app-written naive UTC via `tiqora.jobs.service.utc_now()` (wave 0); API exposes them as `tiqora.domain.schemas.UtcDateTime`. Writes to Znuny sync tables use `current_timestamp` exactly like Znuny's SQL.
- Long-running endpoints (> a few seconds possible) must use the wave-0 job system (prod nginx 90 s timeout). Nothing in this plan is long-running; the snapshot build runs in the worker loop, never in a request.
- After any API model/route change: regenerate `packages/api-client/openapi.json` (skill `tiqora-openapi-regen-or-ci-wipes-schema`): `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`; check `head -c 40 packages/api-client/openapi.json` starts `{"components"` and the diff is tens/hundreds of lines. Never hand-edit `schema.d.ts`. `packages/api-client/src/client.ts` is hand-written — update its inline types where this plan changes a response it wraps.
- A Pydantic response field WITH a default becomes required in the generated TS type — test fixtures and demo mocks that build such objects must include it (this plan adds such fields to `TicketProcessStateOut` and `TicketFieldOptionsOut`).
- Frontend: `cd frontend && pnpm lint && pnpm test --run`; never run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales); `node frontend/scripts/check-i18n-keys.mjs` must pass. New screens get demo-mode mocks (`frontend/src/demo/mockData.ts` `resolveData`; `VITE_DEMO=1`). Frontend API wrappers are hand-written `frontend/src/lib/*Api.ts` files using `api.request<T>(…)` / `portalApi.request<T>(…)` with `Schemas["…"]` types (pattern `frontend/src/lib/phoneApi.ts`); error text via `apiErrorMessage` (`frontend/src/lib/avatarApi.ts`).
- UI copy German first, plain words, named by what admins recognise ("Prozess", "Schritt", "Bereitstellen", "Entwurf", "In Znuny übernommen"); no system jargon in UI text ("Revision", "Snapshot", "ACL-Principal" only in code).
- Znuny behaviour is the reference for every engine decision; each ported rule cites its Znuny file and line range in a code comment.

## Review Focus

1. **A process admin edits a process in Znuny and does NOT deploy** → Tiqora keeps running the last deployed version (no draft leaks into agents' screens); after "Bereitstellen" in Znuny, Tiqora picks the new version within one watch interval. Pinned in Task 5 (`test_draft_edit_is_not_picked_up_until_sync_table_empties`) and Task 4 (`test_engine_runs_newest_revision_not_live_rows`).
2. **A customer in the portal sees a process or dialog an ACL forbids** → hidden; a forged submit is refused (403). Customer ACLs are evaluated as the customer, never as the portal's system user 1 (which bypasses every ACL). Pinned in Task 7 (`test_customer_principal_is_not_root`) and Task 8 (`test_portal_submit_of_acl_hidden_dialog_is_403`).
3. **A FadeAway or Inactive process** → FadeAway: running tickets keep working, no new starts; Inactive: not offered anywhere, its tickets' dialogs refused (409), transitions not evaluated. Pinned in Task 8 (`test_state_gate_matrix`).
4. **A dialog field configured `Display: 1` (shown, optional)** is not required; `Display: 2` is required; `Display: 0` is hidden and its `DefaultValue` is applied server-side even when a client sends another value. Pinned in Task 1 (`test_display_semantics_match_znuny`, `test_hidden_field_default_overrides_submitted_value`).
5. **A new process ticket whose start dialog hides Queue/State/Priority/Lock** → the ticket is created with `Process::Default*` sysconfig values, owner set after create, both process dynamic fields set, and the first matching transition runs immediately (Znuny event module behaviour). Pinned in Task 13 (`test_create_process_ticket_uses_process_defaults_and_runs_transition`).

---

## Design (fixed)

Later phases (2: typed widgets + actions, 3: designer + Tiqora deploy via TiqoraSync) build on the names in this section. Do not rename anything here without updating this document in the same commit.

### D0. File map

Backend (new):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/process/fields.py` | dialog field semantics: `Display`, field-name canonicalisation, hidden defaults, customer skip list |
| `backend/src/tiqora/process/actor.py` | `ProcessActor` (agent / customer acting on a process) |
| `backend/src/tiqora/process/dump.py` | build the Znuny `ProcessDump` shape from `pm_*` rows; `canonical_json`, `dump_checksum` |
| `backend/src/tiqora/process/source.py` | `ProcessSource` protocol, `LiveProcessSource`, `RevisionProcessSource`, `active_process_source` |
| `backend/src/tiqora/process/availability.py` | state gate + interface + ACL for every process surface |
| `backend/src/tiqora/process/conditions.py` | condition data (`get_condition_data`) + Znuny-exact condition evaluation incl. smart tags |
| `backend/src/tiqora/process/smart_tags.py` | `<OTRS_…>` substitution in action config and condition values |
| `backend/src/tiqora/process/dialog_form.py` | server-built dialog form (fields + ACL-filtered options) |
| `backend/src/tiqora/process/process_ticket.py` | `create_process_ticket`, `enroll_ticket` |
| `backend/src/tiqora/deployed/__init__.py` | re-exports |
| `backend/src/tiqora/deployed/store.py` | revision tables read/write + per-process cache |
| `backend/src/tiqora/deployed/sync_tables.py` | `pm_entity_sync` / `acl_sync` counts, `acl_sync` writes, clear (ownership mode) |
| `backend/src/tiqora/deployed/acl_dump.py` | build the Znuny `ACLDump` shape from `acl` rows |
| `backend/src/tiqora/deployed/watcher.py` | deploy detection tick |
| `backend/src/tiqora/api/v1/admin/deploy_status.py` | `GET /api/v1/admin/deploy-status`, `GET /api/v1/admin/processes` |
| `backend/src/tiqora/domain/ticket_action_acl.py` | Znuny `Action` ACL for the ticket zoom menu |
| `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_deployed_revisions.py` | `tiqora_process_revision`, `tiqora_acl_revision` |

Backend (modified): `process/config.py`, `process/graph.py`, `process/engine.py`, `process/ticket_state.py`, `process/schemas.py`, `process/exceptions.py`, `process/__init__.py`, `domain/ticket_acl.py`, `channels/email/placeholder.py`, `api/v1/process.py`, `api/portal/process.py`, `api/v1/admin/acl.py`, `api/v1/admin/__init__.py`, `api/v1/reference.py`, `api/v1/tickets.py`, `db/tiqora/models.py`, `db/legacy/process_management.py` (docstring), `config.py`, `worker/__main__.py`, `worker/services.py`.

Frontend (new):

| Path | Responsibility |
|---|---|
| `frontend/src/lib/processApi.ts` | agent process endpoints |
| `frontend/src/lib/portalProcessApi.ts` | portal process endpoints |
| `frontend/src/lib/deployStatusApi.ts` | admin deploy status + admin process list |
| `frontend/src/components/process/processForm.ts` | `ProcessDialogFormOut` → wave-0 `FieldDef[]`, initial values, submit mapping |
| `frontend/src/components/process/ProcessDialogForm.tsx` | shared dialog form (agent + portal) on `FieldsForm` |
| `frontend/src/components/process/DeployStatusCard.tsx` | "Bereitgestellt / Entwurf" card for admin pages |
| `frontend/src/routes/agent/NewProcessTicketPage.tsx` | `/agent/process/new` |
| `frontend/src/routes/portal/NewProcessTicketPage.tsx` | `/portal/process/new` |

Frontend (modified): `components/agent/process/ActivityDialogModal.tsx`, `StartProcessDialog.tsx`, `ProcessWidget.tsx`, `processDialogFields.ts` (deleted in Task 16), `components/agent/NewTicketButton.tsx`, `components/agent/TicketHeaderActions.tsx`, `components/agent/TicketZoomOverflowMenu.tsx`, `components/portal/PortalProcessWidget.tsx`, `routes/agent/QueuesPage.tsx`, `routes/admin/ProcessesPage.tsx`, `routes/admin/AclPage.tsx`, `routes/admin/DaemonsPage.tsx`, `routes/portal/TicketListPage.tsx`, `router.tsx`, `demo/mockData.ts`, `i18n/locales/*.json`.

### D1. Verified Znuny facts (sources)

- **Process states** are hardcoded in `Kernel/System/ProcessManagement/DB/Process/State.pm:48-69`: `S1`=Active, `S2`=Inactive, `S3`=FadeAway (not a `general_catalog` lookup — the docstring in `graph.py` is wrong and gets fixed). `AgentTicketProcess.pm:274-286`: the start list uses `Active` only; dialogs on existing tickets use `Active`+`FadeAway`. `Ticket/Event/TicketProcessTransitions.pm` runs transitions only for `Active`+`FadeAway`. `Process.pm ProcessList` additionally keeps a process only if its **start dialog** lists the requested `Interface`.
- **Dialog `Display`**: `0` = do not show (use `DefaultValue`, DF without default is set to empty — `AgentTicketProcess.pm:4916-4927`), `1` = show, optional, `2` = show, mandatory (`Mandatory => Display == 2`, `AgentTicketProcess.pm:2695`, `4936`). A missing `Display` key counts as `0` (`_CheckField`: `if ( !$Param{Display} )`). The current Tiqora code (`ActivityDialogFieldConfig.required = display != "2"`, frontend `isFieldRequired = display === "1"`) is inverted — fixed in Task 1. `Queue`/`State`/`Lock`/`Priority` with `Display: 1` is a config error in Znuny (`_CheckField`); on a new ticket they fall back to `Process::Default{Queue,State,Lock,Priority}` (`AgentTicketProcess.pm:5135-5155`).
- **Customer interface** skips `Owner`, `Responsible`, `Lock`, `PendingTime`, `CustomerID` (`CustomerTicketProcess.pm:75`), writes as `CustomerPanelUserID` (Tiqora `PORTAL_SYSTEM_USER_ID = 1`), forces the customer's `CustomerID`/`CustomerUser` (`CustomerTicketProcess.pm:3675-3678`), article `SenderType customer`.
- **New process ticket** (`AgentTicketProcess.pm:5104-5290`): `TicketCreate` with `OwnerID => 1`, title default `"<ProcessName> - <now> (<OTRSTimeZone>)"` or the submitted `Subject`, title passed through smart tags; then `ProcessTicketProcessSet`, `ProcessTicketActivitySet` (start activity), then dynamic fields, then the remaining fields; afterwards `TicketOwnerSet` to the chosen owner (line ~5967); transitions run via the `TicketProcessTransitions` event module (one transition per request, loop-protected).
- **Enroll** (`IsProcessEnroll`, `AgentTicketProcess.pm:5293-5355`): an existing ticket gets the start dialog; process + start activity are set, then the dialog fields are stored, then transitions run. Tiqora's current "start" sets the two dynamic fields without the dialog — fixed in Task 13.
- **ACLs in the process screens**: process list `TicketAcl(ReturnType => 'Process', ReturnSubType => '-', Data => {eid => eid}, Action, TicketID)` (`AgentTicketProcess.pm:321-343`); dialog `TicketAcl(ReturnType => 'ActivityDialog', Data => {1 => eid}, ActivityDialogEntityID, TicketID)` (`:235-256`); action `TicketAcl(ReturnType => 'Action', Data => {1 => Action})` (`:156-177`). Customer interface passes `CustomerUserID` instead of `UserID`; `TicketAcl` skips only `UserID == 1` (`TicketACL.pm:175`) and builds `Checks{CustomerUser}` with `Group_<perm>` from customer groups (`TicketACL.pm:1253-1291`). Tiqora's portal currently runs every ACL as user 1 → every ACL is bypassed (fixed in Task 7/8).
- **GenericInterface does not apply ACLs**: `grep -rn TicketAcl znuny-6.5.22/Kernel/GenericInterface` is empty; `Operation/Ticket/Common.pm` validates via lookups, not `*List`. The compat API therefore stays ACL-free (deviation from spec, see below); pinned by a test in Task 15.
- **Deploy**: process "Deploy" = `ProcessDump(ResultType => 'FILE')` + `EntitySyncStatePurge` (`DELETE FROM pm_entity_sync`, `AdminProcessManagement.pm:1189-1210`); ACL "Deploy" = `ACLDump` + `ACLsNeedSyncReset` (`DELETE FROM acl_sync`, `AdminACL.pm:354-370`, `ACL/DB/ACL.pm:806-810`). Neither writes anything else to the DB. Admin edits write `pm_entity_sync` (`EntitySyncStateSet`, `sync_state` `not_sync`/`deleted`) and `acl_sync` (`ACLAdd`/`ACLUpdate`: `not_sync`, `ACLDelete`: `deleted`; plain `INSERT … VALUES (?, ?, current_timestamp, current_timestamp)` with `acl_id` as string, never upserted — `ACL/DB/ACL.pm:195-200, 254-259, 563-568`). **`ProcessImport` writes no sync rows** (`DB/Process.pm:1485ff`, `AdminProcessManagement.pm:150-210` only shows a "please synchronize" note) — see Known limitations.
- **Znuny 7.x YAML**: verified offline against upstream `rel-7_3_5` (fetched raw from `github.com/znuny/Znuny` on 2026-10-08): `Kernel/System/ProcessManagement/DB/Process.pm`, `Process.pm`, `Modules/AgentTicketProcess.pm` contain **zero** occurrences of `SequenceFlow` (149 resp. 58 of `Transition`); the TransitionAction and TransitionValidation module lists are identical to 6.5; `scripts/test/sample/ProcessManagement/{AgentTicketProcess,Complex1,CustomerTicketProcess}.yml` are byte-identical to 6.5.22; the 7.0–7.3 schema fixtures in `backend/tests/fixtures/legacy-schema/` keep `pm_transition`/`pm_transition_action`. "Sequence flow" is UI wording only. The one 7.2+ delta is the new table `pm_process_preferences` (`process_entity_id`, `preferences_key`, `preferences_value`), merged into `ProcessGet` as extra keys (process categories/info). → No alias parsing; the snapshot carries `pm_process_preferences` so phase 2 categories need no new format. A manual golden test on the 6.5 and 7.3 peers confirms (Task 20).
- **Smart tags** (`TransitionAction/Base.pm:339-610, 636-810`; `TransitionValidation/Base.pm:154-285`): every action runs `_ReplaceTicketAttributes` (top-level config string values; `<OTRS_TICKET_DynamicField_X_Value>` = display value, `<OTRS_TICKET_DynamicField_X>` = stored key, other `<OTRS_TICKET_Attr>` from `TicketGet`; an array attribute replaces the whole config value; `<OTRS_FIRST_ARTICLE_*>`/`<OTRS_LAST_ARTICLE_*>`) then `_ReplaceAdditionalAttributes` (full `TemplateGenerator::_Replace`, `Data` = last customer article, `DataAgent` = last agent article). Conditions: only the `Base`-derived types (`Equal`, `NotEqual`, `Contains`, `NotContains`, `GreaterThan[OrEqual]`, `LessThan[OrEqual]`) substitute — in the field name (`CheckValueGet`), in the data value found under it, and in `Match` (`MatchValueGet`). `String` and `Regexp` compare raw.

### D2. Dialog field semantics — `tiqora.process.fields`

```python
DISPLAY_HIDDEN = "0"
DISPLAY_OPTIONAL = "1"
DISPLAY_REQUIRED = "2"

# Znuny AgentTicketProcess NameToID: dialog configs may use either spelling.
FIELD_ALIASES: dict[str, str] = {
    "QueueID": "Queue", "StateID": "State", "PriorityID": "Priority", "LockID": "Lock",
    "TypeID": "Type", "ServiceID": "Service", "SLAID": "SLA", "OwnerID": "Owner",
    "ResponsibleID": "Responsible", "CustomerUserID": "CustomerUserID", "CustomerID": "CustomerID",
}
TICKET_REQUIRED_FIELDS = ("Queue", "State", "Lock", "Priority")
CUSTOMER_SKIP_FIELDS = frozenset({"Owner", "Responsible", "Lock", "PendingTime", "CustomerID"})

def canonical_field_name(name: str) -> str            # FIELD_ALIASES.get(name, name)
def dialog_field_order(dialog: ActivityDialogConfig) -> list[str]
    # dialog.field_order if non-empty else list(dialog.fields); de-duplicated after canonicalisation
def visible_fields(dialog: ActivityDialogConfig, interface: Interface) -> list[str]
    # field order minus hidden minus (CUSTOMER_SKIP_FIELDS when interface == "CustomerInterface")
def normalize_field_values(raw: Mapping[str, Any]) -> dict[str, Any]
    # canonical names; "Article": {"Subject","Body"} flattened to top-level "Subject"/"Body"
def effective_field_values(dialog: ActivityDialogConfig, submitted: Mapping[str, Any],
                           interface: Interface) -> dict[str, Any]
    # start from normalize_field_values(submitted) restricted to visible fields (+ Subject/Body when
    # Article is visible); every hidden field with a non-empty DefaultValue gets that default (overrides
    # any submitted value); every hidden DynamicField_* without default gets [] (Znuny clears it).
def missing_required_fields(dialog: ActivityDialogConfig, values: Mapping[str, Any],
                            interface: Interface) -> list[str]
    # required = Display "2" among visible fields; Article requires BOTH Subject and Body
    # (Znuny stores the article only when both are set, AgentTicketProcess.pm:5576)
```

`ActivityDialogFieldConfig` (`process/config.py`) changes: `display` default `"0"` when the key is absent; properties `hidden` (`display in {"", "0"}`), `required` (`display == "2"`). The module docstring's Display paragraph is rewritten with the D1 citation.

Engine (`_apply_dialog_field_changes`) additionally applies `Type` (`change_type`), `Service` (`change_service`), `SLA` (`change_sla`), `Lock` (`lock_ticket`/`unlock_ticket`, value `lock`/`unlock` or lock id). Article: channel from `Config.CommunicationChannel` (`Internal`→note, `Email`, `Phone`; legacy `ArticleType` still read), visibility from `Config.IsVisibleForCustomer` (default `0`), sender type `agent` (agent) / `customer` (customer) — never from the submitted values; created only when Subject and Body are both non-empty. `RequiredLock: '1'` (agent only, Znuny `AgentTicketProcess.pm:181-245`): ticket locked by someone else → `ProcessPermissionDenied("Das Ticket ist von einer anderen Person gesperrt.")`; unlocked → `lock_ticket` + `assign_owner(user)` before the fields are applied.

### D3. Deployed snapshots

Tables (one migration, Task 3):

```
tiqora_process_revision
  id           BIGINT PK autoincrement
  source       VARCHAR(20) NOT NULL     -- 'initial' | 'znuny' | 'tiqora'
  checksum     VARCHAR(64) NOT NULL     -- sha256 hex of canonical_json(config)
  entity_count INT NOT NULL             -- number of entities in the dump (all kinds)
  config       TEXT NOT NULL (MEDIUMTEXT on mysql/mariadb)   -- canonical JSON of the dump
  deployed_at  DATETIME NOT NULL        -- app-written naive UTC
  deployed_by  INT NULL                 -- users.id; NULL = detected / system
  INDEX ix_tiqora_process_revision_checksum (checksum)

tiqora_acl_revision                     -- same columns, own indexes
  INDEX ix_tiqora_acl_revision_checksum (checksum)
```

ORM: `TiqoraProcessRevision`, `TiqoraAclRevision`; module constant `REVISION_CONFIG_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")`; `config` is `deferred()`.

Process dump = Znuny `ProcessDump(ResultType => 'HASH')` shape (`DB/Process.pm ProcessDump`) plus `ID` on every entity, `Description` on processes and the Tiqora key `ProcessPreferences`:

```
{
  "Process":          {eid: {"ID", "Name", "CreateTime", "ChangeTime", "StateEntityID", "State",
                             "StartActivity", "StartActivityDialog", "Path", "Description"}},
  "State":            {"S1": "Active", "S2": "Inactive", "S3": "FadeAway"},
  "Activity":         {eid: {"ID", "Name", "CreateTime", "ChangeTime", "ActivityDialog"}},
  "ActivityDialog":   {eid: {"ID", "Name", "CreateTime", "ChangeTime", "Interface", "DescriptionShort",
                             "DescriptionLong", "Fields", "FieldOrder", "Permission", "RequiredLock",
                             "SubmitAdviceText", "SubmitButtonText"}},
  "Transition":       {eid: {"ID", "Name", "CreateTime", "ChangeTime", "Condition", "ConditionLinking"}},
  "TransitionAction": {eid: {"ID", "Name", "CreateTime", "ChangeTime", "Module", "Config"}},
  "ProcessPreferences": {process_eid: {key: value}}      # {} below Znuny 7.2 (profile without the table)
}
```

Empty-value defaults exactly as Znuny's dump (`|| ''`, `|| {}`, `|| []`): `StartActivity ''`, `StartActivityDialog ''`, `Path {}`, `ActivityDialog ''`, `Interface ''`, `DescriptionShort ''`, `DescriptionLong ''`, `Fields {}`, `FieldOrder []`, `Permission ''`, `RequiredLock ''`, `SubmitAdviceText ''`, `SubmitButtonText ''`, `Condition {}`, `ConditionLinking ''`, `Module ''`, `Config {}`. `CreateTime`/`ChangeTime` as `"YYYY-MM-DD HH:MM:SS"`.

ACL dump = Znuny `ACLDump` shape, valid ACLs only, name sanitised like Znuny (`s{[\r\n\\'"]}{}g`):

```
{"ACL": {name: {"ID", "CreateTime", "ChangeTime", "CreateBy", "ChangeBy", "Comment",
                "Values": {"StopAfterMatch": 0|1, "Properties": {}, "PropertiesDatabase": {},
                           "Possible": {}, "PossibleAdd": {}, "PossibleNot": {}}}}}
```

`CreateBy`/`ChangeBy` are user logins (Znuny `ACLListGet`).

```python
# backend/src/tiqora/process/dump.py
PROCESS_STATE_NAMES: dict[str, str] = {"S1": "Active", "S2": "Inactive", "S3": "FadeAway"}
STATE_ACTIVE, STATE_INACTIVE, STATE_FADEAWAY = "S1", "S2", "S3"
def canonical_json(data: Any) -> str        # json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
def dump_checksum(data: Any) -> str         # sha256(canonical_json(data).encode()).hexdigest()
def dump_entity_count(dump: Mapping[str, Any]) -> int   # sum of len() over all kinds except "State"
async def build_process_dump(session: AsyncSession) -> dict[str, Any]
    # reads all pm_* rows (+ pm_process_preferences when the detected legacy profile has it)

# backend/src/tiqora/deployed/acl_dump.py
async def build_acl_dump(session: AsyncSession) -> dict[str, Any]
```

`process/config.py` gains `from_dict(data: Mapping[str, Any])` on `ProcessConfig`, `ActivityConfig`, `ActivityDialogConfig`, `TransitionConfig`, `TransitionActionConfig`; each `from_yaml(raw)` becomes `cls.from_dict(_load_yaml_mapping(raw))`. `from_dict` treats `''` like a missing mapping/list (Znuny dump defaults) and maps a falsy `StartActivity`/`StartActivityDialog` (`''` in Znuny's dump) to `None` — `start_process` and `ProcessGraph.start_activity` test `is None`, so `''` must never reach them.

Store:

```python
# backend/src/tiqora/deployed/store.py
RevisionKind = Literal["process", "acl"]
RevisionSource = Literal["initial", "znuny", "tiqora"]

@dataclass(frozen=True, slots=True)
class RevisionInfo:
    id: int
    kind: RevisionKind
    source: str
    checksum: str
    entity_count: int
    deployed_at: datetime          # naive UTC
    deployed_by: int | None

@dataclass(frozen=True, slots=True)
class LoadedRevision:
    info: RevisionInfo
    dump: Mapping[str, Any]        # parsed JSON; treat as read-only

async def latest_revision_info(session: AsyncSession, kind: RevisionKind) -> RevisionInfo | None
async def load_active_revision(session: AsyncSession, kind: RevisionKind) -> LoadedRevision | None
    # SELECT the newest id; if it equals the cached id return the cached LoadedRevision,
    # else load config, json.loads, cache (module-level dict per kind). One small query per call.
async def record_revision(session: AsyncSession, kind: RevisionKind, *, dump: Mapping[str, Any],
                          source: RevisionSource, deployed_by: int | None) -> RevisionInfo
    # INSERT (flush only, caller commits); deployed_at = utc_now()
def clear_revision_cache() -> None            # tests
```

Process source (the engine never reads `pm_*` directly any more):

```python
# backend/src/tiqora/process/source.py
EntityKind = Literal["Process", "Activity", "ActivityDialog", "Transition", "TransitionAction"]

@dataclass(frozen=True, slots=True)
class EntityRecord:
    id: int
    entity_id: str
    name: str
    data: Mapping[str, Any]        # parsed config (dump entry or yaml.safe_load of pm_*.config)
    state_entity_id: str = ""      # processes only
    layout: Mapping[str, Any] = field(default_factory=dict)   # processes only, live source only

class ProcessSource(Protocol):
    revision_id: int | None        # None = live pm_* rows
    async def list_processes(self) -> list[EntityRecord]
    async def load(self, kind: EntityKind, entity_ids: Iterable[str]) -> dict[str, EntityRecord]

class LiveProcessSource:      def __init__(self, session: AsyncSession) -> None
class RevisionProcessSource:  def __init__(self, revision: LoadedRevision) -> None
async def active_process_source(session: AsyncSession) -> ProcessSource
    # newest process revision → RevisionProcessSource; none → LiveProcessSource + logger.warning once
    # per process ("process_definitions_running_live", reason="no deployed revision yet")
```

`ProcessRepository(session, source: ProcessSource | None = None)` resolves `active_process_source` lazily on first use and builds every node from `EntityRecord` + `Config.from_dict`. `ProcessSummary` gains `state_name: str` (`PROCESS_STATE_NAMES.get(state_entity_id, "")`). `ProcessRepository.get_activity_dialog(entity_id)` returns `ActivityDialogNode | None` (was a `PmActivityDialog` row); callers in `api/v1/process.py` / `api/portal/process.py` use `node.config` / `node.name`.

ACL evaluation reads the newest ACL revision too:

```python
# backend/src/tiqora/domain/ticket_acl.py
@dataclass(frozen=True, slots=True)
class AclRule:
    name: str
    stop_after_match: bool
    config_match: dict[str, Any]       # {"Properties": …, "PropertiesDatabase": …}
    config_change: dict[str, Any]      # {"Possible": …, "PossibleAdd": …, "PossibleNot": …}

async def load_valid_acls(session: AsyncSession) -> list[AclRule]
    # newest ACL revision → rules sorted by name; none → live valid acl rows (today's behaviour)
```

### D4. Deploy detection, sync tables, admin status

```python
# backend/src/tiqora/deployed/sync_tables.py
AclSyncState = Literal["not_sync", "deleted"]
async def pending_sync_count(session: AsyncSession, kind: RevisionKind) -> int   # COUNT(*) pm_entity_sync | acl_sync
async def mark_acl_changed(session: AsyncSession, *, acl_id: int, sync_state: AclSyncState) -> None
    # text("INSERT INTO acl_sync (acl_id, sync_state, create_time, change_time)
    #       VALUES (:acl_id, :state, current_timestamp, current_timestamp)"), acl_id=str(acl_id)
async def clear_sync_table(session: AsyncSession, kind: RevisionKind) -> int   # DELETE …; ownership mode only

# backend/src/tiqora/deployed/watcher.py
WatchAction = Literal["unchanged", "snapshot", "waiting_for_deploy", "running_live"]

@dataclass(frozen=True, slots=True)
class WatchOutcome:
    kind: RevisionKind
    action: WatchAction
    revision_id: int | None
    pending_changes: int

async def check_deploy_state(session: AsyncSession, kind: RevisionKind, *,
                             ownership_active: bool) -> WatchOutcome
async def run_deploy_watch_tick(*, settings: Settings,
                                session_factory: async_sessionmaker[AsyncSession] | None = None) -> dict[str, Any]
    # {"mode": "deployed" | "live", "process": {...WatchOutcome fields}, "acl": {...}}; commits once
```

`check_deploy_state` rules (per kind):

| ownership active | pending sync rows | newest revision | dump checksum | action |
|---|---|---|---|---|
| no | > 0 | none | – | `running_live` (engine runs `pm_*`/`acl`; "Dienste" page warns) |
| no | > 0 | exists | – | `waiting_for_deploy` (drafts exist; revision keeps running) |
| no | 0 | none | – | record `source="initial"` → `snapshot` (the spec's startup rule; the worker's first tick runs at start) |
| no | 0 | exists | ≠ newest | record `source="znuny"` → `snapshot` |
| no | 0 | exists | = newest | `unchanged` |
| yes | any | none or ≠ | – | record `source="tiqora"`, then `clear_sync_table` → `snapshot` (Tiqora is the only system after cutover; phase 3 replaces this with an explicit deploy) |
| yes | any | = newest | – | `clear_sync_table` when pending > 0 → `unchanged` |

`ownership_active` = `tiqora.domain.ownership.get_ownership_state(session, settings).active`. The dump is built only when it is needed (pending == 0 or ownership active).

Worker: `_interval_loop("deploy_watch", deploy_watch_tick, settings.deploy_watch_interval_seconds, None, stop)` in `worker/__main__.py`. Config `deploy_watch_interval_seconds: int = Field(30, ge=5, le=3600)` (env `TIQORA_DEPLOY_WATCH_INTERVAL_SECONDS`). Catalog `DaemonService(slug="deploy_watch", enabled_key=None, default_enabled=True, toggleable=False, schedule_kind="interval", interval_key=None, interval_settings_attr="deploy_watch_interval_seconds")`.

Admin ACL writes (`api/v1/admin/acl.py`): create → `mark_acl_changed(not_sync)` after flush (needs the id); update → `not_sync`; delete → `deleted`. All in the same transaction as the row change. `pm_entity_sync` is not written by Tiqora in this phase (no process write path exists).

Admin API (`api/v1/admin/deploy_status.py`, router prefix `""`, tags `admin:deploy`):

```python
class RevisionOut(BaseModel):
    id: int
    source: str
    entity_count: int
    deployed_at: UtcDateTime
    deployed_by: int | None
class DeployKindStatusOut(BaseModel):
    revision: RevisionOut | None
    pending_changes: int
    running_live: bool                 # no revision yet → engine reads live rows
class DeployStatusOut(BaseModel):
    process: DeployKindStatusOut
    acl: DeployKindStatusOut
    ownership_active: bool
class AdminProcessOut(BaseModel):
    entity_id: str
    name: str
    state_entity_id: str
    state_name: str                     # Active | Inactive | FadeAway | ""
    activity_count: int
    start_interfaces: list[str]         # Interface list of the start dialog
GET /api/v1/admin/deploy-status -> DeployStatusOut
GET /api/v1/admin/processes     -> list[AdminProcessOut]   # all processes of the active source
```

### D5. ACL principal and the one process-ACL function

```python
# backend/src/tiqora/domain/ticket_acl.py
@dataclass(frozen=True, slots=True)
class AclPrincipal:
    user_id: int | None = None                 # agent
    customer_user_login: str | None = None     # customer
    @classmethod
    def agent(cls, user_id: int) -> AclPrincipal
    @classmethod
    def customer(cls, login: str) -> AclPrincipal

ProcessAclKind = Literal["Process", "ActivityDialog", "Action"]

async def load_customer_user_checks(session: AsyncSession, login: str) -> dict[str, Any]
    # Znuny CustomerUserDataGet subset: UserLogin, UserID (=login), UserCustomerID, UserEmail,
    # UserFirstname, UserLastname, UserFullname, UserTitle + Group_<perm> lists from valid
    # group_customer_user rows (permission_value = 1; "rw" implies every key like the agent side)

async def apply_ticket_acl(session, *, user_id: int | None, data, return_type, return_sub_type,
                           ticket_id=None, action=None, checks=None, customer_user_id=None,
                           customer_user_login: str | None = None,
                           process: Mapping[str, str] | None = None) -> dict[Any, str]
    # unchanged for agents; root bypass ONLY when user_id == 1; user_id None + customer_user_login
    # → Checks{CustomerUser} from the login, Checks{Ticket}{CustomerUserID/CustomerID} overridden
    # (TicketACL.pm:1253-1275); agent with a ticket → Checks{CustomerUser} from the ticket's
    # customer (TicketACL.pm:1276-1291); `process` → Checks{Process}{ProcessEntityID,
    # ActivityEntityID, ActivityDialogEntityID} (live side only, TicketACL.pm:1118-1146);
    # both principals None → original data (Znuny logs "Need UserID or CustomerUserID")

async def filter_process_options(session: AsyncSession, *, principal: AclPrincipal,
                                 kind: ProcessAclKind, items: Sequence[str], action: str,
                                 ticket_id: int | None = None,
                                 process_entity_id: str | None = None,
                                 activity_dialog_entity_id: str | None = None) -> list[str]
    # data = {item: item} (Znuny passes entities, not names); return_sub_type "-";
    # returns the kept items in input order
```

Existing callers of `apply_ticket_acl`/`filter_id_name_map` keep working unchanged (keyword `user_id=` stays). The spec's `filter_process_options(user, ticket, kind)` is widened to this signature (principal type, action, process context) — see Deviations.

### D6. Availability per surface — `tiqora.process.availability` + `tiqora.process.actor`

```python
# backend/src/tiqora/process/actor.py
Interface = Literal["AgentInterface", "CustomerInterface"]

@dataclass(frozen=True, slots=True)
class ProcessActor:
    interface: Interface
    user_id: int                          # whose id writes are attributed to
    customer_user_login: str | None = None
    customer_id: str | None = None
    @classmethod
    def agent(cls, user_id: int) -> ProcessActor
    @classmethod
    def customer(cls, *, login: str, customer_id: str) -> ProcessActor   # user_id = CUSTOMER_PANEL_USER_ID

CUSTOMER_PANEL_USER_ID = 1   # = domain.portal_ticket_service.PORTAL_SYSTEM_USER_ID (Znuny CustomerPanelUserID);
                             # defined here to keep tiqora.process free of the portal service import graph
    @property
    def frontend_action(self) -> str      # "AgentTicketProcess" | "CustomerTicketProcess"
    @property
    def acl_principal(self) -> AclPrincipal

# backend/src/tiqora/process/availability.py
STARTABLE_STATES = frozenset({"S1"})
RUNNING_STATES = frozenset({"S1", "S3"})

def dialog_allows(dialog: ActivityDialogConfig, interface: Interface) -> bool
    # Interface list contains `interface`; an empty/missing list matches NO interface (Znuny
    # ActivityDialog.pm ActivityDialogGet logs "No Interface … found" and returns nothing)
async def list_startable_processes(session: AsyncSession, actor: ProcessActor, *,
                                   ticket_id: int | None = None,
                                   repository: ProcessRepository | None = None) -> list[ProcessSummary]
    # state S1 + start dialog exists and dialog_allows(actor.interface) + ACL kind "Process"
async def process_action_allowed(session: AsyncSession, actor: ProcessActor, *, ticket_id: int | None) -> bool
    # ACL kind "Action", items [actor.frontend_action]
async def available_dialogs(session: AsyncSession, actor: ProcessActor, *, ticket_id: int,
                            candidates: TicketProcessCandidates) -> list[ActivityDialogNode]
    # [] unless process state in RUNNING_STATES and process_action_allowed; then
    # dialog_allows(actor.interface) + one ACL call kind "ActivityDialog" over all remaining dialogs
async def assert_can_start(session, actor, *, process_entity_id: str, ticket_id: int | None,
                           repository: ProcessRepository) -> ProcessGraph
    # ProcessNotFound when unknown; ProcessNotStartable (409) when not S1 / no start dialog for the
    # interface / ACL-hidden / action not allowed
async def assert_can_submit(session, actor, *, ticket_id: int, candidates: TicketProcessCandidates,
                            activity_dialog_entity_id: str) -> ActivityDialogNode
    # ProcessNotRunning (409) when state not in RUNNING_STATES; ActivityDialogNotAvailable (409)
    # when not on the current activity; ProcessPermissionDenied (403) when interface/ACL forbid
```

New exceptions in `process/exceptions.py`: `ProcessNotStartable`, `ProcessNotRunning` (both → 409; portal maps them to 409 as well).

`submit_activity_dialog(…, actor: ProcessActor | None = None)` — `None` means `ProcessActor.agent(user_id)` (keeps every existing caller and test working) — calls `assert_can_submit`. `run_transitions` returns without evaluating when the process state is not in `RUNNING_STATES`.

Schema changes (`process/schemas.py`):

```python
class ProcessSummaryOut(BaseModel):       # + state_name: str = ""
class TicketProcessStateOut(BaseModel):   # + process_state: str | None = None   ("Active"|"Inactive"|"FadeAway")
                                          # + can_start: bool = False            (not in a process, action
                                          #   allowed, ≥ 1 startable process)
class ProcessStartIn(BaseModel):          # + field_values: dict[str, Any] = Field(default_factory=dict)
```

Agent `GET /api/v1/process/` and portal `GET /api/portal/process/` return `list_startable_processes` (optional query `ticket_id` for the enroll context, access-checked). The admin page switches to `GET /api/v1/admin/processes` (Task 6).

### D7. Condition data + smart tags

```python
# backend/src/tiqora/process/conditions.py
ConditionValue = str | list[str] | None
ConditionData = dict[str, ConditionValue]
BASE_CONDITION_TYPES = frozenset({"equal", "notequal", "contains", "notcontains", "greaterthan",
    "greaterthanorequal", "greaterthanequals", "greaterthanequal", "lessthan", "lessthanorequal",
    "lessthanequals", "lessthanequal"})

async def get_condition_data(session: AsyncSession, ticket_id: int) -> ConditionData
    # Znuny TicketGet(DynamicFields => 1) subset: TicketID, TicketNumber, Title, Queue, QueueID,
    # State, StateID, StateType, Priority, PriorityID, Lock, LockID, Type, TypeID, Service, ServiceID,
    # SLA, SLAID, Owner (login), OwnerID, Responsible (login), ResponsibleID, CustomerID,
    # CustomerUserID, ArchiveFlag ("y"/"n"), Created, Changed; every valid ticket DF as
    # DynamicField_<Name>: None when unset, list[str] for Multiselect, str otherwise
    # (Date/DateTime "YYYY-MM-DD HH:MM:SS"). IDs as str. Unset optional attributes → None.

def evaluate_condition_field(field_name: str, cond: TransitionConditionField, data: Mapping[str, ConditionValue],
                             *, tags: Mapping[str, str] | None = None) -> bool
def evaluate_transition(transition_config: TransitionConfig, ticket_attrs: Mapping[str, ConditionValue],
                        *, tags: Mapping[str, str] | None = None) -> bool
    # moved from engine.py (engine re-exports it); `tags` maps raw text → substituted text
async def resolve_transition_tags(transition_config: TransitionConfig, data: Mapping[str, ConditionValue],
                                  *, render: Callable[[str], Awaitable[str]]) -> dict[str, str]
    # collects every string that contains "<OTRS_" (case-insensitive) among: field names and Match
    # values of BASE_CONDITION_TYPES conditions; the data value under the (substituted) field name
    # (two rounds); renders each once
```

Per-type semantics (ports, cite the Znuny file in comments): `String` (`String.pm:99-170`): Match must be a non-empty string or `"0"`, else no match; value must be defined; str → `==`; list → any element `==`. `Regexp` (`Regexp.pm:99-175`): value must be truthy; str → `re.search`; list → any element. Base types (`Base.pm:154-285`, `346-610`): check value = `data.get(name_substituted)`, `None`/missing → the substituted field name itself (Znuny `//`); a falsy check value returns it unchanged; then substitute tags inside it, then `ValueValidate` (date/datetime → epoch); match value substituted then validated. `Equal`/`NotEqual`/`Contains`/`NotContains`/ordered compares follow `Base.pm` incl. list vs comma-separated string rules; `Equal("", x)` never matches (Znuny `IsStringWithData`). Unknown type → `False` + warning (unchanged).

```python
# backend/src/tiqora/process/smart_tags.py
async def build_tag_context(session: AsyncSession, *, ticket_id: int, user_id: int,
                            sysconfig: SysConfig) -> PlaceholderContext
    # load_placeholder_context(ticket_id, user_id) + last customer article (customer_subject,
    # customer_body, customer_email_lines from its From) + last agent article (agent_subject,
    # agent_body) — Znuny _ReplaceAdditionalAttributes Data/DataAgent
async def render_tags(text: str, *, ctx: PlaceholderContext, sysconfig: SysConfig) -> str
    # expand_placeholders(None, sysconfig, text, context=ctx)
async def replace_action_config(config: Mapping[str, Any], *, session: AsyncSession, ticket_id: int,
                                user_id: int, sysconfig: SysConfig) -> dict[str, Any]
    # deep copy; step 1 _ReplaceTicketAttributes on top-level str values (DF key / DF display value /
    # TicketGet attribute; list attribute replaces the whole value; FIRST/LAST_ARTICLE Subject, Body,
    # From, To, Cc, SenderType, ArticleID); step 2 render_tags on every top-level str value that still
    # contains "<OTRS_"; non-string values untouched
```

`channels/email/placeholder.py` `load_placeholder_context` adds to the ticket map, for every valid ticket DF with a value: `dynamicfield_<name>` (stored value, multi-value joined `", "`) and `dynamicfield_<name>_value` (display value: Dropdown/Multiselect via `PossibleValues`, Checkbox `"1"`→`Checked`/`"0"`→`Unchecked`, others = stored value). This also makes `<OTRS_TICKET_DynamicField_*>` resolve in templates and notifications (today they render empty).

Engine wiring: `execute_transition_action` calls `replace_action_config` before the handler (config copied, never mutated); `run_transitions` builds `ConditionData` once per transition check, resolves tags with `render_tags` over one `build_tag_context`, and calls `evaluate_transition(…, tags=…)`.

### D8. PendingTime in dialogs

`field_values["PendingTime"]`: ISO 8601 datetime string with offset (e.g. `"2026-10-12T08:00:00Z"`), the same contract as `PATCH /tickets/{id}` `pending_time`. Applied after `State` (Znuny stores fields in `FieldOrder`; PendingTime only matters when the resulting state is pending): resolved target state = submitted State → dialog `State` DefaultValue → ticket's current state (`AgentTicketProcess.pm:1253-1330`); when its type starts with `pending` → `_set_ticket_pending_time` (writes `until_time` + `SetPendingTime` history like `TicketPendingTimeSet`); otherwise ignored. Invalid string → `UnresolvedFieldValue` (400). `PendingTime` with `Display: 2` and a pending target state but no value → `RequiredFieldMissing`. Customers never submit it (`CUSTOMER_SKIP_FIELDS`).

### D9. Server-built dialog form — `tiqora.process.dialog_form`

```python
FieldKind = Literal["text", "textarea", "select", "multiselect", "boolean", "date", "datetime",
                    "article", "pending_time", "customer", "unsupported"]

class ProcessDialogOptionOut(BaseModel):
    value: str
    label: str
class ProcessDialogFieldOut(BaseModel):
    name: str                         # canonical name, e.g. "Queue", "DynamicField_Region", "Article"
    kind: FieldKind
    label: str                        # DF label; ticket attributes: the canonical name (frontend translates)
    required: bool
    description_short: str = ""
    description_long: str = ""
    default: Any = None
    options: list[ProcessDialogOptionOut] = Field(default_factory=list)
class ProcessDialogFormOut(BaseModel):
    process_entity_id: str
    activity_dialog_entity_id: str
    name: str
    description_short: str
    description_long: str
    submit_button_text: str
    submit_advice_text: str
    fields: list[ProcessDialogFieldOut]

async def build_dialog_form(session: AsyncSession, *, actor: ProcessActor, user: AuthenticatedUser | None,
                            graph: ProcessGraph, dialog: ActivityDialogNode,
                            ticket_id: int | None) -> ProcessDialogFormOut
    # `user`: the agent's AuthenticatedUser (agent routes pass CurrentUser; wave-0
    # collect_field_options needs it); None for customers (portal routes)
```

Visible fields only (`visible_fields`). Kinds: `Title`→text; `Queue`/`State`/`Priority`/`Type`/`Service`/`SLA`/`Owner`/`Responsible`/`Lock`→select; `CustomerUserID`/`CustomerID`→customer (agent only; one `customer` field named `CustomerUserID` carries both); `Article`→article; `PendingTime`→pending_time; `DynamicField_X` by DF type (Text→text, TextArea→textarea, Dropdown→select, Multiselect→multiselect, Checkbox→boolean, Date→date, DateTime→datetime, other→unsupported); `Attachments` and anything else → unsupported (phase 2). Options:

- Agent: wave-0 `tiqora.domain.field_options.collect_field_options(session, user, FieldOptionsRequest(fields=[…], context=FieldOptionsContext(action="AgentTicketProcess", ticket_id=ticket_id, queue_id=…)))` for `queue`/`state`/`priority`/`type`/`service`/`sla` and `DynamicField_*`; `owner`/`responsible` from `collect_ticket_field_options` (extended in Task 15) — so the dialog form ACL-filters exactly like the zoom pickers. Values are ids as strings; DF values are DF keys.
- Customer: `collect_customer_field_options(session, actor, fields, ticket_id)` in `dialog_form.py`: valid rows of the attribute tables (queues: all valid queues except Znuny's system queues `Postmaster`, `Raw`, `Junk` — Znuny's default `TicketMoveList` for customers without `CustomerPanelOwnSelection`; see Known limitations), DF `PossibleValues`; each list filtered with `apply_ticket_acl(user_id=None, customer_user_login=…, action="CustomerTicketProcess", return_type="Ticket", return_sub_type=<Queue|State|…|DynamicField_X>, process={…})`.
- Lock options: `lock`/`unlock` (labels `lock`/`unlock`; frontend translates), no ACL (Znuny `_RenderLock`: "no ACL checking yet").

### D10. New process ticket + enroll — `tiqora.process.process_ticket`

```python
@dataclass(frozen=True, slots=True)
class ProcessTicketResult:
    ticket_id: int
    ticket_number: str
    transition: TransitionOutcome

async def create_process_ticket(session: AsyncSession, session_factory: async_sessionmaker[AsyncSession],
                                sysconfig: SysConfig, *, actor: ProcessActor, process_entity_id: str,
                                field_values: Mapping[str, Any]) -> ProcessTicketResult
async def enroll_ticket(session: AsyncSession, sysconfig: SysConfig, *, actor: ProcessActor,
                        ticket_id: int, process_entity_id: str,
                        field_values: Mapping[str, Any]) -> TransitionOutcome
```

`create_process_ticket` order (Znuny `AgentTicketProcess.pm:5104-5290`, `CustomerTicketProcess.pm:3660-3800`): `assert_can_start` → start dialog → `effective_field_values` + `missing_required_fields` → resolve `Queue`/`State`/`Lock`/`Priority` from values, else `Process::Default{Queue,State,Lock,Priority}` sysconfig (by name; unknown → `RequiredFieldMissing("Process::Default<X> fehlt oder ist ungültig.")`) → title = `Title` value, else `Subject`, else `"<process name> - <now in OTRSTimeZone, YYYY-MM-DD HH:MM:SS> (<OTRSTimeZone>)"`, then `render_tags` over a ticket-less context → `create_ticket(TicketIn(owner_id=1, …, customer from values (agent) or forced from actor (customer), type/service/sla from values))` → `start_process` (sets both DFs) → `apply_activity_dialog` for the remaining fields (DFs, Article, PendingTime, Responsible, Lock…) → `assign_owner` to the chosen `Owner` when it differs from 1 → `run_transitions` → result. Agent requires `create` permission on the resolved queue (`PermissionEngine.check(user, queue_id, "create")`, else `ProcessPermissionDenied`).

`enroll_ticket`: `assert_can_start(…, ticket_id)` → ticket not already in a process (`TicketAlreadyInProcess`) → `start_process` → `apply_activity_dialog` with the start dialog → `run_transitions`.

`engine.py` additions:

```python
@dataclass(frozen=True)
class TransitionOutcome:
    activity_changed: bool
    new_activity_entity_id: str | None
    transition_entity_id: str | None
    unsupported_actions: list[str]

async def apply_activity_dialog(session: AsyncSession, *, ticket_id: int, dialog: ActivityDialogConfig,
                                field_values: Mapping[str, Any], actor: ProcessActor,
                                sysconfig: SysConfig) -> None
    # effective values → required check → RequiredLock → _apply_dialog_field_changes → PendingTime
async def run_transitions(session: AsyncSession, *, ticket_id: int, user_id: int,
                          sysconfig: SysConfig) -> TransitionOutcome
```

`submit_activity_dialog` = `assert_can_submit` + `apply_activity_dialog` + `run_transitions`; `ActivityDialogSubmitResult` stays (built from `TransitionOutcome`).

Endpoints:

```python
class NewProcessTicketIn(BaseModel):
    process_entity_id: str
    field_values: dict[str, Any] = Field(default_factory=dict)
class NewProcessTicketOut(BaseModel):
    ticket_id: int
    ticket_number: str
    activity_changed: bool
    unsupported_actions: list[str]
    state: TicketProcessStateOut

# agent  /api/v1/process
POST /tickets                                    -> 201 NewProcessTicketOut
GET  /{process_entity_id}/start-form?ticket_id=  -> ProcessDialogFormOut   (enroll context optional)
GET  /ticket/{ticket_id}/dialog/{activity_dialog_entity_id}/form -> ProcessDialogFormOut
POST /ticket/{ticket_id}/start  (ProcessStartIn incl. field_values) -> TicketProcessStateOut   (enroll)
# portal /api/portal/process — same four, customer actor, ticket access via customer_can_access_ticket
```

`GET /process/activity-dialog/{id}` (agent and portal) stays for API clients but is no longer used by the UI.

### D11. Zoom action menu, bulk, compat

```python
# backend/src/tiqora/domain/ticket_action_acl.py
ZOOM_ACTIONS: tuple[str, ...] = (
    "AgentTicketCompose", "AgentTicketForward", "AgentTicketNote", "AgentTicketPhoneOutbound",
    "AgentTicketPhoneInbound", "AgentTicketClose", "AgentTicketPending", "AgentTicketPriority",
    "AgentTicketMove", "AgentTicketOwner", "AgentTicketResponsible", "AgentTicketCustomer",
    "AgentTicketLock", "AgentTicketWatcher", "AgentLinkObject", "AgentTicketMerge",
    "AgentTicketPrint", "AgentTicketFreeText", "AgentTicketProcess",
)
async def allowed_ticket_actions(session: AsyncSession, *, user_id: int, ticket_id: int) -> list[str]
    # one apply_ticket_acl(return_type="Action", return_sub_type="-", action="AgentTicketZoom",
    # data={i: name}) like Znuny's zoom menu (AgentTicketZoom.pm TicketAclActionData)
class TicketAclActionsOut(BaseModel):
    allowed: list[str]
GET /api/v1/tickets/{ticket_id}/acl-actions -> TicketAclActionsOut   (ro access check like GET /tickets/{id})
```

Frontend hides the mapped menu entries (D12). Server-side enforcement is limited to `AgentTicketProcess`/`CustomerTicketProcess` (process endpoints); the other actions stay UI-only in this phase (Known limitations).

Bulk: `collect_ticket_field_options` (+ `TicketFieldOptionsOut`) gains `owner` and `responsible` (data `{user_id: login}` filtered with `ReturnSubType` `Owner`/`Responsible` like Znuny `_GetOwners`/`_GetResponsibles`, labels = full names). QueuesPage loads `fields=state,priority,queue,owner,responsible&action=AgentTicketBulk` (no ticket context, Znuny `AgentTicketBulk.pm`) and keeps only allowed entries in the bulk pickers.

Compat API (`api/compat/*`): unchanged — Znuny GI applies no ACLs (D1). A test pins it.

### D12. Frontend

```ts
// frontend/src/lib/processApi.ts
export type ProcessSummary = Schemas["ProcessSummaryOut"];
export type TicketProcessState = Schemas["TicketProcessStateOut"];
export type ProcessDialogFormData = Schemas["ProcessDialogFormOut"];
export type ProcessDialogFieldData = Schemas["ProcessDialogFieldOut"];
export type NewProcessTicketIn = Schemas["NewProcessTicketIn"];
export type NewProcessTicketOut = Schemas["NewProcessTicketOut"];
export type ActivityDialogSubmitOut = Schemas["ActivityDialogSubmitOut"];
export type TicketAclActions = Schemas["TicketAclActionsOut"];
export const processApi = {
  listStartable(opts?: { ticketId?: number }, signal?: AbortSignal): Promise<ProcessSummary[]>,
  startForm(processEntityId: string, opts?: { ticketId?: number }, signal?: AbortSignal): Promise<ProcessDialogFormData>,
  dialogForm(ticketId: number, dialogEntityId: string, signal?: AbortSignal): Promise<ProcessDialogFormData>,
  createTicket(body: NewProcessTicketIn): Promise<NewProcessTicketOut>,
  enroll(ticketId: number, body: { process_entity_id: string; field_values: Record<string, unknown> }): Promise<TicketProcessState>,
  submit(ticketId: number, body: { activity_dialog_entity_id: string; field_values: Record<string, unknown> }): Promise<ActivityDialogSubmitOut>,
  ticketState(ticketId: number, signal?: AbortSignal): Promise<TicketProcessState>,
  aclActions(ticketId: number, signal?: AbortSignal): Promise<TicketAclActions>,
};
// frontend/src/lib/portalProcessApi.ts — same shape minus aclActions, paths under /api/portal/process
export const portalProcessApi = { listStartable, startForm, dialogForm, createTicket, enroll, submit, ticketState };

// frontend/src/lib/deployStatusApi.ts
export type DeployStatus = Schemas["DeployStatusOut"];
export type AdminProcess = Schemas["AdminProcessOut"];
export const deployStatusApi = {
  status(signal?: AbortSignal): Promise<DeployStatus>,
  processes(signal?: AbortSignal): Promise<AdminProcess[]>,
};

// frontend/src/components/process/processForm.ts
export const ARTICLE_SUBJECT_KEY = "Article.Subject";
export const ARTICLE_BODY_KEY = "Article.Body";
export const CUSTOMER_ID_KEY = "CustomerID";
export function processFieldDefs(form: ProcessDialogFormData, t: TFunction): FieldDef[];
  // article → two defs (ARTICLE_SUBJECT_KEY text, ARTICLE_BODY_KEY textarea; both required when the
  // field is required); customer → two text defs (CustomerUserID, CustomerID); pending_time → datetime;
  // date/datetime/boolean/select/multiselect/text/textarea 1:1 with static `options` (no optionsKey);
  // unsupported → readOnly text def with help t("process.form.unsupported")
export function initialProcessValues(form: ProcessDialogFormData): FieldValues;   // from field.default
export function toSubmitValues(form: ProcessDialogFormData, values: FieldValues, zone?: string): Record<string, unknown>;
  // ARTICLE_* → "Subject"/"Body"; DynamicField_* → fieldValueToDf(kind, value, zone);
  // pending_time / datetime → UTC ISO via fromZonedInputValue; select → the id string; boolean → "1"/"0"
export function processLabel(field: ProcessDialogFieldData, t: TFunction): string;
  // DF → field.label; ticket attributes → t(`process.field.${name}`)

// frontend/src/components/process/ProcessDialogForm.tsx
export function ProcessDialogForm(props: { form: ProcessDialogFormData; values: FieldValues;
  onChange: (key: string, value: FieldValue) => void; errors?: FieldErrors; disabled?: boolean;
  testId?: string /* default "process-form" */ }): JSX.Element;
  // description_short as lead text; FieldsForm columns 1; submit button is the caller's

// frontend/src/components/process/DeployStatusCard.tsx
export function DeployStatusCard(props: { kind: "process" | "acl"; testId?: string }): JSX.Element | null;
  // "Bereitgestellt am … (aus Znuny|beim Start|von Tiqora)"; pending > 0 → warning
  // "N Änderungen sind noch nicht bereitgestellt. Sie gelten erst nach „Bereitstellen“ in Znuny.";
  // running_live → danger "Noch keine bereitgestellte Fassung – Tiqora nutzt den Entwurfsstand."
```

Routes: `agentNewProcessTicketRoute` path `/process/new` under `agentLayoutRoute`, search `{ process?: string }`; `portalNewProcessTicketRoute` path `/process/new` under `portalLayoutRoute`, same search. Entry points: `NewTicketButton` renders the menu whenever startable processes exist, with a `MenuLabel` "Prozess starten" and one `MenuItem` per process (testid `new-ticket-process-<entity_id>`) navigating to `/agent/process/new?process=<eid>`; the portal ticket list gets a link "Prozess starten" (testid `portal-new-process`) when `portalProcessApi.listStartable()` is non-empty. `StartProcessDialog` (enroll) becomes two steps: pick process → `ProcessDialogForm` of `startForm(eid, {ticketId})` → `enroll`. `ActivityDialogModal` and `PortalProcessWidget` render `ProcessDialogForm` from `dialogForm`. Zoom menu gating maps `TicketAclActionsOut.allowed` to: reply → `AgentTicketCompose`, note/internal note → `AgentTicketNote`, phone menu items → `AgentTicketPhoneInbound`/`AgentTicketPhoneOutbound`, more-lock → `AgentTicketLock`, more-watch → `AgentTicketWatcher`, more-link → `AgentLinkObject`, more-merge → `AgentTicketMerge`, more-print → `AgentTicketPrint`, status-bar pending → `AgentTicketPending`, status-bar closed → `AgentTicketClose`, priority pill → `AgentTicketPriority`, queue pill → `AgentTicketMove`, owner pill → `AgentTicketOwner`, responsible pill → `AgentTicketResponsible`, customer pill → `AgentTicketCustomer`, type/service/SLA pills + dynamic-field edit → `AgentTicketFreeText`, overflow start-process + process widget dialogs → `AgentTicketProcess`. A forbidden entry is hidden (not disabled) — Znuny hides menu entries.

### D13. i18n keys (German first; English in `en.json`)

- `process.field.{Title,Queue,State,Priority,Type,Service,SLA,Owner,Responsible,Lock,CustomerUserID,CustomerID,PendingTime,ArticleSubject,ArticleBody}` — de: Titel, Queue, Status, Priorität, Typ, Service, SLA, Besitzer, Verantwortlicher, Sperre, Kundenbenutzer, Kundennummer, Erinnerung am, Betreff, Text.
- `process.form.{unsupported,required,submit,cancel,loadError,submitError}` — "Dieses Feld kann hier noch nicht ausgefüllt werden.", "Pflichtfeld", "Absenden", "Abbrechen", "Formular konnte nicht geladen werden.", "Senden fehlgeschlagen."
- `process.lock.{lock,unlock}` — "gesperrt", "frei".
- `process.newTicket.{title,chooseProcess,empty,created}` — "Neues Prozessticket", "Prozess wählen", "Es gibt keinen Prozess, den Sie starten können.", "Ticket {{tn}} wurde angelegt."
- `newTicket.processLabel` — "Prozess starten"; `portal.process.start` — "Prozess starten".
- `process.start.next` — "Weiter".
- `process.widget.inactive` — "Dieser Prozess ist abgeschaltet. Es sind keine Schritte möglich."
- `admin.deploy.{title,deployedAt,source.initial,source.znuny,source.tiqora,pending,runningLive,ownership}` — "Bereitstellung", "Bereitgestellt am {{date}}", "beim Start", "aus Znuny", "von Tiqora", "{{count}} Änderungen sind noch nicht bereitgestellt. Sie gelten erst nach „Bereitstellen“ in Znuny.", "Noch keine bereitgestellte Fassung – Tiqora nutzt den Entwurfsstand.", "Tiqora stellt Änderungen selbst bereit (Znuny ist abgeschaltet)."
- `admin.processes.{state,state.Active,state.Inactive,state.FadeAway,startInterfaces}` — "Status", "Aktiv", "Abgeschaltet", "Läuft aus", "Startbar für".
- `admin.daemons.services.deploy_watch.{name,description}` — "Bereitstellung beobachten", "Erkennt, wenn Prozesse oder ACLs in Znuny bereitgestellt wurden, und übernimmt sie."
- `admin.daemons.deployLive` — "Prozesse und ACLs laufen noch aus dem Entwurfsstand. Bitte in Znuny einmal bereitstellen."

### Deviations from spec

1. **No SequenceFlow aliases.** The spec asked to "verify any renamed keys" and, if unverifiable, parse both. Verified offline against upstream `rel-7_3_5` (D1): nothing was renamed in stored YAML or the DB; only `pm_process_preferences` is new (7.2+). We snapshot that table and add no alias parsing (YAGNI). The golden test (Task 20) re-confirms on a running 7.3 peer.
2. **Revision cache invalidation without Redis.** Spec: "cached, invalidated via Redis". Instead every engine call reads the newest revision id (one indexed `SELECT`) and reuses an in-process parsed copy keyed by that id — correct across API replicas and worker processes with no pub/sub plumbing.
3. **ACLs also run from a deployed snapshot** (`tiqora_acl_revision`) — the spec lists it; consequence spelled out: Tiqora's own ACL admin edits become drafts (`acl_sync`) that take effect after "Bereitstellen" in Znuny, exactly like Znuny's AdminACL. After cutover (schema ownership active) the watcher deploys automatically and clears the sync tables (spec: "after cutover Tiqora deploys alone") until phase 3 brings the explicit deploy button.
4. **Compat API gets no ACL filtering.** The spec includes "compat API"; Znuny's GenericInterface does not call `TicketAcl` (D1), so filtering there would break GI clients that work against Znuny. The compat API keeps Znuny behaviour; a test pins it.
5. **`filter_process_options` signature** is `(session, *, principal, kind, items, action, ticket_id, process_entity_id, activity_dialog_entity_id)` instead of `(user, ticket, kind)` — customers need a different principal, Znuny passes the frontend `Action` and process context into the checks.
6. **Action ACLs**: menu entries are hidden for every Znuny action in `ZOOM_ACTIONS`; server-side refusal only for the process action. A Tiqora `PATCH /tickets/{id}` carries several Znuny actions at once; enforcing those is left for later (Known limitations).
7. **Enroll goes through the start dialog** (Znuny `IsProcessEnroll`), so `POST /process/ticket/{id}/start` now validates the start dialog's mandatory fields — API clients that start processes whose start dialog has `Display: 2` fields must send `field_values`.
8. **Dialog `Display` semantics corrected** (D1): `1` optional, `2` required, `0`/missing hidden — the reverse of the current Tiqora behaviour. Existing processes whose dialogs were filled with this inverted rule now behave like in Znuny.

### Known limitations (documented, not built here)

- `ProcessImport` in Znuny writes no `pm_entity_sync` rows, so an import is indistinguishable from "import + deploy" in the DB; Tiqora picks it up at the next watch tick even if the admin never presses "Bereitstellen". Fixed in phase 3 when TiqoraSync reports real deploys.
- Edits that start within one watch interval after a Znuny deploy hide that deploy (sync table never seen empty); Tiqora catches up with the next deploy. Interval default 30 s.
- Znuny evaluates transitions after **every** ticket change (event module `TicketProcessTransitions`); Tiqora evaluates them after process dialogs and new process tickets only. Changing a ticket through the normal zoom does not move it to the next activity. Candidate for phase 2.
- Owner/Responsible option lists are all valid agents ACL-filtered (no per-queue `owner`/`rw` scoping, same as `GET /reference/agents`); customer queue list = all valid non-system queues (Znuny default without `CustomerPanelOwnSelection`); customer groups come from `group_customer_user` only (no company groups).
- Dialog field options do not refresh when another field changes (Znuny's AJAX update) — phase 2 typed widgets.

---

## Tasks

Order: backend Tasks 1–15 (each commits on its own), golden test 20 may run any time after 14, frontend Tasks 16–19 need wave 0 Tasks 11–14 (`components/fields/*`) merged, docs last.

### Task 1: Dialog field semantics (Display, aliases, hidden defaults, article, RequiredLock, missing fields)

Backend-only. (The frontend's inverted `isFieldRequired` in `processDialogFields.ts` is replaced in Task 16; do not touch the frontend here.)

**Files:**
- Create: `backend/src/tiqora/process/fields.py`, `backend/src/tiqora/process/actor.py`
- Modify: `backend/src/tiqora/process/config.py` (`ActivityDialogFieldConfig.display` default `"0"`, `hidden`, `required`, docstring), `backend/src/tiqora/process/engine.py` (`apply_activity_dialog`, `_apply_dialog_field_changes` for Type/Service/SLA/Lock + article rules + RequiredLock; `submit_activity_dialog` uses them), `backend/src/tiqora/process/__init__.py` (exports)
- Test: `backend/tests/test_process_fields.py` (pure), `backend/tests/test_process_engine.py` (fixtures whose `Display: '1'` meant "required" become `'2'`; new DB cases)

**Interfaces:**
- Consumes: existing engine + `ticket_write_service` functions (`change_type`, `change_service`, `change_sla`, `lock_ticket`, `unlock_ticket`, `assign_owner`).
- Produces: D2 verbatim (`DISPLAY_*`, `FIELD_ALIASES`, `TICKET_REQUIRED_FIELDS`, `CUSTOMER_SKIP_FIELDS`, `canonical_field_name`, `dialog_field_order`, `visible_fields`, `normalize_field_values`, `effective_field_values`, `missing_required_fields`); D6 `ProcessActor`, `Interface`; `engine.apply_activity_dialog(session, *, ticket_id, dialog, field_values, actor, sysconfig) -> None`. (`actor.acl_principal` is added in Task 7 — in this task `ProcessActor` has no `acl_principal` yet.)

- [ ] **Step 1: Write the failing pure tests** (`backend/tests/test_process_fields.py`)

```python
from tiqora.process.config import ActivityDialogConfig
from tiqora.process.fields import (
    canonical_field_name, effective_field_values, missing_required_fields, visible_fields,
)

DIALOG = ActivityDialogConfig.from_yaml("""
FieldOrder: [Title, QueueID, DynamicField_Region, DynamicField_Note, Article, Owner]
Fields:
  Title: {Display: '1'}
  QueueID: {Display: '0', DefaultValue: 'Support'}
  DynamicField_Region: {Display: '2'}
  DynamicField_Note: {Display: '0'}
  Article: {Display: '2', Config: {CommunicationChannel: Internal, IsVisibleForCustomer: '0'}}
  Owner: {Display: '1'}
Interface: [AgentInterface, CustomerInterface]
""")

def test_display_semantics_match_znuny() -> None:
    assert DIALOG.fields["Title"].required is False          # 1 = shown, optional
    assert DIALOG.fields["DynamicField_Region"].required is True   # 2 = mandatory
    assert DIALOG.fields["QueueID"].hidden is True           # 0 = hidden

def test_missing_display_key_means_hidden() -> None:
    d = ActivityDialogConfig.from_yaml("FieldOrder: [Title]\nFields:\n  Title: {}\n")
    assert d.fields["Title"].hidden is True

def test_aliases_are_canonical() -> None:
    assert canonical_field_name("QueueID") == "Queue"
    assert canonical_field_name("DynamicField_X") == "DynamicField_X"

def test_visible_fields_skip_hidden_and_customer_fields() -> None:
    assert visible_fields(DIALOG, "AgentInterface") == ["Title", "DynamicField_Region", "Article", "Owner"]
    assert visible_fields(DIALOG, "CustomerInterface") == ["Title", "DynamicField_Region", "Article"]

def test_hidden_field_default_overrides_submitted_value() -> None:
    values = effective_field_values(DIALOG, {"Queue": "Junk", "Title": "x"}, "AgentInterface")
    assert values["Queue"] == "Support"

def test_hidden_dynamic_field_without_default_is_cleared() -> None:
    values = effective_field_values(DIALOG, {"DynamicField_Note": "keep?"}, "AgentInterface")
    assert values["DynamicField_Note"] == []

def test_customer_cannot_submit_owner() -> None:
    values = effective_field_values(DIALOG, {"Owner": "2"}, "CustomerInterface")
    assert "Owner" not in values

def test_article_needs_subject_and_body() -> None:
    v = effective_field_values(DIALOG, {"Article": {"Subject": "s", "Body": ""}, "DynamicField_Region": "EU"}, "AgentInterface")
    assert missing_required_fields(DIALOG, v, "AgentInterface") == ["Article"]
    v2 = effective_field_values(DIALOG, {"Subject": "s", "Body": "b", "DynamicField_Region": "EU"}, "AgentInterface")
    assert missing_required_fields(DIALOG, v2, "AgentInterface") == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run python -m pytest tests/test_process_fields.py -q`
Expected: FAIL — `ModuleNotFoundError: tiqora.process.fields`.

- [ ] **Step 3: Implement** `fields.py` + `actor.py` (D2/D6; `ProcessActor.customer` uses the local `CUSTOMER_PANEL_USER_ID`; add a unit test asserting it equals `tiqora.domain.portal_ticket_service.PORTAL_SYSTEM_USER_ID`), the config change (rewrite the `Display` paragraph in the module docstring: "`'0'`/missing = hidden, DefaultValue applies; `'1'` = shown, optional; `'2'` = shown, mandatory — `AgentTicketProcess.pm:2695, 4916-4936`"), and in `engine.py`:

```python
async def apply_activity_dialog(session, *, ticket_id, dialog, field_values, actor, sysconfig) -> None:
    values = effective_field_values(dialog, field_values, actor.interface)
    missing = missing_required_fields(dialog, values, actor.interface)
    if missing:
        raise RequiredFieldMissing(f"required activity dialog field missing: {', '.join(missing)}")
    if dialog.required_lock == "1" and actor.interface == "AgentInterface":
        await _ensure_required_lock(session, ticket_id=ticket_id, user_id=actor.user_id, sysconfig=sysconfig)
    await _apply_dialog_field_changes(session, dialog_config=dialog, field_values=values,
                                      ticket_id=ticket_id, user_id=actor.user_id, sysconfig=sysconfig,
                                      sender_type="customer" if actor.interface == "CustomerInterface" else "agent")
```

`_apply_dialog_field_changes` gains `sender_type: str = "agent"`; add Type/Service/SLA/Lock branches using `_resolve_dialog_field_id` with `_resolve_type_id`/`_resolve_service_id`/`_resolve_sla_id` and `lock`/`unlock` (`"lock"`, `"unlock"`, `"2"`, `"1"`); article: channel from `Config.CommunicationChannel` (`Internal`→`note`, `Email`→`email`, `Phone`→`phone`, legacy `ArticleType` via `_ARTICLE_CHANNEL_MAP`), `is_visible_for_customer = str(config.get("IsVisibleForCustomer", "0")) == "1"`, created only when `Subject` and `Body` are both non-empty. `submit_activity_dialog(…, actor: ProcessActor | None = None)` replaces its inline required check with `apply_activity_dialog(actor=actor or ProcessActor.agent(user_id))`.

- [ ] **Step 4: Update `backend/tests/test_process_engine.py` fixtures** — every dialog YAML whose test expects a required field gets `Display: '2'`; add DB tests `test_dialog_applies_type_service_sla_lock`, `test_article_visibility_comes_from_dialog_config_not_request` (submit `IsVisibleForCustomer: True` with config `'0'` → article `is_visible_for_customer = 0`), `test_required_lock_locks_and_sets_owner`, `test_required_lock_refuses_foreign_lock` (raises `ProcessPermissionDenied`).

- [ ] **Step 5: Run** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest tests/test_process_fields.py tests/test_process_engine.py tests/test_process_api.py tests/test_process_config.py -q` → PASS. Lint gates.

- [ ] **Step 6: Commit**

```bash
rtk git add backend/src/tiqora/process backend/tests/test_process_fields.py backend/tests/test_process_engine.py
rtk git commit -m "fix(process): Znuny dialog Display semantics, hidden defaults, article config, RequiredLock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
rtk git log -1 --oneline
```

### Task 2: Process dump + ACL dump builders (Znuny dump shapes, checksum)

Backend-only.

**Files:**
- Create: `backend/src/tiqora/process/dump.py`, `backend/src/tiqora/deployed/__init__.py`, `backend/src/tiqora/deployed/acl_dump.py`
- Modify: `backend/src/tiqora/process/config.py` (`from_dict` on all five config models; `from_yaml` delegates), `backend/src/tiqora/db/legacy/process_management.py` (add `PmProcessPreferences` model for `pm_process_preferences`; docstring: 7.2+ only), `backend/src/tiqora/process/graph.py` (docstring: S1/S2/S3 are hardcoded in Znuny `State.pm`)
- Test: `backend/tests/test_process_dump.py` (DB, band 881000–881499)

**Interfaces:**
- Consumes: `PmProcess`…`PmTransitionAction`, `Acl`, `Users`, `tiqora.db.legacy.profile` (to know whether `pm_process_preferences` exists — use the same profile accessor the app already uses; grep `pm_process_preferences` in `db/legacy/profile.py`).
- Produces: D3 `PROCESS_STATE_NAMES`, `STATE_*`, `canonical_json`, `dump_checksum`, `dump_entity_count`, `build_process_dump`, `build_acl_dump`; `Config.from_dict` classmethods.

- [ ] **Step 1: Write the failing tests**

```python
async def test_process_dump_matches_znuny_shape(session) -> None:
    # seed one process (S3), two activities, one dialog without Interface, one transition, one action
    dump = await build_process_dump(session)
    p = dump["Process"]["Process-881001"]
    assert p["State"] == "FadeAway" and p["StateEntityID"] == "S3"
    assert set(p) >= {"ID", "Name", "CreateTime", "ChangeTime", "StartActivity", "StartActivityDialog", "Path"}
    assert dump["ActivityDialog"]["ActivityDialog-881001"]["Interface"] == ""    # Znuny `|| ''`
    assert dump["State"] == {"S1": "Active", "S2": "Inactive", "S3": "FadeAway"}
    assert dump["ProcessPreferences"] == {}

async def test_checksum_is_stable_and_content_sensitive(session) -> None:
    a = dump_checksum(await build_process_dump(session))
    assert a == dump_checksum(await build_process_dump(session))
    # UPDATE pm_transition config (change Match) → checksum differs

async def test_acl_dump_valid_only_sorted_and_sanitised(session) -> None:
    # seed ACLs "b-acl" valid, "a'acl" valid (quote stripped → "aacl"), "c" invalid (valid_id 2)
    dump = await build_acl_dump(session)
    assert list(dump["ACL"]) == ["aacl", "b-acl"]
    assert dump["ACL"]["b-acl"]["Values"]["StopAfterMatch"] in (0, 1)

def test_config_from_dict_accepts_znuny_dump_defaults() -> None:
    assert ActivityConfig.from_dict({"ActivityDialog": ""}).activity_dialogs == {}
    assert TransitionConfig.from_dict({"Condition": {}, "ConditionLinking": ""}).condition_linking == "and"
    cfg = ProcessConfig.from_dict({"StartActivity": "", "StartActivityDialog": "", "Path": {}})
    assert cfg.start_activity is None and cfg.start_activity_dialog is None
```

Use the `mariadb_znuny_url` fixture and the `_seed_statements` cleanup pattern; one test module also runs against `postgres_znuny_url` for `build_process_dump` (parametrize the URL fixture like other dual-dialect modules — grep `postgres_znuny_url` for an example).

- [ ] **Step 2: Run** `cd backend && uv run python -m pytest tests/test_process_dump.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement.** YAML via `_load_yaml_mapping`; times via `strftime("%Y-%m-%d %H:%M:%S")`; ACL logins via one `SELECT id, login FROM users WHERE id IN (…)` (no cross-table string JOIN). `ConditionLinking ''` must still parse to `"and"` in `TransitionConfig.from_dict` (`data.get("ConditionLinking") or "and"`).
- [ ] **Step 4: Run** the module + `tests/test_process_config.py` → PASS; lint gates.
- [ ] **Step 5: Commit** `feat(process): build Znuny ProcessDump/ACLDump shapes with stable checksum` (+ trailer), `rtk git log -1 --oneline`.

### Task 3: Revision tables, ORM, migration, store

Backend-only.

**Files:**
- Create: `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_deployed_revisions.py` (revision = next number after the current head, `down_revision` = current head)
- Create: `backend/src/tiqora/deployed/store.py`
- Modify: `backend/src/tiqora/db/tiqora/models.py` (`TiqoraProcessRevision`, `TiqoraAclRevision`, `REVISION_CONFIG_TEXT`), `backend/src/tiqora/deployed/__init__.py`, `backend/tests/test_migration_gate.py` (`TIQORA_HEAD`), `backend/tests/test_migrate_cli.py` (`version ==`), `backend/tests/test_tiqora_model_ddl.py`
- Test: `backend/tests/test_deployed_store.py` (DB, band 881500–881999)

**Interfaces:**
- Consumes: `tiqora.jobs.service.utc_now` (wave 0), `canonical_json`, `dump_checksum`, `dump_entity_count` (Task 2).
- Produces: D3 tables/ORM; `RevisionKind`, `RevisionSource`, `RevisionInfo`, `LoadedRevision`, `latest_revision_info`, `load_active_revision`, `record_revision`, `clear_revision_cache`.

- [ ] **Step 1: Failing tests**

```python
def test_revision_config_fits_large_dumps_on_mariadb() -> None:
    # like test_job_file_data_and_result_fit_large_payloads_on_mariadb: compile CREATE TABLE for
    # mysql dialect and assert "MEDIUMTEXT" in the config column DDL for both tables

async def test_record_and_load_newest(session) -> None:
    clear_revision_cache()
    r1 = await record_revision(session, "process", dump={"Process": {}}, source="initial", deployed_by=None)
    r2 = await record_revision(session, "process", dump={"Process": {"P": {}}}, source="znuny", deployed_by=None)
    loaded = await load_active_revision(session, "process")
    assert loaded is not None and loaded.info.id == r2.id and loaded.dump == {"Process": {"P": {}}}
    assert (await latest_revision_info(session, "acl")) is None

async def test_cache_is_keyed_by_revision_id(session) -> None:
    # load twice → second call does not re-read config (patch json.loads counter); record a new
    # revision → next load returns the new dump

async def test_record_does_not_commit(session) -> None   # rollback → row gone
```

Test DB tables: create with `TiqoraBase.metadata.create_all(conn, tables=[TiqoraProcessRevision.__table__, TiqoraAclRevision.__table__], checkfirst=True)`; the cleanup fixture deletes the rows it created by id.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** migration (both tables + indexes; `sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")`), ORM, store. Downgrade drops both tables.
- [ ] **Step 4: Run** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest tests/test_deployed_store.py tests/test_migration_gate.py tests/test_migrate_cli.py tests/test_tiqora_model_ddl.py -q` → PASS; `cd backend && uv run alembic heads` shows one primary head; lint gates.
- [ ] **Step 5: Commit** `feat(deployed): process/ACL revision tables and store` (+ trailer), `rtk git log -1 --oneline`.

### Task 4: Engine and ACL evaluator read the newest revision

Backend-only.

**Files:**
- Create: `backend/src/tiqora/process/source.py`
- Modify: `backend/src/tiqora/process/graph.py` (`ProcessRepository` over `ProcessSource`; `ProcessSummary.state_name`; `get_activity_dialog` returns `ActivityDialogNode | None`), `backend/src/tiqora/process/ticket_state.py` (pass the repository through), `backend/src/tiqora/api/v1/process.py`, `backend/src/tiqora/api/portal/process.py` (use `node.config`/`node.name`), `backend/src/tiqora/domain/ticket_acl.py` (`AclRule`, `load_valid_acls` from revision, `apply_ticket_acl` uses `rule.config_match`/`rule.config_change`)
- Test: `backend/tests/test_process_source.py` (DB, band 881000–881999 shared with Task 2 — use ids 881700+), extend `backend/tests/test_ticket_acl.py`
- Modify: `backend/tests/conftest.py` — in `mariadb_znuny_url` and `postgres_znuny_url`, create the two revision tables (`TiqoraBase.metadata.create_all(engine, tables=[TiqoraProcessRevision.__table__, TiqoraAclRevision.__table__])`) right after the Znuny DDL load and BEFORE `_take_snapshot`, so every module (ACL evaluation now touches `tiqora_acl_revision` from reference, tickets, portal tests…) has them and the between-module restore keeps them. Fallback only if that breaks the snapshot/leak machinery: per-module `create_all(..., checkfirst=True)` in each failing module. Never catch DB errors in production code to paper over a missing table.

**Interfaces:**
- Consumes: Task 2 dump + `Config.from_dict`, Task 3 store.
- Produces: D3 `EntityKind`, `EntityRecord`, `ProcessSource`, `LiveProcessSource`, `RevisionProcessSource`, `active_process_source`; `AclRule`; `ProcessRepository(session, source=None)`.

- [ ] **Step 1: Failing tests**

```python
async def test_engine_runs_newest_revision_not_live_rows(session, sysconfig, ticket_id) -> None:
    # seed a process whose transition sets state "closed successful"; record a revision of it;
    # then UPDATE pm_transition_action config to state "open" (a draft) and add a pm_entity_sync row
    await start_process(...); result = await submit_activity_dialog(...)
    assert await _state_name(session, ticket_id) == "closed successful"   # revision wins

async def test_live_rows_are_used_while_no_revision_exists(session, ...) -> None
async def test_get_process_unknown_in_revision_returns_none(session) -> None
    # entity present in pm_* but not in the revision → None

async def test_acl_from_revision_ignores_undeployed_acl(session) -> None:
    # revision has ACL "a" (PossibleNot State [open]); live table additionally has a new valid ACL
    # "b" (PossibleNot State [new]) → apply_ticket_acl removes "open" only
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `LiveProcessSource.load` reads `pm_*` rows by entity id (as today) and parses YAML; `list_processes` returns all `pm_process` rows. `RevisionProcessSource` reads the dump (`ID` → `id`, `StateEntityID` → `state_entity_id`). `ProcessRepository` builds nodes with `ProcessConfig.from_dict(record.data)` etc.; `ProcessLayout` comes from `record.layout` (empty for revisions — layout is cosmetic). Log `process_definitions_running_live` once per process via a module-level flag.
- [ ] **Step 4: Run** `TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest tests/test_process_source.py tests/test_ticket_acl.py tests/test_ticket_acl_permissions.py tests/test_process_engine.py tests/test_process_api.py -q` → PASS, then the full backend suite once (missing-table failures → add `create_all` to that module's setup); lint gates.
- [ ] **Step 5: Commit** `feat(process): engine and ACLs run from the deployed revision` (+ trailer), `rtk git log -1 --oneline`.

### Task 5: Deploy watcher + `acl_sync` on admin ACL edits

Backend (plus two i18n keys for the "Dienste" row).

**Files:**
- Create: `backend/src/tiqora/deployed/sync_tables.py`, `backend/src/tiqora/deployed/watcher.py`
- Modify: `backend/src/tiqora/api/v1/admin/acl.py` (create/update/delete write `acl_sync`), `backend/src/tiqora/config.py` (`deploy_watch_interval_seconds`), `backend/src/tiqora/worker/__main__.py` (`deploy_watch_tick` + `_interval_loop`), `backend/src/tiqora/worker/services.py` (`DaemonService(slug="deploy_watch", …)` per D4), `backend/tests/test_admin_daemons.py` (`len(out.services) ==` current count + 1), `frontend/src/i18n/locales/en.json` + `de.json` (`admin.daemons.services.deploy_watch.{name,description}`, `admin.daemons.deployLive`) + propagate
- Test: `backend/tests/test_deploy_watch.py` (DB, band 882000–882499), `backend/tests/test_admin_acl_sync.py` (DB, band 882500–882999)

**Interfaces:**
- Consumes: Tasks 2–4; `tiqora.domain.ownership.get_ownership_state`; `tiqora.worker.status.record_tick_status` (called by `_interval_loop`).
- Produces: D4 `AclSyncState`, `pending_sync_count`, `mark_acl_changed`, `clear_sync_table`, `WatchAction`, `WatchOutcome`, `check_deploy_state`, `run_deploy_watch_tick`.

- [ ] **Step 1: Failing tests** (`test_deploy_watch.py`, one test per row of the D4 table, plus):

```python
async def test_draft_edit_is_not_picked_up_until_sync_table_empties(session) -> None:
    out1 = await check_deploy_state(session, "process", ownership_active=False)   # empty sync → initial
    assert out1.action == "snapshot"
    # admin edit in Znuny: change pm_transition config + INSERT pm_entity_sync (Transition, eid, not_sync)
    out2 = await check_deploy_state(session, "process", ownership_active=False)
    assert out2.action == "waiting_for_deploy" and out2.pending_changes == 1
    # Znuny deploy: DELETE FROM pm_entity_sync
    out3 = await check_deploy_state(session, "process", ownership_active=False)
    assert out3.action == "snapshot" and out3.revision_id != out1.revision_id
    info = await latest_revision_info(session, "process"); assert info.source == "znuny"

async def test_running_live_when_no_revision_and_drafts_exist(session) -> None
async def test_unchanged_dump_records_nothing(session) -> None
async def test_ownership_mode_snapshots_and_clears_sync_tables(session) -> None
async def test_tick_result_reports_live_mode(session_factory, settings) -> None:
    # run_deploy_watch_tick → {"mode": "live", "process": {"action": "running_live", ...}, ...}
```

`test_admin_acl_sync.py` (through the HTTP API like other admin tests — grep `test_admin_api.py` for the client fixture):

```python
async def test_create_update_delete_write_acl_sync_like_znuny(client, session) -> None:
    # POST /api/v1/admin/acl → one acl_sync row (acl_id=str(id), sync_state not_sync)
    # PATCH → a second not_sync row (Znuny inserts, never upserts)
    # DELETE → a 'deleted' row; create_time/change_time set
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D4. `run_deploy_watch_tick` opens one session, reads ownership once, checks both kinds, commits, returns the dict. In `admin/acl.py` call `await session.flush()` before `mark_acl_changed` on create.
- [ ] **Step 4: Run** both modules + `tests/test_admin_daemons.py` → PASS; lint gates; `node frontend/scripts/check-i18n-keys.mjs`.
- [ ] **Step 5: Commit** `feat(deployed): detect Znuny deploys; ACL admin writes acl_sync like Znuny` (+ trailer), `rtk git log -1 --oneline`.

### Task 6: Admin deploy status + admin process list API

Backend-only.

**Files:**
- Create: `backend/src/tiqora/api/v1/admin/deploy_status.py`
- Modify: `backend/src/tiqora/api/v1/admin/__init__.py` (include router), `packages/api-client/openapi.json` (regen)
- Test: `backend/tests/test_admin_deploy_status.py` (DB, band 881800–881899)

**Interfaces:**
- Consumes: Tasks 3–5 (`latest_revision_info`, `pending_sync_count`, `get_ownership_state`, `ProcessRepository`).
- Produces: D4 `RevisionOut`, `DeployKindStatusOut`, `DeployStatusOut`, `AdminProcessOut`, `GET /api/v1/admin/deploy-status`, `GET /api/v1/admin/processes`.

- [ ] **Step 1: Failing tests:** `test_status_without_revision_is_running_live`, `test_status_reports_pending_and_revision` (seed revision + 2 `acl_sync` rows → `acl.pending_changes == 2`, `acl.running_live is False`), `test_admin_processes_lists_inactive_too` (S2 process appears with `state_name == "Inactive"`, `start_interfaces == ["AgentInterface"]`), `test_requires_admin` (agent → 403).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; `activity_count` = number of activities in the loaded graph; start interfaces from the start dialog config (empty list → `[]`).
- [ ] **Step 4: Run** → PASS; regenerate openapi (Global Constraints commands); lint gates.
- [ ] **Step 5: Commit** `feat(admin): deploy status and admin process list` (+ trailer), `rtk git log -1 --oneline`.

### Task 7: ACL principal (customers), process checks, `filter_process_options`

Backend-only.

**Files:**
- Modify: `backend/src/tiqora/domain/ticket_acl.py` (D5), `backend/src/tiqora/process/actor.py` (`acl_principal`)
- Test: `backend/tests/test_ticket_acl_principal.py` (DB, band 884000–884999)

**Interfaces:**
- Consumes: `GroupCustomerUser`, `CustomerUser`, `PermissionGroups` models.
- Produces: D5 `AclPrincipal`, `ProcessAclKind`, `load_customer_user_checks`, extended `apply_ticket_acl`, `filter_process_options`; `ProcessActor.acl_principal`.

- [ ] **Step 1: Failing tests**

```python
async def test_customer_principal_is_not_root(session) -> None:
    # ACL: Properties CustomerUser UserLogin [cu-884001]; PossibleNot Process [Process-884001]
    kept = await filter_process_options(session, principal=AclPrincipal.customer("cu-884001"),
        kind="Process", items=["Process-884001", "Process-884002"], action="CustomerTicketProcess")
    assert kept == ["Process-884002"]

async def test_agent_root_still_bypasses(session) -> None   # AclPrincipal.agent(1) keeps both

async def test_customer_groups_feed_checks(session) -> None:
    # group_customer_user (cu-884002, group "support-884", rw, 1) → CustomerUser Group_rw contains it

async def test_activity_dialog_acl_uses_process_checks(session) -> None:
    # ACL Properties Process ActivityDialogEntityID [AD-884001]; Possible ActivityDialog [AD-884002]
    kept = await filter_process_options(session, principal=AclPrincipal.agent(884010),
        kind="ActivityDialog", items=["AD-884001", "AD-884002"], action="AgentTicketProcess",
        activity_dialog_entity_id="AD-884001")
    assert kept == ["AD-884002"]

async def test_action_acl(session) -> None:
    # PossibleNot Action [AgentTicketProcess] for queue X → [] for a ticket in X, kept elsewhere

async def test_agent_on_ticket_sees_ticket_customer_in_checks(session) -> None:
    # ACL PropertiesDatabase CustomerUser UserCustomerID [example-co] → applies to that ticket
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D5 (cite `TicketACL.pm` line ranges in comments). `_ROOT_USER_ID` bypass condition becomes `user_id is not None and user_id == _ROOT_USER_ID`.
- [ ] **Step 4: Run** the module + `tests/test_ticket_acl.py tests/test_ticket_acl_permissions.py` → PASS; lint gates.
- [ ] **Step 5: Commit** `feat(acl): customer principal, process checks, filter_process_options` (+ trailer), `rtk git log -1 --oneline`.

### Task 8: Availability — state gate, interface, ACL on every process surface

Backend + frontend fixture updates (the new response fields with defaults become required TS properties — CI runs tsc on every commit).

**Files:**
- Create: `backend/src/tiqora/process/availability.py`
- Modify: `backend/src/tiqora/process/exceptions.py` (`ProcessNotStartable`, `ProcessNotRunning`), `backend/src/tiqora/process/engine.py` (`submit_activity_dialog` → `assert_can_submit`; `start_process` unchanged), `backend/src/tiqora/process/schemas.py` (D6 field additions), `backend/src/tiqora/api/v1/process.py` and `backend/src/tiqora/api/portal/process.py` (lists via `list_startable_processes`; state endpoints use `available_dialogs`, fill `process_state`/`can_start`; start endpoints call `assert_can_start`; map new exceptions to 409), `packages/api-client/openapi.json`, `packages/api-client/src/client.ts` (no signature change; types follow schema)
- Modify (frontend fixtures only): every test fixture / demo mock that builds `TicketProcessStateOut` or `ProcessSummaryOut` — find them with `grep -rln "available_transitions_count\|state_entity_id" frontend/src` (at least `components/agent/process/ProcessWidget.test.tsx`, `demo/mockData.ts`, admin `ProcessDetailPage.test.tsx`) — gets `process_state: "Active"`, `can_start: false` resp. `state_name: "Active"`
- Test: `backend/tests/test_process_availability.py` (DB, band 886000–886999), extend `backend/tests/test_process_api.py`

**Interfaces:**
- Consumes: Task 1 `ProcessActor`, `dialog_field_order`; Task 4 `ProcessRepository`; Task 7 `filter_process_options`.
- Produces: D6 `STARTABLE_STATES`, `RUNNING_STATES`, `dialog_allows`, `list_startable_processes`, `process_action_allowed`, `available_dialogs`, `assert_can_start`, `assert_can_submit`; schema fields `ProcessSummaryOut.state_name`, `TicketProcessStateOut.process_state`, `TicketProcessStateOut.can_start`.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize(("state", "listed", "dialogs_on_running_ticket", "submit_status"), [
    ("S1", True, True, 200),
    ("S3", False, True, 200),      # FadeAway: running tickets continue, no new starts
    ("S2", False, False, 409),     # Inactive: nothing
])
async def test_state_gate_matrix(client, session, state, listed, dialogs_on_running_ticket, submit_status) -> None

async def test_agent_list_excludes_customer_only_start_dialog(client, session) -> None
async def test_agent_dialogs_exclude_customer_only_dialogs(client, session) -> None
async def test_dialog_without_interface_is_offered_to_nobody(client, portal_client, session) -> None
async def test_portal_list_excludes_agent_only_start_dialog(portal_client, session) -> None
async def test_acl_hides_process_from_list_and_refuses_start(client, session) -> None   # 409
async def test_acl_hides_dialog_and_refuses_submit(client, session) -> None             # 403
async def test_portal_submit_of_acl_hidden_dialog_is_403(portal_client, session) -> None
async def test_action_acl_blocks_all_process_surfaces(client, session) -> None
    # PossibleNot Action [AgentTicketProcess] → state.available_dialogs == [], can_start False,
    # start → 409, submit → 403
async def test_state_out_reports_process_state_and_can_start(client, session) -> None
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D6. The portal endpoints build `ProcessActor.customer(login=customer.login, customer_id=customer.customer_id)`; the agent endpoints `ProcessActor.agent(user.id)`. Remove the portal's private `_dialog_allows_customer` (replaced by `dialog_allows`). `ProcessNotStartable`/`ProcessNotRunning` join both `_MAPPED_ERRORS` tuples.
- [ ] **Step 4: Run** both modules + `tests/test_process_engine.py` → PASS; regenerate openapi; update the frontend fixtures; `cd frontend && pnpm lint && pnpm test --run` → PASS; backend lint gates.
- [ ] **Step 5: Commit** `feat(process): state gate, interface and ACLs on every process surface` (+ trailer), `rtk git log -1 --oneline`.

### Task 9: Condition data parity + smart tags in conditions

Backend-only.

**Files:**
- Create: `backend/src/tiqora/process/conditions.py`
- Modify: `backend/src/tiqora/process/engine.py` (`evaluate_transition`/`_evaluate_field` move to `conditions.py` and are re-exported; `run_transitions` from D10 introduced here: loads `get_condition_data`, resolves tags, evaluates; `submit_activity_dialog` uses it; `get_ticket_attrs` stays as a thin `dict[str, str]` wrapper for `_action_dynamic_field_pending_time_set`), `backend/src/tiqora/process/__init__.py`
- Test: `backend/tests/test_process_conditions.py` (pure), `backend/tests/test_process_engine_conditions.py` (update cases whose expectations contradict Znuny — each changed assertion gets a comment citing the `.pm` line), `backend/tests/test_process_condition_data.py` (DB, band 887100–887499)

**Interfaces:**
- Consumes: wave-0 `tiqora.domain.dynamic_field_values` (field types), Task 8 `RUNNING_STATES`.
- Produces: D7 `ConditionValue`, `ConditionData`, `BASE_CONDITION_TYPES`, `get_condition_data`, `evaluate_condition_field`, `evaluate_transition(…, tags=None)`, `resolve_transition_tags`; D10 `TransitionOutcome`, `run_transitions` (returns a no-op outcome when the ticket's process is not in `RUNNING_STATES` — add DB test `test_run_transitions_skips_inactive_process`; tags rendered with a minimal renderer in this task: `render=lambda s: expand_placeholders(session, sysconfig, s, ticket_id=ticket_id, user_id=user_id)`; Task 10 swaps in `render_tags` over `build_tag_context`).

- [ ] **Step 1: Failing pure tests** (`test_process_conditions.py`)

```python
from tiqora.process.conditions import evaluate_condition_field as ev
from tiqora.process.config import TransitionConditionField as C

def test_string_needs_defined_value_and_nonempty_match() -> None:
    assert ev("Queue", C(match="Raw", type_="String"), {"Queue": "Raw"})
    assert not ev("DynamicField_X", C(match="", type_="String"), {"DynamicField_X": ""})
    assert not ev("DynamicField_X", C(match="a", type_="String"), {"DynamicField_X": None})
    assert ev("DynamicField_M", C(match="b", type_="String"), {"DynamicField_M": ["a", "b"]})

def test_regexp_needs_truthy_value() -> None:
    assert not ev("Title", C(match=".*", type_="Regexp"), {"Title": ""})
    assert ev("DynamicField_M", C(match="^b$", type_="Regexp"), {"DynamicField_M": ["a", "b"]})

def test_base_missing_value_falls_back_to_field_name() -> None:
    # Znuny CheckValueGet: Data->{FieldName} // FieldName
    assert ev("DynamicField_Unset", C(match="dynamicfield_unset", type_="Equal"), {"DynamicField_Unset": None})

def test_equal_empty_check_value_never_matches() -> None:
    assert not ev("Title", C(match="", type_="Equal"), {"Title": ""})
    assert ev("Title", C(match="x", type_="NotEqual"), {"Title": ""})

def test_equal_list_rules() -> None:
    assert ev("M", C(match="a, b", type_="Equal"), {"M": ["a", "b"]})
    assert ev("M", C(match="A", type_="Equal"), {"M": ["a"]})
    assert not ev("M", C(match="a", type_="Equal"), {"M": ["a", "b"]})

def test_tags_in_field_name_and_match_for_base_types_only() -> None:
    tags = {"<OTRS_TICKET_DynamicField_Target>": "Postmaster", "<OTRS_TICKET_Queue>": "raw"}
    assert ev("<OTRS_TICKET_DynamicField_Target>", C(match="postmaster", type_="Equal"), {}, tags=tags)
    assert ev("Queue", C(match="<OTRS_TICKET_Queue>", type_="Equal"), {"Queue": "Raw"}, tags=tags)
    assert not ev("Queue", C(match="<OTRS_TICKET_Queue>", type_="String"), {"Queue": "Raw"}, tags=tags)

def test_ordered_compare_with_dates() -> None:
    assert ev("DynamicField_D", C(match="2026-01-01", type_="GreaterThan"), {"DynamicField_D": "2026-02-01 00:00:00"})
```

DB test (`test_process_condition_data.py`): `test_condition_data_has_znuny_ticketget_keys` (Type/Service/SLA/Owner login/Lock/CustomerID/StateType present; Multiselect DF → list; unset DF → None) and `test_transition_on_type_matches_now` (a transition with `Type: String Match: Incident` fires after the dialog sets the type — this silently never matched before).

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D7 (port `Base.pm:346-610` list rules faithfully; comments cite lines).
- [ ] **Step 4: Run** the three modules + `tests/test_process_engine.py` → PASS; lint gates.
- [ ] **Step 5: Commit** `fix(process): Znuny condition data and semantics, smart tags in conditions` (+ trailer), `rtk git log -1 --oneline`.

### Task 10: Smart tags in transition action config (+ DF tags in the placeholder renderer)

Backend-only.

**Files:**
- Create: `backend/src/tiqora/process/smart_tags.py`
- Modify: `backend/src/tiqora/channels/email/placeholder.py` (`load_placeholder_context` DF entries per D7), `backend/src/tiqora/process/engine.py` (`execute_transition_action` → `replace_action_config`; `run_transitions` renders via `build_tag_context` + `render_tags`; remove the "smart tags not implemented" paragraph from the module docstring)
- Test: `backend/tests/test_process_smart_tags.py` (DB, band 887500–887999), extend `backend/tests/test_placeholder_resolution_db.py`

**Interfaces:**
- Consumes: `expand_placeholders`, `load_placeholder_context`, `PlaceholderContext`; Task 9 `get_condition_data`.
- Produces: D7 `build_tag_context`, `render_tags`, `replace_action_config`.

- [ ] **Step 1: Failing tests**

```python
async def test_ticket_attribute_and_df_tags_in_action_config(session, sysconfig, ticket_id) -> None:
    # DF Region (Dropdown, PossibleValues {eu: Europa}) value "eu"; ticket title "Printer"
    cfg = await replace_action_config({"Title": "<OTRS_TICKET_Title> / <OTRS_TICKET_DynamicField_Region> / <OTRS_TICKET_DynamicField_Region_Value>",
                                       "Note": 5}, session=session, ticket_id=ticket_id, user_id=1, sysconfig=sysconfig)
    assert cfg == {"Title": "Printer / eu / Europa", "Note": 5}

async def test_multiselect_attribute_replaces_whole_value(...) -> None
    # {"Value": "<OTRS_TICKET_DynamicField_Tags>"} → {"Value": ["a", "b"]}

async def test_customer_and_agent_article_tags(...) -> None
    # last customer article subject "Hilfe" → "<OTRS_CUSTOMER_SUBJECT>" == "Hilfe";
    # last agent article body → "<OTRS_AGENT_BODY>"; "<OTRS_CURRENT_UserLogin>" = acting user

async def test_config_tag_and_first_article(...) -> None
    # "<OTRS_CONFIG_Ticket::Hook>" (written as <OTRS_CONFIG_Ticket::Hook>), "<OTRS_FIRST_ARTICLE_Subject>"

async def test_action_config_is_not_mutated(...) -> None

async def test_title_set_action_uses_tags_end_to_end(...) -> None
    # TicketTitleSet Config Title "Order <OTRS_TICKET_TicketNumber>" → ticket title contains the TN
```

`test_placeholder_resolution_db.py`: `test_dynamic_field_tags_resolve_in_templates` (`<OTRS_TICKET_DynamicField_Region_Value>` → `Europa`).

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D7; article rows via `text()` on `article` + `article_data_mime` + `article_sender_type` (load sender type ids first, then filter by id — no string JOIN across tiqora/Znuny tables needed here since all are Znuny tables).
- [ ] **Step 4: Run** both modules + `tests/test_process_engine.py tests/test_postmaster_placeholder.py` → PASS; lint gates.
- [ ] **Step 5: Commit** `feat(process): Znuny smart tags in transition action config` (+ trailer), `rtk git log -1 --oneline`.

### Task 11: Dialog `PendingTime`

Backend-only.

**Files:**
- Modify: `backend/src/tiqora/process/engine.py` (`_apply_pending_time` called from `apply_activity_dialog` after the other fields; module docstring line about PendingTime removed)
- Test: extend `backend/tests/test_process_engine.py` (DB)

**Interfaces:**
- Consumes: `_set_ticket_pending_time`, `_state_type_name` (from `ticket_write_service`, private import with `# noqa: PLC2701` like the others).
- Produces: D8 behaviour.

- [ ] **Step 1: Failing tests:** `test_pending_time_applied_when_target_state_is_pending` (dialog fields `State` (2), `PendingTime` (1); submit `State: "pending reminder"`, `PendingTime: "2026-10-12T08:00:00Z"` → `ticket.until_time` = that epoch, one `SetPendingTime` history row), `test_pending_time_ignored_for_open_state`, `test_pending_time_uses_dialog_state_default_when_state_hidden`, `test_invalid_pending_time_is_400` (`UnresolvedFieldValue`), `test_required_pending_time_missing` (`RequiredFieldMissing`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D8 (`datetime.fromisoformat(value.replace("Z", "+00:00"))`; naive → UTC).
- [ ] **Step 4: Run** → PASS; lint gates.
- [ ] **Step 5: Commit** `feat(process): apply dialog PendingTime like Znuny` (+ trailer), `rtk git log -1 --oneline`.

### Task 12: Server-built dialog form (fields + ACL-filtered options)

Backend-only.

**Files:**
- Create: `backend/src/tiqora/process/dialog_form.py`
- Modify: `backend/src/tiqora/process/schemas.py` (D9 models), `backend/src/tiqora/api/v1/process.py` (`GET /{process_entity_id}/start-form`, `GET /ticket/{ticket_id}/dialog/{activity_dialog_entity_id}/form`), `backend/src/tiqora/api/portal/process.py` (same two), `packages/api-client/openapi.json`
- Test: `backend/tests/test_process_dialog_form.py` (DB, band 889000–889999)

**Interfaces:**
- Consumes: wave-0 `collect_field_options`, `FieldOptionsRequest`, `FieldOptionsContext` (`tiqora.domain.field_options`); `collect_ticket_field_options` (owner/responsible come in Task 15 — until then `Owner`/`Responsible` options use `{id: full name}` of valid agents unfiltered; Task 15 switches them); Task 7 `apply_ticket_acl(customer_user_login=…)`; Task 8 `assert_can_start` / `assert_can_submit`.
- Produces: D9 `FieldKind`, `ProcessDialogOptionOut`, `ProcessDialogFieldOut`, `ProcessDialogFormOut`, `build_dialog_form(session, *, actor, user, graph, dialog, ticket_id)` (agent routes pass `user=CurrentUser`, portal routes `user=None`), `collect_customer_field_options`; the four form routes.

- [ ] **Step 1: Failing tests**

```python
async def test_form_lists_visible_fields_in_order_with_kinds(client, session) -> None:
    # dialog FieldOrder [Title(1), QueueID(2), DynamicField_Region(2, Dropdown), Article(2), Lock(0)]
    form = (await client.get(f"/api/v1/process/{PID}/start-form")).json()
    assert [(f["name"], f["kind"], f["required"]) for f in form["fields"]] == [
        ("Title", "text", False), ("Queue", "select", True),
        ("DynamicField_Region", "select", True), ("Article", "article", True)]

async def test_agent_options_are_acl_filtered(client, session) -> None
    # ACL Properties Frontend Action [AgentTicketProcess]; PossibleNot Ticket Queue [Junk] → no Junk option

async def test_customer_form_skips_owner_and_filters_as_customer(portal_client, session) -> None
async def test_dialog_form_for_hidden_dialog_is_403(client, session) -> None
async def test_unsupported_df_type_is_marked(client, session) -> None   # e.g. a "Database" DF → "unsupported"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D9. Options ordered as the option sources return them; DF options follow wave-0 ordering.
- [ ] **Step 4: Run** → PASS; regenerate openapi; lint gates.
- [ ] **Step 5: Commit** `feat(process): server-built dialog forms with ACL-filtered options` (+ trailer), `rtk git log -1 --oneline`.

### Task 13: New process ticket (agent) + enroll through the start dialog

Backend-only.

**Files:**
- Create: `backend/src/tiqora/process/process_ticket.py`
- Modify: `backend/src/tiqora/process/schemas.py` (`ProcessStartIn.field_values`, `NewProcessTicketIn`, `NewProcessTicketOut`), `backend/src/tiqora/api/v1/process.py` (`POST /tickets` → 201; `POST /ticket/{id}/start` → `enroll_ticket`), `backend/src/tiqora/process/__init__.py`, `packages/api-client/openapi.json`
- Test: `backend/tests/test_process_ticket_create.py` (DB, band 888000–888499)

**Interfaces:**
- Consumes: Tasks 1, 8–11 (`apply_activity_dialog`, `run_transitions`, `assert_can_start`, `render_tags`), `create_ticket`, `assign_owner`, `PermissionEngine`, `SysConfig.get_str`.
- Produces: D10 `ProcessTicketResult`, `create_process_ticket`, `enroll_ticket`, `NewProcessTicketIn`, `NewProcessTicketOut`, `POST /api/v1/process/tickets`.

- [ ] **Step 1: Failing tests**

```python
async def test_create_process_ticket_uses_process_defaults_and_runs_transition(client, session) -> None:
    # sysconfig Process::DefaultQueue "Raw", DefaultState "new", DefaultLock "unlock",
    # DefaultPriority "3 normal"; start dialog: DynamicField_Region (2), Article (2), Owner (1);
    # unconditional transition to Activity B with TicketStateSet "open"
    r = await client.post("/api/v1/process/tickets", json={"process_entity_id": PID, "field_values": {
        "DynamicField_Region": "eu", "Subject": "Laptop", "Body": "Bitte bestellen", "Owner": str(AGENT2)}})
    assert r.status_code == 201
    out = r.json()
    t = await _ticket(session, out["ticket_id"])
    assert t.queue == "Raw" and t.state == "open" and t.owner_id == AGENT2
    assert t.title == "Laptop"                                 # Subject fallback
    assert out["state"]["activity_entity_id"] == ACTIVITY_B and out["activity_changed"] is True
    # history: NewTicket, TicketDynamicFieldUpdate x2 (process + activity), OwnerUpdate, AddNote, StateUpdate

async def test_default_title_is_process_name_and_timestamp(...) -> None
    # no Title/Subject → title starts with "<process name> - " and ends with "(<OTRSTimeZone>)"

async def test_missing_required_field_is_400_and_nothing_written(...) -> None
async def test_needs_create_permission_on_queue(...) -> None   # 403, no ticket row
async def test_fadeaway_process_cannot_create(...) -> None      # 409
async def test_enroll_applies_start_dialog_and_transitions(...) -> None
async def test_enroll_requires_mandatory_start_fields(...) -> None   # 400 without field_values
async def test_invalid_default_queue_is_clear_error(...) -> None    # Process::DefaultQueue "Nope" → 400 "Process::DefaultQueue fehlt oder ist ungültig."
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D10. The router uses one `async with session.begin()` for the whole create (ticket number generation uses the session factory as `create_ticket` already does — pass `get_session_factory()` like `api/v1/tickets.py` create does; grep it).
- [ ] **Step 4: Run** → PASS; regenerate openapi; lint gates.
- [ ] **Step 5: Commit** `feat(process): new process ticket from the start dialog; enroll via start dialog` (+ trailer), `rtk git log -1 --oneline`.

### Task 14: New process ticket (portal) + portal enroll through the start dialog

Backend-only.

**Files:**
- Modify: `backend/src/tiqora/api/portal/process.py` (`POST /tickets`, `POST /ticket/{id}/start` → `enroll_ticket` with the customer actor), `packages/api-client/openapi.json`
- Test: `backend/tests/test_portal_process_ticket.py` (DB, band 888500–888999)

**Interfaces:**
- Consumes: Task 13 `create_process_ticket`, `enroll_ticket`; `customer_can_access_ticket`.
- Produces: `POST /api/portal/process/tickets` → 201 `NewProcessTicketOut`.

- [ ] **Step 1: Failing tests:** `test_customer_creates_process_ticket_with_forced_identity` (submitted `CustomerUserID`/`CustomerID`/`Owner` ignored; ticket `customer_user_id` = the login, `customer_id` = the customer's id, `owner_id` = 1, article `SenderType customer`, `create_by` = 1), `test_customer_cannot_use_agent_only_process` (409), `test_customer_acl_hides_process` (409), `test_customer_enroll_needs_ticket_access` (foreign ticket → 404/403 as `_assert_customer_ticket`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; reuse the portal's `_map_exc` + `_MAPPED` (add `ProcessNotStartable`, `ProcessNotRunning`).
- [ ] **Step 4: Run** → PASS; regenerate openapi; lint gates.
- [ ] **Step 5: Commit** `feat(portal): customers start process tickets from the start dialog` (+ trailer), `rtk git log -1 --oneline`.

### Task 15: Zoom action ACL endpoint, owner/responsible ACL options for bulk, compat pin

Backend + frontend fixture updates (`TicketFieldOptionsOut.owner`/`.responsible` become required TS properties).

**Files:**
- Create: `backend/src/tiqora/domain/ticket_action_acl.py`
- Modify: `backend/src/tiqora/api/v1/tickets.py` (`GET /{ticket_id}/acl-actions`, registered next to `/{ticket_id}/field-options`), `backend/src/tiqora/api/v1/reference.py` (`_FIELD_TO_SUBTYPE` + `TicketFieldOptionsOut` gain `owner`/`responsible`; `_base_field_maps` adds them; ACL data `{id: login}`, output labels full names), `backend/src/tiqora/process/dialog_form.py` (Owner/Responsible options via `collect_ticket_field_options(…, fields={"owner","responsible"}, action="AgentTicketProcess", ticket_id=…)`), `packages/api-client/src/client.ts` (inline type of the field-options call gains `owner`, `responsible`), `packages/api-client/openapi.json`
- Modify (frontend fixtures only): mocks/tests that build the field-options response — `grep -rln "ticketAclFieldOptions\|ticket-field-options\|field-options" frontend/src` (e.g. `TicketHeaderActions` tests, `demo/mockData.ts`) get `owner: {}`, `responsible: {}`
- Test: `backend/tests/test_ticket_action_acl.py` (DB, band 886500–886999), extend `backend/tests/test_ticket_acl.py` (owner options), `backend/tests/test_compat_no_acl.py` (DB, band 884500–884999)

**Interfaces:**
- Consumes: Task 7 `apply_ticket_acl`.
- Produces: D11 `ZOOM_ACTIONS`, `allowed_ticket_actions`, `TicketAclActionsOut`, `GET /api/v1/tickets/{ticket_id}/acl-actions`; `TicketFieldOptionsOut.owner`, `.responsible`.

- [ ] **Step 1: Failing tests**

```python
async def test_acl_actions_hide_close_for_queue(client, session) -> None:
    # ACL Properties Queue Name [Billing-886]; PossibleNot Action [AgentTicketClose]
    r = await client.get(f"/api/v1/tickets/{TICKET_IN_BILLING}/acl-actions")
    assert "AgentTicketClose" not in r.json()["allowed"] and "AgentTicketNote" in r.json()["allowed"]

async def test_acl_actions_without_acls_lists_all(client, session) -> None   # == list(ZOOM_ACTIONS)
async def test_acl_actions_needs_ro(client, session) -> None                  # foreign queue → 403/404 like GET ticket
async def test_bulk_owner_options_filtered(client, session) -> None:
    # ACL Properties Frontend Action [AgentTicketBulk]; Possible Ticket Owner [agent-886001]
    # GET /reference/ticket-field-options?fields=owner&action=AgentTicketBulk → only that agent

async def test_compat_ticket_update_ignores_acls(compat_client, session) -> None:
    # ACL PossibleNot Ticket State [closed successful] for everyone; compat TicketUpdate
    # State "closed successful" succeeds — Znuny GI applies no TicketAcl (D1)
```

For the compat test reuse the fixtures of an existing compat test module (grep `op_ticket_update` in `backend/tests`).

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D11.
- [ ] **Step 4: Run** → PASS; regenerate openapi; update the frontend fixtures; `cd frontend && pnpm lint && pnpm test --run` → PASS; backend lint gates.
- [ ] **Step 5: Commit** `feat(acl): action ACLs for the zoom menu, owner/responsible ACL options` (+ trailer), `rtk git log -1 --oneline`.

### Task 16: Frontend — process API wrappers + shared `ProcessDialogForm`; dialog modal uses it

Frontend-only. Requires wave 0 Tasks 11 and 14 (`FieldsForm`, `FieldDef`, `fieldValueToDf`, `DF_KIND`).

**Files:**
- Create: `frontend/src/lib/processApi.ts`, `frontend/src/lib/portalProcessApi.ts`, `frontend/src/components/process/processForm.ts`, `frontend/src/components/process/processForm.test.ts`, `frontend/src/components/process/ProcessDialogForm.tsx`, `frontend/src/components/process/ProcessDialogForm.test.tsx`
- Modify: `frontend/src/components/agent/process/ActivityDialogModal.tsx` (loads `processApi.dialogForm`, renders `ProcessDialogForm`, validates with wave-0 `validateFieldValues`, submits `toSubmitValues`), `frontend/src/components/agent/process/ProcessWidget.tsx` + `ProcessWidget.test.tsx` (new state fields `process_state`, `can_start`; inactive note `process.widget.inactive`), `frontend/src/demo/mockData.ts` (form + state mocks), `en.json`/`de.json` (`process.field.*`, `process.form.*`, `process.lock.*`, `process.widget.inactive`) + propagate
- Delete: `frontend/src/components/agent/process/processDialogFields.ts`

**Interfaces:**
- Consumes: wave-0 `FieldsForm`, `FieldDef`, `FieldValues`, `FieldValue`, `FieldErrors`, `validateFieldValues`, `fieldValueToDf`, `fromZonedInputValue` (`lib/timeZone`); D9 schemas.
- Produces: D12 `processApi`, `portalProcessApi`, `processFieldDefs`, `initialProcessValues`, `toSubmitValues`, `processLabel`, `ARTICLE_SUBJECT_KEY`, `ARTICLE_BODY_KEY`, `ProcessDialogForm`.

- [ ] **Step 1: Failing tests** (`processForm.test.ts`)

```ts
const form: ProcessDialogFormData = { process_entity_id: "P", activity_dialog_entity_id: "AD", name: "Bestellung",
  description_short: "", description_long: "", submit_button_text: "", submit_advice_text: "",
  fields: [
    { name: "Queue", kind: "select", label: "Queue", required: true, description_short: "", description_long: "", default: null,
      options: [{ value: "3", label: "Support" }] },
    { name: "Article", kind: "article", label: "Article", required: true, description_short: "", description_long: "", default: null, options: [] },
    { name: "PendingTime", kind: "pending_time", label: "PendingTime", required: false, description_short: "", description_long: "", default: null, options: [] },
    { name: "DynamicField_Tags", kind: "multiselect", label: "Schlagworte", required: false, description_short: "", description_long: "", default: null,
      options: [{ value: "a", label: "A" }, { value: "b", label: "B" }] },
  ] };

it("splits the article into subject and body", () => {
  const defs = processFieldDefs(form, t);
  expect(defs.map((d) => d.key)).toEqual(["Queue", ARTICLE_SUBJECT_KEY, ARTICLE_BODY_KEY, "PendingTime", "DynamicField_Tags"]);
  expect(defs.find((d) => d.key === ARTICLE_BODY_KEY)?.kind).toBe("textarea");
});
it("maps values back to the API contract", () => {
  const v = toSubmitValues(form, { Queue: "3", [ARTICLE_SUBJECT_KEY]: "S", [ARTICLE_BODY_KEY]: "B",
    PendingTime: "2026-10-12T10:00", DynamicField_Tags: ["a", "b"] }, "Europe/Berlin");
  expect(v).toEqual({ Queue: "3", Subject: "S", Body: "B", PendingTime: "2026-10-12T08:00:00.000Z", DynamicField_Tags: ["a", "b"] });
});
```

(Use whatever ISO format `fromZonedInputValue` returns; assert against it rather than hand-writing a different format.) `ProcessDialogForm.test.tsx`: renders required marker for `required` fields and the unsupported note for `kind: "unsupported"`. `ActivityDialogModal` test (new, or extend `ProcessWidget.test.tsx`): submit blocked while a required field is empty; submit posts `toSubmitValues`.

- [ ] **Step 2: Run** `cd frontend && pnpm test --run src/components/process` → FAIL.
- [ ] **Step 3: Implement** per D12.
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm test --run`; `node frontend/scripts/check-i18n-keys.mjs` → PASS.
- [ ] **Step 5: Commit** `feat(frontend): shared process dialog form on the field engine` (+ trailer), `rtk git log -1 --oneline`.

### Task 17: Frontend — agent "Neues Prozessticket" page, entry point, enroll via start dialog

Frontend-only.

**Files:**
- Create: `frontend/src/routes/agent/NewProcessTicketPage.tsx`, `frontend/src/routes/agent/NewProcessTicketPage.test.tsx`
- Modify: `frontend/src/router.tsx` (`agentNewProcessTicketRoute`, registered before any `$param` sibling), `frontend/src/components/agent/NewTicketButton.tsx` + `NewTicketButton.test.tsx` (process section), `frontend/src/components/agent/process/StartProcessDialog.tsx` (two steps: process → start form → `processApi.enroll`), `frontend/src/demo/mockData.ts`, `en.json`/`de.json` (`process.newTicket.*`, `newTicket.processLabel`, `process.start.next`) + propagate

**Interfaces:**
- Consumes: Task 16 `processApi`, `ProcessDialogForm`, `processFieldDefs`, `initialProcessValues`, `toSubmitValues`.
- Produces: route `/agent/process/new?process=<eid>`; testids `new-process-page`, `new-process-select`, `new-process-submit`, `new-process-error`, `new-ticket-process-<eid>`.

- [ ] **Step 1: Failing tests** (`NewProcessTicketPage.test.tsx`): with `?process=P` the start form renders; submit calls `processApi.createTicket({process_entity_id: "P", field_values: …})` and navigates to `/agent/tickets/<id>`; API 400 shows `apiErrorMessage` in `new-process-error`; without startable processes shows `process.newTicket.empty`. `NewTicketButton.test.tsx`: with one queue and one startable process the menu shows the queue entry and `new-ticket-process-P`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D12 (page layout like `NewTicketPage`: heading, process `SelectField` when no `process` search param, then the form, primary button = `submit_button_text || t("process.form.submit")`, advice text under it).
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm test --run`; i18n check → PASS. Demo check: `VITE_DEMO=1` page renders with mocks (skill/notes `tiqora-ui-check-via-demo-mode`; delete `.playwright-mcp` before commit).
- [ ] **Step 5: Commit** `feat(frontend): agents start new process tickets from the start dialog` (+ trailer), `rtk git log -1 --oneline`.

### Task 18: Frontend — portal new process ticket + portal dialogs via the shared form

Frontend-only.

**Files:**
- Create: `frontend/src/routes/portal/NewProcessTicketPage.tsx`, `frontend/src/routes/portal/NewProcessTicketPage.test.tsx`
- Modify: `frontend/src/router.tsx` (`portalNewProcessTicketRoute`), `frontend/src/routes/portal/TicketListPage.tsx` (link `portal-new-process` when startable processes exist), `frontend/src/components/portal/PortalProcessWidget.tsx` (dialogs via `portalProcessApi.dialogForm` + `ProcessDialogForm`; enroll via start form), `frontend/src/demo/mockData.ts`, `en.json`/`de.json` (`portal.process.start`) + propagate

**Interfaces:**
- Consumes: Task 16 `portalProcessApi`, `ProcessDialogForm`.
- Produces: route `/portal/process/new?process=<eid>`; testids `portal-new-process`, `portal-new-process-page`, `portal-new-process-submit`.

- [ ] **Step 1: Failing tests:** page creates the ticket via `portalProcessApi.createTicket` and navigates to `/portal/tickets/<id>`; the widget renders a `customer`-skipped form (no Owner field) from a mocked form; the list page shows the link only when `listStartable` is non-empty.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm test --run`; i18n check → PASS.
- [ ] **Step 5: Commit** `feat(portal): customers start process tickets; dialogs on the shared form` (+ trailer), `rtk git log -1 --oneline`.

### Task 19: Frontend — zoom menu gating, bulk ACL options, admin deploy status

Frontend-only.

**Files:**
- Create: `frontend/src/lib/deployStatusApi.ts`, `frontend/src/components/process/DeployStatusCard.tsx`, `frontend/src/components/process/DeployStatusCard.test.tsx`
- Modify: `frontend/src/components/agent/TicketHeaderActions.tsx` (+ its test), `frontend/src/components/agent/TicketZoomOverflowMenu.tsx`, `frontend/src/routes/agent/TicketZoomPage.tsx` (one `useQuery(["tickets", id, "acl-actions"], processApi.aclActions)` passed down as `allowedActions: Set<string> | null` — `null` while loading or on error = show everything, never block the UI on this call), `frontend/src/routes/agent/QueuesPage.tsx` (+ `QueuesPage.test.tsx`: bulk pickers filtered by `fields=state,priority,queue,owner,responsible&action=AgentTicketBulk`), `frontend/src/routes/admin/ProcessesPage.tsx` (uses `deployStatusApi.processes`, state badge column, `DeployStatusCard kind="process"`), `frontend/src/routes/admin/AclPage.tsx` (+ test: `DeployStatusCard kind="acl"`), `frontend/src/routes/admin/DaemonsPage.tsx` (+ test: banner `daemons-deploy-live` with `admin.daemons.deployLive` when the `deploy_watch` row's `last_result.mode === "live"`), `frontend/src/demo/mockData.ts`, `en.json`/`de.json` (`admin.deploy.*`, `admin.processes.*`) + propagate

**Interfaces:**
- Consumes: Task 6 + Task 15 endpoints; Task 16 `processApi.aclActions`.
- Produces: D12 `deployStatusApi`, `DeployStatusCard`; testids `deploy-status-process`, `deploy-status-acl`, `daemons-deploy-live`.

- [ ] **Step 1: Failing tests:** `TicketHeaderActions` with `allowedActions = new Set(["AgentTicketNote"])` hides `ticket-actions-reply`, `more-merge`, `ticket-status-closed`; with `null` shows all. `DeployStatusCard`: pending 3 → warning text with 3; `running_live` → danger text; revision source `znuny` → "aus Znuny". `QueuesPage`: an owner missing from the ACL map is not offered in the bulk owner picker. `DaemonsPage`: banner appears for `mode: "live"`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per D11/D12 mapping.
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm test --run`; i18n check → PASS; demo-mode visual check of ProcessesPage and AclPage.
- [ ] **Step 5: Commit** `feat(frontend): ACL-gated zoom menu and bulk pickers, deploy status for admins` (+ trailer), `rtk git log -1 --oneline`.

### Task 20: Golden tests on real peers (manual, GOLDEN=1) — 6.5 and 7.3

Backend tests only; runs against `tests/golden/` stacks (`GOLDEN_PEER=znuny-6.5` and `GOLDEN_PEER=znuny-7.3`; 7.3 source tree `znuny-7.3.5/` must be present, see `tests/golden/README-multi-peer.md`).

**Files:**
- Create: `tests/golden/test_process_deploy.py`
- Modify: `tests/golden/README-multi-peer.md` (one line: what the new module proves)

**Interfaces:**
- Consumes: `znuny_perl_eval`, golden session fixtures; Tasks 2–5, 13.

- [ ] **Step 1: Write the golden module** (all tests `pytestmark = pytest.mark.golden`):

```python
_IMPORT_AND_DEPLOY = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
use JSON::PP;
local $Kernel::OM = Kernel::System::ObjectManager->new('Kernel::System::Log' => {LogPrefix => 'golden'});
my $P = $Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Process');
my $Yaml = $Kernel::OM->Get('Kernel::System::Main')->FileRead(
    Location => '/opt/otrs/scripts/test/sample/ProcessManagement/AgentTicketProcess.yml', Result => 'SCALAR');
my %R = $P->ProcessImport(Content => $$Yaml, OverwriteExistingEntities => 1, UserID => 1);
die $R{Message} if !$R{Success};
my $Dump = $P->ProcessDump(ResultType => 'HASH', UserID => 1);
$Kernel::OM->Get('Kernel::System::ProcessManagement::DB::Entity')->EntitySyncStatePurge(UserID => 1);
print JSON::PP->new->canonical->encode($Dump);
"""

async def test_tiqora_dump_equals_znuny_process_dump(golden_session) -> None:
    znuny = json.loads(znuny_perl_eval(_IMPORT_AND_DEPLOY))
    tiqora = await build_process_dump(golden_session)
    for kind in ("Process", "Activity", "ActivityDialog", "Transition", "TransitionAction"):
        for eid, entry in znuny[kind].items():
            # JSON::PP emits Perl scalars as numbers or strings inconsistently (ID, Display) —
            # compare after str()-normalising every scalar on both sides (helper _norm)
            assert _norm({k: tiqora[kind][eid][k] for k in entry}) == _norm(entry), (kind, eid)

async def test_watcher_snapshots_after_znuny_deploy(golden_session) -> None:
    # after the import+purge above: check_deploy_state(process) → snapshot, source znuny or initial

async def test_acl_deploy_cycle(golden_session) -> None:
    # Znuny ACLAdd via perl → acl_sync non-empty → watcher waiting/running_live;
    # ACLDump(ResultType => 'FILE', Location => '/tmp/golden-acl.pm') + ACLsNeedSyncReset → snapshot;
    # ACLDump has no HASH result type, so compare Tiqora's dump["ACL"][name]["Values"] with the
    # peer's ACLListGet(UserID => 1, ValidIDs => [1]) mapped exactly like ACLDump does
    # (ACL/DB/ACL.pm:858-905: Properties/PropertiesDatabase from ConfigMatch, Possible* from ConfigChange)

async def test_new_process_ticket_matches_znuny_history(golden_session) -> None:
    # create one process ticket via Tiqora create_process_ticket and one via Znuny
    # (Kernel::System::Ticket TicketCreate + ProcessTicketProcessSet + ActivitySet as AgentTicketProcess
    # does) on the imported process → same history_type sequence and same DF values
```

- [ ] **Step 2: Run** on both peers:

```bash
GOLDEN_PEER=znuny-6.5 just golden-run   # or: golden-up, golden-seed, then
cd backend && GOLDEN=1 uv run python -m pytest ../tests/golden/test_process_deploy.py -q
GOLDEN_PEER=znuny-7.3 just golden-run
```

Expected: PASS on both. A 7.3 difference in dump keys → fix `build_process_dump` (and note it in `docs/compatibility.md`), never special-case the test.
- [ ] **Step 3: Commit** `test(golden): process/ACL deploy and process ticket parity on 6.5 and 7.3` (+ trailer), `rtk git log -1 --oneline`.

### Task 21: Docs + full test and lint run

**Files:**
- Modify: `docs/process-management.md` (deploy model + revisions, state gate, ACL surfaces, Display semantics, smart tags, PendingTime, new process ticket for agents and customers, enroll via start dialog, Known limitations from this plan; remove "Tiqora does not write pm_entity_sync / smart tags deferred / PendingTime not applied" statements), `docs/compatibility.md` (process rows: deployed-only, state gate, ACL surfaces, new process ticket, compat API without ACLs as in Znuny GI, 7.x verified; ACL row: `acl_sync` written like AdminACL, ACLs run from the deployed state), `docs/architecture.md` (new `tiqora.deployed` package, `deploy_watch` worker loop, revision tables), `docs/parallel-operation.md` (admins must press "Bereitstellen" in Znuny for process/ACL edits made in either system; after cutover Tiqora deploys automatically), user/admin guide entries under `docs/guide/` where processes/ACLs are described (grep `process` in `docs/guide`)
- Do NOT edit `site/compare.html` or README claims.

- [ ] **Step 1: Write the docs** (German UI names quoted as in the UI: "Bereitstellen", "Neues Prozessticket", "Prozess starten").
- [ ] **Step 2: Full backend run:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora` → all green.
- [ ] **Step 3: Full frontend run:** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → all green; `rtk git diff --stat packages/api-client/openapi.json` sane.
- [ ] **Step 4: Commit** `docs(process): deployed revisions, state gate, ACL surfaces, process tickets` (+ trailer), `rtk git log -1 --oneline`.

---

## Self-review notes (for the executor)

- Spec coverage: state gate (Tasks 8, 9 `run_transitions`), deployed snapshots + Znuny deploy detection + startup rule (Tasks 2–5), ACLs on process list / dialogs / action menu / bulk / compat (Tasks 7, 8, 12, 15, 19), 7.x YAML verification (D1 + Task 20), smart tags in actions and conditions (Tasks 9, 10), dialog PendingTime (Task 11), new process ticket agent + portal (Tasks 13, 14, 17, 18), `acl_sync` on Tiqora ACL edits (Task 5). Phase 3 (Tiqora deploy button, TiqoraSync `ProcessDump`/`ACLDump`) is intentionally absent.
- Shared-file hotspots with other wave-1 plans: one Alembic migration (Task 3 — check the head right before writing it), `config.py` (one field), `worker/__main__.py` + `worker/services.py` (one loop, one catalog row → `test_admin_daemons` count), `api/v1/admin/__init__.py` (one router), `api/v1/reference.py` (`TicketFieldOptionsOut` + owner/responsible), `channels/email/placeholder.py` (DF tags), `router.tsx` (two routes), i18n files.
