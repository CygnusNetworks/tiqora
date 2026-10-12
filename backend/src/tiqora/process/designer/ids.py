"""Entity ids for the process designer, in Znuny's ``<Type>-<32 hex>`` shape."""

from __future__ import annotations

import re
import secrets
from typing import Literal, get_args

EntityType = Literal["Process", "Activity", "ActivityDialog", "Transition", "TransitionAction"]

SubEntityType = Literal["Activity", "ActivityDialog", "Transition", "TransitionAction"]

ENTITY_TYPES: tuple[EntityType, ...] = get_args(EntityType)
SUB_ENTITY_TYPES: tuple[EntityType, ...] = tuple(t for t in ENTITY_TYPES if t != "Process")

# ``pm_*.entity_id`` is String(50); the lookahead anchors the total length.
ENTITY_ID_RE = re.compile(
    r"\A(?=.{1,50}\Z)(Process|Activity|ActivityDialog|Transition|TransitionAction)-[0-9a-zA-Z]{1,40}\Z"
)


def generate_entity_id(entity_type: EntityType) -> str:
    return f"{entity_type}-{secrets.token_hex(16)}"
