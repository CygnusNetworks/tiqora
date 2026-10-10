"""First-visit dashboard: role template copy or the built-in default (D4)."""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.dashboard import store
from tiqora.dashboard.defaults import (
    DEFAULT_TEMPLATE_KEY,
    DEFAULT_TEMPLATE_VERSION,
    default_widgets,
)
from tiqora.dashboard.schemas import DashboardOut
from tiqora.db.tiqora.models import TiqoraDashboard, TiqoraDashboardRole, TiqoraDashboardWidget
from tiqora.jobs.service import utc_now

__all__ = ["START_NAME", "ensure_start"]

START_NAME: Final = "Mein Start"
_ATTEMPTS: Final = 3


_RETRY_CODES: Final = frozenset({1213, 1205})  # MariaDB: deadlock, lock wait timeout
_RETRY_STATES: Final = frozenset({"40001", "40P01"})  # PostgreSQL: serialization, deadlock


def is_race_error(exc: DBAPIError) -> bool:
    """Unique violation, deadlock, lock wait timeout or serialization failure."""
    if isinstance(exc, IntegrityError):
        return True
    orig = exc.orig
    args = getattr(orig, "args", ())
    if args and isinstance(args[0], int) and args[0] in _RETRY_CODES:
        return True
    for source in (orig, getattr(orig, "__cause__", None)):
        state = getattr(source, "sqlstate", None) or getattr(source, "pgcode", None)
        if state in _RETRY_STATES:
            return True
    return False


async def _pick_template(session: AsyncSession, role_ids: list[int]) -> TiqoraDashboard | None:
    """First unlocked template of the user's roles by (sort, id)."""
    if not role_ids:
        return None
    found = await session.execute(
        select(TiqoraDashboardRole.dashboard_id).where(TiqoraDashboardRole.role_id.in_(role_ids))
    )
    ids = sorted({int(i) for i in found.scalars().all()})
    if not ids:
        return None
    res = await session.execute(
        select(TiqoraDashboard)
        .where(
            TiqoraDashboard.id.in_(ids),
            TiqoraDashboard.kind == "template",
            TiqoraDashboard.locked.is_(False),
        )
        .order_by(TiqoraDashboard.sort, TiqoraDashboard.id)
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _template_copies(session: AsyncSession, template_id: int) -> list[TiqoraDashboardWidget]:
    res = await session.execute(
        select(TiqoraDashboardWidget)
        .where(TiqoraDashboardWidget.dashboard_id == template_id)
        .order_by(TiqoraDashboardWidget.position, TiqoraDashboardWidget.id)
    )
    return [
        TiqoraDashboardWidget(
            id=uuid.uuid4().hex,
            dashboard_id=0,
            type_key=w.type_key,
            type_version=w.type_version,
            title_override=w.title_override,
            settings=w.settings,
            layout=w.layout,
            position=i,
        )
        for i, w in enumerate(res.scalars().all())
    ]


async def ensure_start(session: AsyncSession, user_id: int) -> tuple[DashboardOut, bool]:
    """Return the user's first dashboard, creating "Mein Start" on the first visit.

    Commits itself. Locked templates are tabs, not copies, so a user who only sees a locked
    template still gets their own start dashboard. Concurrent first visits are serialised
    by ``uq_tiqora_dashboard_source``: the loser rolls back and returns the winner's row.
    """
    for attempt in range(_ATTEMPTS):
        own = await store._own_rows(session, user_id)
        if own:
            out = await store.dashboard_out(session, own[0], user_id, admin=False)
            await session.rollback()
            return out, False
        template = await _pick_template(session, await store.user_role_ids(session, user_id))
        now = utc_now()
        try:
            row = TiqoraDashboard(
                owner_user_id=user_id,
                name=START_NAME,
                sort=0,
                kind="personal",
                locked=False,
                source_key=f"tpl:{template.id}" if template else DEFAULT_TEMPLATE_KEY,
                source_template_id=template.id if template else None,
                source_template_version=template.version if template else DEFAULT_TEMPLATE_VERSION,
                filters="{}",
                version=1,
                created_at=now,
                changed_at=now,
            )
            if template is not None:
                copies = await _template_copies(session, template.id)
            else:
                copies = [store.widget_row(0, w, i) for i, w in enumerate(default_widgets())]
            session.add(row)
            await session.flush()
            for c in copies:
                c.dashboard_id = row.id
                session.add(c)
            await session.commit()
        except DBAPIError as exc:
            # unique violation (or a deadlock between concurrent first visits): the other
            # request has created the dashboard, or will have by the next attempt
            await session.rollback()
            if not is_race_error(exc) or attempt == _ATTEMPTS - 1:
                raise
            continue
        fresh = await store._load_row(session, row.id)
        assert fresh is not None
        return await store.dashboard_out(session, fresh, user_id, admin=False), True
    raise AssertionError("unreachable")  # pragma: no cover
