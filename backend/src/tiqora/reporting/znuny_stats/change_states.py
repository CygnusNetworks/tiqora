"""Znuny change states (general catalog ``ITSM::ChangeManagement::Change::State``) in Tiqora terms.

Pure data, shared by ``mapping_common`` (value resolution) and ``map_itsm`` (re-export).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

#: Assumption 8, keys casefolded: Znuny state name -> Tiqora ``change_status`` values.
ZNUNY_CHANGE_STATES: Final[Mapping[str, tuple[str, ...]]] = {
    "requested": ("submitted", "assessment"),
    "pending approval": ("approval",),
    "rejected": ("rejected",),
    "approved": ("scheduled",),
    "in progress": ("implementing",),
    "pending pir": ("review",),
    "successful": ("closed_successful",),
    "failed": ("closed_failed", "closed_backed_out"),
    "canceled": ("cancelled",),
    "retracted": ("cancelled",),
}

CHANGE_STATE_MAPPED: Final = "Znuny-Status wurden auf Tiqora-Phasen abgebildet."
CHANGE_STATE_UNKNOWN: Final = "Diesen Change-Status gibt es in Tiqora nicht."


def to_phase(status: str) -> str:
    """The phase of a ``change_status`` value (``closed_*`` collapse to ``closed``)."""
    return "closed" if status.startswith("closed_") else status
