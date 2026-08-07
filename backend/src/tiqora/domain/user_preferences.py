"""Shared read/write helpers for the Znuny-style ``user_preferences`` table.

Agent email, mobile phone, and UI language are not columns on ``users`` —
Znuny stores them as key/value rows here (``UserEmail``, ``UserMobile``,
``UserLanguage``). Raw SQL is used for the write because ``LargeBinary``
coercion misbehaves against PostgreSQL's TEXT-typed column in this schema
(see :func:`tiqora.domain.auth.decode_preference_value` for the matching
read-side decoding of the various driver representations).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.domain.auth import decode_preference_value


async def get_preference(session: AsyncSession, user_id: int, key: str) -> str | None:
    result = await session.execute(
        text(
            "SELECT preferences_value FROM user_preferences"
            " WHERE user_id = :uid AND preferences_key = :k"
        ),
        {"uid": user_id, "k": key},
    )
    return decode_preference_value(result.scalar_one_or_none())


async def bulk_get_preferences(
    session: AsyncSession, user_ids: Sequence[int], key: str
) -> dict[int, str]:
    """Return ``{user_id: value}`` for *user_ids* holding *key* — one round trip."""
    if not user_ids:
        return {}
    stmt = text(
        "SELECT user_id, preferences_value FROM user_preferences"
        " WHERE preferences_key = :k AND user_id IN :uids"
    ).bindparams(bindparam("uids", expanding=True))
    result = await session.execute(stmt, {"k": key, "uids": list(user_ids)})
    out: dict[int, str] = {}
    for user_id, raw in result.all():
        decoded = decode_preference_value(raw)
        if decoded is not None:
            out[int(user_id)] = decoded
    return out


async def set_preference(session: AsyncSession, user_id: int, key: str, value: str | None) -> None:
    """Upsert *key* to *value*, or delete the row when *value* is empty/None."""
    if not value:
        await session.execute(
            text(
                "DELETE FROM user_preferences WHERE user_id = :uid AND preferences_key = :k"
            ),
            {"uid": user_id, "k": key},
        )
        return
    existing = (
        await session.execute(
            text(
                "SELECT 1 FROM user_preferences"
                " WHERE user_id = :uid AND preferences_key = :k LIMIT 1"
            ),
            {"uid": user_id, "k": key},
        )
    ).first()
    raw = value.encode("utf-8")
    if existing:
        await session.execute(
            text(
                "UPDATE user_preferences SET preferences_value = :v"
                " WHERE user_id = :uid AND preferences_key = :k"
            ),
            {"v": raw, "uid": user_id, "k": key},
        )
    else:
        await session.execute(
            text(
                "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (:uid, :k, :v)"
            ),
            {"uid": user_id, "k": key, "v": raw},
        )
