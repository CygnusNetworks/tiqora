# CTI popup: `handled` event (call taken over by a non-agent party)

Date: 2026-09-30 · Status: implemented (Tiqora side)

## Problem

When nobody at the desk phones answers an inbound call, the PBX hands it to
the AI secretary (pbx-secretary) or to voicemail. The dialplan reports this
as `hangup` (it has to report *something* - the ringing channels are gone).
Tiqora then shows the card as **"Verpasster Anruf"** and keeps it for
`RECENT_AFTER_HANGUP_SECONDS` (15 min) so an agent can log the call, until
every agent dismisses it by hand.

For a call the secretary is handling (and files a ticket for) that is wrong:
the call is not missed, and "erfassen?" invites a duplicate ticket. Observed
2026-09-30 09:01 (`call_id` `39403f1fe3c0-1790751686.207`): `ringing` 07:01:27,
`hangup` 07:01:52 UTC (= the 25 s ring timeout, the moment the secretary
answered), state `ended`, no `answered_at`; both agents had to dismiss it.

There is no PBX-side workaround: `POST /phone/calls/{id}/dismiss` needs an
agent session, and the webhook knows only `ringing` / `answered` / `hangup`.
Sending `answered` with a non-agent extension leaves the card up as
"Im Gespräch" for the same audience.

## Design

A fourth webhook event, **`handled`**: the call was taken over by a party that
is not an agent (secretary, IVR, voicemail). Its cards disappear for everybody
who was notified, as if each agent had dismissed it, and it is never offered
for logging.

### Backend (`channels/phone/cti.py`, `api/v1/channels_phone.py`)

1. `CallEventType` gains `"handled"`; the hand-written `_CALL_EVENT_SCHEMA`
   (webhook OpenAPI body) lists it too. Body fields unchanged (`extension`
   and `caller_number` optional, ignored).
2. `apply_call_event` for `handled`:
   - unknown call id -> ignored (`recipients=[]`), like any event for an
     unknown call;
   - `state == "answered"` -> ignored: a call an agent already picked up is
     never removed under them;
   - otherwise: `state = "ended"`, `ended_at` set, `dismissed_user_ids` :=
     union of `notified_user_ids` and `user_ids`; store; publish
     **`dismissed`** (the existing SSE event) to everybody who had not
     dismissed yet; `recipients` = those users.
   - idempotent: a repeated `handled` finds nobody left to notify.
3. Late events stay harmless: `hangup`/`ringing` after `handled` do not
   reopen the call (`state == "ended"` guard) and reach nobody (all users are
   in `dismissed_user_ids`). `list_active_calls` already skips dismissed
   calls, so a page reload does not bring the card back.
4. Response unchanged: `202 {"accepted": true, "delivered_to": n}`.

### Frontend

No change: `applyCallEvent` already removes a card on `dismissed`
(`frontend/src/lib/callPopup.ts`). Optionally widen `CallEventName` docs;
the SSE payload is the existing `call_event` message.

### Docs / API client

- `docs/channels.md` "Phone / CTI": `event` list, state machine (`handled`
  = dismiss for all notified agents, ignored once answered), and a dialplan
  hint: use `handled` instead of `hangup` when the call continues on the PBX
  (secretary / IVR / voicemail).
- Regenerate `packages/api-client/openapi.json` (skill
  `tiqora-openapi-regen-or-ci-wipes-schema`), the event enum changes.

### Tests (`backend/tests/test_phone_cti.py`)

- ringing (two agents) -> `handled`: both get `dismissed`, state `ended`,
  `list_active_calls` empty for both.
- ringing -> answered -> `handled`: ignored, card stays.
- `handled` for an unknown call: `recipients == []`, no key created.
- `handled` twice: second one notifies nobody.
- ringing -> `handled` -> late `hangup`: nobody notified, still dismissed.
- webhook: form-encoded `event=handled` -> 202; unknown `event` still 422.

## PBX side (pbx-secretary repo, after the Tiqora release is live)

Order matters: an old Tiqora answers `handled` with 422, the card would never
close (2 h TTL). Deploy Tiqora first.

- `sipgate.conf`, label `n(agent)`: `Gosub(tiqora-cti,s,1(handled,...))`
  instead of `hangup`, before `Goto(secretary,s,1)`.
- Safety net for a Tiqora that does not know the event yet: in
  `[tiqora-cti]` after the `CURL()`, when `event` is `handled` and
  `TIQORA_CTI_RESULT` does not contain `"accepted":true`, send `hangup` as
  before.
- Not changed: `call-extension.conf` (direct extension -> voicemail): there
  the card *should* read "Verpasster Anruf" and stay loggable. Whether
  voicemail deserves its own treatment is a separate question.
- Mirror the production `tiqora-cti.conf` / `sipgate.conf` change in
  `deploy/asterisk/` and `deploy/asterisk/PATCHES.md`.

## Out of scope

- Showing "Sekretariat hat den Anruf" on the card (would need a new state and
  frontend copy).
- Linking the card to the ticket the secretary creates afterwards.
