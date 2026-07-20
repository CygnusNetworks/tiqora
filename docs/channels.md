# Additional channels: SMS, WhatsApp Business, Phone/CTI

Beyond email (`channels/email/`, see the postmaster pipeline docs) and the
customer web portal, Tiqora ships three more `CommunicationChannel` plugins.
All three are **disabled by default** — they are integrations an operator
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

## Admin config

`GET/PUT /api/v1/admin/channels` and `/api/v1/admin/channels/{sms,whatsapp,phone}`
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
