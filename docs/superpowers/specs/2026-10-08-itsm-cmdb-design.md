# ITSM: incident/problem, CMDB, change, service catalogue — design

> Part of the [competitive parity roadmap](2026-10-08-competitive-parity-roadmap-design.md).
> Builds on the [process management design](2026-10-08-process-management-design.md)
> (approvals engine, typed field engine, deploy model) and the
> [GenericInterface design](2026-10-08-generic-interface-design.md) (`ConfigItem*` operations).
> Status: proposed 2026-10-08. Decisions below are fixed unless marked "open".

## Goal

Cover everything the free Znuny ITSM bundle does, so "you need ITSM/CMDB →
stay on Znuny" disappears from the comparison, and reach the level of
Freshservice / Jira Service Management where it matters to self-hosted
teams: impact analysis, change risk, approvals, service catalogue.

## Current state (2026-10-08)

- **No ITSM code.** No models, API or UI for `configitem*`,
  `general_catalog`, `change_*`, impact/criticality, incident/problem.
- Services, SLAs, ticket types: admin CRUD exists (`admin/services.py`,
  `slas.py`, `types.py`); agents pick service/SLA in `TicketHeaderActions.tsx`;
  a service view page exists (`routes/agent/ServiceViewPage.tsx`).
  `ServiceCustomerUser` is modelled but unused.
- Links: ticket ↔ ticket add/list only (`LinkRelation`); no delete, no
  link-type admin, no "note to linked tickets".
- Dynamic fields: 7 types; ticket zoom shows them **read-only**
  (`TicketHeader.tsx`) although `PATCH /tickets/{id}` can update them.
- Calendar module with appointments and ICS exists (`tiqora/calendar/`).
- Process engine exists (see process design), approvals do not.

## What competitors offer (research 2026-10-08)

**Znuny 7.3 ITSM** — still separate packages, none deprecated: GeneralCatalog
7.3.1, ITSMCore 7.3.3, ITSMIncidentProblemManagement 7.3.2,
ITSMConfigurationManagement 7.3.5, ITSMChangeManagement 7.3.4,
ITSMServiceLevelManagement 7.3.1, ImportExport 7.3.3. Znuny's own docs say
that "in most cases" a process is better than installing Change Management.

- *ITSMCore*: services/SLAs with view/zoom/print/link; Criticality × Impact
  → Priority matrix ("CIP allocation"); service ↔ ticket/change/CI links.
- *Incident/Problem*: types Incident, Incident::Major, ServiceRequest,
  Problem; state "closed with workaround"; decision screen and additional
  ITSM fields; live service incident state on ticket screens; "minimum time
  between incidents"; stats: first-level solution rate, average solution time.
- *CMDB*: classes defined in YAML (Key, Name, Searchable, Input.Type,
  CountMin/Max/Default, nested Sub); input types CI, CIAttachment, Customer,
  CustomerCompany, Date, DateTime, GeneralCatalog, Integer, Dummy, Priority,
  Queue, Service, SLA, State, Text, TextArea, TextLink, Type, User;
  deployment state + incident state per CI; full version history,
  attachments; links CI ↔ CI/ticket/change/work order with admin link types;
  full-text + attribute search; bulk actions; customer portal CI view;
  GenericInterface `ConfigItemCreate/Get/Search/Update/Delete`.
- *Change*: change and work-order state machines, conditions, templates
  (change, work order, CAB), CAB, PIR, change schedule (agent + customer),
  PSA/time slots, involved persons, My Changes/My Work Orders, notifications;
  work-order types Approval/Workorder/Backout/Decision/PIR.
- *ImportExport*: CSV templates for CIs.

**Best in class**: Freshservice — discovery probe/agent/cloud discovery,
change risk checks (incidents on the same services, blackout windows,
conflicting changes), CAB with any/all/majority, multi-level approvals,
approvals from Slack/Teams. JSM — assets with network discovery and imports,
change risk engine, low-risk auto-approval, approvers from asset owners,
change calendar with freeze windows.

## Fixed decisions

### 1. Tiqora-owned ITSM model + importer (not Znuny's ITSM tables)

- New state in `tiqora_itsm_*` / `tiqora_cmdb_*` tables (ground rule 2).
- Reasons: (a) Znuny's ITSM tables exist only if the OPM packages are
  installed, with schema differences across 6.0–7.3 and CI attribute data
  in `xml_storage` (6.x); (b) "better than Znuny" needs a model Znuny's
  cannot express (typed references, repeatable groups, fast attribute search,
  impact graph); (c) ITSMCore even alters Znuny's `service` table (extra
  columns), which Tiqora must not do (ground rule 1).
