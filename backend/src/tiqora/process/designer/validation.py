"""Validation while editing (plan D12): a pure function over a process draft.

Znuny itself validates nothing at deploy time, so these issues never block a deploy; they
point at configurations that cannot work (errors) or probably do not do what the author
wants (warnings). :func:`validate_process` is pure; :func:`load_reference_data` fetches the
valid names and ids it compares against, one plain query per table (no joins).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.dynamic_field import DynamicField
from tiqora.domain.dynamic_field_config import config_from_yaml
from tiqora.domain.field_options import DF_OPTION_PREFIX, VALID_ID
from tiqora.extensions.core import get_registry
from tiqora.process.actions.registry import (
    TRANSITION_ACTIONS,
    ZNUNY_MODULE_PREFIX,
    ActionParam,
    TransitionActionModule,
)
from tiqora.process.condition_modules import normalize_module_match, resolve_condition_module
from tiqora.process.designer.catalog import CONDITION_TYPES
from tiqora.process.designer.ids import ENTITY_TYPES, EntityType
from tiqora.process.designer.schemas import DesignerEntityOut, ProcessDraftOut
from tiqora.util.perl_regex import PerlRegexError, compile_perl_regex

Severity = Literal["error", "warning"]

SMART_TAG = "<OTRS_"
# ``conditions.py`` also evaluates ``Array`` and ``Hash`` fields; the designer does not offer
# them, but imported Znuny processes may carry them (ruling).
_KNOWN_CONDITION_TYPES = frozenset((*CONDITION_TYPES, "Array", "Hash"))
# Ticket attributes that a dialog shows as required-only selects (wave-1 D1).
_REQUIRED_DISPLAY_FIELDS = ("Queue", "State", "Lock", "Priority")
# Condition field -> ReferenceData attribute holding its valid names.
_NAME_FIELDS = {
    "Queue": "queues",
    "State": "states",
    "Priority": "priorities",
    "Type": "types",
    "Service": "services",
    "SLA": "slas",
}
# Action param kind -> ReferenceData attribute (names) and key of ``ReferenceData.ids``.
_KIND_REFS = {
    "queue": "queues",
    "state": "states",
    "priority": "priorities",
    "type": "types",
    "service": "services",
    "sla": "slas",
    "user": "users",
    "calendar": "calendars",
}
# Modules whose Config keys (apart from the listed ones) are dynamic field names.
_DF_KEY_MODULES = {
    "DynamicFieldSet": frozenset({"UserID", "DynamicField_UserID"}),
    "DynamicFieldRemove": frozenset({"UserID", "DynamicField_UserID"}),
    "DynamicFieldIncrement": frozenset({"UserID", "DynamicField_UserID", "Value"}),
}


@dataclass(frozen=True, slots=True)
class DfInfo:
    type: str
    possible_values: frozenset[str] | None = None  # None: free-form / no PossibleValues


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    severity: Severity
    entity_type: EntityType
    entity_id: str
    field: str = ""
    params: Mapping[str, str] = dc_field(default_factory=dict)


class ValidationIssueOut(BaseModel):
    code: str
    severity: Severity
    entity_type: EntityType
    entity_id: str
    field: str
    params: dict[str, str]

    @classmethod
    def of(cls, issue: ValidationIssue) -> ValidationIssueOut:
        return cls(
            code=issue.code,
            severity=issue.severity,
            entity_type=issue.entity_type,
            entity_id=issue.entity_id,
            field=issue.field,
            params=dict(issue.params),
        )


@dataclass(frozen=True, slots=True)
class ReferenceData:
    queues: frozenset[str] = frozenset()
    states: frozenset[str] = frozenset()
    priorities: frozenset[str] = frozenset()
    types: frozenset[str] = frozenset()
    services: frozenset[str] = frozenset()
    slas: frozenset[str] = frozenset()
    users: frozenset[str] = frozenset()  # logins
    dynamic_fields: Mapping[str, DfInfo] = dc_field(default_factory=dict)
    calendars: frozenset[str] = frozenset()
    # Valid ids per attribute name above (``{"queues": {"1", "2"}}``), for ``QueueID``-style keys.
    ids: Mapping[str, frozenset[str]] = dc_field(default_factory=dict)


# --- helpers ------------------------------------------------------------------------------


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _s(value: object) -> str:
    return "" if value is None else str(value)


def _order_key(key: object) -> tuple[int, int, str]:
    s = str(key)
    return (0, int(s), s) if s.isdigit() else (1, 0, s)


def _values(value: object) -> list[str]:
    """A config value as a list of strings (a list stays a list, a scalar becomes one item)."""
    if isinstance(value, list | tuple):
        return [_s(v) for v in value]
    return [_s(value)]


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, list | tuple | Mapping):
        return len(value) > 0
    return True


def _known(names: Iterable[str], value: str) -> bool:
    """Names are compared case-insensitively, like Znuny's lookups on the default collation."""
    folded = value.casefold()
    return any(n.casefold() == folded for n in names)


