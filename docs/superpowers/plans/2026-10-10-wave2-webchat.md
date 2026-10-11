# Wave 2 — Web chat phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A website chat that lands in normal tickets: an embeddable widget (loader + launcher + iframe chat with pre-chat form, file upload and offline form), a public, rate-limited chat API with a reconnect-tolerant SSE stream, agent availability ("Chats annehmen") with first-accept-wins and an inbox in the agent shell, the Telegram composer generalised to a channel-neutral messenger composer, an admin editor per chat, and one ticket per chat session with one article per message.

**Architecture:** New backend package `tiqora.webchat` (config models, origin/CSP rules, tokens, rate limiter, per-process Redis fan-out `ChatHub`, session/agent services, visitor SSE stream, static widget routes, sweep loop). The public API lives under `/api/chat/v1` (visitor token in `X-Chat-Token`, no cookies, no CORS — every widget call is same-origin because the chat runs in an iframe on the Tiqora origin); agent endpoints under `/api/v1/chat` and `/api/v1/tickets/{id}/chat`; admin under `/api/v1/admin/chat-configs`. Messages are stored twice on purpose: as Znuny articles on a Tiqora-owned `WebChat` communication channel (MIME storage, rendered by Znuny's `Internal` backend) and as `tiqora_chat_message` rows with a gapless per-session `seq` that is the SSE event id. The widget is a separate Preact bundle in the new workspace package `packages/webchat-widget` (Vite: an IIFE `loader.js` < 5 KB gz and a `frame.html` app < 40 KB gz), built in the Docker frontend stage and served by `tiqora-api` at `/chat/v1/*`.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic two-chain, Pydantic v2, redis.asyncio pub/sub (`psubscribe`), SSE over `StreamingResponse`; Preact 10 + TypeScript + Vite 6 (library + app builds), fetch-streaming SSE client; React 19 + TanStack Query/Router + Tailwind tokens in the agent/admin app; Vitest (jsdom), Playwright (widget e2e against a Node mock server on a second origin).

**Spec:** `docs/superpowers/specs/2026-10-08-webchat-channel-design.md` — phase 1 row of "Phases" (tables, public API + SSE, widget: launcher, chat, pre-chat form, upload, offline form; agent availability + accept + inbox; messenger composer generalised; admin editor; ticket/article mapping) plus "Transport", "Widget", "Abuse protection" (rate limits, upload limits, origin/CSP — block lists and proof-of-work are phase 2). Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` (principles, definition of done). Builds on wave 0 (`docs/superpowers/plans/2026-10-08-wave0-foundations.md`: extension registry D1, typed field engine D5) — names used exactly as they exist in code on `roadmap-wave1`.

## Global Constraints

- Work in the worktree `<repo>` on the local branch `roadmap-wave1`; never touch `<repo>`; never commit to main; never push (a push to origin main deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Only made-up example data (example.org, .test). Use `rtk git`; check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Stage explicit paths only and run `rtk git diff --cached --name-only` before every commit (other agents share the index). Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never plain `uv run pytest`). Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their rows and never get added to `backend/tests/db_leak_baseline.txt`.
- Seed ids only inside this plan's band **48400–48799** (sub-bands per task below; confirm free with `grep -rn "48[4-7][0-9][0-9]" backend/tests` — today only ticket-number strings match). Seeded logins/names contain `wc48`. Autoincrement rows the services create (tickets, articles, history, chat messages, the `WebChat` channel row) are removed with `tests._row_cleanup.snapshot_marks` / `delete_above_marks`; chat sessions (string ids) by `config_id` band. Use the shared helper of Task 1 (`backend/tests/_webchat_fixtures.py`).
- DB tests that touch SQL run on BOTH dialects: `@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])` + `request.getfixturevalue(url_fixture)` and `_to_async_url` (pattern: `backend/tests/test_tickets_telegram_api.py`).
- Migrations: Alembic two-chain setup; new migrations in `backend/alembic/versions_tiqora`; `down_revision` = the CURRENT primary head at implementation time (`ls backend/alembic/versions_tiqora | tail -1` and `cd backend && uv run alembic heads` — other wave-2 plans add heads too; never trust an id written here); follow skill `alembic-two-chain-new-head-forks-graph`; update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version == …` assertion in `backend/tests/test_migrate_cli.py`. ORM models in `backend/src/tiqora/db/tiqora/models.py` produce the same DDL as the migration (`backend/tests/test_tiqora_model_ddl.py`). Tables only `tiqora_*`; never change Znuny DDL; writes to Znuny tables must be behaviourally identical to Znuny (history rows, change_time, TiqoraSync cache invalidation) — every Znuny write in this plan goes through the existing `tiqora.domain.ticket_write_service` functions.
- MySQL/MariaDB AND PostgreSQL must both work. Prod MariaDB mixes collations: never JOIN `tiqora_*` tables with Znuny tables on string columns — load keys, then IN-list. (Integer joins like `tiqora_chat_session.ticket_id = ticket.id` are allowed but this plan loads ticket rows by IN-list anyway.)
- Znuny SysConfig/YAML checkbox values are Perl-truthy strings ('0' is false): always read them with `SysConfig.get_bool` / `tiqora.util.perl.perl_truthy`, never `bool(...)`.
- Long-running endpoints (> a few seconds possible) use the wave-0 job system (prod nginx 90 s timeout). This plan has none: every chat endpoint is short; the SSE stream is long-lived by design and survives the 90 s `proxy_read_timeout` through 25 s heartbeats (`X-Accel-Buffering: no` disables nginx buffering per response — no nginx change needed).
- Timestamps the application writes into `tiqora_chat_*` columns are naive UTC from `tiqora.jobs.service.utc_now()`; never compare them with `func.now()` (skill `db-session-timezone-vs-app-written-rows`). API datetimes use `tiqora.domain.schemas.UtcDateTime`.
- After any API model/route change: regenerate both openapi files and schema.d.ts with `uv run tiqora openapi` per skill `tiqora-openapi-regen-or-ci-wipes-schema`; never hand-edit `schema.d.ts`. Commands: `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`; then `head -c 40 packages/api-client/openapi.json` (starts `{"components"`) and `rtk git diff --stat packages/api-client/openapi.json` (hundreds of lines, not five figures). A Pydantic response field WITH a default becomes required in the generated TS type — fixtures and demo mocks must include it.
- Frontend (`frontend/`): `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; never run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then propagate (skill `tiqora-i18n-key-propagation`: `node frontend/scripts/propagate-i18n-keys.mjs`; never `scaffold-locale.mjs` on existing locales). New screens get demo-mode mocks (VITE_DEMO=1; this plan puts them in the new `frontend/src/demo/webchatMock.ts`, wired with one line into `resolveData` in `mockData.ts`) and are checked in demo mode with Playwright (delete `.playwright-mcp` before committing). Tailwind colour tokens only via `frontend/src/themes/tokens.css` + `themeColor()`. API wrappers are hand-written `frontend/src/lib/*Api.ts` using `api.request<T>(method, path, {query, body, signal})` with `Schemas["…"]` types (pattern `frontend/src/lib/phoneApi.ts`).
- Widget (`packages/webchat-widget/`): `pnpm --filter @tiqora/webchat-widget lint && pnpm --filter @tiqora/webchat-widget test && pnpm --filter @tiqora/webchat-widget build` (the build runs the size check). No `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`dangerouslySetInnerHTML`/`eval`/`new Function`/`document.write` anywhere in the package (ESLint rule, Task 14). The widget's texts are `webchatWidget.*` keys in `frontend/src/i18n/locales/*.json` (same propagation/check scripts). New npm dependency: `preact` only (pnpm `minimumReleaseAge` applies — pick a version older than 3 days; `pnpm install` must pass without overriding the age).
- UI copy German first, plain words, named by what admins/agents/visitors recognise ("Web-Chat", "Chats annehmen", "Wartende Chats", "Annehmen", "Chat beenden", "Vorab-Formular", "Einbetten", "Zeiten & Offline", "Aussehen", "Schutz"); no system jargon in UI text ("SSE", "Token", "Origin", "Seq", "Hub" only in code/docs; the admin field for origins is "Erlaubte Websites").
- Security (internet-facing): every public endpoint rate-limited per IP hash and per session/visitor (Task 4/7); raw IP addresses are never stored or used as Redis keys (`hash_ip`); visitor responses never contain ticket id/number, queue, article id, user id, agent login/email or internal notes (Task 7 schemas are the only visitor-facing shapes); message bodies are plain text end-to-end and rendered as text nodes.

## Review Focus

1. **A visitor's network drops (mobile switch, laptop lid) mid-conversation and a POST is retried** → after reconnect every message appears exactly once in order, nothing typed is lost, nothing is duplicated. Pinned in Task 6 (`test_client_id_retry_returns_same_message`), Task 8 (`test_replay_after_last_event_id`, `test_wake_published_before_commit_is_still_delivered`) and Task 15 (`reconnects with Last-Event-ID after a dropped stream`).
2. **Two agents click "Annehmen" at the same instant, or an agent at the concurrent limit accepts** → exactly one wins (owner + lock like Znuny: history `Lock` then `OwnerUpdate`), the other sees "Schon angenommen" / "Sie haben bereits 3 Chats", no half state. Pinned in Task 9 (`test_first_accept_wins_both_dialects`, `test_accept_at_capacity_conflict`, `test_accept_writes_lock_then_owner_history`).
3. **Hostile input from the internet** — HTML/script in a message, prechat value or filename, `javascript:` links, bidi-override characters, an SVG/HTML renamed to `.png`, a 30 MB body, a foreign `Origin`, a frame request from a non-allowed site → rejected or neutralised; nothing executes in the widget or the agent UI. Pinned in Task 3 (`test_sanitize_message_*`, `test_sniff_rejects_svg_renamed_png`, `test_normalize_origin_rejects_*`), Task 7 (`test_cross_origin_post_refused`, `test_upload_svg_rejected`), Task 13 (`test_frame_csp_lists_only_allowed_origins`, `test_frame_refused_for_foreign_origin`), Task 15 (`renders script and javascript: links as text`).
4. **An agent replies after the visitor left, after the waiting timeout, or after the chat was ended** → a clear 409 ("Der Chat ist beendet – bitte per E-Mail antworten.") with nothing stored, and the normal reply dialog (email) is offered on that ticket. Pinned in Task 10 (`test_reply_to_ended_chat_is_409_and_stores_nothing`) and Task 19 (`shows the reply dialog instead of the messenger composer when the chat ended`).
5. **Data of other tickets/sessions reaching a visitor** — an internal note or an email reply on the chat ticket, messages of another session via a guessed session id or a token of another session → never in the stream, the message list or an attachment download. Pinned in Task 7 (`test_wrong_token_and_unknown_session_look_identical`, `test_attachment_of_other_session_404`) and Task 10 (`test_internal_note_not_sent_to_visitor`, `test_email_reply_on_chat_ticket_not_sent_to_visitor`).

Also pinned (not top five): behind a TLS-terminating proxy the same-origin check and per-IP limits still work (Task 7 `test_same_origin_check_uses_public_base_url`, `test_per_ip_limit_keys_on_client_not_proxy`); "Antworten" never mails a `@chat.invalid` address (Task 19); one bell/toast per chat, not per line (Task 2 `test_webchat_follow_up_does_not_toast`); Redis down → public endpoints answer 503 instead of crashing or skipping limits (Task 4/7); a calendar with no working hours means "always open" (Task 5); a corrupt JSON column in a config falls back to defaults instead of 500 (Task 2).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Existing names used exactly as they are in code: `ensure_channel_row`, `channel_enabled`, `CHANNEL_CONFIG_KEYS`, `CHANNELS`, `ArticleIn`, `TicketIn`, `create_ticket`, `add_article`, `lock_ticket`, `assign_owner`, `set_customer`, `TicketWriteService`, `_ticket_must_exist`, `PermissionEngine.check`, `LIST_CHANNELS`, `_CHAT_CHANNELS`, `TIQORA_EVENTS_CHANNEL`, `_publish`, `_should_forward`, `_QUEUE_SCOPED_EVENT_TYPES`, `_event_stream`, `HEARTBEAT_INTERVAL_SECONDS`, `client_ip`, `read_upload_within_limit`, `MAX_ATTACHMENT_BYTES`, `safe_attachment_response`, `calendar_config`, `working_seconds_between`, `html_to_plaintext` (`tiqora.domain.quoting`), `_backup_and_delete_by`, `_TICKET_DELETE_ORDER`, `TIQORA_ONLY_CHANNELS`, `DaemonService`, `DAEMON_SERVICES`, `_interval_loop`, `utc_now`, `register_core`, `get_redis`, `RedisDep`, `UtcDateTime`, `encrypt_secret`; frontend `api.request`, `Schemas`, `useComposerLock`, `useNextStateOptions`, `useChatAttachments`, `useChatDraftSync`, `useComposerRequests`, `handleSSEMessage`, `SSEMessage`, `dominantChannel`, `channelNameOf`, `CONVERSATIONAL_CHANNEL_NAMES`, `channelIcon`, `FieldsForm`, `FieldDef`, `ADMIN_PAGES` entries in `frontend/src/lib/adminSearch.ts`.

### C0. File map

Backend (new unless marked):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/webchat/__init__.py` | package docstring only |
| `backend/src/tiqora/webchat/models.py` | config value models (C2), constants |
| `backend/src/tiqora/webchat/origins.py` | `OriginError`, `normalize_origin`, `validate_origins`, `origin_allowed`, `own_origin`, `frame_csp` |
| `backend/src/tiqora/webchat/text.py` | `MessageRejected`, `sanitize_message`, `chat_body_from_article`, `MAX_MESSAGE_LENGTH`, `MAX_AGENT_MESSAGE_LENGTH` |
| `backend/src/tiqora/webchat/uploads.py` | `UploadRejected`, `sniff_upload_type`, `safe_upload_filename`, `check_upload` |
| `backend/src/tiqora/webchat/tokens.py` | `new_session_id`, `issue_visitor_token`, `parse_visitor_token`, `token_matches`, `hash_ip`, `VISITOR_ID_RE`, `CHAT_TOKEN_HEADER` |
| `backend/src/tiqora/webchat/ratelimit.py` | `RateLimited`, `ChatBackendUnavailable`, `ChatRateLimiter` |
| `backend/src/tiqora/webchat/hub.py` | `CHAT_CHANNEL_PREFIX`, `chat_channel`, `publish_chat_wake`, `publish_chat_typing`, `StreamLimitReached`, `ChatHub`, `get_chat_hub` |
| `backend/src/tiqora/webchat/store.py` | `load_config_by_key`, `load_config`, `config_values`, `get_chat_session`, `allocate_seq`, `insert_message`, `find_by_client_id`, `load_messages_since`, `live_session_for_ticket` |
| `backend/src/tiqora/webchat/notify.py` | `queue_wakeup`, `queue_agent_event`, `flush_chat_notifications` (post-commit publishing) |
| `backend/src/tiqora/webchat/availability.py` | agent state, online markers, business hours, eligibility, `config_availability` |
| `backend/src/tiqora/webchat/prechat.py` | `PrechatResult`, `validate_prechat`, `resolve_chat_customer` |
| `backend/src/tiqora/webchat/sessions.py` | `ChatError`, `VisitorContext`, `create_session`, `post_visitor_message`, `post_visitor_attachment`, `submit_offline`, `set_contact`, `end_by_visitor`, `add_system_message`, channel constants |
| `backend/src/tiqora/webchat/agent.py` | `accept_session`, `end_session_by_agent`, `list_inbox`, `deliver_agent_chat_reply`, `ChatNotLive`, `agent_typing` |
| `backend/src/tiqora/webchat/stream.py` | `visitor_event_stream`, SSE formatting |
| `backend/src/tiqora/webchat/sweep.py` | `run_webchat_sweep_tick` |
| `backend/src/tiqora/webchat/schemas.py` | all API models (C8) |
| `backend/src/tiqora/webchat/public_api.py` | router `/api/chat/v1` |
| `backend/src/tiqora/webchat/static.py` | `/chat/v1/loader.js`, `/chat/v1/frame`, `mount_webchat_assets` |
| `backend/src/tiqora/api/v1/chat.py` | agent router (`/chat/*`, `/tickets/{id}/chat*`) |
| `backend/src/tiqora/api/v1/admin/chat_configs.py` | admin router `/admin/chat-configs` |
| `backend/src/tiqora/worker/webchat.py` | `run_webchat_worker_tick` |
| `backend/tests/_fake_redis.py` | shared in-memory Redis (KV, TTL, incr, pub/sub incl. `psubscribe`) |
| `backend/tests/_webchat_fixtures.py` | seed/cleanup helpers for the 484xx–487xx band |
| `tests/golden/test_webchat_articles.py` | Znuny peer renders and deletes WebChat articles |
| modify shared: `db/tiqora/models.py`, `alembic/versions_tiqora/<new>`, `domain/ticket_write_service.py`, `domain/ticket_service.py`, `channels/registry.py`, `gdpr/erasure.py`, `ai/gate.py`, `ai/auto_worker.py`, `events/pubsub.py`, `api/v1/events.py`, `api/v1/tickets.py`, `api/v1/__init__.py`, `api/v1/admin/__init__.py`, `api/app.py`, `api/spa.py`, `config.py`, `domain/settings_store.py`, `worker/services.py`, `worker/__main__.py`, `worker/poller.py`, `Dockerfile`, `.github/workflows/ci.yml` | registrations / small hooks only (listed per task) |

Widget (new package `packages/webchat-widget/`):

| Path | Responsibility |
|---|---|
| `package.json`, `tsconfig.json`, `eslint.config.js`, `vitest.config.ts`, `playwright.config.ts` | package `@tiqora/webchat-widget` |
| `vite.loader.config.ts` | IIFE build of `src/loader/index.ts` → `dist/loader.js` (no hash) |
| `vite.frame.config.ts` | app build of `src/frame/frame.html` → `dist/frame.html` + `dist/assets/*` (hashed, `base: "/chat/v1/"`) |
| `scripts/widget-locales.ts` | Vite plugin `widgetLocales()` + pure `extractWidgetMessages` |
| `scripts/check-size.mjs` | gzip budgets (loader ≤ 5120 B, frame JS+CSS ≤ 40960 B) |
| `scripts/mock-server.mjs` | e2e mock of `/api/chat/v1` + static `dist` + host pages on a second port |
| `src/shared/protocol.ts` | postMessage protocol + guards |
| `src/loader/index.ts`, `launcher.ts`, `bridge.ts`, `hostApi.ts` | loader, launcher button, frame bridge, `window.Tiqora.chat` |
| `src/frame/frame.html`, `main.tsx`, `App.tsx`, `state.ts` | frame entry + view state machine |
| `src/frame/api.ts`, `sse.ts`, `storage.ts`, `i18n.ts`, `linkify.ts`, `theme.ts`, `styles.css` | client, stream, persistence, texts, safe linkify, theming |
| `src/frame/views/*.tsx` | `OfflineForm`, `PrechatForm`, `ChatView`, `MessageList`, `Composer`, `ContactPrompt`, `EndedView` |
| `e2e/widget.spec.ts`, `e2e/host.html` | Playwright on a different origin |

Frontend app (new unless marked):

| Path | Responsibility |
|---|---|
| `frontend/src/lib/webchatApi.ts` | wrappers, query keys, types |
| `frontend/src/lib/chatRing.ts` | ring/notification store for waiting chats |
| `frontend/src/demo/webchatMock.ts` | demo data + `resolveWebchatData` |
| `frontend/src/components/agent/messenger/MessengerComposer.tsx`, `adapters.ts`, `useTypingPing.ts` | generalised composer |
| `frontend/src/components/agent/webchat/WebchatSessionHeader.tsx`, `ChatInboxButton.tsx`, `ChatInboxPanel.tsx`, `ChatAvailabilityForm.tsx` | agent chat UI |
| `frontend/src/routes/admin/WebchatConfigsPage.tsx`, `WebchatConfigEditorPage.tsx`, `frontend/src/components/admin/webchat/*` | admin editor |
| modify `frontend/src/components/agent/telegram/TelegramChatComposer.tsx` (thin wrapper), `telegram/useTypingPing.ts` (re-export), `ArticleMasterDetail.tsx`, `TicketHeaderActions.tsx`, `ArticleQuickActions.tsx`, `ReplyDialog.tsx`, `ArticleConversationView.tsx`, `lib/articleChannel.ts`, `lib/useSSE.ts`, `components/layout/AgentShell.tsx`, `router.tsx`, `lib/adminSearch.ts`, `demo/mockData.ts`, `i18n/locales/*.json` | mounts and registrations |

### C1. Tables (one migration, Task 1)

All JSON is stored as `TEXT` (house style, see `tiqora_telegram_message.buttons_json`). Booleans `server_default=sa.false()`/`sa.true()`.

```
tiqora_chat_config
  id                     INT PK autoincrement
  name                   VARCHAR(100) NOT NULL          UNIQUE ux_tiqora_chat_config_name
  public_key             VARCHAR(40)  NOT NULL          UNIQUE ux_tiqora_chat_config_key   -- "wc_" + 32 urlsafe chars
  valid                  BOOL NOT NULL default true
  allowed_origins        TEXT NOT NULL                  -- JSON list[str], normalised origins
  topics                 TEXT NOT NULL                  -- JSON list[ChatTopic]
  greeting               VARCHAR(500) NULL
  privacy_url            VARCHAR(500) NULL
  offline_mode           VARCHAR(10) NOT NULL default 'form'   -- form|hide
  business_calendar      VARCHAR(10) NULL               -- NULL = always open; "" = default calendar; "1".."99"
  wait_timeout_seconds   INT NOT NULL default 180
  prechat_fields         TEXT NOT NULL                  -- JSON list[PrechatField]
  upload_policy          TEXT NOT NULL                  -- JSON UploadPolicy
  theme                  TEXT NOT NULL                  -- JSON ChatTheme
  rate_limits            TEXT NOT NULL                  -- JSON ChatRateLimits
  default_customer_user  VARCHAR(200) NULL
  show_agent_names       BOOL NOT NULL default true
  retention_days         INT NOT NULL default 30
  created                DATETIME NOT NULL
  created_by             INT NOT NULL
  changed                DATETIME NOT NULL
  changed_by             INT NOT NULL

tiqora_chat_session
  id                     VARCHAR(32) PK                 -- uuid4().hex
  config_id              INT NOT NULL
  topic_key              VARCHAR(40) NULL
  queue_id               INT NOT NULL                   -- the topic's queue at creation (event scoping)
  ticket_id              BIGINT NOT NULL
  status                 VARCHAR(12) NOT NULL           -- waiting|active|ended|abandoned ("bot" reserved for phase 2)
  end_reason             VARCHAR(20) NULL               -- visitor|agent|no_agent|idle|offline_form
  token_hash             VARCHAR(64) NOT NULL           -- sha256 hex of the token secret; "" after purge
  visitor_id             VARCHAR(64) NOT NULL
  visitor_name           VARCHAR(200) NULL
  visitor_email          VARCHAR(250) NULL
  customer_user_login    VARCHAR(200) NULL
  assigned_user_id       INT NULL
  last_seq               INT NOT NULL default 0
  waiting_since          DATETIME NULL
  accepted_at            DATETIME NULL
  ended_at               DATETIME NULL
  visitor_last_seen      DATETIME NOT NULL
  page_url               VARCHAR(500) NULL
  user_agent             VARCHAR(300) NULL
  ip_hash                VARCHAR(32) NULL
  language               VARCHAR(16) NULL
  created                DATETIME NOT NULL
  purged_at              DATETIME NULL
  INDEX ix_tiqora_chat_session_status (status, config_id)
  INDEX ix_tiqora_chat_session_ticket (ticket_id)
  INDEX ix_tiqora_chat_session_assigned (assigned_user_id, status)
  INDEX ix_tiqora_chat_session_visitor (visitor_id)

tiqora_chat_message
  id                     BIGINT PK autoincrement
  session_id             VARCHAR(32) NOT NULL
  ticket_id              BIGINT NOT NULL                -- denormalised for merge/erasure
  seq                    INT NOT NULL
  sender                 VARCHAR(10) NOT NULL           -- visitor|agent|system ("bot" reserved)
  user_id                INT NULL
  body                   TEXT NOT NULL                  -- plain text; for system rows the code (C5)
  meta                   TEXT NULL                      -- JSON: system params / {"agent_name": …}
  attachments            TEXT NULL                      -- JSON list[{index, filename, content_type, size}]
  article_id             BIGINT NULL
  client_id              VARCHAR(64) NULL
  created                DATETIME NOT NULL
  UNIQUE ux_tiqora_chat_message_seq (session_id, seq)
  UNIQUE ux_tiqora_chat_message_client (session_id, client_id)   -- NULLs never collide on both dialects
  INDEX  ix_tiqora_chat_message_ticket (ticket_id)
  INDEX  ix_tiqora_chat_message_article (article_id)

tiqora_chat_agent_state
  user_id                INT PK (no autoincrement)
  available              BOOL NOT NULL default false
  topics                 TEXT NOT NULL                  -- JSON list["<config_id>:<topic_key>"]; [] = all topics
  max_concurrent         SMALLINT NOT NULL default 3
  changed                DATETIME NOT NULL
```

ORM classes: `TiqoraChatConfig`, `TiqoraChatSession`, `TiqoraChatMessage`, `TiqoraChatAgentState` (same names/types/indexes as the migration).

### C2. Config value models (`tiqora/webchat/models.py`, Task 1)

```python
MIB: Final = 1024 * 1024
ALLOWED_UPLOAD_TYPES: Final[tuple[str, ...]] = (
    "image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain",
)
UploadType = Literal["image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain"]
TOPIC_KEY_PATTERN: Final = r"^[a-z][a-z0-9_-]{0,39}$"
FIELD_KEY_PATTERN: Final = r"^[a-z][a-z0-9_]{0,39}$"
RESERVED_FIELD_KEYS: Final = frozenset({"message", "topic"})
DF_KINDS_FOR_PRECHAT: Final = frozenset({"Text", "TextArea", "Dropdown", "Checkbox"})

class ChatTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=TOPIC_KEY_PATTERN)
    label: str = Field(min_length=1, max_length=80)
    queue_id: int = Field(ge=1)
    priority_id: int | None = Field(default=None, ge=1)

class PrechatOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100)

class PrechatField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=FIELD_KEY_PATTERN)
    kind: Literal["text", "textarea", "email", "select", "checkbox"]
    label: str = Field(min_length=1, max_length=100)
    required: bool = False
    options: list[PrechatOption] = Field(default_factory=list, max_length=50)
    maps_to: Literal["name", "email", "dynamic_field", "none"] = "none"
    dynamic_field: str | None = Field(default=None, max_length=200)
    # model_validator(mode="after"): key not in RESERVED_FIELD_KEYS; select ⇔ options non-empty (unique values);
    # maps_to "email" ⇒ kind "email"; maps_to "name" ⇒ kind "text"; maps_to "dynamic_field" ⇔ dynamic_field set.

class ChatTheme(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_color: str = Field("#2563eb", pattern=r"^#[0-9a-fA-F]{6}$")
    position: Literal["right", "left"] = "right"
    mode: Literal["auto", "light", "dark"] = "auto"
    launcher_label: str | None = Field(default=None, max_length=40)

class UploadPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_bytes: int = Field(10 * MIB, ge=MIB, le=MAX_ATTACHMENT_BYTES)
    types: list[UploadType] = Field(default_factory=lambda: list(ALLOWED_UPLOAD_TYPES), min_length=1)

class ChatRateLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sessions_per_ip_10min: int = Field(5, ge=1, le=100)
    messages_per_session_minute: int = Field(30, ge=5, le=300)
    messages_per_ip_minute: int = Field(60, ge=5, le=600)
    uploads_per_session_10min: int = Field(10, ge=1, le=100)
    offline_per_ip_hour: int = Field(3, ge=1, le=50)

def default_prechat_fields() -> list[PrechatField]:
    return [
        PrechatField(key="name", kind="text", label="Name", required=False, maps_to="name"),
        PrechatField(key="email", kind="email", label="E-Mail", required=False, maps_to="email"),
    ]

class ChatConfigValues(BaseModel):
    """Everything the admin edits except name / public_key / valid."""
    model_config = ConfigDict(extra="forbid")
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)   # field_validator → validate_origins
    topics: list[ChatTopic] = Field(min_length=1, max_length=20)             # unique keys
    greeting: str | None = Field(default=None, max_length=500)
    privacy_url: str | None = Field(default=None, max_length=500)            # must start with https:// (http:// for localhost)
    offline_mode: Literal["form", "hide"] = "form"
    business_calendar: str | None = Field(default=None, pattern=r"^(|[1-9][0-9]?)$")
    wait_timeout_seconds: int = Field(180, ge=30, le=3600)
    prechat_fields: list[PrechatField] = Field(default_factory=default_prechat_fields, max_length=15)  # unique keys; ≤1 maps_to name, ≤1 maps_to email
    upload_policy: UploadPolicy = Field(default_factory=UploadPolicy)
    theme: ChatTheme = Field(default_factory=ChatTheme)
    rate_limits: ChatRateLimits = Field(default_factory=ChatRateLimits)
    default_customer_user: str | None = Field(default=None, max_length=200)
    show_agent_names: bool = True
    retention_days: int = Field(30, ge=1, le=3650)
```

### C3. Pure rules (Task 3)

```python
# origins.py
class OriginError(ValueError): ...
def normalize_origin(raw: str) -> str
    # strip; urlsplit; scheme https, or http only for host localhost/127.0.0.1; no userinfo, path in ("", "/"),
    # no query/fragment, no "*"; host lowercased + IDNA ("xn--"); default port dropped; result matches
    # ^https?://[a-z0-9.-]+(:[0-9]{1,5})?$ — so nothing can break out of a CSP header. Raises OriginError(message_de).
def validate_origins(values: Sequence[str]) -> list[str]          # normalise, dedupe preserving order, ≤ 20
def origin_allowed(origin: str | None, allowed: Sequence[str]) -> bool   # normalise (error → False), exact match
def own_origin(request: Request, settings: Settings) -> str       # origin of settings.public_base_url, else request scheme+netloc
FRAME_CSP_BASE: Final = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
    "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'"
)
def frame_csp(allowed: Sequence[str], *, preview_only: bool = False) -> str
    # f"{FRAME_CSP_BASE}; frame-ancestors 'self' {' '.join(allowed)}" (only "'self'" when preview_only or allowed empty)

# text.py
MAX_MESSAGE_LENGTH: Final = 2000
MAX_AGENT_MESSAGE_LENGTH: Final = 4000
class MessageRejected(ValueError):
    def __init__(self, code: Literal["empty", "too_long"]) -> None
def sanitize_message(raw: str, *, max_length: int = MAX_MESSAGE_LENGTH) -> str
    # CRLF/CR → LF; drop C0 controls except \n \t, DEL, bidi controls U+202A–U+202E and U+2066–U+2069;
    # strip; "" → MessageRejected("empty"); len > max_length → MessageRejected("too_long")
def chat_body_from_article(body: str, content_type: str) -> str
    # "html" in content_type → html_to_plaintext(body); then sanitize_message(max_length=MAX_AGENT_MESSAGE_LENGTH)

# uploads.py
class UploadRejected(ValueError):
    def __init__(self, code: Literal["disabled", "empty", "size", "type"]) -> None
def sniff_upload_type(content: bytes, filename: str) -> str | None
    # PNG \x89PNG\r\n\x1a\n; JPEG \xff\xd8\xff; GIF GIF87a/GIF89a; WEBP RIFF????WEBP; PDF %PDF-;
    # text/plain only for extensions .txt/.log/.csv, no NUL byte, strict UTF-8 decode of the whole content. Else None.
def safe_upload_filename(name: str, content_type: str) -> str
    # basename after "/" and "\\"; drop controls + bidi; collapse whitespace; ≤ 120 chars keeping the extension;
    # extension forced to the canonical one of content_type (.png .jpg .gif .webp .pdf .txt); "" → "datei" + ext
def check_upload(content: bytes, filename: str, policy: UploadPolicy) -> tuple[str, str]   # (safe_name, content_type)
    # disabled → UploadRejected("disabled"); empty → "empty"; len > policy.max_bytes → "size";
    # sniffed type None or not in policy.types → "type"

# tokens.py
CHAT_TOKEN_HEADER: Final = "X-Chat-Token"
VISITOR_ID_RE: Final = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
def new_session_id() -> str                                   # uuid4().hex
def issue_visitor_token(session_id: str) -> tuple[str, str]   # ("<sid>.<secrets.token_urlsafe(32)>", sha256(secret) hex)
def parse_visitor_token(token: str | None) -> tuple[str, str] | None   # sid 32 lowercase hex, secret 20..100 urlsafe
def token_matches(secret: str, token_hash: str) -> bool       # hmac.compare_digest; False for ""
def hash_ip(ip: str, secret_key: str) -> str                  # hmac-sha256(secret_key, "webchat-ip:" + ip).hexdigest()[:32]
```

### C4. Rate limiter, hub, post-commit notifications (Tasks 4, 6)

```python
# ratelimit.py — fixed window in Redis, FAIL CLOSED (chat needs Redis for fan-out anyway)
class RateLimited(Exception):
    def __init__(self, scope: str, retry_after: int) -> None
class ChatBackendUnavailable(Exception): ...
class ChatRateLimiter:
    def __init__(self, client: Any) -> None
    async def hit(self, scope: str, subject: str, *, limit: int, window_seconds: int) -> None
        # key f"tiqora:chat:rl:{scope}:{subject}"; INCR; first hit → EXPIRE; count > limit → RateLimited(scope, max(1, ttl));
        # any Redis error → ChatBackendUnavailable
    async def try_hit(self, scope: str, subject: str, *, window_seconds: int) -> bool
        # SET key 1 NX EX window → True if set (used for typing: at most one per window; Redis error → False)

# hub.py — one psubscribe per API process
CHAT_CHANNEL_PREFIX: Final = "tiqora:chat:"
def chat_channel(session_id: str) -> str                               # f"tiqora:chat:{session_id}"
async def publish_chat_wake(client: Any, session_id: str) -> None      # {"type": "wake"}; best-effort, never raises
async def publish_chat_typing(client: Any, session_id: str) -> None    # {"type": "typing", "who": "agent"}
class StreamLimitReached(Exception): ...
class ChatHub:
    def __init__(self, client: Any, *, max_streams: int, max_per_session: int = 3) -> None
    async def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]
        # StreamLimitReached when total ≥ max_streams or this session ≥ max_per_session; starts the reader task on first use:
        # pubsub.psubscribe(f"{CHAT_CHANNEL_PREFIX}*"), loop get_message(ignore_subscribe_messages=True, timeout=1.0),
        # route "pmessage" by channel suffix to every queue of that session (Queue(maxsize=50); full → drop the oldest item);
        # reader errors are logged and the reader restarts after 1 s (streams also re-read the DB on every heartbeat).
    def unsubscribe(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None
    @property
    def active_streams(self) -> int
    async def aclose(self) -> None
def get_chat_hub(request: Request) -> ChatHub     # app.state.chat_hub, created lazily from get_redis + settings.chat_max_streams

# notify.py — publish only after commit (a wake before commit would make a stream re-read and find nothing)
def queue_wakeup(db: AsyncSession, session_id: str) -> None             # db.info["tiqora.webchat.wakeups"] set
def queue_agent_event(db: AsyncSession, payload: dict[str, Any]) -> None  # db.info["tiqora.webchat.agent_events"] list
async def flush_chat_notifications(db: AsyncSession, client: Any) -> None
    # pops both; publish_chat_wake per session id; events via tiqora.events.pubsub.publish_chat_event; never raises
```

`tiqora/events/pubsub.py` gains (and documents in its module docstring):

```python
CHAT_EVENT_TYPES: Final = frozenset({"chat_waiting", "chat_assigned", "chat_message", "chat_typing", "chat_ended"})
async def publish_chat_event(client: redis.Redis, event_type: str, *, session_id: str, ticket_id: int,
                             queue_id: int, user_id: int | None = None, topic_ref: str | None = None) -> None
    # {"type": event_type, "session_id", "ticket_id", "queue_id", ["user_id"], ["topic_ref"]} — no visitor names,
    # no message text on the agent bus (clients refetch); best-effort like _publish
```

`tiqora/api/v1/events.py`: `_QUEUE_SCOPED_EVENT_TYPES |= CHAT_EVENT_TYPES`; `_event_stream(..., on_alive: Callable[[], Awaitable[None]] | None = None)` calls `on_alive()` at connect and whenever ≥ 20 s passed since the last call; `stream_events` passes `lambda: mark_agent_online(redis_client, user.id)`.

### C5. Sessions, statuses, system messages (Tasks 5, 6, 9, 10, 11)

Status machine: `waiting` (ticket exists, nobody accepted) → `active` (accepted) → `ended` (visitor/agent/no_agent) or `abandoned` (idle). `ended`/`abandoned` are final. Offline form: a row with `status="ended"`, `end_reason="offline_form"`, `token_hash` = sha256 of a random never-returned secret.

System message codes (stored in `tiqora_chat_message.body` with `sender="system"`, params in `meta`): `agent_joined` `{"name": str | None}`, `chat_ended` `{}`, `visitor_left` `{}`, `no_agent` `{"has_email": bool}`, `idle_closed` `{}`. Visitor UI translates `webchatWidget.system.<code>`; system rows are not articles.

```python
# sessions.py
CHANNEL: Final = "webchat"
COMM_CHANNEL_NAME: Final = "WebChat"
COMM_CHANNEL_MODULE: Final = "Kernel::System::CommunicationChannel::Internal"
SYSTEM_USER_ID: Final = 1
LIVE_STATUSES: Final = frozenset({"waiting", "active"})
class ChatError(Exception):
    def __init__(self, code: str, http_status: int, *, errors: dict[str, str] | None = None) -> None
    # codes: disabled 404, not_found 404, origin 403, offline 409, session_closed 409, invalid_topic 422,
    # invalid_prechat 422 (errors per field), message_rejected 422, upload_rejected 422 (413 for "size"), forbidden 403,
    # already_taken 409, at_capacity 409, not_waiting 409, chat_not_live 409, contact_not_allowed 409
@dataclass(frozen=True, slots=True)
class VisitorContext:
    ip_hash: str
    user_agent: str | None
    page_url: str | None
    language: str | None
async def create_session(db, factory, sysconfig, redis_client, *, config: TiqoraChatConfig,
                         values: ChatConfigValues, payload: ChatSessionCreateIn, ctx: VisitorContext,
                         now: datetime) -> tuple[TiqoraChatSession, str, TiqoraChatMessage]
async def post_visitor_message(db, sysconfig, *, chat: TiqoraChatSession, body: str, client_id: str | None,
                               now: datetime) -> TiqoraChatMessage
async def post_visitor_attachment(db, sysconfig, *, chat, values: ChatConfigValues, filename: str,
                                  content: bytes, caption: str | None, client_id: str | None,
                                  now: datetime) -> TiqoraChatMessage
async def submit_offline(db, factory, sysconfig, *, config, values, payload: ChatOfflineIn,
                         ctx: VisitorContext, now: datetime) -> None
async def set_contact(db, sysconfig, *, chat, name: str | None, email: str, now: datetime) -> None
async def end_by_visitor(db, *, chat, now: datetime) -> None
async def add_system_message(db, *, chat, code: str, params: dict[str, Any]) -> TiqoraChatMessage
def visitor_from_address(name: str | None, email: str | None, visitor_id: str) -> str
    # email → '"Name" <email>' (or bare email); else '"Name or Besucher" <visitor-<first 12 of visitor_id>@chat.invalid>'
```

Ticket mapping (Task 6): `create_ticket(TicketIn(title="Chat: " + first line of the message[:60], queue_id=topic.queue_id, state_id=<PostmasterDefaultState>, priority_id=topic.priority_id or <PostmasterDefaultPriority>, owner_id=1, customer_id, customer_user_id, dynamic_fields=prechat.df_values), user_id=1)`, then `add_article(ArticleIn(sender_type="customer", is_visible_for_customer=True, subject=<title>, body=<message + prechat block>, channel="webchat", from_address=visitor_from_address(...)))`. History types follow the existing `_CHAT_CHANNELS` rule (first customer article `WebRequestCustomer`, later `FollowUp`, agent `SendAnswer`). Follow-up visitor messages set `no_agent_notify=True` (no mail per chat line; the agent bus + ring covers them). Prechat block appended to the first article body:

```
<message>

— Angaben aus dem Vorab-Formular —
<Label>: <value>
```

Customer resolution (`prechat.resolve_chat_customer(db, *, email, default_customer_user) -> tuple[str | None, str | None]`): email → `customer_user` by `LOWER(login)`/`LOWER(email)` = `LOWER(:email)` → `(customer_id, login)`, else `(email, email)` (same rule as `channels/email/pipeline._resolve_customer`); no email → `default_customer_user` (its `customer_id` looked up) → else `(None, None)`.

### C6. Agent side (Tasks 5, 9, 10)

```python
# availability.py
AGENT_ONLINE_PREFIX: Final = "tiqora:agent_online:"
AGENT_ONLINE_TTL_SECONDS: Final = 60
AVAILABILITY_CACHE_SECONDS: Final = 10
VISITOR_SEEN_PREFIX: Final = "tiqora:chat:seen:"        # TTL 90 s, refreshed by the visitor stream
VISITOR_TYPING_PREFIX: Final = "tiqora:chat:vtyping:"   # TTL 6 s
@dataclass(frozen=True, slots=True)
class AgentChatState:
    user_id: int
    available: bool
    topics: tuple[str, ...]
    max_concurrent: int
def topic_ref(config_id: int, topic_key: str) -> str      # f"{config_id}:{topic_key}"
async def mark_agent_online(client: Any, user_id: int) -> None
async def get_agent_state(db: AsyncSession, user_id: int) -> AgentChatState     # defaults (False, (), 3) without row
async def set_agent_state(db: AsyncSession, user_id: int, *, available: bool, topics: Sequence[str],
                          max_concurrent: int, now: datetime) -> AgentChatState  # upsert; max_concurrent 1..20
async def active_count(db: AsyncSession, user_id: int) -> int
async def within_business_hours(sysconfig: SysConfig, calendar: str | None, now: datetime) -> bool
    # None → True; calendar_config(sysconfig, calendar or None); no working hours at all → True;
    # else working_seconds_between(epoch, epoch + 60, hours, vacation, vacation_once, tz) > 0
async def eligible_agent_ids(db, client, *, config_id: int, topic: ChatTopic) -> list[int]
    # available rows → topics [] or topic_ref in topics → online marker exists → PermissionEngine.check(uid, queue, "rw")
    # → active_count < max_concurrent
async def config_availability(db, client, sysconfig, *, config: TiqoraChatConfig, values: ChatConfigValues,
                              now: datetime) -> dict[str, bool]
    # {topic.key: online}; all False outside business hours; cached as JSON in Redis f"tiqora:chat:avail:{config.id}"
    # for AVAILABILITY_CACHE_SECONDS (Redis error → computed uncached)

# agent.py
class ChatNotLive(Exception): ...
async def accept_session(db, sysconfig, *, session_id: str, user_id: int, now: datetime) -> TiqoraChatSession
async def end_session_by_agent(db, sysconfig, *, session_id: str, user_id: int, close_ticket: bool,
                               now: datetime) -> None
async def list_inbox(db, *, user_id: int) -> ChatInboxOut
async def deliver_agent_chat_reply(db, sysconfig, *, ticket_id: int, user_id: int, article: ArticleIn) -> int
async def agent_typing(db, client, *, ticket_id: int, user_id: int) -> None
```

Accept (Task 9), in one transaction: `rw` on `chat.queue_id` else `forbidden`; ensure + `SELECT … FOR UPDATE` the agent state row; `active_count ≥ max_concurrent` → `at_capacity`; `UPDATE tiqora_chat_session SET status='active', assigned_user_id=:u, accepted_at=:now WHERE id=:sid AND status='waiting'` — rowcount 0 → `already_taken` (status active) / `not_waiting` (else); ticket: unlocked → `lock_ticket` then (owner ≠ user) `assign_owner` (Znuny order Lock, OwnerUpdate); locked by someone else → `assign_owner` only; system message `agent_joined` (`name` = agent first name when `show_agent_names`, else None); wake; agent event `chat_assigned` with `user_id`.

### C7. Visitor stream (Task 8)

`GET /api/chat/v1/sessions/{session_id}/stream` with header `X-Chat-Token`, optional `Last-Event-ID` header (or `?after=` for the first connect). Response `text/event-stream`, headers `Cache-Control: no-store`, `X-Accel-Buffering: no`. Events:

```
id: <seq>\nevent: message\ndata: <ChatMessageOut JSON>\n\n      (persisted rows only carry ids)
event: session\ndata: {"status": "...", "agent_name": ...}\n\n    (on connect and when status/agent changes)
event: typing\ndata: {"who": "agent"}\n\n
event: end\ndata: {"reason": "..."}\n\n                          (then the stream closes; the client stops reconnecting)
: hb\n\n                                                          (every STREAM_HEARTBEAT_SECONDS = 25 when idle)
```

Loop: subscribe to hub FIRST, then read rows `seq > cursor` from the DB with a short-lived session (never hold a DB connection while waiting), emit, then wait on the hub queue with the heartbeat timeout; any `wake` or timeout re-reads; `typing` items are forwarded without re-reading. On connect and every heartbeat: `SET tiqora:chat:seen:<sid> 1 EX 90`. Limits: hub `StreamLimitReached` → 503 `Retry-After: 30`; rate `stream_connect` 30/min per session.

### C8. API models (`tiqora/webchat/schemas.py`) and endpoints

Visitor-facing (never contain ticket id/tn, queue, article id, user id, login, email of agents):

```python
class ChatPublicTopicOut(BaseModel): key: str; label: str; online: bool
class ChatPublicFieldOut(BaseModel):
    key: str; kind: Literal["text", "textarea", "email", "select", "checkbox"]; label: str; required: bool
    options: list[PrechatOption]
class ChatPublicUploadOut(BaseModel): enabled: bool; max_bytes: int; types: list[str]
class ChatPublicConfigOut(BaseModel):          # no admin name, ids or queue data
    greeting: str | None; privacy_url: str | None; theme: ChatTheme
    topics: list[ChatPublicTopicOut]; online: bool; offline_mode: Literal["form", "hide"]
    prechat_fields: list[ChatPublicFieldOut]; upload: ChatPublicUploadOut; max_message_length: int
class ChatSessionCreateIn(BaseModel):
    key: str = Field(max_length=40); origin: str = Field(max_length=200)
    visitor_id: str = Field(pattern=r"^[A-Za-z0-9_-]{16,64}$"); topic: str | None = Field(default=None, max_length=40)
    prechat: dict[str, str | bool] = Field(default_factory=dict, max_length=20)
    message: str = Field(max_length=4000); client_id: str | None = Field(default=None, max_length=64)
    page_url: str | None = Field(default=None, max_length=500); language: str | None = Field(default=None, max_length=16)
class ChatSessionCreatedOut(BaseModel): session_id: str; token: str; status: str; message: ChatMessageOut
class ChatAttachmentOut(BaseModel): index: int; filename: str; content_type: str; size: int
class ChatMessageOut(BaseModel):
    seq: int; sender: Literal["visitor", "agent", "system"]; body: str | None; code: str | None
    params: dict[str, str | bool | None]; agent_name: str | None; created: UtcDateTime
    attachments: list[ChatAttachmentOut]; client_id: str | None
class ChatSessionOut(BaseModel):
    session_id: str; status: str; agent_name: str | None; topic_label: str | None
    can_send: bool; needs_contact: bool; last_seq: int
class ChatMessageIn(BaseModel): body: str = Field(max_length=4000); client_id: str | None = Field(default=None, max_length=64)
class ChatContactIn(BaseModel): name: str | None = Field(default=None, max_length=200); email: str = Field(max_length=250)
class ChatOfflineIn(BaseModel):
    key: str; origin: str; visitor_id: str (same pattern); topic: str | None; name: str | None = Field(None, max_length=200)
    email: str = Field(max_length=250); message: str = Field(max_length=4000)
    prechat: dict[str, str | bool] = Field(default_factory=dict, max_length=20); page_url: str | None; language: str | None
```

Public router `/api/chat/v1` (`tags=["webchat-public"]`; every endpoint first refuses a foreign `Origin` header with 403 `cross_origin`, then checks `channel_enabled(db, "webchat")` → 404):

| Method + path | Body / params | Response | Limits |
|---|---|---|---|
| `GET /config` | `k`, `origin` | `ChatPublicConfigOut` (403 origin, 404 unknown/invalid/disabled) | `config` 60/min per ip |
| `POST /sessions` | `ChatSessionCreateIn` | 201 `ChatSessionCreatedOut`; 409 `offline` | `session_create` `sessions_per_ip_10min` per ip + 3/10 min per visitor_id + 120/min per config |
| `GET /sessions/{sid}` | token | `ChatSessionOut` | `session_read` 120/min per session |
| `GET /sessions/{sid}/messages` | token, `after` (≥0), `limit` ≤ 200 | `list[ChatMessageOut]` | `session_read` |
| `GET /sessions/{sid}/stream` | token, `Last-Event-ID` / `after` | SSE (C7) | `stream_connect` 30/min per session |
| `POST /sessions/{sid}/messages` | `ChatMessageIn` | 201 `ChatMessageOut` | `message` per session + per ip |
| `POST /sessions/{sid}/typing` | — | 204 (dropped silently above 1 / 2 s) | `try_hit` |
| `POST /sessions/{sid}/attachments` | multipart `file`, `caption?`, `client_id?` | 201 `ChatMessageOut` | `upload` per session |
| `GET /sessions/{sid}/messages/{seq}/attachments/{index}` | token | file via `safe_attachment_response` (raster inline, else attachment) | `session_read` |
| `POST /sessions/{sid}/contact` | `ChatContactIn` | 204 | `message` |
| `POST /sessions/{sid}/end` | — | 204 | `message` |
| `POST /offline` | `ChatOfflineIn` | 201 `{}` | `offline_per_ip_hour` |

Token failures, unknown session ids and purged sessions all answer the same `404 {"detail": "not_found"}`.

Agent (`backend/src/tiqora/api/v1/chat.py`, router `tags=["chat"]`):

```python
class ChatTopicRefOut(BaseModel): ref: str; config_name: str; label: str; queue_id: int
class ChatAgentStateOut(BaseModel): available: bool; topics: list[str]; max_concurrent: int; active_count: int
class ChatAgentStateIn(BaseModel): available: bool; topics: list[str] = Field(default_factory=list, max_length=100); max_concurrent: int = Field(3, ge=1, le=20)
class ChatInboxItemOut(BaseModel):
    session_id: str; ticket_id: int; tn: str; title: str; topic_label: str | None; visitor_name: str | None
    status: Literal["waiting", "active"]; waiting_since: UtcDateTime | None; accepted_at: UtcDateTime | None
class ChatInboxOut(BaseModel): waiting: list[ChatInboxItemOut]; mine: list[ChatInboxItemOut]
class ChatAcceptOut(BaseModel): ticket_id: int
class ChatEndIn(BaseModel): close_ticket: bool = False
class TicketChatOut(BaseModel):
    session_id: str; status: str; end_reason: str | None; visitor_name: str | None; visitor_email: str | None
    topic_label: str | None; page_url: str | None; language: str | None
    waiting_since: UtcDateTime | None; accepted_at: UtcDateTime | None; ended_at: UtcDateTime | None
    assigned_user_id: int | None; assigned_name: str | None; visitor_online: bool; visitor_typing: bool
```

| Method + path | Response |
|---|---|
| `GET /api/v1/chat/agent-state` / `PUT` (`ChatAgentStateIn`) | `ChatAgentStateOut` |
| `GET /api/v1/chat/topics` | `list[ChatTopicRefOut]` (topics of valid configs whose queue the agent has `rw` on) |
| `GET /api/v1/chat/inbox` | `ChatInboxOut` |
| `POST /api/v1/chat/sessions/{sid}/accept` | `ChatAcceptOut`; 409 `already_taken`/`at_capacity`/`not_waiting`; 403; 404 |
| `POST /api/v1/chat/sessions/{sid}/end` (`ChatEndIn`) | 204 |
| `GET /api/v1/tickets/{ticket_id}/chat` | `TicketChatOut` of the newest session of the ticket (404 none; ro on the ticket's queue) |
| `POST /api/v1/tickets/{ticket_id}/chat/typing` | 204 |

Admin (`backend/src/tiqora/api/v1/admin/chat_configs.py`, prefix `/chat-configs`, `AdminUser`):

```python
class ChatConfigOut(BaseModel):
    id: int; name: str; public_key: str; valid: bool; values: ChatConfigValues; embed_snippet: str
    session_count: int; created: UtcDateTime; changed: UtcDateTime
class ChatConfigCreateIn(BaseModel): name: str = Field(min_length=1, max_length=100); valid: bool = True; values: ChatConfigValues
class ChatConfigUpdateIn(BaseModel): name: str | None = Field(None, min_length=1, max_length=100); valid: bool | None = None; values: ChatConfigValues | None = None
```

`GET ""` list, `POST ""` 201, `GET /{id}`, `PATCH /{id}`, `DELETE /{id}` (409 `in_use` when sessions exist — switch it off instead), `POST /{id}/rotate-key`. Save-time checks (422 with `{"detail": {"errors": {"<path>": "<German message>"}}}`): topic `queue_id` exists and valid, `priority_id` exists, `dynamic_field` exists, is valid, object type Ticket, field type in `DF_KINDS_FOR_PRECHAT`, `default_customer_user` exists. `embed_snippet = f'<script src="{base}/chat/v1/loader.js" data-tiqora-chat="{public_key}" async></script>'` with `base = own_origin(request, settings)`.

### C9. Static routes and the widget contract (Tasks 13–17)

`backend/src/tiqora/webchat/static.py` (`include_in_schema=False`), mounted before `mount_spa`; `/chat/` added to `spa._RESERVED_PREFIXES`; `security_headers_middleware` skips its CSP `setdefault` for paths starting with `/chat/v1/` (the frame sets an ENFORCED `Content-Security-Policy` itself, independent of `csp_enforce`; Report-Only `frame-ancestors` would block nothing).

- `GET /chat/v1/loader.js` → `dist/loader.js`, `text/javascript; charset=utf-8`, `Cache-Control: public, max-age=300`, `Cross-Origin-Resource-Policy: cross-origin`. 404 when the build is missing or the channel is off.
- `GET /chat/v1/frame?k=&origin=&lang=&preview=` → `dist/frame.html` (read once, cached by mtime), `Cache-Control: no-store`, `X-Robots-Tag: noindex`, `Referrer-Policy: same-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `Content-Security-Policy: frame_csp(allowed)`. Refusals (403, tiny German text, `frame-ancestors 'none'`): unknown/invalid key or channel off (404), `origin` not allowed, `Referer` present whose origin ≠ `origin` param. `preview=1` → only allowed when the `Referer` origin is Tiqora's own origin, CSP `frame_csp([], preview_only=True)`.
- `/chat/v1/assets/*` → `StaticFiles` subclass with `Cache-Control: public, max-age=31536000, immutable`.
- Settings: `webchat_dist_dir: str = Field("/app/webchat/dist", validation_alias="TIQORA_WEBCHAT_DIST_DIR")`, `chat_max_streams: int = Field(2000, ge=10, validation_alias="TIQORA_CHAT_MAX_STREAMS")`.

postMessage protocol (`packages/webchat-widget/src/shared/protocol.ts`):

```ts
export const PROTOCOL = "tiqora-chat/1" as const;
export type FrameTheme = { primaryColor: string; position: "right" | "left"; mode: "auto" | "light" | "dark" };
export type FrameToHost =
  | { p: typeof PROTOCOL; type: "ready"; online: boolean; hidden: boolean; launcherLabel: string; theme: FrameTheme; dir: "ltr" | "rtl" }
  | { p: typeof PROTOCOL; type: "availability"; online: boolean }
  | { p: typeof PROTOCOL; type: "close-request" }
  | { p: typeof PROTOCOL; type: "unread"; count: number }
  | { p: typeof PROTOCOL; type: "message"; sender: "agent" | "system" };
export type HostToFrame =
  | { p: typeof PROTOCOL; type: "open" }
  | { p: typeof PROTOCOL; type: "close" }
  | { p: typeof PROTOCOL; type: "set-topic"; topic: string }
  | { p: typeof PROTOCOL; type: "preview-config"; config: PreviewConfig };   // only honoured with preview=1 from Tiqora's own origin
export function isFrameToHost(data: unknown): data is FrameToHost;   // strict shape check incl. value types
export function isHostToFrame(data: unknown): data is HostToFrame;
```

Rules: the loader accepts a message only when `event.origin === tiqoraOrigin && event.source === iframe.contentWindow && isFrameToHost(event.data)`; the frame accepts only when `event.origin === hostOrigin && event.source === window.parent && isHostToFrame(event.data)` and posts with `window.parent.postMessage(msg, hostOrigin)` — never `"*"`. Message content never crosses to the host page (only "a message arrived").

Host API (`window.Tiqora.chat`): `open()`, `close()`, `toggle()`, `setTopic(key: string)`, `on(event: "ready" | "open" | "close" | "message" | "availability", cb) → () => void`. Embed: `<script src="https://helpdesk.example.org/chat/v1/loader.js" data-tiqora-chat="wc_…" async></script>`. Host CSP needs `script-src` and `frame-src` for the Tiqora origin (documented).

Widget storage (in the iframe's partitioned storage, every access in try/catch, memory fallback): `tiqora-chat:visitor` → visitor id (22 chars `crypto.getRandomValues` base64url); `tiqora-chat:session:<key>` → `{sessionId, token, lastSeq}`.

### C10. Frontend (agent/admin) contracts (Tasks 18–21)

```ts
// lib/webchatApi.ts
export type ChatAgentState = Schemas["ChatAgentStateOut"];   export type ChatAgentStateIn = Schemas["ChatAgentStateIn"];
export type ChatTopicRef = Schemas["ChatTopicRefOut"];        export type ChatInbox = Schemas["ChatInboxOut"];
export type ChatInboxItem = Schemas["ChatInboxItemOut"];      export type TicketChat = Schemas["TicketChatOut"];
export type ChatConfig = Schemas["ChatConfigOut"];            export type ChatConfigValues = Schemas["ChatConfigValues"];
export const chatAgentStateKey = ["chat", "agent-state"] as const;
export const chatInboxKey = ["chat", "inbox"] as const;
export const chatTopicsKey = ["chat", "topics"] as const;
export function ticketChatKey(ticketId: number) { return ["tickets", ticketId, "chat"] as const; }
export const webchatApi = { getAgentState, putAgentState, topics, inbox, accept, end, ticketChat, typing,
  listConfigs, getConfig, createConfig, updateConfig, deleteConfig, rotateKey };
export function useTicketChat(ticketId: number, enabled: boolean): UseQueryResult<TicketChat | null>;  // 404 → null

// lib/chatRing.ts
export function receiveChatWaiting(msg: { session_id: string; topic_ref?: string | null }): void
export function useChatRing(): { pending: number }          // ring = WebAudio beep + Notification when permitted
export function setRingContext(ctx: { available: boolean; topics: string[] }): void

// components/agent/messenger/adapters.ts
export type MessengerChannelAdapter = {
  channel: "telegram" | "webchat";
  maxMessageLength: number;            // telegram 4096, webchat 4000
  supportsButtons: boolean;            // telegram true, webchat false
  supportsQuote: boolean;              // telegram true, webchat false
  metaQueryKey: (ticketId: number) => readonly unknown[];
  sendTyping: (ticketId: number) => Promise<unknown>;
  extraPayload: (o: { quoteId: number | null; buttons: TelegramButtonIn[] }) => Partial<ArticleCreateRequest>;
  useRecipientName: (ticketId: number) => string | null;
};
export const telegramAdapter: MessengerChannelAdapter;
export const webchatAdapter: MessengerChannelAdapter;
// MessengerComposer.tsx
export function MessengerComposer(props: { ticketId: number; adapter: MessengerChannelAdapter;
  onComposingChange?: (composing: boolean) => void }): JSX.Element;
```

SSE (`lib/useSSE.ts`): new `ChatEventMessage = { type: "chat_waiting" | "chat_assigned" | "chat_message" | "chat_typing" | "chat_ended"; session_id: string; ticket_id: number; queue_id: number; user_id?: number; topic_ref?: string }` in `SSEMessage`; `handleSSEMessage`: every chat type invalidates `chatInboxKey`; `chat_message`/`chat_assigned`/`chat_ended` also invalidate `["tickets", ticket_id]`; `chat_typing` invalidates `ticketChatKey(ticket_id)`; `chat_waiting` calls `receiveChatWaiting`.

### C11. i18n namespaces (German first; English in `en.json`)

- `webchatWidget.*` — every text in the widget (≈ 45 keys; list in Task 14). Extracted at widget build time, flattened to `"a.b": "…"`, one lazy chunk per language, fallback `en`.
- `webchat.agent.*` — inbox, availability, session header, composer messages.
- `webchat.admin.*` — list, editor tabs, field labels, validation messages; `admin.nav.webchat`, `admin.pageDescriptions.webchat`.

### Deviations from spec

1. **Article storage.** Spec: articles on Znuny's seeded `Chat` channel. Znuny's `Chat` backend stores lines in `article_data_otrs_chat`, has no attachments (`ArticleAttachmentIndex` returns nothing) and its `ArticleDelete` deletes only `article_data_otrs_chat` (a MIME row would then break `TicketDelete` on the `article_data_mime` FK), while ~40 Tiqora readers (agent view, portal, AI context, search, MCP, compat `TicketGet`, GDPR) read MIME. Decision: a Tiqora-owned `WebChat` channel row via `ensure_channel_row("WebChat", "Kernel::System::CommunicationChannel::Internal")` — MIME storage like Telegram, but with the `Internal` module Znuny's `BackendForArticle` loads the MIMEBase `Internal` backend, so Znuny renders the text and attachments (not "Invalid article" as for Telegram) and deletes them correctly (golden test, Task 2). `LIST_CHANNELS` gains `"WebChat": "webchat"`; the existing `"Chat": "webchat"` mapping stays for archived OTRS chats. A Znuny `Admin::CommunicationChannel::Sync` drops channel rows that have no `CommunicationChannel###…` registration, exactly like the Telegram row today — documented; registering `WebChat` in TiqoraSync is a follow-up.
2. **Session = first message.** `POST /sessions` carries the first message; there are no empty sessions. The ticket is created then (as the spec wants).
3. **New ticket per session** in phase 1 (continuation by email/portal is phase 3).
4. **Statuses**: `bot` is reserved; phase 1 uses `waiting|active|ended|abandoned`. The offline form stores an `ended` session row with `end_reason="offline_form"` (so GDPR and reporting see it).
5. **Columns deferred** to the phase that uses them (own migration then): `identity_secret`, `block_ips`, `block_countries`, `participants`, `country`, `rating`, `rating_comment`, `read_at`, edit/retract flags. `active_count` is computed, not stored (no drift). Added: `queue_id`, `token_hash`, `last_seq`, `visitor_last_seen`, `end_reason`, `purged_at`, `privacy_url`, `wait_timeout_seconds`, `rate_limits`, `default_customer_user`, `show_agent_names`, message `ticket_id`/`meta`/`client_id`.
6. **Visitor auth**: per-session random token (only its SHA-256 stored) in the `X-Chat-Token` header; the widget reads SSE with `fetch` streaming instead of `EventSource` (which cannot send headers; a token in the URL would end up in proxy logs).
7. **CORS**: none. All widget calls come from the iframe on the Tiqora origin (same-origin). Requests carrying a foreign `Origin` are refused with 403. "Allowed origins per chat" is enforced by `frame-ancestors`, the frame's `origin`/`Referer` check, the `origin` check on `/config`, `/sessions`, `/offline`, and the postMessage origin checks.
8. **Fan-out**: one `psubscribe tiqora:chat:*` per API process (`ChatHub`); Redis carries only wake/typing markers and the stream reads messages from the DB, so live delivery and replay share one code path.
9. **Uploads**: png/jpeg/gif/webp/pdf/txt only in phase 1 (no reliable magic-byte check for Office/zip); one upload = one message (no staging store).
10. **Waiting**: phase 1 has the waiting timeout (`no_agent` end + email follow-up prompt); queue position, transfer/invite, rating and block lists are phase 2.
11. **AI**: webchat articles are skipped by the auto-reply worker in phase 1 (`AUTO_REPLY_SKIP_CHANNELS`), so no email auto-reply reaches a chat visitor; drafts (assist) work as on other tickets. Phase 2 adds the first line.
12. **Master switch** = the existing channel flag `channel.webchat.enabled` (`CHANNEL_CONFIG_KEYS["webchat"] = set()`, default off; same admin channels API).
13. **Host API**: `open/close/toggle/setTopic/on(...)`; `identify` comes with signed identities (phase 2).
14. **Notifications**: first visitor message → `NotificationNewTicket` (WebRequestCustomer, like Znuny's web requests); follow-up chat lines use `NoAgentNotify`.
15. **Admin tabs**: *KI* and the block-list part of *Schutz* come with phase 2; *Einbetten* shows the snippet and key rotation (identity secret phase 2).
16. **Business hours**: a calendar without any working hours counts as "always open" (Znuny would compute 0 working minutes; for a chat, "never open" would silently hide it).
17. **Rate limiter fails closed** (503) when Redis is down — the chat cannot deliver anything without Redis anyway.
18. **Transcript option** ("Transkript als ein Artikel beim Beenden") moves to phase 2; with decision 1 Znuny already shows every message.
19. **Load test** is a manual script with documented results (`backend/scripts/webchat_stream_load.py`, Task 22), not a CI gate.
20. **Retention** runs in the webchat sweep (`tiqora-worker`, `webchat_sweep`), not in the GDPR retention engine: the engine handles tickets, chat rows are a side table (R-DEVIATIONS a).
21. **Ticket defaults**: chat tickets use the Postmaster default state and priority and user 1 (not the `CustomerTicketMessage` defaults or `CustomerPanelUserID`) and never trigger Znuny's web-ticket auto-response (`AutoResponseForWebTickets`, a Perl boolean that must be read with `get_bool` if it is ever honoured) (R-DEVIATIONS b).
22. **Widget locales**: English is merged into every widget locale chunk at build time, so a missing key falls back to English (Task 14).
23. **Retention purge also clears `visitor_id`** (and the token hash) of the session row, not only name, e-mail, page, user agent and IP hash (Task 11).
24. **Admin preview** works for disabled, invalid or draft configs and while the channel switch is off; it is frameable only by 'self' (R-PREVIEW-DRAFT).
25. **Pre-chat reserved keys and mapping**: the field keys `message` and `topic` are reserved, and at most one field maps to E-Mail and one to Name (R-PRECHAT-KEYS), so the widget's offline "first email field" convention holds.
26. **No `availability` frame message in phase 1**: the loader fires the host `on("availability")` event once, derived from `ready.online`; the message type stays in the protocol for later phases (R-AVAILABILITY).
27. **MCP**: `ticket_reply` into a waiting chat returns `chat_not_accepted`; there is no MCP accept tool in phase 1 (R-MCP-CHAT-WAITING). MCP-posted replies reach the visitor with the next stream heartbeat (at most 25 s).
28. **Notifications**: recipients in the reserved `.invalid` TLD are dropped on every outbound mail path, and a visitor without an e-mail address is never mailed (R-NOTIFY).

---
## Tasks

Order: backend Tasks 1→13 sequential (each builds on the committed previous ones). Widget Tasks 14→17 need only Task 7's regenerated `openapi.json` for types (14 may start after Task 1; 15–17 after 7). Frontend Tasks 18→21 need Tasks 9–12 (openapi). Task 22 closes. Backend-only: 1–13. Widget-only: 14–17. Frontend-only: 18–21.

Seed sub-bands: T1 48400–48419 · T2 48420–48449 · T5 48450–48489 · T6 48490–48549 · T7 48550–48599 · T8 48600–48619 · T9 48620–48669 · T10 48670–48699 · T11 48700–48729 · T12 48730–48759 · T13 48760–48779 · spare 48780–48799.

### Task 1: Tables, ORM, migration, config value models, master switch, test fixtures (backend)

**Files:**
- Create: `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_webchat.py` (revision = next number after the current head; `down_revision` = current head — check, never copy an id from here)
- Modify: `backend/src/tiqora/db/tiqora/models.py` (four ORM classes of C1)
- Create: `backend/src/tiqora/webchat/__init__.py`, `backend/src/tiqora/webchat/models.py` (C2 verbatim)
- Modify: `backend/src/tiqora/channels/registry.py` (`CHANNEL_CONFIG_KEYS["webchat"] = set()` — the master switch `channel.webchat.enabled`; `CHANNELS` description gains "web chat")
- Modify: `backend/tests/test_migration_gate.py`, `backend/tests/test_migrate_cli.py`, `backend/tests/test_tiqora_model_ddl.py`, `backend/tests/test_admin_channels.py` (list now contains `webchat`)
- Create: `backend/tests/_webchat_fixtures.py`
- Test: `backend/tests/test_webchat_tables.py` (both dialects, band 48400–48419), `backend/tests/test_webchat_models.py` (pure)

**Interfaces:**
- Consumes: `TiqoraBase`, `MAX_ATTACHMENT_BYTES` (`tiqora.api.uploads`), `tests._row_cleanup.snapshot_marks/delete_above_marks`.
- Produces: C1 tables + `TiqoraChatConfig`, `TiqoraChatSession`, `TiqoraChatMessage`, `TiqoraChatAgentState`; C2 models; fixtures:
  ```python
  # backend/tests/_webchat_fixtures.py
  ZNUNY_MARK_TABLES: tuple[str, ...] = (
      "tiqora_chat_message", "tiqora_event_outbox", "tiqora_cache_invalidation", "ticket_history",
      "dynamic_field_value", "article_flag", "article_data_mime_attachment", "article_data_mime_plain",
      "article_data_mime", "article", "ticket", "ticket_number_counter", "communication_channel",
  )
  def seed_queue_agent(conn, *, queue_id: int, group_id: int, user_id: int, login: str, rw: bool = True) -> None
      # permission_groups + queue (system_address 1, salutation 1, signature 1, follow_up_id 1) + users + group_user rw/ro
  def insert_config(conn, *, config_id: int, queue_id: int, public_key: str, origins: list[str],
                    values_override: dict[str, Any] | None = None, valid: bool = True) -> None
      # JSON columns from ChatConfigValues(topics=[ChatTopic(key="support", label="Support", queue_id=queue_id)],
      #                                    allowed_origins=origins, **override).model_dump(mode="json")
  def cleanup_band(conn, *, config_ids: range, queue_ids: Iterable[int], group_ids: Iterable[int],
                   user_ids: Iterable[int]) -> None
      # DELETE tiqora_chat_message/session by config band, tiqora_chat_agent_state by users, tiqora_chat_config,
      # group_user, queue, permission_groups, users; tiqora_settings 'channel.webchat.%'
  def enable_webchat(conn) -> None    # tiqora_settings channel.webchat.enabled = "1" (via TiqoraSettings model)
  ```

- [ ] **Step 1: Write the failing tests.** `test_webchat_models.py`:
  ```python
  def test_defaults_are_valid() -> None:
      v = ChatConfigValues(topics=[ChatTopic(key="support", label="Support", queue_id=1)])
      assert v.offline_mode == "form" and v.wait_timeout_seconds == 180 and v.retention_days == 30
      assert [f.key for f in v.prechat_fields] == ["name", "email"]
      assert v.upload_policy.max_bytes == 10 * 1024 * 1024 and "image/svg+xml" not in v.upload_policy.types
  def test_topic_keys_unique() -> None:
      with pytest.raises(ValidationError):
          ChatConfigValues(topics=[ChatTopic(key="a", label="A", queue_id=1), ChatTopic(key="a", label="B", queue_id=2)])
  @pytest.mark.parametrize("field", [
      {"key": "message", "kind": "text", "label": "x"},                       # reserved key
      {"key": "s", "kind": "select", "label": "x"},                           # select without options
      {"key": "t", "kind": "text", "label": "x", "options": [{"value": "a", "label": "A"}]},
      {"key": "m", "kind": "text", "label": "x", "maps_to": "email"},         # email mapping needs kind email
      {"key": "d", "kind": "text", "label": "x", "maps_to": "dynamic_field"}, # missing dynamic_field
  ])
  def test_bad_prechat_fields_rejected(field: dict[str, Any]) -> None:
      with pytest.raises(ValidationError):
          PrechatField.model_validate(field)
  def test_two_email_mappings_rejected() -> None:
      fields = [PrechatField(key="a", kind="email", label="A", maps_to="email"),
                PrechatField(key="b", kind="email", label="B", maps_to="email")]
      with pytest.raises(ValidationError):
          ChatConfigValues(topics=[ChatTopic(key="s", label="S", queue_id=1)], prechat_fields=fields)
  def test_theme_color_and_upload_bounds() -> None:
      with pytest.raises(ValidationError):
          ChatTheme(primary_color="red")
      with pytest.raises(ValidationError):
          UploadPolicy(max_bytes=26 * 1024 * 1024)
      with pytest.raises(ValidationError):
          UploadPolicy(types=["image/svg+xml"])
  ```
  (`allowed_origins` normalisation is tested in Task 3, where `validate_origins` is created; in this task the field validator is wired to it, so Task 3 adds `test_values_normalise_origins`.) Append to `test_tiqora_model_ddl.py`:
  ```python
  def test_webchat_ddl() -> None:
      from tiqora.db.tiqora.models import TiqoraChatAgentState, TiqoraChatMessage, TiqoraChatSession
      assert "AUTO_INCREMENT" not in _column_ddl(TiqoraChatAgentState.__table__, "user_id", mysql.dialect())
      assert "VARCHAR(32)" in _column_ddl(TiqoraChatSession.__table__, "id", mysql.dialect())
      uniques = {tuple(c.name for c in ix.columns) for ix in TiqoraChatMessage.__table__.indexes if ix.unique}
      assert ("session_id", "seq") in uniques and ("session_id", "client_id") in uniques
  ```
  `test_webchat_tables.py` (both dialects; `create_all`; module fixture `cleanup_band(config_ids=range(48400, 48420), …)`):
  ```python
  async def test_config_session_message_round_trip(url_fixture, request) -> None:
      # insert_config(config_id=48401, queue_id=48402, public_key="wc_" + "a" * 32, origins=["https://www.example.org"])
      # insert TiqoraChatSession(id="0"*32, config_id=48401, queue_id=48402, ticket_id=48403, status="waiting",
      #   token_hash="f"*64, visitor_id="v"*22, visitor_last_seen=NOW, created=NOW)
      # insert two TiqoraChatMessage rows seq 1 and 2 with client_id None both  → no IntegrityError (NULLs never collide)
      # a third row with seq=2 → IntegrityError; a row with client_id "c1" twice → IntegrityError
      # read back: config_values(row).topics[0].queue_id == 48402 (config_values comes in Task 2: here json.loads only)
  async def test_agent_state_pk_is_user_id(url_fixture, request) -> None:
      # insert TiqoraChatAgentState(user_id=48405, available=True, topics="[]", max_concurrent=3, changed=NOW)
      # second insert with same user_id → IntegrityError
  ```
- [ ] **Step 2: Run to verify failure.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_webchat_models.py tests/test_webchat_tables.py tests/test_tiqora_model_ddl.py` → FAIL (`ModuleNotFoundError: tiqora.webchat`).
- [ ] **Step 3: Migration.** `op.create_table` for the four tables exactly as C1 (index/unique names as listed), `downgrade` drops them in reverse order. Update `TIQORA_HEAD` and the `version ==` assertion. `uv run python -m pytest -q tests/test_migration_gate.py tests/test_migrate_cli.py` → PASS.
- [ ] **Step 4: Implement** the ORM classes, `webchat/models.py` (C2; the `allowed_origins` validator calls `tiqora.webchat.origins.validate_origins` — create `origins.py` now with a stub `def validate_origins(values): return list(values)` that Task 3 replaces), `CHANNEL_CONFIG_KEYS["webchat"] = set()`, the fixtures module. Update `test_admin_channels.py` expectations for the extra `webchat` row (enabled False, empty config).
- [ ] **Step 5: Run** Step 2 command + `tests/test_admin_channels.py` → PASS; lint gates; commit:
  ```bash
  rtk git add backend/alembic/versions_tiqora backend/src/tiqora/db/tiqora/models.py backend/src/tiqora/webchat backend/src/tiqora/channels/registry.py backend/tests/_webchat_fixtures.py backend/tests/test_webchat_tables.py backend/tests/test_webchat_models.py backend/tests/test_tiqora_model_ddl.py backend/tests/test_migration_gate.py backend/tests/test_migrate_cli.py backend/tests/test_admin_channels.py
  rtk git diff --cached --name-only
  rtk git commit -m "$(printf 'feat(webchat): chat tables, config value models, channel master switch\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
  rtk git log -1 --oneline
  ```

### Task 2: WebChat article channel, ticket-write hooks, GDPR, auto-reply guard, golden test (backend)

**Files:**
- Modify: `backend/src/tiqora/domain/ticket_write_service.py` (`channel_name_map["webchat"] = "WebChat"`; `_CHAT_CHANNELS` += `"webchat"`; `merge_tickets` moves `tiqora_chat_message`/`tiqora_chat_session` rows like `tiqora_telegram_message` (step "4b"); `delete_article` deletes `tiqora_chat_message` rows of that article)
- Modify: `backend/src/tiqora/domain/ticket_service.py` (`LIST_CHANNELS["WebChat"] = "webchat"`)
- Modify: `backend/src/tiqora/gdpr/erasure.py` (`_TICKET_DELETE_ORDER` gains `("tiqora_chat_message", "ticket_id", "ticket")`, `("tiqora_chat_session", "ticket_id", "ticket")` before `("article", …)`; anonymize path deletes both via `_backup_and_delete_by(…, fk_col="ticket_id", ids=ticket_ids)` next to the telegram block; counts keys `tiqora_chat_message`, `tiqora_chat_session`)
- Modify: `backend/src/tiqora/ai/gate.py` (`AUTO_REPLY_SKIP_CHANNELS: frozenset[str] = frozenset({"webchat"})`), `backend/src/tiqora/ai/auto_worker.py` (skip events whose `channel` is in it before `should_process`, counted as `totals["skipped_channel"]`)
- Modify: `backend/src/tiqora/worker/poller.py` (customer articles on the `WebChat` channel no longer add the ticket to `notify_ticket_ids` — the `ticket_new_in_queue` bell/toast would otherwise fire for every chat line; the `NewTicket` history row of a chat's first message still triggers it once; join `communication_channel` on the integer `Article.communication_channel_id`)
- Modify: `backend/src/tiqora/webchat/store.py` (create: `load_config`, `load_config_by_key`, `config_values`)
- Test: `backend/tests/test_poller.py` (one new test), `backend/tests/test_webchat_ticket_mapping.py` (both dialects, band 48420–48449), `backend/tests/test_ai_auto_worker.py` (one new test), `backend/tests/test_gdpr_erasure.py` (one new test), `tests/golden/test_webchat_articles.py`

**Interfaces:**
- Consumes: `ensure_channel_row`, `add_article`, `merge_tickets`, `delete_article`, `_backup_and_delete_by`.
- Produces: `COMM_CHANNEL_NAME`/`COMM_CHANNEL_MODULE` constants (put them in `webchat/sessions.py` now, with only the constants), `AUTO_REPLY_SKIP_CHANNELS`, `store.load_config(db, config_id) -> TiqoraChatConfig | None`, `store.load_config_by_key(db, public_key) -> TiqoraChatConfig | None` (valid only), `store.config_values(row) -> ChatConfigValues` (each JSON column parsed separately; invalid JSON or validation error for a column → that column's default + `logger.warning("webchat_config_column_invalid", config_id=…, column=…)`; topics invalid → `[]` and the config counts as offline).

- [ ] **Step 1: Write the failing tests.** `test_webchat_ticket_mapping.py`:
  ```python
  async def test_webchat_article_uses_webchat_channel_and_web_request_history(url_fixture, request) -> None:
      # seed queue 48421 (+group 48422, agent 48423); create_ticket(...) then
      # ensure_channel_row(db, "WebChat", "Kernel::System::CommunicationChannel::Internal")
      # add_article(customer, visible, channel="webchat") twice, then add_article(agent, visible, channel="webchat")
      # assert communication_channel row name "WebChat", module "Kernel::System::CommunicationChannel::Internal",
      #        channel_data equals the Internal row's channel_data (bytes compare)
      # assert ticket_history types of the three ArticleCreate rows == ["WebRequestCustomer", "FollowUp", "SendAnswer"]
      # assert TicketService list channel for the ticket == "webchat"
  async def test_merge_moves_chat_rows(url_fixture, request) -> None:
      # two tickets 48430/48431 with a tiqora_chat_session (ticket_id=48431) and two tiqora_chat_message rows
      # merge 48431 into 48430 → session.ticket_id == 48430 and both message rows ticket_id == 48430
  async def test_delete_article_removes_chat_message(url_fixture, request) -> None:
      # message row with article_id = A; delete_article(A) → no tiqora_chat_message with article_id A
  def test_config_values_fall_back_per_column(caplog) -> None:
      row = TiqoraChatConfig(id=1, name="x", public_key="wc_x", valid=True, allowed_origins="not json",
                             topics='[{"key": "s", "label": "S", "queue_id": 3}]', prechat_fields="[]",
                             upload_policy='{"max_bytes": 1}', theme="{}", rate_limits="{}", offline_mode="form",
                             wait_timeout_seconds=180, show_agent_names=True, retention_days=30)
      v = config_values(row)
      assert v.allowed_origins == [] and v.topics[0].queue_id == 3
      assert v.upload_policy.max_bytes == 10 * 1024 * 1024        # invalid value → default
      assert "webchat_config_column_invalid" in caplog.text
  ```
  `test_ai_auto_worker.py`: `test_webchat_customer_article_is_never_auto_replied` — feed one `ArticleCreate` outbox event with `payload={"channel": "webchat", "sender_type": "customer", …}` in `tiqora_primary` mode with a policy that would auto-reply; assert `_process_customer_article_event` was not called (patch it with an `AsyncMock`) and `totals["skipped_channel"] == 1`.
  GDPR test: `test_hard_delete_and_anonymize_remove_chat_rows` — erase a customer whose ticket has one chat session + two messages; after apply both tables have no rows for the ticket; after `rollback_job` they are back with identical `body`/`visitor_email`.
  `test_poller.py`: `test_webchat_follow_up_does_not_toast` — a ticket with a `NewTicket` history row and three customer articles on the `WebChat` channel in one poll window → exactly one `ticket_new_in_queue` publish for it; three customer articles on `Email` for another ticket → still one publish (unchanged behaviour); a second poll window with only WebChat customer articles → no publish.
  Golden (`tests/golden/test_webchat_articles.py`, `pytestmark = pytest.mark.golden`): Tiqora creates a ticket with one webchat customer article containing an attachment (`hello.txt`); `znuny_perl_eval` gets `my $Backend = $ArticleObject->BackendForArticle(TicketID => …, ArticleID => …)` and prints `ref($Backend)`, `$Backend->ArticleGet(...)->{Body}` and the `ArticleAttachmentIndex` filenames → asserts `Kernel::System::Ticket::Article::Backend::Internal` (NOT `…::Invalid`), the body text, `hello.txt`; then Perl `TicketDelete(TicketID => …, UserID => 1)` returns 1 and `SELECT COUNT(*) FROM article WHERE ticket_id = …` is 0.
- [ ] **Step 2: Run to verify failure** (`uv run python -m pytest -q tests/test_webchat_ticket_mapping.py tests/test_ai_auto_worker.py -k webchat`) → FAIL (history type `AddNote`, channel row "Internal").
- [ ] **Step 3: Implement** the hooks listed under Files. In `merge_tickets` use the existing `_table_exists` guard and `UPDATE … SET ticket_id = :main WHERE ticket_id = :merge` for both tables.
- [ ] **Step 4: Run** the tests → PASS; golden locally when a peer is up (`cd tests/golden && …` per `tests/golden/README-multi-peer.md`; CI runs it in `golden.yml`); lint gates; commit (`feat(webchat): WebChat article channel, merge/delete/GDPR hooks, no auto-reply on chat`).

### Task 3: Pure rules — origins, CSP, text, uploads, tokens (backend, pure)

**Files:**
- Modify: `backend/src/tiqora/webchat/origins.py` (replace the stub; C3)
- Create: `backend/src/tiqora/webchat/text.py`, `backend/src/tiqora/webchat/uploads.py`, `backend/src/tiqora/webchat/tokens.py` (C3)
- Test: `backend/tests/test_webchat_rules.py`

**Interfaces:**
- Consumes: `html_to_plaintext` (`tiqora.domain.quoting`), `Settings.public_base_url`, `UploadPolicy`.
- Produces: every C3 name.

- [ ] **Step 1: Write the failing tests.**
  ```python
  @pytest.mark.parametrize("raw,expected", [
      ("https://www.Example.org", "https://www.example.org"),
      ("https://www.example.org:443/", "https://www.example.org"),
      ("https://shop.example.org:8443", "https://shop.example.org:8443"),
      ("http://localhost:5173", "http://localhost:5173"),
      ("https://bücher.example", "https://xn--bcher-kva.example"),
  ])
  def test_normalize_origin_ok(raw: str, expected: str) -> None:
      assert normalize_origin(raw) == expected
  @pytest.mark.parametrize("raw", [
      "http://www.example.org",                  # http only for localhost
      "https://www.example.org/path", "https://www.example.org?x=1", "https://user@www.example.org",
      "https://*.example.org", "javascript:alert(1)", "https://a.example.org; script-src *",
      "https://a.example.org 'unsafe-inline'", "", "example.org",
  ])
  def test_normalize_origin_rejects_bad(raw: str) -> None:
      with pytest.raises(OriginError):
          normalize_origin(raw)
  def test_values_normalise_origins() -> None:
      v = ChatConfigValues(topics=[ChatTopic(key="s", label="S", queue_id=1)],
                           allowed_origins=["https://WWW.example.org/", "https://www.example.org"])
      assert v.allowed_origins == ["https://www.example.org"]
  def test_origin_allowed_exact_only() -> None:
      allowed = ["https://www.example.org"]
      assert origin_allowed("https://www.example.org", allowed)
      assert not origin_allowed("https://evil.example.org", allowed)
      assert not origin_allowed("https://www.example.org.evil.test", allowed)
      assert not origin_allowed(None, allowed) and not origin_allowed("garbage", allowed)
  def test_frame_csp() -> None:
      csp = frame_csp(["https://www.example.org", "https://shop.example.org"])
      assert csp.endswith("frame-ancestors 'self' https://www.example.org https://shop.example.org")
      assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "default-src 'none'" in csp
      assert frame_csp([]).endswith("frame-ancestors 'self'")
      assert frame_csp(["https://www.example.org"], preview_only=True).endswith("frame-ancestors 'self'")
  def test_sanitize_message_strips_controls_and_bidi() -> None:
      assert sanitize_message("  Hallo\r\nWelt\x07\u202e  ") == "Hallo\nWelt"
      assert sanitize_message("<script>alert(1)</script>") == "<script>alert(1)</script>"   # kept as TEXT
  def test_sanitize_message_rejects() -> None:
      with pytest.raises(MessageRejected) as e1:
          sanitize_message(" \n\t ")
      assert e1.value.code == "empty"
      with pytest.raises(MessageRejected) as e2:
          sanitize_message("x" * 2001)
      assert e2.value.code == "too_long"
  def test_chat_body_from_html_article() -> None:
      assert chat_body_from_article("<p>Hallo <b>Welt</b></p>", "text/html; charset=utf-8") == "Hallo Welt"
  @pytest.mark.parametrize("content,name,expected", [
      (b"\x89PNG\r\n\x1a\n" + b"0" * 20, "a.png", "image/png"),
      (b"\xff\xd8\xff\xe0" + b"0" * 20, "a.jpeg", "image/jpeg"),
      (b"GIF89a" + b"0" * 20, "a.gif", "image/gif"),
      (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "a.webp", "image/webp"),
      (b"%PDF-1.7\n", "a.pdf", "application/pdf"),
      ("Grüße\n".encode(), "notiz.txt", "text/plain"),
  ])
  def test_sniff_ok(content: bytes, name: str, expected: str) -> None:
      assert sniff_upload_type(content, name) == expected
  def test_sniff_rejects_svg_renamed_png() -> None:
      assert sniff_upload_type(b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "x.png") is None
      assert sniff_upload_type(b"<html><script>x</script></html>", "x.html") is None   # .html is never text
      # An HTML document named .txt IS valid UTF-8 text: accepted as text/plain on purpose. It is neutralised on
      # download (text/plain + nosniff + CSP sandbox + attachment) — asserted in Task 7 test_attachment_download_headers.
      assert sniff_upload_type(b"<html><script>x</script></html>", "x.txt") == "text/plain"
  ```
  ```python
  def test_sniff_rejects_binary_txt_and_exe() -> None:
      assert sniff_upload_type(b"MZ\x90\x00\x03", "setup.exe") is None
      assert sniff_upload_type(b"abc\x00def", "x.txt") is None
      assert sniff_upload_type(b"\xff\xfe\xfa", "x.txt") is None
  def test_safe_upload_filename() -> None:
      assert safe_upload_filename("../../etc/passwd", "text/plain") == "passwd.txt"
      assert safe_upload_filename("C:\\Users\\x\\Bild.PNG", "image/png") == "Bild.png"
      assert safe_upload_filename("rech\u202enung.exe.pdf", "application/pdf") == "rechnung.exe.pdf"
      assert safe_upload_filename("", "image/jpeg") == "datei.jpg"
      assert len(safe_upload_filename("a" * 300 + ".pdf", "application/pdf")) == 120
  def test_check_upload_policy() -> None:
      png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
      assert check_upload(png, "x.png", UploadPolicy()) == ("x.png", "image/png")
      for policy, code in ((UploadPolicy(enabled=False), "disabled"),
                           (UploadPolicy(types=["application/pdf"]), "type")):
          with pytest.raises(UploadRejected) as e:
              check_upload(png, "x.png", policy)
          assert e.value.code == code
      with pytest.raises(UploadRejected) as e:
          check_upload(b"%PDF-" + b"0" * (2 * 1024 * 1024), "x.pdf", UploadPolicy(max_bytes=1024 * 1024))
      assert e.value.code == "size"
  def test_visitor_token_round_trip() -> None:
      sid = new_session_id()
      token, digest = issue_visitor_token(sid)
      parsed = parse_visitor_token(token)
      assert parsed is not None and parsed[0] == sid and token_matches(parsed[1], digest)
      assert not token_matches(parsed[1], "0" * 64) and not token_matches(parsed[1], "")
      for bad in (None, "", "abc", f"{sid}", f"{sid}.", "Z" * 32 + ".x" * 20, f"{sid}.{'a' * 200}"):
          assert parse_visitor_token(bad) is None
  def test_hash_ip_is_keyed_and_short() -> None:
      a = hash_ip("192.0.2.1", "k1")
      assert len(a) == 32 and a != hash_ip("192.0.2.1", "k2") and "192" not in a
  ```
  `own_origin`: `Settings(public_base_url="https://helpdesk.example.org/sub")` → `"https://helpdesk.example.org"`; without it a Starlette `Request` for `http://testserver/x` → `"http://testserver"`.
- [ ] **Step 2: Run to verify failure** → FAIL (ImportError).
- [ ] **Step 3: Implement** C3. `normalize_origin`: `urllib.parse.urlsplit`; `host.encode("idna").decode("ascii")` per label; final `re.fullmatch(r"https?://[a-z0-9.-]+(:[0-9]{1,5})?", out)` or `OriginError`. Bidi/control filtering via one compiled regex shared by `text.py` and `uploads.py` (`text.CONTROL_CHARS`).
- [ ] **Step 4: Run** → PASS; lint gates; commit (`feat(webchat): origin, CSP, message, upload and token rules`).

### Task 4: Shared fake Redis, chat rate limiter, ChatHub fan-out (backend)

**Files:**
- Create: `backend/tests/_fake_redis.py`, `backend/src/tiqora/webchat/ratelimit.py`, `backend/src/tiqora/webchat/hub.py` (C4)
- Modify: `backend/src/tiqora/config.py` (`chat_max_streams`, `webchat_dist_dir` of C9), `backend/src/tiqora/api/app.py` (lifespan: `app.state.chat_hub = None` at start; on shutdown `await app.state.chat_hub.aclose()` when set)
- Test: `backend/tests/test_webchat_ratelimit.py`, `backend/tests/test_webchat_hub.py`

**Interfaces:**
- Produces: C4 `ChatRateLimiter`, `RateLimited`, `ChatBackendUnavailable`, `ChatHub`, `StreamLimitReached`, `chat_channel`, `publish_chat_wake`, `publish_chat_typing`, `get_chat_hub`; test helper:
  ```python
  class FakeRedis:                     # backend/tests/_fake_redis.py
      published: list[tuple[str, str]]
      fail: bool                        # True → every command raises ConnectionError (for fail-closed tests)
      async def get/set(key, value, ex=None, nx=False)/delete(*keys)/exists(*keys)/incr/expire/ttl(key)
      async def mget(*keys); async def publish(channel, message); def pubsub() -> FakePubSub
      def advance(seconds: float) -> None   # moves the fake clock for TTL expiry
  class FakePubSub:                    # subscribe/psubscribe/unsubscribe/punsubscribe/get_message(ignore_subscribe_messages, timeout)/aclose
      # psubscribe patterns use fnmatch; pmessage dicts: {"type": "pmessage", "pattern", "channel", "data"}
  ```

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_fixed_window_limit_then_retry_after() -> None:
      r = FakeRedis(); lim = ChatRateLimiter(r)
      for _ in range(3):
          await lim.hit("message", "s1", limit=3, window_seconds=60)
      with pytest.raises(RateLimited) as e:
          await lim.hit("message", "s1", limit=3, window_seconds=60)
      assert e.value.scope == "message" and 1 <= e.value.retry_after <= 60
      await lim.hit("message", "s2", limit=3, window_seconds=60)       # other subject unaffected
      r.advance(61)
      await lim.hit("message", "s1", limit=3, window_seconds=60)       # window rolled over
  async def test_redis_down_fails_closed() -> None:
      r = FakeRedis(); r.fail = True
      with pytest.raises(ChatBackendUnavailable):
          await ChatRateLimiter(r).hit("session_create", "ip", limit=5, window_seconds=600)
  async def test_try_hit_once_per_window() -> None:
      r = FakeRedis(); lim = ChatRateLimiter(r)
      assert await lim.try_hit("typing", "s1", window_seconds=2) is True
      assert await lim.try_hit("typing", "s1", window_seconds=2) is False
      r.advance(3)
      assert await lim.try_hit("typing", "s1", window_seconds=2) is True
  async def test_hub_routes_by_session_with_one_subscription() -> None:
      r = FakeRedis(); hub = ChatHub(r, max_streams=10)
      qa = await hub.subscribe("a" * 32); qb = await hub.subscribe("b" * 32)
      await publish_chat_wake(r, "a" * 32)
      assert (await asyncio.wait_for(qa.get(), 2)) == {"type": "wake"}
      assert qb.empty()
      assert r.pattern_subscription_count() == 1                      # one psubscribe for the whole process
      hub.unsubscribe("a" * 32, qa); await hub.aclose()
  async def test_hub_limits() -> None:
      hub = ChatHub(FakeRedis(), max_streams=2, max_per_session=1)
      await hub.subscribe("a" * 32)
      with pytest.raises(StreamLimitReached):
          await hub.subscribe("a" * 32)                                # per session
      await hub.subscribe("b" * 32)
      with pytest.raises(StreamLimitReached):
          await hub.subscribe("c" * 32)                                # total
      assert hub.active_streams == 2
  async def test_hub_full_queue_drops_oldest() -> None:
      r = FakeRedis(); hub = ChatHub(r, max_streams=10); q = await hub.subscribe("a" * 32)
      for _ in range(60):
          await publish_chat_typing(r, "a" * 32)
      await asyncio.sleep(0.05)
      assert q.qsize() == 50
  async def test_publish_helpers_never_raise() -> None:
      r = FakeRedis(); r.fail = True
      await publish_chat_wake(r, "a" * 32); await publish_chat_typing(r, "a" * 32)   # no exception
  ```
- [ ] **Step 2: Run to verify failure** → FAIL (ImportError).
- [ ] **Step 3: Implement** C4 (ratelimit + hub), `FakeRedis` (`pattern_subscription_count()` test helper counts active `psubscribe` patterns), config fields, lifespan hook, `get_chat_hub`.
- [ ] **Step 4: Run** → PASS (also `tests/test_events_sse.py` still green); lint gates; commit (`feat(webchat): rate limiter and per-process chat fan-out`).

### Task 5: Agent availability, online markers, business hours, agent bus events (backend)

**Files:**
- Create: `backend/src/tiqora/webchat/availability.py` (C6 availability part)
- Modify: `backend/src/tiqora/events/pubsub.py` (`CHAT_EVENT_TYPES`, `publish_chat_event`, docstring), `backend/src/tiqora/api/v1/events.py` (`_QUEUE_SCOPED_EVENT_TYPES |= CHAT_EVENT_TYPES`; `on_alive` per C4)
- Test: `backend/tests/test_webchat_availability.py` (both dialects, band 48450–48489), `backend/tests/test_events_sse.py` (two new tests)

**Interfaces:**
- Consumes: `calendar_config`, `working_seconds_between`, `PermissionEngine.check`, `ChatTopic`, `FakeRedis`.
- Produces: C6 availability names; `publish_chat_event`; `CHAT_EVENT_TYPES`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_agent_state_defaults_and_upsert(url_fixture, request) -> None:
      st = await get_agent_state(db, 48451)
      assert st == AgentChatState(48451, False, (), 3)
      st = await set_agent_state(db, 48451, available=True, topics=["48460:support"], max_concurrent=2, now=NOW)
      await db.commit()
      assert (await get_agent_state(db, 48451)).topics == ("48460:support",)
  async def test_eligible_agents_need_available_online_rw_capacity_topic(url_fixture, request) -> None:
      # queue 48452 rw for agents 48453 (available, online), 48454 (available, NOT online),
      # 48455 (online, available, but only "ro"), 48456 (online, available, topics=["48460:billing"]),
      # 48457 (online, available, max_concurrent=1 and one active session)
      r = FakeRedis()
      for uid in (48453, 48455, 48456, 48457):
          await mark_agent_online(r, uid)
      ids = await eligible_agent_ids(db, r, config_id=48460, topic=ChatTopic(key="support", label="S", queue_id=48452))
      assert ids == [48453]
  async def test_business_hours(monkeypatch) -> None:
      # monkeypatch tiqora.webchat.availability.calendar_config to return Mon-Fri 8..17 Europe/Berlin
      monday_10 = datetime(2026, 10, 12, 8, 0)      # 10:00 local (naive UTC input)
      sunday_10 = datetime(2026, 10, 11, 8, 0)
      assert await within_business_hours(sysconfig, "", monday_10) is True
      assert await within_business_hours(sysconfig, "", sunday_10) is False
      assert await within_business_hours(sysconfig, None, sunday_10) is True       # no calendar = always
      # calendar with no working hours at all → True (deviation 16)
  async def test_config_availability_is_cached(url_fixture, request) -> None:
      # first call computes {"support": True}; flip agent available=False; second call within 10 s still True;
      # r.advance(11) → False
  ```
  `test_events_sse.py`:
  ```python
  def test_chat_events_are_queue_scoped() -> None:
      raw = json.dumps({"type": "chat_waiting", "session_id": "a" * 32, "ticket_id": 1, "queue_id": 7})
      assert _should_forward(raw, {7}) is True and _should_forward(raw, {8}) is False
      assert _should_forward(json.dumps({"type": "chat_message", "ticket_id": 1}), {7}) is False   # no queue_id
  async def test_event_stream_marks_agent_online() -> None:
      calls: list[int] = []
      async def alive() -> None: calls.append(1)
      gen = _event_stream(cast(Any, _FakeRequest()), cast(Any, _FakeRedis()), set(), on_alive=alive)
      # with HEARTBEAT_INTERVAL_SECONDS patched to 0.05 read one heartbeat → calls == [1, …] (≥ 1 call at connect)
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement.** Online check uses `mget` of `tiqora:agent_online:<uid>` keys (one round-trip). `eligible_agent_ids` loads available rows, filters in Python, sorts ids ascending. `config_availability`: `online = within_business_hours(...)`; per topic `bool(eligible_agent_ids(...))`.
- [ ] **Step 4: Run** → PASS; lint gates; commit (`feat(webchat): agent availability, online markers, chat events on the agent bus`).

### Task 6: Session service — create, messages, uploads, offline form, contact, end; post-commit notifications (backend)

**Files:**
- Create: `backend/src/tiqora/webchat/prechat.py`, `backend/src/tiqora/webchat/notify.py`
- Modify: `backend/src/tiqora/webchat/sessions.py` (C5), `backend/src/tiqora/webchat/store.py` (`get_chat_session`, `allocate_seq`, `insert_message`, `find_by_client_id`, `load_messages_since`, `live_session_for_ticket`)
- Test: `backend/tests/test_webchat_prechat.py` (pure), `backend/tests/test_webchat_sessions.py` (both dialects, band 48490–48549)

**Interfaces:**
- Consumes: Tasks 1–5; `create_ticket`, `add_article`, `set_customer`, `TicketIn`, `ArticleIn`, `ensure_channel_row`, `load_ticket_dynamic_field`, `DynamicFieldValueError`.
- Produces:
  ```python
  # prechat.py
  @dataclass(frozen=True, slots=True)
  class PrechatResult:
      name: str | None
      email: str | None
      df_values: dict[str, list[str]]
      extra_lines: list[tuple[str, str]]          # (label, value) for the article block
  class PrechatInvalid(Exception):
      errors: dict[str, str]                       # field key → "required" | "email" | "option" | "too_long" | "type"
  EMAIL_RE: Final = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s.]{2,}$")
  def validate_prechat(fields: Sequence[PrechatField], values: Mapping[str, str | bool],
                       *, require_email: bool = False) -> PrechatResult
      # unknown keys ignored; text ≤ 200, textarea ≤ 2000, email ≤ 250 + EMAIL_RE; select value must be an option value;
      # checkbox must be bool; strings go through sanitize_message-style control stripping (no length error for empty
      # optional fields); require_email (offline form) → "email" required even if no field maps to it
  async def resolve_chat_customer(db, *, email: str | None, default_customer_user: str | None) -> tuple[str | None, str | None]
  # store.py
  async def allocate_seq(db: AsyncSession, session_id: str) -> int
      # UPDATE tiqora_chat_session SET last_seq = last_seq + 1 WHERE id = :sid; SELECT last_seq — the UPDATE's row lock
      # serialises concurrent writers until commit on both dialects (gapless, no duplicates)
  async def insert_message(db, *, chat: TiqoraChatSession, sender: str, body: str, user_id: int | None = None,
                           article_id: int | None = None, attachments: Sequence[dict[str, Any]] = (),
                           meta: dict[str, Any] | None = None, client_id: str | None = None,
                           now: datetime) -> TiqoraChatMessage
  async def find_by_client_id(db, session_id: str, client_id: str) -> TiqoraChatMessage | None
  async def load_messages_since(db, session_id: str, after_seq: int, limit: int = 200) -> list[TiqoraChatMessage]
  async def get_chat_session(db, session_id: str, *, for_update: bool = False) -> TiqoraChatSession | None
  async def live_session_for_ticket(db, ticket_id: int) -> TiqoraChatSession | None   # newest with status in LIVE_STATUSES
  ```
  plus C4 `notify.py` and C5 `sessions.py`.

- [ ] **Step 1: Write the failing tests.** `test_webchat_prechat.py`:
  ```python
  FIELDS = [PrechatField(key="name", kind="text", label="Name", maps_to="name"),
            PrechatField(key="email", kind="email", label="E-Mail", required=True, maps_to="email"),
            PrechatField(key="contract", kind="select", label="Vertrag",
                         options=[PrechatOption(value="a", label="A")], maps_to="dynamic_field", dynamic_field="Contract"),
            PrechatField(key="newsletter", kind="checkbox", label="Newsletter")]
  def test_valid_prechat() -> None:
      r = validate_prechat(FIELDS, {"name": "Erika Muster", "email": "erika@example.org", "contract": "a",
                                    "newsletter": True, "unknown": "x"})
      assert r.name == "Erika Muster" and r.email == "erika@example.org"
      assert r.df_values == {"Contract": ["a"]} and r.extra_lines == [("Newsletter", "Ja")]
  def test_invalid_prechat_collects_all_errors() -> None:
      with pytest.raises(PrechatInvalid) as e:
          validate_prechat(FIELDS, {"email": "nope", "contract": "zzz", "newsletter": "yes", "name": "x" * 201})
      assert e.value.errors == {"email": "email", "contract": "option", "newsletter": "type", "name": "too_long"}
  def test_offline_requires_email() -> None:
      with pytest.raises(PrechatInvalid) as e:
          validate_prechat([], {}, require_email=True)
      assert e.value.errors == {"email": "required"}
  ```
  `test_webchat_sessions.py` (seed: queue 48491 rw agent 48492 online+available, config 48495 with `default_customer_user=None`, DF `Contract` Dropdown seeded with id 48496; `enable_webchat`; FakeRedis):
  ```python
  async def test_create_session_creates_ticket_article_and_first_message(url_fixture, request) -> None:
      chat, token, first = await create_session(db, factory, sysconfig, r, config=cfg, values=values,
          payload=ChatSessionCreateIn(key=cfg.public_key, origin="https://www.example.org", visitor_id="v" * 22,
              prechat={"name": "Erika", "email": "erika@example.org", "contract": "a"},
              message="Mein Router blinkt rot", client_id="c-1"), ctx=CTX, now=NOW)
      await db.commit()
      assert chat.status == "waiting" and chat.queue_id == 48491 and chat.last_seq == 1 and first.seq == 1
      assert token.startswith(chat.id + ".") and chat.token_hash != "" and token.split(".", 1)[1] not in chat.token_hash
      t = ticket row → title == "Chat: Mein Router blinkt rot", customer_user_id == "erika@example.org", queue_id 48491
      a = only article → channel name "WebChat", sender customer, body startswith "Mein Router blinkt rot",
          contains "— Angaben aus dem Vorab-Formular —" only when extra lines exist (here: none → absent)
      assert DF Contract value for the ticket == ["a"]
      assert ticket_history types contain "NewTicket" and "WebRequestCustomer"
      assert db.info["tiqora.webchat.agent_events"][0]["type"] == "chat_waiting"   (before flush)
  async def test_create_session_offline_refused(url_fixture, request) -> None:
      # agent 48492 marked unavailable → ChatError code "offline", 409; no ticket created (count unchanged)
  async def test_prechat_invalid_creates_nothing(url_fixture, request) -> None:
      # prechat email "nope" → ChatError("invalid_prechat").errors == {"email": "email"}; ticket count unchanged
  async def test_follow_up_message_no_notify_and_seq(url_fixture, request) -> None:
      # post_visitor_message → seq 2, article history "FollowUp", ArticleIn.no_agent_notify True
      # (assert no tiqora_event_outbox row with event_type "NotificationFollowUp" for the ticket)
  async def test_client_id_retry_returns_same_message(url_fixture, request) -> None:
      m1 = await post_visitor_message(db, sysconfig, chat=chat, body="Hallo", client_id="c-2", now=NOW)
      m2 = await post_visitor_message(db, sysconfig, chat=chat, body="Hallo", client_id="c-2", now=NOW)
      assert m1.id == m2.id and article count for the ticket increased by exactly 1
  async def test_concurrent_messages_get_gapless_seq(url_fixture, request) -> None:
      # 10 tasks, each own session from factory: post_visitor_message + commit concurrently (asyncio.gather)
      # → sorted seqs == list(range(2, 12)); last_seq == 11
  async def test_closed_session_refuses_messages(url_fixture, request) -> None:
      # end_by_visitor → status "ended", end_reason "visitor", system message code "visitor_left";
      # post_visitor_message → ChatError code "session_closed" (409)
  async def test_attachment_message(url_fixture, request) -> None:
      png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
      m = await post_visitor_attachment(db, sysconfig, chat=chat, values=values, filename="../bild.png",
                                        content=png, caption=None, client_id=None, now=NOW)
      assert json.loads(m.attachments) == [{"index": 0, "filename": "bild.png", "content_type": "image/png", "size": 72}]
      # article has one article_data_mime_attachment row with filename "bild.png"; m.body == "bild.png"
      # svg bytes → ChatError code "upload_rejected", http_status 422, errors {"file": "type"};
      # oversize → http_status 413, errors {"file": "size"}
  async def test_offline_form_creates_ended_session_and_ticket(url_fixture, request) -> None:
      # submit_offline(... email "max@example.org", message "Bitte um Rückruf") → one ticket, customer_user_id email,
      # article channel WebChat; session row status "ended", end_reason "offline_form", no agent event
  async def test_set_contact_after_no_agent(url_fixture, request) -> None:
      # anonymous session (no email): status ended, end_reason "no_agent", ended_at NOW
      # set_contact(email="erika@example.org") → session.visitor_email set; ticket customer_user_id now the email
      #   (history CustomerUpdate written by set_customer); 2 h later → ChatError("contact_not_allowed")
  async def test_flush_publishes_after_commit(url_fixture, request) -> None:
      queue_wakeup(db, chat.id); queue_agent_event(db, {...chat_message payload...}); await db.commit()
      await flush_chat_notifications(db, r)
      assert (chat_channel(chat.id), '{"type": "wake"}') in r.published
      assert any('"chat_message"' in m for ch, m in r.published if ch == "tiqora:events")
      assert "tiqora.webchat.wakeups" not in db.info
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** C5 + the Interfaces above. Duplicate `client_id` race: `find_by_client_id` first; the insert runs in a SAVEPOINT (`async with db.begin_nested()`), and an `IntegrityError` on `ux_tiqora_chat_message_client` rolls back the savepoint (incl. the article — so add the article inside the same savepoint) and returns the re-selected row. DF writes: `create_ticket` with `TicketIn.dynamic_fields`; a `DynamicFieldValueError` for one field (option removed from the DF since the admin saved) moves that value into `extra_lines` instead of failing the visitor (retry `create_ticket` is not needed: validate each DF value first with `normalize_df_values` from `tiqora.domain.dynamic_field_values`). The visitor message article body for an attachment-only message is the file name. Every service function calls `queue_wakeup`/`queue_agent_event`; none publishes directly.
- [ ] **Step 4: Run** → PASS; lint gates; commit (`feat(webchat): chat sessions create tickets and articles with gapless message order`).

### Task 7: Public chat API `/api/chat/v1` (backend)

**Files:**
- Create: `backend/src/tiqora/webchat/schemas.py` (visitor part of C8), `backend/src/tiqora/webchat/public_api.py`
- Modify: `backend/src/tiqora/api/app.py` (`app.include_router(webchat_public_router, prefix="/api/chat/v1")`; `metrics_middleware` labels `/api/chat/v1/...` paths with 32-hex segments collapsed to `{id}` and digit segments to `{n}`), `backend/src/tiqora/api/spa.py` (`_RESERVED_PREFIXES` += `"/chat/"`)
- Regenerate: `packages/api-client/openapi.json`, `docs/api/openapi.json`, `packages/api-client/src/schema.d.ts`
- Test: `backend/tests/test_webchat_public_api.py` (both dialects, band 48550–48599)

**Interfaces:**
- Consumes: Tasks 3–6; `client_ip`, `read_upload_within_limit`, `safe_attachment_response`, `channel_enabled`, `get_redis`.
- Produces: the public endpoints of C8 (the stream comes in Task 8); dependency helpers in `public_api.py`:
  ```python
  async def require_same_origin(request: Request, settings: AppSettings) -> None   # foreign Origin → 403 "cross_origin"
  async def visitor_session(session_id: str, request: Request, db: DbSession) -> TiqoraChatSession
      # parse X-Chat-Token; sid must equal path id; token_matches; purged → same 404 {"detail": "not_found"}
  def visitor_context(request: Request, settings: Settings) -> VisitorContext   # hash_ip(client_ip(request), settings.secret_key)
  def message_out(m: TiqoraChatMessage) -> ChatMessageOut                     # the ONLY visitor serializer
  def chat_error_response(exc: ChatError | RateLimited | ChatBackendUnavailable) -> JSONResponse
      # ChatError → status + {"detail": code[, "errors": …]}; RateLimited → 429 + Retry-After + {"detail": "rate_limited"};
      # ChatBackendUnavailable / StreamLimitReached → 503 + Retry-After 30 + {"detail": "chat_unavailable"}
  ```

- [ ] **Step 1: Write the failing tests** (httpx `ASGITransport`, app from `create_app(Settings(environment="test", …))` with `get_redis` → `FakeRedis`, DB url from the fixture; seeded config 48551 with origins `["https://www.example.org"]`, agent 48552 online+available on queue 48553):
  ```python
  async def test_config_ok_and_leaks_nothing(...) -> None:
      res = await client.get("/api/chat/v1/config", params={"k": KEY, "origin": "https://www.example.org"})
      assert res.status_code == 200
      body = res.json()
      assert body["online"] is True and body["topics"] == [{"key": "support", "label": "Support", "online": True}]
      assert "queue_id" not in res.text and "48553" not in res.text and "agent" not in res.text.lower()
  async def test_config_refuses_foreign_origin_param_and_disabled_channel(...) -> None:
      assert (await client.get("/api/chat/v1/config", params={"k": KEY, "origin": "https://evil.example"})).status_code == 403
      # channel.webchat.enabled = "0" → 404; unknown key → 404; invalid config → 404
  async def test_cross_origin_post_refused(...) -> None:
      res = await client.post("/api/chat/v1/sessions", json=CREATE, headers={"Origin": "https://evil.example"})
      assert res.status_code == 403 and res.json() == {"detail": "cross_origin"}
      assert "access-control-allow-origin" not in {k.lower() for k in res.headers}
      ok = await client.post("/api/chat/v1/sessions", json=CREATE, headers={"Origin": "http://testserver"})
      assert ok.status_code == 201
  async def test_session_flow_and_shapes(...) -> None:
      created = (await client.post("/api/chat/v1/sessions", json=CREATE)).json()
      assert set(created) == {"session_id", "token", "status", "message"}
      assert set(created["message"]) == {"seq", "sender", "body", "code", "params", "agent_name", "created",
                                         "attachments", "client_id"}
      h = {"X-Chat-Token": created["token"]}
      sid = created["session_id"]
      assert (await client.post(f"/api/chat/v1/sessions/{sid}/messages", json={"body": "Noch da?"}, headers=h)).json()["seq"] == 2
      msgs = (await client.get(f"/api/chat/v1/sessions/{sid}/messages", params={"after": 1}, headers=h)).json()
      assert [m["body"] for m in msgs] == ["Noch da?"]
      s = (await client.get(f"/api/chat/v1/sessions/{sid}", headers=h)).json()
      assert s == {"session_id": sid, "status": "waiting", "agent_name": None, "topic_label": "Support",
                   "can_send": True, "needs_contact": False, "last_seq": 2}
  async def test_wrong_token_and_unknown_session_look_identical(...) -> None:
      # other session's token, garbled token, no token, unknown sid → identical status 404 and identical JSON body
  async def test_rate_limits(...) -> None:
      # rate_limits.sessions_per_ip_10min = 2 → third POST /sessions → 429 with Retry-After header ≥ 1
      # messages_per_session_minute = 5 → 6th message → 429 {"detail": "rate_limited"}
  async def test_redis_down_is_503(...) -> None:
      fake.fail = True → POST /sessions → 503, Retry-After "30"; GET /config → 503
  async def test_upload_svg_rejected_and_png_ok(...) -> None:
      svg = ("x.png", b"<svg onload='alert(1)'/>", "image/png")
      assert (await client.post(f".../attachments", files={"file": svg}, headers=h)).status_code == 422
      png = ("bild.png", PNG_BYTES, "image/png")
      out = (await client.post(f".../attachments", files={"file": png}, data={"client_id": "u1"}, headers=h)).json()
      assert out["attachments"][0]["filename"] == "bild.png"
  async def test_upload_too_large_413(...) -> None:
      # upload_policy.max_bytes = 1 MiB; 2 MiB pdf → 413 {"detail": "upload_rejected", "errors": {"file": "size"}}
  async def test_attachment_download_headers(...) -> None:
      # text upload "<html><script>x</script></html>" as "n.txt" → download:
      # content-type "text/plain", "x-content-type-options: nosniff", "content-security-policy: sandbox",
      # content-disposition starts "attachment"
  async def test_attachment_of_other_session_404(...) -> None:
      # session B's token + session A's seq/index → 404; seq of a message without attachments → 404
  async def test_offline_form(...) -> None:
      # all agents offline → GET /config online False; POST /offline {email, message} → 201 {};
      # missing email → 422 {"detail": "invalid_prechat", "errors": {"email": "required"}}; 4th within the hour → 429
  async def test_end_and_contact(...) -> None:
      # POST /end → 204; GET session → status "ended", can_send False; POST /messages → 409 {"detail": "session_closed"}
  async def test_response_never_contains_ticket_number(...) -> None:
      # collect every response text of the flow above; assert the ticket's tn and ticket_id never occur
  async def test_same_origin_check_uses_public_base_url(...) -> None:
      # Settings(public_base_url="https://helpdesk.example.org"); request to http://testserver (TLS ends at the proxy)
      # with Origin https://helpdesk.example.org → 201; Origin http://testserver → 403 (not the public origin)
  async def test_per_ip_limit_keys_on_client_not_proxy(...) -> None:
      # TIQORA_TRUSTED_PROXIES = the test client's peer address; two visitors with X-Forwarded-For 198.51.100.7 and
      # 198.51.100.8, sessions_per_ip_10min = 1 → both get 201 (separate buckets); a second session from .7 → 429;
      # FakeRedis keys contain neither "198.51.100" nor the proxy address (only hash_ip values)
  def test_production_warns_without_public_base_url(caplog) -> None:
      # own_origin() with Settings(environment="production", public_base_url="") logs "webchat_public_base_url_missing" once
  async def test_metrics_label_collapses_session_ids(...) -> None:
      # after two different sessions, REQUEST_COUNT labels contain "/api/chat/v1/sessions/{id}/messages" and no raw sid
  ```
- [ ] **Step 2: Run to verify failure** → FAIL (404 for every route).
- [ ] **Step 3: Implement** the router. Own origin = origin of `TIQORA_PUBLIC_BASE_URL` (required behind a TLS-terminating proxy; the request-derived fallback is for dev and logs `webchat_public_base_url_missing` once in production). Client IP = `client_ip(request)` (honours `TIQORA_TRUSTED_PROXIES`; uvicorn `TIQORA_FORWARDED_ALLOW_IPS` must also be set — both documented in Task 22). Each handler: `require_same_origin` dependency; `channel_enabled`; rate limits (C8 table) via `ChatRateLimiter(redis)`; service call inside `async with db.begin():`; after the block `await flush_chat_notifications(db, redis)`. Multipart upload: `read_upload_within_limit(file, values.upload_policy.max_bytes + 1)` then `check_upload`. Typing: `try_hit("typing", sid, window_seconds=2)` → when True `SET tiqora:chat:vtyping:<sid> 1 EX 6` + `queue_agent_event(chat_typing)` + flush. Exceptions are mapped in one `except (ChatError, RateLimited, ChatBackendUnavailable)` per handler via `chat_error_response`. Regenerate openapi (Global Constraints).
- [ ] **Step 4: Run** → PASS (+ `tests/test_spa_serving.py`); lint gates; commit (`feat(webchat): public chat API with origin, token and rate-limit checks`) including the regenerated openapi files.

### Task 8: Visitor SSE stream with replay (backend)

**Files:**
- Create: `backend/src/tiqora/webchat/stream.py`
- Modify: `backend/src/tiqora/webchat/public_api.py` (`GET /sessions/{sid}/stream`)
- Test: `backend/tests/test_webchat_stream.py` (both dialects, band 48600–48619)

**Interfaces:**
- Consumes: `ChatHub`, `load_messages_since`, `get_chat_session`, `message_out`.
- Produces:
  ```python
  def sse_event(*, event: str, data: dict[str, Any], event_id: int | None = None) -> bytes
  async def visitor_event_stream(request: Request, *, hub: ChatHub, session_factory: async_sessionmaker[AsyncSession],
                                 redis_client: Any, session_id: str, after_seq: int,
                                 heartbeat_seconds: float = STREAM_HEARTBEAT_SECONDS) -> AsyncGenerator[bytes, None]
  STREAM_HEARTBEAT_SECONDS: Final = 25    # own constant; same value as the agent stream, no import from tiqora.api
  ```

- [ ] **Step 1: Write the failing tests** (drive the generator directly like `test_events_sse.py` does — `ASGITransport` buffers SSE):
  ```python
  async def test_replay_after_last_event_id(...) -> None:
      # session with messages seq 1..4; gen = visitor_event_stream(..., after_seq=2)
      chunks = [await anext(gen) for _ in range(3)]
      assert chunks[0].startswith(b"id: 3\nevent: message\n") and chunks[1].startswith(b"id: 4\nevent: message\n")
      assert chunks[2].startswith(b"event: session\n") and b'"status": "waiting"' in chunks[2]
  async def test_live_message_after_wake(...) -> None:
      # start gen (after_seq=4), read the session event; then insert seq 5 + commit, publish_chat_wake
      # → next chunk starts b"id: 5\nevent: message\n" within 2 s
  async def test_wake_published_before_commit_is_still_delivered(...) -> None:
      # heartbeat 0.2 s; publish wake, THEN insert seq 6 + commit → seq 6 arrives after the next heartbeat re-read;
      # assert a b": hb\n\n" chunk can precede it and that seq 6 arrives within 1 s
  async def test_typing_passes_through_without_id(...) -> None:
      await publish_chat_typing(r, sid) → chunk == b'event: typing\ndata: {"who": "agent"}\n\n'
  async def test_session_event_on_accept_and_end_closes(...) -> None:
      # status → active with agent first name "Max" + wake → chunk event session with "agent_name": "Max"
      # status → ended + wake → event end {"reason": "agent"}; the generator then finishes (StopAsyncIteration)
  async def test_internal_and_foreign_rows_never_emitted(...) -> None:
      # a tiqora_chat_message row of ANOTHER session with seq 7 → never appears on this stream
  async def test_stream_marks_visitor_seen(...) -> None:
      # after connect r.get("tiqora:chat:seen:<sid>") == "1" with TTL ≤ 90
  async def test_stream_endpoint_limits(...) -> None:
      # via the router with hub max_per_session=1 already used → 503 + Retry-After 30; wrong token → 404
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** C7 exactly (subscribe first, then read; `asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)`; on `TimeoutError` yield `b": hb\n\n"` and re-read; check `request.is_disconnected()` each loop; `finally: hub.unsubscribe`). Endpoint: `Last-Event-ID` header (int ≥ 0) wins over `after`; `StreamingResponse(media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})`; rate limit `stream_connect` 30/min per session.
- [ ] **Step 4: Run** → PASS; lint gates; commit (`feat(webchat): visitor event stream with replay from the message log`).

### Task 9: Agent API — availability, topics, inbox, first-accept-wins, end (backend)

**Files:**
- Create: `backend/src/tiqora/webchat/agent.py` (accept/end/inbox), `backend/src/tiqora/api/v1/chat.py`
- Modify: `backend/src/tiqora/webchat/schemas.py` (agent models of C8), `backend/src/tiqora/api/v1/__init__.py` (`include_router(chat.router)`)
- Regenerate openapi files
- Test: `backend/tests/test_webchat_agent_api.py` (both dialects, band 48620–48669)

**Interfaces:**
- Consumes: Tasks 5–6; `lock_ticket`, `assign_owner`, `TicketWriteService.change_state`, `PermissionEngine`.
- Produces: C6 `accept_session`, `end_session_by_agent`, `list_inbox`; endpoints of C8 "Agent" (except the ticket chat info and typing, Task 10).

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_agent_state_put_get(...) -> None:
      # PUT {"available": true, "topics": ["48621:support"], "max_concurrent": 2} → GET returns same + active_count 0
      # PUT topics with a ref of a config the agent has no rw on → 422 {"detail": "unknown_topic"}
  async def test_topics_only_rw_queues(...) -> None:
      # config 48621 topics support(queue 48622 rw) + billing(queue 48623 ro only) → GET /chat/topics == [support ref]
  async def test_inbox_lists_waiting_in_rw_queues_and_mine(...) -> None:
      # waiting session in queue 48622 and one in 48623 → waiting has only the first, with tn/title from ticket;
      # after accept → waiting [] and mine [the session] with status "active"
  async def test_first_accept_wins_both_dialects(...) -> None:
      # two agents 48624/48625 (rw, capacity 3), one waiting session; asyncio.gather of two accept calls via two
      # separate DB sessions (service level, each commits) → exactly one returns, the other raises ChatError
      # "already_taken"; session.assigned_user_id == the winner; exactly one "agent_joined" system message
  async def test_accept_at_capacity_conflict(...) -> None:
      # max_concurrent 1 + one active session → POST accept → 409 {"detail": "at_capacity"}; session stays waiting
  async def test_accept_writes_lock_then_owner_history(...) -> None:
      # ticket unlocked, owner 1 → after accept: ticket_lock_id == 2 (lock), user_id == agent;
      # ticket_history types appended in order ["Lock", "OwnerUpdate"]
  async def test_accept_forbidden_without_rw(...) -> None: 403; ro-only agent
  async def test_end_by_agent_optionally_closes(...) -> None:
      # POST end {"close_ticket": true} → session ended/agent, system message "chat_ended",
      # ticket state "closed successful", history StateUpdate; second call → 204 (idempotent)
  async def test_events_published_after_commit(...) -> None:
      # accept → FakeRedis.published has ("tiqora:events", chat_assigned with user_id) and a wake on the chat channel
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** `PUT /chat/agent-state` validates every topic ref against the agent's `/chat/topics` (unknown → 422 `{"detail": "unknown_topic"}`); C6 accept (exact order), end (closed state = `ticket_state` named `closed successful`; when missing, the first valid state whose type is `closed`), inbox (load sessions, then `SELECT id, tn, title FROM ticket WHERE id IN (...)`; `rw` per queue via `PermissionEngine.check`, cached per request). Map `ChatError` → HTTP in the router.
- [ ] **Step 4: Run** → PASS; regenerate openapi; lint gates; commit (`feat(webchat): agent availability, inbox and first-accept-wins`).

### Task 10: Agent replies into the chat, ticket chat info, typing (backend)

**Files:**
- Modify: `backend/src/tiqora/webchat/agent.py` (`deliver_agent_chat_reply`, `agent_typing`, `ChatNotLive`), `backend/src/tiqora/api/v1/chat.py` (`GET /tickets/{id}/chat`, `POST /tickets/{id}/chat/typing`)
- Modify: `backend/src/tiqora/domain/ticket_write_service.py` (`TicketWriteService.add_article`: branch `channel == "webchat" and sender_type == "agent" and is_visible_for_customer` → `deliver_agent_chat_reply`; `ChatNotLive` re-raised as `InvalidInput("chat_not_live")`)
- Modify: `backend/src/tiqora/api/v1/tickets.py` (`_telegram_extras`: attachments allowed for `channel == "webchat"` agent replies, buttons/quote → 422; `create_article` takes `redis_client: RedisDep` and calls `flush_chat_notifications(session, redis_client)` after the write block; `InvalidInput("chat_not_live")` → 409 `{"detail": "chat_not_live"}`)
- Regenerate openapi files
- Test: `backend/tests/test_webchat_agent_reply.py` (both dialects, band 48670–48699)

**Interfaces:**
- Consumes: Tasks 6–9; `chat_body_from_article`, `live_session_for_ticket`, `accept_session`.
- Produces: `deliver_agent_chat_reply`, `agent_typing`, `TicketChatOut` endpoint.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_agent_reply_reaches_visitor_message_log(...) -> None:
      # active session (agent 48671); POST /api/v1/tickets/{tid}/articles {"channel": "webchat", "sender_type": "agent",
      #   "is_visible_for_customer": true, "subject": "", "body": "Bitte Router neu starten"}
      # → 201; tiqora_chat_message newest: sender "agent", body "Bitte Router neu starten", article_id == response id,
      #   meta {"agent_name": "Max"}; article history "SendAnswer"; FakeRedis.published has a wake for the session
  async def test_reply_to_waiting_chat_accepts_it(...) -> None:
      # waiting session → reply → session active, assigned_user_id == agent, "agent_joined" before the reply (seq order)
  async def test_reply_to_ended_chat_is_409_and_stores_nothing(...) -> None:
      # ended session → 409 {"detail": "chat_not_live"}; article count unchanged; no chat message row
  async def test_internal_note_not_sent_to_visitor(...) -> None:
      # channel "note", is_visible_for_customer false → 201, no tiqora_chat_message row
  async def test_email_reply_on_chat_ticket_not_sent_to_visitor(...) -> None:
      # channel "email" with CapturingMailSender → no tiqora_chat_message row
  async def test_html_body_converted_and_attachment_listed(...) -> None:
      # body "<p>Hallo</p>", content_type text/html, one base64 attachment "plan.pdf" → message body "Hallo",
      #   attachments [{"index": 0, "filename": "plan.pdf", ...}]; visitor download endpoint returns the bytes
  async def test_reply_too_long_422(...) -> None: 4001 chars → 422; nothing stored
  async def test_ticket_chat_info(...) -> None:
      # GET /tickets/{tid}/chat → status, visitor_name, topic_label "Support", visitor_online True after setting
      #   tiqora:chat:seen:<sid>, visitor_typing True after the visitor typing endpoint; ticket without session → 404;
      #   agent without ro → 403
  async def test_agent_typing_rate_limited(...) -> None:
      # two POSTs within 2 s → exactly one typing publish on the chat channel
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** per C6. `deliver_agent_chat_reply`: `live_session_for_ticket` (none → `ChatNotLive`); waiting → `accept_session` first (same transaction); body via `chat_body_from_article` (`MessageRejected` → `InvalidInput`); `ArticleIn` with `content_type="text/plain; charset=utf-8"`, `subject=article.subject or ticket title`, `to_address=visitor_from_address(...)`; `add_article`; `insert_message(sender="agent", meta={"agent_name": first name or None per show_agent_names}, attachments=[{index, filename, content_type, size}…])`; `queue_wakeup`.
- [ ] **Step 4: Run** → PASS (+ `tests/test_tickets_telegram_api.py` unchanged green); regenerate openapi; lint gates; commit (`feat(webchat): agent replies, ticket chat info and typing`).

### Task 11: Sweep loop — waiting timeout, idle sessions, retention (backend)

**Files:**
- Create: `backend/src/tiqora/webchat/sweep.py`, `backend/src/tiqora/worker/webchat.py`
- Modify: `backend/src/tiqora/domain/settings_store.py` (`KEY_WEBCHAT_SWEEP_ENABLED = "daemon.webchat_sweep.enabled"`, `KEY_WEBCHAT_SWEEP_INTERVAL_SECONDS = "daemon.webchat_sweep.interval_seconds"`), `backend/src/tiqora/config.py` (`webchat_sweep_interval_seconds: int = 15`), `backend/src/tiqora/worker/services.py` (`DaemonService(slug="webchat_sweep", enabled_key=KEY_WEBCHAT_SWEEP_ENABLED, default_enabled=True, toggleable=True, schedule_kind="interval", interval_key=KEY_WEBCHAT_SWEEP_INTERVAL_SECONDS, interval_settings_attr="webchat_sweep_interval_seconds")`), `backend/src/tiqora/worker/__main__.py` (`_interval_loop("webchat_sweep", …)`), the admin daemons i18n keys (`admin.daemons.services.webchat_sweep.*` — check the real key path in `de.json` for `report_facts` and mirror it)
- Test: `backend/tests/test_webchat_sweep.py` (both dialects, band 48700–48729), `backend/tests/test_admin_daemons.py` (catalogue count)

**Interfaces:**
- Produces:
  ```python
  WAITING_IDLE_SECONDS: Final = 120
  ACTIVE_IDLE_SECONDS: Final = 900
  SWEEP_BATCH: Final = 200
  async def run_webchat_sweep_tick(*, session_factory: async_sessionmaker[AsyncSession], redis_client: Any,
                                   now: datetime | None = None) -> dict[str, int]
      # {"timed_out": n, "abandoned": n, "purged": n}
  async def run_webchat_worker_tick(settings: Settings) -> dict[str, int]   # {"skipped": 1} when switched off
  ```

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_waiting_timeout_ends_with_no_agent(...) -> None:
      # waiting_since = NOW - 181 s, config wait_timeout 180, visitor seen key present
      res = await run_webchat_sweep_tick(session_factory=f, redis_client=r, now=NOW)
      assert res["timed_out"] == 1
      # session ended/no_agent, system message code "no_agent" params {"has_email": False}; ticket still open;
      # published chat_ended + wake
  async def test_idle_visitor_abandoned(...) -> None:
      # active, no seen key, visitor_last_seen NOW - 901 s → abandoned/idle, system "idle_closed";
      # waiting with seen key missing and last seen NOW - 121 s → abandoned
      # active with seen key present and last seen NOW - 2 h → untouched
  async def test_retention_purges_messages_and_pii(...) -> None:
      # ended_at NOW - 31 days (retention 30) → its tiqora_chat_message rows deleted; visitor_name/email/page_url/
      # user_agent/ip_hash NULL, token_hash "", purged_at NOW; ticket + articles untouched; second tick purged == 0
  async def test_switch_off_skips(...) -> None:
      # tiqora_settings daemon.webchat_sweep.enabled = "0" → run_webchat_worker_tick returns {"skipped": 1}
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement**; each step is one bounded query + per-row updates in its own transaction, then `flush_chat_notifications`. Config values are loaded once per config id per tick.
- [ ] **Step 4: Run** → PASS; lint gates; commit (`feat(webchat): sweep for waiting timeouts, idle chats and retention`).

### Task 12: Admin API for chat configurations (backend)

**Files:**
- Create: `backend/src/tiqora/api/v1/admin/chat_configs.py`
- Modify: `backend/src/tiqora/webchat/schemas.py` (admin models of C8), `backend/src/tiqora/api/v1/admin/__init__.py`
- Regenerate openapi files
- Test: `backend/tests/test_admin_chat_configs_api.py` (both dialects, band 48730–48759)

**Interfaces:**
- Consumes: `AdminUser`, `load_ticket_dynamic_field`, `own_origin`, C2.
- Produces: admin endpoints of C8; `new_public_key() -> str` (`"wc_" + secrets.token_urlsafe(24)[:32]`).

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_create_get_update_list(...) -> None:
      res = await client.post("/api/v1/admin/chat-configs", json={"name": "Website wc48", "values": {
          "allowed_origins": ["https://WWW.example.org/"], "topics": [{"key": "support", "label": "Support", "queue_id": 48731}]}})
      assert res.status_code == 201
      body = res.json()
      assert body["public_key"].startswith("wc_") and len(body["public_key"]) == 35
      assert body["values"]["allowed_origins"] == ["https://www.example.org"]
      assert body["embed_snippet"] == f'<script src="http://testserver/chat/v1/loader.js" data-tiqora-chat="{body["public_key"]}" async></script>'
      # PATCH {"values": {..., "greeting": "Hallo!"}} → greeting saved, changed_by == admin id; GET list contains it
  async def test_save_time_checks(...) -> None:
      # unknown queue 48799 → 422 errors {"topics.0.queue_id": "Diese Queue gibt es nicht."}
      # DF "NoSuchField" → errors {"prechat_fields.0.dynamic_field": …}; DF of type "Date" → error
      # origin "https://*.example.org" → 422 (pydantic) ; default_customer_user unknown → error
  async def test_rotate_key_changes_key(...) -> None: old != new; GET /config with the old key → 404
  async def test_delete_in_use_conflict(...) -> None: with one session → 409 {"detail": "in_use"}; without → 204
  async def test_non_admin_forbidden(...) -> None: 403
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement.** Write JSON columns with `model.model_dump_json()` per column; `changed`/`created` = `utc_now()`.
- [ ] **Step 4: Run** → PASS; regenerate openapi; lint gates; commit (`feat(webchat): admin API for chat configurations`).

### Task 13: Static widget routes, frame CSP, Docker + CI wiring (backend)

**Files:**
- Create: `backend/src/tiqora/webchat/static.py` (C9)
- Modify: `backend/src/tiqora/api/app.py` (`app.include_router(webchat_static_router)` and `mount_webchat_assets(app, cfg)` before `mount_spa`; `security_headers_middleware` skips the CSP `setdefault` for `/chat/v1/`)
- Modify: `Dockerfile` (frontend-build stage: `RUN pnpm --filter tiqora-frontend build && pnpm --filter @tiqora/webchat-widget build`; runtime: `COPY --from=frontend-build /workspace/packages/webchat-widget/dist /app/webchat/dist`) — the package exists only after Task 14; if Task 14 is not committed yet, do this Dockerfile edit in Task 14 instead and note it in the commit message
- Test: `backend/tests/test_webchat_static.py` (both dialects for the DB part, band 48760–48779; dist = `tmp_path` with `loader.js`, `frame.html`, `assets/app-abc123.js`)

**Interfaces:**
- Produces: `router` (GET `/chat/v1/loader.js`, GET `/chat/v1/frame`), `mount_webchat_assets(app: FastAPI, cfg: Settings) -> bool`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_loader_served_with_cache_and_corp(...) -> None:
      res = await client.get("/chat/v1/loader.js")
      assert res.status_code == 200 and res.headers["content-type"].startswith("text/javascript")
      assert res.headers["cache-control"] == "public, max-age=300"
      assert res.headers["cross-origin-resource-policy"] == "cross-origin"
      assert "content-security-policy-report-only" not in res.headers
  async def test_frame_csp_lists_only_allowed_origins(...) -> None:
      res = await client.get("/chat/v1/frame", params={"k": KEY, "origin": "https://www.example.org"})
      csp = res.headers["content-security-policy"]
      assert res.status_code == 200 and csp.endswith("frame-ancestors 'self' https://www.example.org")
      assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp
      assert res.headers["cache-control"] == "no-store" and res.headers["x-robots-tag"] == "noindex"
      assert "content-security-policy-report-only" not in res.headers
  async def test_frame_refused_for_foreign_origin(...) -> None:
      res = await client.get("/chat/v1/frame", params={"k": KEY, "origin": "https://evil.example"})
      assert res.status_code == 403 and res.headers["content-security-policy"].endswith("frame-ancestors 'none'")
      res2 = await client.get("/chat/v1/frame", params={"k": KEY, "origin": "https://www.example.org"},
                              headers={"Referer": "https://evil.example/page"})
      assert res2.status_code == 403
  async def test_frame_preview_only_from_own_origin(...) -> None:
      # preview=1 with Referer http://testserver/admin/webchat/1 → 200 + "frame-ancestors 'self'" only;
      # preview=1 with Referer https://www.example.org → 403
  async def test_unknown_key_or_channel_off_404(...) -> None: ...
  async def test_assets_immutable_and_missing_build_404(...) -> None:
      # /chat/v1/assets/app-abc123.js → 200, cache-control immutable; with webchat_dist_dir pointing to an empty dir
      # loader.js → 404 and app start does not crash
  async def test_spa_does_not_shadow_chat_paths(...) -> None:
      # with a SPA dist present, GET /chat/v1/unknown → 404 JSON, not index.html
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** C9. `frame.html` cached as `(mtime, bytes)`; response `Response(content, media_type="text/html; charset=utf-8", headers=…)`. Referer origin via `urlsplit(referer)` → `normalize_origin` (error → refuse).
- [ ] **Step 4: Run** → PASS; lint gates; commit (`feat(webchat): serve loader, frame and assets with per-chat frame-ancestors`).

### Task 14: Widget package scaffold — builds, XSS lint rules, locale extraction, size budget, CI (widget)

**Files:**
- Create: `packages/webchat-widget/package.json`, `tsconfig.json`, `eslint.config.js`, `vitest.config.ts`, `vite.loader.config.ts`, `vite.frame.config.ts`, `scripts/widget-locales.ts`, `scripts/check-size.mjs`, `src/shared/protocol.ts` (C9), `src/loader/index.ts` (placeholder that only reads `data-tiqora-chat`), `src/frame/frame.html`, `src/frame/main.tsx` (renders "…" placeholder), `src/frame/i18n.ts`, `src/vite-env.d.ts` (declares `virtual:webchat-locales`)
- Modify: `frontend/src/i18n/locales/en.json`, `de.json` (+ propagate) — `webchatWidget.*` keys below
- Modify: `.github/workflows/ci.yml` (frontend job: after "Build api-client" add `pnpm --filter @tiqora/webchat-widget lint`, `… test`, `… build`), `Dockerfile` (if not done in Task 13), `pnpm-lock.yaml` (via `pnpm install`)
- Test: `packages/webchat-widget/src/shared/protocol.test.ts`, `scripts/widget-locales.test.ts`, `src/frame/i18n.test.ts`

**Interfaces:**
- Produces:
  ```ts
  // package.json (abridged)
  { "name": "@tiqora/webchat-widget", "private": true, "type": "module",
    "scripts": { "build": "tsc --noEmit && vite build -c vite.loader.config.ts && vite build -c vite.frame.config.ts && node scripts/check-size.mjs",
                 "dev": "vite -c vite.frame.config.ts", "test": "vitest run", "lint": "eslint src scripts e2e --max-warnings 0 && tsc --noEmit",
                 "e2e": "playwright test" },
    "dependencies": { "preact": "^10.26.0" },   // resolve with pnpm add; pnpm minimumReleaseAge (3 days) must accept the resolved version
    "devDependencies": { "@tiqora/api-client": "workspace:*", "typescript": "~5.7.2", "vite": "^6.0.0", "vitest": "^4.1.11",
                         "jsdom": "^26.0.0", "@playwright/test": "^1.50.0", "eslint": "^9.18.0", "typescript-eslint": "^8.21.0", "@eslint/js": "^9.18.0", "globals": "^15.14.0" } }
  // scripts/widget-locales.ts
  export function extractWidgetMessages(locale: Record<string, unknown>, namespace = "webchatWidget"): Record<string, string>
      // flattens locale[namespace] to {"launcher.open": "…"}; non-string leaves ignored; missing namespace → {}
  export function widgetLocales(opts: { localesDir: string; namespace?: string }): Plugin
      // virtual:webchat-locales → `export const LANGS = [...]; export const load = { de: () => import("virtual:webchat-locale/de"), … }`
      // virtual:webchat-locale/<lang> → `export default {…flattened…}`; watches the locale files in dev
  // src/frame/i18n.ts
  export const RTL_LANGS: ReadonlySet<string>   // ar, he, fa, ur
  export function pickLanguage(requested: string | null, available: readonly string[]): string   // "de-AT"→"de", unknown→"en"
  export async function loadMessages(lang: string): Promise<(key: string, vars?: Record<string, string>) => string>
      // missing key → English text → key itself; {{name}} interpolation with String(value) (no HTML)
  ```
  `vite.loader.config.ts`: `build.lib = { entry: "src/loader/index.ts", formats: ["iife"], name: "TiqoraChatLoader", fileName: () => "loader.js" }`, `outDir: "dist"`, `emptyOutDir: true`, `target: "es2018"`. `vite.frame.config.ts`: `root: "src/frame"`, `base: "/chat/v1/"`, `build.outDir: "../../dist"`, `emptyOutDir: false`, `rollupOptions.input: "src/frame/frame.html"`, `assetsDir: "assets"`, `esbuild: { jsx: "automatic", jsxImportSource: "preact" }`, `plugins: [widgetLocales({ localesDir: "../../frontend/src/i18n/locales" })]`, dev proxy `/api` → `VITE_PROXY_TARGET || http://localhost:8000`. `frame.html` has no inline script or style (`<script type="module" src="./main.tsx">`, `<link rel="stylesheet" href="./styles.css">`). ESLint (`eslint.config.js`): typescript-eslint recommended + `no-restricted-properties` for `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `write` on `document`; `no-restricted-syntax` for JSX attribute `dangerouslySetInnerHTML`; `no-eval`, `no-implied-eval`, `no-new-func`.

  `webchatWidget.*` keys (German first; English in `en.json`): `launcher.open` "Chat öffnen", `launcher.close` "Chat schließen", `launcher.default` "Fragen? Schreiben Sie uns", `header.close` "Schließen", `header.support` "Support", `status.waiting` "Einen Moment – gleich ist jemand für Sie da.", `status.active` "{{name}} ist im Chat", `status.activeAnon` "Ein Mitarbeiter ist im Chat", `status.ended` "Der Chat ist beendet.", `status.reconnecting` "Verbindung unterbrochen – wir verbinden neu …", `prechat.title` "Bevor es losgeht", `prechat.topic` "Worum geht es?", `prechat.message` "Ihre Nachricht", `prechat.start` "Chat starten", `prechat.privacy` "Datenschutzhinweise", `prechat.required` "Bitte ausfüllen.", `prechat.invalidEmail` "Bitte eine gültige E-Mail-Adresse eingeben.", `prechat.invalidOption` "Bitte eine Auswahl treffen.", `prechat.tooLong` "Der Text ist zu lang.", `composer.placeholder` "Nachricht schreiben …", `composer.send` "Senden", `composer.attach` "Datei anhängen", `composer.tooLong` "Die Nachricht ist zu lang (höchstens {{max}} Zeichen).", `composer.failed` "Nicht gesendet – erneut versuchen", `composer.retry` "Erneut senden", `upload.tooLarge` "Die Datei ist zu groß (höchstens {{max}}).", `upload.type` "Diese Dateiart wird nicht angenommen.", `upload.failed` "Die Datei konnte nicht hochgeladen werden.", `upload.download` "Herunterladen", `typing` "schreibt …", `you` "Sie", `offline.title` "Gerade ist niemand im Chat", `offline.text` "Hinterlassen Sie uns eine Nachricht – wir antworten per E-Mail.", `offline.send` "Nachricht senden", `offline.thanks` "Danke! Wir melden uns per E-Mail.", `system.agent_joined` "{{name}} ist dem Chat beigetreten.", `system.agent_joinedAnon` "Ein Mitarbeiter ist dem Chat beigetreten.", `system.chat_ended` "Der Chat wurde beendet.", `system.visitor_left` "Sie haben den Chat beendet.", `system.no_agent` "Gerade ist niemand frei. Wir antworten Ihnen per E-Mail.", `system.no_agentNoEmail` "Gerade ist niemand frei. Wohin dürfen wir Ihnen antworten?", `system.idle_closed` "Der Chat wurde wegen Inaktivität beendet.", `contact.title` "Ihre E-Mail-Adresse", `contact.save` "Speichern", `contact.saved` "Danke, wir antworten per E-Mail.", `ended.restart` "Neuen Chat starten", `ended.end` "Chat beenden", `error.generic` "Etwas ist schiefgelaufen. Bitte versuchen Sie es noch einmal.", `error.rateLimited` "Zu viele Nachrichten – bitte kurz warten.", `error.unavailable` "Der Chat ist gerade nicht erreichbar.", `error.notAllowed` "Dieser Chat ist für diese Website nicht freigegeben."

- [ ] **Step 1: Write the failing tests.**
  ```ts
  // protocol.test.ts
  it("accepts only well-formed frame messages", () => {
    expect(isFrameToHost({ p: PROTOCOL, type: "availability", online: true })).toBe(true);
    expect(isFrameToHost({ p: PROTOCOL, type: "availability", online: "yes" })).toBe(false);
    expect(isFrameToHost({ p: "other", type: "availability", online: true })).toBe(false);
    expect(isFrameToHost({ p: PROTOCOL, type: "ready", online: true, hidden: false, launcherLabel: "x",
      theme: { primaryColor: "#2563eb", position: "right", mode: "auto" }, dir: "ltr" })).toBe(true);
    expect(isFrameToHost({ p: PROTOCOL, type: "ready", online: true, hidden: false, launcherLabel: "x",
      theme: { primaryColor: "red;background:url(x)", position: "right", mode: "auto" }, dir: "ltr" })).toBe(false);
    expect(isFrameToHost("string")).toBe(false);
  });
  it("host messages", () => {
    expect(isHostToFrame({ p: PROTOCOL, type: "set-topic", topic: "support" })).toBe(true);
    expect(isHostToFrame({ p: PROTOCOL, type: "set-topic", topic: 5 })).toBe(false);
  });
  // widget-locales.test.ts
  it("extracts and flattens only the widget namespace", () => {
    expect(extractWidgetMessages({ webchatWidget: { launcher: { open: "Chat öffnen" }, typing: "schreibt …" },
      other: { a: "b" } })).toEqual({ "launcher.open": "Chat öffnen", typing: "schreibt …" });
    expect(extractWidgetMessages({})).toEqual({});
  });
  it("every locale file has the same widget keys as de.json", async () => {
    // read all frontend/src/i18n/locales/*.json; key set of extractWidgetMessages(locale) equals de's for every file
  });
  // i18n.test.ts
  it("picks languages", () => {
    expect(pickLanguage("de-AT", ["de", "en"])).toBe("de");
    expect(pickLanguage("xx", ["de", "en"])).toBe("en");
    expect(pickLanguage(null, ["de", "en"])).toBe("en");
    expect(RTL_LANGS.has("ar")).toBe(true);
  });
  it("interpolates as plain text", async () => {
    const t = await loadMessages("de");
    expect(t("status.active", { name: "<b>Max</b>" })).toBe("<b>Max</b> ist im Chat");   // later rendered as text node
  });
  ```
- [ ] **Step 2: Run to verify failure.** `pnpm install && pnpm --filter @tiqora/webchat-widget test` → FAIL (modules missing).
- [ ] **Step 3: Implement** the scaffold, the plugin, i18n runtime, keys in `en.json`/`de.json`, `node frontend/scripts/propagate-i18n-keys.mjs`, `scripts/check-size.mjs` (gzip via `node:zlib` `gzipSync`; fails with a message naming the file and the budget: `dist/loader.js` ≤ 5120 B; sum of `dist/assets/*.js` + `*.css` loaded by `frame.html` ≤ 40960 B), CI steps.
- [ ] **Step 4: Run** `pnpm --filter @tiqora/webchat-widget lint && pnpm --filter @tiqora/webchat-widget test && pnpm --filter @tiqora/webchat-widget build && node frontend/scripts/check-i18n-keys.mjs && cd frontend && pnpm lint` → PASS (`ls packages/webchat-widget/dist` shows `loader.js`, `frame.html`, `assets/`); add `packages/webchat-widget/dist` to `.gitignore` if not covered; commit (`feat(webchat-widget): package scaffold, builds, locale extraction and size budget`).

### Task 15: Frame core — API client, fetch SSE client, storage, safe linkify, theme (widget)

**Files:**
- Create: `packages/webchat-widget/src/frame/api.ts`, `sse.ts`, `storage.ts`, `linkify.ts`, `theme.ts`, `src/frame/views/MessageText.tsx`
- Test: `src/frame/sse.test.ts`, `api.test.ts`, `storage.test.ts`, `linkify.test.tsx`, `theme.test.ts`

**Interfaces:**
- Consumes: Task 7/8 endpoint shapes (types: `import type { Schemas } from "@tiqora/api-client"` — type-only, zero runtime bytes).
- Produces:
  ```ts
  // api.ts
  export type ChatPublicConfig = Schemas["ChatPublicConfigOut"]; export type ChatMessage = Schemas["ChatMessageOut"];
  export type ChatSession = Schemas["ChatSessionOut"]; export type ChatSessionCreated = Schemas["ChatSessionCreatedOut"];
  export class ChatApiError extends Error { status: number; code: string; errors: Record<string, string>; retryAfter: number | null }
  export function createChatApi(opts: { base?: string; fetchImpl?: typeof fetch }): {
    config(key: string, origin: string): Promise<ChatPublicConfig>;
    createSession(body: Schemas["ChatSessionCreateIn"]): Promise<ChatSessionCreated>;
    session(id: string, token: string): Promise<ChatSession>;
    messages(id: string, token: string, after: number): Promise<ChatMessage[]>;
    send(id: string, token: string, body: string, clientId: string): Promise<ChatMessage>;
    typing(id: string, token: string): Promise<void>;
    upload(id: string, token: string, file: File, clientId: string): Promise<ChatMessage>;
    download(id: string, token: string, seq: number, index: number): Promise<Blob>;
    contact(id: string, token: string, email: string, name: string | null): Promise<void>;
    end(id: string, token: string): Promise<void>;
    offline(body: Schemas["ChatOfflineIn"]): Promise<void>;
  };
  // sse.ts
  export type SseEvent = { id: string | null; event: string; data: string };
  export function createSseParser(): (chunk: string) => SseEvent[]   // \n\n and \r\n\r\n boundaries, ":" comments, multi-line data
  export type StreamState = "connecting" | "live" | "reconnecting" | "closed";
  export class ChatStream {
    constructor(opts: { url: string; token: string; lastSeq: () => number; onEvent: (e: SseEvent) => void;
      onState: (s: StreamState) => void; fetchImpl?: typeof fetch; sleep?: (ms: number) => Promise<void>;
      random?: () => number });
    start(): void;   // fetch with headers X-Chat-Token, Accept: text/event-stream, Last-Event-ID: String(lastSeq())
    stop(): void;    // AbortController
  }
  // backoff: 1000, 2000, 5000, 10000, 30000 ms (+0–20 % jitter), reset after a received event; 429/503 → Retry-After;
  // 404/403 → "closed" (no retry); event "end" → "closed"
  // storage.ts
  export function safeStorage(): { get(k: string): string | null; set(k: string, v: string): void; remove(k: string): void }
      // localStorage in try/catch, Map fallback
  export function visitorId(store = safeStorage()): string           // 22 chars base64url from crypto.getRandomValues
  export type StoredSession = { sessionId: string; token: string; lastSeq: number };
  export function loadSession(key: string, store = safeStorage()): StoredSession | null   // validates shape
  export function saveSession(key: string, s: StoredSession, store = safeStorage()): void
  export function clearSession(key: string, store = safeStorage()): void
  // linkify.ts
  export type TextPart = { kind: "text"; text: string } | { kind: "link"; text: string; href: string };
  export function linkify(text: string): TextPart[]   // only http(s) URLs (URL constructor, protocol check), trailing ).,;:!? excluded
  // views/MessageText.tsx
  export function MessageText(props: { text: string }): JSX.Element   // text nodes, <br> per "\n", <a target="_blank" rel="noopener noreferrer nofollow">
  // theme.ts
  export function applyTheme(root: HTMLElement, theme: { primaryColor: string; mode: "auto" | "light" | "dark" }, dir: "ltr" | "rtl"): void
      // CSSOM only: root.style.setProperty("--tq-primary", color) after /^#[0-9a-f]{6}$/i check; data-theme attr; dir attr
  ```

- [ ] **Step 1: Write the failing tests.**
  ```ts
  it("parses SSE across chunk boundaries", () => {
    const p = createSseParser();
    expect(p("id: 3\nevent: mess")).toEqual([]);
    expect(p('age\ndata: {"seq":3}\n\n: hb\n\nevent: typing\r\ndata: {"who":"agent"}\r\n\r\n')).toEqual([
      { id: "3", event: "message", data: '{"seq":3}' }, { id: null, event: "typing", data: '{"who":"agent"}' }]);
  });
  it("reconnects with Last-Event-ID after a dropped stream", async () => {
    // fetchImpl #1 streams "id: 4\nevent: message\ndata: {...seq 4}\n\n" then errors (TypeError: network);
    // lastSeq() returns the highest seq seen (test keeps it in a variable updated by onEvent)
    // fetchImpl #2 is called with headers["Last-Event-ID"] === "4" after sleep(1000..1200); states seen:
    //   ["connecting", "live", "reconnecting", "live"]
  });
  it("honours Retry-After on 503 and stops on 404", async () => { /* sleep called with 30000; 404 → state closed, no further fetch */ });
  it("stops after an end event", async () => { /* event end → state closed, fetch called once */ });
  it("api maps errors", async () => {
    // fetchImpl → 429 {detail: "rate_limited"} with Retry-After 7 → ChatApiError {status 429, code "rate_limited", retryAfter 7}
    // 422 {detail: "invalid_prechat", errors: {email: "email"}} → errors {email: "email"}
    // send() sends header X-Chat-Token and JSON {body, client_id}; never puts the token in the URL
  });
  it("storage falls back to memory when localStorage throws", () => {
    // stub localStorage getter to throw → saveSession/loadSession round-trip still works within the same module instance
    // loadSession returns null for {"sessionId": 5} (bad shape)
  });
  it("renders script and javascript: links as text", () => {
    const { container } = renderPreact(<MessageText text={'<img src=x onerror=alert(1)> javascript:alert(1) https://example.org/a).'} />);
    expect(container.querySelector("img")).toBeNull();
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("https://example.org/a");
    expect(links[0].getAttribute("rel")).toBe("noopener noreferrer nofollow");
    expect(container.textContent).toContain("<img src=x onerror=alert(1)> javascript:alert(1)");
  });
  it("applies theme through CSSOM and rejects bad colours", () => {
    const el = document.createElement("div");
    applyTheme(el, { primaryColor: "#ff0000", mode: "dark" }, "rtl");
    expect(el.style.getPropertyValue("--tq-primary")).toBe("#ff0000");
    expect(el.getAttribute("dir")).toBe("rtl"); expect(el.dataset.theme).toBe("dark");
    applyTheme(el, { primaryColor: "red;x:y", mode: "auto" }, "ltr");
    expect(el.style.getPropertyValue("--tq-primary")).toBe("#2563eb");   // fallback default
  });
  ```
  (`renderPreact` = small helper in `src/test/render.ts`: `render(vnode, container)` from `preact` + `act` from `preact/test-utils`.)
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** the modules. `ChatStream` reads `response.body.getReader()` + `TextDecoder("utf-8", { stream: true })`.
- [ ] **Step 4: Run** lint/test/build of the package → PASS; commit (`feat(webchat-widget): API client, reconnecting stream, storage, safe text rendering`).

### Task 16: Frame views — offline form, pre-chat form, chat, upload, contact, ended; preview mode (widget)

**Files:**
- Create: `packages/webchat-widget/src/frame/App.tsx`, `state.ts`, `styles.css`, `views/OfflineForm.tsx`, `PrechatForm.tsx`, `ChatView.tsx`, `MessageList.tsx`, `Composer.tsx`, `ContactPrompt.tsx`, `EndedView.tsx`, `bridge.ts` (frame side of postMessage)
- Modify: `src/frame/main.tsx`
- Test: `src/frame/state.test.ts`, `App.test.tsx`, `views/Composer.test.tsx`, `views/PrechatForm.test.tsx`

**Interfaces:**
- Consumes: Task 15 modules; C9 protocol.
- Produces:
  ```ts
  // state.ts — pure reducer, every view decision lives here
  export type View = "loading" | "blocked" | "hidden" | "offline" | "offline-sent" | "prechat" | "chat" | "ended";
  export type ChatState = { view: View; config: ChatPublicConfig | null; session: ChatSession | null;
    messages: ChatMessage[]; pending: PendingMessage[]; agentTyping: boolean; stream: StreamState; error: string | null;
    topic: string | null; unread: number; open: boolean };
  export type PendingMessage = { clientId: string; body: string; file?: File; status: "sending" | "failed" };
  export type Action =
    | { type: "config-loaded"; config: ChatPublicConfig } | { type: "config-failed"; status: number }
    | { type: "session-restored"; session: ChatSession; messages: ChatMessage[] } | { type: "session-created"; created: ChatSessionCreated }
    | { type: "message"; message: ChatMessage } | { type: "session-event"; status: string; agentName: string | null }
    | { type: "typing" } | { type: "typing-expired" } | { type: "pending"; pending: PendingMessage }
    | { type: "pending-failed"; clientId: string } | { type: "end" } | { type: "opened" } | { type: "closed" }
    | { type: "offline-sent" } | { type: "set-topic"; topic: string } | { type: "stream-state"; state: StreamState };
  export function reducer(state: ChatState, action: Action): ChatState
      // messages merged by seq (dedupe, sorted); a message whose client_id matches a pending one removes the pending;
      // agent/system messages while closed increase unread; config-failed 403 → "blocked", 404 → "hidden";
      // config online=false → "offline" (offline_mode "form") or "hidden" ("hide"); restored session → "chat";
      // session status ended/abandoned → "ended"
  // bridge.ts
  export function createFrameBridge(opts: { hostOrigin: string; onHostMessage: (m: HostToFrame) => void;
    win?: Window }): { post(m: FrameToHost): void; dispose(): void }
  ```
  Views: `PrechatForm` renders the configured fields (text/textarea/email/select/checkbox — plain Preact inputs with `<label for>`, `aria-invalid`, `aria-describedby` error ids), topic select when > 1 topic (only `online` topics enabled), message textarea, privacy link (`privacy_url`, `rel="noopener noreferrer"`), client-side validation mirroring the server codes and showing server `errors` per field. `Composer`: Enter sends, Shift+Enter newline, counter at > 90 % of `max_message_length`, paperclip opens `<input type=file accept=…types>`, image paste (`clipboardData.files`) uploads, client-side size/type check against `config.upload`, typing ping at most every 3 s, failed sends stay with "Erneut senden". `MessageList`: `role="log"` + `aria-live="polite"`, own messages right/left by `dir`, agent name above agent bubbles (or `header.support`), system lines centred and translated by `code` (`no_agent` with `has_email=false` → `system.no_agentNoEmail` + `ContactPrompt`), attachments as buttons that download through `api.download` → `URL.createObjectURL` (image attachments shown as `<img src=blob:…>` thumbnails, max 240 px), typing indicator. `EndedView`: transcript stays visible + "Neuen Chat starten" (clears stored session). Preview mode (`preview=1`): no API calls except `config`; renders a sample conversation from fixed texts; applies `preview-config` theme/greeting/fields from the host (Tiqora admin) message. Escape key → `close-request`.

- [ ] **Step 1: Write the failing tests.**
  ```ts
  it("reducer dedupes by seq and resolves pending by client_id", () => {
    let s = reducer(initial, { type: "pending", pending: { clientId: "c1", body: "Hallo", status: "sending" } });
    s = reducer(s, { type: "message", message: msg({ seq: 2, sender: "visitor", body: "Hallo", client_id: "c1" }) });
    s = reducer(s, { type: "message", message: msg({ seq: 2, sender: "visitor", body: "Hallo", client_id: "c1" }) });
    s = reducer(s, { type: "message", message: msg({ seq: 1, sender: "visitor", body: "Erste" }) });
    expect(s.messages.map((m) => m.seq)).toEqual([1, 2]); expect(s.pending).toEqual([]);
  });
  it("offline config shows the offline form or hides", () => {
    expect(reducer(initial, { type: "config-loaded", config: cfg({ online: false, offline_mode: "form" }) }).view).toBe("offline");
    expect(reducer(initial, { type: "config-loaded", config: cfg({ online: false, offline_mode: "hide" }) }).view).toBe("hidden");
    expect(reducer(initial, { type: "config-failed", status: 403 }).view).toBe("blocked");
  });
  it("counts unread only while closed", () => { /* open=false + agent message → unread 1; opened → 0 */ });
  it("App: pre-chat → session → agent reply → end", async () => {
    // fake api (vi.fn) + fake ChatStream factory; render <App params={{k, origin, lang: "de", preview: false}} api={…} />
    // fill Name, E-Mail, Nachricht, click "Chat starten" → createSession called with prechat {name, email}, message,
    //   visitor_id 22 chars, client_id string; view "chat"; status line "Einen Moment – gleich ist jemand für Sie da."
    // stream emits session {status active, agent_name "Max"} + message seq 2 agent "Hallo!" → text "Max ist im Chat",
    //   bubble "Hallo!"; bridge.post called with {type: "message", sender: "agent"} (no body)
    // click "Chat beenden" → api.end called; stream end → "Neuen Chat starten" visible
  });
  it("App restores a stored session and replays from lastSeq", async () => { /* saveSession(...lastSeq 5) → messages(after=5) */ });
  it("Composer: Enter sends, Shift+Enter newline, too long blocked", async () => { /* … */ });
  it("Composer: rejects too-large and wrong-type files before upload", async () => {
    // config.upload {max_bytes: 1024, types: ["image/png"]} → File 2048 bytes → text "Die Datei ist zu groß (höchstens 1 KB)."; upload not called
    // File type "image/svg+xml" → "Diese Dateiart wird nicht angenommen."
  });
  it("PrechatForm shows server field errors", async () => { /* createSession rejects ChatApiError 422 errors {email:"email"} → aria-invalid + text */ });
  it("bridge ignores foreign origins and foreign sources", () => {
    // dispatch MessageEvent {origin: "https://evil.example", source: window.parent, data: {p, type:"open"}} → handler not called
    // origin === hostOrigin but source !== window.parent → not called; valid → called once
    // post() calls parent.postMessage(msg, hostOrigin) — never "*"
  });
  it("renders RTL", () => { /* lang "ar" → root dir="rtl" */ });
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement**; `styles.css` uses only `var(--tq-primary)` and fixed neutral colours with `[data-theme="dark"]` overrides and `prefers-color-scheme` for `auto`; focus-visible outlines; touch targets ≥ 44 px; no external fonts.
- [ ] **Step 4: Run** lint/test/build (size budget!) → PASS; commit (`feat(webchat-widget): chat, pre-chat, upload, offline and contact views`).

