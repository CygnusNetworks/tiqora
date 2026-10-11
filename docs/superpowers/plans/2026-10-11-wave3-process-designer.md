# Wave 3: Process Management Phases 2–3 ("Typed dialogs + Designer") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining process-management parity gaps and give admins a process designer. Phase 2 (parity actions and forms) adds the transition action registry, the `Appointment*` actions, the condition module registry, the remaining typed dialog widgets (customer search, article uploads and time units, the Znuny `Attachments` field, options that refresh when a field changes) and the "path so far" view in the ticket zoom. Phase 3 (designer) adds a write service for `pm_*` with Znuny's draft semantics, the admin designer API, validation, YAML import/export in Znuny's format, revision diff and restore, deploy from Tiqora via TiqoraSync 1.2.0 while Znuny still runs, and a canvas designer on `@xyflow/react` with typed panels.

**Architecture:** Backend phase 2 extends `tiqora.process` in place. The action handler dict becomes the extension point `TRANSITION_ACTIONS`, which carries parameter specs for the designer forms. A new `CONDITION_MODULES` point serves `Type: Module` conditions. Dialog form kinds grow, and the engine learns uploads, time units, `ProcessManagementAttachment` and `AttachmentsReuse`. Two option-refresh endpoints and one path endpoint are added. Backend phase 3 adds the package `tiqora.process.designer`, which holds the only code that writes `pm_*` and `pm_entity_sync`. Its parts are: ids, a YAML codec, versions, the writer, the draft reader, the catalogue, validation, exchange and revisions. Admin routes live at `/api/v1/admin/process-designer/*`. The existing `POST /admin/deploy` gains a "signal TiqoraSync" mode: it records the revision and queues a `tiqora_cache_invalidation` row `TiqoraDeploy_<kind>_<revision_id>`. TiqoraSync 1.2.0 then runs Znuny's own `ProcessDump`/`EntitySyncStatePurge` (`ACLDump`/`ACLsNeedSyncReset` for ACLs) and reports back through `tiqora_settings`. The frontend registers two new field kinds (`customer` and `ticket_attachments`) and an option-refresh hook in the shared process form. The designer is a lazily loaded admin route: a pure canvas model (draft to nodes and edges), the canvas, typed side panels, a condition builder, action forms generated from the registry's parameter specs, plus validation, revision and deploy panels.

**Tech Stack:** FastAPI, SQLAlchemy 2 async (MariaDB and PostgreSQL), PyYAML, pydantic v2, pytest, Perl (Znuny add-on, Framework 6.0–7.3), React 19, TanStack Router and Query, Tailwind tokens, i18next, `@xyflow/react` (new, MIT, lazy chunk), Vitest, Playwright (demo mode).

