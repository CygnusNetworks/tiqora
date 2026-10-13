"""The import report's issue codes, their German explanations and the admin documentation agree.

Every code the importer can emit (literal second argument of ``report.issue(...)`` plus the
codes the shared Znuny-format converters hand through) has an entry in ``ISSUE_CODES``; every
entry is emitted somewhere; every entry has a row in the issue-code table of
``docs/itsm-import.md``.
"""

from __future__ import annotations

import ast
import re
import typing
from pathlib import Path

from tiqora.itsm.importer.issue_codes import ISSUE_CODES
from tiqora.itsm.znuny_format import definition as znuny_definition
from tiqora.itsm.znuny_format.xmldata import AttrIssue

_IMPORTER = Path(__file__).resolve().parents[1] / "src" / "tiqora" / "itsm" / "importer"
_DOC = Path(__file__).resolve().parents[2] / "docs" / "itsm-import.md"
_CODE = re.compile(r"^[a-z]+(_[a-z]+)*$")


def _emitted_literals() -> tuple[set[str], int]:
    """Literal codes in ``<x>.issue(step, "<code>", ...)`` calls, and the count of dynamic ones."""
    literals: set[str] = set()
    dynamic = 0
    for path in sorted(_IMPORTER.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "issue"
            ):
                continue
            keyword = [kw.value for kw in node.keywords if kw.arg == "code"]
            if len(node.args) < 2 and not keyword:
                continue
            code = keyword[0] if keyword else node.args[1]
            if isinstance(code, ast.Constant) and isinstance(code.value, str):
                literals.add(code.value)
            else:
                dynamic += 1
    return literals, dynamic


def _passed_through() -> set[str]:
    attr_codes = set(typing.get_args(AttrIssue.__annotations__["code"]))
    if not attr_codes:  # ``from __future__ import annotations`` stores a string
        attr_codes = set(typing.get_args(typing.get_type_hints(AttrIssue)["code"]))
    return {znuny_definition.ISSUE_KEY_INVALID, znuny_definition.ISSUE_KEY_UNKNOWN} | attr_codes


def test_scan_sees_the_known_emitters() -> None:
    literals, dynamic = _emitted_literals()
    assert {"bad_value", "no_definition", "changes_not_imported"} <= literals
    assert dynamic >= 2  # classes (definition issues) and cis (attribute issues)
    assert _passed_through() >= {"unknown_attribute", "definition_key_invalid"}


def test_every_emitted_code_has_an_explanation() -> None:
    literals, _ = _emitted_literals()
    emitted = literals | _passed_through()
    assert emitted - ISSUE_CODES.keys() == set()


def test_every_explained_code_is_emitted() -> None:
    literals, _ = _emitted_literals()
    emitted = literals | _passed_through()
    assert ISSUE_CODES.keys() - emitted == set()


def test_codes_are_snake_case_with_a_german_sentence() -> None:
    for code, text in ISSUE_CODES.items():
        assert _CODE.match(code), code
        assert len(text) > 20, code
        assert not text.endswith("."), code  # shown inline and in the CSV, no trailing period


def test_every_code_has_a_row_in_the_documentation() -> None:
    doc = _DOC.read_text(encoding="utf-8")
    assert "## Importer: Zuordnung und Bericht" in doc
    rows = set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", doc, flags=re.MULTILINE))
    assert ISSUE_CODES.keys() - rows == set()
