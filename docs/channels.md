# Additional channels: SMS, WhatsApp Business, Phone/CTI, Telegram

Beyond email (`channels/email/`, see the postmaster pipeline docs) and the
customer web portal, Tiqora ships four more `CommunicationChannel` plugins.
All four are **disabled by default** — they are integrations an operator
opts into, not something that activates on upgrade. Every plugin funnels
ticket/article writes through `domain/ticket_write_service` — never writes
tickets/articles directly — and shares building blocks from
`channels/common.py`:

- **`communication_channel` row registration** (`ensure_channel_row`) —
  SMS and WhatsApp get their own row (`SMS`, `WhatsApp`) on first use,
  copying the `channel_data` Storable blob from the built-in `Internal` row
  since Tiqora never writes real Perl `Storable` bytes and these channels
  are never rendered by Znuny's own UI. Phone reuses the built-in `Phone`
  row — no new channel to register.
- **Phone → `customer_user` resolution** (`resolve_customer_by_phone`) —
  matches `customer_user.phone`/`.mobile` against the inbound number using a
  normalized (digits-only) suffix match, tolerant of `+49`/`0`/spacing
  differences. Falls back to a per-channel `default_customer_user` setting
  when nothing matches.
- **Follow-up-or-create dispatch** (`resolve_ticket_for_inbound`) — reuses
  `znuny.followup.detect_followup` (the same `Ticket::Hook` subject/body tag
  scan the email pipeline uses) first; if that doesn't match, falls back to
  "most recent non-closed ticket for this `customer_user`" (a session-style
  continuity heuristic — SMS/WhatsApp replies rarely echo the ticket hook tag
  the way email subjects do). Only creates a new ticket if neither matches.

Config lives in `tiqora_settings` (key/value, Alembic-managed already —
no new migration), namespaced `channel.<name>.<key>`; see "Admin config"
below.

## SMS (`channels/sms/`)

- **Gateway abstraction**: `SmsGateway` protocol (`send(to, body)`); one
  concrete driver, `GenericHttpSmsGateway`, POSTs
  `{"to": ..., "body": ...}` as JSON to a configurable webhook URL,
  optionally HMAC-SHA256-signed (`X-Tiqora-Signature: sha256=<hex>`) with a
  shared secret. Point this at your aggregator's/gateway's outbound API (or
  an adapter in front of it).
- **Inbound**: `POST /api/v1/channels/sms/inbound`
  `{"from_number", "to_number"?, "body"}`, authenticated via
  `X-Tiqora-Sms-Secret` header (constant-time compared against
  `channel.sms.inbound_shared_secret`). Creates or follows up a ticket,
  appends an `SMS`-channel customer article.
- **Outbound**: `POST /api/v1/channels/sms/send` (agent session/API-key
  auth) `{"ticket_id", "to_number", "body"}` — appends an agent article,
  then delivers via the configured gateway.
- **Config keys** (`channel.sms.*`): `enabled`, `outbound_webhook_url`,
  `outbound_shared_secret`, `inbound_shared_secret`, `default_customer_user`,
  `queue_name`.

## WhatsApp Business (`channels/whatsapp/`)

