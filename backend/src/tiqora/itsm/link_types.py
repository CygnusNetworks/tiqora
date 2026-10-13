"""Link types: the ``tiqora_itsm_link_type`` service and the Znuny link-type catalogue (I6, C4).

Znuny keeps link types in SysConfig (``LinkObject::Type###<Name>``, the object pairs in
``LinkObject::PossibleLink###<nnnn>``, the incident direction in
``ITSM::Core::IncidentLinkTypeDirection``). The table is an *override layer* on top of that
catalogue (ruling F3):

* a Znuny type without a row is still offered (link create must work before any sync ran);
* a row can hide it (``valid`` / offer flag off) or relabel it, never widen what Znuny can store;
* ``sync_link_type_rows`` upserts ``origin="znuny"`` rows by ``name`` and seeds the Tiqora
  defaults of step 0. It never deletes and never overwrites labels, offer flags or ``valid``.

``name`` is the stable key (immutable after insert); the CMDB and the importer refer to it.
All functions flush only, the caller commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

from sqlalchemy import column, delete, inspect, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.link_type_defaults import ensure_default_link_types
from tiqora.itsm.mode import ItsmMode
from tiqora.itsm.models import LINK_TYPE_PROPAGATIONS, TiqoraItsmLinkType
from tiqora.znuny.link_config import read_link_config
from tiqora.znuny.sysconfig import SysConfig

Propagation = Literal["none", "source", "target", "both"]
LinkPair = Literal["ticket_ticket", "ticket_ci", "ci_ci", "ci_service"]

CI_OBJECT: Final = "ITSMConfigItem"
LINK_PAIRS: Final[tuple[LinkPair, ...]] = ("ticket_ticket", "ticket_ci", "ci_ci", "ci_service")
_PAIR_OBJECTS: Final[dict[str, tuple[str, str]]] = {
    "ticket_ticket": ("Ticket", "Ticket"),
    "ticket_ci": ("Ticket", CI_OBJECT),
    "ci_ci": (CI_OBJECT, CI_OBJECT),
    "ci_service": (CI_OBJECT, "Service"),
}
_OFFER_COLUMN: Final[dict[str, str]] = {pair: f"offer_{pair}" for pair in _PAIR_OBJECTS}
_DIRECTION_SETTING: Final = "ITSM::Core::IncidentLinkTypeDirection"
_DIRECTION: Final[dict[str, Propagation]] = {
    "source": "source",
    "target": "target",
    "both": "both",
}
# Position of rows created from Znuny's catalogue: the ticket types first, then by name.
_ZNUNY_FIRST: Final = {"Normal": 1, "ParentChild": 2}
_ZNUNY_POSITION_BASE: Final = 100


@dataclass(frozen=True, slots=True)
class ZnunyLinkType:
    name: str
    source_name: str
    target_name: str
    pointed: bool
    possible: frozenset[tuple[str, str]]  # (Object1, Object2) pairs of LinkObject::PossibleLink
    propagation: Propagation  # ITSM::Core::IncidentLinkTypeDirection (read-only)

    def allows(self, object1: str, object2: str) -> bool:
        """The pair is listed for this type in either order (``PossibleTypesList``)."""
        return (object1, object2) in self.possible or (object2, object1) in self.possible


@dataclass(frozen=True, slots=True)
class LinkTypeOption:
    """One entry of the link type picker; labels are overrides, ``*_name`` Znuny's names."""

    name: str
    source_label: str | None
    target_label: str | None
    source_name: str | None
    target_name: str | None
    pointed: bool
    propagation: Propagation
    origin: Literal["znuny", "tiqora"]


