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
| `process/source.py` | `ProcessSource`: the engine reads the newest deployed revision (`RevisionProcessSource`), or the live `pm_*` rows until a first revision exists (`LiveProcessSource`) |
| `process/dump.py` | Builds Znuny's `ProcessDump` shape from the `pm_*` rows; canonical JSON and checksum |
| `process/availability.py` | State gate, interface and ACL check for every process surface |
| `process/fields.py`, `process/dialog_form.py` | Dialog field semantics (`Display`, hidden defaults) and the server-built dialog form |
| `process/conditions.py`, `process/smart_tags.py` | Condition data, condition types, `<OTRS_...>` smart tags |
| `process/process_ticket.py` | New process ticket (agent and customer) and enroll |
| `deployed/` | Deployed revisions, deploy detection (`watcher.py`), `acl_sync` writes |
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
- `pm_entity_sync` — Znuny's dirty-flag bookkeeping table (one row per
  undeployed process edit). Tiqora only reads it, and clears it after cutover
  (see "Deployed revisions" below)
- `pm_process_preferences` — Znuny 7.2+ only; carried in the snapshot when the table exists

The process definitions themselves have no Tiqora-specific schema: a process
authored in Znuny's admin UI works unmodified in Tiqora, and a process
authored via direct DB/YAML insertion is visible to a co-running OTRS/Znuny
peer (6.0–7.3) too. The only Tiqora additions are the two revision tables
`tiqora_process_revision` and `tiqora_acl_revision` (below).

## Deployed revisions: Tiqora runs what was deployed

Znuny's process editor works on a draft. Agents only see a change after an
admin presses **"Bereitstellen"** (Znuny writes the deployed file and empties
`pm_entity_sync`). Tiqora follows the same rule:

- The engine, the process lists, the dialogs and the ACLs run from the
  **newest deployed revision** (`tiqora_process_revision` /
  `tiqora_acl_revision`: canonical JSON of Znuny's `ProcessDump` / `ACLDump`
  plus checksum, source and time). A draft edit in Znuny is not visible to
  agents until it is deployed.
- The worker loop `deploy_watch` (service "Deploy-Wächter" on the
  "Dienste" page, every `TIQORA_DEPLOY_WATCH_INTERVAL_SECONDS`, default 30)
  decides per kind (process, ACL):

  | Situation | Result |
  |---|---|
  | No revision yet, sync table empty | records revision `initial` ("beim Start") |
  | No revision yet, drafts pending | keeps reading the live rows (warning on "Dienste"); one "Bereitstellen" in Znuny fixes it |
  | Revision exists, drafts pending | waits; the old revision keeps running |
  | Sync table empty, dump differs from the newest revision | records revision `znuny` ("aus Znuny") |
  | Sync table empty, dump equal | nothing |
  | Tiqora owns the schema (after cutover) | records revision `tiqora` ("von Tiqora") whenever the dump differs and clears the sync tables: Tiqora deploys on its own |

- Every engine call reads the newest revision id (one indexed `SELECT`) and
  reuses an in-process parsed copy keyed by that id. This works across API
  replicas and worker processes without pub/sub (no Redis).
- The admin pages for processes and ACLs show a card "Bereitstellung"
  ("Bereitgestellt am ..."), the number of undeployed changes and a warning
  when Tiqora still runs the draft state. `GET /api/v1/admin/deploy-status`
  and `GET /api/v1/admin/processes` back them.

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
      `PermissionEngine.check` against the ticket's queue. The state gate,
      the dialog's `Interface` and the ACLs (below) are checked first.
   c. Validate required fields (`Display: 2`) are present in `field_values`.
   d. Apply the submitted field changes to the ticket (title, queue,
      state, priority, type, service, SLA, lock, owner, responsible,
      customer, pending time, article, dynamic fields — reusing `ticket_write_service` functions, so each change
      writes its normal Znuny history row).
   e. Evaluate the current activity's outgoing transitions **in
      declared (YAML/dict insertion) order**; the **first** whose
      `Condition` matches the (freshly re-read) ticket attributes wins —
      no further transitions are tried.
   f. Run the matched transition's `TransitionAction`s in order (smart tags
      in their `Config` are substituted first).
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

