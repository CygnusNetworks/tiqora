"""YAML parsing of ProcessManagement (BPM) ``pm_*.config``/``pm_process.layout``
columns into typed Pydantic models.

Mirrors the ``yaml.safe_load``/``safe_dump`` convention used for
``dynamic_field.config`` in ``tiqora.api.v1.admin.dynamic_fields`` — Znuny
writes these columns with Perl's ``YAML::Dump`` and reads them with
``YAML::Load``; ``yaml.safe_load`` round-trips the same plain
scalars/mappings/sequences.

Config shapes (verified against
``znuny-6.5.22/scripts/test/sample/ProcessManagement/*.yml``):

``pm_process.config``::

    Description: <str>
    StartActivity: Activity-<...>
    StartActivityDialog: ActivityDialog-<...>
    Path:
      Activity-<source>:
        Transition-<id>:
          ActivityEntityID: Activity-<target>
          TransitionAction: [TransitionAction-<id>, ...]

``pm_process.layout`` — designer canvas positions, ``{ActivityEntityID:
{left: ..., top: ...}}``. Cosmetic only; parsed and kept available for a
future designer UI, not consulted by engine logic.

``pm_activity.config``::

    ActivityDialog:
      '1': ActivityDialog-<...>   # ordered map: order number -> EntityID
      '2': ActivityDialog-<...>

``pm_activity_dialog.config``::

    DescriptionLong: <str>
    DescriptionShort: <str>
    FieldOrder: [<field name>, ...]
    Fields:
      <field name>:
        Config: {...}            # per-field extra config, shape varies
        DefaultValue: <any>
        DescriptionLong: <str>
        DescriptionShort: <str>
        Display: '1' | '2'       # '1' = required/mandatory, '2' = optional
                                  # (shown, not required). Verified against
                                  # Kernel/Modules/AgentTicketProcess.pm:
                                  # `Mandatory => Display == 2` is FALSE, i.e.
                                  # Display == 2 means NOT mandatory, anything
                                  # else truthy (in practice '1') means
                                  # mandatory. Display == 0/absent means the
                                  # field is skipped entirely (not in
                                  # FieldOrder) — Tiqora does not special-case
                                  # a literal '0' value beyond exposing it
                                  # as-is; callers should treat missing/'0' as
                                  # "do not render".
    Interface: [AgentInterface, CustomerInterface]
    Permission: <str>
    RequiredLock: '0' | '1'
    SubmitAdviceText: <str>
    SubmitButtonText: <str>

  ``Fields`` keys are either ticket/article pseudo-fields (this loader
  handles all of them generically — it does not validate the key against an
  allow-list) or ``DynamicField_<Name>``. Only Queue, State, Priority,
  Title, Owner, Article, and ``DynamicField_*`` are expected to be
  understood by later subtasks (the activity-dialog *renderer*); any other
  pseudo-field name parses fine here but is not guaranteed to be rendered.

``pm_transition.config``::

    Condition:
      '1':
        Fields:
          <field name>: {Match: <any>, Type: <str>}
        Type: and | or           # how Fields within this block combine
    ConditionLinking: and | or    # how condition blocks combine

  No ``Condition`` key (or an empty one) means the transition matches
  unconditionally (standard Znuny semantics: an empty condition set is
  vacuously true). Supported ``Type`` values for condition evaluation
  (subtask 2's job — this module only preserves them) are String, Regexp,
  Contains, NotContains, Equal, NotEqual, per
  ``Kernel/System/ProcessManagement/TransitionValidation/*.pm``.
  GreaterThan(OrEqual)/LessThan(OrEqual) and any ``Module``-based (custom
  Perl module) condition type are UNSUPPORTED/deferred — this loader still
  parses and preserves the raw ``type`` string, but downstream evaluation
  code must treat unknown types as non-matching rather than crashing.

``pm_transition_action.config``::

    Config: {...}                 # shape varies by Module, see
                                   # Kernel/System/ProcessManagement/TransitionAction/*.pm
    Module: Kernel::System::ProcessManagement::TransitionAction::TicketStateSet

  This loader preserves ``module`` (full dotted string) and ``config``
  (dict, as-is) without interpreting them — action *dispatch* is a later
  subtask (``process/engine.py``, not implemented here).
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


def _load_yaml_mapping(raw: str | bytes | None) -> dict[str, Any]:
    """Parse a ``pm_*.config``/``layout`` TEXT column into a dict.

    Returns ``{}`` for empty/``None`` input or content that fails to parse
    as YAML or does not decode to a mapping — mirrors
    ``tiqora.api.v1.admin.dynamic_fields.config_from_yaml``.
    """
    if not raw:
        return {}
    if isinstance(raw, memoryview | bytearray):
        raw = bytes(raw)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _order_key(key: str) -> tuple[int, str]:
    """Sort helper for numbered-string map keys (``'1'``, ``'2'``, ...).

    Falls back to a lexicographic tiebreak for non-numeric keys instead of
    raising, since the YAML is externally authored (Znuny process designer)
    and should never crash the loader.
    """
    try:
        return (int(key), key)
    except ValueError:
        return (10**9, key)


class TransitionPathEntry(BaseModel):
    """One outgoing transition entry under ``Path[<activity>][<transition>]``."""

    model_config = ConfigDict(frozen=True)

    activity_entity_id: str
    transition_actions: list[str] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransitionPathEntry:
        return cls(
            activity_entity_id=data.get("ActivityEntityID") or "",
            transition_actions=list(data.get("TransitionAction") or []),
        )


class ProcessConfig(BaseModel):
    """Parsed ``pm_process.config``."""

    model_config = ConfigDict(frozen=True)

    description: str = ""
    start_activity: str | None = None
    start_activity_dialog: str | None = None
    # activity_entity_id -> {transition_entity_id -> TransitionPathEntry}
    path: dict[str, dict[str, TransitionPathEntry]] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, raw: str | bytes | None) -> ProcessConfig:
        data = _load_yaml_mapping(raw)
        path_raw: dict[str, Any] = data.get("Path") or {}
        path: dict[str, dict[str, TransitionPathEntry]] = {}
        for activity_id, transitions in path_raw.items():
            transitions = transitions or {}
            path[activity_id] = {
                transition_id: TransitionPathEntry.from_dict(entry or {})
                for transition_id, entry in transitions.items()
            }
        return cls(
            description=data.get("Description") or "",
            start_activity=data.get("StartActivity"),
            start_activity_dialog=data.get("StartActivityDialog"),
            path=path,
        )


class ProcessLayout(BaseModel):
    """Parsed ``pm_process.layout`` — cosmetic designer canvas positions only."""

    model_config = ConfigDict(frozen=True)

    positions: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, raw: str | bytes | None) -> ProcessLayout:
        return cls(positions=_load_yaml_mapping(raw))


class ActivityConfig(BaseModel):
    """Parsed ``pm_activity.config``."""

    model_config = ConfigDict(frozen=True)

    # Insertion-ordered by the numeric ActivityDialog order key ('1', '2', ...).
    activity_dialogs: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, raw: str | bytes | None) -> ActivityConfig:
        data = _load_yaml_mapping(raw)
        raw_dialogs: dict[str, Any] = data.get("ActivityDialog") or {}
        ordered = dict(sorted(raw_dialogs.items(), key=lambda kv: _order_key(kv[0])))
        return cls(activity_dialogs=ordered)

    @property
    def ordered_activity_dialog_entity_ids(self) -> list[str]:
        return list(self.activity_dialogs.values())


class ActivityDialogFieldConfig(BaseModel):
    """One entry under ``Fields`` in an activity dialog config."""

    model_config = ConfigDict(frozen=True)

    config: dict[str, Any] = Field(default_factory=dict)
    default_value: Any = None
    description_long: str = ""
    description_short: str = ""
    display: str = "1"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActivityDialogFieldConfig:
        return cls(
            config=data.get("Config") or {},
            default_value=data.get("DefaultValue"),
            description_long=data.get("DescriptionLong") or "",
            description_short=data.get("DescriptionShort") or "",
            display=str(data.get("Display") if data.get("Display") is not None else "1"),
        )

    @property
    def required(self) -> bool:
        """``True`` unless ``Display == '2'`` (optional/shown-not-required)."""
        return self.display != "2"


class ActivityDialogConfig(BaseModel):
    """Parsed ``pm_activity_dialog.config``."""

    model_config = ConfigDict(frozen=True)

    description_long: str = ""
    description_short: str = ""
    field_order: list[str] = Field(default_factory=list)
    fields: dict[str, ActivityDialogFieldConfig] = Field(default_factory=dict)
    interface: list[str] = Field(default_factory=list)
    permission: str = ""
    required_lock: str = "0"
    submit_advice_text: str = ""
    submit_button_text: str = ""

    @classmethod
    def from_yaml(cls, raw: str | bytes | None) -> ActivityDialogConfig:
        data = _load_yaml_mapping(raw)
        raw_fields: dict[str, Any] = data.get("Fields") or {}
        return cls(
            description_long=data.get("DescriptionLong") or "",
            description_short=data.get("DescriptionShort") or "",
            field_order=list(data.get("FieldOrder") or []),
            fields={
                name: ActivityDialogFieldConfig.from_dict(field_data or {})
                for name, field_data in raw_fields.items()
            },
            interface=list(data.get("Interface") or []),
            permission=data.get("Permission") or "",
            required_lock=str(data.get("RequiredLock") or "0"),
            submit_advice_text=data.get("SubmitAdviceText") or "",
            submit_button_text=data.get("SubmitButtonText") or "",
        )


class TransitionConditionField(BaseModel):
    """One field match inside a transition condition block."""

    model_config = ConfigDict(frozen=True)

    match: Any = None
    type_: str = "String"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransitionConditionField:
        return cls(match=data.get("Match"), type_=data.get("Type") or "String")


class TransitionConditionBlock(BaseModel):
    """One numbered condition block (``Condition['1']``, ``Condition['2']``, ...)."""

    model_config = ConfigDict(frozen=True)

    type_: str = "and"
    fields: dict[str, TransitionConditionField] = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransitionConditionBlock:
        raw_fields: dict[str, Any] = data.get("Fields") or {}
        return cls(
            type_=data.get("Type") or "and",
            fields={
                name: TransitionConditionField.from_dict(field_data or {})
                for name, field_data in raw_fields.items()
            },
        )


class TransitionConfig(BaseModel):
    """Parsed ``pm_transition.config``.

    A transition with no ``Condition`` key (or an empty one) has an empty
    ``conditions`` list here — callers (subtask 2's condition evaluator)
    must treat an empty list as "matches unconditionally", per Znuny
    semantics.
    """

    model_config = ConfigDict(frozen=True)

    condition_linking: str = "and"
    conditions: list[TransitionConditionBlock] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, raw: str | bytes | None) -> TransitionConfig:
        data = _load_yaml_mapping(raw)
        raw_conditions: dict[str, Any] = data.get("Condition") or {}
        ordered = sorted(raw_conditions.items(), key=lambda kv: _order_key(kv[0]))
        return cls(
            condition_linking=data.get("ConditionLinking") or "and",
            conditions=[TransitionConditionBlock.from_dict(block or {}) for _, block in ordered],
        )


class TransitionActionConfig(BaseModel):
    """Parsed ``pm_transition_action.config``.

    ``module`` and ``config`` are preserved as-is; dispatch by module name
    happens in a later subtask's ``process/engine.py``, not here.
    """

    model_config = ConfigDict(frozen=True)

    module: str = ""
    config: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, raw: str | bytes | None) -> TransitionActionConfig:
        data = _load_yaml_mapping(raw)
        return cls(module=data.get("Module") or "", config=data.get("Config") or {})
