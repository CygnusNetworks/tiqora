"""Znuny ITSM behaviour vectors (wave-4 itsm-core, Task 3). Pure: reads JSON and fixture files.

``fixtures/znuny-itsm/behaviour/<profile>.json`` holds, per profile, ten pinned answers about how
Znuny's ITSM packages behave (``questions``: title, ``source`` file:line references, ``answer``,
``expected`` value used by later tasks, ``consequence`` for Tiqora) and the results the real peer
produced for the scenarios (``peer.results``, written by ``tests/golden/itsm_survey.py
--behaviour``). The tests keep the two halves honest against each other and against the
generated schema fixtures; the Tasks that implement the rules assert against ``expected``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tiqora.itsm.link_type_defaults import DEFAULT_LINK_TYPES

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FIXTURES = BACKEND / "tests" / "fixtures" / "znuny-itsm"
BEHAVIOUR = FIXTURES / "behaviour"
PACKAGE_SOURCES = REPO / "tests" / "golden" / "itsm-packages" / "src"
PROFILES = ("itsm-6", "itsm-7")
QUESTION_KEYS = [str(n) for n in range(1, 11)]

_SOURCE = re.compile(
    r"^(?P<path>[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+):(?P<first>\d+)(?:-(?P<last>\d+))?$"
)
_LEVELS = ("1 very low", "2 low", "3 normal", "4 high", "5 very high")


def _load(profile: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((BEHAVIOUR / f"{profile}.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture(params=PROFILES)
def profile(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def doc(profile: str) -> dict[str, Any]:
    return _load(profile)


def _dig(data: Any, path: str) -> Any:
    for part in path.split("."):
        data = data[part]
    return data


def _peer_results(doc: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = doc["peer"]["results"]
    return results


# --- structure ---------------------------------------------------------------------------------


def test_vectors_cover_every_question(doc: dict[str, Any]) -> None:
    questions = doc["questions"]
    assert sorted(questions, key=int) == QUESTION_KEYS
    for key in QUESTION_KEYS:
        q = questions[key]
        assert q["title"].strip(), key
        assert isinstance(q["answer"], str) and len(q["answer"]) > 80, key
        assert q["expected"] not in (None, "", [], {}), (
            key
        )  # F27: every vector has an expected value
        assert isinstance(q["consequence"], str) and q["consequence"].strip(), key
        assert isinstance(q["resolves"], list), key
        sources = q["source"]
        assert isinstance(sources, list) and sources, key
        for ref in sources:
            match = _SOURCE.match(ref)
            assert match, f"question {key}: malformed source {ref!r}"
            first = int(match["first"])
            last = int(match["last"] or first)
            assert 1 <= first <= last, f"question {key}: bad line range in {ref!r}"


def test_vectors_record_the_peer(doc: dict[str, Any], profile: str) -> None:
    peer = doc["peer"]
    assert doc["profile"] == profile
    assert peer["peer"] == {"itsm-6": "znuny-6.5-itsm", "itsm-7": "znuny-7.3-itsm"}[profile]
    assert peer["znuny_version"].startswith({"itsm-6": "6.5.", "itsm-7": "7.3."}[profile])
    assert "itsm_survey.py --behaviour" in peer["captured_with"]


def test_vectors_have_no_real_data(profile: str) -> None:
    text = (BEHAVIOUR / f"{profile}.json").read_text(encoding="utf-8")
    emails = re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text)
    assert all(host.endswith(("example.org", "example.com", ".test")) for host in emails), emails
    ips = re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", text)
    assert all(ip.startswith(("192.0.2.", "198.51.100.", "203.0.113.")) for ip in ips), ips


def test_source_references_point_into_existing_lines(doc: dict[str, Any]) -> None:
    """Fixture files are always checked; package sources when the (git-ignored) tree is present."""
    checked_fixture = 0
    for key, q in doc["questions"].items():
        for ref in q["source"]:
            match = _SOURCE.match(ref)
            assert match
            rel = match["path"]
            if rel.startswith("backend/"):
                path = REPO / rel
                assert path.is_file(), f"question {key}: {ref}"
                checked_fixture += 1
            else:
                tree = rel.split("/", 1)[0]
                if tree.startswith("znuny-"):
                    continue  # the Znuny core tree is not part of the repository
                path = PACKAGE_SOURCES / rel
                if not path.is_file():
                    continue  # package sources are downloaded on demand (just golden-itsm-fetch)
            lines = len(path.read_text(encoding="utf-8").splitlines())
            assert int(match["last"] or match["first"]) <= lines, f"question {key}: {ref}"
    assert checked_fixture >= 3


# --- 1: priority allocation --------------------------------------------------------------------


def _initial_insert_cells(profile: str) -> dict[tuple[str, str], int]:
    text = (FIXTURES / profile / "initial_insert.sql").read_text(encoding="utf-8")
    cells: dict[tuple[str, str], int] = {}
    pattern = re.compile(
        r"^INSERT INTO cip_allocate \(.*?\) VALUES \(\d+, '([^']+)', '([^']+)', (\d+),", re.M
    )
    for criticality, impact, priority in pattern.findall(text):
        cells[(criticality, impact)] = int(priority)
    return cells


def test_cip_vectors_are_25_cells(doc: dict[str, Any], profile: str) -> None:
    expected = doc["questions"]["1"]["expected"]
    cells = {(c["criticality"], c["impact"]): c["priority_id"] for c in expected["cells"]}
    assert len(expected["cells"]) == len(cells) == 25
    assert set(cells) == {(c, i) for c in _LEVELS for i in _LEVELS}
    assert all(1 <= p <= 5 for p in cells.values())
    # the vector, the generated installation data and the peer agree
    assert cells == _initial_insert_cells(profile)
    peer = _peer_results(doc)["cip"]
    assert {(c["criticality"], c["impact"]): c["priority_id"] for c in peer["cells"]} == cells
    # lookup edge cases (exact match, no fallback)
    assert expected["unknown_pair"] is None and peer["unknown_value"] is None
    assert expected["missing_argument"] is None and peer["missing_argument"] is None
    assert peer["empty_impact"] is None
    assert expected["case_sensitive_on_mariadb"] is True and peer["other_case_returns"] is None


def test_cip_matrix_is_monotone_in_both_axes(doc: dict[str, Any]) -> None:
    cells = {
        (c["criticality"], c["impact"]): c["priority_id"]
        for c in doc["questions"]["1"]["expected"]["cells"]
    }
    for impact in _LEVELS:
        row = [cells[(crit, impact)] for crit in _LEVELS]
        assert row == sorted(row), impact
    for crit in _LEVELS:
        column = [cells[(crit, impact)] for impact in _LEVELS]
        assert column == sorted(column), crit
    assert cells[("3 normal", "3 normal")] == 3
    assert cells[("1 very low", "1 very low")] == 1
    assert cells[("5 very high", "5 very high")] == 5


# --- 2: service incident state -----------------------------------------------------------------


def test_service_incident_vectors_have_expected_state(doc: dict[str, Any]) -> None:
    expected = doc["questions"]["2"]["expected"]
    scenarios = _peer_results(doc)["service_incident"]
    checks = expected["scenarios"]
    assert len(checks) >= 3
    assert {c["scenario"] for c in checks} >= {
        "s1_incident_ci",
        "s3_non_productive_ignored",
        "s6_child_service_makes_parent_warning",
    }
    for check in checks:
        got = _dig(scenarios[check["scenario"]], check["path"])
        assert got == check["value"], f"{check['scenario']}.{check['path']}: {got!r}"
    # ranking is incident > warning > operational and covers the observed values
    assert expected["ranking"] == ["operational", "warning", "incident"]
    assert {c["value"] for c in checks} <= set(expected["ranking"])
    assert expected["counted_link_types"] == {"DependsOn": "both"}
    assert _peer_results(doc)["incident_link_type_direction"] == {"DependsOn": "Both"}


def test_service_incident_scenarios_are_the_whole_peer_run(doc: dict[str, Any]) -> None:
    covered = {c["scenario"] for c in doc["questions"]["2"]["expected"]["scenarios"]}
    assert covered == set(_peer_results(doc)["service_incident"])


# --- 3 .. 10: facts the peer confirms ----------------------------------------------------------


@pytest.mark.parametrize("key", ["5", "6", "8", "9"])
def test_peer_checks_match_the_peer_results(doc: dict[str, Any], key: str) -> None:
    checks = doc["questions"][key]["expected"]["peer_checks"]
    assert checks, key
    results = _peer_results(doc)
    for check in checks:
        parts = check["path"].split(".")
        data: Any = results
        for part in parts:
            data = data[part]
        assert data == check["value"], f"{check['path']}: {data!r}"


def test_criticality_copy_and_priority_recalculation_are_core_form_logic(
    doc: dict[str, Any],
) -> None:
    copy = doc["questions"]["3"]
    assert copy["expected"]["mechanism"] == "core_frontend_modules"
    assert copy["expected"]["event_module"] is False
    assert "open question 9" in copy["resolves"]
    recalc = doc["questions"]["4"]["expected"]
    assert recalc["mechanism"] == "form_controller" and recalc["event_module"] is False
    assert recalc["trigger_elements"] == [
        "ServiceID",
        "DynamicField_ITSMImpact",
        "DynamicField_ITSMCriticality",
    ]
    assert recalc["server_enforcement"] is False


def test_ticket_ci_permission_decision_is_recorded(doc: dict[str, Any]) -> None:
    expected = doc["questions"]["6"]["expected"]
    assert expected["znuny_permission"]["dialog_object"] == "ro"
    assert expected["znuny_permission"]["link_target"] == "ro"
    assert expected["tiqora_decision"]["link_ticket_ci"] == {"ticket": "rw", "ci": "ro"}
    assert expected["tiqora_decision"]["unlink_ticket_ci"] == {"ticket": "rw", "ci": "ro"}
    assert "open question 4" in doc["questions"]["6"]["resolves"]
    assert "R12 (ticket-CI permission)" in doc["questions"]["6"]["resolves"]


def test_link_type_defaults_follow_the_survey(doc: dict[str, Any]) -> None:
    """Task 3 corrects ``link_type_defaults`` from the vectors (ruling: Tasks 3, 4, 6, 7, ...)."""
    results = _peer_results(doc)
    types = results["link_types"]["types"]
    by_name = {d.name: d for d in DEFAULT_LINK_TYPES}
    assert set(by_name) == {n for n in types if n not in ("Normal", "ParentChild")}
    for name, default in by_name.items():
        assert default.pointed == bool(types[name]["pointed"]), name
        assert types[name]["pointed"] == int(
            types[name]["source_name"] != types[name]["target_name"]
        )
    possible = results["link_types"]["possible"]
    assert {n for n, d in by_name.items() if d.offer_ticket_ci} == set(
        possible["Ticket/ITSMConfigItem"]
    )
    assert {n for n, d in by_name.items() if d.offer_ci_ci} == set(
        possible["ITSMConfigItem/ITSMConfigItem"]
    )
    assert {n for n, d in by_name.items() if d.offer_ci_service} == set(
        possible["ITSMConfigItem/Service"]
    )
    assert not any(d.offer_ticket_ticket for d in DEFAULT_LINK_TYPES)
    assert set(possible["Ticket/Ticket"]) == {"Normal", "ParentChild"}
    counted = doc["questions"]["2"]["expected"]["counted_link_types"]
    assert {n: d.propagation for n, d in by_name.items() if d.propagation != "none"} == counted


# --- 7: definitions ----------------------------------------------------------------------------


def _prepare(item: dict[str, Any]) -> dict[str, Any]:
    """Reference implementation of ``_DefinitionPrepare`` as described by the vector."""
    out: dict[str, Any] = {}
    count_min = item.get("CountMin", 1)
    count_max = item.get("CountMax") or 1
    count_min = min(count_min, count_max)
    count_default = item.get("CountDefault", 1)
    count_default = max(count_default, count_min)
    count_default = min(count_default, count_max)
    out.update(CountMin=count_min, CountMax=count_max, CountDefault=count_default)
    sub = item.get("Sub")
    out["has_sub"] = int(isinstance(sub, list))
    if isinstance(sub, list) and sub:
        child = _prepare(sub[0])
        out["sub_child"] = {k: child[k] for k in ("CountMin", "CountMax", "CountDefault")}
    return out


def test_definition_rules_match_the_peer(doc: dict[str, Any]) -> None:
    expected = doc["questions"]["7"]["expected"]
    peer = _peer_results(doc)["definition_rules"]
    assert expected["format"] == "yaml"
    assert len(expected["input_types"]) == 20
    assert sorted([*expected["input_types"], "BaseSelectable"]) == peer["input_type_modules"]
    for example in expected["prepare_examples"]:
        assert _prepare(example["in"]) == example["out"] == peer["prepare"][example["name"]], (
            example["name"]
        )
    assert {e["name"] for e in expected["prepare_examples"]} == set(peer["prepare"])
    assert expected["check_examples"] == {k: bool(v) for k, v in peer["check"].items()}
    assert expected["check_examples"]["valid_yaml"] is True
    assert expected["check_examples"]["legacy_perl"] is False


def test_shipped_definitions_use_only_known_input_types(doc: dict[str, Any], profile: str) -> None:
    text = (FIXTURES / profile / "initial_insert.sql").read_text(encoding="utf-8")
    used = set(re.findall(r"^\s+Type: (\w+)$", text, re.M))
    expected = doc["questions"]["7"]["expected"]
    assert set(expected["shipped_definitions_use"]) <= used
    assert used - {"Text"} >= set(expected["shipped_definitions_use"]) - {"Text"}
    assert used & set(expected["input_types"]) >= set(expected["shipped_definitions_use"])


# --- 8 / 10 ------------------------------------------------------------------------------------


def test_major_incident_type_name_is_the_same_on_both_lines() -> None:
    names = {p: _load(p)["questions"]["8"]["expected"]["major_incident_type"] for p in PROFILES}
    assert names == {"itsm-6": "Incident::Major", "itsm-7": "Incident::Major"}
    for profile in PROFILES:
        text = (FIXTURES / profile / "initial_insert.sql").read_text(encoding="utf-8")
        assert re.search(r"INSERT INTO ticket_type \(.*?\) VALUES \(\d+, 'Incident::Major',", text)


def test_deep_link_patterns_are_the_same_on_both_lines() -> None:
    patterns = {p: _load(p)["questions"]["10"]["expected"] for p in PROFILES}
    assert patterns["itsm-6"] == patterns["itsm-7"]
    zoom = patterns["itsm-6"]["zoom"]
    assert zoom == "Action=AgentITSMConfigItemZoom;ConfigItemID={id}"
    for profile in PROFILES:
        sysconfig = json.loads((FIXTURES / profile / "sysconfig.json").read_text(encoding="utf-8"))
        assert any(k.startswith("Frontend::Module###AgentITSMConfigItemZoom") for k in sysconfig)
        assert any(k.startswith("Frontend::Module###AgentITSMConfigItemEdit") for k in sysconfig)


def test_both_profiles_behave_identically() -> None:
    a, b = (_peer_results(_load(p)) for p in PROFILES)
    for section in (
        "cip",
        "service_incident",
        "permissions",
        "link_types",
        "definition_rules",
        "ticket_types",
        "closed_with_workaround",
        "incident_link_type_direction",
    ):
        assert a[section] == b[section], section