- Consequence, stated plainly in docs and UI: **ITSM is Tiqora-primary.**
  Znuny's ITSM screens do not see Tiqora CIs or changes. Sites running Znuny
  ITSM import once (or repeatedly until the switch) and then use Tiqora for
  ITSM while tickets stay shared. Section 1a describes how Znuny users still
  see ITSM data (read-through mode, Znuny add-on).
- What *is* shared with Znuny: ticket types, states, ITSM ticket dynamic
  fields (they are ordinary dynamic fields), services, SLAs, ticket ↔ ticket
  links. Tiqora creates these the Znuny way (existing admin services).
- Alternative recorded: Tiqora's own ITSM *writes* Znuny's ITSM tables
  (two-way sync). Rejected: per-version schemas (6.x `xml_storage`, 7.x
  YAML definitions), Znuny ITSM business logic (version creation, incident
  propagation, change state machines) would have to be re-implemented
  byte-identically, and every Znuny ITSM release could break it. Read-only
  access to Znuny's ITSM tables is fine and is used below.

### 1a. Coexistence with Znuny — how sites are not left alone

"Tiqora-primary" must not mean "Znuny users are blind". Three building
blocks, each optional, so a site picks what fits:

**Mode A — "Znuny führt das ITSM" (read-through, for the parallel phase).**
For sites that run Znuny ITSM and want to keep editing CIs and changes there
while agents already work in Tiqora:
- Tiqora reads Znuny's ITSM tables **read-only** through a version-aware
  adapter (`tiqora/itsm/znuny_adapter/`, one reader per schema profile):
  CI list/search/detail incl. versions, classes, deployment/incident state,
  changes and work orders.
- Ticket ↔ CI links are written to Znuny's `link_relation` with the
  `ITSMConfigItem` link object — this is a plain Znuny link row, written the
  way `LinkObject::LinkAdd` writes it (ground rule 3), so Znuny's ITSM sees
  links made in Tiqora and vice versa.
- Priority matrix (`cip_allocate`) and service criticality are read from
  Znuny and applied in Tiqora exactly as ITSMCore would.
- Editing CIs/changes deep-links into Znuny's UI ("In Znuny bearbeiten").
- Covers the daily agent need (see and link affected CIs, see change
  windows) without any data duplication.

**Mode B — "Tiqora führt das ITSM" (target state).**
Own model below; the switch is one importer run (re-runnable until the
switch date). From then on, Znuny's ITSM is read-only by policy.

**Znuny add-on `TiqoraITSM` (OPM, optional, for Mode B).**
Since both systems share one database, a small Perl package installed next to
TiqoraSync can show Tiqora's ITSM data inside Znuny without a sync:
- Ticket zoom sidebar widget "Betroffene CIs (Tiqora)": CI name, class,
  deployment/incident state, link to Tiqora — read straight from
  `tiqora_cmdb_*` / `tiqora_itsm_link` (read-only SQL, no Tiqora tables are
  written by Perl).
- Optional linking from Znuny through Tiqora's REST API (`/api/v1/cmdb/links`,
  API key stored in Znuny SysConfig) so all writes keep going through
  Tiqora's validation.
- Optional: put Znuny's ITSM screens into read-only mode after the switch
  (ACL/permission config shipped by the add-on) so nobody edits the old
  CMDB by accident.
- Customer interface widget with the customer's CIs (same read path).
- Scope is deliberately small (widgets + one API client); it is not a second
  ITSM UI inside Znuny.

**Rejected:** full bidirectional sync (see above).