### Task 17: Loader, launcher, host API; widget e2e on a second origin (widget)

**Files:**
- Create: `packages/webchat-widget/src/loader/index.ts` (replace placeholder), `launcher.ts`, `bridge.ts`, `hostApi.ts`, `scripts/mock-server.mjs`, `e2e/host.html`, `e2e/widget.spec.ts`, `playwright.config.ts`
- Test: `src/loader/loader.test.ts` (jsdom), `e2e/widget.spec.ts`

**Interfaces:**
- Produces:
  ```ts
  // loader/index.ts — runs once per page (guard window.__tiqoraChatLoaded)
  // reads document.currentScript: data-tiqora-chat (key), its src origin = tiqoraOrigin; waits for window "load"
  // (or immediately if complete) + requestIdleCallback fallback setTimeout 1500; creates a hidden container + iframe
  // src `${tiqoraOrigin}/chat/v1/frame?k=${key}&origin=${encodeURIComponent(location.origin)}&lang=${lang}`
  // with title "Chat", allow="clipboard-write", referrerpolicy "strict-origin"; shows the launcher only after "ready"
  // with hidden=false
  export function createLauncher(doc: Document, opts: { label: string; position: "right" | "left"; color: string;
    onClick: () => void }): { el: HTMLButtonElement; setOpen(open: boolean): void; setUnread(n: number): void; remove(): void }
      // <button type="button" aria-label aria-expanded aria-controls>; styles via el.style.setProperty only;
      // z-index 2147483000; 56 px circle; unread badge as a child span with textContent
  export function createHostBridge(opts: { iframe: HTMLIFrameElement; tiqoraOrigin: string;
    onFrameMessage: (m: FrameToHost) => void }): { post(m: HostToFrame): void; dispose(): void }
  export function installHostApi(win: Window, impl: { open(): void; close(): void; toggle(): void;
    setTopic(key: string): void; on(event: HostEvent, cb: (detail: unknown) => void): () => void }): void
      // window.Tiqora = { ...existing, chat: impl } — never overwrites another key on window.Tiqora
  ```
  Layout: open panel 380 × 600 px (max-height `calc(100vh - 100px)`), bottom 88 px on the configured side; viewport ≤ 480 px → full screen (`inset: 0`), body scroll untouched; focus moves into the iframe on open and back to the launcher on close.

  `scripts/mock-server.mjs` (Node `http`, no dependencies): port 5190 = "Tiqora" (serves `dist/` under `/chat/v1/…` with the same headers as Task 13 — CSP `frame-ancestors 'self' http://127.0.0.1:5191` — and an in-memory `/api/chat/v1` implementing `config`, `sessions`, `messages`, `stream` (with `Last-Event-ID` replay), `typing`, `attachments` (+ download), `end`, `offline`, plus test hooks `POST /__test/agent-message {session_id, body}`, `POST /__test/drop-streams`, `POST /__test/offline {on}`); port 5191 = allowed host page (`e2e/host.html` embedding the loader); port 5192 = NOT allowed host page (same HTML).