## State gate

The process state (`pm_process.state_entity_id`) is fixed in Znuny, not a
general-catalog lookup: `S1` Active, `S2` Inactive, `S3` FadeAway.

| State | New starts / enroll | Dialogs of running tickets | Transitions |
|---|---|---|---|
| Active (`S1`) | yes | yes | yes |
| FadeAway (`S3`, "Läuft aus") | no | yes | yes |
| Inactive (`S2`, "Abgeschaltet") | no | refused (409), dialogs not offered | not evaluated |

A process is also offered for starting only if its start dialog lists the
caller's interface (`AgentInterface` / `CustomerInterface`); an empty
`Interface` list matches nobody (Znuny behaviour).

## ACL surfaces

The same ACL function (`filter_process_options`) is applied on every process
surface, in the agent interface as the agent and in the portal as the
**customer** (never as the portal's system user 1, which would bypass every
ACL):

- the list of startable processes (ACL return type `Process`),
- the dialogs offered on a ticket and a submit of a dialog (return type
  `ActivityDialog`; a forged submit of a hidden dialog is refused with 403),
- the frontend action `AgentTicketProcess` / `CustomerTicketProcess`
  (return type `Action`), also for the zoom menu,
- the ticket zoom menu itself: `GET /api/v1/tickets/{id}/acl-actions` returns
  which Znuny actions the ACLs leave (reply, note, phone, lock, watch, link,
  merge, print, pending, close, free text, process). Hidden entries are
  removed, not greyed out; value pills (type, service, ...) lose their picker
  but keep showing the value. `GET /tickets/{id}` answers 403 when an ACL
  removes `AgentTicketZoom`,
- the owner and responsible pickers (below) and the bulk dialog.

Customer ACLs use the customer user's data (`CustomerUser` properties,
`Group_<perm>` lists from `group_customer_user`). ACLs match on
`Properties: Process: ProcessEntityID / ActivityEntityID /
ActivityDialogEntityID` like Znuny.

The compat (GenericInterface) API applies **no** ACLs. Znuny's GenericInterface
never calls `TicketAcl` either, so filtering there would break clients that work
against Znuny.

Owner and responsible lists follow queue permissions like Znuny: valid users
with `owner` (responsible: `rw`, unless `responsible` is listed in
`System::Permission`) on the ticket's queue, from `group_user` and role
groups. `Ticket::ChangeOwnerToEveryone` (read with Perl truthiness, stored
`0` is off) lifts the restriction; the process dialog on an existing ticket
ignores that setting, as Znuny does. Bulk: union over the selected tickets'
queues, a selected move-target queue wins, no known queue gives an empty list.

## Dialog field semantics

Per field, `Display` means: `0` (or missing) hidden, `1` shown and optional,
`2` shown and mandatory. (Earlier Tiqora versions had `1` as mandatory; processes
that were filled with that rule now behave like in Znuny.)

- Hidden fields apply their `DefaultValue` server-side, even when a client sends
  another value; a hidden dynamic field without default is cleared.
- Shown fields start with the **ticket's current value** (dialog default only if
  the ticket has none); unchanged Queue, Priority and Responsible write no
  history. Dynamic fields can be cleared by sending an empty list.
- `Queue`, `State`, `Lock`, `Priority` with `Display: 1` are a Znuny config
  error; on a new ticket they fall back to `Process::DefaultQueue/State/Lock/Priority`.
- Field names may be given as `QueueID`, `StateID`, ... (aliases).
- The customer interface never shows `Owner`, `Responsible`, `Lock`,
  `PendingTime`, `CustomerID`; it writes as the customer panel user, forces the
  acting customer's `CustomerID`/`CustomerUser` and the article sender type
  `customer`.
- `Article`: needs Subject and Body; channel from `Config.CommunicationChannel`
  (`Internal`, `Email`, `Phone`), visibility from `Config.IsVisibleForCustomer`
  (default 0).
