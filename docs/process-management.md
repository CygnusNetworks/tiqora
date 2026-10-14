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
| `process/designer/` | The process designer backend: ids, YAML codec, draft writer, validation, import/export, revisions (see "Process designer") |
| `process/actions/registry.py` | `TRANSITION_ACTIONS`: handler plus designer parameter specs per TransitionAction module |
| `process/condition_modules.py` | `CONDITION_MODULES`: Python re-implementations of `Type: Module` conditions |
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
  undeployed process edit). Tiqora reads it, and clears it when it deploys
  itself (after cutover, or with "Bereitstellen" in Tiqora; see "Deployed
  revisions" below)
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
- The worker loop `deploy_watch` (service "Bereitstellung beobachten" on the
  "Dienste" page, every `TIQORA_DEPLOY_WATCH_INTERVAL_SECONDS`, default 30)
  decides per kind (process, ACL); the tick result names the action:

  | Situation | Action | Result |
  |---|---|---|
  | No revision yet, sync table empty | `snapshot` | records revision `initial` ("beim Start") |
  | No revision yet, drafts pending | `running_live` | keeps reading the live rows (warning on "Dienste"); one "Bereitstellen" fixes it |
  | Revision exists, drafts pending | `waiting_for_deploy` | waits; the old revision keeps running |
  | Sync table empty, dump differs from the newest revision, first tick | `confirming` | notes the new checksum only (a designer edit may have started meanwhile) |
  | Same changed dump on the next tick | `snapshot` | records revision `znuny` ("aus Znuny") |
  | Sync table empty, dump equal | `unchanged` | nothing |
  | Tiqora owns the schema (after cutover), drafts pending | `waiting_for_deploy` | drafts never run; only "Bereitstellen" in Tiqora clears the sync rows |
  | Tiqora owns the schema (after cutover), sync table empty, dump differs | `snapshot` | records revision `tiqora` ("von Tiqora") without the two-tick confirmation (an import or a direct DB edit) |

- **"Bereitstellen" in Tiqora.** `POST /api/v1/admin/deploy` (`{"kind":
  "process" | "acl"}`, admins only) does Znuny's deploy DB effect: it records
  the current `pm_*` / `acl` rows (drafts included) as revision `tiqora` and
  clears `pm_entity_sync` / `acl_sync`. When nothing changed it records no
  revision and only clears the sync rows (`"action": "unchanged"`). It is
  allowed in one of three modes (`deployed/gate.py`, checked in this order):
  Tiqora owns the schema (`local`); a fresh TiqoraSync heartbeat with the
  deploy capability (`signal`, see "Deploy in parallel operation"); or the
  setting `process.deploy_from_tiqora` is on (`local`) ("Einstellungen", section "Prozesse und
  ACLs": "Prozesse und ACLs in Tiqora bereitstellen (Znuny läuft nicht mehr)");
  otherwise it answers 409. In `local` mode it records the revision and clears
  the sync rows itself. It answers 503 when another deploy holds the
  revision lock. The "Bereitstellung" card shows the button "Bereitstellen"
  when that gate is open and changes are pending. Only switch the setting on
  when Znuny no longer runs: Znuny keeps its own deployed file
  (`ZZZProcessManagement.pm` / `ZZZACL.pm`) and would go on showing the old
  state. After cutover the button is the only way to deploy: edits stay
  drafts until someone presses "Bereitstellen" (the watcher never deploys
  drafts).
- Every engine call reads the newest revision id and checksum (one indexed
  `SELECT`); the stored JSON is read again only when that pair changed. Each
  caller gets its own parsed copy, so a caller cannot change the deployed state
  for others. This works across API replicas and worker processes without
  pub/sub (no Redis).
- The admin pages for processes and ACLs show a card "Bereitstellung"
  ("Bereitgestellt am ..."), the number of undeployed changes and a warning
  when Tiqora still runs the draft state. `GET /api/v1/admin/deploy-status`
  (including `can_deploy_from_tiqora`) and `GET /api/v1/admin/processes` back
  them.

## Deploy in parallel operation (signal mode)

While Znuny still runs, "Bereitstellen" in Tiqora must make **Znuny** run the
new state too, otherwise the two systems would execute different process
definitions. That needs the Znuny add-on **TiqoraSync 1.2.0** or newer:

1. TiqoraSync writes a heartbeat (`tiqorasync.heartbeat`, once a minute) with
   its version and the capabilities `process_deploy` / `acl_deploy`. Tiqora
   offers a signal deploy only when the heartbeat is younger than 10 minutes
   (it proves that the Znuny daemon is alive). A heartbeat beats the setting
   `process.deploy_from_tiqora`.
2. Tiqora records the revision (source `tiqora`) and queues a signal row in
   `tiqora_cache_invalidation`, remembering its id per kind.
3. On its next run TiqoraSync does what Znuny's own Deploy button does: it
   writes `ZZZProcessManagement.pm` / `ZZZACL.pm` and resets the sync state
   (`pm_entity_sync` / `acl_sync`).
4. TiqoraSync reports `ok` or `error` (with the message) under the signal
   row id. The card "Bereitstellung" shows "Wartet auf Znuny ..." until a
   status with a signal id at least as new as the request arrives, then
   "In Znuny übernommen" or the error. An `error` banner stays until the
   problem is fixed and the deploy is requested again: **Znuny keeps running
   the previous version until then**, while Tiqora already runs the new one.
   If Znuny does not react within a few minutes the card warns that the
   daemon may be down.

Without a fresh heartbeat (add-on missing or older than 1.2.0, daemon down)
and without ownership or the setting, "Bereitstellen" answers 409 with the
reason. Install notes for the add-on:
[`packages/znuny-addon/TiqoraSync/install/README.md`](../packages/znuny-addon/TiqoraSync/install/README.md).

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
  (`and`/`or`/`xor` across blocks; falls back to `Condition.Type`, then `and`)
  and `Condition` — a map of numbered condition blocks, each with its own
  `Type` (`and`/`or`/`xor` across that block's fields) and a `Fields` map.
  A field entry is `{Type, Match}`, a plain scalar (a `String` compare) or a
  list (an `Array` check: every listed value must be present). A missing or
  empty `Condition` is a configuration error, as in Znuny
  (`Transition.pm:321`): it is logged and **no** transition of that activity
  fires. A `Condition` that holds only `Type`/`ConditionLinking` and no
  blocks matches when the linking is `and`.
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
   b. Check the state gate, the dialog's `Interface`, the `Action` and
      `ActivityDialog` ACLs and the dialog `Permission`
      (`availability.assert_can_submit`): agents need the dialog's
      `Permission` on the ticket's queue when the dialog sets one; customers
      need it (default `rw`) through Znuny's GroupCheck.
   c. Validate required fields (`Display: 2`) are present in `field_values`;
      with `RequiredLock` (agents) lock the ticket first.
   d. Apply the submitted field changes to the ticket (title, queue,
      state, priority, type, service, SLA, lock, owner, responsible,
      customer, pending time, article, dynamic fields — reusing `ticket_write_service` functions, so each change
      writes its normal Znuny history row).
   e. Evaluate the current activity's outgoing transitions in the order of
      their entity ids (`Process.pm:442-445`) against freshly read ticket
      data; the **first** whose `Condition` matches wins. A configuration
      error in any of them (see "Config YAML shapes") stops the check: no
      transition fires.
   f. Set `ProcessManagementActivityID` to the transition's target
      activity (`Process.pm:501`), so this history row comes first.
   g. Run the matched transition's `TransitionAction`s in order (smart tags
      in their `Config` are substituted first; `Process.pm:558ff`).
   h. If no transition matches, the submission still succeeds — the
      ticket simply stays on its current activity
      (`activity_changed=False`).

Condition evaluation lives in `process/conditions.py` (`check_transition`,
`_evaluate_field`); action dispatch is `execute_transition_action` in
`engine.py`. Tests: `backend/tests/test_process_conditions.py` and
`test_process_engine_conditions.py` (pure logic), `test_process_engine.py`
(DB integration, including matching vs non-matching conditions and an
unsupported action).

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
  the ticket has none). An unchanged Queue, State, Priority, Type, Service, SLA,
  Title, Lock, Owner, Responsible, customer or dynamic field writes no history
  and does not touch `change_time`, as in Znuny. Dynamic fields can be cleared
  by sending an empty list.
- `Queue`, `State`, `Lock`, `Priority` with `Display: 1` are a Znuny config
  error; on a new ticket they fall back to `Process::DefaultQueue/State/Lock/Priority`.
- Field names may be given as `QueueID`, `StateID`, ... (aliases).
- The customer interface never shows `Owner`, `Responsible`, `Lock`,
  `PendingTime`, `CustomerID`; it writes as the customer panel user, forces the
  acting customer's `CustomerID`/`CustomerUser` and the article sender type
  `customer`.
- `State` offers only the state types of
  `Ticket::Frontend::AgentTicketProcess###StateType` (default new, open,
  pending auto, pending reminder, closed) resp.
  `Ticket::Frontend::CustomerTicketProcess###StateType` (default new, open),
  like Znuny's `TicketStateList(Action => ...)`.
- `Article`: needs Subject and Body; channel from `Config.CommunicationChannel`
  (`Internal`, `Email`, `Phone`), visibility from `Config.IsVisibleForCustomer`
  (Perl truthiness: `'0'` is internal; default 0), never from the request.
  History type like Znuny: agents `AddNote` (`PhoneCallAgent` for Phone),
  customers `WebRequestCustomer` on a new ticket and `FollowUp` otherwise
  (`PhoneCallCustomer` for Phone).
- `RequiredLock` (agents, Perl truthiness): a ticket locked by someone else is
  refused; an unlocked one is locked and taken over before the fields are
  applied.
- The form is built by the server (`GET .../start-form`, `GET
  .../ticket/{id}/dialog/{eid}/form`) with options already filtered by ACLs
  and queue permissions, and rendered by the shared form component in the
  agent interface and the portal.

### Working with typed dialogs (agents)

A dialog on a ticket (panel "Prozess", process) and the start dialogs render as
a typed form: every field has the widget of its type. UI labels are German by
default; the English gloss is in brackets.

- **"Bisheriger Weg" (path so far).** Below the current step, the process panel
  has a collapsible "Bisheriger Weg" toggle. It lists the steps the ticket has
  taken through the process, oldest first: step name, time, "von {name}" (by
  whom) and a badge "Aktuell" (current) on the active step. A step that no
  longer exists in the deployed process shows "(gelöscht)" (deleted); a ticket
  that has not moved yet shows "Noch keine Schritte aufgezeichnet." (no steps
  recorded). The path is read from the ticket history (at most 200 steps, since
  the latest start of the process on this ticket) and loaded only when the
  toggle is opened.
- **Dependent selects (option refresh).** When a field changes that a select
  depends on, the form asks the server again for that select's options after
  a pause of about 0.3 s and shows "Auswahl wird aktualisiert …" (options are
  being refreshed). A failed refresh keeps the options the field already had.
  Nothing is requested when the form opens.
- **Buttons and errors.** The submit button shows the dialog's own button text
  if the process defines one, otherwise "Absenden" (submit); "Abbrechen"
  cancels. A mandatory field is marked `*` and, if empty, says "Dieses Feld ist
  erforderlich."; "Bitte prüfen Sie die Eingaben. Ein Pflichtfeld fehlt oder
  ein Wert ist ungültig." means the server rejected the values.
- **"Dateien" (files for the article).** A dialog with an `Article` field has an
  attach button, with the same limits as the reply dialog (up to 10 files;
  "Die Dateien sind zu groß." if they exceed the size limit).
- **"Anhänge aus dem Ticket" (existing attachments).** The dialog field
  `Attachments` lets an agent pick attachments that already exist on the
  ticket's articles; the actions `ArticleSend` and `TicketArticleCreate` then
  mail or store them. The field exists for agents on an existing ticket only
  (not on a start dialog, not in the customer portal). "Das Ticket hat keine
  Anhänge." appears if there is nothing to choose from.
- **"Zeitaufwand" (time units).** A time field for the article, shown to agents
  only while the SysConfig `Ticket::Frontend::AccountTime` is on and the
  dialog's `Article` field config sets `TimeUnits` (`2` = mandatory, other
  truthy values = optional). The value is booked as time accounting on the new
  article. The customer portal never shows it.
