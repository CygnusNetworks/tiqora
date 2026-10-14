# Web chat: assistant loop, claims and identity paths

Technical reference for the parts of the web chat that are not obvious from the admin guide
([guide/webchat.md](guide/webchat.md)): how the AI first line runs, how it is kept exactly-once, how a visitor is
recognised and which tables carry the state. Package layout, static routes, fan-out and shutdown are in
[architecture.md](architecture.md#web-chat-webchat-packageswebchat-widget); retention and erasure in
[gdpr.md](gdpr.md#web-chat). Example hosts are `helpdesk.example.org` (Tiqora) and `www.example.org` (the website).

## Session states

```
create -> bot       topic ai_mode = first_line AND assistant available AND (humans available OR offline_mode = ai)
       -> waiting   humans available (business hours and an eligible agent)
       -> 409       neither (the widget shows the offline form or hides)
bot  -> waiting     hand_off_chat, humans available            (ring: chat_waiting)
bot  -> ended       hand_off_chat, nobody available            (end_reason no_agent, e-mail prompt)
bot  -> active      an agent accepts or replies (takeover, handoff_reason = takeover)
bot  -> waiting/ended   sweep, no AI worker answers a visitor line for 60 s (handoff_reason = unavailable)
bot  -> ended/abandoned   visitor ends, agent ends, idle sweep
```

`bot` is a live status: the sweep, uploads, typing, end and the agent inbox treat it like `waiting`/`active`.
A bot chat is not "waiting": it does not ring and is not counted in `core.chat-queue.waiting`. The dashboard
widget counts it separately in `core.chat-queue.bot` (count only, never items; kept on wallboard links).

## Assistant availability

`bot_availability(db, redis, settings, topic, queue_id, use_cache=True)` returns `ok` plus a reason code. Order:
topic `ai_mode == first_line`; the policy of the ticket's **current** queue (so a ticket moved to another queue
follows that queue's policy) with `enabled_auto_reply` and a service user; autonomy not `off`;
`require_feature_allowed(auto_reply, source_channel="webchat")` (kill switch and pause; `webchat` is a Tiqora-only
channel, so `parallel` mode does not block it); `tiqora:chat:bot:alive` present; the queue-level caps
(`auto_reply_queue_cap_reason`: hourly, global, daily token budget); a usable agent model. The result is cached 10 s
in Redis under `tiqora:chat:botavail:<config>:<topic>:<queue>`; the bot turn and `hand_off_chat` call it with
`use_cache=False` because money is spent on that decision.

## The bot loop (`tiqora-ai-worker`)

```
visitor line stored (create_session / send message)
  -> last_visitor_seq = seq (only upward, only for a newly stored line)
  -> post-commit: RPUSH tiqora:chat:bot:queue <session id>   (deduped by SET tiqora:chat:bot:queued:<sid> NX EX 120)
ai-worker bot loop (ai/chat_bot_loop.py)
  -> BLPOP the queue, and every 15 s rescan the DB for status=bot AND last_visitor_seq > bot_answered_seq
  -> at most TIQORA_CHAT_BOT_CONCURRENCY run_chat_bot_turn tasks
  -> SET tiqora:chat:bot:alive EX 30 every 10 s (only while the loop is enabled, see below)
```

**Enabled gate.** The loop runs only while both `daemon.ai_worker.enabled` and the web chat master switch
(`channel.webchat.enabled`) are on; the check is cached for 5 s. While either is off the loop idles (it sleeps 2 s per
round) and touches neither Redis nor the chat tables, and it does **not** refresh `tiqora:chat:bot:alive`. The
availability check then reports `worker_down`, so new chats go to humans, and the chat sweep hands over a `bot` chat
whose visitor line has waited longer than 60 s ([Orphaned bot chats](#orphaned-bot-chats)). Setting `operation_mode`
to `parallel` does not block the loop: `webchat` is a Tiqora-only channel.

**Redis client (since v0.18.1).** The loop uses its own Redis client (`make_bot_redis`) whose socket timeout
(`BOT_BLPOP_TIMEOUT` 5 s + 2 s grace + 1 s = 8 s) is longer than the server-side `BLPOP` wait. Before, the shared
client's default 5 s socket timeout equalled the `BLPOP` timeout, so every idle poll ended in a `TimeoutError`. Each
`BLPOP` is also wrapped in `asyncio.wait_for(5 s + 2 s)`, so a half-open connection cannot stall the 15 s rescan.
Identical failures (`webchat_bot_enabled_check_failed`, `webchat_bot_alive_refresh_failed`,
`webchat_bot_rescan_failed`, `webchat_bot_queue_pop_failed`) are logged once per distinct error and then at most every
10 minutes, with the exception text. These changes ship since v0.18.1 (commit `976ada44a`).

The Redis list is an accelerator only. The database is the truth, so a lost push, a Redis restart or a crashed
worker costs at most one rescan interval (15 s).

### Claim (exactly one turn at a time)

`claim_bot_turn` is one conditional `UPDATE` on `tiqora_chat_session`: the status is still `bot`, a visitor line is
pending (`last_visitor_seq > bot_answered_seq`) and `bot_claimed_at` is NULL or older than 180 s. The winner stores
`bot_claimed_at` and `bot_claim_owner` (`<host:40>:<pid>:<uuid8>`, at most 64 characters). Taking over a **stale**
claim also releases the AI run lock (`worker_instance="chat-bot"`, owner `chat-bot:...`) the dead turn left behind,
in the same transaction. A `LockHeldError` from a non-bot owner (an agent's manual draft) is a retry without counting
an attempt. Every exit clears the claim in its own transaction; if a visitor line arrived meanwhile
(`last_visitor_seq > bot_answered_seq` and still `bot`) the turn is queued again at once.

### The turn

1. Claim; remember `target_seq`.
2. Re-check availability (current queue, no cache) and the per-ticket caps (`max_auto_replies`,
   `max_clarifications`, escalation stamp, pauses). A miss hands the chat to a human.
3. Publish the "bot is typing" marker every 3 s (`who = bot`, carried through the hub to the visitor stream).
4. `run_ticket_agent(trigger=auto, source_channel="webchat", worker_instance="chat-bot", ...)` with the inputs from
   the shared `prepare_auto_run` (same session; KB tools need it open).
5. Map the result: `sent` -> `bot_answered_seq = target_seq`, attempts reset, outcome `replied`; `superseded` ->
   queued again; `escalated`, a draft or no reply -> `hand_off_chat`; an LLM or agent error -> `bot_attempts += 1`,
   retry after 5 s, after the third failure `hand_off_chat(reason="error")`.

`deliver_bot_chat_reply` (called by the runtime for `webchat`, before Telegram and e-mail, so it can never reach
SMTP) locks the chat row, refuses if the status is no longer `bot` (`BotChatTakenOver`: the answer is saved as a
draft), writes an agent article on the `WebChat` channel by the service user and the transcript line
(`sender = bot`), then queues the wake-up and the agent event. Lock order is session row, then ticket, in
`hand_off_chat`, `deliver_bot_chat_reply` and `identify_session`.

### Handoff

`hand_off_chat(reason, note)` (reason `visitor | ai | limit | error | unavailable`) runs in a fresh transaction:
it locks the session; a status other than `bot` is a no-op. With humans available the status becomes `waiting`
(`waiting_since` set, ring `chat_waiting` with `topic_ref`), otherwise `ended/no_agent` with the phase-1 e-mail
prompt. It stores `handoff_at`, `handoff_reason`, `bot_outcome = handoff`, writes the internal note
"KI-Übergabe im Chat" when there is a summary, marks the ticket as escalated by the AI
(`webchat_<reason>`), and adds the system lines `handoff` (and `no_agent`). Both notification paths (post-commit
agent event and wake-up) go through `flush_chat_notifications`.

When a chat ends from `bot` after a reply and without a handoff, `close_bot_resolved_ticket` moves the ticket to the
state the policy allows after a sent auto-reply.

### Agent takeover

An agent accepting a `bot` chat (`POST /api/v1/chat/sessions/{id}/accept`) or replying to the ticket
(`POST /api/v1/tickets/{id}/articles` with a chat reply, via `accept_waiting_chat_for_reply`) changes it to `active`
under the same row lock. It also stores `handoff_at` and `handoff_reason = takeover`; unlike `hand_off_chat` it sets no
AI escalation mark, and the ticket is not parked as bot-resolved afterwards. A running turn notices at delivery
(`BotChatTakenOver`) and saves its answer as a draft. The agent needs `rw` on the ticket's **current** queue and a free
slot (`max_concurrent`, 1-20, default 3). The visitor gets the system line `handoff` with reason `takeover`
("Ein Mitarbeiter hat den Chat übernommen."). A reply through MCP (`ticket_reply`) never takes over: while the chat
is `waiting` or `bot` (anything but `active`) it is refused with `chat_not_accepted`.

### Visitor handoff

`POST /api/chat/v1/sessions/{id}/handoff` ("Mit einem Menschen sprechen") calls `hand_off_chat(reason="visitor")`
with the internal note "Der Besucher möchte mit einem Menschen sprechen." It answers `409 not_bot` when the chat is not
(or no longer) in `bot`, and has its own bucket of 3 presses per session and minute. A running AI turn does not block it.

### Orphaned bot chats

`webchat_sweep` (in `tiqora-worker`) also protects visitors from a stopped assistant. When `tiqora:chat:bot:alive` is
missing and a `bot` chat has a visitor line older than 60 s (`ORPHAN_BOT_SECONDS`) with no answer, the sweep calls
`hand_off_chat(reason="unavailable")`: the chat becomes `waiting`, or `ended/no_agent` when nobody is there. If the
Redis check itself fails the sweep leaves the chats alone for that tick. Other sweep thresholds: a `waiting` visitor
without a sign of life for 120 s and an `active` or `bot` chat quiet for 900 s become `abandoned/idle`; `waiting`
longer than the config's `wait_timeout_seconds` (default 180, 30-3600) ends as `no_agent`; ended chats are purged
after `retention_days` (default 30).

## Identity paths

All three paths end in `resolve_identity` (customer user by `sub`, else by a unique `email`, else optionally a
created customer, else the free-text address) and set `identity_source` (`website` or `portal`) on the session, so
the ticket header can show **Identität bestätigt**.

| Path | Who proves it | Where it is verified |
|---|---|---|
| Website | the website's server signs `{sub or email, name, exp[, aud]}` with the chat's secret (HS256); the page calls `Tiqora.chat.identify(token)` | `POST /api/chat/v1/sessions` (field `identity`) and `POST /api/chat/v1/sessions/{id}/identify` |
| Portal | the customer's portal session cookie, read **only** by `POST /api/chat/v1/identity/portal` from Tiqora's own origin; the answer is a 10 min assertion (`kid = tiqora-portal`, bound to the chat's public key and the visitor id) | same two endpoints, same verifier |
| None | the visitor types name and e-mail | pre-chat form; e-mail stays unverified |

Rules of the verifier (`tiqora.webchat.identity`, pure): compact JWT form, at most 4096 ASCII characters; the
algorithm is exactly `HS256` (no `none`, no `crit/jku/jwk/x5u`); constant-time signature comparison over the received
bytes; `exp` is required, expired after 60 s leeway, at most 24 h ahead; `nbf` optional; `aud`, when present, must be
the chat's public key (required for portal assertions, which also need `vid` and `src = portal`). Every failure is
the single public code `invalid_identity` (the exact reason is logged, never returned). A mid-chat `identify` on a
session that already has an identity is a no-op for the same customer and `409 identity_conflict` for another.

The website secret is stored encrypted in `tiqora_chat_config.identity_secret`. The config cache copies it still
encrypted and decrypts it only inside `website_secret`, so the pre-authentication path needs no extra database
read; a rotated secret takes effect on every replica within the cache TTL (10 s). The signed token is never
stored.

Widget side: the loader keeps only the latest token passed before the frame is ready; the frame keeps it in memory,
never in storage. A stored session of a signed-in visitor is restored on reload only while the host page (or the
portal fetch) vouches for the visitor again; otherwise the stored chat is dropped. `Tiqora.chat.logout()` clears
the token and the stored session. Frames started for the admin preview never call the portal identity endpoint.

## Tables

All state is in `tiqora_*` tables (migration `20261010_0073`): on `tiqora_chat_config` the columns `bot_name`,
`bot_tone_prompt`, `show_on_portal`, `identity_create_customers`, `identity_secret`; on `tiqora_chat_session`
`identity_source`, `last_visitor_seq`, `bot_answered_seq`, `bot_claimed_at`, `bot_claim_owner`, `bot_attempts`,
`bot_outcome`, `handoff_at`, `handoff_reason`; topics carry `ai_mode`. Znuny tables are only written through the
existing ticket write services, so the articles stay normal Znuny articles
([compatibility.md](compatibility.md#web-chat-in-znuny)).