- `RequiredLock: 1` (agents): a ticket locked by someone else is refused; an
  unlocked one is locked and taken over before the fields are applied.
- The form is built by the server (`GET .../start-form`, `GET
  .../ticket/{id}/dialog/{eid}/form`) with options already filtered by ACLs
  and queue permissions, and rendered by the shared form component in the
  agent interface and the portal.

## PendingTime

`field_values["PendingTime"]` is an ISO 8601 datetime with offset (like
`pending_time` in `PATCH /tickets/{id}`). It is applied after `State`
when the resulting state is a pending state (submitted State, else the
dialog's `State` default, else the ticket's current state), writing
`until_time` and a `SetPendingTime` history row. For other states it is
ignored; an invalid value is a 400; with `Display: 2` and a pending state it is
mandatory. The `PendingTimeDiff` of a `TicketStateSet` action is honoured too.

## Smart tags

`<OTRS_...>` tags are substituted like Znuny:

- In every TransitionAction `Config` (top-level string values): ticket
  attributes (`<OTRS_TICKET_Queue>`), dynamic fields
  (`<OTRS_TICKET_DynamicField_X>` stored value, `..._X_Value>` display value),
  first/last article data, then the full template placeholders (customer and
  agent data, last customer/agent article). Unknown tags render `-` where
  Znuny does.
- In conditions of the types `Equal`, `NotEqual`, `Contains`, `NotContains`
  and the ordered compares: in the field name, the data value and `Match`.
  `String` and `Regexp` compare raw, as in Znuny.

The `<OTRS_TICKET_DynamicField_*>` tags now also work in email templates and
notifications.

## New process ticket

Agents: "Neues Ticket" menu, entry **"Prozess starten"**, then the page
"Neues Prozessticket" (`/agent/process/new`). Customers: link "Prozess
starten" on the ticket list (`/portal/process/new`). Both call `POST
/api/v1/process/tickets` resp. `POST /api/portal/process/tickets`.

The ticket is created like Znuny's `AgentTicketProcess`: hidden Queue/State/
Priority/Lock take the `Process::Default*` values, the owner is set after the
create, both process dynamic fields are set, the title defaults to
`<Process name> - <time>` (or the submitted subject) with smart tags, and the first
matching transition runs immediately. Customer tickets are created in a queue
the customer may create tickets in (`CustomerGroupSupport`), and Service/SLA lists
are narrowed to the customer user's services.

**Enroll** (existing ticket): the zoom "Prozess starten" dialog now has two
steps (pick a process, fill the start dialog) and goes through the start
dialog like Znuny (`POST /process/ticket/{id}/start` takes `field_values` and
validates mandatory fields).

## REST API

All endpoints live under `/api/v1/process` (`backend/src/tiqora/api/v1/process.py`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/process/` | Processes the caller may start (state, interface, ACL; optional `ticket_id`) |
| GET | `/api/v1/process/{process_entity_id}/start-form` | Server-built start dialog form |
| GET | `/api/v1/process/ticket/{ticket_id}/dialog/{activity_dialog_entity_id}/form` | Server-built form of a dialog on a ticket |
| POST | `/api/v1/process/tickets` | Create a new process ticket |
| GET | `/api/v1/process/{process_entity_id}` | Process detail: activities, their dialogs, outgoing transitions |
| GET | `/api/v1/process/activity-dialog/{activity_dialog_entity_id}` | One activity dialog's fields/order/permission |
| GET | `/api/v1/process/ticket/{ticket_id}/state` | A ticket's current process/activity + available dialogs |
| POST | `/api/v1/process/ticket/{ticket_id}/start` | Enroll a ticket in a process (start dialog `field_values`) |
| POST | `/api/v1/process/ticket/{ticket_id}/submit` | Submit an activity dialog (advance the ticket) |

The portal has the same set under `/api/portal/process/*` (customer
interface dialogs only). Admin: `GET /api/v1/admin/processes` (all processes of
the deployed revision) and `GET /api/v1/admin/deploy-status`.

