# Znuny lock/owner parity for composer actions

Approved 2026-08-13 (chat). Goal: replying (and forward/bounce/close) behaves
exactly like Znuny — opening the composer locks the ticket and makes the agent
its owner; a ticket locked by another agent shows a takeover banner; manual
lock takes ownership; closing unlocks.

## Behaviour (Znuny golden reference)

- `AgentTicketCompose/Forward/Bounce/Close` carry `RequiredLock=1` by default;
  `AgentTicketNote` carries 0. Opening such a screen on an *unlocked* ticket
  runs `TicketLockSet('lock')` then `TicketOwnerSet(self)` — history order
  Lock, OwnerUpdate.
- A ticket locked by another agent blocks the screen; taking over = owner
  change to self (requires `owner` permission), lock stays.
- `AgentTicketLock` (menu "Sperren") also locks-then-owns; "Freigeben" only
  unlocks, owner unchanged.
- Closing from a frontend screen unlocks the ticket afterwards.
- `TicketLockSet` no-ops (no UPDATE, no history) when the lock state is
  already the requested one.

## Backend

- `znuny/sysconfig.py`: defaults + accessor for
  `Ticket::Frontend::AgentTicket{Compose,Forward,Bounce,Close}###RequiredLock`
  (all 1). Values are read from the Znuny sysconfig DB, so per-instance
  overrides apply.
- `ticket_write_service.py`
  - `lock_ticket` / `unlock_ticket`: add the Znuny same-state no-op guard.
  - new `acquire_lock(session, ticket_id, user_id, sysconfig, action, takeover)`
    → `not_required | acquired | already_mine | taken_over | locked_by_other`
    (+ `locked_by_id/_name`). Unlocked ⇒ lock then own (history order as
    Znuny). Locked by other ⇒ report holder, or with `takeover` ⇒ owner to
    self (lock stays).
  - class `TicketWriteService.acquire_lock`: `rw` assert; takeover asserts
    `owner` (parity with `assign_owner`).
  - class `lock_ticket(take_ownership=True)` used by PATCH `lock:"lock"`
    (menu parity); unlock unchanged.
  - class `change_state`: when the new state type starts with `close`,
    apply frontend close parity — acquire (lock+own) if unlocked and
    RequiredLock(Close), then state change, then unlock.
- API: `POST /api/v1/tickets/{id}/acquire-lock`
  `{action: compose|forward|bounce|close, takeover: bool}` → 200
  `{result, locked_by_id, locked_by_name}`. (Design initially said 409 for
  locked-by-other; a 200 result enum keeps the client logic and the ApiError
  path clean — deviation noted.)

## Frontend

- api-client: `acquireTicketLock` (four-place edit + package rebuild).
- `useTicketLockAcquisition(ticketId, action, open)` hook: fires on dialog
  open, exposes `{lockedBy, takeOver()}`, invalidates the ticket query after
  any lock/owner change.
- ReplyDialog, ForwardDialog, BounceDialog: banner "Gesperrt von {name}" +
  "Übernehmen" button while `locked_by_other`; send disabled until resolved.
  Closing the dialog leaves the lock in place (Znuny behaviour, unlock
  timeout applies).
- i18n: `ticket.lockedByBanner`, `ticket.takeOver` in all 49 locales.

## Tests

- Backend (`@pytest.mark.db`): acquire outcomes ×4, sysconfig override off,
  history rows and order, lock no-op guard, PATCH lock⇒owner, close⇒unlock
  (+ owner when previously unlocked).
- Frontend: hook + ReplyDialog banner/takeover vitest; e2e second-agent
  takeover flow.