**Documentation duty** (part of every ITSM phase's definition of done):
`docs/itsm.md` with a coexistence chapter — which mode does what, what
Znuny users see in each mode, the switch runbook (import dry run → import →
policy read-only → add-on install), and a "what you lose / keep" table per
Znuny ITSM screen. The compare page and `docs/parallel-operation.md` get a
short pointer to it.

### 2. Changes are tickets, not a separate object

- A change = ticket of type `Change` (sub-type Standard / Normal / Emergency
  as dynamic field) driven by a change **process template**; work orders =
  child tickets of type `Change::Task` with planned start/end, assignee and
  order; CAB and approvals through the generic approval engine.
- Reasons: SLA, notifications, history, portal, search, reporting, AI and
  Znuny visibility come for free; Znuny's own docs recommend processes over
  the Change package; Freshservice/JSM model changes as tickets too.
- Znuny `change_*` data is imported as a **read-only archive**
  (`tiqora_itsm_legacy_change`, JSON + search fields) linked to CIs/tickets,
  not converted into tickets. Conversion can be added later if a site needs it.

### 3. Generic approval engine (shared)

- `tiqora_approval` (`id, ticket_id, key, step, rule (any|all|quorum n|majority),
  status, due_at, created_by, decided_at`) and `tiqora_approval_vote`
  (`approval_id, approver_kind (user|group|role|customer_user), approver_ref,
  decision, comment, decided_by, decided_at, channel (ui|email|portal|chat)`).
- Process action `ApprovalRequest`, condition field `Approval_<key>`;
  approve/reject in the agent UI, portal and by signed email link
  (single-use token, expiry). Delegation and reminders.
- Used by: change CAB, service requests, process steps, purchase/onboarding
  templates. Built once, in whichever of process phase 4 / ITSM phase 3 comes
  first.

## Scope

### A. ITSM core + incident/problem

- Ticket types `Incident`, `Incident::Major`, `ServiceRequest`, `Problem`,
  `Change`, `Change::Task`; state `closed with workaround` — created if
  missing via the existing admin services (Znuny-identical rows).
- ITSM ticket dynamic fields with Znuny's names, created if missing:
  `ITSMCriticality`, `ITSMImpact`, `ITSMReviewRequired`,
  `ITSMDecisionResult`, `ITSMDecisionDate`, `ITSMRepairStartTime`,
  `ITSMRecoveryStartTime`, `ITSMDueDate` — so a site with Znuny ITSM keeps its
  data and both UIs show the same fields.
- Criticality × Impact → Priority matrix: `tiqora_itsm_priority_matrix`;
  priority set automatically when impact/criticality change (unless the agent
  overrides); imported from Znuny's `cip_allocate` when present.
- Service attributes Znuny ITSM adds (type, criticality) + owner,
  description, portal visibility: `tiqora_itsm_service_meta` (imported from
  the extra `service` columns when present, read-only).
- Service incident state derived from linked CIs (see CMDB) and shown on
  ticket screens and the service view.
- "Minimum time between incidents" per service (flood control).
- Editable dynamic fields in ticket zoom (fix the read-only display) — needed
  for the decision/ITSM fields screens.
- Links: delete, link-type admin, **note to linked tickets** (both on the
  `compatibility.md` missing list), link graph in zoom.
- Problem management: link incidents → problem, root cause + workaround
  fields, **known error = KB article** with workaround (Tiqora KB, review
  workflow), "closed with workaround" on linked incidents in one action.
- Major incident: banner on linked tickets, one update to all affected
  customers (note/reply to linked tickets), timeline.

### B. CMDB

Tables:
- `tiqora_cmdb_catalog` — general-catalog equivalent (`class, key, name,
  valid, functionality` — e.g. deployment states preproductive/productive/
  postproductive, incident states operational/warning/incident).
- `tiqora_cmdb_class` (`key, name, icon, number_prefix, group_id
  (Znuny group for ro/rw), current_definition_id, valid`) and
  `tiqora_cmdb_class_definition` (`class_id, version, schema JSON, created`).
- Attribute types (superset of Znuny's): text, textarea, integer, decimal,
  date, datetime, catalog value, CI reference (class filter), customer user,
  customer company, agent, service, SLA, queue, priority, ticket type, state,
  URL, email, IP/CIDR, MAC, money, attachment, nested group with
  CountMin/CountMax/CountDefault (Znuny `Sub` semantics), searchable flag,
  required, default, help text.
- `tiqora_cmdb_ci` (`number, class_id, name, deployment_state_id,
  incident_state_id (derived), current_version_id, created/changed`) and
  `tiqora_cmdb_ci_version` (`ci_id, definition_version, name,
  deployment_state_id, attributes JSON, change_note, created_by, created`) —
  every save is a version (Znuny parity), with a "no new version for
  unchanged data" rule.
- `tiqora_cmdb_ci_attr` — denormalised search index for searchable
  attributes (`ci_id, key, value_text, value_num, value_date`) — works the
  same on MySQL and PostgreSQL (no dialect-specific JSON indexes).
- `tiqora_itsm_link` (`source_kind, source_id, target_kind, target_id,
  link_type, created_by, created`) for CI ↔ CI/ticket/service/KB/customer;
  link types admin-managed with direction and "propagates incidents" flag.
  Ticket ↔ ticket links stay in Znuny's `link_relation`.

Features:
- Class designer (form-based, with YAML import of Znuny class definitions).
- CI create/edit/version history with diff, attachments, bulk edit, CSV/XLSX
  import/export with saved mapping templates (Znuny ImportExport parity),
  full-text + attribute search, saved searches (reporting design).
- Incident state propagation along link types flagged as propagating
  (Znuny `IncidentLinkTypeDirection` semantics): CI in incident → dependent
  CIs/services get *warning*.
- **Impact analysis** ("besser als"): graph view of a CI's dependencies;
  "what is affected" = services, customers and open tickets reachable through
  dependency links; shown in ticket zoom for linked CIs.
- Ticket integration: link CIs from the ticket (search by name/number/IP),
  affected CIs listed in zoom, CI tab shows tickets/changes.
- Portal: customers see CIs linked to them or their company (Znuny
  customer CI view parity), can pick them in ticket forms.
- QR/label print with deep link (phase 4).
- Reporting source `config_items` (counts by class/state, changes per CI,
  incident-prone CIs).
- MCP tools: `cmdb_search`, `cmdb_get`, `cmdb_impact` (AI can answer "welche
  Kunden hängen an Switch X?").

### C. Change enablement

- Change process template (request → assessment → CAB approval → schedule →
  implementation tasks → review/PIR → close), shipped as importable YAML;
  emergency path with post-approval; standard changes as pre-approved
  templates.
- Risk: questionnaire fields → score; automatic checks (Freshservice/JSM
  level): criticality of affected CIs/services, open incidents on the same
  services, overlapping changes on the same CIs, freeze/blackout windows;
  AI risk summary as an internal note (opt-in, PII-masked).
- Change calendar = calendar module with change appointments (planned
  windows), freeze windows, per-service availability view (PSA); portal
  change schedule for customers (Znuny `CustomerITSMChangeSchedule` parity).
- CAB as a role/group or named list; voting via approval engine; PIR as the
  review activity with its own dialog.
- My changes / my tasks views = saved searches.

### D. Service catalogue and request fulfilment

- Portal catalogue: categories, search, service descriptions, request forms
  (typed field engine or process start dialog), approvals, status tracking.
- Request templates map to queue/type/service/SLA and optionally a process.
- Service-request reporting (fulfilment time per catalogue item).

## Importer from Znuny ITSM

- Read-only, idempotent, re-runnable (`tiqora itsm import --dry-run`), async
  job with a report: general catalog → catalog; CI classes + definitions
  (6.x Perl-structure definitions and 7.x YAML definitions) → class
  definitions; CIs + **all versions** (attributes from `xml_storage` on 6.x;
  7.x storage to be verified) → CIs/versions; CI links from `link_relation`
  (`ITSMConfigItem` objects) → `tiqora_itsm_link`; `cip_allocate` → matrix;
  service ITSM columns → service meta; `change_*` → legacy archive.
- **Task 0 before sizing**: fetch the package database definitions for each
  supported version from `github.com/znuny/ITSM*` (the local
  `znuny-6.5.22/` tree is core only) and record the per-version schema in
  `docs/itsm-import.md`.

## GenericInterface

`ConfigItemCreate/Get/Search/Update/Delete` with Znuny's request/response
shapes on the compat layer, mapped to the Tiqora model; process action
`ConfigItemUpdate` with Znuny's config keys. See the GI design.

## Tests

- Importer: fixtures from real Znuny 6.5 and 7.3 peers with ITSM installed
  (golden toolkit gains an "ITSM" peer variant) → expected Tiqora objects,
  version counts, links.
- Definition validation, version diffing, attribute index consistency,
  incident propagation (cycles!), impact traversal limits.
- Approval engine: rules any/all/quorum/majority, email token single use,
  delegation, permission (customer approvers only in their company).
- Change risk checks with synthetic calendars.

## Phases

| Phase | Content | Size |
|---|---|---|
| 0 | Znuny ITSM schema survey per version (task 0) | 2–3 days |
| 1 | ITSM core + incident/problem: types/states/DFs, priority matrix, service meta, editable DFs in zoom, link admin + note to linked tickets, problem/known error/major incident; **Mode A read-through adapter** (CIs visible + linkable in Tiqora while Znuny leads) | 3–4 weeks |
| 2 | CMDB (Mode B): model, class designer, CI CRUD/versions/search/import-export, links + propagation + impact graph, ticket + portal integration, Znuny ITSM importer, GI `ConfigItem*`, `ConfigItemUpdate` action, MCP tools, Znuny add-on `TiqoraITSM` (widgets), `docs/itsm.md` coexistence chapter | 6–7 weeks |
| 3 | Change + catalogue: approval engine (if not built yet), change templates, risk checks, change calendar + freeze, CAB, tasks, portal catalogue + request forms, legacy change archive | 4–5 weeks |
| 4 | Beyond: discovery/sync connectors (first target open — NetBox, Snipe-IT, i-doit or Entra/Intune devices), AI problem detection from incident clusters, AI change risk, QR labels | 3 weeks |

## Risks / open

- Sites that edit in Znuny's ITSM UI *after* the Mode B switch would see
  diverging CMDBs — hence the read-only policy shipped by the `TiqoraITSM`
  add-on and a re-runnable importer up to the switch date. Until then Mode A
  avoids any duplication.
- Mode A depends on Znuny ITSM's schema per version — the adapter is limited
  to reading and is covered by the task-0 survey and golden ITSM peers.
- CMDB size: 100k+ CIs with many versions → attribute index and version
  table need paging and an archive policy for old versions.
- **Open:** first discovery connector — depends on what pilot sites use.
