---
name: tiqora-route-read-blocks-session-begin
description: |
  Tiqora/aurix (FastAPI + SQLAlchemy 2 async): adding a DB read at the TOP of a
  route handler that later does `async with session.begin():` breaks the route
  with sqlalchemy.exc.InvalidRequestError: "A transaction is already begun on
  this Session." Use when: (1) you just added a permission/ACL/existence check
  (e.g. TicketService._assert_ticket_ro, PermissionEngine.check, a `select()`)
  to a route that writes, (2) a previously-passing endpoint suddenly 500s and
  the traceback ends at `session.begin()` inside your own handler, (3) a test
  that drives the route through httpx/ASGITransport fails with that error while
  the same service call works standalone, (4) you are reviewing why
  api/deps.py::get_current_user calls `await session.rollback()` "for no
  reason". Root cause is SQLAlchemy 2 autobegin on the shared request-scoped
  session, not a bug in your check. Also covers the sibling breakage: tests that
  call route functions directly fail with "missing 1 required positional
  argument" when a route gains a dependency param.
author: Claude Code
version: 1.0.0
date: 2026-08-12
---

# Route-level DB reads block a later `session.begin()`

## Problem

Tiqora routes share ONE request-scoped `AsyncSession` (`DbSession` →
`api.deps.get_db`). SQLAlchemy 2 **autobegins** a transaction on the session's
first use — including a plain `SELECT`. A write route that later opens an
explicit block:

```python
async with session.begin():
    ...
```

then raises:

```
sqlalchemy.exc.InvalidRequestError: A transaction is already begun on this Session.
```

The traceback points at *your* `session.begin()` line, so it reads like the
write is at fault. It isn't — the read you added above it is.

This is the exact hazard `api/deps.py::get_current_user` already documents and
works around; its docstring says so explicitly:

> The auth lookups read the DB on the *shared* request session, which
> autobegins a transaction. We roll it back before returning so endpoints can
> open their own `async with session.begin()` … (the lookups are read-only, so
> nothing is lost).

Because that rollback happens in the **dependency**, routes normally start
clean — which is precisely why adding a read *inside the handler* reintroduces
the problem, and why it is easy to miss in review.

## Context / Trigger Conditions

Hits when ALL of these are true:

- The handler takes `session: DbSession` (the shared request session).
- You add a read before the write: an ACL/permission check, an existence probe,
  a lookup of a related row.
- The handler later does `async with session.begin():` (or `session.begin()` in
  a `try`).

Typical shapes that trigger it in this codebase:

```python
await TicketService(session)._assert_ticket_ro(user.id, ticket_id)
await PermissionEngine(session).is_admin(user.id)
await session.execute(select(Ticket.id).where(...))
```

Symptoms:

- 500 on an endpoint that worked a minute ago.
- Traceback ends in `sqlalchemy/orm/session.py … begin` →
  `InvalidRequestError: A transaction is already begun on this Session.`
- Only the *write* endpoints break; the read-only sibling endpoint using the
  same new check is fine (it never calls `begin()`).

## Solution

Roll the read-only transaction back before returning from the check. Put the
rollback in a `finally` so it also runs when the check raises (the handler's
exception mapper still needs a clean session):

```python
async def _assert_draft_ticket_readable(
    session: AsyncSession, user_id: int, ticket_id: int
) -> None:
    """Require ``ro`` on the ticket a draft is being stored against.

    Rolls the read-only lookup back before returning: it autobegins a
    transaction on the shared request session, and the writers open their own
    ``async with session.begin()`` right after (same dance as
    ``api.deps.get_current_user``).
    """
    try:
        await TicketService(session)._assert_ticket_ro(user_id, ticket_id)
    except (TicketAccessDenied, TicketNotFound) as exc:
        raise _map_exc(exc) from exc
    finally:
        await session.rollback()
```

Then call that one helper from every route that needs the check, instead of
inlining the read three times. Rolling back a read-only transaction discards
nothing.

