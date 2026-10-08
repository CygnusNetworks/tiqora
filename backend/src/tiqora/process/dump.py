"""Build Znuny's ``ProcessDump(ResultType => 'HASH')`` shape from the live ``pm_*`` tables.

The dump is what Znuny's "Synchronize" step serialises into
``ZZZProcessManagement.pm``; Tiqora snapshots the same shape (plus ``ID`` on
every entity, ``Description`` on processes and ``ProcessPreferences``) and
checksums it, so "has the process configuration changed since the last
deployment" is a pure content comparison.

Every value is JSON-normalised on the way in (string keys, ``str()`` for
anything JSON cannot encode) so a dump equals its own JSON round trip and the
checksum is stable across storing and reloading it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.process_management import (
    PmActivity,
    PmActivityDialog,
    PmProcess,
    PmProcessPreferences,
    PmTransition,
    PmTransitionAction,
)
from tiqora.db.legacy.profile import get_legacy_schema_profile
from tiqora.process.config import _load_yaml_mapping

# Znuny Process/State.pm:48-50 and :67-69 hardcode the three process states.
PROCESS_STATE_NAMES: dict[str, str] = {"S1": "Active", "S2": "Inactive", "S3": "FadeAway"}
STATE_ACTIVE, STATE_INACTIVE, STATE_FADEAWAY = "S1", "S2", "S3"

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _jsonable(value: Any) -> Any:
    """Recursively coerce ``value`` to what ``json.loads(json.dumps(value))`` returns."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def canonical_json(data: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 characters kept."""
    return json.dumps(
        _jsonable(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def dump_checksum(data: Any) -> str:
    """SHA-256 hex digest of :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def dump_entity_count(dump: Mapping[str, Any]) -> int:
    """Number of entities over all kinds (``State`` is a constant lookup, not counted)."""
    return sum(len(v) for k, v in dump.items() if k != "State" and isinstance(v, Mapping))


def format_time(value: datetime | None) -> str:
    return value.strftime(_TIME_FORMAT) if value is not None else ""


def _config(raw: str | bytes | None) -> dict[str, Any]:
    return _jsonable(_load_yaml_mapping(raw))  # type: ignore[no-any-return]


def _common(row: Any) -> dict[str, Any]:
    return {
        "ID": row.id,
        "Name": row.name,
        "CreateTime": format_time(row.create_time),
        "ChangeTime": format_time(row.change_time),
    }


async def _load_preferences(session: AsyncSession) -> dict[str, dict[str, Any]]:
    # Znuny 7.2+ only. No profile or no table means no preferences (ruling F25).
    profile = get_legacy_schema_profile()
    if profile is None or not profile.has_table("pm_process_preferences"):
        return {}
    result: dict[str, dict[str, Any]] = {}
    rows = await session.execute(
        select(
            PmProcessPreferences.process_entity_id,
            PmProcessPreferences.preferences_key,
            PmProcessPreferences.preferences_value,
        ).order_by(PmProcessPreferences.process_entity_id, PmProcessPreferences.preferences_key)
    )
    for entity_id, key, value in rows.all():
        result.setdefault(entity_id, {})[key] = value if value is not None else ""
    return result


async def build_process_dump(session: AsyncSession) -> dict[str, Any]:
    """Znuny ``ProcessDump`` (DB/Process.pm:1225-1377) for the live ``pm_*`` rows.

    Empty values use Znuny's ``|| ''`` / ``|| {}`` / ``|| []`` defaults.
    """
    processes: dict[str, Any] = {}
    # DB/Process.pm:1265-1274; ID and Description are Tiqora additions.
    for p in (await session.execute(select(PmProcess).order_by(PmProcess.id))).scalars():
        cfg = _config(p.config)
        processes[p.entity_id] = {
            **_common(p),
            "StateEntityID": p.state_entity_id,
            "State": PROCESS_STATE_NAMES.get(p.state_entity_id, ""),
            "StartActivity": cfg.get("StartActivity") or "",
            "StartActivityDialog": cfg.get("StartActivityDialog") or "",
            "Path": cfg.get("Path") or {},
            "Description": cfg.get("Description") or "",
        }

    activities: dict[str, Any] = {}
    # DB/Process.pm:1287-1293
    for a in (await session.execute(select(PmActivity).order_by(PmActivity.id))).scalars():
        cfg = _config(a.config)
        activities[a.entity_id] = {**_common(a), "ActivityDialog": cfg.get("ActivityDialog") or ""}

    dialogs: dict[str, Any] = {}
    # DB/Process.pm:1306-1319
    for d in (
        await session.execute(select(PmActivityDialog).order_by(PmActivityDialog.id))
    ).scalars():
        cfg = _config(d.config)
        dialogs[d.entity_id] = {
            **_common(d),
            "Interface": cfg.get("Interface") or "",
            "DescriptionShort": cfg.get("DescriptionShort") or "",
            "DescriptionLong": cfg.get("DescriptionLong") or "",
            "Fields": cfg.get("Fields") or {},
            "FieldOrder": cfg.get("FieldOrder") or [],
            "Permission": cfg.get("Permission") or "",
            "RequiredLock": cfg.get("RequiredLock") or "",
            "SubmitAdviceText": cfg.get("SubmitAdviceText") or "",
            "SubmitButtonText": cfg.get("SubmitButtonText") or "",
        }

    transitions: dict[str, Any] = {}
    # DB/Process.pm:1332-1338
    for t in (await session.execute(select(PmTransition).order_by(PmTransition.id))).scalars():
        cfg = _config(t.config)
        transitions[t.entity_id] = {
            **_common(t),
            "Condition": cfg.get("Condition") or {},
            "ConditionLinking": cfg.get("ConditionLinking") or "",
        }

    actions: dict[str, Any] = {}
    # DB/Process.pm:1351-1357
    for x in (
        await session.execute(select(PmTransitionAction).order_by(PmTransitionAction.id))
    ).scalars():
        cfg = _config(x.config)
        actions[x.entity_id] = {
            **_common(x),
            "Module": cfg.get("Module") or "",
            "Config": cfg.get("Config") or {},
        }

    return {
        "Process": processes,
        "State": dict(PROCESS_STATE_NAMES),
        "Activity": activities,
        "ActivityDialog": dialogs,
        "Transition": transitions,
        "TransitionAction": actions,
        "ProcessPreferences": await _load_preferences(session),
    }