- [ ] **Step 1: Write the failing tests.** `loader.test.ts`:
  ```ts
  it("ignores messages from other origins or windows", () => {
    // createHostBridge with iframe; window.dispatchEvent(new MessageEvent("message", {origin: "https://evil.example",
    //   source: iframe.contentWindow, data: {p: PROTOCOL, type: "unread", count: 3}})) → onFrameMessage not called
    // origin ok but source window → not called; valid → called; malformed data → not called
  });
  it("launcher is an accessible button styled through CSSOM", () => {
    const l = createLauncher(document, { label: "Chat öffnen", position: "left", color: "#ff0000", onClick });
    expect(l.el.tagName).toBe("BUTTON"); expect(l.el.getAttribute("aria-label")).toBe("Chat öffnen");
    expect(l.el.getAttribute("style")).not.toContain("javascript"); expect(l.el.style.left).toBe("20px");
    l.setUnread(2); expect(l.el.textContent).toContain("2"); l.setOpen(true); expect(l.el.getAttribute("aria-expanded")).toBe("true");
  });
  it("host API does not clobber window.Tiqora", () => {
    (window as any).Tiqora = { other: 1 }; installHostApi(window, impl);
    expect((window as any).Tiqora.other).toBe(1); expect(typeof (window as any).Tiqora.chat.open).toBe("function");
  });
  ```
  `e2e/widget.spec.ts` (Chromium; `webServer: node scripts/mock-server.mjs` after `pnpm build`):
  ```ts
  test("opens, pre-chat, chats with an agent", async ({ page }) => {
    // goto http://127.0.0.1:5191/host.html → launcher visible → click → frame shows "Bevor es losgeht" → fill + start
    // → POST /__test/agent-message "Hallo, wie kann ich helfen?" → bubble visible in the frame
  });
  test("reconnects after a dropped stream without losing or doubling messages", async ({ page }) => {
    // after first agent message: POST /__test/drop-streams, then agent-message "Zweite" while disconnected
    // → "Zweite" appears exactly once (count of bubbles with that text === 1)
  });
  test("offline form when nobody is online", async ({ page }) => { /* /__test/offline on → "Gerade ist niemand im Chat" → send → "Danke!" */ });
  test("uploads a PNG and refuses an SVG", async ({ page }) => { /* setInputFiles png → thumbnail; svg → "Diese Dateiart wird nicht angenommen." */ });
  test("RTL for Arabic pages", async ({ page }) => { /* host.html?lang=ar → frame html dir="rtl" */ });
  test("is not shown on a site that is not allowed", async ({ page }) => {
    // goto http://127.0.0.1:5192/host.html → no launcher after 3 s (frame blocked by frame-ancestors, no "ready")
  });
  test("host API opens and reports messages without content", async ({ page }) => {
    // page.evaluate(() => Tiqora.chat.on("message", d => (window.__got = d))) → agent message → window.__got has sender only
  });
  ```
