"""ITSM mode: types, ``tiqora_settings`` keys and the guarded transitions (design I2).

This module is the only writer of ``itsm.mode``, ``itsm.mode_meta`` and ``itsm.switch``; the
ITSM importer writes only ``itsm.znuny_readonly`` and changes the mode through
``switch_to_tiqora`` / ``rollback_to_znuny``. Admin transitions go through ``set_itsm_mode`` and
are guarded by ``allowed_transitions``: Mode A -> Mode B happens only through the importer.

Modes: ``off`` — no ITSM; ``znuny_readthrough`` (Mode A) — Znuny's ITSM packages hold the CIs,
Tiqora reads them; ``tiqora`` (Mode B) — the Tiqora CMDB holds them.

Functions that write only ``flush``; the caller commits (``ensure_itsm_mode_initialised`` owns its
own session and commits).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

import structlog
from fastapi import HTTPException, status
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.itsm.znuny_adapter.detect import ZnunyItsmInfo, detect_znuny_itsm
from tiqora.jobs.service import utc_now

log = structlog.get_logger(__name__)

ItsmMode = Literal["off", "znuny_readthrough", "tiqora"]
ModeSource = Literal["auto", "admin", "importer"]

#: stored mode, value = the bare string ``off`` | ``znuny_readthrough`` | ``tiqora``
#: (not part of the ``itsm`` settings section: transitions are guarded)
SETTING_MODE: Final = "itsm.mode"
#: JSON ``{"source", "changed_at", "changed_by", "import_run_id", "previous_mode"}``
SETTING_MODE_META: Final = "itsm.mode_meta"
#: JSON ``{"switched_at", "by", "run_id"}`` while an import switched the site to Mode B
SETTING_SWITCH: Final = "itsm.switch"
#: ``"1"`` while Znuny's own CI screens are blocked after a switch (written by the importer)
SETTING_ZNUNY_READONLY: Final = "itsm.znuny_readonly"

#: Znuny ITSM counts as installed when both tables exist (the one detection predicate)
ZNUNY_ITSM_MARKER_TABLES: Final[tuple[str, ...]] = ("general_catalog", "cip_allocate")

#: The ``itsm`` settings section stores ``itsm.<field>`` in the same key space as the keys above,
#: so no ``ItsmSettings`` field may use one of these names.
RESERVED_ITSM_SECTION_FIELDS: Final[frozenset[str]] = frozenset(
    {"mode", "mode_meta", "switch", "znuny_readonly"}
)

_ALL_MODES: Final[tuple[ItsmMode, ...]] = ("off", "znuny_readthrough", "tiqora")
_SOURCES: Final[tuple[ModeSource, ...]] = ("auto", "admin", "importer")


@dataclass(frozen=True, slots=True)
class ItsmModeState:
    mode: ItsmMode
    source: ModeSource
    stored: bool  # False only before ensure_itsm_mode_initialised ran (tests, first request race)
    detected: ZnunyItsmInfo
    changed_at: datetime | None
    changed_by: int | None
    import_run_id: int | None  # set when source == "importer"
    #: the mode that was active before ``off`` (set only while ``mode == "off"``)
    previous_mode: ItsmMode | None = None


class ModeTransitionNotAllowed(Exception):
    """An admin asked for a transition the rules do not allow (``reason`` is a stable code)."""

    def __init__(self, current: ItsmMode, new: ItsmMode, reason: str) -> None:
        super().__init__(f"itsm mode {current} -> {new} not allowed: {reason}")
        self.current = current
        self.new = new
        self.reason = reason


def auto_default(info: ZnunyItsmInfo) -> ItsmMode:
    """Znuny ITSM core installed -> Mode A, otherwise Mode B."""
    return "znuny_readthrough" if info.core_installed else "tiqora"


def _denial(
    state: ItsmModeState, new: ItsmMode, *, tiqora_cmdb_empty: bool, switched: bool
) -> str | None:
    """Reason code why ``state.mode -> new`` is refused, ``None`` when it is allowed."""
    if new == state.mode:
        return "already_in_mode"
    if new == "off":
        return None
    if state.mode == "off":
        if switched:
            return None if new == "tiqora" else "off_returns_to_tiqora_while_switched"
        target = state.previous_mode or auto_default(state.detected)
        return None if new == target else "off_returns_to_previous_mode"
    if state.mode == "znuny_readthrough":
        return "znuny_to_tiqora_importer_only"  # the only other target is "tiqora"
    # state.mode == "tiqora", new == "znuny_readthrough"
    if switched:
        return "tiqora_to_znuny_importer_only"
    if not state.detected.core_installed:
        return "znuny_itsm_not_detected"
    if not tiqora_cmdb_empty:
        return "tiqora_cmdb_not_empty"
    return None


def allowed_transitions(
    state: ItsmModeState, *, tiqora_cmdb_empty: bool, switched: bool = False
) -> frozenset[ItsmMode]:
    """Modes an admin may switch to (pure).

    * any -> ``off``
    * ``off`` -> the mode before ``off`` (no re-detection; without it the auto default), but only
      ``tiqora`` while ``itsm.switch`` exists (the importer switched the site)
    * ``tiqora`` -> ``znuny_readthrough`` only while Znuny ITSM is detected and the Tiqora CMDB is
      empty (and not after an importer switch: that is rolled back by the importer)
    * ``znuny_readthrough`` -> ``tiqora`` never (importer only)
    """
    return frozenset(
        m
        for m in _ALL_MODES
        if _denial(state, m, tiqora_cmdb_empty=tiqora_cmdb_empty, switched=switched) is None
    )


def _as_mode(value: object) -> ItsmMode | None:
    return next((m for m in _ALL_MODES if m == value), None)


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_dt(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _int_or_none(raw: object) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


async def _stored_values(session: AsyncSession, *keys: str) -> dict[str, str | None]:
    rows = await session.execute(
        select(TiqoraSettings.key, TiqoraSettings.value).where(TiqoraSettings.key.in_(keys))
    )
    return {k: v for k, v in rows.all()}


async def get_itsm_mode(session: AsyncSession, *, fresh: bool = False) -> ItsmModeState:
    """The stored mode, or the computed auto default with ``stored=False``.

    ``fresh=True`` bypasses the 60 s detection cache (admin screens).
    """
    detected = await detect_znuny_itsm(session, use_cache=not fresh)
    values = await _stored_values(session, SETTING_MODE, SETTING_MODE_META)
    raw_mode = (values.get(SETTING_MODE) or "").strip()
    stored_mode = _as_mode(raw_mode)
    if stored_mode is None:
        if raw_mode:
            log.warning("itsm_mode_invalid", value=raw_mode)
        return ItsmModeState(
            mode=auto_default(detected),
            source="auto",
            stored=False,
            detected=detected,
            changed_at=None,
            changed_by=None,
            import_run_id=None,
        )
    meta = _parse_json(values.get(SETTING_MODE_META))
    source: ModeSource = next((x for x in _SOURCES if x == meta.get("source")), "auto")
    previous = _as_mode(meta.get("previous_mode"))
    return ItsmModeState(
        mode=stored_mode,
        source=source,
        stored=True,
        detected=detected,
        changed_at=_parse_dt(meta.get("changed_at")),
        changed_by=_int_or_none(meta.get("changed_by")),
        import_run_id=_int_or_none(meta.get("import_run_id")),
        previous_mode=previous if stored_mode == "off" and previous != "off" else None,
    )


async def is_switched(session: AsyncSession) -> bool:
    """``itsm.switch`` exists: an import switched the site to Mode B."""
    return bool((await _stored_values(session, SETTING_SWITCH)).get(SETTING_SWITCH))


async def get_switch_info(session: AsyncSession) -> dict[str, Any] | None:
    raw = (await _stored_values(session, SETTING_SWITCH)).get(SETTING_SWITCH)
    return (_parse_json(raw) or None) if raw else None


async def _put(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(TiqoraSettings, key)
    if row is None:
        session.add(TiqoraSettings(key=key, value=value))
    else:
        row.value = value


def _meta_json(
    *,
    source: ModeSource,
    user_id: int | None,
    import_run_id: int | None,
    previous_mode: ItsmMode | None,
    now: datetime,
) -> str:
    return json.dumps(
        {
            "source": source,
            "changed_at": now.isoformat(),
            "changed_by": user_id,
            "import_run_id": import_run_id,
            "previous_mode": previous_mode,
        }
    )


async def _write_mode(
    session: AsyncSession,
    mode: ItsmMode,
    *,
    source: ModeSource,
    user_id: int | None,
    import_run_id: int | None,
    previous_mode: ItsmMode | None,
    now: datetime,
) -> None:
    await _put(session, SETTING_MODE, mode)
    await _put(
        session,
        SETTING_MODE_META,
        _meta_json(
            source=source,
            user_id=user_id,
            import_run_id=import_run_id,
            previous_mode=previous_mode,
            now=now,
        ),
    )
    await session.flush()


async def ensure_itsm_mode_initialised(
    session_factory: async_sessionmaker[AsyncSession],
) -> ItsmModeState:
    """Store the auto default once. Idempotent; later detection results are ignored.

    Insert-if-absent: api and worker start at the same time on deploy, so a concurrent second
    writer hits the primary key, rolls back and reads the first writer's value.
    """
    async with session_factory() as session:
        state = await get_itsm_mode(session, fresh=True)
        if state.stored:
            return state
        mode = auto_default(state.detected)
        now = utc_now()
        try:
            await session.execute(insert(TiqoraSettings).values(key=SETTING_MODE, value=mode))
        except IntegrityError:
            await session.rollback()
            return await get_itsm_mode(session, fresh=True)
        await _put(
            session,
            SETTING_MODE_META,
            _meta_json(
                source="auto", user_id=None, import_run_id=None, previous_mode=None, now=now
            ),
        )
        await session.commit()
        log.info("itsm_mode_initialised", mode=mode, source="auto")
        return ItsmModeState(
            mode=mode,
            source="auto",
            stored=True,
            detected=state.detected,
            changed_at=now,
            changed_by=None,
            import_run_id=None,
        )


async def init_itsm_mode_at_startup(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Startup hook of api and worker: never raises (the DB may not be migrated yet)."""
    try:
        await ensure_itsm_mode_initialised(session_factory)
    except Exception as exc:  # noqa: BLE001 - a failing detection must never block startup
        log.warning("itsm_mode_init_failed", error=str(exc), error_type=type(exc).__name__)