def _entity_type_of(entity_id: str) -> EntityType:
    prefix = entity_id.split("-", 1)[0]
    return next((t for t in ENTITY_TYPES if t == prefix), "Activity")


def _short_module(module: str) -> str:
    return module.removeprefix(ZNUNY_MODULE_PREFIX).strip()


class _Issues:
    def __init__(self) -> None:
        self.items: list[ValidationIssue] = []
        self._seen: set[tuple[str, str, str, str]] = set()

    def add(
        self,
        code: str,
        severity: Severity,
        entity: DesignerEntityOut | None,
        field_: str = "",
        *,
        process_id: str = "",
        **params: str,
    ) -> None:
        etype: EntityType = entity.entity_type if entity else "Process"
        eid = entity.entity_id if entity else process_id
        key = (code, eid, field_, "|".join(f"{k}={v}" for k, v in sorted(params.items())))
        if key in self._seen:
            return
        self._seen.add(key)
        if entity is not None and "name" not in params:
            params = {"name": entity.name, **params}
        self.items.append(ValidationIssue(code, severity, etype, eid, field_, params))


# --- process level ------------------------------------------------------------------------


def _check_process(draft: ProcessDraftOut, out: _Issues) -> dict[str, DesignerEntityOut]:
    """Start settings, references, transitions' targets and reachability."""
    process = draft.process
    cfg = process.config
    path = _map(cfg.get("Path"))
    activities = {a.entity_id: a for a in draft.activities}
    dialogs = {d.entity_id: d for d in draft.activity_dialogs}
    transitions = {t.entity_id for t in draft.transitions}
    actions = {a.entity_id for a in draft.transition_actions}

    start = _s(cfg.get("StartActivity"))
    start_dialog = _s(cfg.get("StartActivityDialog"))
    if not start:
        out.add("start_activity_missing", "error", process, "StartActivity")
    elif start not in activities:
        out.add("reference_missing", "error", process, "StartActivity", id=start, kind="Activity")
    if not start_dialog:
        out.add("start_dialog_missing", "error", process, "StartActivityDialog")
    elif start_dialog not in dialogs:
        out.add(
            "reference_missing",
            "error",
            process,
            "StartActivityDialog",
            id=start_dialog,
            kind="ActivityDialog",
        )
    elif start in activities:
        offered = {_s(v) for v in _map(activities[start].config.get("ActivityDialog")).values()}
        if start_dialog not in offered:
            out.add(
                "start_dialog_not_in_start_activity",
                "error",
                process,
                "StartActivityDialog",
                id=start_dialog,
                activity=start,
            )

    for aid, entries in path.items():
        if aid not in activities:
            out.add("reference_missing", "error", process, f"Path.{aid}", id=aid, kind="Activity")
        for tid, entry in _map(entries).items():
            field_ = f"Path.{aid}.{tid}"
            if tid not in transitions:
                out.add("reference_missing", "error", process, field_, id=tid, kind="Transition")
            target = _s(_map(entry).get("ActivityEntityID"))
            if not target:
                out.add("transition_target_missing", "error", process, field_, transition=tid)
            elif target not in activities:
                out.add(
                    "reference_missing",
                    "error",
                    process,
                    f"{field_}.ActivityEntityID",
                    id=target,
                    kind="Activity",
                )
            raw_actions = _map(entry).get("TransitionAction") or []
            for action_id in raw_actions if isinstance(raw_actions, list) else []:
                if _s(action_id) not in actions:
                    out.add(
                        "reference_missing",
                        "error",
                        process,
                        f"{field_}.TransitionAction",
                        id=_s(action_id),
                        kind="TransitionAction",
                    )

    for activity in activities.values():
        dialog_map = _map(activity.config.get("ActivityDialog"))
        for key in sorted(dialog_map, key=_order_key):
            did = _s(dialog_map[key])
            if did not in dialogs:
                out.add(
                    "reference_missing",
                    "error",
                    activity,
                    f"ActivityDialog.{key}",
                    id=did,
                    kind="ActivityDialog",
                )

    # Reachability along the Path, from the start activity.
    if start and start in activities:
        reached: set[str] = set()
        queue = [start]
        while queue:
            current = queue.pop()
            if current in reached:
                continue
            reached.add(current)
            for entry in _map(path.get(current)).values():
                target = _s(_map(entry).get("ActivityEntityID"))
                if target and target not in reached:
                    queue.append(target)
        for aid, activity in activities.items():
            if aid not in reached:
                out.add("activity_unreachable", "warning", activity)
    for aid, activity in activities.items():
        has_dialogs = bool(_map(activity.config.get("ActivityDialog")))
        if has_dialogs and not _map(path.get(aid)):
            out.add("activity_dead_end", "warning", activity)
    return activities


