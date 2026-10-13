"""Znuny class definitions for the importer (R8: one parser, no third grammar implementation).

``parse_znuny_definition`` is cmdb's (itsm1's bounded YAML/Perl reader plus Znuny's count rules,
mapped onto Tiqora attributes). :func:`parse_definition_with_issues` adds what the importer must
report (IMPORT-DEFPARSE): attributes whose ``Key`` Znuny's ``DefinitionCheck`` would refuse
(whitespace, non-ASCII) are left out of the class with an issue instead of failing the whole
class, and attribute keys the Tiqora model does not carry are listed instead of vanishing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

import yaml

from tiqora.cmdb.znuny_definition import (
    DefinitionImportResult,
    DefinitionParseError,
    parse_znuny_definition,
)
from tiqora.itsm.znuny_adapter.definitions import (
    MAX_DEPTH,
    MAX_NODES,
    _load_yaml,
)
from tiqora.itsm.znuny_adapter.definitions import (
    DefinitionError as _CoreDefinitionError,
)
from tiqora.znuny.perl_data import MAX_TEXT_CHARS, PerlDataError, parse_perl_data

DefinitionError = DefinitionParseError

#: attribute keys of Znuny's definition grammar that the Tiqora attribute model carries
KNOWN_ATTRIBUTE_KEYS: Final = frozenset(
    {"Key", "Name", "Searchable", "Input", "CountMin", "CountMax", "CountDefault", "Sub"}
)

ISSUE_KEY_INVALID: Final = "definition_key_invalid"
ISSUE_KEY_UNKNOWN: Final = "definition_attribute_key_unknown"


@dataclass(frozen=True, slots=True)
class DefinitionIssue:
    """One finding of a class definition (``path`` dotted attribute path, ``key`` the name)."""

    code: str
    path: str
    key: str
    message: str


@dataclass
class DefinitionWithIssues:
    result: DefinitionImportResult
    issues: list[DefinitionIssue] = field(default_factory=list)


def _read_raw(text: str, fmt: Literal["yaml", "perl"]) -> Any:
    if len(text) > MAX_TEXT_CHARS:
        raise DefinitionParseError(f"Definition größer als {MAX_TEXT_CHARS} Zeichen")
    try:
        if fmt == "perl":
            return parse_perl_data(text, max_depth=MAX_DEPTH, max_nodes=MAX_NODES)
        return _load_yaml(text if text.lstrip().startswith("---") else "---\n" + text)
    except (PerlDataError, _CoreDefinitionError) as exc:
        raise DefinitionParseError(f"Definition nicht lesbar: {exc}") from exc


def _key_is_valid(raw: Any) -> bool:
    key = str(raw)
    return bool(key) and key.isascii() and not any(ch.isspace() for ch in key)


def _scan(items: list[Any], prefix: str, issues: list[DefinitionIssue]) -> list[Any]:
    kept: list[Any] = []
    for item in items:
        if not isinstance(item, dict) or item.get("Key") is None:
            kept.append(item)  # the real parser reports the shape error
            continue
        key = str(item["Key"])
        path = f"{prefix}{key}"
        if isinstance(item["Key"], (dict, list)):
            kept.append(item)
            continue
        if not _key_is_valid(item["Key"]):
            issues.append(
                DefinitionIssue(
                    ISSUE_KEY_INVALID,
                    path,
                    key,
                    f"Attribut {path}: Schlüssel mit Leerzeichen oder Nicht-ASCII-Zeichen, "
                    "nicht übernommen",
                )
            )
            continue
        for extra in sorted(k for k in item if str(k) not in KNOWN_ATTRIBUTE_KEYS):
            issues.append(
                DefinitionIssue(
                    ISSUE_KEY_UNKNOWN,
                    path,
                    str(extra),
                    f"Attribut {path}: Eintrag {extra} wird von Tiqora nicht abgebildet",
                )
            )
        if isinstance(item.get("Sub"), list):
            item = {**item, "Sub": _scan(item["Sub"], f"{path}.", issues)}
        kept.append(item)
    return kept


def parse_definition_with_issues(text: str, fmt: Literal["yaml", "perl"]) -> DefinitionWithIssues:
    """Parse like :func:`parse_znuny_definition`, plus the findings listed in the module doc."""
    raw = _read_raw(text, fmt)
    issues: list[DefinitionIssue] = []
    if not isinstance(raw, list):
        return DefinitionWithIssues(parse_znuny_definition(text, fmt), issues)
    cleaned = _scan(raw, "", issues)
    if not any(i.code == ISSUE_KEY_INVALID for i in issues):
        return DefinitionWithIssues(parse_znuny_definition(text, fmt), issues)
    # re-feed the cleaned tree through the one parser (as bounded YAML, never evaluated)
    dumped = "---\n" + yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False)
    return DefinitionWithIssues(parse_znuny_definition(dumped, "yaml"), issues)


__all__ = [
    "ISSUE_KEY_INVALID",
    "ISSUE_KEY_UNKNOWN",
    "DefinitionError",
    "DefinitionIssue",
    "DefinitionParseError",
    "DefinitionWithIssues",
    "parse_definition_with_issues",
    "parse_znuny_definition",
]
