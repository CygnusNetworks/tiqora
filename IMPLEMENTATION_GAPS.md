# Implementation Gaps: Znuny Feature Parity → Tiqora

**As of:** 2026-07-27  
**Method:** Feature comparison of Znuny 6.5.x (reference) against Tiqora
(`backend/`, `frontend/`, project docs).  
**Purpose:** Product roadmap input for Znuny-style feature parity — not an
implementation order and not a security audit.

> **Background:** First draft 2026-07-21 (local only). Restored and updated for
> the 2026-07-27 codebase. Peer database support is OTRS/Znuny **6.0–7.3**
> (schema profiles); the feature comparison still uses Znuny 6.5.x as the
> functional reference.

Related (API / MCP / auth surfaces): [`API_GAPS.md`](./API_GAPS.md).

---

## Summary

Tiqora is a **clean-room reimplementation** with **database compatibility** and
**behavioural parity** for core ticket workflows — not a 1:1 port of every
Znuny module. Scope is intentional and documented.

| Area | Status |
|---|---|
| Phase 0–5 core (ticket, auth, permissions, daemon flags, cutover) | ✅ largely complete |
| Agent ticket workflow (create / reply / note / move / state / owner / merge / link / …) | ✅ core covered |
| GenericInterface (Session*, Ticket*, History, TimeAccounting, OutOfOffice; REST + SOAP) | ✅ extended subset of Znuny GI |
| Admin | ⚠️ strong coverage; some areas list-only or still missing |
| Postmaster / notifications / GenericAgent | ⚠️ takeover available; documented simplifications |
| Process management / calendar / stats | ⚠️ present; intentionally reduced vs Znuny |
| Znuny platform exclusives (package manager, SysConfig UI, GI admin, mentions, …) | ❌ missing or model-only |

**How to read “complete”:**

| Definition | Met? |
|---|---|
| **A. Design V1 + phases 0–5 + documented extensions** (parallel DB, core workflow, cutover) | **Yes, with known simplifications** |
| **B. Every Znuny admin/agent feature replaceable without the Znuny UI** | **No** — see roadmap below |
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

### 1.7 Closed since the 2026-07-21 draft (excerpt)

| Former gap | Status 2026-07-27 |
|---|---|
| GI SessionGet/Remove, TicketHistoryGet, TimeAccountingGet, OutOfOffice | ✅ |
| Postmaster filters list-only | ✅ **CRUD** (API + UI) |
| API-key lifecycle / MCP P1 (reference + write tools) | ✅ see [`API_GAPS.md`](./API_GAPS.md) |

---

## 2. Planned parity work (roadmap)

Items below are **product/feature** gaps relative to Znuny-style workflows.
Operational caveats for mail/daemon takeover live in
[`docs/parallel-operation.md`](./docs/parallel-operation.md); GI details in
[`docs/compatibility.md`](./docs/compatibility.md). This file does not inventory
transport, session, or crypto edge cases.

### Near-term product priorities

#### Service / SLA / type after create

- Create accepts type, service, and SLA identifiers.
- Post-create mutation and agent toolbar dialogs for those fields are not wired
  yet (history format constants exist; write path / UI pending).
- Admin CRUD for service, SLA, and type is not present (Znuny:
  `AdminService` / `AdminSLA` / `AdminType`).

#### Admin coverage still thin or list-only

| Area | Tiqora today |
|---|---|
| Service, SLA, type | no admin CRUD |
| Mail accounts | fetch uses accounts; no dedicated admin API/UI |
| System addresses | picker list only (no full CRUD) |
| Notification events | runtime evaluation; no editor |
| PGP / S-MIME | engines present; no admin UI |
| System configuration | reader only; no deploy/edit UI |
| ACL | list/detail only (editing deferred) |
| GenericAgent jobs | list/detail only |

#### Process management subset

Deferred conditions and transition actions (including service/SLA/type setters
and several ticket/article helpers) are listed in
[`docs/process-management.md`](./docs/process-management.md). No visual designer;
no full customer process UI; placeholder language is simplified.

#### Mentions & time accounting

Legacy models exist. Mentions have no domain/API/UI. Time accounting rows move
on merge and Compat `TimeAccountingGet` can read; no native book/report UI.

### Integration & mail parity

