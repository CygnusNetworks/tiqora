"""Difference of two CI versions (D5): name, states, then attribute leaves by positional path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.access import CmdbAccess
from tiqora.cmdb.render import flatten, render_values
from tiqora.cmdb.service import CiService, VersionRecord
from tiqora.db.tiqora.models import TiqoraCmdbCatalog


@dataclass(frozen=True, slots=True)
class DiffRow:
    path: str
    label: str
    kind: Literal["added", "removed", "changed"]
    old: str | None
    new: str | None


def _row(path: str, label: str, old: str | None, new: str | None) -> DiffRow | None:
    if old == new:
        return None
    if old is None:
        return DiffRow(path, label, "added", None, new)
    if new is None:
        return DiffRow(path, label, "removed", old, None)
    return DiffRow(path, label, "changed", old, new)


async def _state_names(session: AsyncSession, ids: set[int]) -> dict[int, str]:
    rows = await session.execute(
        select(TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.name).where(
            TiqoraCmdbCatalog.id.in_(sorted(ids))
        )
    )
    return {int(i): str(n) for i, n in rows.all()}


async def _leaves(
    session: AsyncSession, access: CmdbAccess, v: VersionRecord
) -> dict[str, tuple[str, str]]:
    """positional path -> (label, display), in document order."""
    nodes = await render_values(session, v.definition_attrs, v.attributes, access=access)
    return {n.path: (n.label, n.display or "") for n in flatten(nodes)}


async def diff_versions(
    session: AsyncSession,
    access: CmdbAccess,
    ci_id: int,
    from_version_id: int,
    to_version_id: int,
) -> list[DiffRow]:
    """Rows for ``name``, ``deployment_state`` and ``incident_state`` first, then attribute
    leaves (``NIC[1].IPAddress[2]``). Each version is rendered with **its own** definition, so
    an attribute removed from the class still shows up as removed. Both versions must belong
    to ``ci_id`` and the caller needs ``ro`` on its class."""
    svc = CiService(session)
    old = await svc.get_version(access, ci_id, from_version_id)
    new = await svc.get_version(access, ci_id, to_version_id)
    rows: list[DiffRow | None] = [_row("name", "Name", old.name, new.name)]
    names = await _state_names(
        session,
        {
            old.deployment_state_id,
            new.deployment_state_id,
            old.incident_state_id,
            new.incident_state_id,
        },
    )
    rows.append(
        _row(
            "deployment_state",
            "Betriebsstatus",
            names.get(old.deployment_state_id, str(old.deployment_state_id)),
            names.get(new.deployment_state_id, str(new.deployment_state_id)),
        )
    )
    rows.append(
        _row(
            "incident_state",
            "Incident-Status",
            names.get(old.incident_state_id, str(old.incident_state_id)),
            names.get(new.incident_state_id, str(new.incident_state_id)),
        )
    )
    before = await _leaves(session, access, old)
    after = await _leaves(session, access, new)
    for path, (label, display) in after.items():
        if path not in before:
            rows.append(_row(path, label, None, display))
        else:
            rows.append(_row(path, label, before[path][1], display))
    for path, (label, display) in before.items():
        if path not in after:
            rows.append(_row(path, label, display, None))
    return [r for r in rows if r is not None]


__all__ = ["DiffRow", "diff_versions"]
