"""Parser for Znuny CI class definitions (pure, no I/O).

``configitem_definition.configitem_definition`` holds YAML in Znuny 6.5 and 7.3 (survey, vector 7);
only databases from old OTRS stock still carry the Perl data format, which is read with the shared
``tiqora.znuny.perl_data`` parser (never evaluated). Both formats produce the same
:class:`AttrDef` tree, prepared with the rules of Znuny's ``Definition.pm``
(``_DefinitionPrepare``):

* ``CountMin`` defaults to 1 when absent (an explicit 0 stays), ``CountMax`` to 1 when absent or 0,
  ``CountMin`` is cut down to ``CountMax``, ``CountDefault`` defaults to 1 and is clamped into
  ``[CountMin, CountMax]``;
* ``Sub`` is kept only when it is a list (an empty list is kept); children are prepared alike;
* ``Key`` must be present, without whitespace and ASCII only (``DefinitionCheck``), also in ``Sub``;
* a stored text that does not start with ``---`` is Znuny's "legacy Perl code format" and reads as
  the empty definition ``--- []`` when the declared format is YAML.

YAML is parsed with ``yaml.safe_load`` semantics under the same bounds as the Perl parser: at most
1 MiB of text, nesting depth 64, a node limit, and no aliases (so no expansion attacks).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

import yaml

from tiqora.znuny.perl_data import MAX_TEXT_CHARS, PerlDataError, parse_perl_data

MAX_DEPTH: Final = 64
MAX_NODES: Final = 200_000


class DefinitionError(ValueError):
    """The stored definition is not a valid, bounded class definition."""


@dataclass(frozen=True, slots=True)
class AttrDef:
    key: str
    name: str
    searchable: bool
    input_type: str  # Znuny Input.Type verbatim ("Text", "GeneralCatalog", "CI", ...)
    input: Mapping[str, Any]  # the rest of the Input hash (Class, Translation, ValueMin, ...)
    count_min: int
    count_max: int
    count_default: int
    sub: tuple[AttrDef, ...]


class _BoundedLoader(yaml.SafeLoader):
    """SafeLoader that refuses aliases and bounds nesting depth and node count."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._depth = 0
        self._nodes = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.events.AliasEvent):
            raise DefinitionError("YAML aliases are not supported in class definitions")
        self._nodes += 1
        if self._nodes > MAX_NODES:
            raise DefinitionError(f"too many YAML nodes (limit {MAX_NODES})")
        self._depth += 1
        if self._depth > MAX_DEPTH:
            raise DefinitionError(f"YAML nesting depth exceeds {MAX_DEPTH}")
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _load_yaml(text: str) -> Any:
    try:
        return yaml.load(text, Loader=_BoundedLoader)  # noqa: S506 - SafeLoader subclass
    except DefinitionError:
        raise
    except yaml.YAMLError as exc:
        raise DefinitionError(f"invalid YAML: {exc}") from exc
    except RecursionError as exc:
        raise DefinitionError(f"YAML nesting depth exceeds {MAX_DEPTH}") from exc


def _int(value: Any, what: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise DefinitionError(f"{what} is not an integer: {value!r}") from exc


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip() not in ("", "0")
    return bool(value)


def _check_key(raw: Any) -> str:
    if raw is None or isinstance(raw, (dict, list)):
        raise DefinitionError("attribute without a Key")
    key = str(raw)
    if not key or any(ch.isspace() for ch in key) or not key.isascii():
        raise DefinitionError(f"invalid attribute Key {key!r} (no whitespace, ASCII only)")
    return key


def _prepare(raw: Any, depth: int) -> AttrDef:
    if depth > MAX_DEPTH:
        raise DefinitionError(f"attribute nesting depth exceeds {MAX_DEPTH}")
    if not isinstance(raw, dict):
        raise DefinitionError("an attribute must be a mapping")
    key = _check_key(raw.get("Key"))
    count_min = _int(raw["CountMin"], "CountMin") if "CountMin" in raw else 1
    count_max = _int(raw.get("CountMax", 0), "CountMax") or 1
    count_min = min(count_min, count_max)
    count_default = _int(raw["CountDefault"], "CountDefault") if "CountDefault" in raw else 1
    count_default = max(count_min, min(count_default, count_max))
    input_raw = raw.get("Input")
    input_hash = dict(input_raw) if isinstance(input_raw, dict) else {}
    input_type = str(input_hash.pop("Type", "") or "")
    sub_raw = raw.get("Sub")
    sub = tuple(_prepare(item, depth + 1) for item in sub_raw) if isinstance(sub_raw, list) else ()
    name = raw.get("Name")
    return AttrDef(
        key=key,
        name=str(name) if name not in (None, "") else key,
        searchable=_truthy(raw.get("Searchable")),
        input_type=input_type,
        input=input_hash,
        count_min=count_min,
        count_max=count_max,
        count_default=count_default,
        sub=sub,
    )


def parse_definition(text: str, fmt: Literal["perl", "yaml"]) -> tuple[AttrDef, ...]:
    """Parse one stored class definition into its prepared attribute tree.

    Raises :class:`DefinitionError` for unparsable, oversized, too deep or invalid input.
    """
    if len(text) > MAX_TEXT_CHARS:
        raise DefinitionError(f"definition larger than {MAX_TEXT_CHARS} characters")
    if fmt == "yaml":
        if not text.startswith("---"):
            return ()  # Znuny: legacy Perl code format reads as the empty definition
        data = _load_yaml(text)
    elif fmt == "perl":
        try:
            data = parse_perl_data(text, max_depth=MAX_DEPTH, max_nodes=MAX_NODES)
        except PerlDataError as exc:
            raise DefinitionError(f"invalid Perl definition: {exc}") from exc
    else:
        raise DefinitionError(f"unknown definition format {fmt!r}")
    if data is None:
        return ()
    if not isinstance(data, list):
        raise DefinitionError("a class definition must be a list of attributes")
    return tuple(_prepare(item, 1) for item in data)


__all__ = ["AttrDef", "DefinitionError", "parse_definition"]
