"""Report files: a saved report as a portable JSON / YAML document (export and import).

Importing never executes anything: YAML is read with the safe loader and any alias
(anchor reference) is refused, so a "billion laughs" document cannot blow up memory.
"""

from __future__ import annotations

import json
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from tiqora.reporting.definition import DefinitionError, ReportDefinition

FILE_FORMAT = "tiqora-report"
ANCHOR_MESSAGE = "Verweise (Anker) sind in Berichtsdateien nicht erlaubt."
MAX_NESTING: Final = 64
TOO_DEEP_MESSAGE = "Die Datei ist zu tief verschachtelt oder ungültig."


class ReportFileOut(BaseModel):
    """The export / import file."""

    format: Literal["tiqora-report"] = "tiqora-report"
    format_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    definition: ReportDefinition


class ReportFileImportIn(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


def dump_report_file(out: ReportFileOut, fmt: Literal["json", "yaml"]) -> bytes:
    data = out.model_dump(mode="json", by_alias=True, exclude_none=True)
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")


def _load_yaml(content: str) -> Any:
    for event in yaml.parse(content, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.AliasEvent):
            raise DefinitionError(ANCHOR_MESSAGE, path="content")
    return yaml.safe_load(content)


def _too_nested(content: str) -> bool:
    """Cheap linear pre-check: flow nesting (brackets) deeper than any real definition.

    Pure-Python YAML needs minutes for a 200 000 character ``[[[[…`` document, so the
    nesting is bounded before either loader runs (brackets inside strings count too,
    which only ever rejects pathological files).
    """
    depth = 0
    for ch in content:
        if ch in "[{":
            depth += 1
            if depth > MAX_NESTING:
                return True
        elif ch in "]}" and depth:
            depth -= 1
    return False


def _load(content: str) -> Any:
    if _too_nested(content):
        raise DefinitionError(TOO_DEEP_MESSAGE, path="content")
    try:
        return json.loads(content)
    except RecursionError:
        raise DefinitionError(TOO_DEEP_MESSAGE, path="content") from None
    except ValueError:
        pass
    try:
        return _load_yaml(content)
    except DefinitionError:
        raise
    except (RecursionError, yaml.YAMLError, ValueError):
        raise DefinitionError(TOO_DEEP_MESSAGE, path="content") from None


def parse_report_file(content: str) -> ReportFileOut:
    """Parse a report file; every problem is a :class:`DefinitionError` (API: 422)."""
    data = _load(content)
    if not isinstance(data, dict):
        raise DefinitionError("Die Datei ist kein Bericht.", path="content")
    if data.get("format") != FILE_FORMAT or data.get("format_version") != 1:
        raise DefinitionError("Unbekanntes Dateiformat.", path="content")
    try:
        return ReportFileOut.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise DefinitionError(str(first["msg"]), path=loc or "content") from None
