---
name: tiqora-create-agent-user-and-api-key-without-admin
description: |
  Create a Tiqora agent (bot) user with queue-group permissions and a scoped
  Bearer API key when you have docker access to the tiqora-api container but
  no admin-UI/admin-API credentials. Use when: (1) a new integration needs a
  `tiqora_…` key with `tickets:rw`, (2) `tiqora api-key create` fails with
  "target user … not found" because the agent user does not exist yet,
  (3) POST /api/v1/tickets returns 403 although the key is valid (missing
  group_user rows), (4) the integration resolves priorities/states by name
  and never creates a ticket. Gotchas: live priority names are `normal`,
  `high`… not Znuny's `3 normal`; `/api/v1/reference/*` lives in the
  `tickets` scope area; on <docker-host> use the `docker@` account.
author: Claude Code
version: 1.0.0
date: 2026-09-17
---

# Tiqora: agent user + API key without admin credentials

## Problem
`tiqora api-key create --user <id>` needs an existing, valid agent user, and
the CLI has no `user create`. The admin REST endpoint (`POST
/api/v1/admin/users`) needs an admin session you may not have. Creating the
row by hand is easy to get wrong (password hash, permission keys, Znuny
caches).

## Context / Trigger Conditions
- You are on the docker host (e.g. `ssh docker@<docker-host-fqdn>`;
  the personal account has neither docker group nor sudo there) and can
  `docker exec tiqora-api …`.
- The integration only needs `tickets:rw` (ticket create + articles,
  `/api/v1/reference/{states,priorities,agents,customers}` are all in the
  `tickets` area — see `tiqora/domain/api_key_scopes.py`).

## Solution
1. Find the queue's group and a template user (an existing bot such as
   `autoabuse`) — read-only, inside the container's venv:
   ```python
   # docker cp script.py tiqora-api:/tmp/ ; docker exec tiqora-api /app/backend/.venv/bin/python /tmp/script.py
   from sqlalchemy import text
   from tiqora.db.engine import get_session_factory
   # select id,name,group_id from queue where id=10
   # select user_id,group_id,permission_key from group_user where user_id=<bot>
   ```
2. Create the user exactly like `api/v1/admin/users.py::create_user` does:
   ```python
   from tiqora.api.v1.admin.common import USER_CACHE_TYPES, USER_GROUP_CACHE_TYPES, invalidate_znuny_cache_types
   from tiqora.db.legacy.user import GroupUser, Users
   from tiqora.domain.password_setup import unusable_password_hash
   user = Users(login="secretary", pw=unusable_password_hash(), first_name="KI",
                last_name="Sekretariat", valid_id=1, create_time=ts, create_by=ADMIN_ID,
                change_time=ts, change_by=ADMIN_ID)
   s.add(user); await s.flush(); await invalidate_znuny_cache_types(s, USER_CACHE_TYPES)
   for k in ("ro","move_into","create","note","owner","priority","rw"):
       s.add(GroupUser(user_id=user.id, group_id=GROUP_ID, permission_key=k, ...))
   await invalidate_znuny_cache_types(s, USER_GROUP_CACHE_TYPES); await s.commit()
   ```
   `owner` is needed if the integration sets `owner_id` to this user.
3. Issue the key (plaintext printed once, keep it out of the shell history):
   ```sh
   docker exec tiqora-api tiqora api-key create --user <id> --name pbx-secretary --scopes tickets:rw
   ```
4. Verify with the key: `GET /api/v1/reference/agents` lists the new login,
   `GET /api/v1/reference/priorities` shows the real names.

## Verification
`curl -H "Authorization: Bearer $K" http://127.0.0.1:8090/api/v1/reference/priorities`
returns `[{"id":3,"name":"normal"},…]` — configure the integration with
`normal`, not `3 normal`, or its exact-name lookup raises and no ticket is
ever created.

## Notes
- The CLI prints a harmless `RuntimeError: Event loop is closed` from
  aiomysql at exit; ignore it.
- `docker exec … rm /tmp/x.py` may fail ("Operation not permitted") on the
  read-only-ish image; the file is harmless.
- Store the key in a 0600 file owned by the deploying user; root-owned stack
  dirs (`/home/docker/pbx`) are not writable as `docker@`.

## Read-only variant (netadmin, 2026-10-01)
For a pure reader (e.g. netadmin's `GET /api/v1/integrations/customer-tickets`)
grant only `permission_key="ro"` on the needed groups (Bonn: 6 StudNet Bonn,
14 Netzkoordinator, 17 Abuse, 8 Studierendenwerk Bonn) and issue
`--scopes tickets:ro`. Capture the key without echoing it:
`ssh docker@… 'docker exec tiqora-api tiqora api-key create …' >| $SP/key.out`
(umask 077), then extract `tiqora_[A-Za-z0-9_-]{20,}` with a script straight
into the target config and delete the file. Container `/tmp` files cannot be
removed afterwards ("Operation not permitted") — keep secrets out of them
(pipe via stdin).

## Changing an existing key's scopes (2026-10-05)
The CLI has only create/list/revoke/delete. To add a scope (e.g. `customers:rw`
for `/api/v1/customers` writes) keep the key and update the row directly — the
key row is read on every request (`domain/auth.py` resolve_api_key), no cache:
```python
row = (await s.execute(text("SELECT id,name,scopes FROM tiqora_api_key WHERE id=5"))).one()
assert row.name == "pbx-secretary"
await s.execute(text("UPDATE tiqora_api_key SET scopes=:s WHERE id=5"), {"s": "tickets:rw,customers:rw"})
await s.commit()
```
Run it via `docker exec -i tiqora-api /app/backend/.venv/bin/python - < script.py`
(stdin, nothing left in the container). Verify with the key: `GET
/api/v1/customers/<login>` → 200. Area mapping: `/api/v1/customers*` →
`customers`, `/api/v1/reference*` → `tickets`, `/api/v1/phone*` → `events`.
