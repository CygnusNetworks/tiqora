"""Znuny ``sessions`` table maintenance: revocation and expiry purge.

Shared by the compat layer (SessionCreate writes the rows), the password/GDPR flows (revoke) and
the worker (purge). Ids are loaded first and deleted by IN-list: MySQL rejects a DELETE that
selects from the table it deletes from, and the tables mix collations in prod.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.sysconfig import SysConfig

_CHUNK = 500


async def _delete_ids(session: AsyncSession, ids: Sequence[str]) -> None:
    stmt = text("DELETE FROM sessions WHERE session_id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    for i in range(0, len(ids), _CHUNK):
        await session.execute(stmt, {"ids": list(ids[i : i + _CHUNK])})


async def _session_ids_of(session: AsyncSession, user_id_value: str, user_type: str) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT a.session_id FROM sessions a JOIN sessions b"
            " ON a.session_id = b.session_id"
            " WHERE a.data_key = 'UserID' AND a.data_value = :uid"
            " AND b.data_key = 'UserType' AND b.data_value = :ut"
        ),
        {"uid": user_id_value, "ut": user_type},
    )
    return [str(r[0]) for r in rows.all()]


async def delete_agent_sessions(session: AsyncSession, user_id: int) -> int:
    """Delete every agent session of *user_id*; the caller commits."""
    ids = await _session_ids_of(session, str(user_id), "User")
    await _delete_ids(session, ids)
    return len(ids)


async def delete_customer_sessions(session: AsyncSession, login: str) -> int:
    """Delete every customer session of *login* (UserType=Customer, UserID=<login>)."""
    ids = await _session_ids_of(session, login, "Customer")
    await _delete_ids(session, ids)
    return len(ids)


def _epoch(raw: str | None) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 0


async def purge_expired_sessions(session: AsyncSession, *, now: int, sysconfig: SysConfig) -> int:
    """Port of ``AuthSession::DB GetExpiredSessionIDs`` + ``Maint::Session::DeleteExpired``.

    Expired: ``UserSessionStart + SessionMaxTime < now`` or
    ``UserLastRequest + SessionMaxIdleTime < now``; a missing or unreadable stamp counts as 0.
    The caller commits.
    """
    max_time = await sysconfig.session_max_time()
    max_idle = await sysconfig.session_max_idle_time()
    rows = await session.execute(
        text(
            "SELECT session_id, data_key, data_value FROM sessions"
            " WHERE data_key IN ('UserSessionStart', 'UserLastRequest')"
        )
    )
    stamps: dict[str, dict[str, int]] = {}
    for sid, key, value in rows.all():
        stamps.setdefault(str(sid), {})[str(key)] = _epoch(value)
    # Row-sets that carry neither stamp are invisible to the query above; Znuny treats them as
    # expired as well.
    stampless = await session.execute(
        text(
            "SELECT DISTINCT session_id FROM sessions WHERE session_id NOT IN"
            " (SELECT session_id FROM sessions"
            "  WHERE data_key IN ('UserSessionStart', 'UserLastRequest'))"
        )
    )
    expired = [str(r[0]) for r in stampless.all()]
    for sid, data in stamps.items():
        start = data.get("UserSessionStart", 0)
        last = data.get("UserLastRequest", 0)
        if start + max_time < now or last + max_idle < now:
            expired.append(sid)
    await _delete_ids(session, expired)
    return len(expired)
