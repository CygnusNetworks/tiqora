"""Every ``ChangeError`` code a screen can meet has a text (``itsm.change.errors.<code>``).

The scan reads ``ChangeError("<code>", ...)`` calls (AST, so multi-line calls count) in
``itsm/change/**`` and the change route modules ``api/**/itsm_change*.py``. Codes built at run
time are listed in :data:`DYNAMIC` with the codes they can produce; the number of such call sites
is pinned, so a new one has to be added there. :data:`INTERNAL` lists codes no screen shows, each
with its reason. The texts live in ``frontend/src/i18n/locales/en.json`` (de.json and the other
locales follow through ``check-i18n-keys.mjs``).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "backend" / "src" / "tiqora"
_EN = _ROOT / "frontend" / "src" / "i18n" / "locales" / "en.json"
_DE = _ROOT / "frontend" / "src" / "i18n" / "locales" / "de.json"

#: call sites whose code is computed -> the codes they can raise
DYNAMIC: dict[str, frozenset[str]] = {
    # ``TicketAccessDenied.code`` (``domain/ticket_access.py``): service._ticket, tasks (twice)
    "exc.code": frozenset({"ticket_not_found", "forbidden"}),
    # ``archive_blocker`` of the legacy archive (admin route)
    "blocked.code": frozenset({"legacy_archive_mode_b_only", "legacy_archive_no_tables"}),
    # ``cab._check_refs``: the member kinds with an id
    "f'{kind}_not_found'": frozenset(
        {"user_not_found", "group_not_found", "role_not_found", "list_not_found"}
    ),
    # ``templates._STALE_CODES``
    "_STALE_CODES[first.kind]": frozenset(
        {
            "template_queue_not_found",
            "template_priority_not_found",
            "template_service_not_found",
            "template_cab_not_found",
            "template_owner_not_found",
            "template_risk_answer_invalid",
        }
    ),
}
#: how many computed call sites :data:`DYNAMIC` covers (``exc.code`` occurs three times)
DYNAMIC_SITES = 6

#: codes without a text, each with the reason no screen needs one
INTERNAL: dict[str, str] = {
    "transition_refused": "carries guard codes in ``failures``; the transition dialog shows those",
}


def _modules() -> list[Path]:
    change = sorted((_SRC / "itsm" / "change").rglob("*.py"))
    routes = sorted(p for p in (_SRC / "api").rglob("itsm_change*.py"))
    return [*change, *routes]


def _scan() -> tuple[set[str], list[str]]:
    literal: set[str] = set()
    dynamic: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ChangeError"
                and node.args
            ):
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                literal.add(first.value)
            else:
                dynamic.append(ast.unparse(first))
    return literal, dynamic


def _errors(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data["itsm"]["change"]["errors"])


def test_scan_sees_the_known_call_sites() -> None:
    literal, dynamic = _scan()
    assert {"change_final", "transition_refused", "standard_template_invalid"} <= literal
    assert {"risk_question_in_use", "legacy_forbidden", "search_too_broad"} <= literal
    assert sorted(set(dynamic)) == sorted(DYNAMIC), dynamic
    assert len(dynamic) == DYNAMIC_SITES, dynamic


def test_every_change_error_code_has_a_text() -> None:
    literal, _ = _scan()
    wanted = (literal | set().union(*DYNAMIC.values())) - INTERNAL.keys()
    for path in (_EN, _DE):
        texts = _errors(path)
        missing = sorted(c for c in wanted if not str(texts.get(c) or "").strip())
        assert missing == [], f"{path.name}: itsm.change.errors.<code> missing for {missing}"


def test_internal_codes_are_still_raised() -> None:
    literal, _ = _scan()
    assert INTERNAL.keys() - literal == set()
