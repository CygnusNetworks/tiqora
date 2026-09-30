# Compact Phone Ticket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "Neues Ticket → Telefonticket" compact: call strip with direction, one property bar (Queue, Besitzer, Priorität, Status) shared with the e-mail variant, rarely used fields collapsed, no junk dynamic fields, a sensible queue default (customer's last ticket instead of "Junk"), owner and status prefilled from the CTI call.

**Architecture:** Backend: a queue-suggestion endpoint for a customer, a configurable new-ticket default queue (Znuny sysconfig `QueueDefault`, never Junk/Raw/Postmaster), the dynamic-field fallback changed to "nothing configured → no fields", and the CTI popup passing the answering agent. Frontend: a `TicketPropsBar` component used by both variants of `NewTicketPage`, a `PhoneCallStrip`, a collapsible "Weitere Felder" section, and prefill rules.

**Tech Stack:** FastAPI, SQLAlchemy async, React 18, TanStack Query/Router, Tailwind tokens, Vitest.

**Spec:** Approved mockup https://claude.ai/artifact/Skvuueebr9nerupayLRKwr (version 3, user: "ja bitte so umsetzen", 2026-09-30). Local copy: `/private/tmp/claude-501/-Users-valerius-git-tiqora/132fa92a-cda6-42ad-8aad-8fea289e2ed4/scratchpad/phone-ticket.html`. Its "notes" section lists the rules; the decisions below are binding.

## Global Constraints

- Commit directly to `main`; never push.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q`; `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`.
- Frontend: `cd frontend && pnpm lint && pnpm test --run`; never prettier/biome; Tailwind theme tokens only (`cn` is plain clsx).
- API changes → regenerate `packages/api-client/openapi.json`, `docs/api/openapi.json`, `packages/api-client/src/schema.d.ts` (skill `tiqora-openapi-regen-or-ci-wipes-schema`).
- i18n: en + de, propagate (skill `tiqora-i18n-key-propagation`), `node frontend/scripts/check-i18n-keys.mjs`.
- UI copy German first, plain words.

## Decisions (binding)

1. **Queue default order** for a new ticket (both variants): `?queue_id=` param → (once a customer is set) the queue of that customer user's most recent ticket → the most recent ticket of the customer's company (`customer_id`) → configured default (`Ticket::Frontend::AgentTicketPhone###QueueDefault` for phone, `Ticket::Frontend::AgentTicketEmail###QueueDefault` for e-mail; value is a queue name) → first visible queue that is NOT Junk, Raw or Postmaster (name match, case-insensitive, top-level names) → first visible queue. Only queues the agent may create tickets in (same permission filter the form's queue list uses). A queue the agent picked by hand is never overwritten by a later customer change. The property bar shows the source under the queue: "wie letztes Ticket von <Name>", "wie letztes Ticket der Firma", or nothing.
2. **Dynamic fields**: `GET /reference/dynamic-fields?screen=…` returns only fields enabled in the screen's sysconfig (1 or 2). Nothing enabled → empty list (the old "all fields" fallback is removed). Required fields (2) render visibly in the main form; optional ones inside "Weitere Felder".
3. **Owner from the call**: the CTI popup passes `owner_id` when the call was answered and the answering extension maps to exactly one agent (`users_for_extension`). Form owner defaults to that; otherwise the current user. The bar shows "hat angenommen" under the owner when prefilled from the call.
4. **Status default**: opened from the call popup (`call_ended` present) → the first state of type `closed` whose name contains "successful"/"erfolgreich" (else first closed state); otherwise today's default (open). Submit label: "Erfassen und schließen" when the chosen state is of type closed, else "Ticket erstellen".
5. **Direction** lives in the call strip: from the popup a read-only badge ("Eingehender Anruf" green arrow in / "Ausgehender Anruf" purple arrow out); manual: a two-button toggle "Anruf kam rein" / "Ich habe angerufen". Next to it the effect: "Kunde bekommt die Eingangsbestätigung" / "keine Eingangsbestätigung".
6. **Property bar** (`TicketPropsBar`): one bordered row of cells, each = uppercase small label, value with chevron, optional green source line; whole cell opens the selection (reuse the existing `SelectMenu` as the trigger/panel, not a hidden native select). Priority and status show colour dots (state colours from `state.*` tokens; priority: low green, normal muted, high amber, very high red). Phone: Queue · Besitzer · Priorität · Status. E-mail: Queue · Priorität · Status (From stays where it is). Two columns below 700 px.
7. **Weitere Felder**: collapsed by default, header shows what is inside ("Verantwortlicher, Typ, 2 Zusatzfelder"); contains Verantwortlicher, Typ, Service, SLA, optional dynamic fields. Pending time input appears directly under the bar when a pending state is chosen (not collapsed).
8. **Note area**: Gesprächsnotiz textarea with a footer bar: "Notiz aufbereiten", "Datei", time accounting chip; the call timer moves into the call strip.
9. Call ID is not stored (no consumer) — out of scope.

