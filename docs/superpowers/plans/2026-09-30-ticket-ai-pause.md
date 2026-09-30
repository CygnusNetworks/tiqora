# Ticket AI Pause Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent can switch off all automatic AI actions (auto-reply/auto-draft, triage, auto-summary) for one ticket, and switch them back on, from the ticket view.

**Architecture:** A new nullable `ai_paused_at` / `ai_paused_by` pair on `tiqora_ai_ticket_state` (the per-ticket AI table that already holds `ai_escalated_at`). The three worker gates skip a paused ticket. Two new routes next to the existing `POST /tickets/{id}/ai/resume`. The ticket AI panel gets a pause toggle and a banner. Unlike `ai_escalated_at`, the pause is never cleared automatically (not by an agent reply, not by closing); only the explicit unpause clears it.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic (`backend/alembic/versions_tiqora`), React + TanStack Query, i18next.

**Spec:** User request 2026-09-30: "Kann ich bei einem Ticket den KI Automatismus ausschalten?" Research summary (code facts) is in this plan's "Code facts" section.

## Global Constraints

- Commit directly to `main`; no branches, no PRs, never push to GitHub.
- Backend tests: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (never plain `uv run pytest`).
- Lint gates, all three before each commit touching backend: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src` (in `backend/`).
- Frontend: `cd frontend && pnpm lint && pnpm test --run` (pnpm lint = eslint + tsc). Never run prettier/biome on `frontend/`.
- After any change to a FastAPI request/response model or route: regenerate `packages/api-client/openapi.json` (see skill `tiqora-openapi-regen-or-ci-wipes-schema`); never hand-edit `schema.d.ts`.
- New i18n keys go into `en.json` + `de.json`, then propagate to all locales per skill `tiqora-i18n-key-propagation`; `node frontend/scripts/check-i18n-keys.mjs` must pass.
- New Alembic migration: primary chain `backend/alembic/versions_tiqora`, `down_revision` = current primary head (`20260929_0051` at plan time — verify with `ls` first; if Plan "LLM Routing" already added `0052`, chain onto that). Follow skill `alembic-two-chain-new-head-forks-graph`; update any test that hardcodes the head id.
- Manual actions (manual draft "KI-Entwurf", refine, manual summary) stay available while paused. Pause only stops automatic background actions.

## Code facts

- Worker gates: auto-reply `_cap_reason` in `backend/src/tiqora/ai/auto_worker.py` (~:159, next to the `ai_escalated_at` check ~:172); triage `_skip_reason` / `_ticket_guard_row` in `backend/src/tiqora/ai/triage_worker.py` (~:141-192; the SQL already joins `tiqora_ai_ticket_state`); auto-summary `auto_summary_due` in `backend/src/tiqora/ai/summary.py` (~:733, already loads the state row); agent auto-trigger block in `run_ticket_agent`, `backend/src/tiqora/ai/runtime.py` (~:1228-1258).
- Escalation helpers: `backend/src/tiqora/ai/handoff.py` (`mark_ai_escalated`, `clear_ai_escalated`).
- Resume route: `backend/src/tiqora/api/v1/ai.py:1040` (`resume_ai_route`, uses `_assert_note_permission`); domain service `resume_ai_automation` in `backend/src/tiqora/domain/ticket_write_service.py` (~:916) writes an internal note "AI-Automatisierung reaktiviert".
- Ticket AI state response: `GET /tickets/{id}/ai` in `backend/src/tiqora/api/v1/ai.py` (~:521, `ai_escalated_at` at ~:191/:582).
- Frontend: `frontend/src/components/agent/AiPanel.tsx:1166-1195` (escalation banner + resume button `ai-panel-resume-button`), API client `frontend/src/lib/ticketAiApi.ts:158`.

## Review Focus

- Pausing a ticket while an auto-reply run is already in flight: the run finishes (no mid-run abort), but the NEXT customer article must be skipped. Test: pause, then feed an ArticleCreate event → skip reason `paused`.
- Pause must survive a customer-visible agent reply and a close/reopen (unlike escalation). Test: pause, `add_article` visible agent reply → still paused.
- Unpause must not retroactively process customer articles that arrived while paused (the outbox events were consumed). The unpause note says so. Test: event consumed while paused is not reprocessed after unpause.
- Pause and escalation are independent: resuming escalation does not unpause; unpausing does not clear escalation. Test both directions.
- A user without note permission on the ticket's queue gets 403 on pause/unpause (same rule as resume).

---

### Task 1: Backend — column, gates, routes

**Files:**
- Create: `backend/alembic/versions_tiqora/20260930_0052_ai_ticket_pause.py` (renumber if 0052 is taken)
- Modify: `backend/src/tiqora/ai/models.py` (`TiqoraAiTicketState`)
- Modify: `backend/src/tiqora/ai/handoff.py` (add `set_ai_paused`, `clear_ai_paused`)
- Modify: `backend/src/tiqora/ai/auto_worker.py`, `backend/src/tiqora/ai/triage_worker.py`, `backend/src/tiqora/ai/summary.py`, `backend/src/tiqora/ai/runtime.py`
- Modify: `backend/src/tiqora/domain/ticket_write_service.py` (add `pause_ai_automation`, `unpause_ai_automation`)
- Modify: `backend/src/tiqora/api/v1/ai.py` (routes + state fields)
- Test: `backend/tests/test_ai_ticket_pause.py` (new)

**Interfaces:**
- Produces (DB): `tiqora_ai_ticket_state.ai_paused_at DATETIME NULL`, `ai_paused_by INT NULL`.
- Produces (Python): `async def set_ai_paused(session, ticket_id: int, user_id: int) -> None` (upserts the state row like `mark_ai_escalated` does), `async def clear_ai_paused(session, ticket_id: int) -> None`.
- Produces (HTTP): `POST /api/v1/tickets/{ticket_id}/ai/pause` → 204; `POST /api/v1/tickets/{ticket_id}/ai/unpause` → 204. Both idempotent. `GET /api/v1/tickets/{ticket_id}/ai` response gains `ai_paused_at: datetime | None` and `ai_paused_by_name: str | None` (full name of the user, resolved like other user names in that module; `None` if unknown).
- Produces (skip reason string): `"ai_paused"` in auto-reply and triage skip reasons.

- [ ] **Step 1: Write failing tests** in `backend/tests/test_ai_ticket_pause.py`, modelled on the existing escalation tests (find them with `grep -rln ai_escalated_at backend/tests`), covering:
  1. `_cap_reason` returns `"ai_paused"` when `ai_paused_at` is set (auto-reply skipped, event consumed).
  2. triage skips a paused ticket with reason `"ai_paused"`.
  3. `auto_summary_due` returns False for a paused ticket.
  4. `POST .../ai/pause` sets `ai_paused_at`/`ai_paused_by`, writes an internal note (subject "KI-Automatik pausiert", body "Die KI-Automatik wurde für dieses Ticket pausiert. Neue Kundennachrichten werden nicht automatisch bearbeitet."), second call is a no-op (no second note).
  5. `POST .../ai/unpause` clears it and writes internal note (subject "KI-Automatik fortgesetzt", body "Die KI-Automatik wurde für dieses Ticket fortgesetzt. Nachrichten, die während der Pause eingegangen sind, werden nicht nachträglich bearbeitet.").
  6. A visible agent reply does NOT clear `ai_paused_at` (but still clears `ai_escalated_at`).
  7. Resume (escalation) does not clear pause; unpause does not clear escalation.
  8. 403 for a user without note permission (reuse the fixture used by the resume-route test).
  9. `GET .../ai` returns `ai_paused_at` and `ai_paused_by_name`.
- [ ] **Step 2: Run** `cd backend && uv run python -m pytest tests/test_ai_ticket_pause.py -q` → FAIL.
- [ ] **Step 3: Migration** — add both columns (`sa.DateTime()` / `sa.Integer()`, nullable), downgrade drops them. Docstring in the style of `20260919_0047_ai_final_answer_model.py`.
- [ ] **Step 4: Model + helpers** — add the two mapped columns with a comment: "Set by an agent to stop all automatic AI actions on this ticket (auto-reply, triage, auto-summary). Unlike ai_escalated_at, never cleared automatically." Add `set_ai_paused` / `clear_ai_paused` to `handoff.py`.
- [ ] **Step 5: Gates** — in each of the four places add the check right next to the existing `ai_escalated_at` check (auto-summary: first check). Update the misleading comment at `auto_worker.py:168-170` if it still claims closing clears escalation without code doing so — only if you verify it's wrong; otherwise leave it.
- [ ] **Step 6: Service + routes** — `pause_ai_automation` / `unpause_ai_automation` in `ticket_write_service.py` mirroring `resume_ai_automation` (internal note via `add_article`, skip note + write when already in target state). Routes in `api/v1/ai.py` mirroring `resume_ai_route` (same permission check). Add fields to the GET state response.
- [ ] **Step 7: Run tests** → PASS; then the full backend suite + three lint gates.
- [ ] **Step 8: Regenerate openapi.json**, commit: `feat(ai): pause all automatic AI actions for a single ticket`.

### Task 2: Frontend — pause toggle in the AI panel

**Files:**
- Modify: `frontend/src/lib/ticketAiApi.ts` (add `pauseTicketAi(ticketId)`, `unpauseTicketAi(ticketId)`)
- Modify: `frontend/src/components/agent/AiPanel.tsx`
- Modify: `frontend/src/i18n/locales/en.json`, `de.json` (+ propagation)
- Test: `frontend/src/components/agent/AiPanel.test.tsx` (extend existing, or create if absent)

**Interfaces:**
- Consumes: routes and `ai_paused_at` / `ai_paused_by_name` from Task 1 (via regenerated schema types).

- [ ] **Step 1: Failing tests**: (a) panel shows a "KI für dieses Ticket pausieren" control (`data-testid="ai-panel-pause-button"`) when not paused; clicking calls the pause endpoint and invalidates the ticket-AI query; (b) when paused, a banner (`data-testid="ai-panel-paused-banner"`) reads "KI-Automatik pausiert von {{name}} am {{date}}" (date formatted like the escalation banner's date) with a "Fortsetzen" button (`ai-panel-unpause-button`) that calls unpause; (c) manual AI buttons (draft/summary) stay enabled while paused.
- [ ] **Step 2: Run** `pnpm test --run AiPanel` → FAIL.
- [ ] **Step 3: Implement.** Place the pause control in the AI panel header/actions area near where the escalation banner lives; reuse the escalation banner's visual pattern (same classes) for the paused banner, but use the neutral/muted tone (`border-hairline bg-surface-subtle text-muted`) rather than the escalation color. Add a small hint line under the banner: "Auto-Antwort, Triage und automatische Zusammenfassung sind für dieses Ticket aus. Manuelle KI-Funktionen gehen weiter." i18n keys under `ticket.ai.pause.*`: `pause`, `unpause`, `banner`, `hint`, `error`. English: "Pause AI for this ticket", "Resume", "AI automation paused by {{name}} on {{date}}", "Auto-reply, triage and automatic summaries are off for this ticket. Manual AI features keep working.", "Could not change the AI pause. Please try again."
- [ ] **Step 4: Propagate i18n keys** to all locales (skill `tiqora-i18n-key-propagation`), run `node frontend/scripts/check-i18n-keys.mjs`.
- [ ] **Step 5: `pnpm lint && pnpm test --run`** → PASS. Commit: `feat(ui): pause and resume AI automation per ticket`.
