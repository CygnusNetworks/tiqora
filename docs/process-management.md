# ProcessManagement (BPM ticket processes)

ProcessManagement is Tiqora's port of Znuny's BPM-style ticket-process
engine (`Kernel::System::ProcessManagement::*` / `AgentTicketProcess`):
a ticket is attached to a *process*, walks through a sequence of
*activities*, and an agent (or customer) advances it by submitting an
*activity dialog* — a small form whose field values can both mutate the
ticket and decide which *transition* fires next.

Code lives under `backend/src/tiqora/process/`:

| Module | Role |
|---|---|
| `db/legacy/process_management.py` | SQLAlchemy models for the 5 reused `pm_*` tables |
| `process/config.py` | Pydantic models for the YAML blobs stored in each table's `config` column |
| `process/graph.py` | `ProcessRepository` — loads one process into a fully-linked, read-only `ProcessGraph` |
| `process/ticket_state.py` | Resolves a ticket's current process/activity from its two Dynamic Fields |
| `process/engine.py` | The execution engine: start a process, evaluate transitions, dispatch actions, submit dialogs |
| `process/exceptions.py` | Typed exceptions the engine raises (mapped to HTTP codes in the API layer) |
| `api/v1/process.py` | REST endpoints |

## Shared schema — no migration

Tiqora reads and writes the **same** Znuny tables Znuny itself uses:

- `pm_process` — one process definition (`entity_id`, `name`, `state_entity_id`, `layout`, `config`)
- `pm_activity` — one activity node (`config` lists its activity dialogs, in order)
- `pm_activity_dialog` — one activity dialog form (fields, permission, interface)
- `pm_transition` — one transition (`config` holds its `Condition` block(s))
- `pm_transition_action` — one action attached to a transition (`config.Module` + `config.Config`)
- `pm_entity_sync` — Znuny's dirty-flag bookkeeping table; Tiqora does not write to it (see "Supported vs deferred" below)

There is **no** Tiqora-specific migration and no schema divergence: a
process authored in Znuny's admin UI works unmodified in Tiqora, and
(subject to the scope limits below) a process authored via direct DB/YAML
insertion is visible to a running Znuny 6.5 instance too. This mirrors the
approach used for `calendar` (see `docs/architecture.md`).

## Ticket <-> process linkage

A ticket's process state is *not* a separate row — it is tracked entirely
through two well-known Dynamic Fields, exactly as Znuny does it:

- `DynamicField_ProcessManagementProcessID` — the `entity_id` of the running process
- `DynamicField_ProcessManagementActivityID` — the `entity_id` of the ticket's current activity

`process/ticket_state.py` resolves these into a `TicketProcessState` by
reading `DynamicFieldValue` rows for the ticket; `start_process`/
`submit_activity_dialog` write them via the normal
`ticket_write_service.update_dynamic_field` path, so each transition writes
an ordinary `TicketDynamicFieldUpdate` history row — see the "History
fidelity" note at the top of `engine.py` for why there is deliberately no
synthetic `ProcessManagement` history type.

A ticket can only be in **one** process at a time; starting a second
process on a ticket already in one raises `TicketAlreadyInProcess`.

## Config YAML shapes

Each `pm_*` table's `config` column holds a YAML blob (the same format
Znuny's admin UI writes). Exact shapes are Pydantic models in
`process/config.py` — see that module for the authoritative field list;
summary:

- `ProcessConfig` (`pm_process.config`): `Description`, `StartActivity`,
  `StartActivityDialog`, and `Path` — a `{activity_entity_id: {transition_entity_id: TransitionPathEntry}}`
  map, where each entry has `ActivityEntityID` (the transition's target)
  and an ordered `TransitionAction` entity-id list.
- `ActivityConfig` (`pm_activity.config`): an `ActivityDialog` map of
  `{"1": entity_id, "2": entity_id, ...}` — numeric-string keys give
  display order (`ordered_activity_dialog_entity_ids`).
- `ActivityDialogConfig` (`pm_activity_dialog.config`): `DescriptionShort`/
  `DescriptionLong`, `FieldOrder`, a `Fields` map (`Display`, `DefaultValue`,
  per-field `Config`), `Interface` (`AgentInterface`/`CustomerInterface`),
  and `Permission`.
- `TransitionConfig` (`pm_transition.config`): `ConditionLinking`
  (`and`/`or` across blocks) and `Condition` — a map of condition blocks,
  each with its own `Type` (`and`/`or` across that block's fields) and a
  `Fields` map of `{field_name: {Match, Type}}`. No `Condition` key at all
  means the transition is unconditional.
- `TransitionActionConfig` (`pm_transition_action.config`): `Module` (a
  Perl-style `Kernel::System::ProcessManagement::TransitionAction::Foo`
  path — only the last `::`-segment is used to dispatch) and `Config`
  (a free-form key/value map passed to the action handler).

## Engine flow

1. **Start process** (`start_process`) — resolve the process's
   `StartActivity`, set both process Dynamic Fields on the ticket.
2. **Submit activity dialog** (`submit_activity_dialog`):
   a. Look up the ticket's current activity and confirm the submitted
      dialog is one of its `ActivityDialog`s.
   b. If the dialog has a `Permission` set, gate on it via
      `PermissionEngine.check` against the ticket's queue.
   c. Validate required fields are present in `field_values`.
   d. Apply the submitted field changes to the ticket (title, queue,
      state, priority, owner, responsible, customer, article, dynamic
      fields — reusing `ticket_write_service` functions, so each change
      writes its normal Znuny history row).
   e. Evaluate the current activity's outgoing transitions **in
      declared (YAML/dict insertion) order**; the **first** whose
      `Condition` matches the (freshly re-read) ticket attributes wins —
      no further transitions are tried.
   f. Run the matched transition's `TransitionAction`s in order.
   g. Set `ProcessManagementActivityID` to the transition's target
      activity.
   h. If no transition matches, the submission still succeeds — the
      ticket simply stays on its current activity
      (`activity_changed=False`).

Condition evaluation (`evaluate_transition`/`_evaluate_field`) and action
dispatch (`execute_transition_action`) are pure/isolated enough to have
dedicated unit and DB-integration test coverage — see `backend/tests/
test_process_engine_conditions.py` (25 pure-logic cases) and `backend/
tests/test_process_engine.py` (DB-integration cases, including a
condition-matching-vs-non-matching pair and an unsupported-action case).

## REST API

All endpoints live under `/api/v1/process` (`backend/src/tiqora/api/v1/process.py`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/process/` | List all processes (summary: entity id, name, state) |
| GET | `/api/v1/process/{process_entity_id}` | Process detail: activities, their dialogs, outgoing transitions |
| GET | `/api/v1/process/activity-dialog/{activity_dialog_entity_id}` | One activity dialog's fields/order/permission |
| GET | `/api/v1/process/ticket/{ticket_id}/state` | A ticket's current process/activity + available dialogs |
| POST | `/api/v1/process/ticket/{ticket_id}/start` | Start a process on a ticket |
| POST | `/api/v1/process/ticket/{ticket_id}/submit` | Submit an activity dialog (advance the ticket) |

All ticket-scoped endpoints apply the same coarse queue `ro`/`rw`
permission gate as the rest of the ticket REST API (see the module
docstring in `api/v1/process.py`).

## Frontend

- **Ticket zoom**: `ProcessWidget` (`frontend/src/components/agent/process/ProcessWidget.tsx`)
  shows a ticket's current process/activity and available next dialogs;
  `StartProcessDialog` lets an agent attach a process to a ticket not yet
  in one; `ActivityDialogModal` renders a dynamic form from an activity
  dialog's `Fields`/`FieldOrder` and submits it.
- **Admin**: read-only `/admin/processes` list (`routes/admin/ProcessesPage.tsx`)
  and detail view (`routes/admin/ProcessDetailPage.tsx`) — browse processes,
  activities, dialogs, and transitions.
- **Out of scope**: there is no visual process *designer*. Processes must
  still be authored via Znuny's admin UI (`AdminProcessManagement`) or by
  writing directly to the `pm_*` tables / their YAML `config` columns.

## Supported vs deferred

### Condition types (`Fields.*.Type`)

| Supported | Deferred (treated as non-matching) |
|---|---|
| `String` (case-sensitive `eq`) | `GreaterThan` / `GreaterThanOrEqual` / `LessThan` / `LessThanOrEqual` |
| `Regexp` | `Module`-based custom conditions |
| `Contains` / `NotContains` (case-insensitive regex) | |
| `Equal` / `NotEqual` (case-insensitive) | |

An unsupported condition type logs a warning and evaluates to `False` — it
never raises. See `_evaluate_field` in `engine.py` for the full semantics
(each type's Znuny-source-verified matching rule).

### TransitionAction modules (`Module`'s last `::`-segment)

| Supported | Deferred (logged, no-op — collected into `unsupported_actions`) |
|---|---|
| `TicketStateSet` | `TicketSLASet` |
| `TicketQueueSet` | `TicketServiceSet` |
| `TicketOwnerSet` | `TicketTypeSet` |
| `TicketPrioritySet` | `DynamicFieldRemove` |
| `TicketTitleSet` | `DynamicFieldIncrement` |
| `TicketCustomerSet` | `DynamicFieldPendingTimeSet` |
| `TicketResponsibleSet` | `LinkAdd` |
| `TicketLockSet` | `TicketWatchSet` |
| `DynamicFieldSet` | `ArticleSend` |
| `TicketArticleCreate` | `TicketCreate`, `ExecuteInvoker`, `Appointment*` (Create/Update/Remove), `ConfigItemUpdate` |

An unsupported action does not abort the submission: the transition still
fires and the activity still advances, but that one action is skipped and
its short module name is reported in `ActivityDialogSubmitResult.
unsupported_actions` so callers/tests can assert on skip behaviour.

### Other simplifications (see `engine.py`'s module docstring for the authoritative list)

- No `%<OTRS_...>%` / `<OTRS_...>` smart-tag placeholder substitution in
  TransitionAction `Config` values or condition `Match` values — both are
  used verbatim.
- Transitions are evaluated in `Path`'s YAML/dict insertion order; Znuny
  itself has no explicit tiebreak either.
- `pm_process.state_entity_id` (process validity, e.g. `S1`/`S2`) is
  exposed raw, without resolving it through `general_catalog`.
- A ticket can only be in one process at a time (`TicketAlreadyInProcess`).
- Activity Dialog field widgets in the frontend are simplified: `Queue`
  uses the existing queue-select widget; `State`/`Priority`/`Owner`/
  `Responsible` and `DynamicField_*` are plain text inputs — no per-type
  widget dispatch (date pickers, dropdowns bound to valid states, etc.).
- No global toast/notification system — the process widgets show inline
  success/error state.
- Activity Dialog `PendingTime`/`PendingTimeDiff` submission fields are not
  applied from `field_values` (only a `TicketStateSet` transition action's
  own `PendingTimeDiff` is honoured).

## See also

- `docs/architecture.md` — ProcessManagement subsection (module list,
  table reuse, one-line engine flow) alongside the rest of Tiqora's
  architecture.
- `backend/src/tiqora/process/engine.py` — the authoritative source for
  every simplification listed above (each is documented at its point of
  implementation, not just in this doc).