**Alternatives (usually worse here):**

- Move the check into a FastAPI dependency — correct, and matches
  `get_current_user`, but a dependency cannot easily take a path param plus the
  same session and still map to route-specific 404/403 bodies.
- Use a second session from `get_session_factory()` — works, but costs a
  connection and splits the read from the write's consistency view.
- Drop `session.begin()` and rely on autobegin + explicit `commit()` — changes
  the transactional shape of the write; don't do this as a side effect of
  adding a check.

## Verification

1. Drive the route through the ASGI transport, not just the service layer —
   this bug only appears via the full request path:

   ```bash
   cd backend && uv run python -m pytest tests/test_form_drafts_api.py -q -p no:randomly
   ```

2. Assert BOTH halves, or you will only catch one:
   - the deny path returns 404/403,
   - the allow path still returns 200/204 (this is the half that regressed).

3. A test that only exercises the denial passes even when every legitimate
   write is broken. Seed a real, readable row for the allow case:

   ```python
   # a bare ticket id is no longer enough once the route checks `ro`
   await _seed_readable_ticket(session)   # group + group_user + queue + ticket
   ```

## Example

Real regression from the security-review pass (finding L-1, drafts needed a
ticket `ro` check):

```python
# BROKEN — three routes each inlined this above their session.begin()
@router.put("/{ticket_id}/drafts/{action}")
async def upsert_draft(...):
    await TicketService(session)._assert_ticket_ro(user.id, ticket_id)  # autobegins
    ...
    async with session.begin():        # InvalidRequestError
        await session.execute(update, params)
```

```python
# FIXED — one helper, rollback in finally
@router.put("/{ticket_id}/drafts/{action}")
async def upsert_draft(...):
    await _assert_draft_ticket_readable(session, user.id, ticket_id)
    ...
    async with session.begin():        # clean session
        await session.execute(update, params)
```

The three drafts tests failed with `InvalidRequestError`; the deny-path test
added alongside them passed the whole time.

## Notes

- **Sibling breakage — tests that call route functions directly.** Several
  Tiqora tests invoke handlers as plain functions
  (`await admin_users.update_user(uid, body, admin, session)`,
  `await auth_api.passkey_delete(pid, user, svc, totp, settings, session)`).
  Adding a parameter to a route — `request: Request`, `settings: AppSettings`,
  a new service dep — breaks them with
  `TypeError: update_user() missing 2 required positional arguments`. FastAPI
  injects these at runtime, so nothing else complains. When a route needs
  `request` only to reach `app.state.redis`, a `SimpleNamespace` double is
  enough:

  ```python
  def _fake_request(redis) -> Any:
      return SimpleNamespace(
          app=SimpleNamespace(state=SimpleNamespace(redis=redis, settings=_settings())),
          state=SimpleNamespace(), cookies={}, headers={},
          client=SimpleNamespace(host="127.0.0.1"),
      )
  ```

- `ruff` catches the related slip where the guard references a dependency the
  route does not declare (`F821 Undefined name 'totp'`) — run
  `uv run ruff check src tests` before the test suite; it is seconds versus
  minutes.

- Postgres makes a *failed* statement poison the rest of the transaction, so a
  missing rollback can also surface later as unrelated queries failing. See
  `_user_me` in `api/v1/auth.py`, which rolls back inside its `except` for that
  reason.

- See also: `tiqora-openapi-regen-or-ci-wipes-schema` (the other thing that
  breaks after a route/model change — regenerate
  `packages/api-client/openapi.json`).

## References

- SQLAlchemy 2.0 — "Auto Begin": a `Session` begins a transaction implicitly on
  first use; `Session.begin()` raises if one is already in progress.
  https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#auto-begin
- In-repo precedent and rationale: `backend/src/tiqora/api/deps.py`
  (`get_current_user` docstring + its `await session.rollback()`).