async def set_itsm_mode(session: AsyncSession, new: ItsmMode, *, user_id: int) -> ItsmModeState:
    """Admin path; raises ``ModeTransitionNotAllowed`` unless ``new`` is an allowed transition."""
    from tiqora.itsm.providers import tiqora_cmdb_empty

    state = await get_itsm_mode(session, fresh=True)
    switched = await is_switched(session)
    empty = await tiqora_cmdb_empty(session) if state.mode == "tiqora" else True
    reason = _denial(state, new, tiqora_cmdb_empty=empty, switched=switched)
    if reason is not None:
        raise ModeTransitionNotAllowed(state.mode, new, reason)
    now = utc_now()
    await _write_mode(
        session,
        new,
        source="admin",
        user_id=user_id,
        import_run_id=None,
        previous_mode=state.mode if new == "off" else None,
        now=now,
    )
    log.info("itsm_mode_changed", old=state.mode, new=new, source="admin", user_id=user_id)
    return await get_itsm_mode(session, fresh=True)


async def switch_to_tiqora(
    session: AsyncSession, *, user_id: int, import_run_id: int
) -> ItsmModeState:
    """Importer path: ``znuny_readthrough`` or ``off`` -> ``tiqora``, source ``importer``.

    Also records ``itsm.switch``. Flush only; the importer commits.
    """
    state = await get_itsm_mode(session, fresh=True)
    if state.mode == "tiqora":
        raise ModeTransitionNotAllowed(state.mode, "tiqora", "already_in_mode")
    now = utc_now()
    await _write_mode(
        session,
        "tiqora",
        source="importer",
        user_id=user_id,
        import_run_id=import_run_id,
        previous_mode=None,
        now=now,
    )
    await _put(
        session,
        SETTING_SWITCH,
        json.dumps({"switched_at": now.isoformat(), "by": user_id, "run_id": import_run_id}),
    )
    await session.flush()
    log.info(
        "itsm_mode_changed", old=state.mode, new="tiqora", source="importer", run_id=import_run_id
    )
    return await get_itsm_mode(session, fresh=True)