class LinkTypeError(ValueError):
    """Admin rule violation; ``code`` is the stable API error code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def znuny_link_types(sysconfig: SysConfig) -> dict[str, ZnunyLinkType]:
    """The link types Znuny's SysConfig defines, in name order (port of ``LinkObject.pm``).

    Types and object pairs come from the shared reader :func:`read_link_config` (the same the
    link write path uses); the incident direction is ITSM's own setting.
    """
    config = await read_link_config(sysconfig)
    direction = await sysconfig.get(_DIRECTION_SETTING)
    directions: dict[str, Propagation] = {}
    if isinstance(direction, dict):
        for ty, how in direction.items():
            mapped = _DIRECTION.get(str(how).strip().lower())
            if mapped is not None:
                directions[str(ty)] = mapped
    return {
        name: ZnunyLinkType(
            name=name,
            source_name=src,
            target_name=tgt,
            pointed=config.pointed(name),
            possible=config.possible.get(name, frozenset()),
            propagation=directions.get(name, "none"),
        )
        for name, (src, tgt) in config.names.items()
    }


def _offer_flags(znuny: ZnunyLinkType) -> dict[str, bool]:
    return {_OFFER_COLUMN[pair]: znuny.allows(*objects) for pair, objects in _PAIR_OBJECTS.items()}


async def _rows_by_name(session: AsyncSession) -> dict[str, TiqoraItsmLinkType]:
    rows = (await session.execute(select(TiqoraItsmLinkType))).scalars().all()
    return {r.name: r for r in rows}


async def sync_link_type_rows(session: AsyncSession, *, user_id: int) -> int:
    """Upsert ``origin="znuny"`` rows from SysConfig, then seed the Tiqora defaults.

    Returns the number of rows inserted or updated.

    New Znuny rows get ``pointed``, ``propagation`` and the offer flags from the catalogue. For an
    existing ``origin="znuny"`` row only ``pointed`` and ``propagation`` follow SysConfig again
    (the admin cannot edit them); labels, offer flags and ``valid`` are admin overrides and are
    kept. Rows of other origin, rows of types Znuny no longer defines, and every ``valid=0`` row
    stay untouched; nothing is deleted. Flush only.
    """
    catalogue = await znuny_link_types(SysConfig(session))
    existing = await _rows_by_name(session)
    now = _now()
    changed = 0
    order = 0
    for name, znuny in catalogue.items():
        row = existing.get(name)
        if row is None:
            order += 1
            session.add(
                TiqoraItsmLinkType(
                    name=name,
                    origin="znuny",
                    source_label=None,
                    target_label=None,
                    pointed=znuny.pointed,
                    propagation=znuny.propagation,
                    position=_ZNUNY_FIRST.get(name, _ZNUNY_POSITION_BASE + order * 10),
                    valid=True,
                    create_time=now,
                    create_by=user_id,
                    change_time=now,
                    change_by=user_id,
                    **_offer_flags(znuny),
                )
            )
            changed += 1
        elif row.origin == "znuny" and (
            row.pointed != znuny.pointed or row.propagation != znuny.propagation
        ):
            row.pointed = znuny.pointed
            row.propagation = znuny.propagation
            row.change_time = now
            row.change_by = user_id
            changed += 1
    if changed:
        await session.flush()
    return changed + await ensure_default_link_types(session, user_id=user_id)


def _option(row: TiqoraItsmLinkType | None, znuny: ZnunyLinkType | None) -> LinkTypeOption:
    if row is not None:
        return LinkTypeOption(
            name=row.name,
            source_label=row.source_label,
            target_label=row.target_label,
            source_name=znuny.source_name if znuny else None,
            target_name=znuny.target_name if znuny else None,
            pointed=znuny.pointed if znuny else row.pointed,
            propagation=znuny.propagation if znuny and row.origin == "znuny" else _prop(row),
            origin="znuny" if row.origin == "znuny" else "tiqora",
        )
    assert znuny is not None
    return LinkTypeOption(
        name=znuny.name,
        source_label=None,
        target_label=None,
        source_name=znuny.source_name,
        target_name=znuny.target_name,
        pointed=znuny.pointed,
        propagation=znuny.propagation,
        origin="znuny",
    )


def _prop(row: TiqoraItsmLinkType) -> Propagation:
    return row.propagation if row.propagation in LINK_TYPE_PROPAGATIONS else "none"  # type: ignore[return-value]


async def offered_link_types(
    session: AsyncSession, *, pair: LinkPair, mode: ItsmMode
) -> list[LinkTypeOption]:
    """The link types an agent may pick for *pair* in *mode*.

    * ``ticket_ticket`` and every pair in Mode A: only what Znuny can store (a SysConfig type
      whose ``PossibleLink`` lists the object pair) that the table does not hide.
    * Mode B pairs with a CI: the valid rows carrying the pair's offer flag (the CMDB stores
      them itself).
    * Mode ``off``: only ``ticket_ticket``; the CI pairs are empty.
    """
    rows = await _rows_by_name(session)
    flag = _OFFER_COLUMN[pair]
    if mode == "tiqora" and pair != "ticket_ticket":
        picked = [r for r in rows.values() if r.valid and getattr(r, flag)]
        picked.sort(key=lambda r: (r.position, r.name))
        return [_option(r, None) for r in picked]
    if mode == "off" and pair != "ticket_ticket":
        return []
    return await _znuny_offered(session, pair, rows)


async def ticket_ticket_link_types(session: AsyncSession) -> list[LinkTypeOption]:
    """The ticket-ticket link types: the same in every ITSM mode (only what Znuny can store)."""
    return await _znuny_offered(session, "ticket_ticket", await _rows_by_name(session))


async def _znuny_offered(
    session: AsyncSession, pair: LinkPair, rows: dict[str, TiqoraItsmLinkType]
) -> list[LinkTypeOption]:
    """SysConfig types whose ``PossibleLink`` lists the pair, minus what the table hides."""
    flag = _OFFER_COLUMN[pair]
    catalogue = await znuny_link_types(SysConfig(session))
    objects = _PAIR_OBJECTS[pair]
    offered: list[tuple[int, str, LinkTypeOption]] = []
    for name, znuny in catalogue.items():
        if not znuny.allows(*objects):
            continue
        row = rows.get(name)
        if row is not None and not (row.valid and getattr(row, flag)):
            continue
        position = row.position if row is not None else _ZNUNY_POSITION_BASE * 10
        offered.append((position, name, _option(row, znuny)))
    offered.sort(key=lambda t: (t[0], t[1]))
    return [o for _p, _n, o in offered]


# --- admin ---------------------------------------------------------------------------------


async def list_link_types(session: AsyncSession) -> list[TiqoraItsmLinkType]:
    rows = (await session.execute(select(TiqoraItsmLinkType))).scalars().all()
    return sorted(rows, key=lambda r: (r.position, r.name))


async def missing_from_table(session: AsyncSession) -> list[str]:
    """Znuny types in SysConfig that have no row yet (a sync adds them; they are offered)."""
    catalogue = await znuny_link_types(SysConfig(session))
    present = set(await _rows_by_name(session))
    return sorted(set(catalogue) - present)


def _check_flags(propagation: str, offer_ticket_ticket: bool) -> None:
    if propagation not in LINK_TYPE_PROPAGATIONS:
        raise LinkTypeError("invalid_propagation", propagation)
    if offer_ticket_ticket:
        # Znuny's link_relation cannot store a type that is not in its SysConfig
        raise LinkTypeError("offer_ticket_ticket_not_allowed")


async def create_link_type(
    session: AsyncSession,
    *,
    name: str,
    source_label: str | None,
    target_label: str | None,
    pointed: bool,
    propagation: str,
    offers: dict[str, bool],
    position: int | None,
    user_id: int,
) -> TiqoraItsmLinkType:
    """A Tiqora-only type (``origin="tiqora"``)."""
    _check_flags(propagation, offers.get("offer_ticket_ticket", False))
    if name in (await _rows_by_name(session)) or name in (
        await znuny_link_types(SysConfig(session))
    ):
        raise LinkTypeError("link_type_exists", name)
    if position is None:
        rows = await list_link_types(session)
        position = max((r.position for r in rows), default=0) + 10
    now = _now()
    row = TiqoraItsmLinkType(
        name=name,
        origin="tiqora",
        source_label=source_label,
        target_label=target_label,
        pointed=pointed,
        propagation=propagation,
        position=position,
        valid=True,
        create_time=now,
        create_by=user_id,
        change_time=now,
        change_by=user_id,
        **{col: bool(offers.get(col, False)) for col in _OFFER_COLUMN.values()},
    )
    session.add(row)
    await session.flush()
    return row


#: fields of a Znuny-origin row the admin may edit (the rest follows SysConfig)
_ZNUNY_EDITABLE: Final = frozenset(
    {"source_label", "target_label", "valid", "position", *_OFFER_COLUMN.values()}
)


async def update_link_type(
    session: AsyncSession, row: TiqoraItsmLinkType, changes: dict[str, Any], *, user_id: int
) -> TiqoraItsmLinkType:
    """Apply *changes* (only the keys the client sent). ``name`` never changes (R1)."""
    if "name" in changes:
        if changes["name"] != row.name:
            raise LinkTypeError("link_type_name_immutable", row.name)
        changes = {k: v for k, v in changes.items() if k != "name"}
    if row.origin == "znuny":
        locked = sorted(set(changes) - _ZNUNY_EDITABLE)
        if locked:
            raise LinkTypeError("znuny_link_type_read_only", ",".join(locked))
    else:
        _check_flags(
            changes.get("propagation", row.propagation),
            bool(changes.get("offer_ticket_ticket", row.offer_ticket_ticket)),
        )
    for key, value in changes.items():
        setattr(row, key, value)
    row.change_time = _now()
    row.change_by = user_id
    await session.flush()
    return row


async def _link_table_in_use(session: AsyncSession, name: str) -> bool:
    """The CMDB plan's ``tiqora_itsm_link`` (absent on this branch) keys its rows by type name."""

    def _has_table(sync_session: Any) -> bool:
        return bool(inspect(sync_session.connection()).has_table("tiqora_itsm_link"))

    if not await session.run_sync(_has_table):
        return False
    # Lightweight table construct: the CMDB's ORM class is not importable on this branch.
    link = table("tiqora_itsm_link", column("link_type"))
    row = (
        await session.execute(select(link.c.link_type).where(link.c.link_type == name).limit(1))
    ).first()
    return row is not None


async def delete_link_type(session: AsyncSession, row: TiqoraItsmLinkType) -> None:
    if row.origin == "znuny":
        raise LinkTypeError("znuny_link_type_read_only", "delete")
    if await _link_table_in_use(session, row.name):
        raise LinkTypeError("link_type_in_use", row.name)
    await session.execute(delete(TiqoraItsmLinkType).where(TiqoraItsmLinkType.id == row.id))
    await session.flush()


__all__ = [
    "CI_OBJECT",
    "LINK_PAIRS",
    "LinkPair",
    "LinkTypeError",
    "LinkTypeOption",
    "ZnunyLinkType",
    "create_link_type",
    "delete_link_type",
    "list_link_types",
    "missing_from_table",
    "offered_link_types",
    "sync_link_type_rows",
    "ticket_ticket_link_types",
    "update_link_type",
    "znuny_link_types",
]
