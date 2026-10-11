# Wave 3 — Web chat phase 2 (AI first line, identified visitors) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The web chat answers first with the existing AI auto-reply machinery (KB grounding, PII masking, per-queue caps and token budgets, kill switch, handoff) and hands over to a human cleanly — on the AI's own decision, on a limit, or whenever the visitor presses "Mit einem Menschen sprechen" — with the agent seeing the whole bot conversation. Visitors are recognised without asking: logged-in portal customers on Tiqora's own portal pages, and visitors on customer websites through a signed identity token the website's backend issues (`Tiqora.chat.identify(token)`).

**Architecture:** Builds on the finished phase-1 package `tiqora.webchat` and the widget `packages/webchat-widget`. The reserved session status `bot` becomes real: a chat whose topic has `ai_mode = "first_line"` starts in `bot`; each visitor line enqueues a *bot turn*. Bot turns run in `tiqora-ai-worker` (the AI kill-switch process) in a new low-latency loop (Redis list + DB rescan, bounded concurrency) — **not** through the outbox auto-reply tick, which keeps skipping `webchat` (`AUTO_REPLY_SKIP_CHANNELS`). A bot turn reuses `run_ticket_agent(trigger=auto, source_channel="webchat")`; the runtime gains a webchat dispatch branch that posts the answer as a `bot` transcript line plus a normal customer-visible article by the queue's AI service user. Handoff moves `bot → waiting` (ring as in phase 1) or, with nobody available, `bot → ended(no_agent)` with the phase-1 email follow-up prompt. Identity: a small HS256 compact-JWT codec (`webchat/identity.py`, no new dependency) verifies website tokens with a per-chat encrypted `identity_secret`; the portal case goes through one cookie-reading endpoint that returns a short-lived, Tiqora-signed assertion of the same format, so session creation has exactly one identity path.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic two-chain, Pydantic v2, redis.asyncio (lists + pub/sub); Preact 10 widget (Vite, Vitest, Playwright on a second origin); React 19 + TanStack Query in the agent/admin app.

**Spec:** `docs/superpowers/specs/2026-10-08-webchat-channel-design.md` — "Beyond parity" items 1 (AI first line) and 4 (identified visitors), "Visitor identity", "Bot / AI first line", the `offline_mode` value `ai` of "Tables", and the phase-2 row of "Phases" restricted to *AI first line + handoff* and *identified visitors (portal + signed token)* (wave 3 of `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md`, table "Waves": "Webchat 2 (AI first line, identity)"). Builds on `docs/superpowers/plans/2026-10-10-wave2-webchat.md` (phase 1, its Design C0–C11 and Deviations 1–29) and its rulings `<repo>/.superpowers/sdd/2026-10-10-wave2-webchat/rulings.md` — every ruling there stays binding (R-TX, R-ORIGIN, R-PREAUTH, R-LOCK-ORDER, R-QUEUE-SOT, R-CHANNEL-ROW, R-NOTIFY, R-REPLY-ACCEPT-FIRST, R-GERMAN-SIE, R-MIG, R-XPLAN …).

## Open questions for the product owner

Each item below is decided conservatively in this plan (marked **Assumption:** where it is used) and can be flipped later without a redesign.

1. **Per-ticket AI caps also limit the bot.** The queue policy's `max_auto_replies` (default 5) and `max_clarifications` (default 2) count bot answers, so a chat hands over after five bot answers. Alternative: a chat-specific "max bot turns" per topic. (C6, Task 8)
2. **The bot needs the queue's auto-reply switch.** A topic's first line runs only when the queue's AI policy has `enabled_auto_reply` and a service user; in `tiqora_primary` mode that also answers emails of that queue. Recommendation in the docs: a dedicated chat queue. Alternative: a separate "chat bot" switch on the policy. (C5)
3. **No automatic per-message drafts (`ai_mode = assist_only`).** Only `off | first_line` are built; the manual "KI-Entwurf" already works on chat tickets. (Deviation 2)
4. **Identity tokens:** HS256 only, `exp` required and at most 24 h ahead, no grace period for the old secret after "Neu erzeugen". (C4)
5. **Unknown customers from a website token are not created** unless the chat's new switch "Unbekannte Kunden anlegen" is on (default off); when created, `customer_id` = the e-mail address. Without the switch an unknown e-mail is used like an inbound mail sender (`customer_user_id` = e-mail); an unknown `sub` without e-mail is stored as the free-text `customer_user_id`. (C4)
6. **Nobody available at handoff → the chat ends** with the phase-1 "Wohin dürfen wir antworten?" e-mail prompt; the bot does not keep chatting after a handoff (the AI handoff flag stops it anyway). (C6)
7. **The AI notice cannot be switched off in chat.** Every bot session starts with a system line "Sie schreiben mit einem KI-Assistenten …" (EU AI Act transparency); the policy's disclosure text is used as its wording when set, but the per-message disclosure footer is not added to chat lines. (C3, Deviation 5)
8. **A bot-only chat that ends after a bot answer parks the ticket** in the state the queue policy allows for a sent auto-reply (same rule the runtime applies after an e-mail auto-reply), at chat end instead of after every bot line. (C6)
9. **Agents may take over a bot chat at any time** ("Übernehmen" in the inbox section "Mit KI im Gespräch", or by replying); capacity limits apply. (C8)
10. **Scope:** the other phase-2 items of the spec — transfer/invite, waiting-list position, block lists + proof-of-work, rating, the `chat_sessions` reporting source, the transcript-as-one-article option and "KI-Zuordnung" (triage) for chats — are not in this plan; they need a "web chat phase 2b" slot in wave 4/5. (Deviation 9)
11. **No streaming of bot answers token by token** — a bot answer arrives as a whole after a "KI-Assistent schreibt …" indicator. (Deviation 1)

## Global Constraints

