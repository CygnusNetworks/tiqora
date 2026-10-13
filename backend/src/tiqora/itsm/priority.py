"""Priority matrix: criticality x impact -> priority (I8).

Mode A reads Znuny's ``cip_allocate`` (read-only, exactly like ``ITSMCIPAllocate::
PriorityAllocationGet``: an exact, case sensitive string lookup, no fallback to a neighbouring
cell). Every other mode keeps its own cells in ``tiqora_itsm_priority_matrix``.

The matrix is applied only in Tiqora writes (ticket PATCH), only when the criticality or the
impact field changed in that write, and never when the same write sets the priority itself (the
agent's override). Znuny's ITSM screens behave the same: the matrix only preselects the priority,
and a server-side recomputation bypasses the ``priority`` permission key (review ruling F33; the
agent who may edit the ITSM fields causes the change).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.domain.dynamic_field_values import (
    df_values_differ,
    get_df_values,
    load_ticket_dynamic_field,
    possible_values_of,
)
from tiqora.itsm.ci_provider import CiReadOnly, CiUnavailable
from tiqora.itsm.mode import ItsmModeState, get_itsm_mode
from tiqora.itsm.models import TiqoraItsmPriorityMatrix
from tiqora.itsm.settings import ItsmSettings, load_itsm_settings
from tiqora.itsm.znuny_adapter.reader import get_reader
from tiqora.znuny.sysconfig import SysConfig


@dataclass(frozen=True, slots=True)
class MatrixCell:
    criticality: str
    impact: str
    priority_id: int


@dataclass(frozen=True, slots=True)
class MatrixView:
    source: Literal["znuny", "tiqora"]  # znuny = cip_allocate (Mode A, read-only)
    editable: bool
    criticality_values: tuple[str, ...]  # DF PossibleValues keys in DF order
    impact_values: tuple[str, ...]
    cells: tuple[MatrixCell, ...]


class InvalidMatrix(ValueError):
    """The submitted cells are not a valid matrix (``reason`` is a stable code)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def lookup(cells: Sequence[MatrixCell], criticality: str | None, impact: str | None) -> int | None:
    """``PriorityAllocationGet``: exact match on both keys; empty input or no cell is ``None``."""
    if not criticality or not impact:
        return None
    for cell in cells:
        if cell.criticality == criticality and cell.impact == impact:
            return cell.priority_id
    return None


def _source_of(mode: ItsmModeState) -> Literal["znuny", "tiqora"]:
    return "znuny" if mode.mode == "znuny_readthrough" else "tiqora"


async def _znuny_cells(session: AsyncSession) -> list[MatrixCell]:
    reader = await get_reader(session)  # CiUnavailable when the Znuny tables are not usable
    return [
        MatrixCell(r.criticality, r.impact, r.priority_id)
        for r in await reader.cip_allocate(session)
    ]


async def _tiqora_cells(session: AsyncSession) -> list[MatrixCell]:
    rows = (
        await session.execute(
            select(
                TiqoraItsmPriorityMatrix.criticality,
                TiqoraItsmPriorityMatrix.impact,
                TiqoraItsmPriorityMatrix.priority_id,
            ).order_by(TiqoraItsmPriorityMatrix.id)
        )
    ).all()
    return [MatrixCell(str(r[0]), str(r[1]), int(r[2])) for r in rows]


async def _cells(session: AsyncSession, mode: ItsmModeState) -> list[MatrixCell]:
    return await (_znuny_cells(session) if _source_of(mode) == "znuny" else _tiqora_cells(session))


async def _field_keys(session: AsyncSession, field_name: str) -> tuple[str, ...]:
    field = await load_ticket_dynamic_field(session, field_name)
    if field is None:
        return ()
    return tuple(await possible_values_of(session, field))


def _ordered(preferred: Sequence[str], seen: Sequence[str]) -> tuple[str, ...]:
    """DF order first, then any key the cells use that the field does not list."""
    out = list(preferred)
    out.extend(v for v in dict.fromkeys(seen) if v not in out)
    return tuple(out)


async def load_matrix(
    session: AsyncSession, mode: ItsmModeState, settings: ItsmSettings
) -> MatrixView:
    cells = await _cells(session, mode)
    source = _source_of(mode)
    return MatrixView(
        source=source,
        editable=source == "tiqora",
        criticality_values=_ordered(
            await _field_keys(session, settings.criticality_field), [c.criticality for c in cells]
        ),
        impact_values=_ordered(
            await _field_keys(session, settings.impact_field), [c.impact for c in cells]
        ),
        cells=tuple(cells),
    )


async def _check_cells(session: AsyncSession, cells: Sequence[MatrixCell]) -> None:
    seen: set[tuple[str, str]] = set()
    for c in cells:
        if not c.criticality.strip() or not c.impact.strip():
            raise InvalidMatrix("empty_key")
        if len(c.criticality) > 200 or len(c.impact) > 200:
            raise InvalidMatrix("key_too_long")
        if (c.criticality, c.impact) in seen:
            raise InvalidMatrix("duplicate_cell", f"{c.criticality} / {c.impact}")
        seen.add((c.criticality, c.impact))
    wanted = {c.priority_id for c in cells}
    if not wanted:
        return
    found = {
        int(r[0])
        for r in (
            await session.execute(
                text("SELECT id FROM ticket_priority WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": sorted(wanted)},
            )
        ).all()
    }
    if wanted - found:
        raise InvalidMatrix("unknown_priority", ", ".join(str(i) for i in sorted(wanted - found)))


