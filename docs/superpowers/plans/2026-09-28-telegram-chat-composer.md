# Telegram Chat Composer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents answer Telegram tickets in a messenger-style composer inside the "Unterhaltung" view (variant A), with attachments, answer buttons, quote replies, edit/retract, a contact header and the "geht sofort" chat conveniences.

**Architecture:** A new per-article side table `tiqora_telegram_message` maps Tiqora articles to Telegram message ids (both directions) and carries buttons, edit/retract state and the quoted article. The gateway grows the missing Bot API calls; the outbound path, the inbound pipeline and the callback dispatcher write the map. Three small endpoints expose the chat (contact + per-article meta), agent typing, and edit/retract. The frontend adds a `TelegramChatComposer` below the conversation view for Telegram tickets and enriches the bubbles from the chat endpoint.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (backend), httpx Bot API client, React 18 + TypeScript + Tailwind + react-query + i18next (frontend), pytest (MariaDB testcontainer), vitest.

**Spec:** the user's choices in this session (artifact https://claude.ai/artifact/VymUbhfUYbfbW641sb7GSx, variant A + the listed extras) and these decisions:
- Edit keeps the article and changes its body; the first original is kept; the bubble shows "bearbeitet". Retract deletes the Telegram message but keeps the article, shown struck through as "zurückgezogen".
- Buttons: free labels per message plus the preset "Problem gelöst? Ja / Nein". A tap becomes a customer article with the button label; "Ja" (resolve_yes) also closes the ticket successfully, "Nein" (resolve_no) sets it open.
- Chat snippets are standard templates with `template_type = "Chat"`, assigned to queues like any template.

## Global Constraints

- Commit straight to `main`, no branches, no PRs, no GitHub. Push only when the user says so (`git push origin main` deploys production).
- Backend tests: `cd backend && uv run python -m pytest ...` (never bare `uv run pytest`). Before finishing: `TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q`, `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`.
- Frontend: never run prettier/biome. Gates: `pnpm --filter tiqora-frontend lint` (eslint + tsc), `pnpm --filter tiqora-frontend test`, `node frontend/scripts/check-i18n-keys.mjs`.
- New i18n keys go into `en.json` and `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs`. Never `scaffold-locale.mjs`.
- Any request/response model change: regenerate `packages/api-client/openapi.json` via `just api-client-gen`; never hand-edit `schema.d.ts`. A Pydantic field with a default becomes a required TS property.
- New migration: `backend/alembic/versions_tiqora/20260928_0050_telegram_message.py`, `down_revision = "20260925_0049"`; update `TIQORA_HEAD` in `tests/test_migration_gate.py` and `version_num` in `tests/test_migrate_cli.py`. Never touch `versions_owned/`. ORM and migration declare identical defaults (test DBs come from `metadata.create_all`).
- Telegram limits: text message 4096 characters; bot uploads up to 50 MB for documents, 10 MB for photos; bots may delete their own messages in private chats for 48 h; `callback_data` max 64 bytes.
- The bot sends plain text only (no `parse_mode`).
- Customer-facing copy on Telegram is informal German ("du"), matching the channel tone.

## Review Focus