All ticket-scoped endpoints apply the same coarse queue `ro`/`rw`
permission gate as the rest of the ticket REST API (see the module
docstring in `api/v1/process.py`).

## Frontend

- **Ticket zoom**: `ProcessWidget` (`frontend/src/components/agent/process/ProcessWidget.tsx`)
  shows a ticket's current process/activity and available next dialogs;
  `StartProcessDialog` lets an agent attach a process to a ticket not yet
  in one; `ActivityDialogModal` renders a dynamic form from an activity
  dialog's `Fields`/`FieldOrder` and submits it.
- **New process ticket**: pages `NewProcessTicketPage` (agent and portal);
  zoom dialogs render the shared `ProcessDialogForm`.
- **Admin**: read-only `/admin/processes` list (`routes/admin/ProcessesPage.tsx`)
  and detail view (`routes/admin/ProcessDetailPage.tsx`) — browse processes,
  activities, dialogs, and transitions.
- **Customer portal**: `PortalProcessWidget` + `/api/portal/process/*`
  expose start/submit for dialogs whose YAML ``Interface`` includes
  ``CustomerInterface`` (Znuny parity). Writes use system user id 1 (same
  as other portal ticket writes), ACLs run as the customer.
- **Out of scope**: there is no visual process *designer*. Processes must
  still be authored via Znuny's admin UI (`AdminProcessManagement`) or by
  writing directly to the `pm_*` tables / their YAML `config` columns.

## Supported vs deferred

### Condition types (`Fields.*.Type`)

| Supported | Deferred (treated as non-matching) |
|---|---|
| `String` (case-sensitive `eq`) | `Module`-based custom conditions |
| `Regexp` | |
| `Contains` / `NotContains` (case-insensitive regex) | |
| `Equal` / `NotEqual` (case-insensitive) | |
| `GreaterThan` / `GreaterThanOrEqual` (also `GreaterThanEquals`) | |
| `LessThan` / `LessThanOrEqual` (also `LessThanEquals`) | |

Ordered compares follow Znuny `TransitionValidation::Base`: DateTime/Date
values are converted to epoch seconds (`ValueValidate`), then both sides
must be integers (`IsInteger`) or the condition does not match.