# --- dialogs ------------------------------------------------------------------------------


def _check_dialog(dialog: DesignerEntityOut, refs: ReferenceData, out: _Issues) -> None:
    cfg = dialog.config
    interface = cfg.get("Interface")
    names = [i for i in _values(interface) if i] if interface else []
    if not names:
        out.add("dialog_no_interface", "error", dialog, "Interface")
    fields_ = _map(cfg.get("Fields"))
    if not fields_:
        out.add("dialog_no_fields", "warning", dialog, "Fields")
    for fname, fcfg in fields_.items():
        if fname.startswith(DF_OPTION_PREFIX):
            df_name = fname.removeprefix(DF_OPTION_PREFIX)
            if df_name not in refs.dynamic_fields:
                out.add("dialog_field_unknown", "warning", dialog, f"Fields.{fname}", field=df_name)
        elif fname in _REQUIRED_DISPLAY_FIELDS and _s(_map(fcfg).get("Display")) == "1":
            out.add("queue_required_display", "warning", dialog, f"Fields.{fname}", field=fname)


# --- transitions --------------------------------------------------------------------------


def _check_match(
    transition: DesignerEntityOut,
    refs: ReferenceData,
    fname: str,
    ftype: str,
    match: object,
    field_: str,
    out: _Issues,
) -> None:
    items = [v for v in _values(match) if v != "" and SMART_TAG not in v]
    if ftype == "Regexp":
        for pattern in items:
            try:
                compile_perl_regex(pattern, verbose=False)
            except PerlRegexError:
                out.add(
                    "condition_value_impossible",
                    "error",
                    transition,
                    field_,
                    field=fname,
                    value=pattern,
                    reason="regexp",
                )
        return
    if ftype not in ("String", "Equal"):
        return
    attr = _NAME_FIELDS.get(fname)
    if attr is not None:
        valid = getattr(refs, attr)
        for value in items:
            if not _known(valid, value):
                out.add(
                    "condition_value_impossible",
                    "error",
                    transition,
                    field_,
                    field=fname,
                    value=value,
                    reason="name",
                )
        return
    if fname.startswith(DF_OPTION_PREFIX):
        info = refs.dynamic_fields.get(fname.removeprefix(DF_OPTION_PREFIX))
        if info is None or info.type not in ("Dropdown", "Multiselect"):
            return
        if info.possible_values is None:
            return
        for value in items:
            if value not in info.possible_values:
                out.add(
                    "condition_value_impossible",
                    "error",
                    transition,
                    field_,
                    field=fname,
                    value=value,
                    reason="possible_values",
                )


def _check_transition(transition: DesignerEntityOut, refs: ReferenceData, out: _Issues) -> None:
    condition = _map(transition.config.get("Condition"))
    if not condition:
        out.add("transition_without_condition", "warning", transition, "Condition")
        return
    for cname, block in condition.items():
        fields_ = _map(_map(block).get("Fields"))
        for fname, fcfg in fields_.items():
            field_ = f"Condition.{cname}.Fields.{fname}"
            ftype = _s(_map(fcfg).get("Type")) or "String"
            match = _map(fcfg).get("Match")
            if ftype not in _KNOWN_CONDITION_TYPES:
                out.add("condition_type_unknown", "warning", transition, field_, type=ftype)
                continue
            if ftype == "Module":
                module = normalize_module_match(_s(match))
                if resolve_condition_module(module) is None:
                    out.add(
                        "condition_module_unknown", "warning", transition, field_, module=module
                    )
                continue
            _check_match(transition, refs, fname, ftype, match, field_, out)


# --- transition actions -------------------------------------------------------------------


def _check_reference_value(
    action: DesignerEntityOut,
    refs: ReferenceData,
    param: ActionParam,
    key: str,
    value: object,
    out: _Issues,
) -> None:
    if param.kind == "dynamic_field":
        valid: Iterable[str] = refs.dynamic_fields.keys()
        is_id = False
    elif param.kind in _KIND_REFS:
        attr = _KIND_REFS[param.kind]
        is_id = key.endswith(("ID", "IDs"))
        valid = refs.ids.get(attr, frozenset()) if is_id else getattr(refs, attr)
    else:
        return
    for item in _values(value):
        for part in (p.strip() for p in item.split(",")) if param.list_value else [item.strip()]:
            if part == "" or SMART_TAG in part:
                continue
            if is_id:
                ok = part in valid
            elif param.kind == "dynamic_field":
                ok = part.removeprefix(DF_OPTION_PREFIX) in refs.dynamic_fields
            else:
                ok = _known(valid, part)
            if not ok:
                out.add(
                    "action_reference_missing",
                    "error",
                    action,
                    f"Config.{key}",
                    key=key,
                    value=part,
                )


