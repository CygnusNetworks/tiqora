# Process management: full Znuny parity, designer, analytics — design

> Part of the [competitive parity roadmap](2026-10-08-competitive-parity-roadmap-design.md).
> ITSM builds on this document: [ITSM / CMDB design](2026-10-08-itsm-cmdb-design.md).
> Status: proposed 2026-10-08. Decisions below are fixed unless marked "open".

## Goal

Run every process a Znuny 6.0–7.3 site has, author and deploy processes in
Tiqora, and add what neither Znuny nor OTRS has: test runs, versioning with
diff, process analytics, approvals and AI help in designing.

**This reverses a documented non-goal.** `docs/compatibility.md` lists the
process designer as "by design" absent and `docs/process-management.md` says
processes must be authored in Znuny. Both docs change when phase 3 ships.

## Current state (2026-10-08)

- Engine `backend/src/tiqora/process/engine.py` (≈1650 lines) runs
  processes from the shared `pm_*` tables: start on an existing ticket,
  submit activity dialogs (permission gate, required fields), first matching
  transition wins, transition actions.
- Conditions: String, Regexp, Contains/NotContains, Equal/NotEqual,
  Greater/Less(+OrEqual). Missing: `Module` conditions.
- Actions: 20 of Znuny's 25. Missing: `ExecuteInvoker`, `AppointmentCreate`,
  `AppointmentUpdate`, `AppointmentRemove`, `ConfigItemUpdate`.
- No `<OTRS_…>` smart tags in action config or condition values; dialog
  `PendingTime` fields not applied; one process per ticket (same as Znuny).
- **Correctness gaps found in the inventory:**
  - Inactive processes are startable: `state_entity_id` (`S1` Active, `S2`
    Inactive, `S3` FadeAway) is passed through raw.
  - Tiqora runs whatever is in `pm_*` — including **undeployed drafts**.
    Znuny only runs what was deployed into `ZZZProcessManagement.pm`
    (`ProcessDump`), dirty-tracked in `pm_entity_sync`. Same for ACLs
    (`ZZZACL.pm` via `ACLDump`, `acl_sync`); Tiqora's admin ACL delete does
    not touch `acl_sync`.
  - ACLs are applied only to field dropdowns (`collect_ticket_field_options`),
    not to the process list, activity dialogs, the action menu, bulk edit or
    the compat API — although `domain/ticket_acl.py` already understands the
    `Process`/`ActivityDialog`/`Action` keys.
  - YAML shapes verified only against Znuny 6.5.22 samples; Znuny 7.x
    process configs are untested.
- No "new process ticket" (Znuny `AgentTicketProcess`: the start dialog
  *creates* the ticket); only "enroll existing ticket" (verify in the
  frontend before sizing).
- Dialog form widgets are basic: only Queue is a select; State, Priority,
  Owner, Responsible and all `DynamicField_*` are plain text inputs.
- Admin: read-only list/detail pages. No write API, no import/export.

## What competitors offer (research 2026-10-08)

- **Znuny 7.3**: canvas designer (activities, activity dialogs, transitions
  with condition types, 25 transition actions incl. `ConfigItemUpdate`,
  appointments, web service invoke), process categories, customer-started
  processes, Ready2Adopt example processes, YAML import/export, deploy step.
- **OTRS 2025/26**: BPMN-style tasks (script, service = invoker, user task),
  sequence flows with and/or/xor conditions, **cron-scheduled start events**,
  Active/FadeAway/Inactive, explicit deploy, ~two dozen Ready2Adopt processes
  including ITIL ones, recommended ≤ 50 processes.
- Neither has a process analytics dashboard or test/simulation mode.

## Parity checklist

Engine:
- [ ] Respect process state: S1 startable, S3 FadeAway = running tickets
      continue but no new starts, S2 Inactive = not offered
