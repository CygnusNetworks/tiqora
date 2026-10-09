"""Znuny-exact activity dialog field semantics (pure, no I/O).

``Display`` (``AgentTicketProcess.pm:2695, 4916-4936``): ``0``/missing = not shown (the
dialog's ``DefaultValue`` applies; a dynamic field without default is set to empty),
``1`` = shown and optional, ``2`` = shown and mandatory (``Mandatory => Display == 2``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tiqora.process.actor import Interface
from tiqora.process.config import ActivityDialogConfig, ActivityDialogFieldConfig

DISPLAY_HIDDEN = "0"
DISPLAY_OPTIONAL = "1"
DISPLAY_REQUIRED = "2"

# Znuny AgentTicketProcess NameToID (:5082ff): dialog configs may use either spelling.
FIELD_ALIASES: dict[str, str] = {
    "QueueID": "Queue",
    "StateID": "State",
    "PriorityID": "Priority",
    "LockID": "Lock",
    "TypeID": "Type",
    "ServiceID": "Service",
    "SLAID": "SLA",
    "OwnerID": "Owner",
    "ResponsibleID": "Responsible",
    "CustomerUserID": "CustomerUserID",
    "CustomerID": "CustomerID",
}
# A new ticket needs these; Display 1 on them is a config error in Znuny (_CheckField) and
# the values fall back to Process::Default* (AgentTicketProcess.pm:5135-5155).
TICKET_REQUIRED_FIELDS = ("Queue", "State", "Lock", "Priority")
# CustomerTicketProcess.pm:75
CUSTOMER_SKIP_FIELDS = frozenset({"Owner", "Responsible", "Lock", "PendingTime", "CustomerID"})

# Sub-fields of the pseudo-field "Article" that a user may submit; everything else about the
# article (sender, visibility, channel) comes from the dialog config (:5686-5687).
_ARTICLE_INPUT_KEYS = ("Subject", "Body")

__all__ = [
    "CUSTOMER_SKIP_FIELDS",
    "DISPLAY_HIDDEN",
    "DISPLAY_OPTIONAL",
    "DISPLAY_REQUIRED",
    "FIELD_ALIASES",
    "TICKET_REQUIRED_FIELDS",
    "canonical_field_name",
    "dialog_field_order",
    "effective_field_values",
    "field_config",
    "is_present",
    "missing_required_fields",
    "normalize_field_values",
    "visible_fields",
]


def canonical_field_name(name: str) -> str:
    return FIELD_ALIASES.get(name, name)


def dialog_field_order(dialog: ActivityDialogConfig) -> list[str]:
    """``FieldOrder`` (or the ``Fields`` keys when it is empty), canonical and de-duplicated."""
    raw = dialog.field_order or list(dialog.fields)
    seen: dict[str, None] = {}
    for name in raw:
        seen.setdefault(canonical_field_name(str(name)), None)
    return list(seen)


def field_config(dialog: ActivityDialogConfig, canonical_name: str) -> ActivityDialogFieldConfig:
    """Config of a field by canonical name; a field absent from ``Fields`` counts as hidden."""
    for name, cfg in dialog.fields.items():
        if canonical_field_name(name) == canonical_name:
            return cfg
    return ActivityDialogFieldConfig()


def _skipped(name: str, interface: Interface) -> bool:
    return interface == "CustomerInterface" and name in CUSTOMER_SKIP_FIELDS


def visible_fields(dialog: ActivityDialogConfig, interface: Interface) -> list[str]:
    return [
        name
        for name in dialog_field_order(dialog)
        if not field_config(dialog, name).hidden and not _skipped(name, interface)
    ]


def normalize_field_values(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical field names; ``"Article": {"Subject", "Body"}`` flattened to top-level keys."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "Article" and isinstance(value, Mapping):
            for sub in _ARTICLE_INPUT_KEYS:
                if sub in value:
                    out[sub] = value[sub]
            continue
        out[canonical_field_name(key)] = value
    return out


def _has_default(value: Any) -> bool:
    return value is not None and str(value) != ""


def effective_field_values(
    dialog: ActivityDialogConfig, submitted: Mapping[str, Any], interface: Interface
) -> dict[str, Any]:
    """What is actually applied: submitted values of visible fields, overridden by hidden defaults.

    Znuny ignores input for hidden fields and uses ``DefaultValue`` (a dynamic field without
    default is cleared) — ``AgentTicketProcess.pm:4916-4927``.
    """
    normalized = normalize_field_values(submitted)
    visible = visible_fields(dialog, interface)
    values: dict[str, Any] = {name: normalized[name] for name in visible if name in normalized}
    if "Article" in visible:
        for sub in _ARTICLE_INPUT_KEYS:
            if sub in normalized:
                values[sub] = normalized[sub]
    for name in dialog_field_order(dialog):
        cfg = field_config(dialog, name)
        if not cfg.hidden or _skipped(name, interface) or name == "Article":
            continue
        if _has_default(cfg.default_value):
            values[name] = cfg.default_value
        elif name.startswith("DynamicField_"):
            values[name] = []
    return values


def is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, list | tuple):
        # [""] is an empty dynamic field value (BaseSelect/Text EditFieldValueValidate: '' fails
        # Mandatory), not a value.
        return any(is_present(v) for v in value)
    if isinstance(value, dict):
        return len(value) > 0
    return True


def missing_required_fields(
    dialog: ActivityDialogConfig, values: Mapping[str, Any], interface: Interface
) -> list[str]:
    """Visible fields with ``Display: '2'`` that have no value.

    ``Article`` needs BOTH Subject and Body (Znuny stores the article only when both are set,
    ``AgentTicketProcess.pm:5576``).
    """
    missing: list[str] = []
    for name in visible_fields(dialog, interface):
        if not field_config(dialog, name).required:
            continue
        if name == "Article":
            ok = all(is_present(values.get(sub)) for sub in _ARTICLE_INPUT_KEYS)
        else:
            ok = is_present(values.get(name))
        if not ok:
            missing.append(name)
    return missing