- A field type the form cannot render shows "Dieses Feld kann hier noch nicht
  ausgefüllt werden." (cannot be filled in here yet) or a plain text input with
  a note.

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
`<Process name> - <time>` (or the submitted subject; agents get smart tags
rendered in it, customers do not, as in `CustomerTicketProcess`), and the first
matching transition runs immediately. Customer tickets are created in a queue
the customer may create tickets in (`CustomerGroupSupport`), and Service/SLA lists
are narrowed to the customer user's services.

**Enroll** (existing ticket): the zoom "Prozess starten" dialog now has two
steps (pick a process, fill the start dialog) and goes through the start
dialog like Znuny (`POST /process/ticket/{id}/start` takes `field_values` and
validates mandatory fields).

## Process designer

`/admin/processes` lists the **draft** state (a process with an undeployed
edit, of its own or of one of its steps, forms, transitions or actions,
carries the badge "Entwurf"); a process opens in the designer at
`/admin/processes/<id>`. Concepts use Znuny's words: a process has *Schritte*
(activities), *Formulare* (activity dialogs), *Übergänge* (transitions)
and *Aktionen* (transition actions). Desktop only: on a phone the designer is
read-only.

- **Canvas and panels.** Steps and transitions are drawn on a canvas
  (positions are stored in `pm_process.layout` in Znuny's format, so Znuny's
  own canvas shows them too). Panels edit the process, a step (its forms in
  order), a form (typed fields, `Display` hidden / shown / required, default
  values, interface, permission), a transition (condition blocks with
  and/or/xor and typed values) and an action (a typed parameter form per
  module, raw key/value for unknown modules). Delete asks for confirmation.
  `Escape` discards the unsaved edits of a panel. The top bar shows the
  process state and links the export.
