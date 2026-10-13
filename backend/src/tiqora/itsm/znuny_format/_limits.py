"""Size guard shared by the XMLData converters (untrusted shapes read from a Znuny database)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

MAX_XML_DEPTH: Final = 16
MAX_XML_LEAVES: Final = 50_000


def guard_xml_data(xml_data: Mapping[str, Any]) -> None:
    """Refuse XMLData nested deeper than 16 attribute levels or with over 50 000 values.

    Raises ``ValueError``. A *level* is one attribute key whose entries sit below it; the walk
    is bounded by the depth limit, so it cannot recurse deeper than that itself.
    """
    leaves = 0

    def walk(node: Mapping[str, Any], depth: int) -> None:
        nonlocal leaves
        if depth > MAX_XML_DEPTH:
            raise ValueError(f"XMLData is nested deeper than {MAX_XML_DEPTH} levels")
        for value in node.values():
            if not isinstance(value, list):
                leaves += 1
                continue
            for entry in value:
                if isinstance(entry, Mapping):
                    leaves += 1
                    walk(entry, depth + 1)
                elif entry is not None:
                    leaves += 1
            if leaves > MAX_XML_LEAVES:
                raise ValueError(f"XMLData has more than {MAX_XML_LEAVES} values")
        if leaves > MAX_XML_LEAVES:
            raise ValueError(f"XMLData has more than {MAX_XML_LEAVES} values")

    walk(xml_data, 1)