- **Where:** a new worktree off `roadmap-wave1` **after** the wave-2 stage-2 merge (webchat merged, migration head `20261010_0072` at the time of writing) — path and branch name come from the controller (pattern of `implementer-instructions-wave2chat.md`). Never touch `<repo>` (main); never commit to main; never push (a push to origin main deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Use `rtk git`; check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Stage explicit paths only and run `rtk git diff --cached --name-only` before every commit. Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. zsh has `noclobber`: overwrite files with `>|` or the Write tool.
- **Only made-up example data** (`example.org`, `example.com`, `.test`; names like "Erika Muster"). No real customer data in tests, docs, fixtures or demo mocks.
- **Tables:** write only `tiqora_*` tables directly. Every Znuny write (ticket, article, history, customer, lock/owner, state) goes through the existing write services (`tiqora.domain.ticket_write_service`: `create_ticket`, `add_article`, `set_customer`, `lock_ticket`, `assign_owner`, `change_state`; `tiqora.domain.user_provisioning.create_customer_user`) so history rows, `change_time` and TiqoraSync cache invalidation stay identical to Znuny. Never change Znuny DDL.
- **No JOIN between `tiqora_*` and Znuny tables** on string columns (prod MariaDB mixes `general_ci`/`unicode_ci` → error 1267): load keys, then query with an IN-list. `tiqora_*`↔`tiqora_*` joins are fine.
- **Transactions (R-TX):** SQLAlchemy 2 autobegins on the first read — never `async with db.begin()` after any read on the same session; do pre-checks in their own block and `commit()`/`rollback()` before the write block, or run every read inside the write transaction. One full-POST test per new router endpoint against a real DB session.
- **Lock order (R-LOCK-ORDER, extended):** agent-state row (accept only) → `tiqora_chat_session` row (`FOR UPDATE` / `allocate_seq`) → Znuny ticket writes → `insert_message`. Any code path that changed ticket fields earlier in the same transaction (AI tools) commits before taking the session lock (Task 7).
- **No long synchronous endpoints:** prod nginx has `proxy_read_timeout 90s` on `/api/`. Every new endpoint here is short; LLM work runs only in `tiqora-ai-worker`. Nothing in an API request waits for an LLM.
- **Both dialects:** DB tests run on MariaDB AND PostgreSQL: `@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])` + `request.getfixturevalue(url_fixture)` + `_to_async_url` (pattern file: `backend/tests/test_admin_postmaster_filters.py`). `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never plain `uv run pytest`). New DB test modules clean up after themselves (shared helpers `backend/tests/_webchat_fixtures.py`, `tests._row_cleanup.snapshot_marks` / `delete_above_marks`) and are never added to `backend/tests/db_leak_baseline.txt`.
- **Lint gates before every backend commit:** `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora` (ruff format is what turns CI red; pytest green means nothing on its own).
- **Seed ids** only inside this plan's band **54400–54799** (sub-bands per task under "Tasks"); confirm free first with `grep -rn "54[4-7][0-9][0-9]" backend/tests` — if another wave-3 plan already took it, move the whole band to the next free 400-wide block and say so in the first commit. Seeded logins/names contain `wc54`.
- **Migrations:** one new migration in `backend/alembic/versions_tiqora`, numbered with the **next free number at implementation time** after the then-current primary head (`ls backend/alembic/versions_tiqora | tail -1`, `cd backend && uv run alembic heads`; other wave-3 plans add heads too — never trust an id written here); `down_revision` = that head; follow skill `alembic-two-chain-new-head-forks-graph` (never rebase the gated chain); update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version == …` assertion in `backend/tests/test_migrate_cli.py`; ORM in `backend/src/tiqora/db/tiqora/models.py` produces the same DDL (`backend/tests/test_tiqora_model_ddl.py`).
- **SysConfig/YAML booleans** are Perl-truthy strings: read with `SysConfig.get_bool` / `tiqora.util.perl.perl_truthy`, never `bool(...)`.
- **Timestamps** written into `tiqora_chat_*` are naive UTC from `tiqora.jobs.service.utc_now()`; never compare them with `func.now()`. API datetimes use `tiqora.domain.schemas.UtcDateTime`.
- **OpenAPI:** after any API model/route change regenerate per skill `tiqora-openapi-regen-or-ci-wipes-schema`: `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`; check `head -c 40 packages/api-client/openapi.json` and that the diff is hundreds of lines, not five figures. Never hand-edit `schema.d.ts`. A response field WITH a default becomes required in TS — fixtures and demo mocks must include it.
- **Frontend (`frontend/`):** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` (`pnpm lint` = eslint + tsc). Never run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). German UI strings address the user with **"Sie/Ihr"** (R-GERMAN-SIE). New screens get demo-mode mocks (`frontend/src/demo/webchatMock.ts`) and are checked with `VITE_DEMO=1` + Playwright (delete `.playwright-mcp` before committing). Tailwind colour tokens only via `frontend/src/themes/tokens.css` + `themeColor()`.
- **Widget (`packages/webchat-widget/`):** `pnpm --filter @tiqora/webchat-widget lint && pnpm --filter @tiqora/webchat-widget test && pnpm --filter @tiqora/webchat-widget build` (build runs the size check: loader ≤ 5 120 B gz, frame ≤ 40 960 B gz incl. the largest locale chunk — R-BUDGET; never raise a budget, report instead). No `innerHTML`/`dangerouslySetInnerHTML`/`eval`/… (existing ESLint rule). Frame API code never imports `@tiqora/api-client` runtime code (types only, R-FRAME-CONNECT). No new npm dependencies.
- **Security (internet-facing):** every new public endpoint follows R-PREAUTH order (IP backstop → same origin → channel/config cache → token → per-session limits); identity failures count against a per-IP bucket; raw IPs are never stored; visitor responses never contain ticket id/number, queue, article id, user id, agent login/e-mail, internal notes, handoff notes or the AI's escalation reason. Identity secrets are stored only encrypted (`tiqora.crypto.secret.encrypt_secret`) and shown once.
- **UI copy:** plain words, German first: "KI-Assistent", "Mit einem Menschen sprechen", "KI antwortet zuerst", "Mit KI im Gespräch", "Übernehmen", "Identität bestätigt", "Website-Anmeldung", "Schlüssel für die Website-Anmeldung", "Im Kundenportal anzeigen". No "JWT", "Token", "Bot-Turn", "Handoff" in UI text (code/docs only; the admin may see "Schlüssel" and "signierte Anmeldung").

## Review Focus

1. **The bot never talks over a human and never answers twice.** Agent takes over while a bot turn runs; visitor sends three lines while the LLM thinks; the AI worker restarts mid-turn; two worker instances pick the same session → exactly one bot answer per visitor burst, none after the takeover (the late answer becomes a draft), no lost turn. Pinned in Task 8 (`test_takeover_during_turn_stores_draft_not_message`, `test_visitor_burst_supersedes_then_one_answer`, `test_claim_is_exclusive_both_dialects`) and Task 9 (`test_stale_claim_is_retaken_after_restart`).
2. **Every limit ends in a human handoff, never in silence.** Queue token budget, hourly cap, per-ticket cap, global kill switch, provider budget, LLM error ×3, policy switched off mid-chat → `handoff` line + `waiting` (ring) or `ended(no_agent)` + e-mail prompt. Pinned in Task 8 (`test_cap_reason_hands_off[...]`, `test_llm_error_three_times_hands_off`) and Task 5 (`test_bot_unavailable_routes_to_humans`).
3. **Identity tokens cannot be forged, replayed across chats, or downgraded.** `alg: none`, `alg: HS512`/`RS256`, `kid: tiqora-portal` on a website token, wrong secret, expired, `exp` > 24 h, `aud` of another chat, a portal assertion of another visitor id, 5 KB token, bad base64/JSON → refused with one code, nothing linked. Pinned in Task 2 (`test_verify_rejects_*`) and Task 3 (`test_portal_assertion_bound_to_visitor_and_chat`).
4. **The portal cookie never leaks into the public chat API.** Only `POST /api/chat/v1/identity/portal` reads it, only for the own origin with `show_on_portal`, and the frame sends cookies only on that call. Pinned in Task 4 (`test_portal_identity_requires_own_origin_and_portal_flag`, `test_other_chat_endpoints_ignore_portal_cookie`) and Task 12/13 (frame `credentials` test).
5. **A shared computer does not show the previous person's chat.** `Tiqora.chat.logout()` and an `identify()` for another person clear the stored session; the server answers `identity_conflict` for a session that belongs to someone else. Pinned in Task 3 (`test_identify_conflict_on_other_verified_identity`) and Task 13 (`logout clears the stored session and shows a fresh start`).

Also pinned: the bot reply is a normal article (Znuny shows it, golden check in Task 17); webchat stays in `AUTO_REPLY_SKIP_CHANNELS` so no outbox auto-reply ever reaches a chat visitor (Task 5 `test_outbox_tick_still_skips_webchat`); the agent sees the full bot conversation and the AI's handoff note (Task 8); bot answers count against the queue's daily token budget (Task 8 `test_bot_usage_counts_against_queue_budget`).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Existing names used exactly as they are in code (phase 1 + AI): `ChatError`, `VisitorContext`, `create_session`, `post_visitor_message`, `store_visitor_attachment`, `post_visitor_attachment`, `_store_visitor_line`, `add_system_message`, `end_by_visitor`, `set_contact`, `visitor_from_address`, `current_queue_id`, `LIVE_STATUSES` (`webchat/store.py`), `get_chat_session`, `allocate_seq`, `insert_message`, `live_session_for_ticket`, `config_values`, `_JSON_COLUMNS`, `_SCALAR_COLUMNS`, `queue_wakeup`, `queue_agent_event`, `flush_chat_notifications`, `publish_chat_wake`, `publish_chat_typing`, `ChatHub`, `eligible_agent_ids`, `within_business_hours`, `config_availability`, `topic_ref`, `accept_session`, `accept_waiting_chat_for_reply`, `deliver_agent_chat_reply`, `end_session_by_agent`, `list_inbox`, `ticket_chat_info`, `_joined_name`, `message_out`, `session_out`, `public_config_cache`, `own_origin`, `origin_allowed`, `frame_csp`, `run_webchat_sweep_tick`, `_abandon_idle`, `resolve_chat_customer`, `validate_prechat`, `PrechatResult`, `ChatTopic`, `ChatConfigValues`; AI: `run_ticket_agent`, `AgentRunResult`, `STATUS_*`, `TRIGGER_AUTO`, `_map_customer_message`, `_resolve_auto_state_id`, `_resolve_telegram_tone_prompt`, `_cap_reason`, `_TICKET_CAP_REASONS`, `_hand_off_capped_ticket`, `_consume_grant`, `build_task_llm`, `NoUsableModel`, `kb_bundle`, `kb_search_fn`, `kb_get_article_fn`, `get_queue_policy_by_queue`, `get_or_create_state`, `mark_ai_escalated`, `create_draft`, `TIQORA_ONLY_CHANNELS`, `AUTO_REPLY_SKIP_CHANNELS`, `require_feature_allowed`, `queue_serves_tiqora_only_channel`, `is_auto_reply_paused`, `is_tiqora_primary`, `queue_tokens_used_today`, `record_usage`, `TiqoraAiArticleOrigin`; portal: `CustomerAuthService.resolve_session`, `CustomerSessionStore`, `AuthenticatedCustomer`, `portal_enabled`, `PortalShell`; frontend: `webchatApi`, `useTicketChat`, `WebchatSessionHeader`, `ChatInboxPanel`, `MessengerComposer`, `webchatAdapter`, `handleSSEMessage`, `WebchatConfigEditorPage`, tabs `GeneralTab`/`HoursTab`/`EmbedTab`.

### C0. File map

Backend (new unless marked):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/webchat/identity.py` | `IdentityError`, `IdentityClaims`, `verify_identity_token`, `sign_identity_token`, `portal_identity_key`, `PORTAL_KID`, `new_identity_secret` (pure) |
| `backend/src/tiqora/webchat/visitor_identity.py` | `ResolvedIdentity`, `resolve_identity`, `apply_identity_to_session`, `identify_session`, `issue_portal_assertion` |
| `backend/src/tiqora/webchat/bot.py` | `BotAvailability`, `bot_availability`, `run_chat_bot_turn`, `hand_off_chat`, `close_bot_resolved_ticket`, `BOT_QUEUE_KEY`, `BOT_ALIVE_KEY` |
| `backend/src/tiqora/webchat/bot_delivery.py` | `BotChatTakenOver`, `deliver_bot_chat_reply`, `post_bot_intro`, `DEFAULT_WEBCHAT_TONE_PROMPT`, `webchat_tone_prompt` |
| `backend/src/tiqora/ai/auto_run.py` | `auto_reply_cap_reason` (moved from `auto_worker._cap_reason`), `prepare_auto_run`, `AutoRunInputs` |
| `backend/src/tiqora/ai/chat_bot_loop.py` | `run_chat_bot_loop` (inside `tiqora-ai-worker`) |
| `backend/src/tiqora/api/portal/webchat.py` | `GET /api/portal/webchat` |
| `backend/tests/_ai_scripted_llm.py` | copy of `ScriptedLlm` + response builders from `test_ai_runtime.py` (that file stays untouched) |
| `backend/tests/_webchat_bot_fixtures.py` | policy/service-user/topic seeding for bot tests in the 544xx–547xx band |
| modify | `webchat/models.py`, `webchat/store.py`, `webchat/sessions.py`, `webchat/agent.py`, `webchat/availability.py`, `webchat/notify.py`, `webchat/hub.py`, `webchat/stream.py`, `webchat/serialize.py`, `webchat/sweep.py`, `webchat/schemas.py`, `webchat/public_api.py`, `webchat/static.py`, `webchat/origins.py`, `webchat/prechat.py`, `webchat/config_cache.py`, `api/v1/chat.py`, `api/v1/admin/chat_configs.py`, `api/portal/__init__.py`, `ai/runtime.py`, `ai/auto_worker.py`, `ai/gate.py`, `ai/worker.py`, `db/tiqora/models.py`, `alembic/versions_tiqora/<next>`, `config.py`, `events/pubsub.py` (docstring only) |

Widget (`packages/webchat-widget/`): modify `src/shared/protocol.ts` (+test), `src/loader/hostApi.ts`, `src/loader/boot.ts`, `src/loader/bridge.ts`, `src/frame/bridge.ts`, `src/frame/api.ts`, `src/frame/state.ts`, `src/frame/App.tsx`, `src/frame/storage.ts`, `src/frame/views/ChatView.tsx`, `MessageList.tsx`, `Composer.tsx`, `PrechatForm.tsx`; new `src/frame/views/HandoffButton.tsx`, `src/frame/identity.ts`; `scripts/mock-server.mjs`, `e2e/widget.spec.ts`, `e2e/host.html`.

Frontend app: modify `frontend/src/lib/webchatApi.ts`, `lib/useSSE.ts` (no new types, see C10), `components/agent/webchat/WebchatSessionHeader.tsx`, `ChatInboxPanel.tsx`, `ChatInboxButton.tsx`, `components/agent/messenger/MessengerComposer.tsx` (hint only), `routes/admin/WebchatConfigEditorPage.tsx`, `components/admin/webchat/HoursTab.tsx`, `EmbedTab.tsx`; new `components/admin/webchat/AiTab.tsx`, `components/admin/webchat/IdentitySecretSection.tsx`, `components/portal/PortalWebchat.tsx`; modify `components/layout/PortalShell.tsx`, `demo/webchatMock.ts`, `i18n/locales/*.json`.

### C1. Migration (one, Task 1)

```
tiqora_chat_config  (add)
  bot_name                   VARCHAR(80)  NULL              -- NULL → "KI-Assistent" (value model default)
  bot_tone_prompt            TEXT         NULL              -- optional chat tone addendum for the AI
  show_on_portal             BOOL NOT NULL default false    -- at most one valid config true (admin check)
  identity_create_customers  BOOL NOT NULL default false
  identity_secret            TEXT         NULL              -- encrypt_secret(settings.secret_key, secret); never in values

tiqora_chat_session  (add)
  identity_source            VARCHAR(10) NULL               -- "portal" | "website"; NULL = anonymous / typed e-mail
  last_visitor_seq           INT NOT NULL default 0         -- seq of the newest visitor line (bot pending check)
  bot_answered_seq           INT NOT NULL default 0         -- newest visitor seq a finished bot turn covered
  bot_claimed_at             DATETIME NULL
  bot_claim_owner            VARCHAR(64) NULL               -- "<host>:<pid>:<uuid8>"
  bot_attempts               SMALLINT NOT NULL default 0    -- failed turns in a row; reset on success
  bot_outcome                VARCHAR(12) NULL               -- "replied" | "handoff" (last bot result)
  handoff_at                 DATETIME NULL
  handoff_reason             VARCHAR(12) NULL               -- visitor|ai|limit|error|unavailable|takeover
  INDEX ix_tiqora_chat_session_bot (status, bot_claimed_at)
```

`tiqora_chat_session.status` gains the value `bot` (the column already allows 12 chars; phase 1 reserved it). `tiqora_chat_message.sender` gains `bot` (reserved in phase 1). No backfill: existing rows get the defaults.

### C2. Value model changes (`webchat/models.py`, Task 1)

```python
AiMode = Literal["off", "first_line"]
DEFAULT_BOT_NAME: Final = "KI-Assistent"

class ChatTopic(BaseModel):            # extra="forbid" stays; old JSON rows lack ai_mode → default "off"
    ...
    ai_mode: AiMode = "off"

class ChatConfigValues(BaseModel):     # new fields, all with defaults
    offline_mode: Literal["form", "ai", "hide"] = "form"
    bot_name: str = Field(default=DEFAULT_BOT_NAME, min_length=1, max_length=80)
    bot_tone_prompt: str | None = Field(default=None, max_length=2000)
    show_on_portal: bool = False
    identity_create_customers: bool = False
```

`store._SCALAR_COLUMNS` += `bot_name`, `bot_tone_prompt`, `show_on_portal`, `identity_create_customers` (`bot_name` NULL column → value default). `store.LIVE_STATUSES` becomes `frozenset({"bot", "waiting", "active"})` — every phase-1 check that uses it (visitor writes, `live_session_for_ticket`, stream end detection) then treats `bot` as live; Task 1 greps every literal `"waiting", "active"` pair in `webchat/` and `api/v1/chat.py` and decides each one explicitly (list them in the commit message).

`offline_mode` semantics: `form` and `hide` as in phase 1; `ai` = topics with `ai_mode == "first_line"` stay online with only the bot when nobody is available or outside business hours, every other topic behaves like `form`.

### C3. Statuses and system lines

```
create ──► bot      (topic first_line AND bot available AND (humans available OR offline_mode == "ai"))
       ──► waiting  (humans available; phase 1)
       ──► 409 offline (neither)
bot ──► waiting        hand_off_chat, humans available        (ring: chat_waiting)
bot ──► ended/no_agent hand_off_chat, nobody available         (e-mail prompt as phase 1)
bot ──► active         agent accepts/replies (takeover)        (agent_joined)
bot ──► ended/visitor  visitor ends;  ended/agent  agent ends;  abandoned/idle  sweep
```

New system line codes (`tiqora_chat_message.sender = "system"`, params in `meta`, translated in the widget as `webchatWidget.system.<code>`):

- `bot_intro` `{"name": str, "notice": str | None}` — first line of every bot session: "Sie schreiben mit {name}, einem KI-Assistenten. Sie können jederzeit „Mit einem Menschen sprechen“ wählen." `notice` = the queue policy's `ai_disclosure_text` when set (shown instead of the default sentence, plus the button hint). **Assumption:** always written (open question 7).
- `handoff` `{"reason": "visitor" | "ai" | "limit" | "error" | "unavailable"}` — "Ich verbinde Sie mit unserem Team …" (reason only selects the wording; never the AI's internal reason text).
- existing `no_agent` `{"has_email": bool}` follows `handoff` when nobody is available; existing `agent_joined` on takeover.

Bot transcript lines: `sender = "bot"`, `user_id` = policy service user, `meta = {"agent_name": <config bot_name>}`, `article_id` = the bot article. `ChatMessageOut.sender` becomes `Literal["visitor", "agent", "bot", "system"]`; `message_out` passes `agent_name` for `bot` too.

### C4. Identity

```python
# webchat/identity.py (pure)
PORTAL_KID: Final = "tiqora-portal"
MAX_TOKEN_LENGTH: Final = 4096
MAX_FUTURE_SECONDS: Final = 24 * 3600        # Assumption (open question 4)
LEEWAY_SECONDS: Final = 60
PORTAL_ASSERTION_SECONDS: Final = 600
class IdentityError(ValueError):
    code: Literal["malformed", "algorithm", "signature", "expired", "not_yet_valid",
                  "lifetime", "audience", "claims", "visitor"]
@dataclass(frozen=True, slots=True)
class IdentityClaims:
    sub: str | None           # ≤ 200, stripped, no controls
    email: str | None         # lower-cased domain, R-INPUT EMAIL_RE
    name: str | None          # ≤ 200, controls/bidi stripped
    source: Literal["portal", "website"]
    visitor_id: str | None    # portal assertions only
def new_identity_secret() -> str                               # secrets.token_urlsafe(32)
def portal_identity_key(secret_key: str) -> bytes              # hmac(secret_key, b"webchat-portal-identity-v1")
def sign_identity_token(claims: dict[str, Any], key: bytes, *, kid: str | None = None) -> str
def verify_identity_token(token: str, *, website_secret: str | None, portal_key: bytes,
                          audience: str, visitor_id: str | None, now: int) -> IdentityClaims
```

Verification rules (all failures → `IdentityError`, mapped to one public code `invalid_identity`):
compact form `b64url(header).b64url(payload).b64url(sig)` without padding, total ≤ 4096 chars, ASCII only; header JSON object with `alg == "HS256"` exactly (no `none`, no other alg, no `crit`, `jku`, `jwk`, `x5u`), `typ` absent or `"JWT"`; `kid == "tiqora-portal"` → portal key, else website secret (missing secret → `signature`); signature compared with `hmac.compare_digest` over the exact received `header.payload` bytes; payload JSON object; `exp` int required, `exp + LEEWAY < now` → `expired`, `exp > now + MAX_FUTURE_SECONDS` → `lifetime`; `nbf` (optional) `> now + LEEWAY` → `not_yet_valid`; `aud` optional for website tokens but must equal the chat's `public_key` when present, required and equal for portal assertions; at least one of `sub`/`email` (`claims`); portal assertions also need `vid == visitor_id` (`visitor`) and `src == "portal"`.

Linking (`visitor_identity.resolve_identity(db, *, claims, values) -> ResolvedIdentity`):
1. `sub` → `customer_user` by `LOWER(login) = LOWER(:sub)` and `valid_id = 1` (single Znuny table query, no join).
2. else `email` → `customer_user` by `LOWER(email) = LOWER(:email)`, valid; exactly one row → that user; several → treat as not found.
3. not found and `values.identity_create_customers` and `email` present → `create_customer_user(login=email, email=email, customer_id=email, first_name/last_name from name split (fallback "Besucher"/email local part), pw_hash=None, actor_id=1)`. **Assumption** (open question 5).
4. not found otherwise → `customer_user_id = email or sub`, `customer_id = email or sub` (inbound-mail convention). **Assumption** (open question 5).

```python
@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    source: Literal["portal", "website"]
    customer_user_login: str          # existing login or the free-text id
    customer_id: str | None
    email: str | None                 # verified address (from the token or the customer_user row)
    name: str | None
    known_customer: bool
```

Session effects: `visitor_name`/`visitor_email` from the identity (prechat values mapped to name/email are ignored); `customer_user_login` + ticket customer via `create_ticket` params (new chat) or `set_customer` (mid-chat identify); `identity_source` set; `TicketChatOut.visitor_email_verified = identity_source is not None and visitor_email is not None` (R-NOTIFY b becomes real).

Mid-chat `identify` rules: session live; `identity_source IS NULL` → apply (set_customer only when the ticket's customer is still the anonymous/typed one, i.e. equals `chat.customer_user_login` before); same identity (same resolved login) → 204 no-op; a different verified identity → `ChatError("identity_conflict", 409)`. An ended session → `session_closed` 409.

Portal: `issue_portal_assertion(customer: AuthenticatedCustomer, *, public_key, visitor_id, settings, now) -> str` signs `{"src": "portal", "sub": login, "email": email, "name": "First Last", "aud": public_key, "vid": visitor_id, "iat": now, "exp": now + 600}` with `kid = PORTAL_KID`.

### C5. Bot availability and routing (Task 5)

```python
BOT_ALIVE_KEY: Final = "tiqora:chat:bot:alive"        # SET EX 30 by the ai-worker bot loop every 10 s
@dataclass(frozen=True, slots=True)
class BotAvailability:
    ok: bool
    reason: str | None   # "ai_mode_off" | "no_policy" | "auto_reply_off" | "no_service_user" | "gate" |
                         # "paused" | "worker_down" | "queue_rate_limit" | "global_rate_limit" |
                         # "budget_tokens_day" | "llm_unavailable"
async def bot_availability(db, redis_client, settings, *, topic: ChatTopic, queue_id: int) -> BotAvailability
```

Order: `topic.ai_mode == "first_line"` → policy via `get_queue_policy_by_queue(queue_id)` with `enabled_auto_reply` and `service_user_id` (**Assumption**, open question 2) → `require_feature_allowed(FEATURE_AUTO_REPLY, source_channel="webchat")` passes (kill switch; `webchat` joins `TIQORA_ONLY_CHANNELS`, so `parallel` mode is allowed as the spec wants) → `BOT_ALIVE_KEY` exists (Redis error → `worker_down`) → the queue-level part of `auto_reply_cap_reason` (hourly, global, daily token budget — per-ticket caps do not apply before a ticket exists) → `build_task_llm(..., TASK_AGENT)` resolves (`NoUsableModel` → `llm_unavailable`). Cached per `(config_id, topic_key)` in Redis `tiqora:chat:botavail:<config>:<topic>` for 10 s (same pattern as `config_availability`).

`create_session` (Task 5): `humans = within_business_hours(...) and eligible_agent_ids(...)`; `bot = bot_availability(...)`; `bot.ok and (humans or values.offline_mode == "ai")` → status `bot` (`waiting_since` NULL, `bot_intro` line written after the visitor's first line, agent event `chat_message` instead of `chat_waiting` — no ring, enqueue a bot turn); else `humans` → phase 1 `waiting`; else `offline` 409. `config_availability` reports a topic online when `humans or (offline_mode == "ai" and bot.ok)`; `ChatPublicTopicOut.ai_first = topic.ai_mode == "first_line" and bot.ok`.

`ai/gate.py`: `TIQORA_ONLY_CHANNELS = frozenset({"telegram", "webchat"})`; `AUTO_REPLY_SKIP_CHANNELS` unchanged (`{"webchat"}`) and checked first in `run_auto_tick`, so the outbox path still never answers a chat article. `queue_serves_tiqora_only_channel(session, queue_id)` additionally returns True when any valid `tiqora_chat_config` has a topic with `queue_id == queue_id and ai_mode == "first_line"` (tiqora table only) — so an admin can switch auto-reply on for a chat queue in `parallel` mode; emails of that queue are still not auto-answered in `parallel` mode because `run_auto_tick` gates them per event.

### C6. Bot turn, claim and handoff (Tasks 8, 9)

Pending condition: `status == "bot" and last_visitor_seq > bot_answered_seq`. `_store_visitor_line` sets `last_visitor_seq = message.seq` (all statuses) and, when the status is `bot`, calls `queue_bot_turn(db, session_id)`.

```python
# notify.py
BOT_QUEUE_KEY: Final = "tiqora:chat:bot:queue"           # Redis list of session ids
def queue_bot_turn(db: AsyncSession, session_id: str) -> None   # post-commit RPUSH, deduped by
                                                                  # SET tiqora:chat:bot:queued:<sid> NX EX 120
# bot.py
BOT_CLAIM_STALE_SECONDS: Final = 180
BOT_MAX_ATTEMPTS: Final = 3
async def claim_bot_turn(db, *, session_id: str, owner: str, now: datetime) -> bool
    # UPDATE tiqora_chat_session SET bot_claimed_at=:now, bot_claim_owner=:owner
    #  WHERE id=:sid AND status='bot' AND last_visitor_seq > bot_answered_seq
    #    AND (bot_claimed_at IS NULL OR bot_claimed_at < :stale); commit; rowcount == 1
async def run_chat_bot_turn(factory, settings, redis_client, *, session_id: str, owner: str,
                            now_fn: Callable[[], datetime] = utc_now) -> str
    # returns "replied" | "handoff" | "superseded" | "skipped" | "retry"
async def hand_off_chat(db, sysconfig, redis_client, *, session_id: str,
                        reason: Literal["visitor", "ai", "limit", "error", "unavailable"],
                        note: str | None, now: datetime) -> Literal["waiting", "no_agent", "noop"]
async def close_bot_resolved_ticket(db, sysconfig, *, chat: TiqoraChatSession) -> None
```

`run_chat_bot_turn` (each step in its own short transaction — never hold a DB transaction across the LLM call except the runtime's own):
1. claim (else `skipped`); remember `target_seq = last_visitor_seq` at claim time.
2. `bot_availability` for the session's topic and CURRENT queue (R-QUEUE-SOT) — not ok → `hand_off_chat(reason="unavailable" | "limit" for the three cap reasons)`.
3. per-ticket part of `auto_reply_cap_reason` (escalated, paused, customer pause, grant, `max_auto_replies`, `max_clarifications`) — a hit → `hand_off_chat(reason="limit")` (**Assumption**: per-ticket caps apply, open question 1); `ai_paused`/customer pause → `reason="unavailable"`.
4. typing task: `publish_chat_typing(client, sid, who="bot")` every 3 s until the turn ends.
5. `run_ticket_agent(trigger=TRIGGER_AUTO, source_channel="webchat", worker_instance="chat-bot", **prepare_auto_run(...))`.
6. result mapping: `sent` → `bot_answered_seq = target_seq`, `bot_attempts = 0`, `bot_outcome = "replied"`, `_consume_grant` → `replied`; `superseded` → claim cleared, re-enqueue at once → `superseded`; `escalated` → `hand_off_chat(reason="ai", note=None)` (the escalate tool already wrote the AI's internal note — that note is the handoff summary, Deviation 3); `drafted` (autonomy `off`/`clarify_only` produced a draft, or the chat was taken over meanwhile) → if still `bot`: `hand_off_chat(reason="ai", note="Die KI hat einen Entwurf vorbereitet …")`; `no_reply`/`skipped` → `hand_off_chat(reason="ai", note="Die KI hatte keine Antwort für den Besucher.")`; `AgentRunError`/`LlmError`/other exception → `bot_attempts += 1`, claim cleared; `bot_attempts ≥ 3` → `hand_off_chat(reason="error", note="KI nicht erreichbar: <class name>")`, else `retry` (re-enqueue after 5 s).
7. every exit clears `bot_claimed_at`/`bot_claim_owner` (in `finally`, own transaction).

`hand_off_chat` (fresh transaction, R-LOCK-ORDER): lock session; status ≠ `bot` → `noop`; humans for the session's topic on its current queue (business hours + `eligible_agent_ids`) → `status="waiting"`, `waiting_since=now`; else `status="ended"`, `end_reason="no_agent"`, `ended_at=now`; `handoff_at=now`, `handoff_reason=reason`, `bot_outcome="handoff"`; when `note` → internal note article (`channel="note"`, `is_visible_for_customer=False`, subject "KI-Übergabe im Chat", user = policy service user or 1); `mark_ai_escalated(ticket, reason=f"webchat_{reason}")`; system lines `handoff` then (no human) `no_agent {"has_email": bool(visitor_email)}`; agent event `chat_waiting` + `topic_ref` (ring) or `chat_ended`; wake.

`close_bot_resolved_ticket`: when a session ends from `bot` (visitor end, agent end of a bot chat, idle) with `bot_outcome == "replied"` and `handoff_at IS NULL` → `resolve_auto_state_id(kind=DRAFT_KIND_REPLY, allowed_state_types=policy)` and `change_state` by the service user (same policy gate the runtime uses after a sent e-mail auto-reply; nothing when the policy allows no such state). **Assumption** (open question 8).

### C7. Runtime webchat dispatch (Task 7)

In `run_ticket_agent`, the `destination == "send"` block checks `src.channel.lower() == "webchat"` **before** Telegram and e-mail (the visitor's `a_from` may be a real address or `…@chat.invalid` and must never reach SMTP):

```python
# bot_delivery.py
class BotChatTakenOver(Exception): ...
async def deliver_bot_chat_reply(db, sysconfig, *, ticket_id: int, service_user_id: int,
                                 subject: str, body: str, now: datetime) -> int
    # commit-first rule: caller committed tool writes; lock live session FOR UPDATE; status != "bot"
    # → BotChatTakenOver; body = chat_body_from_article(body, "text/plain"); add_article(sender agent,
    # visible, channel "webchat", to_address visitor_from_address(...), content text/plain) by the
    # service user; insert_message(sender="bot", meta={"agent_name": bot_name}); queue_wakeup;
    # queue_agent_event(chat_message); returns the article id
async def post_bot_intro(db, *, chat, values: ChatConfigValues, notice: str | None, now) -> None
DEFAULT_WEBCHAT_TONE_PROMPT: Final[str]   # short plain-text chat answers, no greeting/signature,
                                          # no Markdown headings, one question at a time, escalate with
                                          # a reason that summarises the visitor's problem and what was tried
async def webchat_tone_prompt(db, *, ticket_id: int) -> str   # config bot_tone_prompt or the default
```

Runtime changes (all guarded by `is_webchat = source_channel == "webchat" or based_on_channel == "webchat"`):
- tone: `_resolve_telegram_tone_prompt` becomes `_resolve_channel_tone_prompt` (Telegram unchanged; webchat → `webchat_tone_prompt`) — manual drafts on chat tickets get the chat tone too.
- no disclosure footer prefix for webchat sends (the `bot_intro` line carries the notice).
- send path: `await session.commit()` (tool writes) → `deliver_bot_chat_reply(...)`; `BotChatTakenOver` → `create_draft(...)` + loop-guard bookkeeping → `AgentRunResult(status=STATUS_DRAFTED, notes="chat_taken_over")`.
- after a webchat send: skip the auto-state parking (`_resolve_auto_state_id`/`domain_change_state`) while the chat is live; `TiqoraAiArticleOrigin` is written as for every auto send.
- the Telegram identity exchange (`identity_mode == clarify_schema`) stays Telegram-only (Deviation 7).
- `_resolve_auto_state_id` gets a public alias `resolve_auto_state_id` (used by `close_bot_resolved_ticket`).

### C8. API changes

Visitor (`/api/chat/v1`, `webchat/schemas.py`):

```python
class ChatSessionCreateIn(...):  identity: str | None = Field(default=None, max_length=4096)
class ChatIdentifyIn(BaseModel): identity: str = Field(min_length=1, max_length=4096)
class ChatPortalIdentityIn(BaseModel):
    key: str = Field(max_length=40); origin: str = Field(max_length=200)
    visitor_id: str = Field(pattern=VISITOR_ID_PATTERN)
class ChatPortalIdentityOut(BaseModel): identity: str; name: str | None
class ChatPublicTopicOut(...):   ai_first: bool
class ChatPublicConfigOut(...):  bot_name: str; portal_identity: bool; offline_mode: Literal["form", "ai", "hide"]
class ChatMessageOut(...):       sender: Literal["visitor", "agent", "bot", "system"]
class ChatSessionOut(...):       bot_name: str | None; can_handoff: bool; identified_as: str | None
```

| Method + path | Body | Response | Limits / order |
|---|---|---|---|
| `POST /sessions` (changed) | `identity?` | as phase 1; 422 `{"detail":"invalid_identity"}` | identity failure also hits per-IP `identity_fail` 30/10 min |
| `POST /sessions/{sid}/identify` | `ChatIdentifyIn` | 204; 422 `invalid_identity`; 409 `identity_conflict`/`session_closed` | token → `message` limits → `identity_fail` on failure |
| `POST /sessions/{sid}/handoff` | — | 204; 409 `not_bot` (status ≠ bot) | token → `message` limits + `handoff` 3/min per session |
| `POST /identity/portal` | `ChatPortalIdentityIn` | 200 `ChatPortalIdentityOut`; 404 `not_found` (no portal session, portal off, config not on portal, origin ≠ own) | backstop → same origin → config → `identity` 20/10 min per IP; the ONLY endpoint that reads the customer cookie |

Session stream `session` event data gains `bot_name` (when status `bot`); typing event `{"who": "bot"}` while a turn runs.

Own-origin embedding (portal): `origins.effective_allowed_origins(values, own) -> list[str]` = `allowed_origins + [own]` when `values.show_on_portal`; used by the frame route, `GET /config`, `POST /sessions`, `POST /offline` (replaces the raw `allowed_origins` reads; `frame_csp` keeps listing only the validated `origin`, R-FRAME-ANCESTORS).

Portal (`/api/portal`, existing `require_portal_enabled` dependency): `GET /webchat` → `{"public_key": str}` of the valid config with `show_on_portal` and the channel on, else 404. Requires a portal session (`CurrentCustomer`).

Agent (`/api/v1/chat`, `/api/v1/tickets/{id}/chat`):
- `ChatInboxOut` gains `bot: list[ChatInboxItemOut]` (sessions in `bot` on queues with `rw`, newest first, ≤ 200); `ChatInboxItemOut.status` Literal adds `"bot"`.
- `POST /chat/sessions/{sid}/accept` also accepts a `bot` session (takeover): `handoff_at=now`, `handoff_reason="takeover"`, no `mark_ai_escalated` (the status change alone stops the bot); capacity check as phase 1; `_refusal_for` maps `bot` nowhere (accept allowed). **Assumption** (open question 9).
- `accept_waiting_chat_for_reply` takes over a `bot` chat too (an agent reply = takeover).
- `TicketChatOut` gains `handoff_reason: str | None`, `handoff_at: UtcDateTime | None`, `identity_source: str | None`, `bot_name: str | None`; `visitor_email_verified` computed (C4).
- No new agent-bus event types: bot sessions emit `chat_message` (inbox refetch, no ring); handoff emits `chat_waiting` (ring) or `chat_ended`.

Admin (`/api/v1/admin/chat-configs`):
- `ChatConfigOut` gains `identity_secret_set: bool`; values carry the new C2 fields.
- `POST /{id}/identity-secret` → 201 `{"secret": str}` (new or rotated; shown once; old secret invalid at once — **Assumption**, open question 4); `DELETE /{id}/identity-secret` → 204.
- `GET /{id}/ai-status` → `list[{"topic_key", "queue_id", "queue_name", "ok", "reason"}]` from `bot_availability` without the cache and without the worker check reported as an error (reason `worker_down` shown as a warning text).
- Save-time checks (R-ADMIN-422 shape): at most one valid config with `show_on_portal` (`values.show_on_portal`: "Es kann nur ein Chat im Kundenportal angezeigt werden."); `offline_mode == "ai"` requires at least one topic with `ai_mode == "first_line"` (`values.offline_mode`).

### C9. Widget contract (Tasks 12–14)

`src/shared/protocol.ts`:

```ts
export type FrameToHost = ... | Msg<"message", { sender: "agent" | "bot" | "system" }>
                              | Msg<"identity", { verified: boolean }>;
export type HostToFrame = ... | Msg<"identify", { token: string }>   // ^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$, ≤ 4096
                              | Msg<"logout">;
```

Host API: `window.Tiqora.chat.identify(token: string)` (queued until `ready`; the loader re-checks shape before posting), `window.Tiqora.chat.logout()`, new host event `"identity"` (detail `{verified}`). Mixed versions during the 5-minute `loader.js` cache window are harmless: each side drops unknown messages by its guard (identify is then simply ignored → anonymous chat).

Frame:
- `identity.ts`: in-memory only (`let pending: string | null`), never in storage; `identify` → if a live session exists `POST /identify` (409 `identity_conflict` → clear stored session, reset to start view; 422 → drop token, `identity` `{verified:false}`), else kept for `createSession`; `logout` → clear token + stored session (+ end nothing server-side) → start view.
- Portal: when `config.portal_identity` → `POST /identity/portal` with `credentials: "same-origin"` (the ONLY call with credentials; every other call keeps `credentials: "omit"`) → token used like `identify`; 404 → anonymous.
- Stored session record gains `identified: boolean`; on restore of an identified session without a fresh identify within the frame start, it is restored only when the host page calls `identify` (any token) before the visitor opens the chat — otherwise cleared. Pinned by test.
- Pre-chat: with a pending identity, fields mapped to `name`/`email` are hidden and "Angemeldet als {name}" is shown; a 422 `invalid_identity` on create shows the full form again with the message "Die Anmeldung konnte nicht bestätigt werden. Bitte geben Sie Ihre Angaben ein."
- Bot UI: bot lines show `{bot_name}` with a "KI" badge; header text "{bot_name} antwortet" while `bot`; "Mit einem Menschen sprechen" button (visible while `can_handoff`), confirm-free, disabled while the request runs; typing `{who:"bot"}` → "{bot_name} schreibt …".
- `isLive` covers `bot`; `canStartNew` unchanged except `offline_mode == "ai"` counts as startable when a topic is online.

### C10. Frontend (agent/admin/portal) contracts (Tasks 15–16)

```ts
// lib/webchatApi.ts additions
export type ChatAiStatus = { topic_key: string; queue_id: number; queue_name: string; ok: boolean; reason: string | null };
webchatApi.aiStatus(configId: number): Promise<ChatAiStatus[]>
webchatApi.createIdentitySecret(configId: number): Promise<{ secret: string }>
webchatApi.deleteIdentitySecret(configId: number): Promise<void>
export function chatAiStatusKey(configId: number) { return ["admin", "chat-configs", configId, "ai-status"] as const; }
// portal
export function usePortalWebchat(): UseQueryResult<{ public_key: string } | null>   // 404 → null
```

`handleSSEMessage` needs no new types (C8). `WebchatSessionHeader`: status chip for `bot` ("Mit KI im Gespräch"), handoff line ("Übergeben: <reason text>" for visitor/ai/limit/error/unavailable/takeover), identity line ("Identität bestätigt · Kundenportal" / "· Website-Anmeldung", else the phase-1 "E-Mail nicht bestätigt"), "Übernehmen" button while `bot`. `ChatInboxPanel`: third section "Mit KI im Gespräch" (count + "Übernehmen"); `ChatInboxButton` badge counts only `waiting` (no ring for bot chats). `MessengerComposer` (webchat adapter) shows "Ihre Antwort übernimmt den Chat von der KI." while the chat is `bot`.

### C11. i18n

- `webchatWidget.bot.*` (badge, typing, header, handoff button, handoff pending), `webchatWidget.system.bot_intro`, `webchatWidget.system.handoff.<reason>`, `webchatWidget.identity.*` (signed-in-as, invalid).
- `webchat.agent.bot.*`, `webchat.agent.handoff.<reason>`, `webchat.agent.identity.*`, `webchat.agent.inbox.botSection`, `webchat.agent.takeover*`.
- `webchat.admin.ai.*` (KI tab), `webchat.admin.identity.*` (Einbetten section incl. code-sample captions), `webchat.admin.portal.*`, `webchat.admin.offlineMode.ai`.

### Deviations from spec

1. **No token streaming.** The spec says the first line answers "(streamed)". `run_ticket_agent` is a tool loop with a separate final-answer model and output guards; streaming partial text would bypass the guards and the PII unmasking. Bot answers arrive whole; the widget shows "{bot_name} schreibt …" (typing `who: "bot"`) for the whole turn.
2. **`ai_mode = off | first_line` only.** `assist_only` (automatic per-message drafts) is not built; manual "KI-Entwurf" already works on every chat ticket and now uses the chat tone. **Assumption** (open question 3).
3. **Handoff summary = the AI's escalation note.** The spec wants "the AI's summary as an internal note". The `escalate_to_human` tool already writes an internal note with the AI's reason; the webchat tone prompt instructs the model that this reason must summarise the visitor's problem and what was tried. No extra summary LLM call (cost, latency). Handoffs not decided by the AI (visitor button, limit, error) write a short fixed note.
4. **Bot turns run in `tiqora-ai-worker`, not through the outbox tick.** `webchat` stays in `AUTO_REPLY_SKIP_CHANNELS`; a dedicated loop (Redis list + DB rescan, `TIQORA_CHAT_BOT_CONCURRENCY` default 4) gives second-level latency instead of the 10 s outbox tick, and keeps the AI kill switch (process stop, `ai.auto_reply.paused`) authoritative. When the worker is down the topic falls back to humans (`worker_down`).
5. **AI notice as a chat line**, not as the per-message disclosure footer (C3). **Assumption** (open question 7).
6. **Identity tokens are verified by Tiqora's own ~120-line HS256 codec** instead of a JWT library (authlib's `jose` is deprecated in authlib 1.x, and a fixed single-algorithm verifier removes algorithm-confusion risk). Website tokens: `exp` ≤ 24 h ahead; one secret per chat, no rotation grace. **Assumption** (open question 4).
7. **Telegram's identity exchange (`identity_mode = clarify_schema`) is not used for chats.** Chats are identified by portal session or signed token instead.
8. **Ticket state after a bot answer** is set at chat end (C6), not after each bot line (the runtime would otherwise park a live chat's ticket after every answer).
9. **Not in this plan** (spec phase 2, needs its own slot): transfer/invite, waiting-list position, block lists (IP/CIDR, country) + proof-of-work, rating, reporting source `chat_sessions`, transcript-as-one-article option, "KI-Zuordnung" (triage) for chat tickets (R-QUEUE-SOT skip stays). Columns this plan adds (`handoff_at`, `handoff_reason`, `bot_outcome`, `identity_source`) are what the later reporting source needs for bot-resolved/handoff rates.
10. **Portal identity via one cookie-reading endpoint** that returns a 10-minute assertion bound to chat + visitor id; the rest of `/api/chat/v1` stays cookie-less (R-CSRF unchanged). On customer websites the portal cookie is never available (third-party context) — there the signed token is the only way, as the spec says.
11. **`offline_mode = "ai"`** affects only topics with `ai_mode == "first_line"`; other topics of the same chat behave like `form`.
12. **Per-ticket AI caps apply to the bot** (`max_auto_replies`, `max_clarifications`, grants) and end in a handoff with reason `limit`. **Assumption** (open question 1).

---

## Tasks

Order: backend 1 → 11 sequential (each builds on the committed previous ones; 2 is pure and may run in parallel with 1). Widget 12 → 14 need Task 10's regenerated `openapi.json` (12 may start after Task 4). Frontend 15 → 16 need Tasks 10–11 (openapi). Task 17 closes.

Seed sub-bands: T1 54400–54419 · T3 54420–54459 · T4 54460–54499 · T5 54500–54539 · T7 54540–54579 · T6 54580–54589 · T8 54590–54629 · T9 54630–54659 · T10 54660–54709 · T11 54710–54739 · T17 54740–54759 · spare 54760–54799.

### Task 1: Migration, ORM, value models, `bot` as a live status (backend)

**Files:**
- Create: `backend/alembic/versions_tiqora/<next free number at implementation time>_webchat_ai_identity.py`
- Modify: `backend/src/tiqora/db/tiqora/models.py` (C1 columns on `TiqoraChatConfig`, `TiqoraChatSession`), `backend/src/tiqora/webchat/models.py` (C2), `backend/src/tiqora/webchat/store.py` (`_SCALAR_COLUMNS`, `LIVE_STATUSES`), `backend/src/tiqora/webchat/schemas.py` (`ChatMessageOut.sender`, `ChatInboxItemOut.status` Literals only), `backend/src/tiqora/webchat/serialize.py` (`message_out` for `bot`), `backend/tests/test_migration_gate.py`, `backend/tests/test_migrate_cli.py`, `backend/tests/_webchat_fixtures.py` (cleanup unchanged; add `seed_policy_service_user` helper stub only if Task 5 needs it — else leave)
- Test: `backend/tests/test_webchat_models.py` (extend), `backend/tests/test_webchat_tables.py` (extend, both dialects, band 54400–54419), `backend/tests/test_tiqora_model_ddl.py` (runs as is)

**Interfaces:**
- Consumes: phase-1 models and store.
- Produces: C1 columns; C2 fields; `store.LIVE_STATUSES = {"bot", "waiting", "active"}`; `DEFAULT_BOT_NAME`; `AiMode`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  # test_webchat_models.py
  def test_topic_without_ai_mode_defaults_off() -> None:
      t = ChatTopic.model_validate({"key": "support", "label": "Support", "queue_id": 3})
      assert t.ai_mode == "off"
  def test_topic_rejects_unknown_ai_mode() -> None:
      with pytest.raises(ValidationError):
          ChatTopic.model_validate({"key": "s", "label": "S", "queue_id": 3, "ai_mode": "assist_only"})
  def test_new_values_defaults() -> None:
      v = ChatConfigValues(topics=[ChatTopic(key="s", label="S", queue_id=3)])
      assert (v.offline_mode, v.bot_name, v.bot_tone_prompt, v.show_on_portal, v.identity_create_customers) == (
          "form", "KI-Assistent", None, False, False)
  def test_offline_mode_ai_accepted() -> None: ...  # ChatConfigValues(offline_mode="ai", ...) validates
  def test_message_out_bot_sender_carries_name() -> None:
      m = TiqoraChatMessage(seq=3, sender="bot", body="Hallo", meta='{"agent_name": "KI-Assistent"}', ...)
      out = message_out(m)
      assert out.sender == "bot" and out.agent_name == "KI-Assistent" and out.body == "Hallo" and out.code is None
  # test_webchat_tables.py (both dialects)
  async def test_new_session_columns_defaults(url_fixture, request) -> None:
      # insert a session row without the new columns → last_visitor_seq 0, bot_answered_seq 0, bot_attempts 0,
      # identity_source/handoff_reason/bot_outcome NULL; status "bot" round-trips
  async def test_config_values_read_new_scalar_columns(url_fixture, request) -> None:
      # bot_name NULL → "KI-Assistent"; show_on_portal True round-trips; a broken bot_name ("" ) → default + warning
  def test_live_statuses_include_bot() -> None:
      assert store.LIVE_STATUSES == frozenset({"bot", "waiting", "active"})
  ```
- [ ] **Step 2: Run** `TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_webchat_models.py tests/test_webchat_tables.py` → FAIL.
- [ ] **Step 3: Implement** C1/C2. Migration: `add_column` per column with `server_default` for the NOT NULL ones, the new index; downgrade drops them. Grep every `"waiting"`/`"active"` literal pair and every `LIVE_STATUSES` use in `backend/src/tiqora/webchat/` and `backend/src/tiqora/api/v1/chat.py`; for each, decide whether `bot` belongs (visitor writes: yes; `active_count` for agent capacity: no; `_abandon_idle`: Task 10; `accept_session`: Task 10) — change only what Task 1 owns (store, serialize, schemas Literals) and list the rest with the owning task in the commit body. Update `TIQORA_HEAD` and `test_migrate_cli.py`; `uv run alembic heads` shows one primary head.
- [ ] **Step 4: Run** the tests + `tests/test_tiqora_model_ddl.py tests/test_migration_gate.py tests/test_migrate_cli.py` + all `tests/test_webchat_*.py` → PASS; lint gates; commit `feat(webchat): bot status, identity and handoff columns`.

### Task 2: Identity token codec (backend, pure)

**Files:**
- Create: `backend/src/tiqora/webchat/identity.py`
- Test: `backend/tests/test_webchat_identity_token.py`

**Interfaces:**
- Consumes: `prechat.EMAIL_RE` (R-INPUT strict regex) and the control/bidi stripping of `webchat.text`.
- Produces: C4 pure part (`IdentityError`, `IdentityClaims`, `new_identity_secret`, `portal_identity_key`, `sign_identity_token`, `verify_identity_token`, constants).

- [ ] **Step 1: Write the failing tests** (helper `_tok(header, payload, key)` builds tokens by hand with `base64.urlsafe_b64encode(...).rstrip(b"=")`, so the tests do not trust `sign_identity_token`):
  ```python
  SECRET = "s3cr3t-" + "x" * 40; KEY = "wc_" + "a" * 32; NOW = 1_800_000_000
  def test_verify_accepts_minimal_website_token() -> None:
      t = _tok({"alg": "HS256", "typ": "JWT"}, {"sub": "erika", "exp": NOW + 600}, SECRET.encode())
      c = verify_identity_token(t, website_secret=SECRET, portal_key=PK, audience=KEY, visitor_id=None, now=NOW)
      assert (c.sub, c.email, c.source) == ("erika", None, "website")
  @pytest.mark.parametrize("header", [{"alg": "none"}, {"alg": "HS512"}, {"alg": "RS256"}, {"alg": "hs256"},
                                      {"alg": "HS256", "crit": ["exp"]}, {"alg": "HS256", "jwk": {}}])
  def test_verify_rejects_algorithms(header) -> None: ...      # code "algorithm"
  def test_verify_rejects_wrong_secret() -> None: ...          # "signature"
  def test_verify_rejects_website_token_with_portal_kid() -> None:
      # header kid "tiqora-portal" but signed with the website secret → "signature"
  def test_verify_rejects_missing_or_expired_exp() -> None: ... # no exp → "claims"; exp NOW-61 → "expired"; NOW-30 ok
  def test_verify_rejects_lifetime_over_24h() -> None: ...     # exp NOW + 86401 + 60 → "lifetime"
  def test_verify_rejects_nbf_in_future() -> None: ...
  def test_verify_audience() -> None: ...                      # aud other key → "audience"; aud == KEY ok; list aud → "audience"
  def test_verify_requires_sub_or_email() -> None: ...          # {"name": "x", "exp": …} → "claims"
  def test_verify_rejects_bad_email() -> None: ...              # email "a@b" / 'x"<y>@example.org' → "claims"
  def test_verify_rejects_malformed() -> None:
      for t in ["", "a.b", "a.b.c.d", "é.b.c", "a" * 4097, "!!.!!.!!", _tok_raw(b"[]", ...), padding "a=.b.c"]: "malformed"
  def test_name_and_sub_are_cleaned() -> None: ...              # "‮Erika\x00" → "Erika"; sub > 200 → "claims"
  def test_portal_assertion_roundtrip_and_binding() -> None:
      t = sign_identity_token({"src": "portal", "sub": "erika", "email": "erika@example.org", "name": "Erika Muster",
                               "aud": KEY, "vid": "v" * 22, "iat": NOW, "exp": NOW + 600}, PK, kid=PORTAL_KID)
      assert verify_identity_token(t, website_secret=None, portal_key=PK, audience=KEY, visitor_id="v" * 22,
                                   now=NOW).source == "portal"
      # other visitor id → "visitor"; missing aud → "audience"; src missing → "claims"
  def test_portal_key_is_derived_not_raw() -> None:
      assert portal_identity_key("k" * 40) != b"k" * 40 and len(portal_identity_key("k" * 40)) == 32
  def test_new_identity_secret_entropy() -> None:
      a, b = new_identity_secret(), new_identity_secret(); assert a != b and len(a) >= 43
  ```
- [ ] **Step 2: Run** → FAIL (module missing).
- [ ] **Step 3: Implement** C4 pure rules exactly (strict base64url decode: reject `=` and non-alphabet chars; `json.loads` with `object_pairs_hook` that rejects duplicate keys → `malformed`; integers only for `exp`/`nbf`/`iat` — `bool` is not an int here). No logging of token contents.
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(webchat): verify signed visitor identities`.

### Task 3: Identity resolution and session integration (backend)

**Files:**
- Create: `backend/src/tiqora/webchat/visitor_identity.py`
- Modify: `backend/src/tiqora/webchat/sessions.py` (`create_session` takes `identity: ResolvedIdentity | None`; `_open_ticket_and_session` uses it; `submit_offline` unchanged), `backend/src/tiqora/webchat/prechat.py` (`validate_prechat(..., identity_fields: bool = False)` → required name/email satisfied and mapped values ignored), `backend/src/tiqora/webchat/agent.py` (`ticket_chat_info`: `identity_source`, computed `visitor_email_verified`), `backend/src/tiqora/webchat/schemas.py` (`TicketChatOut.identity_source`)
- Test: `backend/tests/test_webchat_identity_service.py` (both dialects, band 54420–54459)

**Interfaces:**
- Consumes: Task 2; `create_customer_user`; `set_customer`; `decrypt_secret`.
- Produces:
  ```python
  async def resolve_identity(db, *, claims: IdentityClaims, values: ChatConfigValues) -> ResolvedIdentity
  def website_secret(config: TiqoraChatConfig, settings: Settings) -> str | None   # decrypt_secret; None when unset/undecryptable
  async def verify_and_resolve(db, settings, *, config, values, token: str, visitor_id: str,
                               now: datetime) -> ResolvedIdentity                  # IdentityError passes through
  async def identify_session(db, sysconfig, *, chat: TiqoraChatSession, identity: ResolvedIdentity,
                             now: datetime) -> bool      # True when something changed; ChatError identity_conflict/session_closed
  def issue_portal_assertion(customer: AuthenticatedCustomer, *, public_key: str, visitor_id: str,
                             settings: Settings, now: datetime) -> str
  ```

- [ ] **Step 1: Write the failing tests** (seed: customer users `wc54-erika` (email `erika@example.org`, customer_id `wc54-co`), `wc54-dup1`/`wc54-dup2` sharing `dup@example.org`; queue 54421 + agent 54422 online/available; config 54425 with identity secret):
  ```python
  async def test_sub_links_existing_customer(url_fixture, request) -> None:
      r = await resolve_identity(db, claims=IdentityClaims(sub="WC54-Erika", email=None, name=None, source="website",
                                                           visitor_id=None), values=values)
      assert (r.customer_user_login, r.customer_id, r.email, r.known_customer) == ("wc54-erika", "wc54-co",
                                                                                   "erika@example.org", True)
  async def test_email_links_unique_customer_only(...) -> None: ...      # dup@example.org → not known, login = email
  async def test_unknown_email_without_create_switch(...) -> None: ...   # login/customer_id = email, no customer_user row
  async def test_unknown_email_creates_customer_when_allowed(...) -> None:
      # values.identity_create_customers=True, email neu@example.org, name "Max Beispiel" → customer_user row exists
      # (login = email, first "Max", last "Beispiel", customer_id = email); cleanup removes it
  async def test_unknown_sub_without_email_free_text(...) -> None: ...
  async def test_create_session_with_identity_sets_verified_customer(url_fixture, request) -> None:
      chat, token, first = await create_session(..., payload=ChatSessionCreateIn(..., prechat={"name": "Falsch",
          "email": "falsch@example.org"}, message="Hallo"), identity=resolved_erika, ...)
      # ticket customer_user_id "wc54-erika", customer_id "wc54-co"; chat.visitor_email "erika@example.org";
      # chat.identity_source "website"; first article a_from contains erika@example.org, not falsch@
      info = await ticket_chat_info(...); assert info.visitor_email_verified is True and info.identity_source == "website"
  async def test_required_prechat_email_satisfied_by_identity(...) -> None: ...
  async def test_identify_anonymous_session_sets_customer(...) -> None:
      # anonymous chat → identify_session → set_customer called (history CustomerUpdate), identity_source set
  async def test_identify_same_identity_is_noop(...) -> None: ...        # returns False, no new history row
  async def test_identify_conflict_on_other_verified_identity(...) -> None: ...  # ChatError("identity_conflict", 409)
  async def test_identify_ended_session_refused(...) -> None: ...        # session_closed
  async def test_portal_assertion_bound_to_visitor_and_chat(...) -> None:
      t = issue_portal_assertion(erika_customer, public_key=cfg.public_key, visitor_id="v" * 22, settings=s, now=NOW)
      # verify_and_resolve with visitor "w"*22 → IdentityError("visitor"); other config key → "audience"; ok → source portal
  async def test_website_secret_missing_rejects_website_tokens(...) -> None: ...  # IdentityError("signature")
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** C4 linking + session effects. `create_session(..., identity=None)` keeps every phase-1 call site working. Customer lookups are single-table queries on `customer_user` (no join with `tiqora_*`). `identify_session` takes the session row lock first (R-LOCK-ORDER) and calls `set_customer` only when the ticket's customer still equals the pre-identify `customer_user_login`. Never log claims beyond `source` and `known_customer`.
- [ ] **Step 4: Run** → PASS (+ `tests/test_webchat_sessions.py` unchanged green); lint gates; commit `feat(webchat): link chats to identified customers`.

### Task 4: Public identity endpoints and portal embedding (backend)

**Files:**
- Create: `backend/src/tiqora/api/portal/webchat.py`
- Modify: `backend/src/tiqora/webchat/public_api.py` (`POST /sessions` identity; `POST /sessions/{sid}/identify`; `POST /identity/portal`), `backend/src/tiqora/webchat/schemas.py` (`ChatIdentifyIn`, `ChatPortalIdentityIn/Out`, `ChatSessionCreateIn.identity`, `ChatSessionOut.identified_as`, `ChatPublicConfigOut.portal_identity`), `backend/src/tiqora/webchat/origins.py` (`effective_allowed_origins`), `backend/src/tiqora/webchat/static.py` (frame uses it), `backend/src/tiqora/webchat/sessions.py` (`_check_origin` via it), `backend/src/tiqora/webchat/config_cache.py` (cache entry keeps `show_on_portal`), `backend/src/tiqora/api/portal/__init__.py` (include router), openapi files
- Test: `backend/tests/test_webchat_identity_api.py` (both dialects, band 54460–54499), `backend/tests/test_webchat_static.py` (extend)

**Interfaces:**
- Consumes: Task 3; `CustomerAuthService`, `CustomerSessionStore`, `portal_enabled`, `public_config_cache`, R-PREAUTH helpers in `public_api.py`.
- Produces: C8 visitor rows for identity; `GET /api/portal/webchat`; `effective_allowed_origins(values: ChatConfigValues, own: str) -> list[str]`.

- [ ] **Step 1: Write the failing tests** (FakeRedis; `Origin` header on every POST per R-ORIGIN):
  ```python
  async def test_create_with_valid_identity_201_and_identified_as(url_fixture, request) -> None:
      # website token for wc54-erika → 201; GET session → identified_as "Erika Muster"; response has no login/e-mail
  async def test_create_with_invalid_identity_422_nothing_written(...) -> None:
      # tampered token → 422 {"detail": "invalid_identity"}; ticket count unchanged; identity_fail bucket incremented
  async def test_identity_fail_bucket_limits(...) -> None: ...      # 31st failure within 10 min → 429 before verification
  async def test_identify_endpoint(...) -> None: ...                # anonymous → 204; other identity → 409 identity_conflict
  async def test_identify_requires_token_header(...) -> None: ...   # wrong X-Chat-Token → 404 not_found (R-TOKEN shape)
  async def test_portal_identity_requires_own_origin_and_portal_flag(...) -> None:
      # cookie of a logged-in portal customer; show_on_portal False → 404; origin https://www.example.org → 404;
      # portal disabled → 404; no cookie / pending 2FA cookie → 404; all ok → 200 with identity that
      # verify_and_resolve accepts for (key, visitor_id) and name "Erika Muster"
  async def test_other_chat_endpoints_ignore_portal_cookie(...) -> None:
      # POST /sessions with the portal cookie but no identity → anonymous chat (identity_source None)
  async def test_frame_on_own_origin_only_with_show_on_portal(...) -> None:
      # GET /chat/v1/frame?k=…&origin=<own> → 403 without the flag, 200 + CSP frame-ancestors 'self' <own> with it
  async def test_config_portal_identity_flag(...) -> None: ...      # GET /config origin=own + flag → portal_identity true; else false
  async def test_portal_webchat_endpoint(...) -> None: ...          # GET /api/portal/webchat → {"public_key"} / 404 without flag
  async def test_identify_full_post_real_session(url_fixture, request) -> None: ...  # R-TX: no InvalidRequestError
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Check order per R-PREAUTH; identity verification after config lookup and before any write; `identity_fail` failures counted per IP hash (`ChatRateLimiter.hit("identity_fail", ip_hash, limit=30, window_seconds=600)` checked first as a pre-check and incremented on failure). The portal endpoint builds `CustomerAuthService` from the request's DB session and `CustomerSessionStore(get_redis)`, reads the cookie named `settings.customer_session_cookie_name` only, never accepts pending (pre-2FA) tokens, and answers the same 404 for every refusal. Regenerate openapi.
- [ ] **Step 4: Run** → PASS (+ all `test_webchat_*`); lint gates; commit `feat(webchat): identify visitors by website sign-in or portal session`.

### Task 5: AI gate, bot availability and routing at chat start (backend)

**Files:**
- Modify: `backend/src/tiqora/ai/gate.py` (`TIQORA_ONLY_CHANNELS`, `queue_serves_tiqora_only_channel`), `backend/src/tiqora/webchat/bot.py` (create: `BotAvailability`, `bot_availability`, `BOT_ALIVE_KEY` only), `backend/src/tiqora/webchat/availability.py` (`config_availability` with offline_mode `ai`), `backend/src/tiqora/webchat/sessions.py` (`create_session` routing C5, `post_bot_intro` call deferred to Task 7 — here write the `bot_intro` line with `notice=None`), `backend/src/tiqora/webchat/public_api.py` (`ChatPublicTopicOut.ai_first`, `ChatPublicConfigOut.bot_name`), `backend/src/tiqora/webchat/schemas.py`, `backend/src/tiqora/ai/auto_run.py` (create; see Task 6 — Task 5 only needs `auto_reply_queue_cap_reason`, so add that function here and let Task 6 move the rest)
- Test: `backend/tests/test_webchat_bot_routing.py` (both dialects, band 54500–54539), `backend/tests/test_ai_gate.py` (extend), `backend/tests/test_ai_auto_worker.py` (extend: `test_outbox_tick_still_skips_webchat`)
- Create: `backend/tests/_webchat_bot_fixtures.py` (`seed_bot_queue(conn, *, queue_id, service_user_id, auto_reply=True, budget_tokens_day=None, autonomy="full")`)

**Interfaces:**
- Consumes: `get_queue_policy_by_queue`, `require_feature_allowed`, `build_task_llm`, `queue_tokens_used_today`, `_auto_replies_last_hour`, `_global_replies_per_hour_cap`.
- Produces: C5; `auto_reply_queue_cap_reason(session, *, queue_id, policy) -> str | None` (hourly, global, daily budget only).

- [ ] **Step 1: Write the failing tests:**
  ```python
  async def test_first_line_topic_starts_in_bot(url_fixture, request) -> None:
      # policy auto-reply on, service user 54502, BOT_ALIVE_KEY set, build_task_llm patched to a dummy TaskLlm
      chat, _, first = await create_session(...)
      assert chat.status == "bot" and chat.waiting_since is None and chat.last_visitor_seq == 1
      lines = await load_messages_since(db, chat.id, 0); assert [m.sender for m in lines] == ["visitor", "system"]
      assert lines[1].body == "bot_intro"
      assert [e["type"] for e in db.info["tiqora.webchat.agent_events"]] == ["chat_message"]   # no ring
  async def test_bot_unavailable_routes_to_humans(...) -> None:
      # parametrize reason: auto_reply off / no service user / worker_down (no alive key) / budget_tokens_day
      # exhausted (usage rows seeded) / paused kill switch → status "waiting", ring event chat_waiting
  async def test_offline_mode_ai_keeps_bot_without_humans(...) -> None:
      # no agent online + offline_mode "ai" → bot; offline_mode "form" → ChatError offline 409
  async def test_config_availability_reports_ai_only_topic_online(...) -> None: ...
  async def test_public_config_ai_first_and_bot_name(...) -> None: ...
  async def test_bot_availability_cached_ten_seconds(...) -> None: ...   # second call does not hit build_task_llm
  # test_ai_gate.py
  async def test_webchat_is_tiqora_only_channel_in_parallel_mode(...) -> None:
      # operation_mode parallel: require_feature_allowed(FEATURE_AUTO_REPLY, source_channel="webchat") passes;
      # with ai.auto_reply.paused → AiAutoReplyPausedError
  async def test_queue_of_first_line_topic_serves_tiqora_only_channel(...) -> None: ...
  # test_ai_auto_worker.py
  async def test_outbox_tick_still_skips_webchat(...) -> None:
      # ArticleCreate event with payload channel "webchat" → totals["skipped_channel"] == 1, runtime never called
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** C5. The bot alive check uses `redis_client.exists(BOT_ALIVE_KEY)`; any Redis error → `worker_down`. `bot_availability` never raises (log + `ok=False, reason=<exception class>`). Keep `AUTO_REPLY_SKIP_CHANNELS` checked before `TIQORA_ONLY_CHANNELS` in `run_auto_tick` and say so in the gate docstring.
- [ ] **Step 4: Run** → PASS (+ `test_webchat_sessions.py`, `test_webchat_availability.py`, `test_ai_auto_worker.py`, `test_ai_agent_policy_fields.py`); lint gates; commit `feat(webchat): start chats with the AI when its topic answers first`.

### Task 6: Shared auto-run preparation and cap check (backend, refactor)

**Files:**
- Modify: `backend/src/tiqora/ai/auto_run.py` (move `_cap_reason` here as `auto_reply_cap_reason`, keep `auto_reply_queue_cap_reason`; add `prepare_auto_run`), `backend/src/tiqora/ai/auto_worker.py` (import from `auto_run`; behaviour unchanged; keep the private names as aliases for existing tests)
- Create: `backend/tests/_ai_scripted_llm.py` (copy of `ScriptedLlm`, `_propose_response`, `_escalate_response`, `_no_reply_response` from `backend/tests/test_ai_runtime.py`, public names)
- Test: `backend/tests/test_ai_auto_run.py` (both dialects where DB is touched, band 54580–54589)

**Interfaces:**
- Consumes: `auto_worker` internals.
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class AutoRunInputs:
      task_llm: TaskLlm
      kb_bundle: str | None
      kb_search_fn: Any
      kb_get_article_fn: Any
  async def auto_reply_cap_reason(session, *, ticket_id: int, queue_id: int, policy, state) -> str | None
  async def prepare_auto_run(session, settings, policy) -> AutoRunInputs     # raises NoUsableModel / no_profile_error
  TICKET_CAP_REASONS: Final = frozenset({"max_clarifications", "max_auto_replies", "grant_used"})
  QUEUE_CAP_REASONS: Final = frozenset({"queue_rate_limit", "global_rate_limit", "budget_tokens_day"})
  ```

- [ ] **Step 1: Write the failing tests:** `test_cap_reason_order_unchanged` (escalated → paused → customer pause → grant → per-ticket caps → hourly → global → budget, one case each, same strings as before), `test_prepare_auto_run_builds_agent_chain` (patched `build_task_llm` / `kb_bundle`), `test_queue_cap_reason_ignores_ticket_caps`.
- [ ] **Step 2: Run** → FAIL (module functions missing).
- [ ] **Step 3: Implement** the move without behaviour change; `auto_worker._process_customer_article_event` calls `prepare_auto_run`.
- [ ] **Step 4: Run** → PASS + the whole `tests/test_ai_auto_worker.py`, `tests/test_ai_runtime.py`, `tests/test_ai_limits.py` unchanged green; lint gates; commit `refactor(ai): share the auto-reply cap check and run preparation`.

### Task 7: Runtime webchat dispatch and bot delivery (backend)

**Files:**
- Create: `backend/src/tiqora/webchat/bot_delivery.py`
- Modify: `backend/src/tiqora/ai/runtime.py` (C7), `backend/src/tiqora/webchat/hub.py` (`publish_chat_typing(client, session_id, *, who: Literal["agent", "bot"] = "agent")`), `backend/src/tiqora/webchat/stream.py` (forward `who`), `backend/src/tiqora/webchat/sessions.py` (`create_session` calls `post_bot_intro` with the policy's disclosure text)
- Test: `backend/tests/test_webchat_bot_delivery.py` (both dialects, band 54540–54579), `backend/tests/test_ai_runtime.py` (add one test only: `test_webchat_source_never_sends_email`)

**Interfaces:**
- Consumes: Tasks 1, 5, 6; `chat_body_from_article`, `visitor_from_address`, `insert_message`, `add_article`, `create_draft`.
- Produces: C7.

- [ ] **Step 1: Write the failing tests** (ScriptedLlm from `_ai_scripted_llm.py`; a `bot` session seeded via `create_session`):
  ```python
  async def test_bot_answer_becomes_bot_line_and_article(url_fixture, request) -> None:
      llm = ScriptedLlm([propose_response("Starten Sie den Router bitte neu.", kind="reply")])
      result = await run_ticket_agent(db, settings=s, llm=llm, ticket_id=tid, trigger=TRIGGER_AUTO,
                                      acting_user_id=None, run_id="r1", source_channel="webchat", ...)
      assert result.status == "sent"
      line = (await load_messages_since(db, chat.id, 2))[-1]
      assert (line.sender, line.body, json.loads(line.meta)["agent_name"]) == ("bot", "Starten Sie den Router bitte neu.",
                                                                               "KI-Assistent")
      # article: sender agent, user = service user, channel WebChat, visible, body without disclosure footer,
      # history SendAnswer; TiqoraAiArticleOrigin row exists; ticket state unchanged (no parking while live)
  async def test_webchat_tone_prompt_in_system_prompt(...) -> None:
      # llm.calls[0] system message contains DEFAULT_WEBCHAT_TONE_PROMPT; with config bot_tone_prompt → that text
  async def test_takeover_during_turn_stores_draft_not_message(...) -> None:
      # ScriptedLlm side effect: before returning, the session is set to "active" in another session (agent accept)
      # → result.status "drafted", notes "chat_taken_over"; no new bot line; one tiqora_ai_draft row
  async def test_bot_intro_uses_policy_disclosure_text(...) -> None: ...  # params notice == policy.ai_disclosure_text
  async def test_tool_writes_committed_before_session_lock(...) -> None:
      # update_ticket_fields tool + propose: concurrent post_visitor_message on the same chat completes (no deadlock,
      # both dialects; asyncio.gather with a 10 s timeout)
  async def test_bot_typing_published_with_who_bot(...) -> None: ...      # publish_chat_typing(..., who="bot") payload
  # test_ai_runtime.py
  async def test_webchat_source_never_sends_email(...) -> None:
      # based_on article channel webchat with a real-looking a_from → deliver_agent_email_reply never called
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** C7. The webchat branch sits before the Telegram branch in the `destination == "send"` block and before the e-mail fallback. Keep Telegram behaviour byte-identical (existing Telegram runtime tests must stay green).
- [ ] **Step 4: Run** → PASS + `tests/test_ai_runtime.py`, `tests/test_webchat_*`; lint gates; commit `feat(ai): answer web chat visitors as the chat assistant`.

### Task 8: Bot turn and handoff service (backend)

**Files:**
- Modify: `backend/src/tiqora/webchat/bot.py` (C6: `claim_bot_turn`, `run_chat_bot_turn`, `hand_off_chat`, `close_bot_resolved_ticket`), `backend/src/tiqora/webchat/notify.py` (`queue_bot_turn`, `BOT_QUEUE_KEY`), `backend/src/tiqora/webchat/sessions.py` (`_store_visitor_line` sets `last_visitor_seq` and queues a bot turn while `bot`), `backend/src/tiqora/ai/runtime.py` (public alias `resolve_auto_state_id`)
- Test: `backend/tests/test_webchat_bot_turn.py` (both dialects, band 54590–54629)

**Interfaces:**
- Consumes: Tasks 5–7; `mark_ai_escalated`, `eligible_agent_ids`, `within_business_hours`, `add_system_message`, `change_state`.
- Produces: C6.

- [ ] **Step 1: Write the failing tests:**
  ```python
  async def test_turn_replies_and_advances_seq(url_fixture, request) -> None:
      outcome = await run_chat_bot_turn(factory, s, r, session_id=chat.id, owner="t:1")
      assert outcome == "replied"; row → bot_answered_seq == 1, bot_attempts 0, bot_outcome "replied",
      bot_claimed_at None; usage row feature auto_reply, queue_id = the chat queue
  async def test_bot_usage_counts_against_queue_budget(...) -> None:
      # budget_tokens_day = tokens of one turn → second visitor line → hand_off_chat(reason "limit"), status waiting
  @pytest.mark.parametrize("reason", ["queue_rate_limit", "global_rate_limit", "budget_tokens_day",
                                      "max_auto_replies", "max_clarifications", "grant_used"])
  async def test_cap_reason_hands_off(reason, url_fixture, request) -> None: ...   # handoff_reason "limit"
  async def test_ai_escalation_hands_off_with_ai_note(...) -> None:
      # escalate_response("Kunde braucht eine Vertragsänderung") → status waiting, handoff_reason "ai",
      # internal note "Escalated to human: Kunde braucht …" exists (from the tool), no second note,
      # system lines [..., "handoff"], ring event chat_waiting with topic_ref, ai_escalated_at set
  async def test_handoff_without_humans_ends_no_agent(...) -> None:
      # no agent online → status ended, end_reason no_agent, lines "handoff","no_agent" {"has_email": False};
      # visitor can then POST /contact (phase-1 path) — call set_contact → ok
  async def test_visitor_burst_supersedes_then_one_answer(...) -> None:
      # ScriptedLlm stalls until two more visitor lines are stored → first run "superseded" (no bot line);
      # re-run → one bot line; bot_answered_seq == 3
  async def test_claim_is_exclusive_both_dialects(...) -> None:
      # asyncio.gather of two claim_bot_turn on the same session → exactly one True
  async def test_llm_error_three_times_hands_off(...) -> None:
      # LlmHttpError ×3 → outcomes "retry","retry","handoff"; handoff_reason "error"; note mentions "KI nicht erreichbar"
  async def test_draft_destination_hands_off(...) -> None:  # autonomy "off" → drafted → handoff "ai" + note
  async def test_policy_switched_off_mid_chat_hands_off_unavailable(...) -> None: ...
  async def test_no_reply_hands_off(...) -> None: ...
  async def test_turn_skips_when_not_bot(...) -> None: ...   # status active → claim False → "skipped", nothing written
  async def test_close_bot_resolved_ticket_on_visitor_end(...) -> None:
      # after one "replied" turn, end_by_visitor → ticket state = the policy's auto reply state (closed type allowed);
      # with a prior handoff → state unchanged
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** C6. `run_chat_bot_turn` opens its own DB sessions from `factory`; the runtime gets a fresh session (it commits itself); handoff runs in another fresh session after the runtime returns (never inside the runtime's transaction). `end_by_visitor` and `end_session_by_agent` call `close_bot_resolved_ticket` when the session leaves `bot`. Logging: `webchat_bot_turn` with `session_id`, `outcome`, `reason` — never message text.
- [ ] **Step 4: Run** → PASS + `tests/test_webchat_*`; lint gates; commit `feat(webchat): AI answers first and hands over to the team`.

### Task 9: Bot loop in the AI worker (backend)

**Files:**
- Create: `backend/src/tiqora/ai/chat_bot_loop.py`
- Modify: `backend/src/tiqora/ai/worker.py` (`_run_all` gathers `run_chat_bot_loop(stop)`), `backend/src/tiqora/config.py` (`chat_bot_concurrency: int = Field(4, ge=1, le=32, validation_alias="TIQORA_CHAT_BOT_CONCURRENCY")`), `docs/deployment.md` (env var, one line)
- Test: `backend/tests/test_ai_chat_bot_loop.py` (both dialects for the rescan, band 54630–54659)

**Interfaces:**
- Consumes: Task 8; `get_pubsub_redis(settings)`; `KEY_AI_WORKER_ENABLED`.
- Produces:
  ```python
  BOT_ALIVE_REFRESH_SECONDS: Final = 10
  BOT_RESCAN_SECONDS: Final = 15
  BOT_BLPOP_TIMEOUT: Final = 5
  async def run_chat_bot_loop(stop: asyncio.Event, *, settings: Settings | None = None,
                              factory: async_sessionmaker[AsyncSession] | None = None,
                              redis_client: Any = None) -> None
  async def rescan_pending_sessions(factory, *, now: datetime, limit: int = 100) -> list[str]
      # status 'bot' AND last_visitor_seq > bot_answered_seq AND (claim NULL OR claim < now - 180 s)
  ```

- [ ] **Step 1: Write the failing tests:** `test_loop_runs_enqueued_turn` (FakeRedis list with one sid → `run_chat_bot_turn` patched called once; stop event ends the loop within 1 s), `test_alive_marker_only_while_worker_enabled` (`daemon.ai_worker.enabled` off → no `BOT_ALIVE_KEY`, queue not consumed), `test_stale_claim_is_retaken_after_restart` (claim 200 s old → rescan returns it → turn runs), `test_concurrency_bounded` (10 sids, concurrency 2 → never more than 2 turns in flight), `test_redis_down_falls_back_to_rescan` (BLPOP raises → loop sleeps 1 s and rescans; no crash), `test_retry_is_delayed` ("retry" outcome → re-enqueued not before 5 s, using a fake clock).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Owner id `f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"`. Every turn in its own task under an `asyncio.Semaphore(settings.chat_bot_concurrency)`; exceptions logged, never stop the loop; on `stop` wait at most 5 s for running turns (they hold no transaction across awaits except the runtime's). The dedupe key `tiqora:chat:bot:queued:<sid>` is deleted when the turn starts.
- [ ] **Step 4: Run** → PASS + `tests/test_ai_worker.py`; lint gates; commit `feat(ai): run chat assistant turns in the AI worker`.

### Task 10: Visitor handoff, agent takeover, inbox, sweep, visitor outputs (backend)

**Files:**
- Modify: `backend/src/tiqora/webchat/public_api.py` (`POST /sessions/{sid}/handoff`; `session_out` with `bot_name`, `can_handoff`), `backend/src/tiqora/webchat/stream.py` (`session` event `bot_name`), `backend/src/tiqora/webchat/agent.py` (`accept_session` for `bot`, `accept_waiting_chat_for_reply` for `bot`, `list_inbox` `bot` section, `ticket_chat_info` handoff + bot fields, `end_session_by_agent` on `bot`), `backend/src/tiqora/webchat/sweep.py` (`_abandon_idle` includes `bot` with the active cutoff + `close_bot_resolved_ticket`), `backend/src/tiqora/webchat/schemas.py`, `backend/src/tiqora/api/v1/chat.py`, openapi files
- Test: `backend/tests/test_webchat_bot_api.py` (both dialects, band 54660–54709), extend `backend/tests/test_webchat_sweep.py`, `backend/tests/test_webchat_agent_api.py`

**Interfaces:**
- Consumes: Tasks 1–9.
- Produces: C8 visitor `handoff` row, agent changes, `ChatInboxOut.bot`.

- [ ] **Step 1: Write the failing tests:**
  ```python
  async def test_visitor_handoff_button(url_fixture, request) -> None:
      # POST /sessions/{sid}/handoff (Origin + token) → 204; status waiting, handoff_reason "visitor",
      # internal note "Der Besucher möchte mit einem Menschen sprechen."; a running claim does not block it
  async def test_handoff_not_bot_409(...) -> None: ...          # status waiting → 409 not_bot
  async def test_handoff_rate_limit(...) -> None: ...           # 4th within a minute → 429
  async def test_session_out_bot_fields(...) -> None:
      # GET session while bot → bot_name "KI-Assistent", can_handoff True; after handoff → can_handoff False
  async def test_agent_takeover_from_bot(...) -> None:
      # POST /api/v1/chat/sessions/{sid}/accept on a bot chat → 200; status active, handoff_reason "takeover",
      # lines end with agent_joined; a following run_chat_bot_turn → "skipped"
  async def test_agent_reply_takes_over_bot_chat(...) -> None: ...  # reply via ticket article API → active + delivered
  async def test_inbox_lists_bot_section_only_for_rw(...) -> None: ...
  async def test_ticket_chat_info_handoff_fields(...) -> None: ...
  async def test_bot_session_idle_abandoned_and_ticket_closed(...) -> None:  # sweep; bot_outcome replied → state set
  async def test_visitor_response_never_contains_handoff_note(...) -> None:
      # messages list + stream after an AI handoff contain no internal note text and no escalation reason
  async def test_handoff_full_post_real_session(...) -> None: ...  # R-TX
  ```
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** The handoff endpoint commits the pre-checks (R-TX) and calls `hand_off_chat(reason="visitor", note="Der Besucher möchte mit einem Menschen sprechen.")` in a fresh transaction, then `flush_chat_notifications`. `accept_session` for a `bot` session keeps the agent-state-first lock order; its `UPDATE … WHERE status IN ('waiting','bot')`. Regenerate openapi.
- [ ] **Step 4: Run** → PASS + all `tests/test_webchat_*`; lint gates; commit `feat(webchat): visitors and agents can take a chat from the AI`.

### Task 11: Admin API — AI settings, identity secret, portal switch (backend)

**Files:**
- Modify: `backend/src/tiqora/api/v1/admin/chat_configs.py` (C8 admin rows + save-time checks), `backend/src/tiqora/webchat/schemas.py` (`ChatConfigOut.identity_secret_set`, `ChatAiStatusOut`, `ChatIdentitySecretOut`), `backend/src/tiqora/webchat/config_cache.py` (clear on the new writes), openapi files
- Test: `backend/tests/test_webchat_admin_ai_identity.py` (both dialects, band 54710–54739)

**Interfaces:**
- Consumes: Tasks 2, 5; `encrypt_secret`, `clear_config_cache`, R-ADMIN-422 handler.
- Produces: `POST/DELETE /admin/chat-configs/{id}/identity-secret`, `GET /admin/chat-configs/{id}/ai-status`.

- [ ] **Step 1: Write the failing tests:** `test_create_identity_secret_shown_once` (201 secret; `GET` shows `identity_secret_set: true` and no secret anywhere in the payload; DB column is not the plain secret and `decrypt_secret` returns it), `test_rotate_invalidates_old_tokens` (token signed with the old secret → `invalid_identity` on `POST /sessions`), `test_delete_identity_secret`, `test_only_one_portal_chat` (second valid config with `show_on_portal` → 422 `{"detail":{"errors":{"show_on_portal": "Es kann nur ein Chat im Kundenportal angezeigt werden."}}}`), `test_offline_ai_requires_first_line_topic` (422 on `offline_mode`), `test_ai_status_reports_reasons` (one ok topic, one with `auto_reply_off`, `worker_down` when no alive key), `test_admin_only` (agent → 403), `test_config_cache_cleared_on_secret_change`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; the plain secret only lives in the 201 response body; log `webchat_identity_secret_rotated` with config id only. Regenerate openapi.
- [ ] **Step 4: Run** → PASS; lint gates; commit `feat(webchat): admin settings for the chat assistant and website sign-in`.

### Task 12: Widget protocol and loader — `identify`, `logout`, bot messages (widget)

**Files:**
- Modify: `packages/webchat-widget/src/shared/protocol.ts` (+ `protocol.test.ts`), `src/loader/hostApi.ts`, `src/loader/boot.ts`, `src/loader/bridge.ts`, `src/loader/loader.test.ts`
- Test: the files above

**Interfaces:**
- Consumes: C9.
- Produces: `HostApiImpl.identify(token: string): void`, `HostApiImpl.logout(): void`, `HostEvent` `"identity"`, guards for the new messages.

- [ ] **Step 1: Write the failing tests:** `isHostToFrame` accepts `{p, type:"identify", token:"a.b.c"}` and rejects tokens with `=`/spaces/4 097 chars/extra keys; accepts `{type:"logout"}`; `isFrameToHost` accepts `message` with sender `bot` and `identity {verified}`; loader: `Tiqora.chat.identify("a.b.c")` before `ready` is posted once right after `ready` with the explicit target origin; a non-string or bad token is dropped with a console warning; `logout()` posts `logout`; `on("identity")` receives `{verified: true}`; the frozen `window.Tiqora.chat` has exactly seven functions.
- [ ] **Step 2: Run** `pnpm --filter @tiqora/webchat-widget test` → FAIL.
- [ ] **Step 3: Implement** C9 protocol + host API; loader size stays ≤ 5 120 B gz.
- [ ] **Step 4: Run** lint + test + build (size check) → PASS; commit `feat(webchat-widget): host page sign-in and logout`.

### Task 13: Frame — chat assistant UI, handoff button, identity flow (widget)

**Files:**
- Create: `packages/webchat-widget/src/frame/identity.ts`, `src/frame/views/HandoffButton.tsx`
- Modify: `src/frame/bridge.ts` (accept `identify`/`logout` from the host, post `identity` + `message` with sender `bot`; + `bridge.test.ts`), `src/frame/api.ts` (`identify`, `handoff`, `portalIdentity` with `credentials: "same-origin"`, `createSession` identity), `src/frame/state.ts` (`bot` live, actions `identity-*`, `handoff-*`), `src/frame/App.tsx`, `src/frame/storage.ts` (`identified` flag, `clearSession`), `src/frame/views/ChatView.tsx`, `MessageList.tsx`, `Composer.tsx` (none or minimal), `PrechatForm.tsx`, widget locale keys in `frontend/src/i18n/locales/en.json` + `de.json` (`webchatWidget.*`, C11) + propagate
- Test: `src/frame/App.test.tsx`, `state.test.ts`, `api.test.ts`, `storage.test.ts`, `views/PrechatForm.test.tsx`, new `identity.test.ts`

**Interfaces:**
- Consumes: Tasks 10–12 (openapi types).
- Produces: C9 frame behaviour.

- [ ] **Step 1: Write the failing tests:**
  - `api.test.ts`: every call uses `credentials: "omit"` except `portalIdentity` (`"same-origin"`); `handoff` posts to `/sessions/<id>/handoff` with the token header.
  - `App.test.tsx`: bot session → header "KI-Assistent antwortet", bot lines show the name and a "KI" badge, `bot_intro` system text rendered from params; "Mit einem Menschen sprechen" posts `handoff` and disappears after the `session` event with status `waiting`; typing `{who:"bot"}` → "KI-Assistent schreibt …"; identify before start hides name/e-mail fields and shows "Angemeldet als Erika Muster" after the session starts (`identified_as`); 422 `invalid_identity` on create → full form + message; `logout` clears the stored session and shows a fresh start; `identity_conflict` on `/identify` clears the stored session; a stored identified session is NOT restored when the host never calls `identify` before the visitor opens the chat; portal config (`portal_identity: true`) fetches the portal identity once and uses it.
  - Host message `message` with sender `bot` is posted for bot lines (no text).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** C9 frame. Texts only via `webchatWidget.*`; text-node rendering only. Keep the frame ≤ 40 960 B gz (R-BUDGET) — if the build fails the size check, shrink (no new dependencies), never raise the budget; report if impossible.
- [ ] **Step 4: Run** widget lint/test/build + `node frontend/scripts/check-i18n-keys.mjs` → PASS; commit `feat(webchat-widget): chat assistant, handover button and recognised visitors`.

### Task 14: Widget end-to-end — assistant and sign-in on a second origin (widget)

**Files:**
- Modify: `packages/webchat-widget/scripts/mock-server.mjs` (bot session mode: scripted bot line after each visitor line, `/handoff`, `/identify`, `/identity/portal`, `identity_conflict` switch), `e2e/host.html` (buttons calling `Tiqora.chat.identify(<mock token>)` and `logout()`), `e2e/widget.spec.ts`
- Test: `e2e/widget.spec.ts`

**Interfaces:** Consumes Tasks 12–13.

- [ ] **Step 1: Write the failing e2e cases:** `bot answers first and hands over on request` (open, send, bot line with KI badge appears, press "Mit einem Menschen sprechen", system line, waiting state); `signed-in visitor skips name and e-mail` (host identify → pre-chat shows only the message field + "Angemeldet als …"); `logout on a shared computer starts fresh` (chat, logout, reopen → no old transcript); `reconnect during a bot turn keeps one answer` (drop the stream mid-typing, reconnect with Last-Event-ID → the bot line appears exactly once); keyboard-only path to the handoff button (Tab order, `aria-label`).
- [ ] **Step 2: Run** `pnpm --filter @tiqora/webchat-widget e2e` (the package's Playwright script) → FAIL.
- [ ] **Step 3: Implement** the mock-server additions (mock only; no production code changes unless a real bug shows up — then fix it in the frame with a unit test first).
- [ ] **Step 4: Run** → PASS; commit `test(webchat-widget): end-to-end for the chat assistant and website sign-in`.

### Task 15: Agent UI — assistant chats, handoff reason, recognised visitors, takeover (frontend)

**Files:**
- Modify: `frontend/src/lib/webchatApi.ts`, `frontend/src/components/agent/webchat/WebchatSessionHeader.tsx` (+test), `ChatInboxPanel.tsx` (+test), `ChatInboxButton.tsx` (+test), `frontend/src/components/agent/messenger/MessengerComposer.tsx` (hint), `frontend/src/demo/webchatMock.ts`, `frontend/src/i18n/locales/en.json` + `de.json` (`webchat.agent.*`) + propagate

**Interfaces:**
- Consumes: Task 10 openapi (`ChatInboxOut.bot`, `TicketChatOut.handoff_*`, `identity_source`, `bot_name`).
- Produces: C10 agent part.

- [ ] **Step 1: Write the failing tests:** header shows "Mit KI im Gespräch" + "Übernehmen" for status `bot` and calls `webchatApi.accept`; handoff line texts for each reason; "Identität bestätigt · Website-Anmeldung" when `identity_source === "website"` and `visitor_email_verified`; phase-1 "E-Mail nicht bestätigt" otherwise; inbox panel renders the third section with count and "Übernehmen", hidden when empty; badge counts only waiting chats (a bot chat does not ring); composer hint while `bot`.
- [ ] **Step 2: Run** `cd frontend && pnpm test --run src/components/agent/webchat` → FAIL.
- [ ] **Step 3: Implement**; demo mocks get one bot chat and one handed-over chat (example.org names); check in demo mode with Playwright (agent inbox + ticket header), delete `.playwright-mcp`.
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS; commit `feat(webchat): agents see and take over assistant chats`.

### Task 16: Admin editor "KI" tab, website sign-in key, portal chat (frontend)

**Files:**
- Create: `frontend/src/components/admin/webchat/AiTab.tsx` (+test), `IdentitySecretSection.tsx` (+test), `frontend/src/components/portal/PortalWebchat.tsx` (+test)
- Modify: `frontend/src/routes/admin/WebchatConfigEditorPage.tsx` (+test: new tab "KI"), `components/admin/webchat/HoursTab.tsx` (offline option "KI antwortet allein"), `EmbedTab.tsx` (identity section, "Im Kundenportal anzeigen", "Unbekannte Kunden anlegen"), `frontend/src/components/layout/PortalShell.tsx` (mount `PortalWebchat`), `frontend/src/lib/webchatApi.ts` (C10 admin + portal), `frontend/src/demo/webchatMock.ts`, locales + propagate

**Interfaces:**
- Consumes: Task 11 + Task 4 openapi.
- Produces: C10 admin + portal part.

- [ ] **Step 1: Write the failing tests:**
  - `AiTab`: per topic a select "KI antwortet zuerst: Aus / Ja"; bot name field (required, ≤ 80); optional tone text (≤ 2 000); status list from `aiStatus` with a readable German reason per code and a link "KI-Einstellungen der Queue öffnen" to the existing AI queue policy editor route; save shows R-ADMIN-422 field errors.
  - `HoursTab`: option "KI antwortet allein" disabled with a hint when no topic answers first.
  - `IdentitySecretSection`: "Schlüssel erzeugen" → shows the secret once in a copy field with the warning "Nur jetzt sichtbar"; "Neu erzeugen" asks for confirmation ("Bestehende Anmeldungen auf Ihrer Website funktionieren erst wieder, wenn Sie den neuen Schlüssel dort eintragen."); "Entfernen" asks for confirmation; code samples (Node, PHP, Python) for signing `{sub|email, name, exp}` with HS256 — copied from `docs/guide/webchat.md` (written in Task 17; until then use the same three samples inline), using `example.org`.
  - `PortalWebchat`: with `usePortalWebchat()` returning a key it appends exactly one `<script src="/chat/v1/loader.js" data-tiqora-chat=…>`; null → nothing; on portal logout it calls `window.Tiqora?.chat?.logout?.()` once; unmount removes nothing else from `window.Tiqora`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; check the editor and the portal in demo mode with Playwright (desktop + 390 px), delete `.playwright-mcp`.
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS; commit `feat(webchat): admin settings for the chat assistant, website sign-in and portal chat`.

### Task 17: Docs, golden check, full verification

**Files:**
- Modify: `docs/guide/webchat.md` (assistant, handoff, website sign-in with signing samples, portal chat, logout duty of the host page, what the visitor sees), `docs/webchat.md` + `docs/architecture.md` webchat section (bot loop, claims, identity paths), `docs/ai-integration.md` (chat assistant uses the queue policy, caps and budgets; `webchat` is a Tiqora-only channel), `docs/compatibility.md` (bot articles are normal `WebChat` articles by the AI service user; Znuny shows them), `docs/deployment.md` (`TIQORA_CHAT_BOT_CONCURRENCY`; the AI worker must run for the assistant; post-deploy check: a bot chat answers within 15 s), `docs/gdpr.md` (identity data = customer data already in Znuny; no token stored), `tests/golden/test_webchat_articles.py` (extend), this plan's "Self-review notes"
- Test: `tests/golden/test_webchat_articles.py` (band 54740–54759)

- [ ] **Step 1: Golden test (failing first):** a bot reply article (sender agent, AI service user, channel `WebChat`) and the handoff internal note are visible through Znuny's `TicketGet`/`ArticleList` on the peer and survive `Admin::CommunicationChannel::Sync` (R-CHANNEL-ROW d).
- [ ] **Step 2: Implement docs**; every example uses `example.org`/`.test`; German UI names exactly as in the code.
- [ ] **Step 3: Full verification:** backend full suite `TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` + the three lint gates; `uv run alembic heads` one primary head; openapi regenerated and `git diff --stat` sane; frontend `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; widget lint/test/build/e2e; Docker image build (`docker build .`) succeeds and the image contains the widget `dist`.
- [ ] **Step 4: Commit** `docs(webchat): chat assistant and recognised visitors`.

## Self-review notes (for the executor)

- Spec coverage: AI first line (C5–C7, Tasks 5–10), visible "Mit einem Menschen sprechen" (Tasks 10, 13), agent sees the bot conversation (articles + handoff note, Tasks 7–8, 15), never a dead end (`offline_mode = ai`, handoff → waiting or e-mail follow-up, Tasks 5, 8), budgets/caps/pause/PII/KB (reused runtime + `auto_reply_cap_reason`, Tasks 6–8), parallel-mode exemption (Task 5), portal customers (Task 4, 16), signed website identity (Tasks 2–4, 11–13), e-mail-only matching (phase 1, unchanged), "never creates a customer unless allowed" (Task 3 switch).
- The deferred phase-2 items are listed in Deviation 9 and open question 10 — do not start them here.
- Names that cross tasks: `bot_availability`, `hand_off_chat`, `run_chat_bot_turn`, `queue_bot_turn`, `deliver_bot_chat_reply`, `verify_and_resolve`, `identify_session`, `effective_allowed_origins`, `auto_reply_cap_reason`, `prepare_auto_run` — grep before renaming anything.
- Watch R-TX in every new endpoint, R-LOCK-ORDER in `hand_off_chat`/`deliver_bot_chat_reply`/`identify_session`, R-QUEUE-SOT (current queue) in bot availability and handoff ring targets.
