# Implementation Gaps: Znuny Feature Parity → Tiqora

**As of:** 2026-08-04  
**Method:** Feature comparison of Znuny 6.5.x (reference) against Tiqora
(`backend/`, `frontend/`, project docs).  
**Purpose:** Product roadmap input for Znuny-style feature parity — not an
implementation order and not a security audit.

> **Background:** First draft 2026-07-21 (local only). Restored and updated for
> the 2026-07-27 codebase; **parity phases 1–4 closed 2026-08-04** (type/service/SLA
> E2E, admin editors, mentions/time accounting, bulk + MCP). Peer database
> support is OTRS/Znuny **6.0–7.3** (schema profiles); the feature comparison
> still uses Znuny 6.5.x as the functional reference.

Related (API / MCP / auth surfaces): [`API_GAPS.md`](./API_GAPS.md).

---

## Summary

Tiqora is a **clean-room reimplementation** with **database compatibility** and
**behavioural parity** for core ticket workflows — not a 1:1 port of every
Znuny module. Scope is intentional and documented.

| Area | Status |
|---|---|
| Phase 0–5 core (ticket, auth, permissions, daemon flags, cutover) | ✅ largely complete |
| Agent ticket workflow (create / reply / note / move / state / owner / merge / link / type / service / SLA / …) | ✅ core covered |
| GenericInterface (Session*, Ticket*, History, TimeAccounting, OutOfOffice; REST + SOAP) | ✅ extended subset of Znuny GI |
| Admin | ✅ strong coverage (type/service/SLA, mail accounts, system addresses, notifications, GenericAgent write); ACL still list-only |
| Postmaster / notifications / GenericAgent | ⚠️ takeover available; GenericAgent + notification **admin write** present; runtime simplifications remain |
| Process management / calendar / stats | ⚠️ present; intentionally reduced vs Znuny (designer still Znuny-side) |
| Mentions + time accounting | ✅ native API + ticket-zoom panel (shared tables) |
| Znuny platform exclusives (package manager, SysConfig UI, GI admin, …) | ❌ missing or model-only |

**How to read “complete”:**

| Definition | Met? |
|---|---|
| **A. Design V1 + phases 0–5 + documented extensions** (parallel DB, core workflow, cutover) | **Yes, with known simplifications** |
| **B. Every Znuny admin/agent feature replaceable without the Znuny UI** | **Mostly for day-to-day ops** — see residual roadmap; ACL/SysConfig/OPM still not |
| **C. Byte-/feature-identical Znuny** | **No** — never a design goal |

---

## 1. Solid / largely complete

### 1.1 Parallel-operation invariants

Ticket numbers, history formats, escalation index, follow-up (subject /
references subset), search flag / ticket index, legacy password compatibility,
SysConfig **read**, cache invalidation (with TiqoraSync OPM), and schema
profiles for peer versions 6.0–7.3 are implemented for dual-stack operation.
Details: [`docs/parallel-operation.md`](./docs/parallel-operation.md),
[`docs/support-matrix.md`](./docs/support-matrix.md).

### 1.2 Agent ticket write path

Create, article (reply/note), move, state (+ pending), priority, title,
customer, owner, responsible, lock/unlock, watch, archive, dynamic fields,
merge, forward, bounce, split, link, drafts, history, attachments, CSV export,
process start/submit on ticket zoom. Create accepts type / service / SLA
identifiers (post-create mutation: see roadmap).

### 1.3 Auth / permissions

Legacy passwords, sessions, API keys (admin UI/API/CLI, expiry, coarse scopes),
OIDC, SPNEGO/Kerberos, TOTP, passkeys, LDAP (agent + customer), group/role
permission engine. Znuny ACL **editing** is list/detail only; runtime uses
group/role (design choice — see [`API_GAPS.md`](./API_GAPS.md)).

### 1.4 Daemon takeover (feature-flagged, default off)

Postmaster, escalation sweep, notifications, GenericAgent, outbox/indexer.

### 1.5 Portal, KB, channels, crypto, GDPR, calendar, stats, process (core)