Targets the Meta WhatsApp Cloud API (a WhatsApp Business app with a
phone-number-id and an access token — see Meta's
[Cloud API docs](https://developers.facebook.com/docs/whatsapp/cloud-api)).

- **Webhook verify**: `GET /api/v1/channels/whatsapp/webhook` handles Meta's
  subscription handshake (`hub.mode=subscribe`, `hub.verify_token`,
  `hub.challenge`) against `channel.whatsapp.verify_token`.
- **Inbound**: `POST /api/v1/channels/whatsapp/webhook`, HMAC-SHA256
  verified via `X-Hub-Signature-256` against `channel.whatsapp.app_secret`.
  Processes every message in `entry[].changes[].value.messages[]`; maps the
  sender's `wa_id` to a `customer_user` (same phone resolution as SMS).
  Media messages (`image`/`audio`/`video`/`document`/`sticker`) download via
  the Graph API media endpoint (`GET /{media-id}` → signed URL → content)
  and are stored as article attachments; the caption (if any) becomes the
  article body, otherwise a `[<type> attachment]` placeholder.
- **Outbound**: `POST /api/v1/channels/whatsapp/send`
  `{"ticket_id", "to", "body"}` (free-form text — only valid inside Meta's
  24h customer-service window) and
  `POST /api/v1/channels/whatsapp/send-template`
  `{"ticket_id", "to", "template_name", "language_code"}` (approved
  templates, needed to re-open a session outside that window).
- **Config keys** (`channel.whatsapp.*`): `enabled`, `phone_number_id`,
  `access_token`, `app_secret`, `verify_token`, `api_version` (default
  `v19.0`), `default_customer_user`, `queue_name`.

## Phone / CTI (`channels/phone/`)

The simplest plugin — no gateway, just a thin logging API over `add_article`
with the sender/history type Znuny already uses for calls
(`PhoneCallCustomer` for inbound, `PhoneCallAgent` for outbound), reusing
the built-in `Phone` communication channel.

- **Endpoint**: `POST /api/v1/channels/phone/note`
  `{"direction": "inbound"|"outbound", "caller_number", "note",
  "ticket_id"?, "subject"?, "agent_user_id"?}`, authenticated via
  `X-Tiqora-Phone-Secret` against `channel.phone.inbound_shared_secret` —
  intended for CTI integrations (Asterisk AMI/AGI hangup hooks) or a
  generic click-to-log button, both of which can hold a shared secret rather
  than a logged-in agent session.
  - With `ticket_id`: appends directly to that ticket.
  - Without: resolves the caller number to a `customer_user` and dispatches
    through the same follow-up-or-create logic as SMS/WhatsApp.
- **Config keys** (`channel.phone.*`): `enabled`, `inbound_shared_secret`,
  `default_customer_user`, `queue_name`.

## Telegram (`channels/telegram/`)

Talks to the [Telegram Bot API](https://core.telegram.org/bots/api). Unlike
SMS/WhatsApp/Phone, Telegram chat_ids are not looked up against
`customer_user` the way phone numbers are — an unmapped Telegram user
(the common case) is a genuinely anonymous chat until identified (see AI
identity verification below), so this plugin does **not** reuse
`resolve_ticket_for_inbound`/`resolve_customer_by_phone`. It keys ticket
continuity off the chat itself (see "Identity / contact mapping").

- **Transports** (`channel.telegram.mode`, default `"polling"`) — exactly
  one must be active for a given bot; running both double-processes every
  update:
  - **Polling**: the `telegram_poller` daemon (`worker/telegram_poller.py`,
    slug `telegram_poller`, gated by `daemon.telegram_poller.enabled` —
    default **OFF**, no Znuny counterpart) long-polls `getUpdates`.
    `getUpdates` is a **single-consumer** API — Telegram serves each update
    to only one caller and advances past it once acknowledged, with no
    redelivery. **Never run two worker replicas against the same bot
    token**: they would race for updates and each one would silently miss
    roughly half the traffic. The offset is persisted in
    `tiqora_settings` (`channel.telegram.update_offset`) and advances in
    the same transaction as the article/ticket write, so a per-update
    failure aborts the tick rather than skipping the message.
  - **Webhook**: `POST /api/v1/channels/telegram/webhook`, one Telegram
    update per request, authenticated via the
    `X-Telegram-Bot-Api-Secret-Token` header (constant-time compared
    against `channel.telegram.webhook_secret_token`). Requires
    `channel.telegram.mode = "webhook"` (409 otherwise, so the poller and
    the webhook route can't both be live by accident).
    `POST /api/v1/channels/telegram/webhook-register` (admin) calls
    Telegram's `setWebhook` with `channel.telegram.webhook_url` and the
    secret token; `.../webhook-unregister` calls `deleteWebhook`.
- **Identity / contact mapping**: every inbound message upserts a
  `tiqora_telegram_contact` row (`chat_id`, `telegram_user_id`, `username`,
  `display_name`, and — once mapped — `customer_user_login`). Until a
  contact is mapped, its ticket/article writes use the channel's
  `default_customer_user` setting; ticket continuity for an unmapped chat
  is instead tracked by matching the `<chat_id@telegram.invalid>` marker
  embedded in the `From` address of the chat's own prior articles (so two
  different anonymous Telegram users never get merged into one ticket).
  Once a contact *is* mapped (manually by an admin, or via the AI identity
  exchange — see [ai-integration.md](ai-integration.md)), normal
  `customer_user_id`-based ticket lookup applies.
- **GDPR consent**: `channel.telegram.consent_required` (default **ON**).
  Before anything else runs, an unconsented chat gets an inline "✅
  Zustimmen" button (`channel.telegram.consent_text`, re-prompted at most
  once per hour per chat); tapping it sets
  `tiqora_telegram_contact.consent_time` and sends
  `channel.telegram.consent_confirmed_text`. **Nothing is stored** for a
  chat before consent — no ticket, no article, not even the message body;
  only the identity fields already needed to show the prompt
  (`chat_id`/`telegram_user_id`/`username`/`display_name`) are upserted.
  The customer has to resend their message after consenting.
- **Outbound**: no separate `/send` endpoint — an agent's reply goes through
  the same `add_article` dispatch seam as every other channel
  (`channels/telegram/outbound.py::deliver_agent_telegram_reply`), send-then-store
  like the email outbound path: `sendMessage` first, article row only on
  success (a failed send never produces a false "sent" customer-visible
  note). The chat_id is resolved from the contact's mapping, falling back to
  parsing the most recent inbound Telegram article's `From` address.
- **`from_address` format**: `"{display_name or @username or chat_id}
  <{chat_id}@telegram.invalid>"` — the synthetic `@telegram.invalid` domain
  and the embedded `chat_id` are what both the ticket-continuity lookup and
  outbound chat_id resolution parse back out; never hand-construct or rely
  on this being a deliverable address.
- **Attachments**: photo/document/voice/video/sticker messages download via
  the Bot API `getFile` + file endpoint and are stored as article
  attachments; the caption (if any) becomes the article body, otherwise a
  placeholder (`[Foto]`, `[Dokument: name]`, ...). A download failure keeps
  the placeholder text and appends a note rather than losing the message.
- **`/start` = new dialog**: sends `channel.telegram.start_text` (`{first_name}`
  interpolated) and sets `tiqora_telegram_contact.new_dialog_since` — no
  ticket/article is created. Afterwards, `_resolve_ticket`'s per-chat
  continuity lookup (stage b) ignores any ticket without a Telegram article
  newer than `new_dialog_since`, and skips the customer-fallback lookup
  (stage c) entirely, so the next message always starts a fresh ticket even
  if an older one for the same chat is still open; the follow-up-tag lookup
  (stage a) still takes priority over a `/start` reset.
- **No email-style quoting on Telegram replies**: the reply-draft composer
  (`TicketService.get_reply_draft`) leaves the answer area empty instead of
  prefixing the Znuny-style `On <date>, <from> wrote:` quote when the
  based-on article's channel is Telegram — a chat reply isn't a quoted email.
- **Du-Anrede / chat tone for AI replies**: `channel.telegram.tone_prompt`
  (default: duze the customer by first name, no formal-letter phrasing) is
  appended to the AI system prompt whenever the run is Telegram-sourced —
  covers both the auto-trigger (`source_channel`) and Manual Assist (decided
  by the based-on/latest customer article's channel instead, since manual
  runs never set `source_channel`) — and to the identity-check exchange,
  which is Telegram-only by construction.
- **Config keys** (`channel.telegram.*`): `enabled`, `bot_token`, `mode`
  (`polling`|`webhook`), `webhook_url`, `webhook_secret_token`,
  `default_customer_user`, `queue_name`, `consent_required`, `consent_text`,
  `consent_confirmed_text`, `start_text`, `tone_prompt`.

### Known limitations

- **Znuny renders Telegram articles as quasi-internal.** Znuny's own UI has
  no concept of the `Telegram` communication channel Tiqora registers (same
  situation as SMS/WhatsApp — see "Uncertainties" below); if an operator
  still looks at tickets through Znuny's agent interface during parallel
  operation, Telegram articles show up looking internal-only even though
  Tiqora marks them customer-visible. Cosmetic in Znuny's UI, not a data
  problem — Tiqora's own UI renders them correctly.
- **Disable the target queue's Znuny autoresponder.** Telegram tickets are
  Tiqora-only (Znuny never receives them), so a Znuny autoresponder still
  configured on the queue Telegram tickets land in would never fire (Znuny
  never sees the ticket) — leave it disabled to avoid confusion, not because
  it would double-send.

## Admin config

`GET/PUT /api/v1/admin/channels` and `/api/v1/admin/channels/{sms,whatsapp,phone,telegram}`
(admin group required) read/write the `tiqora_settings` keys above.
`PUT` accepts `{"enabled": bool, "config": {...}}`; unknown config keys are
rejected (422) rather than silently written. `GET` responses mask any key
whose name contains `secret` or `token` (returned as `********`) — write the
same key again to rotate it, the old value is never echoed back.

## Uncertainties / simplifications

- **Follow-up heuristic**: the "most recent non-closed ticket for this
  customer" fallback is a pragmatic choice, not a port of any Znuny
  mechanism — SMS/WhatsApp/phone have no equivalent to
  `PostMaster::FollowUpCheck::References`. An operator running multiple
  concurrent conversations per customer on the same channel will want a
  smarter session/thread id scheme (e.g. WhatsApp's own conversation
  windows) — out of scope here.
- **Outbound delivery is synchronous** inside the `POST .../send*` request
  (no event-outbox-driven retry queue like `worker/webhooks.py`). Acceptable
  for agent-initiated single sends; a high-volume bulk-SMS/WhatsApp sender
  would want to move this to the outbox drain instead.
- **`communication_channel.channel_data`** is a Perl `Storable::nfreeze`
  blob Tiqora cannot construct; new rows reuse `Internal`'s bytes verbatim.
  Harmless as long as Znuny's own UI never renders SMS/WhatsApp articles
  (it doesn't — these channels are Tiqora-only).