- [ ] **Step 2: Run to verify failure** (`pnpm --filter @tiqora/webchat-widget test`, then `… build && … e2e`) → FAIL.
- [ ] **Step 3: Implement** loader/launcher/bridge/host API and the mock server.
- [ ] **Step 4: Run** lint, test, build (loader ≤ 5 KB gz), e2e → PASS. Add to `ci.yml` (inside the tag-only Playwright block): `pnpm --filter @tiqora/webchat-widget exec playwright install --with-deps chromium` is already covered by the frontend install step (same cache) — add only `pnpm --filter @tiqora/webchat-widget e2e`. Commit (`feat(webchat-widget): loader, launcher, host API and cross-origin e2e`).

### Task 18: Frontend wiring — API wrappers, SSE chat events, ring, channel naming, demo mocks (frontend)

**Files:**
- Create: `frontend/src/lib/webchatApi.ts`, `frontend/src/lib/chatRing.ts`, `frontend/src/demo/webchatMock.ts`
- Modify: `frontend/src/lib/useSSE.ts` (C10), `frontend/src/lib/articleChannel.ts` (`CONVERSATIONAL_CHANNEL_NAMES` += `"WebChat"`; `channelIcon("WebChat") === "💬"`), `frontend/src/demo/mockData.ts` (one demo ticket 116 "Chat: Router blinkt rot" with `channel: "webchat"`, three `WebChat` articles; `resolveData` first line: `const wc = resolveWebchatData(p, method); if (wc !== undefined) return wc;`), `frontend/src/demo/handlers.ts` only if the inbox needs POST handling there
- Test: `frontend/src/lib/webchatApi.test.ts`, `frontend/src/lib/useSSE.test.tsx` (new cases), `frontend/src/lib/chatRing.test.ts`, `frontend/src/lib/articleChannel.test.ts`