- Portal tickets + knowledge base
- SMS / WhatsApp / phone plugins
- PGP/S-MIME (flag-gated)
- GDPR anonymize/retention (ownership-gated)
- Calendar (shared `calendar*` tables)
- Stats (fixed modern reports; not the Znuny stats framework)
- Process engine (subset of actions/conditions)
- AI assistance / MCP (Tiqora-native; no Znuny equivalent)

### 1.6 Compat API (GenericInterface provider)

| Operation | Status |
|---|---|
| `SessionCreate` | ✅ |
| `SessionGet` / `SessionRemove` (`SessionDelete` alias) | ✅ |
| `TicketCreate` / `TicketUpdate` / `TicketGet` / `TicketSearch` | ✅ |
| `TicketHistoryGet` | ✅ |
| `TimeAccountingGet` | ✅ |
| `OutOfOffice` | ✅ |
| REST + SOAP transport | ✅ |
| Requester / custom ops / GI admin | ❌ (HTTP 501 / out of scope) |

Scope and intentional differences: [`docs/compatibility.md`](./docs/compatibility.md).

### 1.7 Closed since earlier drafts (excerpt)

| Former gap | Status |
|---|---|
| GI SessionGet/Remove, TicketHistoryGet, TimeAccountingGet, OutOfOffice | ✅ (2026-07-27) |
| Postmaster filters list-only | ✅ **CRUD** (API + UI) |
| API-key lifecycle / MCP P1 (reference + write tools) | ✅ see [`API_GAPS.md`](./API_GAPS.md) |
| Type / service / SLA post-create + admin + process actions | ✅ **2026-08-04** |
| System address / notification event / GenericAgent write | ✅ **2026-08-04** (ACL still list-only) |
| Mentions + native time accounting | ✅ **2026-08-04** (ticket API + zoom panel) |
| Bulk multi-select (state/priority/owner + queue move + lock) | ✅ **2026-08-04** |

---

## 2. Remaining parity work (roadmap)

Items below are **product/feature** gaps relative to Znuny-style workflows.
Operational caveats for mail/daemon takeover live in
[`docs/parallel-operation.md`](./docs/parallel-operation.md); GI details in
[`docs/compatibility.md`](./docs/compatibility.md). This file does not inventory
transport, session, or crypto edge cases.

### Closed in the 2026-08-04 parity pass (no longer open)

#### Service / SLA / type (end-to-end)

- Create accepts type, service, and SLA identifiers.
- Post-create mutation via `TicketWriteService` + `PATCH` (`type_id` /
  `service_id` / `sla_id` / `clear_service` / `clear_sla`) with Znuny history
  formats (`TypeUpdate` / `ServiceUpdate` / `SLAUpdate`).
- Agent ticket-zoom pickers; reference lists
  `GET /api/v1/reference/{types,services,slas}`.
- Admin CRUD: `AdminType` / `AdminService` / `AdminSLA` (shared tables +
  `service_sla` links).
- Process TransitionActions: `TicketTypeSet` / `TicketServiceSet` /
  `TicketSLASet` (plus `TicketWatchSet`, `LinkAdd`).

#### Mentions & time accounting

- Shared tables `mention` / `time_accounting`.
- Native REST list/create/delete on ticket; ticket-zoom panel.
- Compat `TimeAccountingGet` still available; merge still moves TA rows.

#### Bulk + selected admin write

- Queue multi-select: state, priority, owner, **queue move**, **lock/unlock**.
- System addresses: create/update/deactivate (picker list kept).
- Notification events: admin CRUD on `notification_event*`.
- GenericAgent jobs: PUT/PATCH/DELETE (list/detail still on readonly paths).

### Still open / thin

| Area | Tiqora today |
|---|---|
| PGP / S-MIME | engines + admin `/admin/crypto-keys` (import/audit); no Znuny-style full keyring UI |
| System configuration | reader only; no deploy/edit UI (platform choice) |
| ACL | ✅ admin CRUD + TicketACL runtime (`domain/ticket_acl.py`); group/role still for queue access |
| Process designer / customer process UI | authoring stays Znuny or DB/YAML; **portal process UI** for CustomerInterface dialogs ✅ |
| Process deferred actions | remaining: `ExecuteInvoker`, `Appointment*`, `ConfigItemUpdate`, condition `Module` |
| Ticket attribute relations | ✅ admin CSV CRUD + picker filter (Service→Queue etc.) |
| Time-accounting report | ✅ agent `/agent/time-accounting` + `GET /api/v1/tickets/time-accounting` |