**Spec:** `docs/superpowers/specs/2026-10-08-process-management-design.md`: "Parity checklist" (Engine: missing actions, `Module` conditions; UI: typed widgets, process widget path; Designer), "Fixed decisions → Deploy model / Engine changes / Designer / Dialog forms", "Phases → 2 Parity actions + forms, 3 Designer". Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` → "Waves", row 3 ("Process 2–3 (typed dialogs, designer, deploy via TiqoraSync)"). Builds on `docs/superpowers/plans/2026-10-08-wave1-process-correctness.md`, which is merged (names D2–D12 used verbatim), and on the wave-0/2 foundations (`tiqora.extensions`, `components/fields/*`, jobs, settings sections).

## Open questions for the product owner

Each item below is decided conservatively in this plan. Every one is marked "Assumption:" where it applies.

1. **Deploy from Tiqora while Znuny runs.** Assumption: allowed only when TiqoraSync ≥ 1.2.0 has reported a heartbeat in the last 10 minutes. Tiqora records the revision. TiqoraSync, not Tiqora, clears `pm_entity_sync`/`acl_sync` and writes the ZZZ file, exactly like Znuny's own button. With an older add-on or none, the request answers 409 as today.
2. **Process import writes `not_sync` rows**, unlike Znuny's `ProcessImport`, so an imported process never runs until someone presses "Bereitstellen". Assumption: yes.
3. **"Restore" a revision** means the process's entities from that revision are written back into `pm_*` as drafts, and someone must then deploy. It is not a live switch, and it deletes nothing. Assumption: yes.
4. **Validation findings never block a deploy** (Znuny validates nothing). With errors present, the deploy dialog asks for confirmation. Assumption: yes.
5. **Only admins (group `admin`) may use the designer and deploy.** This matches Znuny's `AdminProcessManagement` (group admin). Assumption: yes; there is no separate "process designer" role.
6. **The path view ("bisheriger Weg") is shown to agents only.** The portal keeps showing just the current step. Assumption: yes.
7. **Transitions after every ticket change** (Znuny's event module `TicketProcessTransitions`, listed in wave 1 as a phase-2 candidate) are not in the spec's phase 2/3 rows and are left out. Assumption: out of scope; please confirm.
8. **Process categories** in the start menu (Znuny 7.2+ `pm_process_preferences`, key not verified on 7.3) are left out. Assumption: deferred until the key is verified on the golden peer.
9. **`ExecuteInvoker`** depends on the parallel wave-3 GI requester plan. Assumption: Task 31 is conditional; without that plan's invoker entry point the action stays "nicht unterstützt".
10. **`ConfigItemUpdate`** needs the CMDB (wave 4). Assumption: out of scope. The designer offers it with the raw key/value form and the note "nur in Znuny wirksam".
11. **Unknown dynamic field types** (anything other than the seven Tiqora types) stay read-only in dialogs. Assumption: yes.
12. **The designer edits the shared Znuny entities** (activities, dialogs, transitions and actions are global and reusable across processes in Znuny). Deleting an entity is refused while any process or activity uses it. A process can be deleted only when it is Inactive. Assumption: these are Znuny's rules, kept unchanged.
13. **Built-in condition modules:** only Znuny's `ValidateDemo` is ported. Tiqora adds no native `Tiqora::` condition module in this wave; plugins can register modules. Assumption: yes.
14. **Concurrent designer edits** use an optimistic version per entity (409 "inzwischen geändert"). Znuny itself has last-write-wins. Assumption: yes.
15. **New processes start Inactive (S2)**, whereas Znuny's form defaults to Active. An undeployed draft is never startable anyway, and this makes going live a conscious switch. Assumption: yes.
16. **`AppointmentCreate` does not link the appointment to the ticket**, like Znuny's action, which writes no `calendar_appointment_ticket` row. Assumption: parity over convenience.
17. **Designer-written config values are stored as YAML strings** (`Display: '2'`, `left: '120'`), as Znuny's web forms submit them. Assumption: yes.
18. **The designer is read-only on phones (< 768 px)**: a list view with sheets, no canvas editing. Assumption: yes.

## Global Constraints

- **Worktree and branch:** work only in the worktree and on the local branch named in the controller's implementer-instructions file for this plan (`.superpowers/sdd/implementer-instructions-wave3process.md`). Never commit to `main`; never push (a push to origin main deploys production); no branches or PRs on GitHub. Never `--no-verify` (PII hooks). Use `rtk git` (`rtk git add <explicit paths>`, `rtk git commit …`). Run `rtk git diff --cached --name-only` before every commit and `rtk git log -1 --oneline` after it, because the pre-commit hook can block silently. Never amend, reset, rebase or stash, and never restore files you did not change. Every commit message ends with the `Co-Authored-By` trailer the executing session's attribution reminder prescribes.
- **Example data** only from `example.org` and `*.test`. Logins and names in tests contain `wf3`.
- **Backend tests:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q <modules>` (Docker; never plain `uv run pytest`). Run the full suite in Task 30. Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. `ruff format --check` is the gate that turns CI red; run all three.
- **Both dialects:** every task that touches SQL tests on BOTH MariaDB and PostgreSQL (`@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])`, pattern `backend/tests/test_reports_api.py`). New DB test modules delete every row they wrote (pattern `_seed_statements` plus a module-scoped autouse fixture, `backend/tests/test_ai_refine.py`). Never add entries to `backend/tests/db_leak_baseline.txt`.
- **Seed id bands** (free on 2026-10-11; re-check with `grep -rn "<prefix>" backend/tests` before use, skill `pytest-session-container-id-band-collisions`): `891000–891999` action registry and appointments, `892000–892999` condition modules and dialog fields, `893000–893999` option refresh and path, `894000–894999` writer and designer API, `896000–896999` validation, exchange and revisions, `898000–898999` deploy signal, `899000–899999` spare. Entity ids in tests look like `Process-wf3…` with 32 hex digits, generated by `generate_entity_id` or a fixed literal such as `Activity-` + `"a" * 32`.
- **Tables:** write only `tiqora_*` tables, except through existing Znuny write services (`tiqora.domain.ticket_write_service`, `tiqora.calendar.service.CalendarService`, `tiqora.deployed.sync_tables`). **Scoped exception, by spec ("Deploy model"):** this plan adds `tiqora.process.designer.writer` as THE write service for `pm_process`, `pm_activity`, `pm_activity_dialog`, `pm_transition`, `pm_transition_action` and `pm_entity_sync`. No other module writes those tables. Its writes match Znuny's `Kernel::System::ProcessManagement::DB::*` row for row (D10). Never change Znuny DDL.
- **No cross joins:** never JOIN `tiqora_*` tables with Znuny tables. Prod MariaDB mixes `general_ci`/`unicode_ci` and fails with error 1267. Load the keys first, then query with an IN-list.
- **Transactions:** request handlers use the request session's autobegin. Never call `session.begin()` / `db.begin()` on a session that already autobegan. Functions that own their transaction (`deploy_from_tiqora`, the revision write lock) are called after `await session.rollback()` of the read transaction, as `api/v1/admin/deploy.py` already does.
- **No long synchronous endpoints:** nothing in this plan may run longer than a few seconds synchronously, because prod nginx cuts `/api/` at 90 s. Import (≤ 5 MB YAML), validation, diff, restore and deploy are bounded (a process set is small; ruling F31 of wave 1). If a task finds a step that can exceed ~10 s on a large set, it moves that step into a wave-0 job (`tiqora.jobs`, `JOB_HANDLERS`) and polls `GET /api/v1/jobs/{id}`.
- **Migrations:** this plan needs **no** migration (revisions, settings and signals already exist). If a task finds one necessary, it goes into `backend/alembic/versions_tiqora` with the **next free number at implementation time**, after the current primary head (at the time of writing `20261010_0072`, after the web chat merge; check `ls backend/alembic/versions_tiqora | tail -1` and `cd backend && uv run alembic heads`; never hardcode it). Follow skill `alembic-two-chain-new-head-forks-graph`, bump `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version ==` assert in `backend/tests/test_migrate_cli.py`, and keep the ORM equal to the DDL (`backend/tests/test_tiqora_model_ddl.py`).
- **Perl-truthy flags:** Znuny YAML and SysConfig flags are Perl-truthy strings (`'0'` is false). Read them with `tiqora.util.perl.perl_truthy` / `SysConfig.get_bool`, never with `bool(...)`.
- **Timestamps:** values written into `tiqora_*` are naive UTC from `tiqora.jobs.service.utc_now()`. API datetimes use `tiqora.domain.schemas.UtcDateTime`. Writes to Znuny tables use SQL `current_timestamp`, exactly like Znuny's SQL.
- **OpenAPI:** after any API model or route change, regenerate per skill `tiqora-openapi-regen-or-ci-wipes-schema`: `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`. Check that `head -c 40 packages/api-client/openapi.json` starts with `{"components"` and that the diff stat is tens or hundreds of lines. Never hand-edit `schema.d.ts`. A Pydantic response field WITH a default becomes REQUIRED in the generated TS type, so fixtures, `frontend/src/demo/mockData.ts` and `frontend/e2e/fixtures/mock-api.ts` must carry it in the same commit.
- **Frontend:** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`. Never run prettier or biome on `frontend/`. New i18n keys go into `en.json` and `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). **Frontend tasks that add i18n keys run sequentially, never in parallel (ruling R-I18NSEQ).** New screens get demo-mode mocks (`VITE_DEMO=1`) and a Playwright check in demo mode at 1280 px and 390 px, light and dark; delete `.playwright-mcp` before committing. Colours come only from existing tokens / `themeColor()`; this plan adds no colour token. API wrappers are hand-written `frontend/src/lib/*Api.ts` files using `api.request<T>(method, path, {query, body, signal})` / `portalApi.request<T>(…)` with `Schemas["…"]` types; error text comes from `apiErrorMessage`.
- **UI copy:** German first, plain words, addressing the user with "Sie": "Prozess", "Schritt", "Formular", "Übergang", "Aktion", "Bedingung", "Entwurf", "Bereitstellen", "In Znuny übernommen", "Bisheriger Weg", "Fassung" (for revision), "Wiederherstellen". No system jargon in UI text: never "Revision", "Snapshot", "Entity", "Path", "TransitionAction", "YAML-Dump" (allowed in code and admin docs only). The file format is called "Znuny-Prozessdatei (.yml)".
- **Znuny as reference:** Znuny behaviour decides every engine and writer question. Each ported rule cites its Znuny file and line range in a code comment. The reference source (6.5 core, read-only) is `<repo>/znuny-6.5.22/`. Golden peers 6.5 and 7.3 are under `tests/golden/`.
- **New npm dependency:** `@xyflow/react` at an exact version at least 3 days old (`pnpm-workspace.yaml` `minimumReleaseAge: 4320`; skill `pnpm-minimum-release-age-lockfile-verification`). First confirm that its `peerDependencies` allow React 19 (`npm view @xyflow/react@<v> peerDependencies`). Add a NOTICE.md entry (MIT).

## Review Focus

1. **A designer edit in Tiqora never runs before a deploy, and Znuny sees it as a draft.** Saving an activity, dialog, transition, action or the process writes the `pm_*` row plus a `pm_entity_sync` `not_sync` row and the `ProcessManagement_*` cache signal. The engine keeps running the newest revision until "Bereitstellen". Pinned in Task 11 (`test_edit_writes_not_sync_row_and_cache_signal`, `test_engine_keeps_revision_after_designer_edit`) and Task 28 (golden: `test_tiqora_draft_is_a_draft_in_znuny`).
2. **Deploy while Znuny runs.** Tiqora runs the new revision at once. The card says "Wartet auf Znuny" until TiqoraSync reports `ok` for exactly that revision id, and never says "In Znuny übernommen" earlier. With a missing or old add-on the request is refused (409) with an explanation. After TiqoraSync's purge the watcher records no duplicate revision. An edit made between the deploy and the purge converges: Znuny dumps it, and the watcher snapshots it as `znuny`. Pinned in Task 17 (`test_signal_mode_records_revision_and_queues_signal`, `test_status_pending_until_addon_reports_same_revision`, `test_deploy_refused_without_fresh_heartbeat`, `test_watcher_unchanged_after_addon_purge`, `test_edit_between_deploy_and_purge_converges`) and Task 28 (golden).
3. **Import/export round trip.** A process exported from Znuny, imported into Tiqora and exported again is equal after parsing, ignoring entity ids, ids, times and key order. An imported process is a draft (`not_sync` rows), and missing dynamic fields stop the import with Znuny's message. Pinned in Task 15 (`test_import_writes_not_sync_rows`, `test_round_trip_parsed_equal`, `test_import_refuses_missing_dynamic_fields`) and Task 28 (golden with the six Znuny sample files).
4. **Two admins edit the same entity, or an admin deletes something in use.** A stale `expected_version` gets 409 "inzwischen geändert", and nothing is overwritten. Deleting a used activity, dialog, transition or action, or a process that is not Inactive, gets 409 with Znuny's rule as German text. Pinned in Task 11 (`test_stale_expected_version_is_409`, `test_delete_used_entity_is_refused`, `test_delete_process_requires_inactive`).
5. **Typed dialog fields keep Znuny's `Display` semantics and the customer boundary.** `TimeUnits` 0/1/2, article uploads, the `Attachments` field (agent and existing ticket only) and the customer picker are hidden, optional or required exactly as configured. A portal customer cannot set `CustomerUserID`/`CustomerID`/`Attachments` by forging the request. A failing `Appointment*` action is logged, and the transition still moves the ticket (Znuny `Process.pm:576-586` ignores the result). Pinned in Task 4 (`test_time_units_display_matrix`, `test_portal_forged_customer_and_attachments_ignored`), Task 5 (`test_attachments_field_stores_df_and_article_send_reuses`) and Task 2 (`test_failing_appointment_action_does_not_stop_transition`).

Also pinned (not top five): option refresh never returns options an ACL hides (Task 6); the path view renders a deleted activity as "(gelöscht)" (Task 7); `Type: Module` with an unknown module path does not match and shows up in validation (Tasks 3, 14); restore never deletes rows (Task 16); the designer chunk is not in the main bundle (Task 19).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Names from earlier waves are used exactly as they exist in code:

- `tiqora.process`: `ProcessRepository`, `ProcessGraph`, `ActivityDialogNode`, `LiveProcessSource`, `RevisionProcessSource`, `active_process_source`, `EntityRecord`, `build_process_dump`, `dump_checksum`, `canonical_json`, `PROCESS_STATE_NAMES`, `STATE_ACTIVE/INACTIVE/FADEAWAY`, `ProcessActor`, `apply_activity_dialog`, `run_transitions`, `execute_transition_action`, `_ACTION_HANDLERS`, `build_dialog_form`, `build_start_form`, `build_ticket_dialog_form`, `collect_customer_field_options`, `FieldKind`, `ProcessDialogFieldOut`, `ProcessDialogFormOut`, `visible_fields`, `effective_field_values`, `missing_required_fields`, `CUSTOMER_SKIP_FIELDS`, `replace_action_config`.
- `tiqora.deployed`: `record_revision`, `latest_revision_info`, `load_active_revision`, `RevisionInfo`, `revision_write_lock`, `deploy_from_tiqora`, `DeployResult`, `check_deploy_state`, `pending_sync_count`, `clear_sync_table`, `can_deploy_from_tiqora`, `ProcessDeploySettings`.
- Other modules: `tiqora.znuny.cache_invalidation.invalidate_cache_type`; `tiqora.extensions.core.get_registry` / `register_core` / `set_registry_for_tests`; `ExtensionPoint`; `CalendarService`; `decode_attachments` / `ArticleAttachmentIn` (`api/v1/tickets.py`); `normalize_time_unit` / `ArticleIn.attachments` (`ticket_write_service`); `TiqoraSettings`.
- Frontend: `FIELD_KINDS`, `FieldDef`, `FieldsForm`, `useFieldOptions`, `ProcessDialogForm`, `processFieldDefs`, `toSubmitValues`, `DeployStatusCard`, `CustomerPickerDialog`, `ComposerChips`, `useChatAttachments`, `ADMIN_PAGES`.

### D1. File map

Backend (new):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/process/actions/__init__.py` | re-exports |
| `backend/src/tiqora/process/actions/registry.py` | `ActionParam`, `TransitionActionModule`, `TRANSITION_ACTIONS`, `resolve_action`, `core_transition_actions` |
| `backend/src/tiqora/process/actions/appointments.py` | `AppointmentCreate/Update/Remove` handlers |
| `backend/src/tiqora/process/condition_modules.py` | `ConditionModule`, `CONDITION_MODULES`, `ValidateDemo` port, `resolve_condition_module` |
| `backend/src/tiqora/process/attachments_field.py` | Znuny `Attachments` field: list, DF value encode/decode, `_GetAttachments` port |
| `backend/src/tiqora/process/form_options.py` | option refresh for a dialog form with current values |
| `backend/src/tiqora/process/path.py` | "path so far" from `ticket_history` |
| `backend/src/tiqora/process/designer/__init__.py` | re-exports |
| `backend/src/tiqora/process/designer/ids.py` | `ENTITY_TYPES`, `generate_entity_id` |
| `backend/src/tiqora/process/designer/yaml_codec.py` | `dump_znuny_yaml`, `load_znuny_yaml`, `stringify_scalars` |
| `backend/src/tiqora/process/designer/versioning.py` | `entity_version` |
| `backend/src/tiqora/process/designer/writer.py` | `ProcessWriter`, the only `pm_*` / `pm_entity_sync` writer |
| `backend/src/tiqora/process/designer/draft.py` | `load_process_draft`, `list_entities`, `load_sync_states` |
| `backend/src/tiqora/process/designer/catalog.py` | `build_designer_catalog` (actions, condition types/modules, fields, interfaces) |
| `backend/src/tiqora/process/designer/validation.py` | `validate_process`, `ValidationIssue` |
| `backend/src/tiqora/process/designer/exchange.py` | `export_process_yaml`, `import_process_yaml` |
| `backend/src/tiqora/process/designer/revisions.py` | `diff_revisions`, `restore_process_from_revision` |
| `backend/src/tiqora/process/designer/schemas.py` | designer DTOs (D11) |
| `backend/src/tiqora/deployed/znuny_sync.py` | heartbeat/status keys, `queue_deploy_signal`, `read_znuny_sync_status`, `addon_can_deploy` |
| `backend/src/tiqora/api/v1/admin/process_designer.py` | admin designer routes |

Backend (modified): `process/engine.py`, `process/conditions.py`, `process/dialog_form.py`, `process/schemas.py`, `process/fields.py`, `process/__init__.py`, `api/v1/process.py`, `api/portal/process.py`, `api/v1/admin/deploy.py`, `api/v1/admin/deploy_status.py`, `api/v1/admin/__init__.py`, `deployed/watcher.py`, `deployed/gate.py`, `extensions/core.py`, `api/v1/tickets.py` (move `decode_attachments` into `tiqora/domain/attachments_in.py` and re-export it).

Znuny add-on (modified): `packages/znuny-addon/TiqoraSync/Kernel/System/TiqoraSync.pm`, `TiqoraSync.sopm` (1.2.0), `install/README.md`.

Frontend (new):

| Path | Responsibility |
|---|---|
| `frontend/src/components/fields/CustomerKind.tsx` | `customer` field kind (search + `CustomerPickerDialog`) |
| `frontend/src/components/fields/TicketAttachmentsKind.tsx` | `ticket_attachments` field kind (multi-select of existing ticket attachments) |
| `frontend/src/components/process/ArticleUploads.tsx` | uploads under the article field (reuses `ComposerChips` + `useChatAttachments`) |
| `frontend/src/components/process/useProcessFormOptions.ts` | option refresh hook |
| `frontend/src/components/agent/process/ProcessPathTimeline.tsx` | "Bisheriger Weg" |
| `frontend/src/lib/processDesignerApi.ts` | designer API wrapper + types |
| `frontend/src/routes/admin/process-designer/ProcessDesignerPage.tsx` | lazy route component `/admin/processes/$processEntityId` |
| `frontend/src/components/process-designer/canvasModel.ts` | pure: draft ↔ nodes/edges, edits |
| `frontend/src/components/process-designer/DesignerCanvas.tsx` | `@xyflow/react` canvas |
| `frontend/src/components/process-designer/ActivityNode.tsx`, `TransitionEdge.tsx`, `StartNode.tsx` | custom nodes/edges |
| `frontend/src/components/process-designer/EntityPalette.tsx` | reuse existing entities / create new |
| `frontend/src/components/process-designer/panels/ProcessPanel.tsx` | name, description, state, start dialog |
| `frontend/src/components/process-designer/panels/ActivityPanel.tsx` | dialogs in order |
| `frontend/src/components/process-designer/panels/DialogPanel.tsx` | fields, Display, defaults, interface, permission |
| `frontend/src/components/process-designer/panels/TransitionPanel.tsx` + `ConditionBuilder.tsx` + `conditionModel.ts` | conditions |
| `frontend/src/components/process-designer/panels/ActionPanel.tsx` + `actionForm.ts` | module picker, typed form, raw fallback |
| `frontend/src/components/process-designer/ValidationPanel.tsx` | issues list, jump to entity |
| `frontend/src/components/process-designer/RevisionsPanel.tsx` + `RevisionDiffView.tsx` | versions, diff, restore |
| `frontend/src/components/process-designer/DeployBar.tsx` | draft count, "Bereitstellen", Znuny status |
| `frontend/src/components/process-designer/ImportDialog.tsx` | file import |

Frontend (modified): `components/fields/kinds.tsx` (registration), `components/fields/types.ts` (`FieldKind` += `customer`, `ticket_attachments`), `components/process/processForm.ts`, `components/process/ProcessDialogForm.tsx`, `components/process/ProcessFormBody.tsx`, `components/process/DeployStatusCard.tsx`, `components/agent/process/ProcessWidget.tsx`, `lib/processApi.ts`, `lib/portalProcessApi.ts`, `lib/deployStatusApi.ts`, `routes/admin/ProcessesPage.tsx`, `routes/admin/ProcessDetailPage.tsx` (deleted; the route now renders the designer), `router.tsx`, `lib/adminSearch.ts`, `demo/mockData.ts`, `e2e/fixtures/mock-api.ts`, `i18n/locales/*.json`, `package.json`, `pnpm-lock.yaml`, `NOTICE.md`.

### D2. Verified Znuny facts (sources, 6.5.22 unless stated)

- **Entity ids:** `DB/Entity.pm:75-110` `EntityIDGenerate` = `<EntityType>-` + 32 random characters from `[0-9a-f]`. Valid types: `Process`, `Activity`, `ActivityDialog`, `Transition`, `TransitionAction`.
- **Sync state:** `DB/Entity.pm:126-200` `EntitySyncStateSet`. If no row exists for `(entity_type, entity_id)` it runs `INSERT INTO pm_entity_sync (entity_type, entity_id, sync_state, create_time, change_time) VALUES (?, ?, ?, current_timestamp, current_timestamp)`; otherwise `UPDATE pm_entity_sync SET sync_state = ?, change_time = current_timestamp WHERE entity_type = ? AND entity_id = ?`. States are `not_sync` (add/update) and `deleted` (delete). `EntitySyncStatePurge` = `DELETE FROM pm_entity_sync`.
- **Rows:** `DB/Process.pm:103-200` `ProcessAdd`: YAML `config` and `layout` (`Kernel::System::YAML->Dump`, values from forms are strings), `create_time/change_time = current_timestamp`, `create_by/change_by = UserID`; then `Cache->CleanUp(Type => 'ProcessManagement_Process')`. The other kinds use cache types `ProcessManagement_Activity`, `ProcessManagement_ActivityDialog`, `ProcessManagement_Transition` and `ProcessManagement_TransitionAction` (each `*Add/*Update/*Delete` in `DB/*.pm`). ACL edits use `ACLEditor_ACL`.
- **Process config:** `{Description, Path: {ActivityEID: {TransitionEID: {ActivityEntityID: <target>, TransitionAction: [<TAEID>…]}}}, StartActivity, StartActivityDialog}`. A process's activities are **the keys of `Path`** (`DB/Process.pm ProcessGet` 400-470), including end activities with `{}`. Layout: `{ActivityEID: {left: '<int>', top: '<int>'}}` with string values (`scripts/test/sample/ProcessManagement/AgentTicketProcess.yml:146`).
- **Activity config:** `{ActivityDialog: {'1': <ADEID>, '2': …}}` (order keys are strings).
- **ActivityDialog config:** `{DescriptionShort, DescriptionLong, Fields: {Name: {DescriptionShort, DescriptionLong, DefaultValue, Display, Config?}}, FieldOrder: [...], Interface: [AgentInterface|CustomerInterface…], Permission, RequiredLock, SubmitAdviceText, SubmitButtonText}`. The Article field `Config` holds `CommunicationChannel`, `IsVisibleForCustomer` and `TimeUnits` (`'0'` off, `'1'` optional, `'2'` required; shown only when `Ticket::Frontend::AccountTime` is on, `AgentTicketProcess.pm:3067-3082`).
- **Transition config:** `{Condition: {'1': {Type: and|or|xor, Fields: {FieldName: {Type, Match}}}}, ConditionLinking: and|or|xor}`. The designer offers the types `Module, Regexp, String, Equal, NotEqual, Contains, NotContains, GreaterThan, GreaterThanOrEqual, LessThan, LessThanOrEqual` (`AdminProcessManagementTransition.pm:532-552`; linking and/or/xor at :518-524).
- **Module conditions:** `TransitionValidation/Module.pm:99-140`. The module path is in **`Match`** (`Require($Condition->{Match})`). An unknown module logs "Can't load …" and returns undef, so it **does not match and does not crash** (this answers the spec's open risk for Znuny). `ValidateDemo.pm` matches iff `Data->{Queue} eq 'Raw'`.
- **TransitionAction config:** `{Module: 'Kernel::System::ProcessManagement::TransitionAction::<Name>', Config: {…}}`. Parameters per module are documented in the POD `Config => {…}` block of each `Kernel/System/ProcessManagement/TransitionAction/<Name>.pm`. Every module's `Config.UserID` overrides the acting user (`Base.pm _OverrideUserID` 245-254).
- **Failing actions:** `Process.pm:553-586` runs every action and ignores `Run()`'s result. A failing action never stops the transition.
- **Appointment actions:** `AppointmentCreate.pm` / `AppointmentUpdate.pm` work as follows. `CalendarName` → `CalendarID`. `TeamID`, `ResourceID`, `RecurrenceFrequency` and `RecurrenceExclude` are comma-split. Date params go through `_ValidDateTimeConvert` (system time zone string `YYYY-MM-DD HH:MM:SS`). `DynamicField_AppointmentID` names a DF that receives the new id. `DynamicField_UserID` → `UserID`. Smart tags are replaced first. `AppointmentRemove.pm` needs `AppointmentID`. `Calendar/Appointment.pm:1548-1560` `AppointmentDelete` requires calendar permission `create` or `rw` for the acting user; `AppointmentCreate` checks no permission.
- **Dialog `Attachments` field** (agent only, existing ticket only, `AgentTicketProcess.pm:1878`, 4450-4515, 5513-5545, 6750-6810): a multi-select of the ticket's existing article attachments. Option key `ObjectType=Article;ObjectID=<ArticleID>;ID=<FileID>`, label `sprintf(Process::DynamicFieldProcessManagementAttachment::FormatString = '%3$s#%1$d: %2$s', ArticleNumber, Filename, 'Artikel', 'Anhang')`. The selection is stored comma-joined in the DF named by `Process::DynamicFieldProcessManagementAttachment` (default `ProcessManagementAttachment`, TextArea, shipped in Znuny's and Tiqora's initial insert). `ArticleSend.pm:163-166` / `TicketArticleCreate.pm:297-300` with `Config.AttachmentsReuse` truthy add those attachments (`Base.pm _GetAttachments` 145-243) and clear the DF unless `Process::DynamicFieldProcessManagementAttachment::Keep`. `Config.Attachments` (std attachment names) / `Config.AttachmentIDs` add standard attachments (`ArticleSend.pm:168-200`). `CustomerTicketProcess.pm` has no `Attachments` field.
- **Article uploads:** agent and customer dialogs upload files into the article (`AgentTicketProcess.pm:860-905`, `CustomerTicketProcess.pm:727-790`, 2224).
- **Export format:** `AdminProcessManagement.pm:215-245` + `_GetProcessData` 2063-2129 produce YAML `{Process: ProcessGet(...), Activities: {eid: ActivityGet}, ActivityDialogs: {eid: ActivityDialogGet}, Transitions: {eid: TransitionGet}, TransitionActions: {eid: TransitionActionGet}}`, filename `Export_ProcessEntityID_<eid>.yml`. `ProcessGet` adds `Activities`, `Transitions` and `TransitionActions` lists derived from `Path`.
- **Import:** `DB/Process.pm:1485-1860` `ProcessImport(Content, OverwriteExistingEntities)`. It refuses when DFs used in dialog `Fields` are missing ("The following dynamic fields are missing: … Import has been stopped."). It checks that every listed activity, dialog, transition and action is present. Without overwrite, every entity gets a new id from `EntityIDGenerate` and all references, including `Layout` keys (1891-1905), are remapped. With overwrite, an existing entity is updated in place. **It writes no `pm_entity_sync` rows.**
- **Delete rules:** `AdminProcessManagement.pm:1882-1918` `_CheckProcessDelete`: a process can be deleted only when its state is `Inactive`, and the delete writes sync `deleted`. `_CheckEntityUsage` (1920ff) refuses to delete an Activity used in any process `Path`, an ActivityDialog used by any activity, or a Transition/TransitionAction used in any process `Path`. `EntityDelete` writes sync `deleted`. `ProcessCopy` (716-800) creates `'<Name> (Kopie)'` (Znuny `'%s (copy)'`) with new ids for the process only and reuses the referenced entities.
- **Deploy:** `AdminProcessManagement.pm:1189-1225` `ProcessSync` runs `ProcessDump(ResultType => 'FILE', Location => <Home>/Kernel/Config/Files/ZZZProcessManagement.pm, UserID)` + `EntitySyncStatePurge`. `AdminACL.pm:354-370` runs `ACLDump(ResultType => 'FILE', Location => <Home>/Kernel/Config/Files/ZZZACL.pm)` + `ACLsNeedSyncReset`.
- **TiqoraSync today** (`packages/znuny-addon/TiqoraSync/Kernel/System/TiqoraSync.pm`, 1.1.1) is a daemon cron task running every minute. It reads `tiqora_cache_invalidation` rows above the `tiqora_settings` watermark `tiqorasync.watermark`, clears ticket caches, and runs `Cache->CleanUp(Type => $cache_type)` for each `cache_type`. An unknown type is harmless: `CleanUp` of a non-existent type finds no directory (`Cache/FileStorable.pm:185-198`).
- **Process path history:** Tiqora and Znuny both write `TicketDynamicFieldUpdate` with name `%%FieldName%%ProcessManagementActivityID%%Value%%<new>%%OldValue%%<old>` (`tiqora/znuny/history.py:520-530`).

### D3. Transition action registry (Task 1)

```python
# backend/src/tiqora/process/actions/registry.py
ActionHandler = Callable[[AsyncSession, dict[str, Any], int, int, SysConfig], Awaitable[None]]  # = engine._ActionHandler

ParamKind = Literal[
    "text", "textarea", "number", "bool", "datetime", "queue", "state", "priority", "type",
    "service", "sla", "user", "customer_user", "dynamic_field", "calendar", "lock",
    "link_type", "std_attachment", "ticket_ref",
]

@dataclass(frozen=True, slots=True)
class ActionParam:
    name: str                          # Znuny Config key, e.g. "State"
    kind: ParamKind
    required: bool = False
    one_of: tuple[str, ...] = ()       # alternative keys, e.g. ("State", "StateID"): exactly one required
    list_value: bool = False           # comma-separated list in Znuny (e.g. TeamID)

@dataclass(frozen=True, slots=True)
class TransitionActionModule:
    name: str                          # short name, e.g. "TicketStateSet"
    run: ActionHandler | None          # None = known to Znuny, not run by Tiqora (designer shows "nur in Znuny wirksam")
    params: tuple[ActionParam, ...] = ()
    tiqora_only: bool = False          # Kernel::System::ProcessManagement::TransitionAction::Tiqora*
    @property
    def znuny_module(self) -> str      # "Kernel::System::ProcessManagement::TransitionAction::" + name

TRANSITION_ACTIONS: ExtensionPoint[TransitionActionModule] = ExtensionPoint(
    key="core.process.transition_actions", item_key=lambda m: m.name,
    description="ProcessManagement transition action modules (handler + designer parameter specs)")

def core_transition_actions() -> list[TransitionActionModule]
    # the 20 implemented handlers (moved from engine._ACTION_HANDLERS) + Appointment* (Task 2)
    # + run=None entries for ExecuteInvoker and ConfigItemUpdate
def resolve_action(module: str) -> TransitionActionModule | None
    # short name via engine._module_short_name; get_registry().get(TRANSITION_ACTIONS, short)
```

`execute_transition_action` looks the module up with `resolve_action`. When the result is `None` or `run is None`, the module is logged and returned as unsupported, as today. `_ACTION_HANDLERS` is deleted; tests that used it switch to `resolve_action`. `register_core` adds `core_transition_actions()`. Parameter specs are copied from each module's POD `Config => {…}` block (D2). Every module also gets the implicit optional `UserID` (`user`) parameter, which the designer shows under "Weitere Einstellungen".

### D4. Appointment actions (Task 2)

```python
# backend/src/tiqora/process/actions/appointments.py
async def action_appointment_create(session, config, ticket_id, user_id, sysconfig) -> None
async def action_appointment_update(session, config, ticket_id, user_id, sysconfig) -> None
async def action_appointment_remove(session, config, ticket_id, user_id, sysconfig) -> None
```

Shared rules (D2):
- `UserID` comes from `Config.UserID` / `DynamicField_UserID`.
- The calendar is resolved from `CalendarID` or `CalendarName` (valid calendars only).
- Comma lists are split.
- Times are parsed as system-time-zone (`OTRSTimeZone`, default UTC) strings `YYYY-MM-DD HH:MM[:SS]` and stored as naive UTC.
- `Title`, `StartTime` and `EndTime` are required for create; create and update set `AllDay`, `Description`, `Location`, `TeamID` and `ResourceID`.
- Recurrence: `Recurring` with `RecurrenceType` in Tiqora's RRULE subset (`RECUR_TYPES`) plus `RecurrenceInterval/Count/Until`. `Custom*`, `RecurrenceFrequency`, `RecurrenceExclude` and all `Notification*` keys are logged and ignored (limitation).
- `DynamicField_AppointmentID` → `update_dynamic_field` with the new id as a string (history as Znuny, `UserID 1`).
- No ticket link is written (`CalendarService.link_ticket` is not called). Znuny's process action does not link; `calendar_appointment_ticket` is written only by ticket appointment rules. Assumption: parity over convenience.
- Permission: create runs without a permission check. Update and remove require `rw` on the calendar's group for the acting user, mirroring `CalendarPermissionGet` create/rw.
- Errors (missing keys, unknown calendar, no permission, unknown appointment) → `logger.warning` and return. Never raise (Znuny ignores failing actions, `Process.pm:576-586`).

`CalendarService` gains `create_appointment_unchecked(user_id, data)`. `update_appointment` / `delete_appointment` keep their permission check, and the handlers call them through a small `try/except HTTPException` that maps refusals to a warning. If `CalendarService` raises a non-HTTP error type, catch that instead (the implementer checks `_require_group_permission`).

### D5. Condition modules (Task 3)

```python
# backend/src/tiqora/process/condition_modules.py
ConditionModuleFn = Callable[[Mapping[str, ConditionValue], str], bool]   # (condition data, field name) -> match

@dataclass(frozen=True, slots=True)
class ConditionModule:
    match: str                     # the Perl module path exactly as in Match, e.g.
                                   # "Kernel::System::ProcessManagement::TransitionValidation::ValidateDemo"
    validate: ConditionModuleFn
    tiqora_only: bool = False      # "Tiqora::…" names

CONDITION_MODULES: ExtensionPoint[ConditionModule] = ExtensionPoint(
    key="core.process.condition_modules", item_key=lambda m: m.match, description=...)
def core_condition_modules() -> list[ConditionModule]          # [ValidateDemo]
def resolve_condition_module(match: str) -> ConditionModule | None
    # accepts a trailing ".pm" (Znuny's POD example) and leading/trailing whitespace
```

In `conditions.py`, `Type: Module` resolves `cond.match`. A known module returns its result. An unknown one logs `transition condition module not available: <match>` (once per process run) and does not match.

### D6. Dialog form kinds (Tasks 4–6)

`process/schemas.py` changes:

```python
FieldKind = Literal["text", "textarea", "select", "multiselect", "boolean", "date", "datetime",
                    "article", "pending_time", "customer", "ticket_attachments", "unsupported"]

class ProcessDialogFieldOut(BaseModel):          # + three fields
    ...
    time_units: Literal["0", "1", "2"] = "0"     # kind "article" only; "0" also when AccountTime is off
    uploads: bool = False                        # kind "article": the dialog may carry file uploads
    refresh_on: list[str] = Field(default_factory=list)
        # canonical names of fields whose change must re-ask this field's options (D7)
```

- **`customer`** (agent): the form keeps wave 1's two keys, `CustomerUserID` (the login, now rendered with the new `customer` field kind: search plus picker) and `CustomerID` (plain text def). The options stay empty, and the frontend searches `GET /reference/customers`. When `CustomerID` is submitted empty, the server fills it with the picked customer user's `UserCustomerID` (Znuny's agent screens do the same through AJAX). No new value shape: `FieldValue` (wave-0 core type) stays unchanged. Portal: never offered (`CUSTOMER_SKIP_FIELDS` + forced values, unchanged).
- **`article`:** field values gain `TimeUnits` (str/number → `normalize_time_unit`; booked with the article when > 0 via the existing time-accounting write used by `PATCH /tickets`) and `ArticleAttachments: list[ArticleAttachmentIn]` (base64, decoded with `decode_attachments`, 413/422 like the reply dialog; max 20 files). Attachments go into `ArticleIn.attachments`. Uploads are only allowed when the article itself is stored (Subject and Body set); otherwise they are ignored with a warning, as in Znuny where files belong to the article. Required rules: `TimeUnits` is required when `time_units == "2"`. `missing_required_fields` reports it as `TimeUnits`.
- **`ticket_attachments`** (agent, existing ticket only; Znuny `Attachments`): options from `list_ticket_attachment_options(session, ticket_id)` (D2 key and label format, `FormatString` from sysconfig, labels with German `Artikel`/`Anhang`). The value is a list of option keys; it is stored comma-joined into the DF named by `Process::DynamicFieldProcessManagementAttachment`. Keys that are not in the ticket's current list are dropped (forged keys). On a start form without a ticket, and for customers, the field is not rendered.
- `ArticleSend` / `TicketArticleCreate` handlers: `AttachmentsReuse` truthy → `collect_reused_attachments(session, ticket_id, sysconfig, clear=not Keep)` returns `(filename, content_type, bytes)` from article attachments. `Attachments` / `AttachmentIDs` → valid `standard_attachment` rows (name lookup, then id). Invalid rows are skipped with a warning (`ArticleSend.pm:168-200`).

```python
# backend/src/tiqora/process/attachments_field.py
ATTACHMENTS_FIELD = "Attachments"
async def list_ticket_attachment_options(session: AsyncSession, ticket_id: int,
                                         sysconfig: SysConfig) -> list[ProcessDialogOptionOut]
def encode_attachment_selection(keys: Sequence[str]) -> str            # ",".join
def decode_attachment_selection(value: str | None) -> list[dict[str, str]]  # [{"ObjectType","ObjectID","ID"}]
async def collect_reused_attachments(session: AsyncSession, *, ticket_id: int, sysconfig: SysConfig,
                                     user_id: int) -> list[tuple[str, str, bytes]]
    # reads the DF; clears it (update_dynamic_field "" with user 1) unless ::Keep is truthy
async def collect_std_attachments(session: AsyncSession, config: Mapping[str, Any]) -> list[tuple[str, str, bytes]]
```

### D7. Option refresh (Task 6)

Znuny re-renders dependent fields over AJAX: `AJAXUpdate` with the current form values. Tiqora asks the server again with the current values:

```python
# backend/src/tiqora/process/form_options.py
class ProcessFormOptionsIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)       # same shape as field_values
    fields: list[str] = Field(default_factory=list, max_length=50)  # canonical names to refresh; [] = all select/multiselect
class ProcessFormOptionsOut(BaseModel):
    options: dict[str, list[ProcessDialogOptionOut]]

async def refresh_dialog_options(session, *, actor: ProcessActor, user: AuthenticatedUser | None,
                                 graph: ProcessGraph, dialog: ActivityDialogNode, ticket_id: int | None,
                                 values: Mapping[str, Any], fields: Sequence[str]) -> dict[str, list[ProcessDialogOptionOut]]
    # same option sources as build_dialog_form (agent: collect_field_options with FieldOptionsContext
    # filled from values: queue_id, state_id, priority_id, type_id, service_id, sla_id,
    # customer_user_id, dynamic_fields; customer: collect_customer_field_options with the values)
```

`refresh_on` per field (set by `build_dialog_form`):
- `Owner` / `Responsible` ← `Queue`.
- `SLA` ← `Service`.
- `Service` ← `CustomerUserID` (agent).
- `State`, `Priority`, `Type`, `Queue` and every DF select ← every other visible select/multiselect/customer field. This is because any value can appear in ACL `Properties` and is conservative; Znuny's `AJAXUpdatableFields` = all.

Routes:

```
POST /api/v1/process/{process_entity_id}/start-form/options?ticket_id=        -> ProcessFormOptionsOut
POST /api/v1/process/ticket/{ticket_id}/dialog/{activity_dialog_entity_id}/form/options -> ProcessFormOptionsOut
POST /api/portal/process/{process_entity_id}/start-form/options?ticket_id=
POST /api/portal/process/ticket/{ticket_id}/dialog/{activity_dialog_entity_id}/form/options
```

Each route runs the same availability checks as its `GET …form` sibling (`assert_can_start` / `assert_can_submit`).

### D8. Path so far (Task 7)

```python
# backend/src/tiqora/process/path.py
class ProcessPathStepOut(BaseModel):
    activity_entity_id: str
    activity_name: str            # "" when the activity no longer exists in the active source
    entered_at: UtcDateTime       # ticket_history.create_time (naive UTC → UtcDateTime)
    entered_by: int               # ticket_history.create_by
    entered_by_name: str          # users full name, "" when unknown
    current: bool
class ProcessPathOut(BaseModel):
    process_entity_id: str | None
    steps: list[ProcessPathStepOut]    # oldest first; max 200

async def load_process_path(session: AsyncSession, ticket_id: int) -> ProcessPathOut
    # history_type TicketDynamicFieldUpdate rows of the ticket whose name starts with
    # "%%FieldName%%ProcessManagementActivityID%%Value%%" → parse the value; only rows after the newest
    # "%%FieldName%%ProcessManagementProcessID%%Value%%<current process>" row; names from the active
    # source (one load per distinct activity id); user names via an integer IN-list on users.id
GET /api/v1/process/ticket/{ticket_id}/path -> ProcessPathOut     # ro access check like …/state
```

### D9. Ids, YAML, versions (Task 10)

```python
# backend/src/tiqora/process/designer/ids.py
EntityType = Literal["Process", "Activity", "ActivityDialog", "Transition", "TransitionAction"]
ENTITY_TYPES: tuple[EntityType, ...]
SUB_ENTITY_TYPES: tuple[EntityType, ...]   # without "Process"
ENTITY_ID_RE = re.compile(r"\A(Process|Activity|ActivityDialog|Transition|TransitionAction)-[0-9a-zA-Z]{1,40}\Z")
def generate_entity_id(entity_type: EntityType) -> str      # f"{entity_type}-{secrets.token_hex(16)}"

# backend/src/tiqora/process/designer/yaml_codec.py
def stringify_scalars(data: Any) -> Any
    # deep copy: int/float/bool → str (True→"1", False→"0"), None → "", keeps dict/list structure
def dump_znuny_yaml(data: Any) -> str
    # yaml.safe_dump(stringify_scalars(data), default_flow_style=False, allow_unicode=True,
    #                explicit_start=True, sort_keys=True)   # Perl YAML::XS output starts with "---"
def load_znuny_yaml(raw: str | bytes | None) -> Any         # safe_load; "" / None → {}

# backend/src/tiqora/process/designer/versioning.py
def entity_version(*, name: str, config: str, layout: str = "", state_entity_id: str = "") -> str
    # sha256 over the stored row text, first 16 hex; the client sends it back as expected_version
```

Assumption: config values are stored as strings, as Znuny's web forms submit them (`Display: '2'`, `left: '120'`). Tiqora's readers already accept both strings and numbers.

### D10. The writer (Task 11)

```python
# backend/src/tiqora/process/designer/writer.py
class EntityNotFound(LookupError): ...
class VersionConflict(Exception): ...             # → 409 "Der Eintrag wurde inzwischen geändert. Bitte neu laden."
class EntityInUse(Exception):                     # → 409 "Wird noch verwendet von: …"
    used_by: list[str]                            # entity ids of the users
class ProcessNotInactive(Exception): ...          # → 409 "Nur abgeschaltete Prozesse können gelöscht werden."
class InvalidReference(ValueError): ...           # → 422 (unknown entity id referenced, malformed id)

@dataclass(frozen=True, slots=True)
class WrittenEntity:
    entity_type: EntityType
    entity_id: str
    id: int
    name: str
    version: str

class ProcessWriter:
    def __init__(self, session: AsyncSession, *, user_id: int) -> None
    async def create_process(self, *, name: str, description: str = "",
                             state_entity_id: str = "S2") -> WrittenEntity
        # new processes start Inactive (Assumption; Znuny's form default is Active, but Inactive
        # is safe: an undeployed draft is never startable anyway, and a deploy then needs a conscious switch)
    async def update_process(self, entity_id: str, *, expected_version: str, name: str,
                             description: str, state_entity_id: str, start_activity: str,
                             start_activity_dialog: str, path: Mapping[str, Mapping[str, Mapping[str, Any]]],
                             layout: Mapping[str, Mapping[str, Any]]) -> WrittenEntity
        # validates: state in S1/S2/S3; every Path key / target / action id exists (InvalidReference);
        # start activity in Path (or ""); start dialog belongs to start activity (or "")
    async def copy_process(self, entity_id: str) -> WrittenEntity      # "<Name> (Kopie)", entities reused
    async def delete_process(self, entity_id: str) -> None             # S2 only; sync 'deleted'
    async def create_entity(self, entity_type: SubEntityType, *, name: str,
                            config: Mapping[str, Any]) -> WrittenEntity
    async def update_entity(self, entity_type: SubEntityType, entity_id: str, *, expected_version: str,
                            name: str, config: Mapping[str, Any]) -> WrittenEntity
    async def delete_entity(self, entity_type: SubEntityType, entity_id: str) -> None  # EntityInUse
    async def entity_usage(self, entity_type: EntityType, entity_id: str) -> list[str]
    # internal: _mark_sync(entity_type, entity_id, state) (Entity.pm:126-200 select-then-insert/update,
    # text() SQL, current_timestamp); _signal(entity_type) → invalidate_cache_type("ProcessManagement_<T>")
```

Every write sets `change_time = current_timestamp` and `change_by = user_id` (create also sets `create_*`). It flushes only; the caller commits. Config is normalised per type before it is stored:
- Activity: `ActivityDialog` keys renumbered "1".."n".
- ActivityDialog: `FieldOrder` restricted to the `Fields` keys plus missing keys appended; `Interface` non-empty list of the two names.
- Transition: condition keys renumbered; `ConditionLinking` default `and`.
- TransitionAction: `Module` must be a `Kernel::System::ProcessManagement::TransitionAction::<Name>` path or any `::`-path (unknown modules are allowed, as in Znuny).

`update_*` compares `entity_version` of the stored row with `expected_version`; a mismatch raises `VersionConflict` before anything is written. A no-op update (same name and config) writes nothing, not even a sync row. This mirrors wave-1 ruling F30 for ACLs.

### D11. Draft reader, catalogue, admin API (Tasks 12–13)

```python
# backend/src/tiqora/process/designer/schemas.py
SyncState = Literal["synced", "not_sync", "deleted"]
class DesignerEntityOut(BaseModel):
    entity_type: EntityType
    entity_id: str
    id: int
    name: str
    config: dict[str, Any]            # parsed YAML, as stored
    version: str
    sync_state: SyncState = "synced"
    changed_at: UtcDateTime
    changed_by: int
class ProcessDraftOut(BaseModel):
    process: DesignerEntityOut        # config = {Description, Path, StartActivity, StartActivityDialog}
    state_entity_id: str              # S1/S2/S3
    state_name: str                   # Active | Inactive | FadeAway
    layout: dict[str, dict[str, str]]
    activities: list[DesignerEntityOut]           # Path keys, in Path order of first appearance
    activity_dialogs: list[DesignerEntityOut]     # referenced by those activities
    transitions: list[DesignerEntityOut]          # referenced in Path
    transition_actions: list[DesignerEntityOut]   # referenced in Path
    missing_references: list[str]                 # ids referenced but not found (shown as broken)
    pending_changes: int                          # COUNT(pm_entity_sync) — global, like Znuny's banner
class EntitySummaryOut(BaseModel):
    entity_type: EntityType
    entity_id: str
    name: str
    used_by: list[str]                # process ids (Activity/Transition/TransitionAction) or activity ids (dialogs)
    sync_state: SyncState
class ProcessCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=10000)
class ProcessUpdateIn(BaseModel):
    expected_version: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=10000)
    state_entity_id: Literal["S1", "S2", "S3"]
    start_activity: str = ""
    start_activity_dialog: str = ""
    path: dict[str, dict[str, dict[str, Any]]]
    layout: dict[str, dict[str, str | int]]
class EntityCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any]
class EntityUpdateIn(EntityCreateIn):
    expected_version: str

# catalogue
class CatalogParamOut(BaseModel):
    name: str
    kind: str                          # ParamKind
    required: bool
    one_of: list[str]
    list_value: bool
class CatalogActionOut(BaseModel):
    name: str
    module: str
    supported: bool                    # run is not None
    tiqora_only: bool
    params: list[CatalogParamOut]
class CatalogFieldOut(BaseModel):
    name: str                          # canonical dialog field / condition field name
    label: str
    kind: str                          # ticket attribute kind or DF type
    dialog: bool                       # offered as dialog field
    condition: bool                    # offered as condition field
    options: list[ProcessDialogOptionOut] = Field(default_factory=list)   # DF PossibleValues, states, queues…
class DesignerCatalogOut(BaseModel):
    actions: list[CatalogActionOut]
    condition_types: list[str]         # D2 list
    condition_modules: list[str]       # registered Match strings
    fields: list[CatalogFieldOut]
    interfaces: list[str]              # ["AgentInterface", "CustomerInterface"]
    permissions: list[str]             # Znuny permission keys ("" = none, "ro", "rw", …)
    account_time: bool                 # Ticket::Frontend::AccountTime

async def load_process_draft(session: AsyncSession, process_entity_id: str) -> ProcessDraftOut   # LiveProcessSource rows
async def list_entities(session: AsyncSession, entity_type: EntityType) -> list[EntitySummaryOut]
async def load_sync_states(session: AsyncSession) -> dict[tuple[str, str], str]
async def build_designer_catalog(session: AsyncSession, sysconfig: SysConfig) -> DesignerCatalogOut
```

Routes (`api/v1/admin/process_designer.py`, `APIRouter(prefix="/process-designer", tags=["admin:process-designer"])`, every route `AdminUser`):

```
GET    /catalog                                           -> DesignerCatalogOut
GET    /entities/{entity_type}                            -> list[EntitySummaryOut]
POST   /processes                         ProcessCreateIn -> 201 ProcessDraftOut
GET    /processes/{process_entity_id}                     -> ProcessDraftOut
PUT    /processes/{process_entity_id}     ProcessUpdateIn -> ProcessDraftOut      (409 VersionConflict, 422 InvalidReference)
POST   /processes/{process_entity_id}/copy                -> 201 ProcessDraftOut
DELETE /processes/{process_entity_id}                     -> 204                  (409 ProcessNotInactive)
POST   /entities/{entity_type}            EntityCreateIn  -> 201 DesignerEntityOut   (entity_type ≠ Process)
PUT    /entities/{entity_type}/{entity_id} EntityUpdateIn -> DesignerEntityOut      (409)
DELETE /entities/{entity_type}/{entity_id}                -> 204                  (409 EntityInUse, detail lists names)
GET    /processes/{process_entity_id}/validation          -> list[ValidationIssueOut]       (Task 14)
GET    /processes/{process_entity_id}/export              -> text/yaml attachment            (Task 15)
POST   /import                            ProcessImportIn -> ProcessImportOut               (Task 15)
GET    /revisions?kind=process&limit=50                   -> list[RevisionOut]              (Task 16)
GET    /revisions/{revision_id}/diff?against=&process=    -> RevisionDiffOut                (Task 16)
POST   /revisions/{revision_id}/restore   RestoreIn       -> ProcessDraftOut                (Task 16)
```

The path parameter `entity_type` is `Literal["Activity", "ActivityDialog", "Transition", "TransitionAction"]`. The agent and portal process APIs stay revision-based and unchanged; `ProcessDetailOut` keeps hiding transitions. Only this admin API exposes the full configuration.

### D12. Validation (Task 14)

```python
# backend/src/tiqora/process/designer/validation.py
Severity = Literal["error", "warning"]
@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str                 # stable, e.g. "start_activity_missing"
    severity: Severity
    entity_type: EntityType
    entity_id: str
    field: str = ""           # e.g. "Fields.DynamicField_X", "Condition.1.Fields.Queue"
    params: Mapping[str, str] = field(default_factory=dict)   # for the German message
class ValidationIssueOut(BaseModel): code; severity; entity_type; entity_id; field; params: dict[str, str]
@dataclass(frozen=True, slots=True)
class ReferenceData:
    queues: frozenset[str]; states: frozenset[str]; priorities: frozenset[str]; types: frozenset[str]
    services: frozenset[str]; slas: frozenset[str]; users: frozenset[str]   # names/logins (valid)
    dynamic_fields: Mapping[str, DfInfo]                                    # name → type + possible values
    calendars: frozenset[str]
def validate_process(draft: ProcessDraftOut, refs: ReferenceData, *,
                     parallel_operation: bool) -> list[ValidationIssue]     # pure
async def load_reference_data(session: AsyncSession) -> ReferenceData
```

Codes (errors):
- `start_activity_missing`
- `start_dialog_missing`
- `start_dialog_not_in_start_activity`
- `reference_missing` (Path, activity or transition ids that do not exist)
- `dialog_no_interface`
- `transition_target_missing`
- `condition_value_impossible` (Dropdown/Multiselect DF with `String`/`Equal` match not in PossibleValues; Queue/State/Priority/Type/Service/SLA `String`/`Equal` match not a valid name; `Regexp` that does not compile)
- `action_reference_missing` (Queue/State/Priority/Type/Service/SLA/Owner/Responsible/DF/Calendar config values that are not valid names or ids and contain no `<OTRS_` smart tag)
- `action_required_param_missing` (registry params)

Codes (warnings):
- `activity_unreachable` (not reachable from the start activity)
- `activity_dead_end` (has dialogs but no outgoing transition)
- `dialog_no_fields`
- `dialog_field_unknown` (DF not existing/invalid)
- `queue_required_display` (Queue/State/Lock/Priority with `Display: 1`, a Znuny config error per wave-1 D1)
- `condition_module_unknown`
- `action_module_unsupported` (`run is None` / not registered: "nur in Znuny wirksam")
- `tiqora_only_module_parallel` (only when `parallel_operation`)
- `condition_type_unknown`
- `transition_without_condition` (`Condition` empty: always matches, Znuny behaviour; warning only)

### D13. Import / export (Task 15)

```python
# backend/src/tiqora/process/designer/exchange.py
class ProcessImportIn(BaseModel):
    content: str = Field(max_length=5_000_000)
    overwrite_existing: bool = False
class ProcessImportOut(BaseModel):
    process_entity_id: str
    name: str
    created: dict[str, int]       # per entity type
    updated: dict[str, int]
class ImportRefused(ValueError): ...      # → 422 with Znuny's message text in German

async def export_process_yaml(session: AsyncSession, process_entity_id: str) -> tuple[str, str]
    # (filename "Export_ProcessEntityID_<eid>.yml", YAML text) in _GetProcessData shape (D2), via dump_znuny_yaml
async def import_process_yaml(session: AsyncSession, writer: ProcessWriter, content: str, *,
                              overwrite_existing: bool) -> ProcessImportOut
```

Import order follows `DB/Process.pm:1485-1860`: parse, then the missing-DF check, then the presence checks, then id remapping (new ids unless overwrite), then dialogs, activities, transitions, actions and finally the process (with the remapped `Path`, `StartActivity`, `StartActivityDialog` and `Layout` keys). Each entity goes through `ProcessWriter` (Deviation 2: sync rows are written). The whole import runs in one transaction; any error rolls back.

### D14. Revision diff and restore (Task 16)

```python
# backend/src/tiqora/process/designer/revisions.py
ChangeKind = Literal["added", "removed", "changed"]
class EntityChangeOut(BaseModel):
    entity_type: str                      # dump kind: Process/Activity/ActivityDialog/Transition/TransitionAction
    entity_id: str
    name: str
    change: ChangeKind
    changed_keys: list[str]               # top-level keys of the entity dict that differ (changed only)
class RevisionDiffOut(BaseModel):
    from_revision: int                    # `against`; 0 = empty
    to_revision: int
    process_entity_id: str | None
    changes: list[EntityChangeOut]
    yaml_before: str                      # dump_znuny_yaml of the selected entities (for a text diff)
    yaml_after: str
class RestoreIn(BaseModel):
    process_entity_id: str

def diff_revisions(old_dump: Mapping[str, Any], new_dump: Mapping[str, Any], *,
                   process_entity_id: str | None) -> RevisionDiffOut
    # ignores ID/CreateTime/ChangeTime; process filter = the process + entities referenced by it in either dump
async def restore_process_from_revision(session: AsyncSession, writer: ProcessWriter, *,
                                        revision_id: int, process_entity_id: str) -> ProcessDraftOut
    # upserts (same entity ids) the process + every entity it references in that revision; never deletes;
    # update path uses the current row's version (no expected_version: restore is an explicit overwrite)
```

`against` defaults to the previous revision of the same kind. Revision JSON comes from `tiqora_process_revision` through `load_active_revision`'s loader (one `deferred` config column read). The RevisionOut list reuses `api/v1/admin/deploy_status.RevisionOut`.

### D15. Deploy via TiqoraSync (Tasks 17–18)

Settings keys in `tiqora_settings`, written by the add-on and read by Tiqora:

```
tiqorasync.heartbeat      {"version": "1.2.0", "framework": "6.5.22", "capabilities": ["process_deploy", "acl_deploy"],
                           "at": 1791700000}                      # every Run(); epoch seconds (DateTime->ToEpoch)
tiqorasync.deploy.process {"revision_id": 12, "status": "ok"|"error", "message": "", "at": 1791700060}
tiqorasync.deploy.acl     same shape
```

`at` is integer epoch seconds, never a formatted time: Znuny's formatted times are in `OTRSTimeZone`, which is not guaranteed to be UTC, and the 10-minute heartbeat gate depends on it. Tiqora compares against `time.time()`.

Signal: one `tiqora_cache_invalidation` row with `cache_type = "TiqoraDeploy_process_<revision_id>"` or `"TiqoraDeploy_acl_<revision_id>"`. The name matches `[A-Za-z0-9_]` and is ≤ 100 characters. TiqoraSync 1.1.1 runs `CleanUp(Type => …)` on it, which is harmless.

```python
# backend/src/tiqora/deployed/znuny_sync.py
HEARTBEAT_KEY = "tiqorasync.heartbeat"
DEPLOY_STATUS_KEY = {"process": "tiqorasync.deploy.process", "acl": "tiqorasync.deploy.acl"}
HEARTBEAT_MAX_AGE = timedelta(minutes=10)
PENDING_WARN_AFTER = timedelta(minutes=5)
DeployMode = Literal["local", "signal"]
ZnunySyncState = Literal["not_needed", "pending", "ok", "error", "addon_missing"]

@dataclass(frozen=True, slots=True)
class AddonInfo:
    version: str
    capabilities: frozenset[str]
    seen_at: datetime              # naive UTC, from datetime.fromtimestamp(at, UTC)
async def read_addon_info(session: AsyncSession) -> AddonInfo | None
async def addon_can_deploy(session: AsyncSession, kind: RevisionKind, *, now: datetime) -> bool
    # heartbeat ≤ HEARTBEAT_MAX_AGE and f"{kind}_deploy" in capabilities
async def queue_deploy_signal(session: AsyncSession, kind: RevisionKind, revision_id: int) -> None
    # invalidate_cache_type(f"TiqoraDeploy_{kind}_{revision_id}")
class ZnunySyncOut(BaseModel):
    state: ZnunySyncState
    revision_id: int | None
    message: str = ""
    since: UtcDateTime | None = None
    overdue: bool = False          # pending longer than PENDING_WARN_AFTER
async def read_znuny_sync_status(session: AsyncSession, kind: RevisionKind, *,
                                 newest: RevisionInfo | None, mode: DeployMode | None,
                                 now: datetime) -> ZnunySyncOut
    # not_needed: newest is None, newest.source != "tiqora", or mode == "local" (ownership/setting);
    # ok/error: status key revision_id >= newest.id (status of an older id → pending);
    # addon_missing: newest.source == "tiqora", mode == "signal" and no fresh heartbeat
```

`deployed/gate.py`:

```python
async def deploy_mode(session: AsyncSession, settings: Settings, kind: RevisionKind) -> DeployMode | None
    # "local" when ownership active OR process.deploy_from_tiqora (today's gate);
    # else "signal" when addon_can_deploy; else None
async def can_deploy_from_tiqora(session, settings) -> bool     # kept: deploy_mode(..., "process") is not None
```

`deploy_from_tiqora(session, kind, user_id, *, mode: DeployMode)`:
- `local`: unchanged (record + `clear_sync_table`).
- `signal`: under `revision_write_lock`, build the dump. If the checksum differs from the newest revision, `record_revision(source="tiqora")`. Then `queue_deploy_signal(kind, newest_id)`, also when unchanged, because Znuny may still have drafts while Tiqora's content is equal. It **never** clears the sync tables.

`DeployOut` gains `mode: DeployMode`. `POST /admin/deploy` returns 409 with a new German detail when `deploy_mode` is `None` ("Bereitstellen aus Tiqora braucht das Znuny-Add-on TiqoraSync ab Version 1.2.0 (zuletzt gesehen: …) oder die Einstellung „Prozesse und ACLs in Tiqora bereitstellen (Znuny läuft nicht mehr)“."). `DeployKindStatusOut` gains `znuny_sync: ZnunySyncOut` and `deploy_mode: DeployMode | None`. `DeployStatusOut.can_deploy_from_tiqora` stays (process kind).

Watcher (no change in rules; pinned by tests):
- After TiqoraSync's purge: sync empty and checksum equal to the newest revision → `unchanged`.
- An edit between Tiqora's revision and the purge: TiqoraSync dumps it and purges. The watcher sees a checksum different from the newest → `confirming` → `znuny` revision.

TiqoraSync 1.2.0 (`Run()`):
1. Write the heartbeat first (an upsert on `tiqora_settings` like `_WatermarkSet`, JSON via `Kernel::System::JSON`).
2. While reading the batch, collect `TiqoraDeploy_<kind>_<id>` rows into `%Deploy{kind} = max(id)` and do **not** pass them to `CleanUp`.
3. After the cache work, for each kind (process first):
   - process: `ProcessDump(ResultType => 'FILE', Location => $Home.'/Kernel/Config/Files/ZZZProcessManagement.pm', UserID => 1)` and then `EntitySyncStatePurge(UserID => 1)`;
   - acl: `ACLDump(ResultType => 'FILE', Location => $Home.'/Kernel/Config/Files/ZZZACL.pm', UserID => 1)` and then `ACLsNeedSyncReset()`.
   Each kind runs inside `eval`, and the status key is written as `ok` or `error` with the message.
4. Advance the watermark as today.

Framework differences across 6.0–7.3 are caught by `eval`, reported in the status key, and verified on the golden peers (Task 28). The `.sopm` file goes to 1.2.0 with a ChangeLog entry and a description paragraph (de/en).

### D16. Frontend contracts

```ts
// frontend/src/components/fields/types.ts
export type FieldKind = … | "customer" | "ticket_attachments";
// FieldValue is NOT widened (wave-0 core type, shared with the dashboards-2 plan):
// "customer" values are the customer user login (string); "ticket_attachments" values are string[].

// frontend/src/components/process/useProcessFormOptions.ts
export function useProcessFormOptions(
  target: { scope: "agent" | "portal"; kind: "start"; processEntityId: string; ticketId?: number }
        | { scope: "agent" | "portal"; kind: "dialog"; ticketId: number; dialogEntityId: string },
  form: ProcessDialogFormData, values: FieldValues, opts?: { debounceMs?: number },
): Record<string, FieldOption[]>;   // only fields whose refresh_on sources changed since open; empty map until then

// frontend/src/lib/processDesignerApi.ts
export type ProcessDraft = Schemas["ProcessDraftOut"]; export type DesignerEntity = Schemas["DesignerEntityOut"];
export type DesignerCatalog = Schemas["DesignerCatalogOut"]; export type ValidationIssue = Schemas["ValidationIssueOut"];
export type RevisionDiff = Schemas["RevisionDiffOut"]; export type EntityType = DesignerEntity["entity_type"];
export const processDesignerApi = {
  catalog(signal?), entities(type, signal?), process(eid, signal?), createProcess(body), updateProcess(eid, body),
  copyProcess(eid), deleteProcess(eid), createEntity(type, body), updateEntity(type, eid, body), deleteEntity(type, eid),
  validation(eid, signal?), exportUrl(eid): string, importProcess(body), revisions(kind, signal?),
  diff(revisionId, opts: { against?: number; process?: string }, signal?), restore(revisionId, body),
};

// frontend/src/components/process-designer/canvasModel.ts  (pure, no React)
export type CanvasNode = { id: string /* activity eid */; position: { x: number; y: number }; data:
  { name: string; dialogs: { id: string; name: string }[]; isStart: boolean; isEnd: boolean; broken: boolean; issues: number } };
