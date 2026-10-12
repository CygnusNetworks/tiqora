"""Import column mapping (D9): targets such as ``attr:NIC[2].IPAddress[1]`` and the options.

Pure module (no database). Positions are 1-based; a missing ``[n]`` means ``[1]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, Field

from tiqora.cmdb.definition import KEY_RE, MAX_COUNT, MAX_DEPTH

TargetKind = Literal["number", "name", "deployment_state", "incident_state", "attr", "ignore"]

_SIMPLE: Final[frozenset[str]] = frozenset(
    {"number", "name", "deployment_state", "incident_state", "ignore"}
)
_KEY: Final = re.compile(KEY_RE)
_SEGMENT: Final = re.compile(r"^([^\[\]]+)(?:\[([^\[\]]*)\])?$")


class TargetError(ValueError):
    """A mapping target that cannot be parsed; the message is German and user-facing."""


@dataclass(frozen=True, slots=True)
class ColumnTarget:
    kind: TargetKind
    #: attribute path as ``(key, position)`` pairs, e.g. ``(("NIC", 2), ("IPAddress", 1))``
    path: tuple[tuple[str, int], ...] = ()

    @property
    def dotted(self) -> str:
        """Definition path without positions (``NIC.IPAddress``), as in the index table."""
        return ".".join(k for k, _ in self.path)


def parse_target(text: str) -> ColumnTarget:
    raw = text.strip()
    if raw in _SIMPLE:
        return ColumnTarget(raw)  # type: ignore[arg-type]
    if not raw.startswith("attr:"):
        raise TargetError(f"Unbekanntes Ziel „{raw}“.")
    body = raw[len("attr:") :]
    if not body:
        raise TargetError("Beim Ziel „attr:“ fehlt der Attributname.")
    path: list[tuple[str, int]] = []
    for segment in body.split("."):
        m = _SEGMENT.match(segment)
        if m is None:
            raise TargetError(f"Ungültiger Attributpfad „{body}“.")
        key, pos = m.group(1), m.group(2)
        if not _KEY.match(key):
            raise TargetError(f"Ungültiger Attributschlüssel „{key}“.")
        index = 1
        if pos is not None:
            if not pos.isascii() or not pos.isdigit():
                raise TargetError(f"Ungültige Position „[{pos}]“ bei „{key}“.")
            index = int(pos)
            if index < 1 or index > MAX_COUNT:
                raise TargetError(
                    f"Die Position bei „{key}“ muss zwischen 1 und {MAX_COUNT} liegen."
                )
        path.append((key, index))
    if len(path) > MAX_DEPTH:
        raise TargetError(f"Der Attributpfad ist tiefer als {MAX_DEPTH} Ebenen.")
    return ColumnTarget("attr", tuple(path))


def format_target(target: ColumnTarget) -> str:
    """Inverse of :func:`parse_target`; ``[1]`` is left out (the export header syntax)."""
    if target.kind != "attr":
        return target.kind
    return "attr:" + ".".join(k if i == 1 else f"{k}[{i}]" for k, i in target.path)


class ImportOptions(BaseModel):
    delimiter: Literal[";", ",", "\t", "|"] = ";"
    encoding: Literal["utf-8", "cp1252"] = "utf-8"
    header_row: bool = True
    sheet: str | None = Field(None, max_length=200)
    #: alternatives, tried in order: the first one with a non-empty cell identifies the row
    #: (``number``, ``name`` or an ``attr:`` target of a searchable attribute)
    identifiers: list[str] = Field(default_factory=lambda: ["number"], min_length=1, max_length=5)
    empty_cells: Literal["keep", "clear"] = "keep"
    on_error: Literal["abort", "skip"] = "abort"
    create_missing: bool = True


__all__ = [
    "ColumnTarget",
    "ImportOptions",
    "TargetError",
    "TargetKind",
    "format_target",
    "parse_target",
]