- **Shared entities.** Steps, forms, transitions and actions are Znuny
  entities that several processes can use. "Vorhandene verwenden" (palette,
  "add an existing form", the connect popover and "add an existing action")
  lists them from all processes (`GET .../entities/{type}` with `used_by`);
  a picked one is loaded with `GET .../entities/{type}/{id}`. A panel of a
  shared entity says which other processes (or, for a form, steps) use it,
  because an edit changes them as well. Entries that nothing uses any more
  are listed under "Nicht mehr verwendet" in the palette and can be deleted
  there; an entry still in use cannot be deleted (409 names the users).
- **Drafts.** Every save writes `pm_*` rows exactly like Znuny's
  `ProcessManagement::DB::*` modules (entity ids `<Type>-<32 hex>`, YAML
  `config`, `create_by` / `change_by`) and a `pm_entity_sync` row. The
  change is a *draft*: the engine keeps running the last deployed revision, a
  draft started in Tiqora shows as a draft in Znuny's designer and vice
  versa. Concurrent edits are detected by comparing the stored version
  (`config` and name) in the same statement; the loser gets a conflict, not a
  silent overwrite.
- **Bereitstellen.** Records the draft state as a revision and, depending on
  the mode, clears the sync rows itself (after cutover, or with
  `process.deploy_from_tiqora`) or hands the deploy to Znuny through
  TiqoraSync 1.2.0 (see "Deploy in parallel operation"). Validation never
  blocks a deploy (Znuny does not validate either); with errors the button
  asks for confirmation.