async def _replace(session: AsyncSession, cells: Sequence[MatrixCell], user_id: int) -> None:
    await session.execute(delete(TiqoraItsmPriorityMatrix))
    now = _now()
    session.add_all(
        TiqoraItsmPriorityMatrix(
            criticality=c.criticality,
            impact=c.impact,
            priority_id=c.priority_id,
            change_time=now,
            change_by=user_id,
        )
        for c in cells
    )
    await session.flush()


async def save_matrix(session: AsyncSession, cells: Sequence[MatrixCell], *, user_id: int) -> None:
    """Replace all cells. Mode A -> :class:`CiReadOnly` (the matrix is Znuny's). Flush only."""
    if _source_of(await get_itsm_mode(session)) == "znuny":
        raise CiReadOnly("the priority matrix is read from Znuny (cip_allocate)")
    await _check_cells(session, cells)
    await _replace(session, cells, user_id)


async def replace_matrix_from_import(
    session: AsyncSession, cells: Sequence[MatrixCell], *, user_id: int
) -> None:
    """The ITSM import copies ``cip_allocate`` into Tiqora's table (same run as the mode switch)."""
    await _check_cells(session, cells)
    await _replace(session, cells, user_id)


async def suggest_priority(
    session: AsyncSession,
    mode: ItsmModeState,
    settings: ItsmSettings,
    *,
    criticality: str | None,
    impact: str | None,
) -> int | None:
    """What the matrix says for the pair; ``None`` when off/disabled/unknown/no cell."""
    if mode.mode == "off" or not settings.priority_matrix_enabled:
        return None
    if not criticality or not impact:
        return None
    try:
        cells = await _cells(session, mode)
    except CiUnavailable:
        return None
    return lookup(cells, criticality, impact)


async def snapshot_matrix_fields(
    session: AsyncSession,
    *,
    ticket_id: int,
    submitted: Mapping[str, list[str]],
    settings: ItsmSettings,
) -> dict[str, list[str]]:
    """Current values of the criticality / impact field when *submitted* touches them.

    Call before the dynamic-field write; pass the result to :func:`changed_matrix_fields`.
    """
    out: dict[str, list[str]] = {}
    for name in (settings.criticality_field, settings.impact_field):
        if name not in submitted:
            continue
        field = await load_ticket_dynamic_field(session, name)
        if field is not None:
            out[name] = await get_df_values(session, field, ticket_id)
    return out


def changed_matrix_fields(
    before: Mapping[str, list[str]], submitted: Mapping[str, list[str]]
) -> dict[str, list[str]]:
    """The subset of *submitted* whose value differs from the snapshot taken before the write."""
    return {
        name: list(submitted[name])
        for name, old in before.items()
        if df_values_differ(old, submitted[name])
    }


async def apply_matrix_after_write(
    session: AsyncSession,
    *,
    ticket_id: int,
    changed_fields: Mapping[str, list[str]],
    explicit_priority: bool,
    user_id: int,
    sysconfig: SysConfig,
    itsm_settings: ItsmSettings | None = None,
) -> int | None:
    """Set the priority from the matrix after criticality or impact changed.

    Returns the new priority id, or ``None`` when nothing was changed (no relevant change, an
    explicit priority in the same write, mode off, matrix disabled, an unknown pair or a priority
    that is already right). Runs inside the caller's transaction.
    """
    if explicit_priority or not changed_fields:
        return None
    from tiqora.config import get_settings  # local: keeps the module importable without config
    from tiqora.domain.ticket_write_service import change_priority

    mode = await get_itsm_mode(session)
    if mode.mode == "off":
        return None
    settings = itsm_settings or await load_itsm_settings(session, get_settings())
    if not settings.priority_matrix_enabled:
        return None
    if (
        settings.criticality_field not in changed_fields
        and settings.impact_field not in changed_fields
    ):
        return None
    values: dict[str, str | None] = {}
    for name in (settings.criticality_field, settings.impact_field):
        field = await load_ticket_dynamic_field(session, name)
        current = await get_df_values(session, field, ticket_id) if field is not None else []
        values[name] = current[0] if current else None
    target = await suggest_priority(
        session,
        mode,
        settings,
        criticality=values[settings.criticality_field],
        impact=values[settings.impact_field],
    )
    if target is None:
        return None
    row = (
        await session.execute(
            text("SELECT ticket_priority_id FROM ticket WHERE id = :t"), {"t": ticket_id}
        )
    ).first()
    if row is None or int(row[0]) == target:
        return None
    await change_priority(
        session,
        ticket_id=ticket_id,
        new_priority_id=target,
        user_id=user_id,
        sysconfig=sysconfig,
    )
    return target