- A customer taps a button twice (or two agents' keyboards race): only the first tap creates an article and changes state; later taps get a callback answer "Schon beantwortet" and nothing else. Owner: Task 5.
- A quote reply to a customer message that predates this feature (no map row): the reply is sent without `reply_parameters` instead of failing. Owner: Task 4.
- Retract after 48 h or of a message the customer already deleted: Telegram refuses; the API answers 409 with a readable reason and the article stays unchanged. Owner: Task 6.
- Attachment send fails after the text went out (or vice versa): no half-stored article; the agent sees an error and can retry. The plan sends attachments first and the text last, all before the insert. Owner: Task 4.
- An agent edits a message that has buttons: the edit keeps the keyboard unless it was already answered. Owner: Task 6.

---

## File Structure

Backend (under `backend/`):
- Create `alembic/versions_tiqora/20260928_0050_telegram_message.py`: the new table.
- Modify `src/tiqora/db/tiqora/models.py`: `TiqoraTelegramMessage` model next to `TiqoraTelegramContact`.
- Create `src/tiqora/channels/telegram/messages.py`: map-table helpers (record, lookup by article / by chat+message, button parsing, `ButtonSpec`).
- Modify `src/tiqora/channels/telegram/gateway.py`: `send_message(reply_to_message_id=)`, `send_photo`, `send_document`, `edit_message_text`, `edit_message_reply_markup`, `delete_message`.
- Modify `src/tiqora/channels/telegram/outbound.py`: attachments, quote, buttons, map row.
- Modify `src/tiqora/channels/telegram/service.py`: record inbound message ids + customer quotes; dispatch `tqb:` callbacks.
- Create `src/tiqora/channels/telegram/chat_actions.py`: edit, retract, chat info, typing (domain functions used by the API).
- Create `src/tiqora/api/v1/tickets_telegram.py`: `GET /tickets/{id}/telegram`, `POST /tickets/{id}/telegram/typing`, `PATCH /tickets/{id}/articles/{aid}/telegram`, `POST /tickets/{id}/articles/{aid}/telegram/retract`; register the router in the app.
- Modify `src/tiqora/api/v1/tickets.py`: `ArticleCreateRequest.attachments / telegram_reply_to_article_id / telegram_buttons`; templates `?type=`.
- Modify `src/tiqora/domain/ticket_write_service.py`: `ArticleIn.telegram` options; clean up map rows in `delete_article`.
- Modify `src/tiqora/domain/ticket_service.py`: `list_templates(..., template_type="Answer")`.
- Modify `src/tiqora/gdpr/erasure.py`: drop map rows (they carry `original_body`) for erased articles.
- Tests: `tests/test_telegram_messages.py` (new), `tests/test_channels_telegram.py` (extend), `tests/test_tickets_telegram_api.py` (new).

Frontend (under `frontend/src/`):
- Create `lib/telegramChatApi.ts`: typed wrappers + query keys.
- Create `components/agent/telegram/TelegramContactHeader.tsx`.
- Create `components/agent/telegram/TelegramChatComposer.tsx` (+ `useTypingPing.ts`, `ChatSnippetPicker.tsx`, `ButtonEditor.tsx`).
- Create `components/agent/telegram/TelegramBubbleExtras.tsx`: attachments inline, quote, buttons, edited/retracted, delivery tick, bubble actions.
- Modify `components/agent/ArticleConversationView.tsx`, `ArticleMasterDetail.tsx`, `TicketHeaderActions.tsx`, `ArticleQuickActions.tsx`, `ReplyDialog.tsx` (Telegram branch: no signature/tone/template), `routes/admin/TemplatesPage.tsx` (type "Chat").
- Extract `components/agent/replyNextState.ts` from `ReplyDialog.tsx` (NEXT_STATES + pending default) so both composers share it.
- Tests next to each component.

---

### Task 1: Map table `tiqora_telegram_message`

**Files:**
- Create: `backend/alembic/versions_tiqora/20260928_0050_telegram_message.py`
- Modify: `backend/src/tiqora/db/tiqora/models.py` (after `TiqoraTelegramContact`)
- Create: `backend/src/tiqora/channels/telegram/messages.py`
- Modify: `backend/src/tiqora/domain/ticket_write_service.py` (`delete_article`, next to the `tiqora_ai_article_origin` cleanup)
- Modify: `backend/src/tiqora/gdpr/erasure.py` (wherever articles of erased tickets are removed or anonymised: delete their map rows)
- Modify: `backend/tests/test_migration_gate.py`, `backend/tests/test_migrate_cli.py` (head literal)
- Test: `backend/tests/test_telegram_messages.py`

**Interfaces:**
- Produces:
  - Model `TiqoraTelegramMessage` (table `tiqora_telegram_message`): `article_id: BigInteger PK` (no FK, like `TiqoraAiArticleOrigin`), `ticket_id: BigInteger NOT NULL index`, `chat_id: BigInteger NOT NULL`, `message_id: BigInteger NULL`, `direction: String(3) NOT NULL` (`"in"`/`"out"`), `extra_message_ids: Text NULL` (JSON list of attachment message ids), `reply_to_article_id: BigInteger NULL`, `buttons_json: Text NULL`, `answered_button: Integer NULL`, `answered_at: DateTime NULL`, `edited_at: DateTime NULL`, `original_body: Text NULL`, `retracted_at: DateTime NULL`, `retracted_by: Integer NULL`, `created: DateTime server_default=func.now()`. Unique index `ux_tiqora_telegram_message_chat_msg (chat_id, message_id)`.
  - `messages.py`:
    ```python
    ButtonAction = Literal["reply", "resolve_yes", "resolve_no"]

    @dataclass(frozen=True, slots=True)
    class ButtonSpec:
        label: str
        action: ButtonAction = "reply"

    CALLBACK_PREFIX = "tqb:"

    def keyboard_for(buttons: list[ButtonSpec]) -> dict[str, Any]  # one button per row, callback_data f"tqb:{i}"
    def buttons_to_json(buttons: list[ButtonSpec]) -> str
    def buttons_from_json(raw: str | None) -> list[ButtonSpec]
    async def record_message(session, *, article_id: int, ticket_id: int, chat_id: int, message_id: int | None, direction: str, extra_message_ids: list[int] | None = None, reply_to_article_id: int | None = None, buttons: list[ButtonSpec] | None = None) -> TiqoraTelegramMessage
    async def get_by_article(session, article_id: int) -> TiqoraTelegramMessage | None
    async def get_by_message(session, chat_id: int, message_id: int) -> TiqoraTelegramMessage | None
    async def list_for_ticket(session, ticket_id: int) -> list[TiqoraTelegramMessage]
    ```

- [ ] **Step 1: Write the failing tests** in `tests/test_telegram_messages.py` (`pytestmark = pytest.mark.db`, `mariadb_znuny_url`, `create_all`, clean up own rows with article ids in the 97_500_000 range):
  - `test_record_and_lookup_by_article_and_message`: record out row (article 97500001, chat 9701, message 55, buttons `[ButtonSpec("Ja","resolve_yes"), ButtonSpec("Nein","resolve_no")]`) → `get_by_article` and `get_by_message(9701, 55)` return it; `buttons_from_json(row.buttons_json)` round-trips.
  - `test_keyboard_for_uses_index_callback_data`: pure unit, `keyboard_for([...2 buttons])["inline_keyboard"] == [[{"text":"Ja","callback_data":"tqb:0"}],[{"text":"Nein","callback_data":"tqb:1"}]]`.
  - `test_buttons_from_json_tolerates_garbage`: `buttons_from_json("not json") == []`, `buttons_from_json(None) == []`, unknown action → `"reply"`.
  - `test_delete_article_removes_map_row`: seed an internal note article via the existing helpers in `tests/test_ticket_write_service*` (reuse their seed pattern), record a map row for it, call domain `delete_article` → `get_by_article` is `None`.
- [ ] **Step 2:** Run `uv run python -m pytest tests/test_telegram_messages.py -q` → FAIL (import error for `messages`).
- [ ] **Step 3:** Implement model, migration (mirror `20260925_0049_ai_audit_mediumtext.py` style; `op.create_table` + unique index; downgrade drops), `messages.py`, the `delete_article` cleanup (guarded with `_table_exists` exactly like `tiqora_ai_article_origin`), and the erasure cleanup. Update the two head literals.
- [ ] **Step 4:** Run the new tests plus `tests/test_migration_gate.py tests/test_migrate_cli.py` → PASS.
- [ ] **Step 5:** Commit `feat(telegram): map table between articles and Telegram messages`.

### Task 2: Gateway calls

**Files:**
- Modify: `backend/src/tiqora/channels/telegram/gateway.py`
- Test: `backend/tests/test_channels_telegram.py` (new section "gateway: media, edit, delete")

**Interfaces:**
- Produces (all raise `TelegramApiError`, token-scrubbed):
  ```python
  async def send_message(self, chat_id, text, *, reply_markup=None, reply_to_message_id: int | None = None) -> dict
      # reply_to_message_id -> payload["reply_parameters"] = {"message_id": id, "allow_sending_without_reply": True}
  async def send_photo(self, chat_id, content: bytes, filename: str, *, caption: str | None = None, reply_to_message_id: int | None = None) -> dict
  async def send_document(self, chat_id, content: bytes, filename: str, content_type: str, *, caption: str | None = None, reply_to_message_id: int | None = None) -> dict
      # both multipart: data={"chat_id": str(chat_id), ...}, files={"photo"|"document": (filename, content, content_type)}
  async def edit_message_text(self, chat_id, message_id: int, text: str, *, reply_markup: dict | None = None) -> None
  async def edit_message_reply_markup(self, chat_id, message_id: int, reply_markup: dict | None) -> None  # None -> {"inline_keyboard": []}
  async def delete_message(self, chat_id, message_id: int) -> None
  ```
  Add `_call_multipart(method, data, files)` next to `_call`, sharing the ok/description handling.

- [ ] **Step 1: Failing tests** with the existing `MockTransport` recorder (`_recording_gateway`, extend it to capture multipart bodies via `request.headers["content-type"]` and `request.content`):
  - `send_message(..., reply_to_message_id=7)` posts `reply_parameters == {"message_id": 7, "allow_sending_without_reply": True}`.
  - `send_photo` posts to `.../sendPhoto` as `multipart/form-data` containing the filename and the bytes.
  - `send_document` → `.../sendDocument`, same checks.
  - `edit_message_text`, `edit_message_reply_markup(…, None)` (payload `reply_markup == {"inline_keyboard": []}`), `delete_message` post the right method and ids.
  - A `{"ok": false, "description": "message can't be deleted for everyone"}` response raises `TelegramApiError` with that description.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(telegram): gateway calls for media, quotes, edit and delete`.

### Task 3: Inbound records message ids and customer quotes

**Files:**
- Modify: `backend/src/tiqora/channels/telegram/service.py` (`process_update`, after `add_article`)
- Test: `backend/tests/test_channels_telegram.py`

**Interfaces:**
- Consumes: `record_message`, `get_by_message` (Task 1).
- Produces: every inbound customer article gets a map row `direction="in"`, `message_id = message["message_id"]`; if `message["reply_to_message"]["message_id"]` maps to a known row in the same chat, `reply_to_article_id` is that row's `article_id`.

- [ ] **Step 1: Failing tests** (extend `_text_message` to take `message_id` and optional `reply_to_message_id`; add `tiqora_telegram_message` to `_WRITE_TABLES`):
  - text message id 101 → map row with `message_id=101`, `direction="in"`, right `ticket_id`.
  - a second message quoting 101 → its row has `reply_to_article_id` = first article.
  - quoting an unknown id → `reply_to_article_id is None`, article still created.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(telegram): remember inbound message ids and quotes`.

### Task 4: Outbound: attachments, quote, buttons, map row

**Files:**
- Modify: `backend/src/tiqora/domain/ticket_write_service.py` (`ArticleIn`: add `telegram: TelegramSendOptions | None = None`)
- Modify: `backend/src/tiqora/channels/telegram/outbound.py`
- Modify: `backend/src/tiqora/api/v1/tickets.py` (`ArticleCreateRequest`, `create_article`)
- Test: `backend/tests/test_channels_telegram.py`, `backend/tests/test_tickets_telegram_api.py`

**Interfaces:**
- Produces:
  ```python
  # ticket_write_service.py
  @dataclass(frozen=True, slots=True)
  class TelegramSendOptions:
      reply_to_article_id: int | None = None
      buttons: tuple[ButtonSpec, ...] = ()
  ```
  ```python
  # tickets.py
  class ArticleAttachmentIn(BaseModel):
      filename: str = Field(min_length=1, max_length=250)
      content_type: str = "application/octet-stream"
      content_base64: str
  class TelegramButtonIn(BaseModel):
      label: str = Field(min_length=1, max_length=64)
      action: Literal["reply", "resolve_yes", "resolve_no"] = "reply"
  # ArticleCreateRequest gains:
      attachments: list[ArticleAttachmentIn] = Field(default_factory=list, max_length=10)
      telegram_reply_to_article_id: int | None = None
      telegram_buttons: list[TelegramButtonIn] = Field(default_factory=list, max_length=8)
  ```
  An empty `subject` on a Telegram reply is replaced by the ticket title (Telegram has no subject; Znuny needs one).
  Rules in `create_article`: attachments, reply-to and buttons are only valid with `channel == "telegram"` → otherwise 422 `"attachments/buttons/quotes are only supported for Telegram replies"`. Decoded total > 50 MB → 422. Bad base64 → 422.
- Outbound order in `deliver_agent_telegram_reply`: resolve chat → resolve quote (`get_by_article(reply_to_article_id)`; use its `message_id` only if same chat) → send each attachment (`image/jpeg|png|webp` ≤ 10 MB → `send_photo`, else `send_document`), collecting message ids → send text (with keyboard if buttons, quote only on the first sent message) → `add_article` with the attachments stored on the article → `record_message(direction="out", message_id=<text msg>, extra_message_ids=<attachment msgs>, buttons=...)`. Empty body with attachments is allowed (then the last attachment carries the keyboard/quote and is the row's `message_id`; body stored as `[Anhang]`). If any send fails, best-effort `delete_message` the already-sent parts, then raise `TelegramDeliveryError`.

- [ ] **Step 1: Failing tests** (gateway fake recording calls; pattern `_FakeTelegramGateway`, extend with the new methods):
  - text + 2 buttons → `sendMessage` carries `reply_markup` with `tqb:0/1`; map row has buttons.
  - photo + document + text → calls in order sendPhoto, sendDocument, sendMessage; article has 2 attachments; row `extra_message_ids` has 2 ids.
  - quote of a mapped customer article → `reply_parameters.message_id` = that message id; quote of an unmapped article → no `reply_parameters`, still sent.
  - text send fails after a photo went out → `delete_message` called for the photo, `TelegramDeliveryError`, no article row.
  - API: `POST /tickets/{id}/articles` with `channel="email"` and attachments → 422; with bad base64 → 422.
  - API: Telegram reply with `subject=""` → stored article subject is the ticket title.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(telegram): send attachments, buttons and quote replies`.

### Task 5: Button callbacks

**Files:**
- Modify: `backend/src/tiqora/channels/telegram/service.py` (`process_update` / new `_handle_button_callback`)
- Test: `backend/tests/test_channels_telegram.py`

**Interfaces:**
- Consumes: `get_by_message`, `buttons_from_json`, `CALLBACK_PREFIX`.
- Behaviour: `callback_query.data` starting with `tqb:` → row = `get_by_message(chat_id, callback.message.message_id)`; unknown row/index → answer "Diese Auswahl ist nicht mehr gültig." and skip. Already answered (`answered_at` set) → answer "Schon beantwortet 👍" and skip. Otherwise: `add_article` customer article on `row.ticket_id`, body = label, subject = ticket title, `from_address` like inbound, `channel="telegram"`, record `direction="in"` row with `reply_to_article_id=row.article_id` and `message_id=None`; set `answered_button`, `answered_at`; `resolve_yes` → state `closed successful`, `resolve_no` → state `open` (look up state ids by name; use the domain `change_state` with `user_id=1`); `answer_callback_query(id, "Danke!")`; `edit_message_reply_markup(chat, msg, None)` best-effort. Consent callback keeps working unchanged.

- [ ] **Step 1: Failing tests:** tap "Ja" → customer article "Ja", ticket state closed successful, keyboard removed; second tap → no new article, answer text "Schon beantwortet 👍"; tap "Nein" on a pending ticket → state open; unknown message → skipped with answer; consent callback test still passes.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(telegram): answer buttons with resolve shortcuts`.

### Task 6: Edit, retract, chat info, typing endpoints

**Files:**
- Create: `backend/src/tiqora/channels/telegram/chat_actions.py`
- Create: `backend/src/tiqora/api/v1/tickets_telegram.py` (+ register in the v1 router where `tickets.router` is included)
- Test: `backend/tests/test_tickets_telegram_api.py`

**Interfaces:**
- Produces:
  ```python
  class TelegramMessageMeta(BaseModel):
      article_id: int; direction: Literal["in", "out"]; reply_to_article_id: int | None
      buttons: list[TelegramButtonIn]; answered_button: int | None
      edited_at: datetime | None; retracted_at: datetime | None
  class TelegramChatOut(BaseModel):
      chat_id: int; username: str | None; display_name: str | None
      identity_verified: bool; customer_user_login: str | None
      consent_time: datetime | None; ai_escalated_at: datetime | None
      messages: list[TelegramMessageMeta]
  GET  /api/v1/tickets/{ticket_id}/telegram                          -> TelegramChatOut   (ro; 404 when the ticket has no Telegram chat)
  POST /api/v1/tickets/{ticket_id}/telegram/typing                   -> 204               (note; sends sendChatAction typing, best-effort)
  PATCH /api/v1/tickets/{ticket_id}/articles/{article_id}/telegram   body {body: str (1..4096)} -> 204 (note)
  POST /api/v1/tickets/{ticket_id}/articles/{article_id}/telegram/retract -> 204 (note)
  ```
  Edit: only `direction="out"`, not retracted, has `message_id`; `edit_message_text` (keeps keyboard when `buttons_json` and not answered); store `original_body` once, `edited_at`; `UPDATE article_data_mime SET a_body` for the article; history row `Misc` `%%TelegramEdited%%<aid>`. Retract: `delete_message` for `message_id` and each `extra_message_ids`; on `TelegramApiError` → 409 with the Telegram description; set `retracted_at/by`; history `%%TelegramRetracted%%<aid>`. Customer articles or foreign tickets → 409 / 404.

- [ ] **Step 1: Failing API tests** (httpx `AsyncClient` against the app, agent auth fixture as in `tests/test_ticket_zoom_db.py`; fake gateway patched via `tiqora.channels.telegram.outbound.build_gateway`): chat info returns contact + per-article meta; edit updates body and meta; edit of a customer article → 409; retract success; retract when gateway raises → 409 and article unchanged; typing → 204 and one `sendChatAction`; user without note permission → 403.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(telegram): edit, retract, chat info and typing endpoints`.

### Task 7: Chat templates and API client

**Files:**
- Modify: `backend/src/tiqora/domain/ticket_service.py` (`list_templates(user_id, ticket_id, template_type="Answer")`)
- Modify: `backend/src/tiqora/api/v1/tickets.py` (`GET /{ticket_id}/templates?type=Answer|Chat`, `Literal`)
- Modify: `frontend/src/routes/admin/TemplatesPage.tsx` (`{ value: "Chat", label: "Chat" }`)
- Regenerate: `packages/api-client/openapi.json` (`just api-client-gen`)
- Modify: `packages/api-client/src/client.ts` (new methods + `listTemplates(ticketId, type?)` + extended `createArticle` payload type)
- Test: backend template test next to the existing `list_templates` tests; `frontend/src/routes/admin/TemplatesPage.test.tsx`

**Interfaces:**
- Produces in `ApiClient`: `getTelegramChat(ticketId)`, `postTelegramTyping(ticketId)`, `editTelegramMessage(ticketId, articleId, body)`, `retractTelegramMessage(ticketId, articleId)`, `listTemplates(ticketId, type?: "Answer" | "Chat")`; types `TelegramChatOut`, `TelegramMessageMeta`, `TelegramButtonIn`, `ArticleAttachmentIn`.

- [ ] Steps: failing test (Chat template listed only with `type=Chat`; default still Answer) → implement → regen → `pnpm --filter tiqora-frontend lint` green → commit `feat(templates): chat snippets as template type Chat`.

### Task 8: Frontend chat data, header and bubble extras

**Files:**
- Create: `frontend/src/lib/telegramChatApi.ts` (`telegramChatKey(ticketId) = ["tickets", id, "telegram"]`, `useTelegramChat(ticketId, enabled)`)
- Create: `frontend/src/components/agent/telegram/TelegramContactHeader.tsx` (name, @username, badges: Identität bestätigt / nicht bestätigt, Einwilligung, KI übergeben)
- Create: `frontend/src/components/agent/telegram/TelegramBubbleExtras.tsx`
- Modify: `frontend/src/components/agent/ArticleConversationView.tsx` (Telegram tickets: header on top, extras in bubbles, auto-scroll to bottom when the newest article id changes, attachments inline via `AttachmentList`)
- Modify: `frontend/src/i18n/locales/en.json`, `de.json` (+ propagate)
- Test: `TelegramContactHeader.test.tsx`, `TelegramBubbleExtras.test.tsx`, extend `ArticleConversationView` tests

**Behaviour:** bubble shows quoted snippet (first 80 chars of the quoted article, fetched from the loaded list/body cache), images inline, buttons as pills under agent messages (answered one highlighted), "bearbeitet" label, retracted body struck through with "zurückgezogen", a ✓ for delivered outbound Telegram messages. Hover actions: Zitieren (all Telegram bubbles), Bearbeiten and Zurückziehen (own-channel agent messages with a map row, not retracted; retract asks inline "Wirklich zurückziehen?" – no `confirm()`). Actions call the Task 6 endpoints and invalidate `["tickets", id, "articles"]` and `telegramChatKey`.

- [ ] Steps: failing component tests (render with fixture articles + chat meta; assert labels, strike-through, pills, action calls) → implement → `pnpm --filter tiqora-frontend test -- telegram` green → commit `feat(ui): Telegram contact header and chat bubble extras`.

### Task 9: TelegramChatComposer

**Files:**
- Create: `frontend/src/components/agent/replyNextState.ts` (moved from `ReplyDialog.tsx`: `NEXT_STATES`, pending default, `useNextStateOptions(ticketId)`); `ReplyDialog.tsx` imports it.
- Create: `frontend/src/components/agent/telegram/TelegramChatComposer.tsx`, `useTypingPing.ts`, `ChatSnippetPicker.tsx`, `ButtonEditor.tsx`
- Modify: `frontend/src/components/agent/ArticleMasterDetail.tsx` (render the composer instead of nothing when `view === "conversation"` and the ticket's dominant channel is Telegram and `canNote`; the note composer stays)
- Test: `TelegramChatComposer.test.tsx`

**Behaviour:**
- Textarea grows to 6 lines; Enter sends, Shift+Enter newline; IME composition ignored.
- Counter `n / 4096`; over the limit the send button is disabled and the counter turns red.
- `/` at the start of a word opens `ChatSnippetPicker` (templates `type=Chat`); choosing inserts the text at the caret.
- Attach button + paste/drag of files → chips with name/size and remove; > 50 MB total rejected with a message; sent as base64 `attachments`.
- Quote: set from bubble action (Task 8, via a small context or prop callback) → chip "Antwort auf: …" with ×; sent as `telegram_reply_to_article_id`.
- Buttons: "Buttons" toggle → `ButtonEditor` with preset "Problem gelöst? Ja / Nein" (fills body "Ist dein Problem damit gelöst?" if empty and buttons `[Ja→resolve_yes, Nein→resolve_no]`) and free labels (max 8, 64 chars).
- Next state segment "danach: offen lassen / wartend / schließen" (rw only), same payload as ReplyDialog.
- KI-Vorschlag bar when an open AI draft exists (`ticketAiApi.getState`): "Übernehmen" fills the text and sets `ai_draft_id`.
- Typing: `useTypingPing` calls `postTelegramTyping` at most every 4 s while the text changes; reports composing via `onComposingChange`.
- Composer lock: reuse `useComposerLock(ticketId, "compose", hasText)` and show `ComposerLockBanner`.
- Failure: the text and attachments stay, a red line shows the server detail and "Erneut senden".
- Draft autosave through `lib/replyDrafts.ts` like ReplyDialog (key per ticket).
- Payload: `{sender_type:"agent", subject: "", body, channel:"telegram", is_visible_for_customer:true, attachments, telegram_reply_to_article_id, telegram_buttons, ai_draft_id, state_id?, pending_time?}`; the backend fills the subject (Task 4: empty subject → ticket title).

- [ ] Steps: failing tests (Enter sends / Shift+Enter not; counter blocks at 4097; snippet insert; attachment payload base64; quote id in payload; preset buttons payload; failure keeps text) → implement → green → commit `feat(ui): Telegram chat composer in the conversation view`.

### Task 10: Entry points, ReplyDialog quick fix, docs

**Files:**
- Modify: `frontend/src/components/agent/TicketHeaderActions.tsx` ("Antworten" on a Telegram ticket switches to the conversation view and focuses the composer instead of opening ReplyDialog)
- Modify: `frontend/src/components/agent/ArticleQuickActions.tsx` (Telegram articles: "Antworten" focuses the composer with a quote)
- Modify: `frontend/src/components/agent/ReplyDialog.tsx` (Telegram branch: hide signature preview, template picker, refine controls; subject hidden)
- Modify: `docs/ai-integration.md` or the Telegram channel doc (where the channel is described) — short section "Antworten im Chat"
- Test: extend `ReplyDialog.test.tsx` (Telegram: no signature preview), `TicketHeaderActions` test

- [ ] Steps: failing tests → implement → green → commit `feat(ui): route Telegram replies to the chat composer`.

### Task 11: Verification and release

- [ ] Backend gates: `TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q`, ruff check, ruff format --check, mypy.
- [ ] Frontend gates: `pnpm --filter tiqora-frontend lint`, `pnpm --filter tiqora-frontend test`, `node frontend/scripts/check-i18n-keys.mjs`, `pnpm --filter tiqora-frontend build`.
- [ ] Final whole-change review (fresh reviewer on the full diff since the plan's first commit).
- [ ] Ask the user before `git push origin main`.