- **Prüfung (validation).** `GET .../validation` returns issues with a code,
  severity and the entity they belong to. Errors: `start_activity_missing`,
  `start_dialog_missing`, `start_dialog_not_in_start_activity`,
  `reference_missing`, `transition_target_missing`, `dialog_no_interface`,
  `action_reference_missing`, `action_required_param_missing`,
  `condition_value_impossible` (value cannot match: bad regexp, unknown
  queue/state name, value outside the possible values of a dynamic field).
  Warnings: `activity_unreachable`, `activity_dead_end`, `dialog_no_fields`,
  `dialog_field_unknown`, `queue_required_display`,
  `transition_without_condition`, `condition_type_unknown`,
  `condition_module_unknown`, `action_module_unsupported` (known to Znuny but
  not run by Tiqora: "nur in Znuny wirksam") and `tiqora_only_module_parallel`.
- **Import / export.** Export downloads a process as Znuny's YAML (the format
  of `AdminProcessManagement` export, selected entities included). Import
  accepts that file from Znuny (6.0 to 7.3) as a *draft*, optionally
  overwriting existing entities; unlike Znuny's `ProcessImport` it writes
  `pm_entity_sync` rows, so an imported process never runs undeployed and
  Znuny shows its usual "please deploy" banner. Limits: 1 MB, 1 000
  entities, no YAML aliases or anchors; entity ids are at most 50 characters.