## Review Focus

- Customer with no previous tickets and no company tickets → configured default or first non-junk queue, never Junk. Test.
- Suggested queue the agent has no create permission for → skipped, next rule. Test.
- Agent changes the queue, then changes the customer → queue stays. Test.
- Sysconfig `QueueDefault` naming a queue that doesn't exist / is invalid → ignored. Test.
- Phone screen with a required dynamic field → visible outside "Weitere Felder" and still enforced. Test.

---

### Task 1: Backend — queue suggestion, default queue, dynamic-field fallback, owner in CTI

**Files:** `backend/src/tiqora/api/v1/reference.py` (dynamic fields ~:398-470), a customers or tickets router for the suggestion (find where customer endpoints live), `backend/src/tiqora/znuny/sysconfig.py` (read QueueDefault keys), `backend/src/tiqora/channels/phone/cti.py` + call payload/SSE (answering user id), tests.

**Interfaces (produce):**
- `GET /api/v1/customers/{customer_user_login}/suggested-queue?screen=phone|email` → `{queue_id: int | null, source: "customer" | "company" | "default" | "fallback" | null}` — applies rules 1 (all steps after the URL param) for the requesting agent. Also `GET /api/v1/tickets/new/default-queue?screen=phone|email` → same shape without the customer steps (used before a customer is known and for "Ohne Kunde").
- Dynamic fields endpoint: nothing configured → `[]`; response items gain `required: bool` if not already present.
- CTI call payload (the object the popup receives via SSE and `list_active_calls`) gains `answered_by_user_id: int | null` (set on `answered` when the extension maps to exactly one user).

- [ ] Tests first for each rule in Decisions 1-3 and the Review Focus backend items; then implement; full suite + gates; regenerate openapi; commit `feat(tickets): suggest a queue from the customer's history, no junk default, only configured phone fields`.

### Task 2: Frontend — TicketPropsBar in both variants + queue suggestion

**Files:** create `frontend/src/components/agent/TicketPropsBar.tsx` (+ test); modify `frontend/src/routes/agent/NewTicketPage.tsx` (+ test), `frontend/src/lib/phoneApi.ts` or a tickets API module for the two new endpoints; i18n.

- [ ] Tests: bar renders the four (phone) / three (e-mail) cells with values and source lines; selecting a value calls onChange; queue suggestion applied on customer set, not after a manual pick; source line texts; submit label switches with a closed state. Implement per Decisions 1, 4, 6. Commit `feat(ui): property bar and customer-based queue suggestion for new tickets`.

### Task 3: Frontend — phone layout: call strip, Weitere Felder, owner/status prefill

**Files:** create `frontend/src/components/agent/phone/PhoneCallStrip.tsx` (+ test); modify `NewTicketPage.tsx`, `frontend/src/components/agent/phone/PhoneTicketFields.tsx` (split into "Weitere Felder" content), `frontend/src/components/agent/phone/CallPopup.tsx` (pass `owner_id` from `answered_by_user_id`), i18n.

- [ ] Tests: strip shows badge from popup params and the toggle when manual, with the effect hint; owner prefilled from `?owner_id=` with "hat angenommen"; status default closed-successful when `call_ended` present; "Weitere Felder" collapsed with its summary, required dynamic field visible outside; timer lives in the strip; note footer has refine/attach/time. Implement per Decisions 2, 3, 5, 7, 8. Commit `feat(ui): compact phone ticket with call strip and collapsed extra fields`.