- **GenericInterface:** core ticket connector surface is in place; custom OPM
  operations, invokers (requester), and GI admin remain out of scope or 501.
  Search/session edge cases and side-effect differences are documented in
  [`docs/compatibility.md`](./docs/compatibility.md).
- **Postmaster / notifications / GenericAgent:** takeover is available with
  documented simplifications (transports, recipient types, follow-up modules,
  job editor). See [`docs/parallel-operation.md`](./docs/parallel-operation.md).
- **Portal follow-up:** some Znuny follow-up modes are not mirrored 1:1; no
  customer process UI.

### Agent UI / productivity

| Znuny-style capability | Tiqora |
|---|---|
| Multi-select bulk actions | not yet |
| Ticket print (PDF) | browser print only |
| Dedicated email/phone create flows | simplified via New Ticket + channels |
| Email resend / plain-text article tools | not yet |
| Full customer information centre | lookup only |
| Locked / owner / responsible / watch / escalation module views | dashboard counts / filters instead |
| Service-centric agent view | not yet |
| Last views / autocompletion | not yet |
| Form drafts | own draft path (+ legacy model) |
| Ticket attribute relations / note-to-linked | not yet |
| Mentions | not yet (see above) |

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
| Merge, bounce, forward, free text (DF), process | ✅ (process = subset) |
| Bulk, PDF print, phone-dedicated, email resend, plain, mention, service view | ❌ / ⚠️ |
| Status / escalation / locked / owner / responsible / watch views | ⚠️ dashboard/filters instead of dedicated modules |

### Admin

**CRUD (among others):** users, groups, roles (+ assignments), queues, states,
priorities, customers/companies (+ groups), templates / salutations /
signatures / attachments, auto-responses, dynamic fields, webhooks, channels,
mail log/outbound, **postmaster filters**, **API keys**, AI admin
(Tiqora-native).

**List / detail only:** ACL, GenericAgent jobs, processes (browse/detail),
system addresses (picker).

**Missing relative to Znuny admin breadth:** service, SLA, type, mail account,
notification event, SysConfig, GI webservice admin, package manager, PGP/SMIME
admin, session admin, support data, cloud services, appointment admin
(calendar via agent UI), ticket attribute relations, OAuth2 token management,
…

### Customer interface

| Znuny | Tiqora portal |
|---|---|
| Overview, zoom, message, search, attachment | ✅ core |
| Process | ❌ |
| Preferences / accept | ⚠️ minimal |
| Print | ❌ |

### GenericInterface

Provider core (Session*, ticket CRUD/search/history, TimeAccounting,
OutOfOffice) ✅. Requester, custom ops, GI admin ❌ — see §1.6 and
[`docs/compatibility.md`](./docs/compatibility.md).

---

## 4. Documentation notes

1. **Design spec** (`docs/specs/2026-07-19-tiqora-design.md`) is historical;
   several items once listed as deferred (process, calendar, stats, PGP/S-MIME,
   SOAP) exist now as subsets. The spec is not live status.
2. **README / feature lists:** do not market ACL or GenericAgent as full admin
   CRUD — list/detail only. Postmaster filters **do** have CRUD.
3. **Compat / GI:** [`docs/compatibility.md`](./docs/compatibility.md) is the
   source of truth for GI scope, not the historical design spec.

---

## 5. Suggested closing order (parity goal B)

Prioritised product roadmap only — not an automatic implementation mandate:

1. **Ticket type / service / SLA** — write APIs + mutation + UI + admin CRUD +
   process actions
2. **Admin editors** — ACL (and optional runtime expansion), GenericAgent,
   notification events
3. **Mail admin** — mail-account admin and remaining fetch/follow-up parity
   (see parallel-operation docs for operational detail)
4. **Mentions + time accounting** — native API + UI (Compat read for TA already
   exists)
5. **Bulk actions** in the agent UI
6. **Process** — remaining transition actions/conditions + designer (or keep
   authoring in Znuny)
7. **Package manager / SysConfig UI** only if there is explicit demand
8. Remainder (PDF print, CIC depth, stats framework, GI side-effects, …)

Already done and removed from this list: GI Session/History/TimeAccounting/OOO;
postmaster-filter CRUD; API-key lifecycle / MCP P1.

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