**Interfaces:** C10 `webchatApi`, query keys, `useTicketChat`, `chatRing`.

- [ ] **Step 1: Write the failing tests.**
  ```ts
  it("chat events invalidate inbox and ticket", () => {
    const qc = new QueryClient(); const spy = vi.spyOn(qc, "invalidateQueries");
    handleSSEMessage(qc, JSON.stringify({ type: "chat_message", session_id: "a", ticket_id: 116, queue_id: 1 }));
    expect(spy).toHaveBeenCalledWith({ queryKey: chatInboxKey });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["tickets", 116] });
  });
  it("chat_waiting rings only when available and topic matches", () => {
    setRingContext({ available: true, topics: ["1:billing"] });
    receiveChatWaiting({ session_id: "a", topic_ref: "1:support" }); expect(beep).not.toHaveBeenCalled();
    setRingContext({ available: true, topics: [] });
    receiveChatWaiting({ session_id: "b", topic_ref: "1:support" }); expect(beep).toHaveBeenCalledTimes(1);
    receiveChatWaiting({ session_id: "b", topic_ref: "1:support" }); expect(beep).toHaveBeenCalledTimes(1);  // same session once
    setRingContext({ available: false, topics: [] });
    receiveChatWaiting({ session_id: "c", topic_ref: "1:support" }); expect(beep).toHaveBeenCalledTimes(1);
  });
  it("useTicketChat returns null on 404", async () => { /* api.request rejects ApiError 404 → data null */ });
  it("WebChat is conversational", () => { expect(isConversationalChannel("WebChat")).toBe(true); });
  ```
