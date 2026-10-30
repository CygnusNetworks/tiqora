"""ITSM roles of ticket types: the settings name the types, reports work with the roles.

``tiqora.itsm.*`` is imported inside the functions (import cycle through the engine, see
``reporting/scope.py``).
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Final

from sqlalchemy import ColumnElement, String, case, cast, literal, null, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import get_settings
from tiqora.db.legacy.ticket import TicketType
from tiqora.reporting.semantic import CompileContext, Dimension, ExprFn
from tiqora.reporting.sources._common import dim

ITSM_ROLES: Final = (
    "incident",
    "major_incident",
    "service_request",
    "problem",
    "change",
    "change_task",
)
INCIDENT_ROLES: Final = ("incident", "major_incident")


async def load_type_roles(session: AsyncSession) -> dict[int, str]:
    """``ticket_type.id`` -> ITSM role of every configured type that exists."""
    from tiqora.itsm.settings import load_itsm_settings  # noqa: PLC0415 - import cycle
    from tiqora.itsm.summary import role_of  # noqa: PLC0415 - import cycle

    cfg = await load_itsm_settings(session, get_settings())
    names = {
        cfg.incident_type,
        cfg.major_incident_type,
        cfg.service_request_type,
        cfg.problem_type,
        cfg.change_type,
        cfg.change_task_type,
    }
    rows = await session.execute(
        select(TicketType.id, TicketType.name).where(TicketType.name.in_(sorted(names)))
    )
    roles: dict[int, str] = {}
    for type_id, name in rows.all():
        role = role_of(name, cfg)
        if role is not None:  # MariaDB's IN ignores case, the settings do not
            roles[int(type_id)] = role
    return roles


def itsm_role_dim(key: str, type_col: ExprFn) -> Dimension:
    """Enum dimension: the ITSM role of the type in ``type_col``; ``NULL`` for other types."""

    def expr(ctx: CompileContext) -> ColumnElement[Any]:
        if not ctx.type_roles:
            return cast(null(), String(20))
        whens = {
            type_id: literal(role, String(20)) for type_id, role in sorted(ctx.type_roles.items())
        }
        return case(whens, value=type_col(ctx), else_=null())

    return dim(key, "enum", expr, values="reports.values.itsm_role", enum=ITSM_ROLES)


def role_type_ids(ctx: CompileContext, roles: Collection[str]) -> list[int]:
    """Sorted ids of the ticket types whose role is in ``roles``."""
    return sorted(type_id for type_id, role in ctx.type_roles.items() if role in roles)