- [ ] Run **deployed** configuration only (see "Deploy model")
- [ ] ACLs applied to process list, activity dialogs, actions, bulk (not the
      compat API: Znuny's GenericInterface calls no `TicketAcl`, ruling F33)
- [ ] Smart tags (`<OTRS_TICKET_*>`, `<OTRS_CUSTOMER_*>`, `<OTRS_CURRENT_*>`,
      `<OTRS_Agent_*>`, `<OTRS_CONFIG_*>`) in action config and condition
      values, using the existing notification placeholder engine
- [ ] Dialog `PendingTime` fields, attachments on dialog articles
- [ ] Missing actions: `AppointmentCreate/Update/Remove` (calendar module
      exists), `ExecuteInvoker` (needs GI requester — see GI design),
      `ConfigItemUpdate` (needs CMDB — see ITSM design)
- [ ] `Module` conditions → mapped to built-in Python condition modules by
      name where Znuny ships them (`ValidateDemo` is a demo); unknown modules
      stay non-matching with a visible warning in the designer
- [ ] Znuny 7.x YAML verified on real 7.0–7.3 peers (golden), including any
      renamed keys
- [ ] "New process ticket" from the start dialog, agent and customer
- [ ] Process categories / grouping in the start menu (verify Znuny 7.3 key)

UI:
- [ ] Typed dialog widgets for every field Znuny dialogs support (Title,
      Article incl. communication channel + visibility + time units, CustomerID
      + CustomerUserID with search, Queue, State, Type, Service, SLA, Owner,
      Responsible, Lock, Priority, PendingTime, Attachments, every dynamic
      field type) with ACL-filtered options, `Display` 0/1/2 (hidden / shown /
      required), default values, short/long descriptions
- [ ] Process widget in ticket zoom shows path so far + current activity

Designer:
- [ ] Create/edit processes, activities, dialogs, transitions, actions
- [ ] Deploy step; import/export in Znuny's YAML format (round-trip)

## Beyond parity ("besser als")

1. **Test run before deploy** ("Probelauf"): pick a real or synthetic ticket,
   step through dialogs in a sandbox, see which transition fires and what
   each action *would* write — nothing persisted.
2. **Revisions with diff and rollback**: every deploy is a revision; visual
   and YAML diff between revisions; rollback = redeploy an old revision.
3. **Validation while editing**: unreachable activities, dead ends, dialogs
   without fields, transitions that can never match (type/value check
   against field definitions), actions referencing missing queues/states.
4. **Process analytics**: tickets per activity now, dwell time per activity
   (P50/P90), paths actually taken, stuck tickets — from the reporting fact
   events (see reporting design).
5. **Activity deadlines**: optional per-activity time limit with escalation
   notification ("Freigabe wartet seit 2 Tagen") — Tiqora-side metadata.
6. **Approvals as a first-class step**: generic approval engine (shared with
   ITSM change and service requests): approvers by user/group/role/customer,
   rule any/all/quorum, approve by email link or portal; new action
   `ApprovalRequest`, new condition field `Approval_<key>` = approved/rejected.
7. **Scheduled and event start** (OTRS has cron start): start a process on a
   schedule (recurring maintenance tasks) or on ticket events matching a
   filter (instead of a GenericAgent workaround).
8. **AI helpers**: draft a process from a description (output = validated
   designer draft, never auto-deployed); AI-filled dialog fields as
   suggestions; AI condition fields (e.g. `AI_Category`) from triage.
9. **Ready-made templates**: ITIL incident, service request with approval,
   change (normal/standard/emergency), onboarding/offboarding, purchase
   request — importable as YAML, also usable in Znuny.

## Fixed decisions

### Deploy model (parallel operation and after cutover)

- Tiqora writes `pm_*` rows exactly as Znuny's
  `Kernel::System::ProcessManagement::DB::*` do: entity ids from
  `EntityIDGenerate` (`<EntityType>-` + 32 random hex characters, e.g.
  `Process-9f1c…`), YAML `config` in Znuny's dump
  format, `create_by/change_by/create_time/change_time`, and a
  `pm_entity_sync` row (`sync_state = 'not_sync'`) per changed entity.
  ACL edits likewise write `acl_sync`.
- Tiqora runs from a **deployed snapshot**, never from live `pm_*` rows:
  `tiqora_process_revision` (`id, deployed_at, deployed_by, source
  (tiqora|znuny), config JSON = full resolved process set, checksum`). The
  engine loads the newest revision (one indexed `SELECT` of the newest id and
  checksum plus an in-process cache; no Redis, ruling F33).
- **Deploy from Tiqora** (parallel-op): snapshot `pm_*` → new revision →
  enqueue a `tiqora_cache_invalidation` signal `process_deploy` →
  **TiqoraSync** (Znuny add-on) runs `ProcessDump` (and `ACLDump` for
  `acl_deploy`) inside Znuny, which writes the ZZZ files and clears the sync
  tables exactly like the admin "Deploy" button. Status is reported back via
  a `tiqora_settings` key and shown in the designer ("In Znuny übernommen").
  TiqoraSync gains a small daemon task for that (Perl, 6.0–7.3 frameworks).
- **Deploy in Znuny** (admin presses Deploy there): Tiqora notices
  `pm_entity_sync` became empty while `pm_*` changed (poller check, or a
  TiqoraSync hook on `ProcessDump`) → snapshots a `source=znuny` revision.
  Before the first snapshot exists, Tiqora takes one at startup only if
  `pm_entity_sync` is empty (= DB equals deployed state); otherwise it warns
  on the Daemons page and runs the DB state (today's behaviour) until a deploy.
- After cutover (`schema ownership`), Tiqora deploys alone: revision +
  clearing the sync tables; no ZZZ files.
- Same model for ACLs: `tiqora_acl_revision`, engine reads the snapshot.

### Engine changes

- Process state gate (S1/S2/S3) in start, list and portal.
- ACL application through one function used by every surface
  (`ticket_acl.filter_process_options(...)`, signature widened for the customer
  principal and process context), with the bulk path included; tests per
  surface. The compat API stays without ACLs like Znuny's GenericInterface
  (ruling F33).
- Smart tags: reuse the notification template renderer; same escaping rules
  as Znuny (`<OTRS_TICKET_DynamicField_X_Value>` etc.).
- Condition modules: registry `tiqora/process/condition_modules.py` keyed by
  Znuny module name; Tiqora-native modules use a `Tiqora::` prefix so YAML
  stays readable by Znuny (Znuny will treat them as unknown → logged, not
  crashing — verify Znuny's behaviour with an unknown module before shipping).
- New actions are Tiqora-native modules under a
  `Kernel::System::ProcessManagement::TransitionAction::Tiqora*` name;
  documented as "nur in Tiqora wirksam". During parallel-op the designer
  warns when a process using them is also enabled in Znuny.

### Designer (frontend)

- Canvas with `@xyflow/react` (MIT, the standard React flow-graph library) —
  new dependency, lazy-loaded on the designer route only. Layout stored in
  `pm_process.layout` in Znuny's format so Znuny's canvas shows the same
  positions.
- Panels: activity (dialogs in order), dialog (fields from the field
  catalogue with typed defaults and Display mode, interface agent/customer,
  permission), transition (condition builder: blocks, and/or/xor, field
  pickers with typed value inputs), action (module picker with a typed form
  per module; raw key/value as fallback for unknown modules).
- Draft state lives in `pm_*` with `pm_entity_sync` dirty rows (exactly
  Znuny's draft semantics), so a draft started in Tiqora is visible as a
  draft in Znuny's designer and vice versa.
- Import/export: Znuny's process YAML (`ProcessImport` / export format)
  round-trip, golden-tested against a real peer (export in Znuny → import in
  Tiqora → export → identical modulo ordering).

### Dialog forms

- One typed field engine (`components/fields/*`) shared by process dialogs,
  new-ticket/phone forms (today `DynamicFieldInputs.tsx`), the webchat
  pre-chat form, ITSM CI forms and the portal. Every Znuny dynamic field type
  Tiqora supports, plus ticket attributes; options come from the reference
  API with ACL filtering.

## Tests

- Engine unit tests per condition type / action (existing suites extended).
- Golden against real peers 6.5 and 7.3: run the same process on the same
  ticket in Znuny and Tiqora → identical ticket state, dynamic fields and
  history rows.
- Deploy: Tiqora deploy → TiqoraSync dump → Znuny runs the new revision;
  Znuny deploy → Tiqora snapshot.
- ACL matrix: each surface hides what an ACL forbids.
- Designer: Playwright create → test run → deploy → start on a ticket.

## Phases

| Phase | Content | Size |
|---|---|---|
| 1 Correctness | State gate, deployed-snapshot model + Znuny-deploy detection, ACLs on every surface, Znuny 7.x YAML verification, smart tags, PendingTime, new process ticket (agent + portal) | 2–3 weeks |
| 2 Parity actions + forms | Appointment actions, typed field engine + all dialog widgets, process widget path view, condition module registry; `ExecuteInvoker` once GI requester exists | 2–3 weeks |
| 3 Designer | Write API, canvas, panels, validation, YAML import/export, revisions + diff, Tiqora deploy via TiqoraSync | 4–5 weeks |
| 4 Beyond | Test run, approvals engine + actions, activity deadlines, scheduled/event start, process analytics, templates, AI drafting | 3–4 weeks |

## Risks / open

- TiqoraSync must run `ProcessDump` / `ACLDump` across Framework 6.0–7.3 —
  APIs may differ; verify per framework in the golden peers.
- Unknown `Tiqora::` condition/action modules in Znuny: confirm they degrade
  to "logged and skipped" rather than breaking the process in Znuny.
- **Open:** whether to allow several processes per ticket. Decision for
  now: no (Znuny compatibility); parallel work uses child tickets
  (`TicketCreate` + `LinkAs`), which already works.