- [ ] **Step 2: Run to verify failure** (`cd frontend && pnpm test --run src/lib`) → FAIL.
- [ ] **Step 3: Implement.** Ring: WebAudio `OscillatorNode` beep (two 880 Hz tones, 120 ms; created lazily after the first user gesture — browsers block audio before), `new Notification(t("webchat.agent.ringTitle"), { body: topic label, tag: session_id })` only when `Notification.permission === "granted"`. Demo mocks: agent state (available true, topics [], max 3), topics (one), inbox (one waiting "Erika Musterfrau · Support", one mine = ticket 116), ticket chat for 116 (active, visitor_online true), configs list with one config "Website" (`https://www.example.org`).
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run` → PASS; commit (`feat(webchat): agent app wiring for chat events, ring and demo data`).

### Task 19: Messenger composer generalised; webchat conversation header (frontend)

**Files:**
- Create: `frontend/src/components/agent/messenger/MessengerComposer.tsx` (body moved from `TelegramChatComposer.tsx`, parameterised by the adapter), `messenger/adapters.ts`, `messenger/useTypingPing.ts` (takes `send: () => Promise<unknown>`), `frontend/src/components/agent/webchat/WebchatSessionHeader.tsx`
- Modify: `frontend/src/components/agent/TicketHeaderActions.tsx` ("Antworten" on a WebChat ticket with a live session does what it does for Telegram at lines ~180–293: `requestConversationView` + `requestComposer(focus)`), `frontend/src/components/agent/ArticleQuickActions.tsx` (same for the per-article reply, Telegram branch at lines ~43–88), `frontend/src/components/agent/ReplyDialog.tsx` (WebChat ticket without live session → channel `email`; To prefilled from `TicketChat.visitor_email`; when the only address is a `@chat.invalid` one or there is none: send disabled with the hint `webchat.agent.noVisitorEmail` "Der Besucher hat keine E-Mail-Adresse hinterlassen." — never mail `@chat.invalid`)
- Modify: `frontend/src/components/agent/telegram/TelegramChatComposer.tsx` (now `export function TelegramChatComposer(props) { return <MessengerComposer {...props} adapter={telegramAdapter} />; }`), `telegram/useTypingPing.ts` (re-export with the Telegram sender, keeps `TYPING_PING_INTERVAL_MS`), `frontend/src/components/agent/ArticleMasterDetail.tsx` (`isWebchat = dominantChannel(state.articles) === "WebChat"`; messenger composer when `view === "conversation" && canNote && isWebchat && ticketChat?.status in {"waiting","active"}`; `sortLocked`/`newestFirst` treat WebChat like Telegram), `frontend/src/components/agent/ArticleConversationView.tsx` (header: `WebchatSessionHeader` for WebChat tickets), i18n `webchat.agent.*`
- Test: existing `telegram/TelegramChatComposer.test.tsx` must stay green unchanged; new `messenger/MessengerComposer.webchat.test.tsx`, `webchat/WebchatSessionHeader.test.tsx`, `ArticleMasterDetail.test.tsx` (new cases)

**Interfaces:**
- Consumes: C10 adapter type, `webchatApi`, `useTicketChat`.
- Produces: `MessengerComposer`, `telegramAdapter`, `webchatAdapter`, `WebchatSessionHeader(props: { ticketId: number })`.

  `webchatAdapter`: `channel "webchat"`, `maxMessageLength 4000`, `supportsButtons false`, `supportsQuote false`, `metaQueryKey: ticketChatKey`, `sendTyping: (id) => webchatApi.typing(id)`, `extraPayload: () => ({})`, `useRecipientName: (id) => useTicketChat(id, true).data?.visitor_name ?? null`. Payload of a webchat send: `{ sender_type: "agent", is_visible_for_customer: true, subject: "", body, content_type: "text/plain; charset=utf-8", channel: "webchat", attachments?, ai_draft_id?, state_id? }`. A 409 `chat_not_live` shows `webchat.agent.chatEnded` ("Der Chat ist beendet – bitte per E-Mail antworten.") and invalidates `ticketChatKey` (the reply dialog then takes over because the composer unmounts).

  `WebchatSessionHeader`: visitor name (or "Besucher"), e-mail, topic, page link (`target=_blank rel="noopener noreferrer"`, shown as host + path, max 60 chars), status pill (`Wartet seit 2 Min.`, `Im Chat mit Max`, `Beendet`), online dot + "schreibt …" when `visitor_typing`; buttons "Annehmen" (status waiting; `webchatApi.accept`, 409 → toast with `webchat.agent.alreadyTaken` / `atCapacity`), "Chat beenden" (active; menu "Ticket offen lassen" / "Ticket schließen" → `webchatApi.end(sid, {close_ticket})`).

- [ ] **Step 1: Write the failing tests.**
  ```tsx
  it("webchat composer sends channel webchat without buttons or quote", async () => {
    // mock api.createArticle; render <MessengerComposer ticketId={116} adapter={webchatAdapter} />; type "Hallo" + Enter
    expect(createArticle).toHaveBeenCalledWith(116, expect.objectContaining({ channel: "webchat", body: "Hallo" }));
    expect(createArticle.mock.calls[0][1]).not.toHaveProperty("telegram_buttons");
    expect(screen.queryByTitle(/Antwort-Buttons/)).toBeNull();      // buttons toggle hidden
  });
  it("typing pings go to the webchat endpoint at most every 4 s", async () => { /* fake timers; 3 keystrokes → 1 call */ });
  it("409 chat_not_live shows the ended hint", async () => { /* createArticle rejects ApiError 409 {detail:"chat_not_live"} → text */ });
  it("shows the reply dialog instead of the messenger composer when the chat ended", () => {
    // ArticleMasterDetail with WebChat articles + useTicketChat → {status: "ended"} → no messenger input, "Antworten" visible
  });
  it("header accept and end", async () => {
    // status waiting → click "Annehmen" → webchatApi.accept("s1"); 409 already_taken → "Schon angenommen" text
    // status active → "Chat beenden" → "Ticket schließen" → webchatApi.end("s1", {close_ticket: true})
  });
  it("page link is safe", () => { /* page_url "javascript:alert(1)" → rendered as text, no anchor */ });
  it("header Antworten on a live chat opens the messenger composer, not the reply dialog", async () => {
    // TicketHeaderActions with WebChat articles + useTicketChat {status: "active"} → click Antworten →
    // requestConversationView(116) + requestComposer(116, {focus: true}); ReplyDialog not rendered
  });
  it("reply dialog never addresses @chat.invalid", async () => {
    // ended chat, visitor_email null, customer article from '"Besucher" <visitor-abc@chat.invalid>' → To empty,
    // send button disabled, hint "Der Besucher hat keine E-Mail-Adresse hinterlassen."; with visitor_email
    // "erika@example.org" → To prefilled with it and send enabled
  });
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** (move code, do not duplicate; keep `ticket.telegram.composer.*` keys for shared texts — renaming across 49 locales is out of scope).
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS; demo check with Playwright (`VITE_DEMO=1 pnpm dev`, open ticket 116, screenshot conversation + composer; delete `.playwright-mcp`); commit (`feat(webchat): channel-neutral messenger composer and chat header`).