async def rollback_to_znuny(
    session: AsyncSession, *, user_id: int, import_run_id: int
) -> ItsmModeState:
    """Importer path: ``tiqora`` -> ``znuny_readthrough`` regardless of CMDB content.

    Deletes ``itsm.switch``. Flush only; the importer commits.
    """
    state = await get_itsm_mode(session, fresh=True)
    if state.mode != "tiqora":
        raise ModeTransitionNotAllowed(
            state.mode, "znuny_readthrough", "rollback_needs_mode_tiqora"
        )
    await _write_mode(
        session,
        "znuny_readthrough",
        source="importer",
        user_id=user_id,
        import_run_id=import_run_id,
        previous_mode=None,
        now=utc_now(),
    )
    switch_row = await session.get(TiqoraSettings, SETTING_SWITCH)
    if switch_row is not None:
        await session.delete(switch_row)
    await session.flush()
    log.info(
        "itsm_mode_changed",
        old="tiqora",
        new="znuny_readthrough",
        source="importer",
        run_id=import_run_id,
    )
    return await get_itsm_mode(session, fresh=True)


async def require_itsm_enabled(session: AsyncSession) -> ItsmModeState:
    """Gate of every ITSM route: 409 ``itsm_off`` while the mode is ``off``."""
    state = await get_itsm_mode(session)
    if state.mode == "off":
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "itsm_off"})
    return state


__all__ = [
    "RESERVED_ITSM_SECTION_FIELDS",
    "SETTING_MODE",
    "SETTING_MODE_META",
    "SETTING_SWITCH",
    "SETTING_ZNUNY_READONLY",
    "ZNUNY_ITSM_MARKER_TABLES",
    "ItsmMode",
    "ItsmModeState",
    "ModeSource",
    "ModeTransitionNotAllowed",
    "ZnunyItsmInfo",
    "allowed_transitions",
    "auto_default",
    "detect_znuny_itsm",
    "ensure_itsm_mode_initialised",
    "get_itsm_mode",
    "get_switch_info",
    "init_itsm_mode_at_startup",
    "is_switched",
    "require_itsm_enabled",
    "rollback_to_znuny",
    "set_itsm_mode",
    "switch_to_tiqora",
]