export type CanvasEdge = { id: string /* `${from}|${transitionEid}` */; source: string; target: string; data:
  { transitionId: string; name: string; actions: { id: string; name: string }[]; issues: number } };
export type ProcessShape = { path: ProcessUpdateIn["path"]; layout: ProcessUpdateIn["layout"];
  startActivity: string; startActivityDialog: string };
export function toCanvas(draft: ProcessDraft, issues?: ValidationIssue[]): { nodes: CanvasNode[]; edges: CanvasEdge[] };
export function shapeOf(draft: ProcessDraft): ProcessShape;
export function addActivity(shape: ProcessShape, activityId: string, at: { x: number; y: number }): ProcessShape;
export function removeActivity(shape: ProcessShape, activityId: string): ProcessShape;   // drops its outgoing and incoming edges
export function connect(shape: ProcessShape, from: string, transitionId: string, to: string): ProcessShape;
export function retarget(shape: ProcessShape, from: string, transitionId: string, to: string): ProcessShape;
export function disconnect(shape: ProcessShape, from: string, transitionId: string): ProcessShape;
export function setActions(shape: ProcessShape, from: string, transitionId: string, actionIds: string[]): ProcessShape;
export function move(shape: ProcessShape, activityId: string, at: { x: number; y: number }): ProcessShape; // ints → strings
export function setStart(shape: ProcessShape, activityId: string, dialogId: string): ProcessShape;
export function autoLayout(shape: ProcessShape): ProcessShape;   // only for activities without a layout entry: BFS columns from start, 260×140 grid
export function isDirty(a: ProcessShape, b: ProcessShape): boolean;
```

Designer page layout (desktop ≥ 1024 px):
- Top bar: name, state badge, `DeployBar`, "Prüfen", "Exportieren", "Fassungen".
- Left: `EntityPalette` (search; "Neuer Schritt", existing activities to drag in).
- Centre: the canvas.
- Right: the panel of the selected item (process when nothing is selected).

Canvas edits (layout, path, start) are kept locally and saved with "Speichern" (`PUT /processes/{eid}`), matching Znuny's canvas "Speichern". Panel edits of an entity save that entity directly (Znuny popups), and `Escape` discards. On a phone (< 768 px) the page shows a read-only list view (steps → forms → transitions) with the panels as full-screen sheets; there is no canvas editing on phones (Assumption). A 409 on save shows "Inzwischen geändert – neu laden" and keeps the local edits until the user reloads.

### D17. i18n keys (German first; English in `en.json`)

- `process.field.{TimeUnits,ArticleAttachments,Attachments}`: "Zeitaufwand", "Dateien", "Anhänge aus dem Ticket".
- `process.form.{uploadTooLarge,customerSearch,customerPick,attachmentsNone,optionsRefreshing}`: "Die Dateien sind zu groß.", "Kunden suchen", "Kunde wählen", "Das Ticket hat keine Anhänge.", "Auswahl wird aktualisiert …".
- `process.path.{title,entered,deleted,empty}`: "Bisheriger Weg", "{{date}} · {{name}}", "(gelöscht)", "Noch kein Schritt erfasst."
- `admin.processDesigner.*`:
  - `title`, `save` "Speichern", `discard` "Verwerfen", `conflict` "Inzwischen geändert – bitte neu laden.", `newStep` "Neuer Schritt", `newDialog` "Neues Formular", `newTransition` "Neuer Übergang", `newAction` "Neue Aktion";
  - `reuse` "Vorhandene verwenden", `start` "Start", `end` "Ende", `delete` "Löschen", `inUse` "Wird noch verwendet von: {{names}}", `onlyInactiveDelete` "Nur abgeschaltete Prozesse können gelöscht werden.";
  - `copySuffix` "(Kopie)", `phoneReadOnly` "Bearbeiten geht nur auf einem größeren Bildschirm.";
  - `panel.{process,activity,dialog,transition,action}` "Prozess", "Schritt", "Formular", "Übergang", "Aktion";
  - `dialog.{display.0,display.1,display.2,interface.agent,interface.customer,permission,requiredLock,submitButton,submitAdvice,descriptionShort,descriptionLong,defaultValue,fieldOrder}` "Ausgeblendet", "Sichtbar", "Pflichtfeld", "Agenten", "Kunden", "Berechtigung", "Ticket muss gesperrt sein", "Text der Schaltfläche", "Hinweis zum Absenden", "Kurzbeschreibung", "Ausführliche Beschreibung", "Vorgabewert", "Reihenfolge";
  - `condition.{group,addGroup,addField,linking.and,linking.or,linking.xor,type.<each of D2>}` "Bedingungsgruppe", "Gruppe hinzufügen", "Feld hinzufügen", "alle", "mindestens eine", "genau eine", type labels "Text gleich" (String), "Regulärer Ausdruck", "Prüfmodul", "gleich", "ungleich", "enthält", "enthält nicht", "größer als", "größer oder gleich", "kleiner als", "kleiner oder gleich";
  - `action.{module,unsupported,tiqoraOnly,rawParams,moreSettings,userOverride}` "Art der Aktion", "nur in Znuny wirksam", "nur in Tiqora wirksam", "Weitere Parameter", "Weitere Einstellungen", "Ausführen als";
  - `validation.{title,run,none,errors,warnings,<code>}` with one German sentence per D12 code;
  - `revisions.{title,compare,restore,restoreConfirm,restored,yamlView}` "Fassungen", "Vergleichen", "Wiederherstellen", "Diese Fassung als Entwurf übernehmen? Danach bitte bereitstellen.", "Als Entwurf übernommen.", "Dateiansicht";
  - `import.{title,file,overwrite,done,refused}` "Znuny-Prozessdatei importieren", "Datei (.yml)", "Vorhandene Einträge überschreiben", "Prozess „{{name}}“ importiert – als Entwurf.", "Import abgelehnt: {{reason}}";
  - `deploy.{button,confirmErrors,waitingZnuny,znunyOk,znunyError,addonMissing,overdue,localMode}` "Bereitstellen", "Die Prüfung hat {{count}} Fehler gefunden. Trotzdem bereitstellen?", "Wartet auf Znuny …", "In Znuny übernommen", "Znuny konnte nicht übernehmen: {{message}}", "Das Znuny-Add-on TiqoraSync ab 1.2.0 fehlt – Bereitstellen ist nur in Znuny möglich.", "Znuny hat seit {{minutes}} Minuten nicht reagiert. Läuft der Znuny-Daemon?", "Tiqora stellt allein bereit (Znuny läuft nicht mehr)."
- `admin.processes.{new,import,export,copy,edit,delete,deleteConfirm}`: "Neuer Prozess", "Importieren", "Exportieren", "Kopieren", "Bearbeiten", "Löschen", "Prozess „{{name}}“ löschen?"

### Deviations from spec

1. **Deploy in parallel operation needs TiqoraSync 1.2.0, and Tiqora does not clear the sync tables then.** The spec says "snapshot → revision → signal → TiqoraSync runs ProcessDump … and clears the sync tables". That is implemented as written. Two additions: (a) without a fresh heartbeat of an add-on that has the `process_deploy`/`acl_deploy` capability, the deploy is refused instead of queued, because Tiqora would otherwise run a state Znuny never deploys; (b) the status is per revision id. The status key name is `tiqorasync.deploy.<kind>` (the spec only says "a `tiqora_settings` key").
2. **Import writes `not_sync` rows** (Znuny's `ProcessImport` does not, see wave-1 known limitation). An imported process therefore never runs undeployed in Tiqora, and Znuny shows its usual "please deploy" banner.
3. **Rollback = restore as draft** (spec: "rollback = redeploy an old revision"). In parallel operation Tiqora cannot switch Znuny's ZZZ file to an old state without writing `pm_*` anyway, so restore writes the old entities as drafts and the normal deploy follows. Phase 4's "beyond" items (visual diff on the canvas) are not built; the diff is a per-entity list plus a text view.
4. **Validation never blocks a deploy.** The spec lists validation as "beyond parity" without saying it blocks; Znuny does not validate.
5. **Condition module registry is keyed by the `Match` value** (the Perl module path), because that is where Znuny's `Module.pm` takes it from; the spec says "keyed by Znuny module name". It is the same string. ACL-related `Tiqora::` modules are not added.
6. **"Attachments" dialog field = Znuny's existing-attachment selection** (`ProcessManagementAttachment` DF + `AttachmentsReuse`), not an upload field. Article uploads are a separate part of the Article field. The spec's line "Attachments" was ambiguous; this is Znuny's meaning (D2).
7. **Typed field engine scope.** The spec's "one typed field engine shared by … new-ticket/phone forms, webchat pre-chat, ITSM CI forms and the portal" is already in place since wave 0. This plan adds the `customer` and `ticket_attachments` kinds to `FIELD_KINDS` but does not move the new-ticket form onto them.
8. **`ExecuteInvoker`** is conditional (Task 31), and `ConfigItemUpdate` is out (wave 4), as the spec allows ("once GI requester exists" / "needs CMDB").
9. **Designer on phones is read-only** (Assumption): editing a flow graph at 390 px is not usable.

### Known limitations (documented in Task 30, not built)

- `Appointment*`: `Custom*` recurrence, `RecurrenceFrequency`, `RecurrenceExclude`, notification keys and `TicketAppointmentRuleID` are ignored. There are no appointment notifications.
- Option refresh re-asks the server; it does not reproduce Znuny's field-by-field `AJAXUpdatableFields` list per dialog.
- TiqoraSync writes the ZZZ file only on the host where the Znuny daemon runs. Multi-node Znuny setups with separate web nodes need a shared `Kernel/Config/Files` directory, exactly as Znuny's own deploy button needs it on the node that serves the request.
- Edits between a Tiqora deploy and TiqoraSync's dump (≤ ~1 minute) are deployed in Znuny as well. Tiqora picks them up within two watch intervals as a `znuny` revision (self-healing, pinned).
- The designer does not show ACLs that restrict processes; the ACL editor stays separate.

## Shared files (coordination with the other wave-3 plans)

The parallel wave-3 plans are GI requester + debugger, webchat AI first line + identity, reporting Znuny stats import + scheduled PDF, and dashboards phase 2. Hotspots:
- `backend/src/tiqora/extensions/core.py` (`register_core`: this plan adds `TRANSITION_ACTIONS` and `CONDITION_MODULES`);
- `backend/src/tiqora/api/v1/admin/__init__.py` (router);
- `backend/src/tiqora/process/engine.py` (the GI plan may add `ExecuteInvoker`: coordinate through Task 31 only);
- `backend/src/tiqora/api/v1/tickets.py` (`decode_attachments` move);
- `packages/api-client/openapi.json`, `docs/api/openapi.json`;
- `frontend/src/router.tsx`, `frontend/src/lib/adminSearch.ts`, `frontend/src/demo/mockData.ts`, `frontend/e2e/fixtures/mock-api.ts`;
- `frontend/src/components/fields/{types.ts,kinds.tsx}`;
- `frontend/package.json` + `pnpm-lock.yaml` (`@xyflow/react`);
- i18n `en.json`/`de.json` + propagation;
- `NOTICE.md`, `docs/compatibility.md`, `docs/process-management.md`, `docs/architecture.md`;
- `packages/znuny-addon/TiqoraSync/*` (the GI plan may also touch the add-on; whoever lands second bumps the version and merges the ChangeLog).

This plan adds no migration. If another wave-3 plan lands one first, nothing here changes.

---

## Tasks

Order: phase 2 (Tasks 1–9), then phase 3 backend (10–18), phase 3 frontend (19–27), then verification (28–30). Task 31 is conditional. Backend tasks inside a phase may run in parallel when their "Needs" are met. **Frontend tasks that add i18n keys run one after another** (R-I18NSEQ).

| # | Task | Stack | Needs |
|---|---|---|---|
| 1 | Transition action registry (`TRANSITION_ACTIONS`) with parameter specs | backend | — |
| 2 | `AppointmentCreate/Update/Remove` actions | backend | 1 |
| 3 | Condition module registry + `ValidateDemo` | backend | — |
| 4 | Article field: uploads + `TimeUnits`; customer field value shape | backend | — |
| 5 | Znuny `Attachments` field + `AttachmentsReuse` / std attachments in ArticleSend/TicketArticleCreate | backend | 4 |
| 6 | Option refresh endpoints (agent + portal) | backend | 4 |
| 7 | Process path endpoint | backend | — |
| 8 | Frontend: field kinds `customer` + `ticket_attachments`, article uploads/time units, option refresh in the process form | frontend | 4, 5, 6 |
| 9 | Frontend: "Bisheriger Weg" in the process widget | frontend | 7, 8 |
| 10 | Designer core: ids, YAML codec, versions | backend | — |
| 11 | `ProcessWriter` (process + entity CRUD, sync rows, cache signals, usage/delete rules, optimistic versions) | backend | 10 |
| 12 | Draft reader + designer catalogue | backend | 1, 3, 11 |
| 13 | Admin designer API (CRUD routes) | backend | 11, 12 |
| 14 | Validation | backend | 12 |
| 15 | Import / export in Znuny's format | backend | 11, 13 |
| 16 | Revisions: list, diff, restore as draft | backend | 11, 13 |
| 17 | Deploy via TiqoraSync (Python side): mode, signal, status, gate | backend | — |
| 18 | TiqoraSync 1.2.0 (Perl): heartbeat, deploy handling, status | add-on | 17 (key names) |
| 19 | Frontend: designer API, demo mocks, lazy route skeleton, `@xyflow/react` | frontend | 13 |
| 20 | Frontend: pure canvas model | frontend | 19 |
| 21 | Frontend: canvas UI (nodes, edges, palette, save/conflict) | frontend | 20 |
| 22 | Frontend: process + activity panels | frontend | 21 |
| 23 | Frontend: dialog panel | frontend | 22 |
| 24 | Frontend: transition panel + condition builder | frontend | 22 |
| 25 | Frontend: action panel (typed forms from registry, raw fallback) | frontend | 22 |
| 26 | Frontend: validation, revisions/diff/restore, deploy bar with Znuny status | frontend | 14, 16, 17, 21 |
| 27 | Frontend: process list page (new, import, export, copy, delete) | frontend | 15, 19 |
| 28 | Golden tests on 6.5 + 7.3 (import/export, drafts, TiqoraSync deploy, appointments) | golden | 2, 15, 17, 18 |
| 29 | E2E (demo mode) designer flow + screenshots | frontend | 21–27 |
| 30 | Docs + full test and lint run | all | all |
| 31 | (conditional) `ExecuteInvoker` via the GI requester | backend | GI requester plan's invoker entry point |

---

### Task 1: Transition action registry with parameter specs (backend)

**Files:**
- Create: `backend/src/tiqora/process/actions/__init__.py`, `backend/src/tiqora/process/actions/registry.py`, `backend/tests/test_process_action_registry.py`
- Modify: `backend/src/tiqora/process/engine.py` (handlers stay in place; `_ACTION_HANDLERS` removed; `execute_transition_action` uses `resolve_action`), `backend/src/tiqora/extensions/core.py`, tests that import `_ACTION_HANDLERS` (`grep -rn "_ACTION_HANDLERS" backend`)

**Interfaces:**
- Consumes: the engine handlers `_action_*` (unchanged signatures), `ExtensionPoint`, `get_registry`.
- Produces: D3 exactly. `core_transition_actions()` lists all 20 implemented modules with `params`; `ExecuteInvoker` and `ConfigItemUpdate` have `run=None`; `AppointmentCreate/Update/Remove` are added with `run=None` here, and Task 2 sets the handlers.

- [ ] **Step 1: Write the failing tests** (no DB)

```python
def test_registry_has_every_znuny_module_name() -> None:
    names = {m.name for m in get_registry().items(TRANSITION_ACTIONS)}
    ref = Path(__file__).resolve().parents[2] / "znuny-6.5.22" / "Kernel/System/ProcessManagement/TransitionAction"
    if not ref.is_dir():
        pytest.skip("Znuny reference tree not present (gitignored; CI)")
    znuny = {p.stem for p in ref.glob("*.pm")} - {"Base"}
    assert znuny <= names

def test_resolve_action_accepts_full_perl_path_and_short_name() -> None:
    assert resolve_action("Kernel::System::ProcessManagement::TransitionAction::TicketStateSet").name == "TicketStateSet"
    assert resolve_action("TicketStateSet") is not None and resolve_action("NoSuchModule") is None

def test_param_specs_match_pod_required_keys() -> None:
    state = resolve_action("TicketStateSet")
    assert any(p.one_of == ("State", "StateID") and p.required for p in state.params)
    df = resolve_action("DynamicFieldSet")
    assert df.params == ()   # DF names are free keys → raw form only (POD: "DynamicField_X => value")

async def test_unsupported_module_is_reported_not_raised(session_factory) -> None:
    # run=None module → execute_transition_action returns its short name
    assert await execute_transition_action(..., action=TransitionActionConfig(module="…::ConfigItemUpdate", config={}), ...) == "ConfigItemUpdate"

def test_plugin_registration_is_rejected_on_duplicate_name() -> None:
    reg = Registry(); register_core(reg)
    with pytest.raises(DuplicateExtensionError):
        reg.add(TRANSITION_ACTIONS, TransitionActionModule(name="TicketStateSet", run=None))
```

- [ ] **Step 2: Run** `uv run python -m pytest tests/test_process_action_registry.py -q` → FAIL (ImportError).
- [ ] **Step 3: Implement** D3. Copy the parameter specs from each module's POD (`Config => {…}` with "(required)" markers; `one_of` for `X`/`XID` pairs). Modules with free DF keys (`DynamicFieldSet`, `DynamicFieldRemove`, `TicketCreate` DF part) get the known keys only, and the designer adds raw keys (D11 catalogue). Update the engine docstring's "Deferred" list.
- [ ] **Step 4: Run** the new module plus `tests/test_process_engine.py tests/test_process_engine_conditions.py tests/test_process_smart_tags.py` → PASS; then the lint gates.
- [ ] **Step 5: Commit**: `feat(process): transition action registry with parameter specs`.

---

### Task 2: `AppointmentCreate`, `AppointmentUpdate`, `AppointmentRemove` (backend)

**Files:**
- Create: `backend/src/tiqora/process/actions/appointments.py`, `backend/tests/test_process_appointment_actions.py`
- Modify: `backend/src/tiqora/process/actions/registry.py` (set `run`), `backend/src/tiqora/calendar/service.py` (`create_appointment_unchecked`)

**Interfaces:**
- Consumes: D4, `CalendarService`, `replace_action_config` (already applied by `execute_transition_action`), `update_dynamic_field`, sysconfig `OTRSTimeZone`.
- Produces: the three handlers registered under their Znuny names.

- [ ] **Step 1: Write the failing tests** (both dialects; band 891000–891999; a calendar with group 891001, users 891010 rw / 891011 none; DF `wf3AppointmentID` Text)

```python
async def test_create_with_calendar_name_times_in_system_zone_and_df(env) -> None:
    # OTRSTimeZone "Europe/Berlin"; StartTime "2026-11-02 09:00:00" → stored 08:00 UTC; DF gets the id as str
async def test_create_needs_no_permission_like_znuny(env) -> None          # user 891011 creates fine
async def test_update_requires_rw_and_keeps_unset_fields(env) -> None
async def test_remove_requires_create_or_rw(env) -> None                    # user 891011 → appointment still there, warning logged
async def test_team_and_resource_comma_lists(env) -> None
async def test_unsupported_recurrence_keys_are_ignored_with_warning(env, caplog) -> None
async def test_failing_appointment_action_does_not_stop_transition(env) -> None
    # transition with [AppointmentCreate (unknown CalendarName), TicketStateSet] → state changed, activity moved
async def test_update_via_smart_tag_from_df(env) -> None
    # AppointmentUpdate AppointmentID "<OTRS_TICKET_DynamicField_wf3AppointmentID>" updates the created one
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** D4. Every early return logs `process_action_appointment_skipped` with a reason. Cite `AppointmentCreate.pm`, `AppointmentUpdate.pm`, `AppointmentRemove.pm` and `Appointment.pm:1548-1560` in comments.
- [ ] **Step 4: Run** the module plus `tests/test_calendar*.py` → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(process): appointment transition actions`.

---

### Task 3: Condition module registry + `ValidateDemo` (backend)

**Files:**
- Create: `backend/src/tiqora/process/condition_modules.py`, `backend/tests/test_process_condition_modules.py`
- Modify: `backend/src/tiqora/process/conditions.py` (`Type: Module` branch), `backend/src/tiqora/extensions/core.py`

**Interfaces:** D5.

- [ ] **Step 1: Failing tests** (pure, no DB)

```python
def test_validate_demo_matches_queue_raw_only() -> None
def test_match_with_trailing_pm_and_whitespace_resolves() -> None
def test_unknown_module_does_not_match_and_logs_once(caplog) -> None
def test_module_condition_inside_or_group_with_string_condition() -> None   # evaluate_transition linking
def test_plugin_can_register_a_tiqora_module() -> None                      # Registry() + add → resolves
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** plus `tests/test_process_conditions.py` → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(process): condition module registry with ValidateDemo`.

---

### Task 4: Article uploads + `TimeUnits`; customer field (backend)

**Files:**
- Create: `backend/src/tiqora/domain/attachments_in.py` (moved `ArticleAttachmentIn`, `decode_attachments`; `api/v1/tickets.py` re-imports them), `backend/tests/test_process_dialog_article_extras.py`
- Modify: `backend/src/tiqora/process/schemas.py` (D6), `backend/src/tiqora/process/dialog_form.py` (time units, uploads flag, customer value), `backend/src/tiqora/process/fields.py` (`missing_required_fields` + `TimeUnits`; `normalize_field_values` keeps `TimeUnits` and `ArticleAttachments`), `backend/src/tiqora/process/process_ticket.py` + engine customer apply (empty `CustomerID` → the customer user's `UserCustomerID`), `backend/src/tiqora/process/engine.py` (`_store_dialog_article`: attachments, time units), `api/v1/process.py` + `api/portal/process.py` (request size: `field_values` may hold base64 → reuse the general upload limit; 413 like the reply dialog)

**Interfaces:**
- Consumes: `decode_attachments`, `ArticleIn.attachments`, the existing time accounting writer (`ticket_write_service` around `normalize_time_unit`, :958-1000), sysconfig `Ticket::Frontend::AccountTime` (`get_bool`).
- Produces: D6 `time_units`, `uploads`, the `customer` value shape; `field_values` keys `TimeUnits`, `ArticleAttachments`.

- [ ] **Step 1: Failing tests** (both dialects; band 892000–892499)

```python
async def test_time_units_display_matrix(env) -> None
    # TimeUnits '0' → not in form, value ignored; '1' optional; '2' required → RequiredFieldMissing("TimeUnits");
    # AccountTime off → time_units "0" whatever the config
async def test_time_units_booked_with_article(env) -> None          # time_accounting row with the article id
async def test_uploads_attached_to_dialog_article(env) -> None      # 2 files, article_data_mime_attachment rows
async def test_uploads_without_article_are_ignored(env, caplog) -> None
async def test_upload_limit_413(env) -> None
async def test_customer_id_derived_from_customer_user_when_empty(env) -> None
async def test_portal_forged_customer_and_attachments_ignored(env) -> None
    # portal submit with {"CustomerUserID": "other@example.org", "Attachments": [...]} → ticket keeps the
    # customer's own ids; no ProcessManagementAttachment DF value
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** D6 (article part + customer). Cite `AgentTicketProcess.pm:3067-3082, 5686-5720` and `CustomerTicketProcess.pm:2224`.
- [ ] **Step 4: Run** the new module plus `tests/test_process_dialog_form.py tests/test_process_fields.py tests/test_process_ticket_create.py tests/test_portal_process_ticket.py tests/test_agent_email_outbound.py` → PASS. Regenerate OpenAPI. Lint gates.
- [ ] **Step 5: Commit**: `feat(process): article uploads, time units and customer value in dialogs`.

---

### Task 5: Znuny `Attachments` field + attachment reuse in actions (backend)

**Files:**
- Create: `backend/src/tiqora/process/attachments_field.py`, `backend/tests/test_process_attachments_field.py`
- Modify: `backend/src/tiqora/process/dialog_form.py` (kind `ticket_attachments` for agent dialogs with a ticket; not rendered otherwise), `backend/src/tiqora/process/engine.py` (`_apply_dialog_field_changes` stores the DF; `_action_article_send` + `_action_ticket_article_create` read `AttachmentsReuse`, `Attachments`, `AttachmentIDs`), `backend/src/tiqora/process/actions/registry.py` (params `AttachmentsReuse` bool, `Attachments` std_attachment list, `AttachmentIDs` text)

**Interfaces:** D6 `attachments_field.py`. Sysconfig keys `Process::DynamicFieldProcessManagementAttachment` (default `ProcessManagementAttachment`), `…::Keep`, `…::FormatString`.

- [ ] **Step 1: Failing tests** (both dialects; band 892500–892999)

```python
async def test_option_keys_and_labels_like_znuny(env) -> None
    # "ObjectType=Article;ObjectID=<aid>;ID=<fid>" and "Artikel#1: offer.pdf"
async def test_forged_keys_are_dropped(env) -> None
async def test_attachments_field_stores_df_and_article_send_reuses(env) -> None
    # dialog selects 2 → DF "k1,k2"; transition ArticleSend AttachmentsReuse '1' → mail job carries both; DF cleared
async def test_keep_setting_keeps_df(env) -> None
async def test_std_attachments_by_name_and_id_invalid_skipped(env, caplog) -> None
async def test_field_absent_on_start_form_and_portal(env) -> None
async def test_missing_df_definition_skips_silently(env) -> None   # Znuny: next DIALOGFIELD if !DF
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (cite D2 lines). **Step 4: Run** plus the engine suites → PASS; lint gates; regenerate OpenAPI if the `FieldKind` literal changed.
- [ ] **Step 5: Commit**: `feat(process): Znuny attachments field and attachment reuse in mail actions`.

---

### Task 6: Option refresh endpoints (backend)

**Files:**
- Create: `backend/src/tiqora/process/form_options.py`, `backend/tests/test_process_form_options.py`
- Modify: `backend/src/tiqora/process/dialog_form.py` (`refresh_on` per field, shared option builder extracted so the form and the refresh use one code path), `api/v1/process.py`, `api/portal/process.py`

**Interfaces:** D7.

- [ ] **Step 1: Failing tests** (both dialects; band 893000–893499)

```python
async def test_owner_options_follow_selected_queue(env) -> None
async def test_sla_options_follow_selected_service(env) -> None
async def test_acl_on_dynamic_field_value_hides_state(env) -> None
    # ACL Properties DynamicField_wf3Area ['North'] → PossibleNot Ticket State ['closed successful']; refresh with
    # values {"DynamicField_wf3Area": "North"} drops it, without the value it is kept
async def test_refresh_never_returns_more_than_the_form(env) -> None    # option set ⊆ unfiltered form options
async def test_portal_refresh_uses_customer_principal(env) -> None
async def test_refresh_checks_availability_like_the_form(env) -> None   # Inactive process → 409, hidden dialog → 403
async def test_refresh_on_lists(env) -> None
```

- [ ] **Step 2–4:** Run → FAIL, implement, run plus `tests/test_process_dialog_form.py tests/test_process_availability.py` → PASS. Regenerate OpenAPI. Lint gates.
- [ ] **Step 5: Commit**: `feat(process): refresh dialog options from the current form values`.

---

### Task 7: Process path endpoint (backend)

**Files:**
- Create: `backend/src/tiqora/process/path.py`, `backend/tests/test_process_path.py`
- Modify: `api/v1/process.py`, `process/schemas.py` (re-export the DTOs)

**Interfaces:** D8.

- [ ] **Step 1: Failing tests** (both dialects; band 893500–893999)

```python
async def test_path_from_history_oldest_first_with_current_flag(env) -> None
async def test_only_rows_after_newest_process_start(env) -> None     # a ticket re-enrolled after process DF reset
async def test_deleted_activity_has_empty_name(env) -> None
async def test_no_access_is_404_like_state(env) -> None
async def test_limit_200(env) -> None
```

- [ ] **Step 2–4:** Run → FAIL, implement (user names via integer IN-list, no join with `tiqora_*`), run → PASS. Regenerate OpenAPI. Lint gates.
- [ ] **Step 5: Commit**: `feat(process): path so far for the ticket zoom`.

---

### Task 8: Frontend: new field kinds, article extras, option refresh in the process form

**Split point (two subagents, sequential):** **8a**: the two field kinds (`CustomerKind`, `TicketAttachmentsKind`), their registration and tests, API wrappers + mocks, i18n. **8b**: `processForm.ts` mapping, `ArticleUploads`, `useProcessFormOptions`, the form wiring and the demo check. Each part commits on its own.

**Files:**
- Create: `frontend/src/components/fields/CustomerKind.tsx` (+ test), `frontend/src/components/fields/TicketAttachmentsKind.tsx` (+ test), `frontend/src/components/process/ArticleUploads.tsx` (+ test), `frontend/src/components/process/useProcessFormOptions.ts` (+ test)
- Modify: `components/fields/types.ts`, `components/fields/kinds.tsx` (register both kinds in `registerCoreFieldKinds`), `components/process/processForm.ts` (map `customer` → `CustomerUserID` def of kind `customer` + `CustomerID` text def; `article` → subject, body, `TimeUnits` number def when `time_units !== "0"` with `required` for "2", uploads flag; `ticket_attachments` → kind def with static options; `toSubmitValues`: `TimeUnits`, `ArticleAttachments` base64 list, `Attachments` key list), `components/process/ProcessDialogForm.tsx` + `ProcessFormBody.tsx` (uploads area under the article, `optionsByKey` from `useProcessFormOptions`), `lib/processApi.ts` + `lib/portalProcessApi.ts` (`startFormOptions`, `dialogFormOptions`, `path`), `demo/mockData.ts`, `e2e/fixtures/mock-api.ts`, i18n (D17 `process.field.*`, `process.form.*`)

**Interfaces:** D16 field types and `useProcessFormOptions`; consumes D6/D7 responses.

- [ ] **Step 1: Failing tests** (Vitest + Testing Library)

```ts
it("customer kind searches /reference/customers and returns the login string")
it("customer kind is keyboard reachable and opens CustomerPickerDialog")
it("ticket_attachments renders options as checkboxes and returns keys")
it("article with time_units '2' marks Zeitaufwand required; '0' renders no field")
it("uploads chips: add, remove, size limit message")
it("toSubmitValues maps TimeUnits, ArticleAttachments, Attachments; CustomerID may stay empty")
it("useProcessFormOptions re-asks only after a refresh_on source changes, debounced 300 ms")
it("portal form never renders customer or ticket_attachments kinds")
```

- [ ] **Step 2: Run** `pnpm test --run src/components/fields src/components/process` → FAIL.
- [ ] **Step 3: Implement.** Reuse `ComposerChips` + `useChatAttachments` (limits and messages as in the reply dialog) and `CustomerPickerDialog`. The `customer` kind is a text input with a search popover plus a "Kunde wählen" button that opens the dialog.
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS. Demo check (`VITE_DEMO=1`): open a process dialog with every kind at 1280/390 px, light/dark.
- [ ] **Step 5: Commit**: `feat(process-ui): customer search, attachments, time units and live options in process forms`.

---

### Task 9: Frontend: "Bisheriger Weg" in the process widget

**Files:**
- Create: `frontend/src/components/agent/process/ProcessPathTimeline.tsx` (+ test)
- Modify: `components/agent/process/ProcessWidget.tsx` (+ test; collapsible section under the current step; the query runs when expanded), `demo/mockData.ts`, i18n `process.path.*`

- [ ] **Step 1: Failing tests:** `it("lists steps oldest first, marks current, shows '(gelöscht)' for empty names")`, `it("collapsed by default and loads on expand")`, `it("empty state")`.
- [ ] **Step 2–4:** Run → FAIL, implement (dates through the existing time-zone formatter), lint and tests → PASS; demo check.
- [ ] **Step 5: Commit**: `feat(process-ui): show the path so far in the process widget`.

---

### Task 10: Designer core: ids, YAML codec, versions (backend)

**Files:**
- Create: `backend/src/tiqora/process/designer/__init__.py`, `ids.py`, `yaml_codec.py`, `versioning.py`, `backend/tests/test_process_designer_core.py`

**Interfaces:** D9.

- [ ] **Step 1: Failing tests** (pure)

```python
def test_entity_id_shape() -> None:          # "Activity-" + 32 hex, unique over 1000 draws
def test_yaml_dump_starts_with_document_marker_and_strings() -> None:
    out = dump_znuny_yaml({"Display": 2, "Flag": True, "None": None, "List": [1, "a"]})
    assert out.startswith("---\n") and load_znuny_yaml(out) == {"Display": "2", "Flag": "1", "None": "", "List": ["1", "a"]}
def test_reads_znuny_sample_files_unchanged() -> None:    # each scripts/test/sample/ProcessManagement/*.yml (skip if absent)
def test_version_changes_with_config_and_name_only() -> None
def test_umlauts_round_trip() -> None
```

- [ ] **Step 2–4:** Run → FAIL, implement, run → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(process-designer): entity ids, Znuny YAML codec and versions`.

---

### Task 11: `ProcessWriter` (backend)

**Split point (two subagents, sequential):** **11a**: entity create/update/delete for the four sub-entity types, `_mark_sync`, `_signal`, versions/`VersionConflict`, config normalisation, no-op detection, usage checks for sub-entities. **11b**: process create/update (reference validation)/copy/delete (Inactive rule), process usage, and `test_engine_keeps_revision_after_designer_edit`. Each part commits on its own.

**Files:**
- Create: `backend/src/tiqora/process/designer/writer.py`, `backend/tests/test_process_designer_writer.py`

**Interfaces:** D10. Consumes `PmProcess` and the other `PmActivity*` models, `invalidate_cache_type`, `dump_znuny_yaml`, `entity_version`, `active_process_source` (for the engine test).

- [ ] **Step 1: Failing tests** (both dialects; band 894000–894499; every test deletes its `pm_*` and `pm_entity_sync` rows by entity id, plus its `tiqora_cache_invalidation` rows above a recorded id)

```python
async def test_create_process_row_shape_like_znuny(env) -> None
    # entity_id "Process-<32hex>", state S2, config YAML keys Description/Path/StartActivity/StartActivityDialog,
    # layout "--- {}\n" or "---\n{}\n", create_by/change_by user, change_time set by DB
async def test_edit_writes_not_sync_row_and_cache_signal(env) -> None
    # update_entity → pm_entity_sync (Activity, eid, not_sync); second update → same row, UPDATE only;
    # tiqora_cache_invalidation has "ProcessManagement_Activity"
async def test_noop_update_writes_nothing(env) -> None
async def test_stale_expected_version_is_409(env) -> None           # VersionConflict, row unchanged
async def test_delete_used_entity_is_refused(env) -> None           # each of the four kinds; used_by lists ids
async def test_delete_process_requires_inactive(env) -> None        # S1 → ProcessNotInactive; S2 → row gone + sync 'deleted'
async def test_update_process_rejects_unknown_references(env) -> None
async def test_copy_reuses_entities_and_names_copy(env) -> None     # "(Kopie)", new process id, same Path ids
async def test_engine_keeps_revision_after_designer_edit(env) -> None
    # record a revision, then writer edits a dialog → active_process_source still the revision; dialog form unchanged
async def test_config_normalisation(env) -> None                    # renumbered keys, FieldOrder sync, string scalars
```

- [ ] **Step 2–4:** Run → FAIL, implement (cite `Entity.pm:126-200`, `DB/Process.pm:103-200`, `AdminProcessManagement.pm:716-800, 1088-1160, 1882-1990`), run → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(process-designer): pm_* write service with Znuny draft semantics`.

---

### Task 12: Draft reader + designer catalogue (backend)

**Files:**
- Create: `backend/src/tiqora/process/designer/draft.py`, `catalog.py`, `schemas.py` (D11 models), `backend/tests/test_process_designer_draft.py`

**Interfaces:** D11 `load_process_draft`, `list_entities`, `load_sync_states`, `build_designer_catalog`. The catalogue fields are: ticket attributes (Title, Queue, State, Priority, Type, Service, SLA, Owner, Responsible, Lock, CustomerUserID, CustomerID, PendingTime, Article, Attachments) with `dialog=True`; condition fields (D7 of wave 1: `get_condition_data` keys); every valid ticket DF; option lists for queues, states, priorities, types, services, SLAs and DF PossibleValues.

- [ ] **Step 1: Failing tests** (both dialects; band 894500–894999)

```python
async def test_draft_lists_activities_in_path_order_and_marks_sync_state(env) -> None
async def test_missing_references_reported(env) -> None
async def test_entities_list_used_by(env) -> None
async def test_catalog_contains_registry_actions_and_modules(env) -> None
async def test_catalog_account_time_flag(env) -> None
```

- [ ] **Step 2–4:** Run → FAIL, implement (load keys first, then IN-lists; no joins), run → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(process-designer): draft reader and designer catalogue`.

---

### Task 13: Admin designer API (backend)

**Files:**
- Create: `backend/src/tiqora/api/v1/admin/process_designer.py`, `backend/tests/test_admin_process_designer_api.py`
- Modify: `backend/src/tiqora/api/v1/admin/__init__.py`

**Interfaces:** D11 routes (CRUD part; validation, export, import and revision routes come in Tasks 14–16). The error mapping is 404 `EntityNotFound`, 409 `VersionConflict` / `EntityInUse` / `ProcessNotInactive` (German `detail`), and 422 `InvalidReference`. Every write commits once at the end of the request.

- [ ] **Step 1: Failing tests** (both dialects; band 894500–894999 shared with Task 12, sub-band 894700–894999)

```python
async def test_non_admin_gets_403(client) -> None
async def test_create_edit_delete_round_trip(client) -> None
async def test_conflict_detail_is_german(client) -> None
async def test_entity_type_path_rejects_process(client) -> None     # 422
async def test_in_use_detail_names_the_users(client) -> None
```

- [ ] **Step 2–4:** Run → FAIL, implement, run → PASS. Regenerate OpenAPI. Lint gates.
- [ ] **Step 5: Commit**: `feat(process-designer): admin API for processes and entities`.

---

### Task 14: Validation (backend)

**Files:**
- Create: `backend/src/tiqora/process/designer/validation.py`, `backend/tests/test_process_designer_validation.py`
- Modify: `api/v1/admin/process_designer.py` (`GET /processes/{eid}/validation`)

**Interfaces:** D12. `parallel_operation` = `not (await get_ownership_state(...)).active`.

- [ ] **Step 1: Failing tests:** one pure test per code (built from small `ProcessDraftOut` fixtures), `test_clean_process_has_no_issues` (Znuny sample `AgentTicketProcess.yml` imported into a draft fixture → no errors), and the API test `test_validation_endpoint_both_dialects`.
- [ ] **Step 2–4:** Run → FAIL, implement (pure function plus one `load_reference_data` with IN-lists), run → PASS. Regenerate OpenAPI. Lint gates.
- [ ] **Step 5: Commit**: `feat(process-designer): validation while editing`.

---

### Task 15: Import / export in Znuny's format (backend)

**Files:**
- Create: `backend/src/tiqora/process/designer/exchange.py`, `backend/tests/test_process_designer_exchange.py`, `backend/tests/fixtures/process_samples/` (copies of Znuny's `AgentTicketProcess.yml`, `CustomerTicketProcess.yml`, `Complex1.yml`. These are GPL-3.0 test fixtures: add a README with origin and licence like `backend/tests/fixtures/znuny-schema/README.md`, and a NOTICE.md line)
- Modify: `api/v1/admin/process_designer.py` (`GET …/export` → `Response(media_type="application/x-yaml", headers={"Content-Disposition": 'attachment; filename="Export_ProcessEntityID_<eid>.yml"'})`, `POST /import`)

**Interfaces:** D13.

- [ ] **Step 1: Failing tests** (both dialects; band 896000–896399; the sample DFs `TestDropdownACLProcess` etc. are created and deleted by the module)

```python
async def test_import_creates_new_ids_and_remaps_layout(env) -> None
async def test_import_writes_not_sync_rows(env) -> None
async def test_import_refuses_missing_dynamic_fields(env) -> None   # Znuny's message, nothing written
async def test_import_overwrite_updates_in_place(env) -> None
async def test_round_trip_parsed_equal(env) -> None
    # import sample → export → load_znuny_yaml → normalise (drop ID/CreateTime/ChangeTime, map ids by name) == sample
async def test_broken_yaml_is_422(env) -> None
async def test_import_is_atomic(env) -> None                        # failure in the last entity leaves no rows
```

- [ ] **Step 2–4:** Run → FAIL, implement, run → PASS. Regenerate OpenAPI. Lint gates.
- [ ] **Step 5: Commit**: `feat(process-designer): import and export Znuny process files`.

---

### Task 16: Revisions: list, diff, restore as draft (backend)

**Files:**
- Create: `backend/src/tiqora/process/designer/revisions.py`, `backend/tests/test_process_designer_revisions.py`
- Modify: `api/v1/admin/process_designer.py` (three routes), `deployed/store.py` (`list_revisions(session, kind, limit)` and `load_revision(session, kind, revision_id)`, both bounded)

**Interfaces:** D14.

- [ ] **Step 1: Failing tests** (both dialects; band 896400–896799)

```python
def test_diff_added_removed_changed_ignores_times() -> None          # pure
def test_diff_filtered_to_one_process_includes_referenced_entities() -> None
async def test_restore_writes_drafts_with_same_ids_and_never_deletes(env) -> None
async def test_restore_unknown_process_in_revision_is_404(env) -> None
async def test_revisions_list_newest_first_limit(env) -> None
```

- [ ] **Step 2–4:** Run → FAIL, implement, run → PASS. Regenerate OpenAPI. Lint gates.
- [ ] **Step 5: Commit**: `feat(process-designer): compare and restore deployed versions`.

---

### Task 17: Deploy via TiqoraSync, Python side (backend)

**Files:**
- Create: `backend/src/tiqora/deployed/znuny_sync.py`, `backend/tests/test_deploy_znuny_sync.py`
- Modify: `deployed/gate.py` (`deploy_mode`), `deployed/watcher.py` (`deploy_from_tiqora(..., mode)`), `api/v1/admin/deploy.py` (mode, 409 text), `api/v1/admin/deploy_status.py` (`znuny_sync`, `deploy_mode`), `backend/tests/test_admin_deploy*.py` (existing expectations: `mode="local"`)

**Interfaces:** D15.

- [ ] **Step 1: Failing tests** (both dialects; band 898000–898999; the tests write the heartbeat and status keys themselves as the add-on would)

```python
async def test_deploy_refused_without_fresh_heartbeat(client) -> None       # no key / 11 min old / no capability → 409 text
async def test_signal_mode_records_revision_and_queues_signal(client) -> None
    # fresh heartbeat; pm_entity_sync has 2 rows → revision source tiqora; cache row "TiqoraDeploy_process_<id>";
    # pm_entity_sync still 2 rows
async def test_signal_mode_unchanged_still_queues_signal(client) -> None
async def test_status_pending_until_addon_reports_same_revision(client) -> None
    # status key for an older id → pending; same id ok → "ok"; error → message passed through; > 5 min → overdue
async def test_local_mode_unchanged_behaviour(client) -> None                # ownership/setting → clears sync tables
async def test_watcher_unchanged_after_addon_purge(env) -> None
    # simulate the add-on: DELETE FROM pm_entity_sync → two ticks → no new revision
async def test_edit_between_deploy_and_purge_converges(env) -> None
    # deploy (signal) → writer edit → simulate purge → two ticks → new revision source "znuny" equals the live dump
async def test_acl_kind_same_rules(client) -> None
```

- [ ] **Step 2–4:** Run → FAIL, implement, run plus `tests/test_deploy*.py tests/test_deployed*.py` → PASS. Regenerate OpenAPI. Lint gates.
- [ ] **Step 5: Commit**: `feat(deploy): deploy processes and ACLs through TiqoraSync while Znuny runs`.

---

### Task 18: TiqoraSync 1.2.0 (Perl add-on)

**Files:**
- Modify: `packages/znuny-addon/TiqoraSync/Kernel/System/TiqoraSync.pm`, `TiqoraSync.sopm` (Version 1.2.0, ChangeLog, de/en description paragraph "Stellt Prozesse und ACLs bereit, wenn Tiqora darum bittet"), `install/README.md` (what the deploy signal does, Znuny user write permission on `Kernel/Config/Files/ZZZ*.pm`, multi-node note)
- Create: `tests/golden/test_tiqorasync_deploy.py` (used in Task 28; written here with the Perl change)

**Interfaces:** D15 "TiqoraSync 1.2.0". New subs `_HeartbeatSet`, `_DeployRun(Kind => 'process'|'acl', RevisionID => n)`, `_StatusSet(Key, Data)`. `@ObjectDependencies` += `Kernel::Config`, `Kernel::System::JSON`, `Kernel::System::ProcessManagement::DB::Process`, `Kernel::System::ProcessManagement::DB::Entity`, `Kernel::System::ACL::DB::ACL`. All of these are loaded lazily inside `eval` so that a framework without one of them reports `error`.

- [ ] **Step 1: Failing test** (golden, manual `GOLDEN=1`; the module is copied into the peer with `docker cp packages/znuny-addon/TiqoraSync/Kernel/System/TiqoraSync.pm <peer>:/opt/otrs/Kernel/System/TiqoraSync.pm` and run with `znuny_perl_eval("$Kernel::OM->Get('Kernel::System::TiqoraSync')->Run();")`. The peer image does not install the add-on, so this test copies it. The golden DB may lack `tiqora_settings` and `tiqora_cache_invalidation`, so a module fixture creates them if absent (same pattern as `tests/golden/test_process_deploy.py` uses for the revision tables), drops them at teardown when it created them, and otherwise deletes only its own rows/keys.)

```python
def test_run_writes_heartbeat_with_capabilities(peer) -> None
def test_deploy_signal_dumps_file_and_purges_sync_table(peer) -> None
    # insert pm_entity_sync row + signal "TiqoraDeploy_process_<n>" → Run → ZZZProcessManagement.pm mtime changed,
    # pm_entity_sync empty, status key {"revision_id": n, "status": "ok"}
def test_acl_signal_dumps_acl_file_and_resets_acl_sync(peer) -> None
def test_signal_is_not_passed_to_cache_cleanup(peer) -> None   # no error lines in the Znuny log about TiqoraDeploy_
def test_multiple_signals_one_dump_highest_revision(peer) -> None
def test_old_style_cache_type_rows_still_cleaned(peer) -> None
```

- [ ] **Step 2: Run** on the 6.5 peer (`GOLDEN_PEER=znuny-6.5 just golden-up && … golden-seed && GOLDEN=1 uv run python -m pytest tests/golden/test_tiqorasync_deploy.py -q`) → FAIL.
- [ ] **Step 3: Implement** in Perl, Znuny code style (`perltidy` conventions of the existing file). Each kind's work goes in its own `eval`. Write heartbeat and status with `INSERT`/`UPDATE` on `tiqora_settings` the way `_WatermarkSet` does. JSON comes from `Kernel::System::JSON->Encode`. `at` is `$Kernel::OM->Create('Kernel::System::DateTime')->ToEpoch()` (integer epoch seconds, D15).
- [ ] **Step 4: Run** on 6.5, then on 7.3 → PASS. Run `perl -c` inside the peer for the module.
- [ ] **Step 5: Commit**: `feat(tiqorasync): 1.2.0 deploys processes and ACLs on request from Tiqora`.

---

### Task 19: Frontend: designer API, demo mocks, lazy route skeleton, `@xyflow/react`

**Files:**
- Create: `frontend/src/lib/processDesignerApi.ts` (+ test), `frontend/src/routes/admin/process-designer/ProcessDesignerPage.tsx` (skeleton: loads the draft and catalogue, renders the layout grid with placeholders, phone list view), `frontend/src/components/process-designer/` directory
- Modify: `frontend/package.json` + `pnpm-lock.yaml` (`@xyflow/react` exact version, ≥ 3 days old, React 19 peer verified), `NOTICE.md`, `router.tsx` (the `/admin/processes/$processEntityId` route uses `lazyRouteComponent(() => import("@/routes/admin/process-designer/ProcessDesignerPage"), "ProcessDesignerPage")`), delete `routes/admin/ProcessDetailPage.tsx` + test, `demo/mockData.ts` (draft of the sample "Bestellung" process with three steps, catalogue, validation, revisions), `e2e/fixtures/mock-api.ts`, i18n `admin.processDesigner.{title,phoneReadOnly}`

- [ ] **Step 1: Failing tests:** `processDesignerApi` paths and methods; `it("designer route is lazy: the page module is not imported by router.tsx")` (static check: `router.tsx` has no static import of `ProcessDesignerPage`); `it("phone width renders the read-only list")`.
- [ ] **Step 2–4:** Run → FAIL, implement, `pnpm lint && pnpm test --run` → PASS. Run `pnpm build` and confirm `dist/assets/` contains a separate `ProcessDesignerPage-*.js` chunk that includes `@xyflow`, and that the main chunk does not grow by more than 5 kB.
- [ ] **Step 5: Commit**: `feat(process-designer-ui): designer route, API wrapper and demo data`.

---

### Task 20: Frontend: pure canvas model

**Files:**
- Create: `frontend/src/components/process-designer/canvasModel.ts` + `canvasModel.test.ts`

**Interfaces:** D16 `canvasModel.ts`.

- [ ] **Step 1: Failing tests**

```ts
it("toCanvas: one node per Path key, edges from Path[from][transition].ActivityEntityID")
it("start marker on StartActivity; end = no outgoing transitions")
it("broken references flagged")
it("connect / retarget / disconnect / setActions keep other edges untouched")
it("removeActivity drops incoming and outgoing edges and the layout entry; clears start when needed")
it("move stores integer strings; autoLayout fills only missing positions")
it("shapeOf(toCanvas-inverse) round-trips the sample draft")
it("isDirty ignores key order")
```

- [ ] **Step 2–4:** Run → FAIL, implement, run → PASS.
- [ ] **Step 5: Commit**: `feat(process-designer-ui): canvas model`.

---

### Task 21: Frontend: canvas UI

**Files:**
- Create: `DesignerCanvas.tsx`, `ActivityNode.tsx`, `TransitionEdge.tsx`, `StartNode.tsx`, `EntityPalette.tsx` (+ tests)
- Modify: `ProcessDesignerPage.tsx` (wires the canvas, selection state, local `ProcessShape`, "Speichern"/"Verwerfen", 409 banner, `beforeunload` warning while dirty), i18n (`admin.processDesigner.{save,discard,conflict,newStep,reuse,start,end,delete}`)

Behaviour:
- Drag a palette item onto the canvas: "Neuer Schritt" creates an activity (`POST /entities/Activity`) and `addActivity`; an existing activity just runs `addActivity`.
- Connecting two nodes opens a small popover with "Neuer Übergang" (name) or an existing transition, then `connect`.
- Selecting a node or edge opens its panel (Tasks 22–25).
- Keyboard: `Delete` on the selected node or edge asks for confirmation and then runs `removeActivity`/`disconnect` (the entity itself is not deleted).
- `Ctrl/Cmd+S` saves.
- The `@xyflow/react` controls and minimap use the existing theme tokens via CSS variables.

- [ ] **Step 1: Failing tests** (Testing Library; render with the sample draft): nodes and edges present; save sends `PUT` with the shape; 409 shows the banner and keeps the local shape; delete key confirm flow; `beforeunload` registered while dirty.
- [ ] **Step 2–4:** Run → FAIL, implement, lint and tests → PASS. Demo check at 1280 px light/dark (canvas) and 390 px (read-only list).
- [ ] **Step 5: Commit**: `feat(process-designer-ui): canvas with steps, transitions and palette`.

---

### Task 22: Frontend: process + activity panels

**Files:**
- Create: `panels/ProcessPanel.tsx`, `panels/ActivityPanel.tsx`, `panels/PanelFrame.tsx` (shared header, save/discard, version conflict message) (+ tests)
- Modify: `ProcessDesignerPage.tsx`, i18n (`admin.processDesigner.panel.*`, state labels reuse `admin.processes.state.*`)

- ProcessPanel: name, description, state (Aktiv / Abgeschaltet / Läuft aus), start step + start form selects. It edits the local `ProcessShape` and process fields; it is saved with the canvas "Speichern".
- ActivityPanel: name, ordered list of forms with up/down buttons (keyboard accessible), add an existing form or "Neues Formular", remove from the list. Saved directly (`PUT /entities/Activity/{eid}`).

- [ ] **Steps 1–5** as usual (tests: state change marks dirty; dialog reorder sends renumbered `ActivityDialog` keys; conflict message). Commit: `feat(process-designer-ui): process and step panels`.

---

### Task 23: Frontend: dialog panel

**Files:**
- Create: `panels/DialogPanel.tsx`, `panels/dialogFields.ts` (pure: catalogue field → editor row; Display/Default editors per field type) (+ tests)
- Modify: i18n `admin.processDesigner.dialog.*`

Content:
- Name and texts (short/long description, submit button and advice).
- Interfaces: agent/customer checkboxes; at least one is required.
- Permission (select from `catalog.permissions`), "Ticket muss gesperrt sein".
- Field list in `FieldOrder` order: add a field from the catalogue (search), Display (Ausgeblendet/Sichtbar/Pflichtfeld), default value with a typed input from the field engine (select options from the catalogue, date pickers for date DFs), short/long description. The Article field additionally has channel (Intern/E-Mail/Telefon), visible-for-customer and time units (when `catalog.account_time`).
- Inline hints: Queue/State/Lock/Priority with "Sichtbar" shows the warning text of `queue_required_display`; customer interface + Owner etc. shows "Kunden sehen dieses Feld nicht".

- [ ] **Steps 1–5** (tests: `dialogFields` mapping for each type; saving produces Znuny config shape with string Display; field order drag via buttons; hidden field requires default hint). Commit: `feat(process-designer-ui): form panel with typed defaults`.

---

### Task 24: Frontend: transition panel + condition builder

**Files:**
- Create: `panels/TransitionPanel.tsx`, `panels/ConditionBuilder.tsx`, `panels/conditionModel.ts` (pure: Znuny `Condition` ↔ UI groups; renumbering) (+ tests)
- Modify: i18n `admin.processDesigner.condition.*`

Content:
- Overall linking (alle / mindestens eine / genau eine).
- Groups, each with its own linking and rows of field + type + value.
- The value input is typed by the field: select for Queue/State/… and Dropdown DFs, a date input for date DFs, text otherwise; `Module` type shows a select of `catalog.condition_modules` plus free text; `Regexp` validates in the browser with a German error.
- "Keine Bedingung = Übergang gilt immer" note.

- [ ] **Steps 1–5** (tests: round trip of the Znuny sample conditions; renumbering after removing a group; `Module` with an unknown path is kept, with a warning). Commit: `feat(process-designer-ui): transition panel with condition builder`.

---

### Task 25: Frontend: action panel

**Files:**
- Create: `panels/ActionPanel.tsx`, `panels/actionForm.ts` (pure: `CatalogActionOut.params` → `FieldDef[]`; config ↔ values; unknown keys → raw rows) (+ tests)
- Modify: `TransitionEdge` edge panel section "Aktionen" (ordered list, add existing/new, reorder → `setActions`), i18n `admin.processDesigner.action.*`

Content:
- Module picker grouped by "Ticket", "Artikel", "Dynamische Felder", "Kalender", "Sonstiges", with badges "nur in Znuny wirksam" / "nur in Tiqora wirksam".
- A typed form from the parameter specs (`queue` → queue select …, `one_of` → "Name oder ID" toggle).
- "Weitere Parameter": raw key/value rows for every other key (including `DynamicField_*`).
- "Weitere Einstellungen": `UserID` ("Ausführen als").
- Values may contain smart tags: every typed select also allows free text (a combobox), so `<OTRS_TICKET_…>` stays possible.

- [ ] **Steps 1–5** (tests: `TicketStateSet` with `State`; raw keys round-trip; unknown module shows only raw rows; free-text smart tag kept). Commit: `feat(process-designer-ui): action panel with typed parameter forms`.

---

### Task 26: Frontend: validation, revisions/diff/restore, deploy bar

**Files:**
- Create: `ValidationPanel.tsx`, `RevisionsPanel.tsx`, `RevisionDiffView.tsx`, `DeployBar.tsx` (+ tests)
- Modify: `components/process/DeployStatusCard.tsx` (+ test; shows `znuny_sync` state texts; reused on the admin ACL page), `lib/deployStatusApi.ts` (deploy call + types), `ProcessDesignerPage.tsx` (badges with issue counts on nodes and edges; "Prüfen" button), i18n `admin.processDesigner.{validation,revisions,deploy}.*`

Behaviour:
- Validation list grouped by severity. A click selects the entity on the canvas.
- Deploy: "Bereitstellen" is visible when `deploy_mode` is not `null`. It runs validation first; if there are errors, a confirm dialog with the count appears. It then posts `/admin/deploy` and polls `deploy-status` every 5 s while `znuny_sync.state === "pending"`, for at most 5 minutes, after which it shows the overdue text.
- Revisions: a list (date, "aus Znuny"/"von Tiqora"/"beim Start", who), "Vergleichen" (with the previous one or a pick), and the diff view as an entity list plus a side-by-side text view (plain `<pre>` with line marks; no diff library). "Wiederherstellen" asks for confirmation and then reloads the draft.

- [ ] **Steps 1–5** (tests: never shows "In Znuny übernommen" while pending; addon_missing text; confirm on errors; diff renders added/removed/changed). Commit: `feat(process-designer-ui): checks, versions and deploy with Znuny status`.

---

### Task 27: Frontend: process list page

**Files:**
- Create: `ImportDialog.tsx` (+ test)
- Modify: `routes/admin/ProcessesPage.tsx` (+ test), `lib/adminSearch.ts` (keywords "designer", "prozess bearbeiten", "import"), i18n `admin.processes.*`, `admin.processDesigner.import.*`

Content:
- Buttons "Neuer Prozess" (name dialog, then navigate to the designer) and "Importieren" (file input `.yml,.yaml` read in the browser, posted as `content`, overwrite checkbox, German refusal text).
- Row actions: "Bearbeiten", "Exportieren" (link to the export URL with the auth header → use `api.request` blob download like the existing report export), "Kopieren", "Löschen" (only for Inactive; confirm).
- The read-only badge is removed.

- [ ] **Steps 1–5** (tests: delete hidden for Active; import success toast "als Entwurf"; export triggers a download). Commit: `feat(process-designer-ui): create, import, export, copy and delete processes`.

---

### Task 28: Golden tests on 6.5 + 7.3 (manual, `GOLDEN=1`)

**Files:**
- Create: `tests/golden/test_process_designer.py`
- Modify: `tests/golden/test_tiqorasync_deploy.py` (from Task 18: add the end-to-end case), `tests/golden/README-multi-peer.md` (how the add-on module is copied in)

Cases:

```python
def test_znuny_export_imports_into_tiqora_and_round_trips(peer) -> None
    # Znuny: ProcessImport sample → export via _GetProcessData (perl eval) ; Tiqora import (new ids) → export →
    # parsed-equal modulo ids/times; for AgentTicketProcess.yml, CustomerTicketProcess.yml and every
    # Complex*.yml in scripts/test/sample/ProcessManagement/ that imports on the peer
def test_tiqora_draft_is_a_draft_in_znuny(peer) -> None
    # ProcessWriter edits a dialog → Znuny EntitySyncStateList shows it not_sync; Znuny ActivityDialogGet returns
    # the new config (cache signal drained by TiqoraSync Run)
def test_tiqora_deploy_runs_in_znuny(peer) -> None
    # Tiqora signal deploy → TiqoraSync Run → ZZZProcessManagement.pm contains the edit; Znuny ProcessGet from
    # Kernel::System::ProcessManagement::Process (config file) sees it; Tiqora watcher: unchanged
def test_znuny_reads_tiqora_written_yaml(peer) -> None
    # every entity Tiqora wrote loads with Znuny's YAML->Load and ProcessDump(HASH) equals build_process_dump
def test_appointment_create_matches_znuny(peer) -> None
    # same config through Znuny TransitionAction::AppointmentCreate and Tiqora → same calendar_appointment columns
    # (except id/unique_id/create/change times)
def test_module_condition_unknown_module_no_crash_in_znuny(peer) -> None  # confirms D2 Module.pm on the peer
```

- [ ] **Step 1:** Write the tests. **Step 2:** Run on `znuny-6.5`, then `znuny-7.3`; fix Tiqora or add-on differences (record each 7.3 difference with its upstream file in the test docstring). **Step 3:** Commit: `test(golden): process designer, import/export and TiqoraSync deploy on 6.5 and 7.3`.

---

### Task 29: E2E (demo mode) designer flow + screenshots

**Files:**
- Create: `frontend/e2e/process-designer.spec.ts`
- Modify: `frontend/e2e/fixtures/mock-api.ts`, `frontend/e2e/screenshots.spec.ts` (one designer screenshot, desktop light)

Flow: "Neuer Prozess" → add two steps, connect them, add a form with two fields, add a "Status setzen" action, then "Prüfen" (no errors) → "Bereitstellen" → status "Wartet auf Znuny" → the mock flips to ok → "In Znuny übernommen" → back to the list → export link present. A second spec tests the 409 conflict banner. The spec's "test run" step is phase 4 and is not included. "Start on a ticket" is covered by the existing process e2e against the deployed mock.

- [ ] **Steps:** write → run `pnpm exec playwright test e2e/process-designer.spec.ts` → green → delete `.playwright-mcp` → commit `test(e2e): process designer flow`.

---

### Task 30: Docs + full test and lint run

**Files:**
- Modify:
  - `docs/process-management.md`: "Out of scope: designer" → a designer section (concepts in Znuny words: Schritt/Formular/Übergang/Aktion; drafts; Bereitstellen in both modes; TiqoraSync 1.2.0 requirement; import/export; versions; validation codes). New known limitations from this plan. Remove "There is no process designer", "Attachments not supported" and "options do not refresh".
  - `docs/compatibility.md`: row "Process designer" → yes; new rows "Process import/export (Znuny YAML)", "Appointment actions", "Module conditions", "Dialog attachments/time units".
  - `docs/architecture.md`: `tiqora.process.designer`, the two extension points, the deploy handshake diagram in text.
  - `docs/parallel-operation.md`: TiqoraSync 1.2.0, the deploy modes.
  - `docs/guide/znuny-to-tiqora.md`: the designer replaces Znuny's.
  - `README.md`: the feature table line.
  - `packages/znuny-addon/TiqoraSync/install/README.md`: from Task 18, review.
  - Spec `2026-10-08-process-management-design.md`: tick the phase 2/3 checklist items done here, and add the deviations of this plan under "Fixed decisions" with a pointer to this plan.
- Run everything: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (full), the three lint gates, `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs && pnpm build`, `pnpm exec playwright test` (demo e2e), OpenAPI regeneration check (`git diff --stat packages/api-client/openapi.json` empty after a fresh regen).
- [ ] Commit: `docs(process): designer, typed dialogs and deploy through TiqoraSync`.

---

### Task 31 (conditional): `ExecuteInvoker` via the GI requester

Run only when the wave-3 GI requester plan has committed a callable invoker entry point, i.e. a function that runs a configured invoker of a web service for a ticket and returns success/failure, usable from an async session. Otherwise skip this task, leave `ExecuteInvoker` with `run=None` (designer: "nur in Znuny wirksam"), and note the dependency in `docs/process-management.md`.

**Files:**
- Modify: `backend/src/tiqora/process/actions/registry.py` (handler + params `WebserviceID`, `Invoker`, `Asynchronous`, `UserID`, per `ExecuteInvoker.pm` POD), and create `backend/src/tiqora/process/actions/execute_invoker.py` + `backend/tests/test_process_execute_invoker.py`.

Behaviour (`ExecuteInvoker.pm`): it calls the requester with `Data => {TicketID}`. Asynchronous invokers run as a wave-0 job (never inside the dialog request; nginx 90 s). Failures are logged and never stop the transition. Tests use the GI plan's test doubles for the requester transport.

- [ ] **Steps 1–5** as usual. Commit: `feat(process): ExecuteInvoker through the GI requester`.

---

## Self-review notes (for the executor)

- This plan writes Znuny tables in exactly two places: `ProcessWriter` (`pm_*`, `pm_entity_sync`) and the existing `ticket_write_service` / `CalendarService` paths. If a task needs another Znuny write, stop and report it; do not add one.
- Keep `pm_*` YAML in strings. When a test reads YAML written by Tiqora, compare after `load_znuny_yaml`, never byte-for-byte against Perl output.
- Seed bands: re-grep before using them. Golden tests use their own entity ids and delete them at teardown.
- The deploy watcher's rules do not change in this plan; only `deploy_from_tiqora` gains a mode. If a test needs a watcher change, the design here is wrong; report it instead.
- The agent and portal process APIs keep hiding transitions and conditions. Only `/admin/process-designer/*` exposes them, and every route there is `AdminUser`.