### Task 20: Chat inbox and availability in the agent shell (frontend)

**Files:**
- Create: `frontend/src/components/agent/webchat/ChatInboxButton.tsx`, `ChatInboxPanel.tsx`, `ChatAvailabilityForm.tsx`
- Modify: `frontend/src/components/layout/AgentShell.tsx` (`<ChatInboxButton />` in `HeaderControls` before `<NotificationBell />`, rendered only when `webchatApi.topics` returns ≥ 1 topic), i18n `webchat.agent.*`
- Test: `ChatInboxButton.test.tsx`, `ChatInboxPanel.test.tsx`, `ChatAvailabilityForm.test.tsx`, `components/layout/AgentShell.test.tsx` (button hidden without topics)

**Interfaces:**
- Produces: `ChatInboxButton()` (icon button, badge = waiting count, `aria-label` "Chats – 2 wartend"), `ChatInboxPanel(props: { onClose(): void })`, `ChatAvailabilityForm()`.

  Panel: switch "Chats annehmen" (PUT agent-state; turning it on asks for notification permission once), "Themen" multi-select (`MultiSelectInput` from `components/fields`, options from `/chat/topics`, empty = alle), "Gleichzeitig höchstens" number 1–20; section "Wartende Chats" (visitor name or "Besucher", topic, "wartet seit …" relative time, button "Annehmen" → accept → navigate to `/agent/tickets/$ticketId`); section "Meine Chats" (links). Ring context (`setRingContext`) is updated from the agent-state query.