### Integration & mail parity

- **GenericInterface:** core ticket connector surface is in place; custom OPM
  operations, invokers (requester), and GI admin remain out of scope or 501.
  Search/session edge cases and side-effect differences are documented in
  [`docs/compatibility.md`](./docs/compatibility.md).
- **Postmaster / notifications / GenericAgent:** takeover is available with
  documented simplifications (transports, recipient types, follow-up modules).
  GenericAgent + notification **admin write** exists; runtime behaviour is
  still a simplified subset. See [`docs/parallel-operation.md`](./docs/parallel-operation.md).
- **Portal follow-up:** some Znuny follow-up modes are not mirrored 1:1; no
  customer process UI.

### Agent UI / productivity

| Znuny-style capability | Tiqora |
|---|---|
| Multi-select bulk actions | ✅ state / priority / owner / queue / lock |
| Ticket print (PDF) | ✅ printable HTML `GET /tickets/{id}/print` (browser print) |
| Dedicated email/phone create flows | simplified via New Ticket + channels |
| Email resend / plain-text article tools | ✅ `…/resend` (bounce alias) + `…/plain` |
| Full customer information centre | ✅ `/agent/customers/$login` (counts + recent) |
| Locked / owner / responsible / watch / escalation module views | ✅ queue presets + sidebar |
| Service-centric agent view | ✅ `/agent/services` |
| Last views / autocompletion | ✅ last views (localStorage); no full autocomplete |
| Form drafts | own draft path (+ legacy model) |
| Ticket attribute relations / note-to-linked | not yet |
| Mentions | ✅ ticket API + zoom panel |
| Ticket ACL field filtering | ✅ zoom pickers via `field-options` |

### Explicitly out of scope / platform

| Feature | Notes |
|---|---|
| Package manager (OPM) | often listed as planned; not implemented |
| Support data collector / cloud services / OTRSBusiness | intentionally not |
| Installer / Znuny unit/selenium/console | N/A |
| Znuny dynamic stats framework | fixed modern reports instead |
| Calendar edge features (occurrence detach, auto ticket-appointment rules, plugins) | documented elsewhere |
| SysConfig deployment UI | not planned as a Znuny clone |
| SelectBox / performance log / system maintenance admin | not present |
| Hybrid/vector Meili RAG | planned; keyword Meili available |

---

## 3. Compact module map

### Agent ticket (core)

| Znuny-style area | Tiqora |
|---|---|
| Zoom, queue, search, history, attachment | ✅ |
| Note, compose/reply, close, pending, move, owner, responsible, priority, customer, lock, watcher | ✅ |
| Type / service / SLA | ✅ create + post-create + zoom pickers |
| Merge, bounce, forward, free text (DF), process | ✅ (process = subset) |
| Bulk (state/priority/owner/queue/lock), mentions, time accounting | ✅ |
| Print HTML, email resend, plain, service view | ✅ |
| Status / escalation / locked / owner / responsible / watch views | ✅ queue presets |

### Admin

**CRUD (among others):** users, groups, roles (+ assignments), queues, states,
priorities, **ticket types**, **services**, **SLAs**, customers/companies
(+ groups), templates / salutations / signatures / attachments, auto-responses,
dynamic fields, webhooks, channels, mail log/outbound, **mail accounts**,
**system addresses**, **notification events**, **GenericAgent jobs** (write),
**postmaster filters**, **API keys**, AI admin (Tiqora-native).

**ACL:** full CRUD + Znuny YAML `config_match`/`config_change` (runtime filters
pickers; queue access still group/role).

**List / detail only:** processes (browse/detail; no visual designer).

**Missing relative to Znuny admin breadth:** SysConfig deploy UI, GI webservice
admin, package manager, session admin, support data, cloud services, appointment
admin (calendar via agent UI), ticket attribute relations, full crypto keyring
parity, …

