"""SessionCreate writing Znuny ``sessions`` rows (``AuthSession::DB`` format).

The rows carry exactly the keys Znuny's ``CreateSessionID`` stores, so a session created
through Tiqora is valid in Znuny and vice versa. Two deliberate omissions for security:
``UserPw`` (the password hash) is never copied, and preference rows that look like
secrets (2FA seeds, tokens, API keys) are skipped.
"""

from __future__ import annotations

import re
import secrets
import string
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.ticket_get import znuny_ts
from tiqora.config import Settings
from tiqora.znuny.sysconfig import SysConfig

_ALPHABET = string.ascii_letters + string.digits
_SESSION_ID_LENGTH = 32
_SECRET_KEY_RE = re.compile(r"secret|token|pass|pw|key|cert|auth|hash", re.IGNORECASE)

# Keys SessionGet may hand out (everything else - secrets included - stays private).
SESSION_GET_ALLOWED_KEYS = frozenset(
    {
        "UserID",
        "UserLogin",
        "UserType",
        "UserTitle",
        "UserFirstname",
        "UserLastname",
        "UserFullname",
        "UserEmail",
        "UserCustomerID",
        "ValidID",
        "CreateTime",
        "ChangeTime",
        "UserLastRequest",
        "UserSessionStart",
        "UserRemoteAddr",
        "UserRemoteUserAgent",
        "SessionSource",
    }
)


def random_string(length: int = _SESSION_ID_LENGTH) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


async def znuny_session_writes_enabled(settings: Settings, sysconfig: SysConfig) -> bool:
    if not settings.compat_session_write_znuny or settings.schema_ownership:
        return False
    module = str(await sysconfig.get("SessionModule", "Kernel::System::AuthSession::DB") or "")
    return module.endswith("AuthSession::DB")


def _utf8(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw).decode("utf-8", errors="replace")
    return str(raw)


def _fullname(order: str, first: str, last: str, login: str) -> str:
    forms = {
        "0": f"{first} {last}",
        "1": f"{last}, {first}",
        "2": f"{first} {last} ({login})",
        "3": f"{last}, {first} ({login})",
        "4": f"({login}) {first} {last}",
        "5": f"({login}) {last}, {first}",
        "6": f"{last} {first}",
        "7": f"{last} {first} ({login})",
        "8": f"({login}) {last} {first}",
        "9": f"{last}{first}",
    }
    return forms.get(order, "")


async def znuny_agent_user_data(
    session: AsyncSession, user_id: int, *, sysconfig: SysConfig | None = None
) -> dict[str, str]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, login, title, first_name, last_name, valid_id, create_time,"
                    " change_time FROM users WHERE id = :uid"
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    order = str(await (sysconfig or SysConfig(session)).get("FirstnameLastnameOrder", 0) or 0)
    data: dict[str, str] = {
        "UserID": str(row["id"]),
        "UserLogin": str(row["login"]),
        "UserTitle": row["title"] or "",
        "UserFirstname": row["first_name"],
        "UserLastname": row["last_name"],
        "UserFullname": _fullname(order, row["first_name"], row["last_name"], row["login"]),
        "ValidID": str(row["valid_id"]),
        "CreateTime": znuny_ts(row["create_time"]),
        "ChangeTime": znuny_ts(row["change_time"]),
    }
    prefs = (
        await session.execute(
            text(
                "SELECT preferences_key, preferences_value FROM user_preferences"
                " WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
    ).all()
    for key, value in prefs:
        if key and not _SECRET_KEY_RE.search(str(key)):
            data.setdefault(str(key), _utf8(value))
    return data


async def znuny_customer_user_data(session: AsyncSession, login: str) -> dict[str, str]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT login, email, customer_id, title, first_name, last_name, valid_id"
                    " FROM customer_user WHERE login = :login"
                ),
                {"login": login},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    data: dict[str, str] = {
        "UserLogin": str(row["login"]),
        "UserID": str(row["login"]),
        "UserCustomerID": row["customer_id"] or "",
        "UserEmail": row["email"] or "",
        "UserTitle": row["title"] or "",
        "UserFirstname": row["first_name"],
        "UserLastname": row["last_name"],
        "UserFullname": f"{row['first_name']} {row['last_name']}",
        "ValidID": str(row["valid_id"]),
    }
    prefs = (
        await session.execute(
            text(
                "SELECT preferences_key, preferences_value FROM customer_preferences"
                " WHERE user_id = :login"
            ),
            {"login": login},
        )
    ).all()
    for key, value in prefs:
        if key and not _SECRET_KEY_RE.search(str(key)):
            data.setdefault(str(key), _utf8(value))
    return data


async def write_znuny_session(
    session: AsyncSession,
    *,
    user_data: Mapping[str, str],
    user_type: str,
    remote_addr: str,
    user_agent: str,
    now_epoch: int,
) -> str:
    """Insert the session rows; the caller commits. Never writes ``UserPw``."""
    session_id = random_string()
    data: dict[str, str] = {k: v for k, v in user_data.items() if k and k != "UserPw"}
    data.update(
        {
            "UserLastRequest": str(now_epoch),
            "UserType": user_type,
            "SessionSource": "GenericInterface",
            "UserSessionStart": str(now_epoch),
            "UserRemoteAddr": remote_addr or "none",
            "UserRemoteUserAgent": user_agent or "none",
            "UserChallengeToken": random_string(),
        }
    )
    await session.execute(text("DELETE FROM sessions WHERE session_id = :sid"), {"sid": session_id})
    values: list[str] = []
    params: dict[str, Any] = {"sid": session_id}
    for i, key in enumerate(sorted(data)):
        values.append(f"(:sid, :k{i}, :v{i}, 0)")
        params[f"k{i}"] = key
        params[f"v{i}"] = data[key]
    await session.execute(
        text(
            "INSERT INTO sessions (session_id, data_key, data_value, serialized) VALUES "
            + ", ".join(values)
        ),
        params,
    )
    return session_id


async def delete_znuny_agent_sessions(session: AsyncSession, user_id: int) -> int:
    """Delete every agent session row-set of *user_id* (revocation); caller commits.

    Ids are loaded first and deleted by IN-list: MySQL rejects a DELETE that selects from the
    table it deletes from.
    """
    ids = [
        str(r[0])
        for r in (
            await session.execute(
                text(
                    "SELECT a.session_id FROM sessions a JOIN sessions b"
                    " ON a.session_id = b.session_id"
                    " WHERE a.data_key = 'UserID' AND a.data_value = :uid"
                    " AND b.data_key = 'UserType' AND b.data_value = 'User'"
                ),
                {"uid": str(user_id)},
            )
        ).all()
    ]
    for sid in ids:
        await session.execute(text("DELETE FROM sessions WHERE session_id = :sid"), {"sid": sid})
    return len(ids)
