"""Compare deployed process revisions and restore one process from a revision (plan D14).

A revision is the canonical ``build_process_dump`` JSON. The diff ignores ``ID`` and the time
columns. A restore writes *drafts* (``ProcessWriter.import_entity``: same entity ids, upsert,
never a delete) and so is not live until somebody deploys.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.process_management import PmProcess
from tiqora.deployed.store import load_revision
from tiqora.process.config import _load_yaml_mapping
from tiqora.process.designer.draft import load_process_draft
from tiqora.process.designer.ids import EntityType
from tiqora.process.designer.schemas import ProcessDraftOut
from tiqora.process.designer.writer import EntityNotFound, ProcessWriter
from tiqora.process.designer.yaml_codec import dump_znuny_yaml

ChangeKind = Literal["added", "removed", "changed"]

# Dump kinds in write order (dialogs before the activities that use them, process last).
DUMP_KINDS = ("Process", "Activity", "ActivityDialog", "Transition", "TransitionAction")
_RESTORE_ORDER: tuple[EntityType, ...] = (
    "ActivityDialog",
    "Activity",
    "Transition",
    "TransitionAction",
    "Process",
)
_VOLATILE = frozenset({"ID", "CreateTime", "ChangeTime"})
_DIALOG_KEYS = (
    "Interface",
    "DescriptionShort",
    "DescriptionLong",
    "Fields",
    "FieldOrder",
    "Permission",
    "RequiredLock",
    "SubmitAdviceText",
    "SubmitButtonText",
)


class EntityChangeOut(BaseModel):
    entity_type: str
    entity_id: str
    name: str
    change: ChangeKind
    changed_keys: list[str]  # top-level keys that differ (changed only)


class RevisionDiffOut(BaseModel):
    from_revision: int  # `against`; 0 = empty
    to_revision: int
    process_entity_id: str | None
    changes: list[EntityChangeOut]
    yaml_before: str
    yaml_after: str


class RestoreIn(BaseModel):
    process_entity_id: str


def _section(dump: Mapping[str, Any], kind: str) -> Mapping[str, Any]:
    value = dump.get(kind)
    return value if isinstance(value, Mapping) else {}


def _entry(section: Mapping[str, Any], eid: str) -> Mapping[str, Any]:
    value = section.get(eid)
    return value if isinstance(value, Mapping) else {}


def _stable(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k not in _VOLATILE}


def process_references(dump: Mapping[str, Any], process_entity_id: str) -> dict[str, set[str]]:
    """Entity ids the process uses in ``dump``, per kind (the process itself included)."""
    refs: dict[str, set[str]] = {k: set() for k in DUMP_KINDS}
    process = _entry(_section(dump, "Process"), process_entity_id)
    if not process:
        return refs
    refs["Process"].add(process_entity_id)
    path = process.get("Path")
    path = path if isinstance(path, Mapping) else {}
    activities = set(path)
    for start in (process.get("StartActivity"),):
        if isinstance(start, str) and start:
            activities.add(start)
    dialogs: set[str] = set()
    start_dialog = process.get("StartActivityDialog")
    if isinstance(start_dialog, str) and start_dialog:
        dialogs.add(start_dialog)
    for transitions in path.values():
        if not isinstance(transitions, Mapping):
            continue
        for tid, spec in transitions.items():
            refs["Transition"].add(str(tid))
            if not isinstance(spec, Mapping):
                continue
            target = spec.get("ActivityEntityID")
            if isinstance(target, str) and target:
                activities.add(target)
            actions = spec.get("TransitionAction")
            if isinstance(actions, list):
                refs["TransitionAction"].update(str(a) for a in actions)
    refs["Activity"] = activities
    for aid in activities:
        by_order = _entry(_section(dump, "Activity"), aid).get("ActivityDialog")
        if isinstance(by_order, Mapping):
            dialogs.update(str(v) for v in by_order.values() if v)
    refs["ActivityDialog"] = dialogs
    return refs


def _selected(
    old: Mapping[str, Any], new: Mapping[str, Any], process_entity_id: str | None
) -> dict[str, set[str]]:
    if process_entity_id is None:
        return {k: set(_section(old, k)) | set(_section(new, k)) for k in DUMP_KINDS}
    a = process_references(old, process_entity_id)
    b = process_references(new, process_entity_id)
    return {k: a[k] | b[k] for k in DUMP_KINDS}


def _yaml(dump: Mapping[str, Any], selected: Mapping[str, Iterable[str]]) -> str:
    data: dict[str, Any] = {}
    for kind in DUMP_KINDS:
        section = _section(dump, kind)
        picked = {
            eid: _stable(_entry(section, eid)) for eid in sorted(selected[kind]) if eid in section
        }
        if picked:
            data[kind] = picked
    return dump_znuny_yaml(data) if data else ""


def diff_revisions(
    old_dump: Mapping[str, Any], new_dump: Mapping[str, Any], *, process_entity_id: str | None
) -> RevisionDiffOut:
    """Pure comparison; revision numbers are filled in by the caller."""
    selected = _selected(old_dump, new_dump, process_entity_id)
    changes: list[EntityChangeOut] = []
    for kind in DUMP_KINDS:
        old_s, new_s = _section(old_dump, kind), _section(new_dump, kind)
        for eid in sorted(selected[kind]):
            before, after = _stable(_entry(old_s, eid)), _stable(_entry(new_s, eid))
            if eid in old_s and eid not in new_s:
                change: ChangeKind = "removed"
            elif eid not in old_s and eid in new_s:
                change = "added"
            elif eid in old_s and before != after:
                change = "changed"
            else:
                continue
            keys = (
                sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
                if change == "changed"
                else []
            )
            name = after.get("Name") or before.get("Name") or ""
            changes.append(
                EntityChangeOut(
                    entity_type=kind,
                    entity_id=eid,
                    name=str(name),
                    change=change,
                    changed_keys=keys,
                )
            )
    return RevisionDiffOut(
        from_revision=0,
        to_revision=0,
        process_entity_id=process_entity_id,
        changes=changes,
        yaml_before=_yaml(old_dump, selected),
        yaml_after=_yaml(new_dump, selected),
    )


def dump_entry_to_config(kind: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Invert ``build_process_dump`` for one entity: the ``config`` the row had.

    The dump replaced empty values with Znuny's defaults, so what comes back reproduces the
    *dump* exactly (``dump_checksum`` of a rebuilt dump matches), not necessarily every byte
    of a hand-written config.
    """
    if kind == "Process":
        path = entry.get("Path")
        return {
            "Description": entry.get("Description") or "",
            "Path": dict(path) if isinstance(path, Mapping) else {},
            "StartActivity": entry.get("StartActivity") or "",
            "StartActivityDialog": entry.get("StartActivityDialog") or "",
        }
    if kind == "Activity":
        dialogs = entry.get("ActivityDialog")
        return {"ActivityDialog": dict(dialogs) if isinstance(dialogs, Mapping) else {}}
    if kind == "ActivityDialog":
        return {key: entry[key] for key in _DIALOG_KEYS if key in entry}
    if kind == "Transition":
        return {
            "Condition": entry.get("Condition", {}),
            "ConditionLinking": entry.get("ConditionLinking", ""),
        }
    if kind == "TransitionAction":
        return {"Module": entry.get("Module", ""), "Config": entry.get("Config", {})}
    raise ValueError(f"unknown entity kind: {kind}")


