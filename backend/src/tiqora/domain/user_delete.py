"""Hard-delete an agent — only when nothing anywhere still references them.

Znuny's schema points 161 foreign keys at ``users.id`` across 77 tables
(``create_by``/``change_by`` on nearly every table, plus ``ticket.user_id``,
``responsible_user_id``, ``group_user``, …), none of them with an ``ON
DELETE`` clause. The database therefore refuses a ``DELETE FROM users`` the
moment any row still points at the account, which is why the admin API's
normal "delete" is a soft ``valid_id = 2``.

A genuine delete is still worth having for the account created by mistake
and never used. This module answers two questions:

* which rows *belong to* the agent and go away with them (preferences, group
  and role assignment, 2FA enrolment, …)
* which rows are *authored or owned* by the agent and must block the delete,
  because removing the account would orphan them

References are discovered from ``information_schema`` rather than hard-coded,
so a schema that gains a table (a Znuny upgrade, a custom add-on) is covered
without touching this file. The ``tiqora_*`` tables carry no FK constraints,
so their user columns are listed explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Rows that exist only because the agent does: deleting the account should
# take them with it rather than refuse. Order matters — children first.
OWNED: tuple[tuple[str, str], ...] = (
    ("tiqora_user_totp", "user_id"),
    ("tiqora_user_passkey", "user_id"),
    ("tiqora_user_auth_config", "user_id"),
    ("tiqora_form_draft", "user_id"),
    ("tiqora_standard_template_user", "user_id"),
    ("tiqora_api_key", "user_id"),
    ("user_preferences", "user_id"),
    ("group_user", "user_id"),
    ("role_user", "user_id"),
    ("personal_queues", "user_id"),
    ("personal_services", "user_id"),
    ("ticket_watcher", "user_id"),
)
_OWNED_KEYS = frozenset(OWNED)

# `tiqora_*` tables have no FK constraints, so information_schema does not
# report them. These are the ones that must BLOCK a delete (things the agent
# created for others), as opposed to the owned rows above.
EXTRA_BLOCKING: tuple[tuple[str, str], ...] = (
    ("tiqora_api_key", "created_by"),
    ("tiqora_mail_outbound", "change_by"),
)


@dataclass(frozen=True, slots=True)
class Reference:
    table: str
    column: str


async def _fk_columns_referencing_users(session: AsyncSession) -> list[Reference]:
    """Every (table, column) with a declared FK to ``users.id``."""
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect.startswith("postgres"):
        stmt = text(
            "SELECT kcu.table_name, kcu.column_name"
            " FROM information_schema.table_constraints tc"
            " JOIN information_schema.key_column_usage kcu"
            "   ON tc.constraint_name = kcu.constraint_name"
            "  AND tc.constraint_schema = kcu.constraint_schema"
            " JOIN information_schema.constraint_column_usage ccu"
            "   ON ccu.constraint_name = tc.constraint_name"
            "  AND ccu.constraint_schema = tc.constraint_schema"
            " WHERE tc.constraint_type = 'FOREIGN KEY'"
            "   AND tc.table_schema = current_schema()"
            "   AND ccu.table_name = 'users' AND ccu.column_name = 'id'"
        )
    else:
        stmt = text(
            "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE"
            " WHERE TABLE_SCHEMA = DATABASE()"
            "   AND REFERENCED_TABLE_NAME = 'users' AND REFERENCED_COLUMN_NAME = 'id'"
        )
    rows = (await session.execute(stmt)).all()
    return [Reference(table=str(t), column=str(c)) for t, c in rows]


async def _table_exists(session: AsyncSession, table: str) -> bool:
    dialect = session.bind.dialect.name if session.bind is not None else ""
    schema_fn = "current_schema()" if dialect.startswith("postgres") else "DATABASE()"
    stmt = text(
        "SELECT 1 FROM information_schema.tables"
        f" WHERE table_schema = {schema_fn} AND table_name = :t LIMIT 1"
    )
    return (await session.execute(stmt, {"t": table})).first() is not None


def _quote(dialect: str, ident: str) -> str:
    # Identifiers come from information_schema / the constants above, never
    # from a request — quoting is belt-and-braces, not the safety boundary.
    if dialect.startswith("postgres"):
        return '"' + ident.replace('"', '""') + '"'
    return "`" + ident.replace("`", "``") + "`"


async def _has_rows(session: AsyncSession, ref: Reference, user_id: int) -> bool:
    """EXISTS rather than COUNT: the answer is yes/no and some of these tables
    (ticket, article, ticket_history) are large and unindexed on create_by."""
    dialect = session.bind.dialect.name if session.bind is not None else ""
    table = _quote(dialect, ref.table)
    column = _quote(dialect, ref.column)
    # `users` references itself (create_by/change_by). The account's own row
    # must not count as a blocker for deleting that same account.
    extra = " AND id <> :uid" if ref.table == "users" else ""
    stmt = text(f"SELECT 1 FROM {table} WHERE {column} = :uid{extra} LIMIT 1")  # noqa: S608
    return (await session.execute(stmt, {"uid": user_id})).first() is not None


async def blocking_references(session: AsyncSession, user_id: int) -> list[Reference]:
    """References that must prevent a hard delete, sorted for stable output."""
    candidates = [
        ref
        for ref in await _fk_columns_referencing_users(session)
        if (ref.table, ref.column) not in _OWNED_KEYS
    ]
    for table, column in EXTRA_BLOCKING:
        if await _table_exists(session, table):
            candidates.append(Reference(table=table, column=column))

    found = [ref for ref in candidates if await _has_rows(session, ref, user_id)]
    return sorted(found, key=lambda r: (r.table, r.column))


async def delete_user_rows(session: AsyncSession, user_id: int) -> None:
    """Delete the agent's own rows and the account. Caller must have checked
    :func:`blocking_references` first and owns the transaction."""
    dialect = session.bind.dialect.name if session.bind is not None else ""
    for table, column in OWNED:
        if not await _table_exists(session, table):
            continue
        stmt = text(  # noqa: S608
            f"DELETE FROM {_quote(dialect, table)} WHERE {_quote(dialect, column)} = :uid"
        )
        await session.execute(stmt, {"uid": user_id})
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