- [ ] **Step 1: Write the failing tests.**
  ```tsx
  it("badge shows waiting count", async () => { /* inbox 2 waiting → badge "2", aria-label contains "2 wartend" */ });
  it("accept navigates to the ticket", async () => { /* click Annehmen → webchatApi.accept("s1") → navigate to /agent/tickets/116 */ });
  it("accept conflict explains", async () => { /* 409 at_capacity → "Sie haben bereits 3 Chats." */ });
  it("availability saves", async () => { /* toggle on → putAgentState({available: true, topics: [], max_concurrent: 3}) */ });
  it("hidden without chat topics", () => { /* topics [] → no button */ });
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** lint/test/i18n check → PASS; demo check with Playwright (header button, open panel, screenshot); commit (`feat(webchat): chat inbox and availability in the agent header`).

### Task 21: Admin editor "Web-Chat" (frontend)

**Files:**
- Create: `frontend/src/routes/admin/WebchatConfigsPage.tsx` (`/admin/webchat`), `frontend/src/routes/admin/WebchatConfigEditorPage.tsx` (`/admin/webchat/$configId`, `new` for create), `frontend/src/components/admin/webchat/GeneralTab.tsx`, `HoursTab.tsx`, `PrechatTab.tsx`, `LookTab.tsx`, `ProtectionTab.tsx`, `EmbedTab.tsx`, `WidgetPreview.tsx`
- Modify: `frontend/src/router.tsx` (two routes), `frontend/src/lib/adminSearch.ts` (`ADMIN_PAGES` entry `{ slug: "webchat", route: "/admin/webchat", group: "communication", nameKey: "admin.nav.webchat", descriptionKey: "admin.pageDescriptions.webchat", keywords: ["webchat", "chat", "live-chat", "widget", "website", "einbetten", "embed"] }`), i18n `webchat.admin.*`
- Test: `WebchatConfigsPage.test.tsx`, `WebchatConfigEditorPage.test.tsx`, `components/admin/webchat/PrechatTab.test.tsx`, `WidgetPreview.test.tsx`

**Interfaces:**
- Consumes: `webchatApi` admin calls, `GET /api/v1/admin/channels/webchat` + `PUT` (master switch), queues/priorities/dynamic-field reference endpoints already used by other admin pages (`api.listQueues`, `/reference/ticket-field-options`, admin dynamic fields list — reuse what `PostmasterFilterDetailPage`/`AiQueuePolicyEditorPage` use).
- Produces: the pages; `WidgetPreview(props: { publicKey: string; values: ChatConfigValues; device: "desktop" | "mobile" })` — an `<iframe src="/chat/v1/frame?k=…&origin=<location.origin>&preview=1">` (380×600 or 360×640) that posts `{p: PROTOCOL, type: "preview-config", config}` (theme, greeting, prechat fields, topics) to `new URL(src).origin` on load and on every change (debounced 200 ms). The widget protocol constants are copied as a 6-line type in `WidgetPreview.tsx` (the app does not depend on the widget package).

  Tabs and fields (German labels): *Allgemein* — Name, Aktiv, Erlaubte Websites (one per line; server errors shown per line), Themen (Bezeichnung, Queue, Priorität; add/remove/reorder), Begrüßung, Datenschutz-Link, Mitarbeitername zeigen; *Zeiten & Offline* — Öffnungszeiten (Immer / Standardkalender / Kalender 1–9), Wartezeit bis E-Mail-Angebot (Sekunden), Wenn niemand da ist (Nachrichtenformular / Chat ausblenden), Standard-Kunde (optional); *Vorab-Formular* — field list editor (Schlüssel, Art, Beschriftung, Pflichtfeld, Auswahlwerte, Übernehmen in: Name / E-Mail / Dynamisches Feld / nur anzeigen) with a live preview rendered by `FieldsForm` (wave-0 typed field engine: `text`→`text`, `textarea`→`textarea`, `email`→`text`, `select`→`select`, `checkbox`→`boolean`); *Aussehen* — Farbe, Position, Hell/Dunkel/Automatisch, Text am Button, `WidgetPreview` with "Desktop"/"Handy" toggle; *Schutz* — the five rate limits with their bounds, Datei-Uploads erlauben, Höchstgröße (MB), erlaubte Dateiarten (checkboxes), Aufbewahrung Chatverlauf (Tage); *Einbetten* — snippet in a read-only code box with "Kopieren" (clipboard API with a select-all fallback on http LAN pages — skill `clipboard-api-missing-on-http-lan-pages`), hint for the website's CSP (`script-src`/`frame-src` with the Tiqora address), "Schlüssel erneuern" with confirm ("Der alte Einbettungscode funktioniert danach nicht mehr."). List page: master switch "Web-Chat eingeschaltet" (admin channels API), table Name / Websites / Themen / Aktiv / Chats, "Neuer Chat".

- [ ] **Step 1: Write the failing tests.**
  ```tsx
  it("lists configs and toggles the master switch", async () => { /* PUT /admin/channels/webchat {enabled: true} */ });
  it("creates a config and shows server errors per field", async () => {
    // fill Name + one Thema (queue select) + origin "https://*.example.org" → server 422 detail errors
    //   {"allowed_origins": "…"} → message under the origins textarea; fix → createConfig called with normalised payload
  });
  it("prechat tab: select needs options, preview uses FieldsForm", async () => {
    // add field kind "Auswahl" without options → "Bitte mindestens einen Auswahlwert angeben." and save disabled
    // add option → preview shows a select with that option (FieldsForm testid "webchat-prechat-preview-<key>")
  });
  it("embed tab copies the snippet and rotates the key after confirm", async () => { /* … */ });
  it("preview posts the draft config to the preview frame origin only", () => {
    // spy on iframe.contentWindow.postMessage → called with ({p: "tiqora-chat/1", type: "preview-config", config}, location.origin)
  });
  ```
- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement**; demo mocks for the admin endpoints in `webchatMock.ts` (list, get, create/patch echo, rotate).
- [ ] **Step 4: Run** lint/test/i18n check → PASS; demo check with Playwright (list, each tab, screenshot; preview iframe shows nothing in demo mode — acceptable, note it); commit (`feat(webchat): admin editor for chats with live preview and embed code`).

### Task 22: Docs, sizing script, full verification

**Files:**
- Create: `docs/guide/webchat.md` (admin + agent guide: set up a chat, embed snippet, website CSP lines, topics → queues, Öffnungszeiten, Vorab-Formular, uploads, what visitors see offline, accepting chats, ending chats, replying by e-mail after a chat ended, privacy: what is stored and for how long), `backend/scripts/webchat_stream_load.py` (httpx async: creates N sessions against a test instance and holds N streams for M minutes; prints connections held, errors, RSS of the API process if given a PID)
- Modify: `docs/channels.md` (section "Web-Chat (`webchat/`)": channel row `WebChat` with module `Internal`, history types, master switch, tables, sweep), `docs/architecture.md` (webchat package, fan-out, widget package, `/chat/v1` static routes), `docs/compatibility.md` (Znuny shows chat messages like internal-note articles because the `WebChat` channel uses the `Internal` backend — expected, not a bug; `Admin::CommunicationChannel::Sync` caveat; Znuny's own chat engine not used), `docs/deployment.md` (REQUIRED for the web chat behind a reverse proxy: `TIQORA_PUBLIC_BASE_URL` (else every chat start is refused as cross-origin when TLS ends at the proxy) and `TIQORA_TRUSTED_PROXIES` + `TIQORA_FORWARDED_ALLOW_IPS` (else every visitor shares the proxy's rate-limit bucket); no nginx change needed: `/chat/` and `/api/chat/` go to `tiqora-api`; if the proxy adds `X-Frame-Options`, exempt `/chat/v1/frame`; `TIQORA_CHAT_MAX_STREAMS`, `TIQORA_WEBCHAT_DIST_DIR`; sizing numbers from the load script; recommended `proxy_buffering off` is already achieved by `X-Accel-Buffering: no`), `docs/gdpr.md` (chat tables in erasure, retention purge, IP only as keyed hash), `docs/i18n.md` (`webchatWidget.*` namespace is bundled into the widget), `docs/testing.md` (widget unit + e2e commands), `docs/api/rest-v1.md` (public chat API section + agent endpoints)
- Test: full suites

- [ ] **Step 1: Write the docs** (German UI names exactly as in the UI; example hosts `helpdesk.example.org`, `www.example.org`).
- [ ] **Step 2: Run the sizing script** once against a local stack (`docker-compose.local.yml`) with N = 200 and N = 500 streams, record the numbers (or "not measured" with the reason) in `docs/deployment.md`.
- [ ] **Step 3: Full verification.**
  ```bash
  cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q
  uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora
  cd ../frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs
  cd .. && pnpm --filter @tiqora/webchat-widget lint && pnpm --filter @tiqora/webchat-widget test && pnpm --filter @tiqora/webchat-widget build && pnpm --filter @tiqora/webchat-widget e2e
  cd backend && uv run tiqora openapi -o /tmp/oa.json && diff -q /tmp/oa.json ../packages/api-client/openapi.json
  ```
  All green; the openapi diff is empty. `rtk git status` shows no stray files (`.playwright-mcp`, `dist/`).
- [ ] **Step 4: Commit** (`docs(webchat): guide, architecture, deployment and GDPR notes for the web chat`).

## Self-review notes (for the executor)

- Spec coverage (phase 1): tables → T1; public API + SSE → T7/T8; widget launcher/chat/pre-chat/upload/offline → T14–T17; availability + accept + inbox → T5/T9/T20; messenger composer generalised → T19 (+ backend T10); admin editor → T12/T21; ticket/article mapping → T2/T6/T10; abuse protection (rate limits, origin/CSP, uploads) → T3/T4/T7/T13; tests incl. golden (T2), first-accept race (T9), SSE replay (T8), widget Vitest + Playwright on a second origin (T15–T17), load script (T22). Phase-2/3 items are listed in "Deviations" 4, 5, 10, 11, 13, 15, 18.
- Shared files other wave-2 plans may also touch: the migration head + `TIQORA_HEAD`, `db/tiqora/models.py`, `config.py`, `api/app.py` (router + middleware), `api/spa.py`, `api/v1/__init__.py`, `api/v1/admin/__init__.py`, `api/v1/tickets.py`, `api/v1/events.py` + `events/pubsub.py`, `domain/ticket_write_service.py`, `gdpr/erasure.py`, `ai/auto_worker.py`, `worker/services.py` + `worker/__main__.py` + `worker/poller.py` + `domain/settings_store.py`, `channels/registry.py`, `extensions` (only via `CHANNEL_CONFIG_KEYS`), `AgentShell.tsx`, `ArticleMasterDetail.tsx`, `TicketHeaderActions.tsx`, `ArticleQuickActions.tsx`, `ReplyDialog.tsx`, `useSSE.ts`, `router.tsx`, `adminSearch.ts`, `mockData.ts` (one line), all locale files, `Dockerfile`, `.github/workflows/ci.yml`, `pnpm-lock.yaml`, `.gitignore`.