async def diff_stored_revisions(
    session: AsyncSession,
    revision_id: int,
    against: int | None,
    process_entity_id: str | None,
    *,
    previous: int | None,
) -> RevisionDiffOut:
    """Load both revisions and compare. ``against`` None means ``previous`` (0 = none)."""
    new = await load_revision(session, "process", revision_id)
    if new is None:
        raise EntityNotFound(str(revision_id))
    base = against if against is not None else (previous or 0)
    old_dump: Mapping[str, Any] = {}
    if base:
        old = await load_revision(session, "process", base)
        if old is None:
            raise EntityNotFound(str(base))
        old_dump = old.dump
    result = diff_revisions(old_dump, new.dump, process_entity_id=process_entity_id)
    result.from_revision = base
    result.to_revision = revision_id
    return result


async def restore_process_from_revision(
    session: AsyncSession,
    writer: ProcessWriter,
    *,
    revision_id: int,
    process_entity_id: str,
) -> ProcessDraftOut:
    """Write the process and everything it references in that revision back as drafts.

    Same entity ids, upsert only (no deletes), no version check: a restore is an explicit
    overwrite. The current canvas layout survives for activities that still exist.
    Flushes only; the caller commits.
    """
    revision = await load_revision(session, "process", revision_id)
    if revision is None:
        raise EntityNotFound(str(revision_id))
    dump = revision.dump
    process = _entry(_section(dump, "Process"), process_entity_id)
    if not process:
        raise EntityNotFound(process_entity_id)
    refs = process_references(dump, process_entity_id)
    for kind in _RESTORE_ORDER:
        for eid in sorted(refs[kind]):
            entry = _entry(_section(dump, kind), eid)
            if not entry:
                continue  # the revision itself lacks it; the validation panel will say so
            config = dump_entry_to_config(kind, entry)
            if kind == "Process":
                await writer.import_entity(
                    "Process",
                    eid,
                    name=str(entry.get("Name") or ""),
                    config=config,
                    layout=await _kept_layout(session, eid, set(config["Path"]) | refs["Activity"]),
                    state_entity_id=str(entry.get("StateEntityID") or "S2"),
                )
            else:
                await writer.import_entity(
                    kind, eid, name=str(entry.get("Name") or ""), config=config
                )
    return await load_process_draft(session, process_entity_id)


async def _kept_layout(
    session: AsyncSession, process_entity_id: str, activity_ids: set[str]
) -> dict[str, Any]:
    raw = (
        await session.execute(
            select(PmProcess.layout).where(PmProcess.entity_id == process_entity_id)
        )
    ).scalar_one_or_none()
    if raw is None:
        return {}
    current = _load_yaml_mapping(raw)
    return {k: v for k, v in current.items() if k in activity_ids}
