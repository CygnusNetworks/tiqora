# Web chat channel + embeddable widget — design

> Part of the [competitive parity roadmap](2026-10-08-competitive-parity-roadmap-design.md).
> Status: proposed 2026-10-08. Decisions below are fixed unless marked "open".

## Goal

A website chat that beats Zammad's (the open-source reference) and gets
close to Zendesk Messaging: live chat with agents, AI first line with
human handoff, offline fallback, file upload, identified visitors, and a
conversation that continues by email or in the portal after the chat ends.
Everything lands in normal tickets, so SLA, reporting, AI and Znuny keep
working.

"Separate client" means: a **separate, small embeddable bundle**
(`packages/webchat-widget`) that customers put on their website, served by
`tiqora-api`. Not a separate product, repo or server.

## Current state (2026-10-08)

- No web chat. `webchat` exists only as a reserved channel value in
  `LIST_CHANNELS` (`domain/ticket_service.py`), mapped to Znuny's seeded
  `Chat` communication channel (`Kernel::System::CommunicationChannel::Chat`).
- Messenger pattern already built for Telegram: `tiqora_telegram_message`
  (article ↔ message id, buttons, edit/retract), composer in the conversation
  view (Enter to send, split send button, `/` chat templates, attachments up to
  18 MB, quote replies, answer buttons incl. "Problem gelöst? Ja/Nein",
  typing indicator) — `api/v1/tickets_telegram.py`,
  plan `docs/superpowers/plans/2026-09-28-telegram-chat-composer.md`.
- Inbound mapping pattern: `channels/common.py::ensure_channel_row`,
  `resolve_ticket_for_inbound` (TN tag → newest open ticket of the customer →
  new ticket), articles through `TicketWriteService.add_article`.
- Realtime: SSE `GET /events/stream` relaying Redis pub/sub `tiqora:events`
  to **agents only**; agent presence (`presence_changed`) exists.
- Auto-reply/AI: per-queue policies, handoff, pause, caps, budgets, PII
  masking; Telegram is already an exempt "Tiqora-only channel" for auto-reply.
- Production nginx: `proxy_read_timeout 90s` on `/api/` (Puppet-managed).

## What competitors offer (research 2026-10-08)

**Zammad 7 chat**: embed snippet (with/without jQuery), options (chatId,
title, fontSize, css, flat, lang, buttonClass …), several chats/topics,
agents choose topics, per-agent concurrent-session limit, widget only shown
when an agent with capacity is online, waiting list with position, typing
indicator both ways, idle/inactive/waiting timeouts, blocked IPs and
countries, designer with device preview, greeting text, chat → ticket on
close, searchable archive. **Missing in Zammad:** pre-chat form, file
upload, offline fallback (widget disappears; site must use the separate Form
channel), chat in mobile view.

**Zendesk Messaging** (best in class): persistent asynchronous conversations,
AI agent first, proactive messages (page/time/URL/visitor/language rules),
after-hours options (AI, hide, block escalation, create ticket).

## Parity checklist (Zammad level)

- [ ] Embeddable widget, configurable look, several chat configurations ("Chats") with topics
- [ ] Agent availability, accept/decline, per-agent concurrent limit, topic selection
- [ ] Waiting list with position, typing indicator both ways, timeouts
- [ ] Greeting, block lists (IP, country)
- [ ] Chat → ticket, transcript searchable

## Beyond parity ("besser als")

1. **AI first line** using the existing auto-reply machinery (KB answers,
   PII masking, budgets, handoff rules); visible "Mit einem Menschen
   sprechen" at any time; agent sees the bot conversation when taking over.
2. **Never a dead end**: outside hours or with no free agent the same widget
   switches to AI-only or to an offline message that becomes a ticket; the
   reply goes out by email and the visitor can continue in the portal.
3. **Pre-chat form** with dynamic fields (reusing the typed field engine),
   **file upload** (image paste + files, size/type limits).
4. **Identified visitors**: logged-in portal customers are recognised
   automatically; websites can pass a signed identity token so a chat is
   attached to the right `customer_user` without asking.
5. **Persistent conversation**: the visitor can close the tab and return; the
   history reloads; replies after the visitor left are sent by email.
6. **Live translation** (phase 3): visitor writes Spanish, agent reads and
   writes German, both see the original on demand — uses the existing reply
   language detection and LLM routing.
7. **Agent assist in chat**: suggested replies (drafts) per incoming message,
   `/` templates, KB search inline — reuse composer + drafts.
8. **Proactive invitations** (phase 3): rules on URL, time on page,
   returning visitor, language.