#: modules that act only in ITSM mode ``tiqora`` (a warning elsewhere: they are inert)
_ITSM_MODE_MODULES = frozenset({"ConfigItemUpdate"})


def _check_action(
    action: DesignerEntityOut,
    refs: ReferenceData,
    modules: Mapping[str, TransitionActionModule],
    parallel_operation: bool,
    itsm_mode: str | None,
    out: _Issues,
) -> None:
    module_name = _short_module(_s(action.config.get("Module")))
    module = modules.get(module_name)
    if module is None or module.run is None:
        out.add("action_module_unsupported", "warning", action, "Module", module=module_name)
    if module is None:
        return
    if module_name in _ITSM_MODE_MODULES and itsm_mode is not None and itsm_mode != "tiqora":
        out.add(
            "action_module_inactive_in_mode",
            "warning",
            action,
            "Module",
            module=module_name,
            mode=itsm_mode,
        )
    if module.tiqora_only and parallel_operation:
        out.add("tiqora_only_module_parallel", "warning", action, "Module", module=module_name)
    config = _map(action.config.get("Config"))
    for param in module.params:
        keys = param.one_of or (param.name,)
        if param.required and not any(_has_value(config.get(k)) for k in keys):
            out.add(
                "action_required_param_missing",
                "error",
                action,
                f"Config.{param.name}",
                param=" / ".join(keys),
            )
        for key in keys:
            if key in config:
                _check_reference_value(action, refs, param, key, config[key], out)
    if module_name in _DF_KEY_MODULES:
        skip = _DF_KEY_MODULES[module_name]
        for key in config:
            if key in skip or any(key in (p.one_of or (p.name,)) for p in module.params):
                continue
            if key.removeprefix(DF_OPTION_PREFIX) not in refs.dynamic_fields:
                out.add(
                    "action_reference_missing",
                    "error",
                    action,
                    f"Config.{key}",
                    key=key,
                    value=key,
                )


# --- entry point --------------------------------------------------------------------------


def validate_process(
    draft: ProcessDraftOut,
    refs: ReferenceData,
    *,
    parallel_operation: bool,
    itsm_mode: str | None = None,
) -> list[ValidationIssue]:
    """All issues of one process draft; errors first, then warnings, each in discovery order.

    ``itsm_mode`` is the effective ``itsm.mode``; ``None`` (unknown) skips the mode warnings.
    """
    out = _Issues()
    _check_process(draft, out)
    for dialog in draft.activity_dialogs:
        _check_dialog(dialog, refs, out)
    for transition in draft.transitions:
        _check_transition(transition, refs, out)
    modules = {m.name: m for m in get_registry().items(TRANSITION_ACTIONS)}
    for action in draft.transition_actions:
        _check_action(action, refs, modules, parallel_operation, itsm_mode, out)
    return sorted(out.items, key=lambda i: 0 if i.severity == "error" else 1)


# --- reference data -----------------------------------------------------------------------

_NAME_QUERIES = {
    "queues": "SELECT id, name FROM queue WHERE valid_id = 1",
    "states": "SELECT id, name FROM ticket_state WHERE valid_id = 1",
    "priorities": "SELECT id, name FROM ticket_priority WHERE valid_id = 1",
    "types": "SELECT id, name FROM ticket_type WHERE valid_id = 1",
    "services": "SELECT id, name FROM service WHERE valid_id = 1",
    "slas": "SELECT id, name FROM sla WHERE valid_id = 1",
    "users": "SELECT id, login FROM users WHERE valid_id = 1",
    "calendars": "SELECT id, name FROM calendar WHERE valid_id = 1",
}


async def load_reference_data(session: AsyncSession) -> ReferenceData:
    """Valid names and ids, one query per table (never a join across table families)."""
    names: dict[str, frozenset[str]] = {}
    ids: dict[str, frozenset[str]] = {}
    for attr, sql in _NAME_QUERIES.items():
        rows = (await session.execute(text(sql))).all()
        names[attr] = frozenset(_s(r[1]) for r in rows)
        ids[attr] = frozenset(_s(r[0]) for r in rows)
    df_rows = (
        (
            await session.execute(
                select(DynamicField).where(
                    DynamicField.valid_id == VALID_ID, DynamicField.object_type == "Ticket"
                )
            )
        )
        .scalars()
        .all()
    )
    dynamic_fields: dict[str, DfInfo] = {}
    for df in df_rows:
        possible = config_from_yaml(df.config).get("PossibleValues")
        dynamic_fields[df.name] = DfInfo(
            type=df.field_type,
            possible_values=(
                frozenset(_s(k) for k in possible) if isinstance(possible, Mapping) else None
            ),
        )
    return ReferenceData(dynamic_fields=dynamic_fields, ids=ids, **names)
