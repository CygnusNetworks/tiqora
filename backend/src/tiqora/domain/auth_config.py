"""Per-agent auth policy: SSO eligibility + 2FA enforcement.

Backed by ``tiqora_user_auth_config`` (missing row ⇒ both flags false),
the global ``auth.totp.enforce_all`` setting, and the group-based
``auth.totp.enforce_group_ids`` list in ``tiqora_settings``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraUserAuthConfig
from tiqora.domain.settings_store import (
    KEY_TOTP_ENFORCE_ALL,
    KEY_TOTP_ENFORCE_GROUP_IDS,
    get_setting,
    get_setting_bool,
    set_setting,
)
from tiqora.permissions.engine import PermissionEngine


def _utcnow() -> datetime:
    """Naive UTC now — matches DateTime columns (server stores naive)."""
    return datetime.utcnow()  # noqa: DTZ003 — intentional naive UTC for DB columns


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Resolved per-agent auth flags (defaults applied when no row exists)."""

    sso_eligible: bool = False
    enforce_2fa: bool = False


async def get_enforce_group_ids(session: AsyncSession) -> list[int]:
    """Parse ``auth.totp.enforce_group_ids`` (JSON int list). Empty on missing/invalid."""
    raw = await get_setting(session, KEY_TOTP_ENFORCE_GROUP_IDS)
    if raw is None or raw.strip() == "":
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[int] = []
    for item in data:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out.append(item)
        elif isinstance(item, str) and item.strip().lstrip("-").isdigit():
            out.append(int(item))
    # Stable unique order (first-seen) for admin UI round-trips.
    seen: set[int] = set()
    unique: list[int] = []
    for gid in out:
        if gid not in seen:
            seen.add(gid)
            unique.append(gid)
    return unique


async def set_enforce_group_ids(session: AsyncSession, group_ids: list[int]) -> None:
    """Persist the enforced group-id list as a JSON array string."""
    # Dedupe while preserving order.
    seen: set[int] = set()
    clean: list[int] = []
    for gid in group_ids:
        if not isinstance(gid, int) or isinstance(gid, bool):
            continue
        if gid not in seen:
            seen.add(gid)
            clean.append(gid)
    await set_setting(session, KEY_TOTP_ENFORCE_GROUP_IDS, json.dumps(clean))


class AuthConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get_row(self, user_id: int) -> TiqoraUserAuthConfig | None:
        result = await self._session.execute(
            select(TiqoraUserAuthConfig).where(TiqoraUserAuthConfig.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get(self, user_id: int) -> AuthConfig:
        row = await self._get_row(user_id)
        if row is None:
            return AuthConfig()
        return AuthConfig(sso_eligible=bool(row.sso_eligible), enforce_2fa=bool(row.enforce_2fa))

    async def set(
        self,
        user_id: int,
        *,
        sso_eligible: bool | None = None,
        enforce_2fa: bool | None = None,
    ) -> AuthConfig:
        """Upsert per-agent flags. Only non-``None`` kwargs are written."""
        row = await self._get_row(user_id)
        ts = _utcnow()
        if row is None:
            row = TiqoraUserAuthConfig(
                user_id=user_id,
                sso_eligible=bool(sso_eligible) if sso_eligible is not None else False,
                enforce_2fa=bool(enforce_2fa) if enforce_2fa is not None else False,
                created=ts,
                changed=ts,
            )
            self._session.add(row)
        else:
            if sso_eligible is not None:
                row.sso_eligible = sso_eligible
            if enforce_2fa is not None:
                row.enforce_2fa = enforce_2fa
            row.changed = ts
        await self._session.commit()
        await self._session.refresh(row)
        return AuthConfig(sso_eligible=bool(row.sso_eligible), enforce_2fa=bool(row.enforce_2fa))

    async def effective_enforce(self, user_id: int) -> bool:
        """True when per-agent, global, or group-based 2FA enforcement applies."""
        cfg = await self.get(user_id)
        if cfg.enforce_2fa:
            return True
        if await get_setting_bool(self._session, KEY_TOTP_ENFORCE_ALL, default=False):
            return True
        enforced = await get_enforce_group_ids(self._session)
        if not enforced:
            return False
        perms = await PermissionEngine(self._session).queue_permissions(user_id)
        return bool(set(perms.keys()) & set(enforced))