**Present (Znuny-compatible):** mail account admin + OAuth2 token management
(shared legacy `oauth2_token*` / `mail_account` tables; XOAUTH2 fetch + optional
SMTP outbound via config name).

### Customer interface

| Znuny | Tiqora portal |
|---|---|
| Overview, zoom, message, search, attachment | ✅ core |
| Process | ✅ CustomerInterface dialogs (start/submit) |
| Preferences / accept | ⚠️ minimal |
| Print | ❌ |

### GenericInterface

Provider core (Session*, ticket CRUD/search/history, TimeAccounting,
OutOfOffice) ✅. Type/Service/SLA mutation on TicketUpdate ✅. Requester,
custom ops, GI admin ❌ — see §1.6 and
[`docs/compatibility.md`](./docs/compatibility.md).

---

## 4. Documentation notes

1. **Design spec** (`docs/specs/2026-07-19-tiqora-design.md`) is historical;
   several items once listed as deferred (process, calendar, stats, PGP/S-MIME,
   SOAP) exist now as subsets. The spec is not live status.
2. **README / feature lists:** do not market ACL as full admin CRUD —
   list/detail only. GenericAgent, postmaster filters, notification events,
   type/service/SLA **do** have write surfaces.
3. **Compat / GI:** [`docs/compatibility.md`](./docs/compatibility.md) is the
   source of truth for GI scope, not the historical design spec.
4. **Process supported actions:** see
   [`docs/process-management.md`](./docs/process-management.md) (includes
   Type/Service/SLA setters as of 2026-08-04).

---

## 5. Suggested closing order (residual parity goal B)

Prioritised product roadmap only — not an automatic implementation mandate:

1. **Process designer** (or keep authoring in Znuny) + remaining actions
   `ExecuteInvoker` / `Appointment*` / `ConfigItemUpdate` / condition `Module`
2. **Note-to-linked** tickets
3. **Package manager / SysConfig UI** only if there is explicit demand
4. Remainder (stats framework, GI side-effects, …)

Already done and removed from this list: GI Session/History/TimeAccounting/OOO;
postmaster-filter CRUD; API-key lifecycle / MCP P1; **type/service/SLA E2E**;
**system address / notification / GenericAgent write**; **mentions + time
accounting**; **bulk queue actions**; MCP history/merge/link/type-service-sla;
**ACL editor + TicketACL runtime**; process TicketCreate/DFPendingTime/ordered
conditions; agent module views / print / CIC / service view / last views /
time-accounting report; email resend + plain; MCP responsible/watch/archive/
attachments/forward/bounce.

---

## 6. Related documentation

| Document | Role |
|---|---|
| [`README.md`](./README.md) | Product / feature overview |
| [`API_GAPS.md`](./API_GAPS.md) | API / MCP / auth gaps (as of 2026-07-27) |
| [`docs/support-matrix.md`](./docs/support-matrix.md) | Peer versions 6.0–7.3 |
| [`docs/parallel-operation.md`](./docs/parallel-operation.md) | Daemon takeover + operational caveats |
| [`docs/process-management.md`](./docs/process-management.md) | Process supported vs deferred |
| [`docs/compatibility.md`](./docs/compatibility.md) | GenericInterface scope |
| [`docs/architecture.md`](./docs/architecture.md) | Architecture + known simplifications |
| [`docs/cutover.md`](./docs/cutover.md) | Cutover runbook |
| [`docs/guide/znuny-to-tiqora.md`](./docs/guide/znuny-to-tiqora.md) | Operator migration playbook |

---

## Document changelog

| Date | Change |
|---|---|
| 2026-07-21 | Initial draft: Znuny 6.5.22 vs Tiqora (local, uncommitted) |
| 2026-07-27 | Restored to repo; updated for GI ops, postmaster-filter CRUD, API_GAPS cross-ref, roadmap/matrix cleanup |
| 2026-07-27 | Public rewrite: English; product-roadmap framing; no security-edge inventory |
| 2026-08-04 | Parity phases 1–4: type/service/SLA E2E; admin write (system addresses, notifications, GenericAgent); mentions + time accounting; bulk queue/lock; process Type/Service/SLA/Watch/Link actions; residual roadmap rewritten |
