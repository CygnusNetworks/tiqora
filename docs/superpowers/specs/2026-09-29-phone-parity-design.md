# Phone parity + phone extras (A1) and CTI call popup (A2)

Date: 2026-09-29 · Status: approved direction (user: "einfach alles umsetzen")

## Goal

Znuny 6.5 feature parity for everything phone-related, plus Tiqora extras
that fit the product: call timer, callback presets, caller lookup, click-to-
call, AI note structuring, phone channel in the queue list (A1), and a live
incoming-call popup fed by a PBX webhook (A2).

## Znuny reference behaviour (6.5.22)

- `AgentTicketPhone` (new phone ticket): channel `Phone`, sender `customer`,
  customer-visible, history `PhoneCallCustomer`, auto-reply to the From
  address when `AutoResponseForWebTickets` (default on) and the queue has an
  `auto reply` response. Form: From customer(s), queue, owner, responsible,
  type, service/SLA, next state incl. pending/closed + pending date, time
  units, dynamic fields, attachments.
- `AgentTicketPhoneOutbound` / `AgentTicketPhoneInbound` (log a call on an
  existing ticket, zoom menu "Phone Call Outbound/Inbound"): outbound =
  sender agent, history `PhoneCallAgent`, default next state
  `closed successful`, **requires lock** (ticket is locked to the agent);
  inbound = sender customer, history `PhoneCallCustomer`, default next state
  `open`, no lock. Both: subject, body, next state + pending date, time
  units, dynamic fields, attachments.
- Overviews never show a per-ticket channel; the channel appears per article
  in zoom (icon + "via"). The Tiqora queue-list channel is a Tiqora feature.

## A1 — design

### Backend

1. **`POST /api/v1/tickets/{ticket_id}/phone-calls`** (agent session / API
   key, area `tickets:rw`). Body:
   `direction: inbound|outbound`, `subject`, `body`, `content_type`
   (`text/plain|text/html`), `is_visible_for_customer` (default true),
   `state_id?`, `pending_time?`, `time_unit?` (float), `dynamic_fields?`
   (name→value, article/ticket fields as the article create path supports),
   `attachments?` (same shape as article create), `caller_number?`.
   One transaction: article via the existing write service
   (`channel="phone"`, sender by direction, history
   `PhoneCallCustomer`/`PhoneCallAgent`), next state + pending time
   (422 when a pending state lacks `pending_time`, as for articles), time
   accounting row bound to the new article, outbound → lock to the acting
   agent (409 if locked by someone else; unlocked → lock + owner stays),
   permission as Znuny: `phone` permission key on the queue where the
   permission engine knows it, else `rw`. Emits the normal outbox events.
   Response: `{article_id, ticket_id, time_accounting_id?, locked: bool}`.
2. **Service function** `log_phone_call_on_ticket(...)` in
   `channels/phone/service.py` holding the logic above; the endpoint, the
   new-ticket flow and the CTI `/channels/phone/note` path all use it
   (`/note` keeps its public contract).
3. **Caller lookup**: `GET /api/v1/reference/caller?number=…` →
   `{number_normalized, customers: [{login, customer_id, name, email, phone,
   mobile, company}], open_tickets: [{id, tn, title, state, queue, changed}]}`
   (open = state type not `closed`/`removed`/`merged`, only queues the agent
   can read, max 10, newest first). Matching reuses
   `normalize_phone` + last-9-digit comparison on `customer_user.phone` and
   `.mobile`; returns **all** matches (cap 10). The general customer search
   (`/reference/customers`, `quick_search`) also matches phone/mobile when
   the query contains ≥ 5 digits.
4. **Auto-reply on new phone tickets**: when a ticket's first article is a
   phone article from the customer (inbound), the ticket was created via the
   agent UI, the SysConfig `AutoResponseForWebTickets` is true (default
   true) and the customer has an email address, call
   `send_auto_response(..., auto_response_type="auto reply")` exactly like
   the email pipeline (loop protection included). Outbound-direction new
   phone tickets never auto-reply. Implemented as a flag on
   `POST /tickets` (`send_auto_response: bool`, default false) that the
   phone form sets, so other callers are unaffected.
5. **New-ticket completeness**: `POST /tickets` + article create already
   accept type/service/SLA/dynamic fields; add what the phone form needs:
   responsible, pending time on the initial state, time units (booked on the
   first article). From header for phone articles is `"Full Name" <email>`.
6. **Queue-list phone channel**: add `phone` to the list channel keys. Chat
   channels keep "any conversational article, earliest wins"; `phone`
   applies only when the ticket's **first** article is on the `Phone`
   channel and no chat channel applies. Filter, facets, CSV export and the
   OpenAPI enum follow. Verify that `channel="phone"` maps to Znuny's
   `Phone` communication channel.
7. **Click-to-call scheme**: setting `phone.dial_scheme` (`tel`|`sip`,
   default `tel`) exposed read-only to agents via the existing
   config/reference endpoint used for UI settings.
8. **AI note mode**: `refine` gains a `mode: "message"|"call_note"`
   (default `message`). `call_note` uses its own rules: internal note,
   structure into *Anliegen / Vereinbart / Nächste Schritte* (headings in the
   UI language), never add facts, keep numbers/dates verbatim, PII masking as
   usual. Same queue policy (`enabled_refine`) and ACL gate.

### Frontend

1. **Ticket header**: new "Anruf" button (phone icon) with menu
   *Eingehend* / *Ausgehend* in `TicketHeaderActions`; opens `PhoneCallDialog`.
   Shown when the agent may write to the ticket.