- **Versions (Fassungen).** Every deployed revision is listed with its source
  (`initial`, `znuny`, `tiqora`). Two versions can be compared per entity
  (and as a text view). *Wiederherstellen* writes the old entities back as a
  **draft** (it does not redeploy), keeps the canvas positions of activities
  that still exist, and the normal deploy follows. The restored state
  reproduces the deployed checksum.
- **Extension points.** `TRANSITION_ACTIONS` (module name to handler plus
  parameter specs) and `CONDITION_MODULES` (Perl module path to a Python
  predicate; `ValidateDemo` ships) are registries in the core. Both are
  `sdk_stable=True` and, since SDK 0.2.0, plugins can add to them
  (`backend/src/tiqora/plugins/process_points.py`): a plugin registers
  `transition_action(...)` and `condition_module(...)`, and they appear in the
  designer as `Plugin_<short_id>_<Name>`. A plugin action runs in a savepoint
  within a shared 20 s budget per dialog submit; when a plugin vetoes the ticket
  write an action makes, only that action is skipped (see `skipped_actions`
  below). See
  [plugins.md](plugins.md#process-transition-actions-and-condition-modules).
  The designer's action forms are generated from the parameter specs, so an
  action module added to a registry shows up in the designer without frontend
  work.
- **API.** `/api/v1/admin/process-designer/*` (admin only, see the OpenAPI
  document): `catalog`, `processes`, `entities/{type}`,
  `entities/{type}/{id}`, per-process
  `validation`, `export`, `import`, `revisions`, `revisions/{id}/diff` and
  `revisions/{id}/restore`.

## REST API

All endpoints live under `/api/v1/process` (`backend/src/tiqora/api/v1/process.py`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/process/` | Processes the caller may start (state, interface, ACL; optional `ticket_id`) |
| GET | `/api/v1/process/{process_entity_id}/start-form` | Server-built start dialog form |
| GET | `/api/v1/process/ticket/{ticket_id}/dialog/{activity_dialog_entity_id}/form` | Server-built form of a dialog on a ticket |
| POST | `/api/v1/process/tickets` | Create a new process ticket |
| GET | `/api/v1/process/{process_entity_id}` | Process detail: activities and their dialogs (no transitions) |
| GET | `/api/v1/process/activity-dialog/{activity_dialog_entity_id}` | One activity dialog's fields/order/permission |
| GET | `/api/v1/process/ticket/{ticket_id}/state` | A ticket's current process/activity + available dialogs |
| POST | `/api/v1/process/ticket/{ticket_id}/start` | Enroll a ticket in a process (start dialog `field_values`) |
| POST | `/api/v1/process/ticket/{ticket_id}/submit` | Submit an activity dialog (advance the ticket) |

The portal has the same endpoints except the process detail under
`/api/portal/process/*` (customer interface dialogs only). The server also
offers `POST /api/v1/process/{id}/start-form/options` (and the ticket
variant, agent and portal) to refresh dialog field options after another
field changed, and `GET /api/v1/process/ticket/{id}/path` for the path so far.
Admin: `GET
/api/v1/admin/processes` (all processes of the deployed revision), `GET
/api/v1/admin/deploy-status` and `POST /api/v1/admin/deploy` (see "Deployed
revisions").

All ticket-scoped endpoints apply the same coarse queue `ro`/`rw`
permission gate as the rest of the ticket REST API (see the module
docstring in `api/v1/process.py`).

## Frontend

- **Ticket zoom**: `ProcessWidget` (`frontend/src/components/agent/process/ProcessWidget.tsx`)
  shows a ticket's current process/activity and available next dialogs;
  `StartProcessDialog` lets an agent attach a process to a ticket not yet
  in one; `ActivityDialogModal` renders the server-built dialog form
  (`GET .../ticket/{id}/dialog/{eid}/form`) and submits it.
- **New process ticket**: pages `NewProcessTicketPage` (agent and portal);
  zoom dialogs render the shared `ProcessDialogForm`.
- **Admin**: `/admin/processes` list (`routes/admin/ProcessesPage.tsx`) with
  the "Bereitstellung" card and create / import / export / copy / delete, and
  the designer (`routes/admin/process-designer/`, see "Process designer").
- **Ticket zoom path**: the process widget shows "Bisheriger Weg" (the path
  so far) next to the current step.
- **Customer portal**: `PortalProcessWidget` + `/api/portal/process/*`
  expose start/submit for dialogs whose YAML ``Interface`` includes
  ``CustomerInterface`` (Znuny parity). Writes use system user id 1 (same
  as other portal ticket writes), ACLs run as the customer.

## Supported vs deferred

### Condition types (`Fields.*.Type`)

| Supported | Deferred |
|---|---|
| `String` (case-sensitive `eq`; also a plain scalar entry) | |
| `Module` (`Match` = Perl module path; registered Python re-implementations, today `ValidateDemo`) | Unknown modules: logged, never match (designer warning `condition_module_unknown`) |
| `Regexp` | |
| `Array` (list entry: every value present) | |
| `Hash` (`Match` is a hash: every key equal) | |
| `Contains` / `NotContains` (case-insensitive regular expression) | |
| `Equal` / `NotEqual` (case-insensitive strings) | |
| `GreaterThan` / `GreaterThanOrEqual` | |
| `LessThan` / `LessThanOrEqual` | |

Type names are case-sensitive, as in Znuny; there are no aliases. Linking and
block types are `and`, `or` and `xor`. Ordered compares follow Znuny
`TransitionValidation::Base`: DateTime/Date values are converted to epoch
seconds (`ValueValidate`), then both sides must be integers (`IsInteger`) or
the condition does not match. Regular expressions run with a timeout; a
timeout counts as no match.

Any other condition type (and a field entry that is a hash without `Type`, a
block without `Fields` or an unknown linking) is a configuration error: it is
logged and **no** transition of the activity fires, as in Znuny's
`TransitionCheck`. See `process/conditions.py` for each type's matching rule
with its Znuny source lines.

### TransitionAction modules (`Module`'s last `::`-segment)

| Supported | Deferred (logged, no-op — collected into `unsupported_actions`) |
|---|---|
| `TicketStateSet` | |
| `TicketQueueSet` | |
| `TicketOwnerSet` | |
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
| `ConfigItemUpdate` (Tiqora CMDB; see the note below) | |
| `TicketWatchSet` | |
| `LinkAdd` | |
| `AppointmentCreate` / `AppointmentUpdate` / `AppointmentRemove` | |
| `TicketCreate` | (Title/Queue/State/Priority/…; optional `LinkAs` to parent) |
| `ExecuteInvoker` | Handler of the GenericInterface requester (`tiqora/gi/process_action.py`): queues the invoker call as a background job in the dialog submit's transaction. `Webservice` is the webservice **name**; `Asynchronous` is accepted and ignored (the designer notes "läuft immer im Hintergrund"). Works without the requester takeover flag; see [compatibility.md](compatibility.md#deviations-from-znuny), deviation 8 |

`ConfigItemUpdate` changes CIs of the Tiqora CMDB as the acting user (`UserID` overrides), without a class permission check, like Znuny. It acts only in ITSM mode `tiqora`; in any other mode it is logged, collected into `unsupported_actions`, and the designer warns (`action_module_inactive_in_mode`). A CI that cannot be updated (unknown number or state, invalid value) is logged and skipped; the transition always fires.

`LinkAdd` is a port of Znuny's `LinkAdd`: `SourceObject` / `SourceKey` /
`TargetObject` / `TargetKey`, `Type` (default `Normal`), `State` (`Valid` or
`Temporary`) and `UserID` (overrides the acting user); without them it links
the current ticket to `TargetTicketID`. When one end is an `ITSMConfigItem`, the
action follows the ITSM mode (see [itsm.md](itsm.md)):

| ITSM mode | Link with a CI end |
|---|---|
| `tiqora` (Mode B) | Stored by Tiqora's CMDB link service as the process actor, under the same rule as the UI: `rw` on the ticket's queue and `ro` on the CI's class (CI to CI needs `rw` on both classes). The other end can be a `Ticket` or a `Service`; the link type must exist. |
| `znuny_readthrough` | Stored as a Znuny `link_relation` row, like any other link object. |
| `off` | Skipped (`itsm_off`); no link is created. |

A link between two non-CI objects (for example two tickets) does not depend on
the ITSM mode. A refused link never stops the transition: as in Znuny, the
refusal is logged and the process goes on.

Two lists tell callers which actions did not run on a dialog submit. Both are
part of `ActivityDialogSubmitResult` and of the submit response of the agent
and portal APIs. Neither aborts the submission: the transition still fires and
the activity still advances.

- `unsupported_actions`: the short module names of actions Tiqora does not run
  (deferred modules, or `ConfigItemUpdate` outside ITSM mode `tiqora`).
- `skipped_actions`: actions Tiqora supports that did not take effect on this
  pass. Each entry has `module` (short name), `reason` (a stable code) and
  `detail` (a German text for the agent). Every action runs in its own
  savepoint, so a skipped action leaves nothing behind. Reasons: `vetoed` (a
  plugin refused the ticket write the action makes), `itsm_off` (`LinkAdd` to
  a CI while ITSM is off) and `link_refused` (`LinkAdd` to a CI that the CMDB
  link service refused). The customer portal returns `module` and `reason`
  only and leaves `detail` empty, because it can hold plugin veto texts and
  internal reasons. The agent UI does not show the list; use it from API
  clients and tests.

### Other simplifications (see `engine.py`'s module docstring for the authoritative list)

- `pm_process.state_entity_id` is exposed raw plus a `state_name`
  (Active / Inactive / FadeAway, see "State gate").
- A ticket can only be in one process at a time (`TicketAlreadyInProcess`).
- Dialog field options are re-asked from the server when another field
  changes; this does not reproduce Znuny's per-dialog `AJAXUpdatableFields`
  list field by field.
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
- "Bereitstellen" in Tiqora is gated (see "Deployed revisions" and "Deploy in
  parallel operation"); after cutover edits in the designer and the ACL
  editor stay drafts until someone presses "Bereitstellen" in Tiqora.
- Edits between a Tiqora deploy and TiqoraSync's dump (about one minute) are
  deployed in Znuny as well; Tiqora picks them up within two watch intervals
  as a `znuny` revision.
- TiqoraSync writes `ZZZProcessManagement.pm` / `ZZZACL.pm` only on the host
  that runs the Znuny daemon. Multi-node Znuny setups with separate web nodes
  need a shared `Kernel/Config/Files` directory, as Znuny's own button does.
- Znuny itself has a short window in which an edit made during its own
  deploy looks deployed but is not; the same window exists in Tiqora.
- The designer does not show the ACLs that restrict a process; the ACL editor
  stays separate.
- `Appointment*` actions ignore `Custom*` recurrence, `RecurrenceFrequency`,
  `RecurrenceExclude`, all notification keys and `TicketAppointmentRuleID`;
  there are no appointment notifications.
- A *Probelauf* (test run), visual diff on the canvas and process analytics
  are not built; the version diff is a per-entity list plus a text view.
- On a phone (< 768 px) the designer only lists the steps of the process
  (read-only); the planned list of steps, forms and transitions with the
  panels as full-screen sheets is not built.

Engine:

- Znuny evaluates transitions after **every** ticket change (event module
  `TicketProcessTransitions`). Tiqora evaluates them after process dialogs and
  new process tickets only; changing a ticket in the normal zoom does not
  move it to the next activity.
- `PendingTimeDiff` with an unchanged state: Znuny writes only
  `SetPendingTime` (when the state type is pending); Tiqora also writes a
  state-update history row.
- Fixed field order in the engine instead of `FieldOrder` for the ticket
  attributes: Queue, State, Priority, Type, Service, SLA, Title, Responsible,
  Lock, customer; then dynamic fields, Article and Owner in `FieldOrder`; then
  PendingTime. Only the history row order differs.
- Condition data lacks `UntilTime`, `Escalation*`, `GroupID`,
  `CreateBy`/`ChangeBy` and `Age`.
- List values of dynamic fields are not available in the title smart-tag
  context.
- A nonexistent numeric Owner/Queue id in a forged request gives a 500 instead of a 400.
- The start permission is checked as `rw` on the queue, not by the start
  dialog's own `Permission`.
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