9. **Accessibility and privacy**: WCAG 2.2 AA widget, no third-party
   requests, no tracking cookies, IP addresses stored only as salted hash,
   per-chat retention through the GDPR retention engine.

## Fixed decisions

### Ticket and article model

- One chat session = one ticket (new or follow-up). The ticket is created at
  the **first visitor message** (not at agent accept) so nothing is lost and
  SLA starts correctly; queue from the chat's topic configuration (or triage
  if the chat has "KI-Zuordnung" enabled).
- **One article per message** on the Znuny `Chat` communication channel
  (same pattern as Telegram, so Znuny shows the conversation and the AI sees
  normal articles). Sender type `customer` / `agent` / `system` (bot
  messages as `agent` with the AI system user, like auto-replies today).
  Optional "Transkript als ein Artikel beim Beenden" setting for sites that
  prefer Znuny's old style — off by default.
- History types: reuse the Telegram mapping (`WebRequestCustomer` /
  `FollowUp` / `SendAnswer`) — Znuny has no chat history types.
- `ensure_channel_row` is not needed: `Chat` is seeded by Znuny; verify on
  6.0 peers (it is part of the OTRS 6 article rewrite) and fall back to the
  Telegram approach if missing.

### Tables (`tiqora_chat_*`)

- `tiqora_chat_config` — one per embeddable chat: `name, public_key,
  allowed_origins[], topics JSON (label, queue_id, priority, AI on/off),
  greeting, offline_mode (form|ai|hide), business_hours (Znuny calendar id),
  prechat_fields JSON, upload_policy, theme JSON, block_ips[],
  block_countries[], identity_secret (encrypted), retention_days, valid`.
- `tiqora_chat_session` — `id (uuid), config_id, topic, ticket_id,
  customer_user_login | null, visitor_id (random, per browser), visitor_name,
  visitor_email, status (bot|waiting|active|ended|abandoned), assigned_user_id,
  participants JSON, waiting_since, accepted_at, ended_at, end_reason,
  page_url, user_agent, ip_hash, country, rating, rating_comment, language`.
- `tiqora_chat_message` — `id, session_id, seq, sender (visitor|agent|bot|system),
  user_id, body (plain text), attachment ids, article_id, created, read_at,
  edited/retracted flags`. `seq` is the SSE event id.
- `tiqora_chat_agent_state` — per agent: `available_for_chat, topics[],
  max_concurrent, active_count` (presence exists; this adds chat capacity).

### Transport: SSE + POST, reconnect-tolerant

- Visitor: `GET /api/chat/v1/sessions/{id}/stream` (SSE), `POST
  …/messages`, `POST …/typing`, `POST …/attachments`, `POST …/end`,
  `POST …/rating`. Session creation `POST /api/chat/v1/sessions` with the
  chat's `public_key` + `Origin` check.
- Heartbeat comment every 25 s; `Last-Event-ID` = last `seq` → replay missed
  messages from `tiqora_chat_message` on reconnect. Works with the 90 s nginx
  timeout and with mobile network changes without any nginx change.
  Recommended nginx tweak for production anyway: `proxy_buffering off` for
  `/api/chat/` (Puppet change, coordinated with ops — see the deploy memory).
- Fan-out through Redis pub/sub channel `tiqora:chat:{session}`; agent side
  uses the existing `/events/stream` with new event types `chat_waiting`,
  `chat_message`, `chat_typing`, `chat_assigned`, `chat_ended`.
- Alternative recorded: WebSockets. Rejected — new infrastructure, proxy
  config everywhere, no benefit at chat message rates; SSE is already proven
  in Tiqora.

### Widget (`packages/webchat-widget`)

- Loader script `/chat/v1/loader.js` (< 5 KB) creates a launcher button and
  an **iframe** pointing at `/chat/v1/frame?config=…` on the Tiqora origin.
  Iframe = CSS and JS isolation from the host page, strict CSP on our side,
  our cookies/storage stay on our origin (partitioned per top-level site by
  modern browsers — acceptable, the visitor id is per site anyway).
- Frame app: Preact + TypeScript, built with Vite as a separate entry,
  < 40 KB gzipped, own i18n bundle from the same locale files (subset of
  keys), RTL support, light/dark/auto, themable colours and launcher position.
- Host page API: `Tiqora.chat.open()`, `.close()`, `.identify(token)`,
  `.setTopic()`, events `onReady`, `onOpen`, `onMessage`, `onAvailability`
  (Zammad callback parity) via `postMessage` with origin checks.
- Embed snippet and live preview generated in the admin UI.
- Versioned URLs (`/chat/v1/…`) with long cache + content hash for assets.

### Visitor identity

