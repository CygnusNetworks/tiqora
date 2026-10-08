"""Znuny ``sessions`` table maintenance: revocation and expiry purge.

Shared by the compat layer (SessionCreate writes the rows), the password/GDPR flows (revoke) and
the worker (purge). Ids are loaded first and deleted by IN-list: MySQL rejects a DELETE that
selects from the table it deletes from, and the tables mix collations in prod.
"""

from __future__ import annotations

import re
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


_NUM_PREFIX = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)")


def _perl_epoch(raw: str | None, now: int) -> float:
    """``$value || $TimeNow`` then Perl numification (leading number, else 0)."""
    if raw is None or raw == "" or raw == "0":
        return now
    m = _NUM_PREFIX.match(str(raw))
    return float(m.group(1)) if m else 0.0


async def purge_expired_sessions(session: AsyncSession, *, now: int, sysconfig: SysConfig) -> int:
    """Exact port of ``AuthSession::DB GetExpiredSessionIDs`` + ``Maint::Session::DeleteExpired``.

    Rows are read ``ORDER BY id`` (last row per key wins). A missing, empty or ``"0"`` stamp
    counts as *now* (not expired); a session is expired when
    ``UserSessionStart + SessionMaxTime - now <= 0`` or
    ``UserLastRequest + SessionMaxIdleTime - now <= 0``. Commits after every delete chunk so
    a big backlog never holds long locks.
    """
    max_time = await sysconfig.session_max_time()
    max_idle = await sysconfig.session_max_idle_time()
    rows = await session.execute(
        text(
            "SELECT session_id, data_key, data_value FROM sessions"
            " WHERE data_key = 'UserSessionStart' OR data_key = 'UserLastRequest'"
            " ORDER BY id ASC"
        )
    )
    data: dict[str, dict[str, str | None]] = {}
    for sid, key, value in rows.all():
        if not sid or not key:
            continue
        data.setdefault(str(sid), {})[str(key)] = None if value is None else str(value)
    await session.commit()  # end the read transaction before the deletes
    expired: list[str] = []
    for sid in sorted(data):
        start = _perl_epoch(data[sid].get("UserSessionStart"), now)
        last = _perl_epoch(data[sid].get("UserLastRequest"), now)
        if start + max_time - now <= 0 or last + max_idle - now <= 0:
            expired.append(sid)
    for i in range(0, len(expired), _CHUNK):
        await _delete_ids(session, expired[i : i + _CHUNK])
        await session.commit()
    return len(expired)