An unsupported condition type logs a warning and evaluates to `False` — it
never raises. See `_evaluate_field` in `engine.py` for the full semantics
(each type's Znuny-source-verified matching rule).

### TransitionAction modules (`Module`'s last `::`-segment)

| Supported | Deferred (logged, no-op — collected into `unsupported_actions`) |
|---|---|
| `TicketStateSet` | `ExecuteInvoker` |
| `TicketQueueSet` | `Appointment*` (Create/Update/Remove) |
| `TicketOwnerSet` | `ConfigItemUpdate` |
| `TicketPrioritySet` | |
| `TicketTitleSet` | |
| `TicketCustomerSet` | |
| `TicketResponsibleSet` | |
| `TicketLockSet` | |
| `DynamicFieldSet` | |
| `DynamicFieldRemove` | |
| `DynamicFieldIncrement` | |
| `DynamicFieldPendingTimeSet` | |
| `TicketArticleCreate` | |
| `ArticleSend` | (agent email via outbound reply path) |
| `TicketTypeSet` | |
| `TicketServiceSet` | |
| `TicketSLASet` | |
| `TicketWatchSet` | |
| `LinkAdd` | |
| `TicketCreate` | (Title/Queue/State/Priority/…; optional `LinkAs` to parent) |

An unsupported action does not abort the submission: the transition still
fires and the activity still advances, but that one action is skipped and
its short module name is reported in `ActivityDialogSubmitResult.
unsupported_actions` so callers/tests can assert on skip behaviour.

### Other simplifications (see `engine.py`'s module docstring for the authoritative list)

- Transitions are evaluated in `Path`'s YAML/dict insertion order; Znuny
  itself has no explicit tiebreak either.
- `pm_process.state_entity_id` is exposed raw plus a `state_name`
  (Active / Inactive / FadeAway, see "State gate").
- A ticket can only be in one process at a time (`TicketAlreadyInProcess`).
- Dialog field options do not refresh when another field changes (Znuny's AJAX
  update). `Attachments` in dialog articles are not supported.
- No global toast/notification system — the process widgets show inline
  success/error state.

## Known limitations

Deployment:

- Znuny's `ProcessImport` writes no `pm_entity_sync` rows, so an import looks
  like "import + deploy". Tiqora picks it up at the next watch tick even if
  nobody pressed "Bereitstellen".
- An edit made within one watch interval (default 30 s) after a Znuny deploy
  hides that deploy (the sync table is never seen empty); Tiqora catches up
  with the next deploy.
- There is no deploy button and no process designer in Tiqora yet; after
  cutover Tiqora deploys automatically.

Engine:

- Znuny evaluates transitions after **every** ticket change (event module
  `TicketProcessTransitions`). Tiqora evaluates them after process dialogs and
  new process tickets only; changing a ticket in the normal zoom does not
  move it to the next activity.
- `PendingTimeDiff` with an unchanged state: Znuny writes only
  `SetPendingTime` (when the state type is pending); Tiqora also writes a
  state-update history row.
- Fixed field order in the engine (State, Queue, Title, Responsible,
  PendingTime), not `FieldOrder`: only the history row order differs.
- Condition data lacks `UntilTime`, `Escalation*`, `GroupID`,
  `CreateBy`/`ChangeBy` and `Age`.
- List values of dynamic fields are not available in the title smart-tag
  context.
- A nonexistent numeric Owner/Queue id in a forged request gives a 500 instead of a 400.
- The start permission is checked as `rw` on the queue, not by the start
  dialog's own `Permission`.
- Dialog articles: customer dialogs write history `AddNote` (Znuny:
  `WebRequestCustomer` / `FollowUp` / `PhoneCallCustomer`); an agent dialog
  article with `CommunicationChannel Email` is derived as `EmailAgent`
  (Znuny writes `AddNote`).
- Clearing Service or SLA in a dialog (the field empty) does not clear the
  ticket value (Znuny does); other empty ticket attributes are ignored in both.
- The agent form keeps the ticket's `CustomerID` when only the customer user
  changes (no prefill of `CustomerID` from the customer user).

ACLs and pickers:

- The action ACLs hide the zoom menu entries; the server refuses only the
  process action and `AgentTicketZoom`. `PATCH /tickets/{id}` is not checked
  against several Znuny actions.
- Per-article reply and forward buttons and the note composer are not gated by
  the zoom ACL (only the header "Antworten" and the menu are).
- The start form's owner list is not narrowed to "My Queues" subscribers
  (Znuny `MyQueues` filter) and `GET /api/v1/reference/agents` stays unscoped;
  other owner pickers use the scoped lists from the field-options endpoints.
- Bulk owner scoping only considers the first 500 selected tickets (the UI
  shows a notice).
- Customers: the queue list is limited to the queues the customer may create
  tickets in, but company groups (`group_customer`) are not built; Service/SLA
  lists are filled without a selected queue/service (Znuny returns an empty list;
  SLA = union over the customer's services).

Deviations that are deliberate:

- The customer queue is checked server-side only when `Queue` is a visible
  field of the dialog (hidden default queues are allowed). This is stricter
  than Znuny, which never validates a submitted queue.
- Forged Service/SLA values are as permissive as in Znuny (looked up, not
  checked against the customer's services).
- Postmaster, Raw and Junk are hidden from customer queue lists (Znuny shows
  them when `users` is in `CustomerGroupAlwaysGroups`); the server does not
  reject them.
- Znuny 7.x: the stored YAML is identical to 6.5 (no `SequenceFlow` rename);
  the only addition is the table `pm_process_preferences` (7.2+), which is
  carried in the snapshot.

## See also

- `docs/architecture.md` — ProcessManagement subsection (module list,
  table reuse, one-line engine flow) alongside the rest of Tiqora's
  architecture.
- `backend/src/tiqora/process/engine.py` — the authoritative source for
  every simplification listed above (each is documented at its point of
  implementation, not just in this doc).