- Anonymous: random `visitor_id` in iframe storage; name/email asked in the
  pre-chat form or lazily ("Wohin dürfen wir antworten, falls Sie offline
  gehen?").
- Portal customers: if a portal session cookie exists on the same Tiqora
  origin, the frame attaches the session's `customer_user`.
- Website-signed identity: host backend signs `{sub|email, name, exp}` with
  the chat's `identity_secret` (HS256 JWT); Tiqora links to `customer_user` by
  email/login, never creates one implicitly unless the chat allows it.
- Email-only visitors are matched to `customer_user` by email like inbound mail.

### Routing and agent work

- Agent availability toggle "Chat annehmen" + topics + max concurrent
  (default 3) in the agent menu; offered chats ring for eligible agents
  (sound + desktop notification), first accept wins (atomic DB update).
- Waiting list: position shown to visitor, timeout → offline mode.
- Transfer to agent or topic, invite a colleague (multi-agent), internal
  notes (normal internal articles), end chat, "Ticket offen lassen" vs. close.
- Agent UI: chat inbox panel (global, from the agent shell) + the existing
  ticket conversation view with the Telegram composer generalised to a
  channel-agnostic "messenger composer" (`chat`, `telegram`, later WhatsApp).
- Lock/owner: accepting a chat sets owner + lock the Znuny way (existing
  lock/owner parity code).

### Bot / AI first line

- Per topic: `ai_mode = off | first_line | assist_only`.
- `first_line`: the existing auto-reply pipeline answers within the chat
  (streamed), honours caps/budgets/pause, PII masking, KB grounding and the
  terminal "handoff" decision; a handoff puts the session in `waiting` with
  the AI's summary as an internal note for the agent.
- Visitor can always request a human; when no human is available the bot
  says so and offers email follow-up — no fake promises.
- Auto-reply on this channel is allowed in `parallel` operation mode (same
  exemption as Telegram: Znuny cannot act on chat anyway).

### Abuse protection

- Rate limits per IP hash and per visitor (session create, messages,
  uploads) in Redis; block lists (IP/CIDR, country via optional GeoLite2
  file — no external call); optional proof-of-work challenge on session
  create when the chat sees a burst (no third-party captcha).
- Message body plain text, max length, linkified on render; attachments
  through the existing upload pipeline (type sniffing, size cap), served with
  `Content-Disposition: attachment`.
- `Origin` must be in `allowed_origins`; frame sets
  `Content-Security-Policy: frame-ancestors <allowed origins>`.

## Admin UI

**Kanäle → Webchat**: list of chats; editor tabs *Allgemein* (name,
origins, topics → queues), *Zeiten & Offline*, *Vorab-Formular*, *KI*,
*Aussehen* (live preview, desktop/mobile), *Schutz* (rate limits, block
lists), *Einbetten* (snippet, identity secret, test page link).

## Reporting hooks

New semantic source `chat_sessions`: chats offered/accepted/missed, wait
time P50/P90, handle time, bot-resolved rate, handoff rate, rating,
by topic/agent/hour.

## Tests

- Backend: session lifecycle, first-accept race, SSE replay with
  `Last-Event-ID`, origin/CSP checks, rate limits, identity token validation,
  ticket/article writes golden-checked against a Znuny peer (article visible
  in Znuny with the `Chat` channel).
- Widget: Vitest + Playwright on a test host page (different origin) —
  open, chat, reconnect after network drop, offline form, upload, RTL.
- Load: 500 concurrent visitor streams against one API worker (SSE
  connection cost) — sets the documented sizing.

## Phases

| Phase | Content | Size |
|---|---|---|
| 1 | Tables, public API + SSE, widget (launcher, chat, pre-chat form, upload, offline form), agent availability + accept + inbox, messenger composer generalised, admin editor, ticket/article mapping | 3–4 weeks |
| 2 | AI first line + handoff, identified visitors (portal + signed token), transfer/invite, waiting list, block lists, rating, reporting source | 2–3 weeks |
| 3 | Live translation, proactive invitations, persistent conversation continuation by email/portal, wallboard tile | 2 weeks |

## Risks / open

- SSE connections per visitor hold an API worker coroutine each — measure;
  if needed run `/api/chat/` on a dedicated `tiqora-api` replica (same image).
- Znuny's UI shows one article per chat message — acceptable (Telegram does
  the same today); the transcript option exists for sites that mind.
- **Open:** further messaging channels (Facebook/Instagram via Meta's
  messaging API, Microsoft Teams, Signal/Matrix) reuse the generalised
  messenger composer and the `tiqora_chat_*` model; not part of this plan.
