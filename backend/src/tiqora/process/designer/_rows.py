"""Row helpers shared by the designer modules (writer, draft, import/export)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tiqora.db.legacy.process_management import (
    PmActivity,
    PmActivityDialog,
    PmProcess,
    PmTransition,
    PmTransitionAction,
)

ENTITY_MODELS: dict[str, Any] = {
    "Process": PmProcess,
    "Activity": PmActivity,
    "ActivityDialog": PmActivityDialog,
    "Transition": PmTransition,
    "TransitionAction": PmTransitionAction,
}


def as_text(value: object) -> str:
    """A DB column value as text (``LONGBLOB`` / ``bytea`` configs come back as bytes)."""
    if isinstance(value, memoryview | bytearray | bytes):
        return bytes(value).decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def order_key(key: object) -> tuple[int, int, str]:
    """Znuny's numeric-then-text order of ``"1".."n"`` keys (``ActivityDialog``, ``Condition``)."""
    s = str(key)
    return (0, int(s), s) if s.isdigit() else (1, 0, s)