2. **`PhoneCallDialog`**: direction toggle; timer (auto-start on open,
   pause/resume, displays mm:ss, prefills time units on save rounded up to
   the configured unit, stays editable, reuses `ComposerTimeChip`); subject
   prefilled "Anruf von ‹Kunde›" / "Anruf an ‹Kunde›"; body via
   `ComposerBody`; `RefineControls` in `call_note` mode ("Notiz aufbereiten");
   next state: *Status unverändert* / defaults per direction (outbound →
   first `closed` state named `closed successful` if present, inbound →
   `open`) / **Rückruf** with presets *in 1 Std · heute 16:00 · morgen 9:00 ·
   Datum…* (sets the first `pending reminder` state + time); customer-visible
   checkbox (default on); dynamic fields (same editor as elsewhere, article-
   and ticket-level fields configured for the screen fall back to all
   editable fields); attachments. Draft (text, direction, elapsed timer)
   persisted per ticket in localStorage until saved or discarded. Outbound
   409 → clear message "Ticket ist von X gesperrt".
3. **New phone ticket** (`NewTicketPage`, phone mode): caller-number field
   with live lookup (`/reference/caller`) → pick customer; if the caller has
   open tickets, show them with "Anruf zu diesem Ticket erfassen" (opens the
   ticket with `PhoneCallDialog`, carrying over typed text and timer).
   Additional fields for parity: owner, responsible, type, service, SLA,
   state incl. pending (with date + presets) and closed, time units (timer
   runs from page open in phone mode), dynamic fields, attachments.
   Sets `send_auto_response` for inbound direction.
4. **Click-to-call**: customer phone and mobile rendered as links
   (`tel:`/`sip:` per setting) in the ticket customer card/pill details and
   `CustomerDetailPage` (mobile added). Clicking dials and opens
   `PhoneCallDialog` (outbound, timer running) when on a ticket; on the
   customer page it opens the new-phone-ticket page with the customer and
   direction outbound preselected.
5. **Queue list**: `phone` in `TICKET_CHANNELS` (phone icon, own colour token
   via `themeColor()`), pill/edge/filter like the other channels.
6. **i18n**: new keys in `en.json` + `de.json`, propagated with
   `frontend/scripts/propagate-i18n-keys.mjs` (never the scaffold script).

## A2 — CTI incoming-call popup

1. **Webhook** `POST /api/v1/channels/phone/events` (shared secret
   `X-Tiqora-Phone-Secret`, channel `phone` must be enabled):
   `{event: ringing|answered|hangup, call_id, caller_number, extension,
   direction?: inbound|outbound, timestamp?}`.
2. **Agent ↔ extension**: user preference `TiqoraPhoneExtension`
   (Znuny `user_preferences`). Self-service `GET/PUT /api/v1/auth/me/phone`
   (`{extension}`), admin edit in user admin. Several extensions per agent
   allowed (comma separated).
3. **Call state**: Redis hash `tiqora:call:<call_id>` (TTL 2 h) with number,
   extension, agent ids, timestamps; `GET /api/v1/phone/calls/active` returns
   the caller's active/recent (≤ 15 min after hangup) calls so a reload
   restores the popup.
4. **Push**: new SSE event type `call_event` with `user_ids`; `_should_forward`
   only forwards to those users. Payload: call id, event, number, caller
   lookup result (customers + open tickets the recipient may read — computed
   per recipient in the stream filter or looked up by the frontend via
   `/reference/caller`; the frontend lookup is used to keep permission logic
   in one place).
5. **Popup** (`CallPopup` in `AgentShell`): on `ringing` a card bottom-right
   "Anruf von ‹Kunde/Nummer›" with customer, open tickets and actions
   *Zu Ticket erfassen* (opens ticket + `PhoneCallDialog` inbound, timer from
   `answered` time), *Neues Telefon-Ticket* (new-ticket page prefilled with
   number/customer), *Ignorieren*. `answered` → timer starts; `hangup` →
   duration fixed, card stays as "Anruf beendet – erfassen?" until handled or
   dismissed (max 15 min). Multiple calls stack.
6. **Docs**: `docs/channels.md` Phone/CTI section with an Asterisk dialplan
   example (`CURL()` on dial/answer/hangup) and a generic curl example.

## Error handling

- Phone call endpoint: all-or-nothing transaction; 403 no permission, 404
  ticket, 409 locked by other agent, 422 validation (pending without time,
  unknown state). Auto-reply failures are logged and never fail ticket
  creation (as in the email pipeline).
- CTI webhook: 401 bad secret, 404 channel disabled, unknown extension →
  202 accepted and ignored (logged at debug). Redis unavailable → 503.

## Testing

- Backend: pytest for the phone-call service/endpoint (inbound/outbound,
  history types, state/pending, time accounting bound to article, lock and
  409, permissions), caller lookup (multi-match, permission filter), phone
  search in customer search, auto-reply flag (sent/not sent, loop
  protection), list channel `phone` (first-article semantics, filter,
  facets), refine `call_note` mode (prompt selection), CTI events (secret,
  extension mapping, Redis state, SSE forward filter). Run
  `uv run python -m pytest`; `ruff format --check`, `ruff check`, `mypy`.
- Frontend: vitest for `PhoneCallDialog` (timer, presets, defaults per
  direction, draft), caller lookup in `NewTicketPage`, click-to-call links,
  channel pill, `CallPopup` state machine. `pnpm lint` (eslint + tsc).
- Regenerate `packages/api-client/openapi.json` (+ `docs/api/openapi.json`)
  after API changes.
