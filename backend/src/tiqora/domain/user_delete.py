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

from tiqora.znuny.sessions import delete_agent_sessions

# Rows that exist only because the agent does: deleting the account should
# take them with it rather than refuse. Order matters — children first.
OWNED: tuple[tuple[str, str], ...] = (
    ("tiqora_password_setup_token", "user_id"),
    ("tiqora_user_totp", "user_id"),
    ("tiqora_user_passkey", "user_id"),
    ("tiqora_user_auth_config", "user_id"),
    # SSO: role/group rows a provider rule created, and the IdP-subject links
    # (agent kind only -- customer ids live in the same column space).
    ("tiqora_identity_managed_assignment", "user_id"),
    ("tiqora_identity_link", "user_id"),
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
# Tables whose user_id column is shared by agents and customers, told apart by user_kind.
_KIND_SCOPED = frozenset({"tiqora_identity_link"})

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
    # `users` references itself through create_by/change_by, and a row that
    # points at *itself* is a genuine blocker: MySQL rejects the DELETE with
    # errno 1451 rather than resolving the cycle. That is the right outcome —
    # an agent whose own row records them as the last editor (e.g. after they
    # redeemed a setup link) has used the account, so refusing beats a raw
    # integrity error.
    stmt = text(f"SELECT 1 FROM {table} WHERE {column} = :uid LIMIT 1")  # noqa: S608
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
    await _delete_report_schedules(session, user_id)
    for table, column in OWNED:
        if not await _table_exists(session, table):
            continue
        kind = " AND user_kind = 'agent'" if table in _KIND_SCOPED else ""
        stmt = text(  # noqa: S608
            f"DELETE FROM {_quote(dialect, table)} WHERE {_quote(dialect, column)} = :uid{kind}"
        )
        await session.execute(stmt, {"uid": user_id})
    # The agent's Znuny/GI sessions carry their name and e-mail: they go with the account.
    await delete_agent_sessions(session, user_id)
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})


async def _delete_report_schedules(session: AsyncSession, user_id: int) -> None:
    """Scheduled reports of the agent: their schedules (items/recipients), run history and
    files go away; the agent as a recipient of other people's schedules is dropped."""
    if not await _table_exists(session, "tiqora_report_schedule"):
        return
    own = "SELECT id FROM tiqora_report_schedule WHERE owner_user_id = :uid"
    runs = f"SELECT id FROM tiqora_report_run WHERE owner_user_id = :uid OR schedule_id IN ({own})"
    params = {"uid": user_id}
    for stmt in (
        f"DELETE FROM tiqora_report_run_file WHERE run_id IN ({runs})",  # noqa: S608
        "DELETE FROM tiqora_report_run WHERE owner_user_id = :uid"  # noqa: S608
        f" OR schedule_id IN ({own})",
        f"DELETE FROM tiqora_report_schedule_recipient WHERE schedule_id IN ({own})",  # noqa: S608
        f"DELETE FROM tiqora_report_schedule_item WHERE schedule_id IN ({own})",  # noqa: S608
        "DELETE FROM tiqora_report_schedule WHERE owner_user_id = :uid",
        "DELETE FROM tiqora_report_schedule_recipient WHERE kind = 'user' AND ref_id = :uid",
    ):
        await session.execute(text(stmt), params)
