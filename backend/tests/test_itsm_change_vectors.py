"""Znuny ITSMChangeManagement survey vectors (wave-5 itsm-change, Task 1). Pure: JSON + fixtures.

``fixtures/znuny-itsm/behaviour/change-<profile>.json`` pins, per profile, nine answers about how
the ITSMChangeManagement package stores and derives its data (``questions``: ``source`` file:line
references, ``answer``, ``expected`` values the later Tasks assert against, ``consequence`` for
Tiqora) next to what the real peer stored and answered (``peer.results``, written by
``tests/golden/itsm_survey.py --change-behaviour``, never edited). The tests keep both halves
honest against each other and against the generated schema fixtures.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests._itsm_fixtures import CHANGE_TIME_SENTINEL, znuny_change_times

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FIXTURES = BACKEND / "tests" / "fixtures" / "znuny-itsm"
BEHAVIOUR = FIXTURES / "behaviour"
PACKAGE_SOURCES = REPO / "tests" / "golden" / "itsm-packages" / "src"
PROFILES = ("itsm-6", "itsm-7")
QUESTION_KEYS = [str(n) for n in range(1, 10)]
CHANGE_TABLES = [
    "change_cab",
    "change_cip_allocate",
    "change_condition",
    "change_history",
    "change_history_type",
    "change_item",
    "change_notification",
    "change_notification_grps",
    "change_notification_message",
    "change_notification_rec",
    "change_number_counter",
    "change_state_machine",
    "change_template",
    "change_template_type",
    "change_workorder",
    "condition_action",
    "condition_attribute",
    "condition_expression",
    "condition_object",
    "condition_operator",
]

_SOURCE = re.compile(
    r"^(?P<path>[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+):(?P<first>\d+)(?:-(?P<last>\d+))?$"
)


def _load(profile: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (BEHAVIOUR / f"change-{profile}.json").read_text(encoding="utf-8")
    )
    return data


@pytest.fixture(params=PROFILES)
def profile(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def doc(profile: str) -> dict[str, Any]:
    return _load(profile)


def _results(doc: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = doc["peer"]["results"]
    return results


def _rows(doc: dict[str, Any], table: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = _results(doc)["stored"]["rows"][table]
    return rows


# --- structure ---------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_change_vectors_cover_every_question(profile: str) -> None:
    questions = _load(profile)["questions"]
    assert set(QUESTION_KEYS) <= set(questions)
    for key in QUESTION_KEYS:
        q = questions[key]
        assert q["title"].strip(), key
        assert isinstance(q["answer"], str) and len(q["answer"]) > 80, key
        assert q["expected"] not in (None, "", [], {}), key
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


def test_change_vectors_record_the_peer(doc: dict[str, Any], profile: str) -> None:
    peer = doc["peer"]
    assert doc["profile"] == profile
    assert peer["peer"] == {"itsm-6": "znuny-6.5-itsm", "itsm-7": "znuny-7.3-itsm"}[profile]
    assert peer["znuny_version"].startswith({"itsm-6": "6.5.", "itsm-7": "7.3."}[profile])
    assert "itsm_survey.py --change-behaviour" in peer["captured_with"]
    assert (
        doc["packages"]["change"]
        == {
            "itsm-6": "ITSMChangeManagement-6.5.2",
            "itsm-7": "ITSMChangeManagement-7.3.4",
        }[profile]
    )


def test_change_vectors_have_no_real_data(profile: str) -> None:
    text = (BEHAVIOUR / f"change-{profile}.json").read_text(encoding="utf-8")
    emails = re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text)
    assert emails, "the customer user of the CAB is part of the vector"
    assert all(host.endswith(("example.org", "example.com", ".test")) for host in emails), emails
    ips = re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", text)
    assert all(ip.startswith(("192.0.2.", "198.51.100.", "203.0.113.")) for ip in ips), ips
    # notification rows keep the event, never the recipient
    history = _results(_load(profile))["probe"]["history"]
    sent = [r for rows in history.values() for r in rows if r["type"].endswith("NotificationSent")]
    assert sent and all(r["content_new"].startswith("<recipient>%%") for r in sent)


def test_change_source_references_point_into_existing_lines(doc: dict[str, Any]) -> None:
    """Fixture files are always checked; package sources when the (git-ignored) tree is present."""
    checked_fixture = checked_package = 0
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
                if rel.split("/", 1)[0].startswith("znuny-"):
                    continue  # the Znuny core tree is not part of the repository
                path = PACKAGE_SOURCES / rel
                if not path.is_file():
                    continue  # package sources are downloaded on demand (just golden-itsm-fetch)
                checked_package += 1
            lines = len(path.read_text(encoding="utf-8").splitlines())
            assert int(match["last"] or match["first"]) <= lines, f"question {key}: {ref}"
    assert checked_fixture >= 3


# --- 9: the DDL is the same in both profiles ---------------------------------------------------

_CREATE = re.compile(r"CREATE TABLE (?P<name>\w+) \((?P<body>.*?)\n\);", re.S)
_RANDOM_INDEX = re.compile(r"\b(\w+)_U_\d+\b")


def _blocks(profile: str, dialect: str) -> dict[str, str]:
    text = (FIXTURES / profile / f"schema.{dialect}.sql").read_text(encoding="utf-8")
    blocks = {m["name"]: _RANDOM_INDEX.sub(r"\1_U_n", m["body"]) for m in _CREATE.finditer(text)}
    return {t: blocks[t] for t in CHANGE_TABLES}


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_change_ddl_identical_across_profiles(dialect: str) -> None:
    six, seven = _blocks("itsm-6", dialect), _blocks("itsm-7", dialect)
    assert sorted(six) == sorted(seven) == sorted(CHANGE_TABLES)
    for table in CHANGE_TABLES:
        assert six[table] == seven[table], table


def test_change_vector_lists_the_twenty_tables(doc: dict[str, Any]) -> None:
    expected = doc["questions"]["9"]["expected"]
    assert expected["tables"] == CHANGE_TABLES
    assert expected["identical_across_profiles"] is True
    assert set(_results(doc)["stored"]["counts"]) == set(CHANGE_TABLES)


# --- peer checks -------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["1", "2", "4", "5", "6", "7", "8"])
def test_change_peer_checks_match_the_peer_results(doc: dict[str, Any], key: str) -> None:
    checks = doc["questions"][key]["expected"]["peer_checks"]
    assert checks, key
    results = _results(doc)
    for check in checks:
        data: Any = results
        for part in check["path"].split("."):
            data = data[int(part)] if isinstance(data, list) else data[part]
        assert data == check["value"], f"{check['path']}: {data!r}"


# --- 1: link objects ---------------------------------------------------------------------------


def test_change_links_use_the_two_objects_and_never_link_a_change_to_a_ci(
    doc: dict[str, Any],
) -> None:
    expected = doc["questions"]["1"]["expected"]
    assert expected["link_objects"] == {"change": "ITSMChange", "workorder": "ITSMWorkOrder"}
    probe = _results(doc)["probe"]["links"]
    assert probe["possible_types"] == expected["possible_types"]
    assert probe["change_to_ci_link_add_succeeded"] == 0
    assert expected["change_to_ci_link_possible"] is False
    stored = _rows(doc, "link_relation")
    pairs = {(r["source_object"], r["target_object"], r["link_type"]) for r in stored}
    assert pairs == {
        ("ITSMChange", "Ticket", "Normal"),
        ("ITSMWorkOrder", "Ticket", "Normal"),
        ("ITSMWorkOrder", "ITSMConfigItem", "DependsOn"),
        ("ITSMWorkOrder", "Service", "Normal"),
    }
    # every stored pair is one the package allows
    for source, target, link_type in pairs:
        assert link_type in expected["possible_types"][f"{source}/{target}"]
    assert all(r["link_state"] == "Valid" for r in stored)


# --- 2: derived times --------------------------------------------------------------------------


def _dt(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007 - naive UTC


def _stored_workorders(doc: dict[str, Any], change_id: int) -> list[dict[str, datetime | None]]:
    def cell(value: str) -> datetime | None:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007 - naive UTC
        return None if parsed == CHANGE_TIME_SENTINEL else parsed

    return [
        {
            "planned_start": cell(r["planned_start_time"]),
            "planned_end": cell(r["planned_end_time"]),
            "actual_start": cell(r["actual_start_time"]),
            "actual_end": cell(r["actual_end_time"]),
        }
        for r in _rows(doc, "change_workorder")
        if r["change_id"] == change_id
    ]


def test_change_time_derivation_reference_matches_the_peer(doc: dict[str, Any]) -> None:
    """``znuny_change_times`` (the reference of Task 18/19) reproduces what ChangeGet answered."""
    expected = doc["questions"]["2"]["expected"]
    seed = _results(doc)["seed"]["changes"]
    probe = _results(doc)["probe"]["times"]
    assert {c["change"] for c in expected["cases"]} == set(seed) == set(probe)
    for case in expected["cases"]:
        derived = znuny_change_times(_stored_workorders(doc, seed[case["change"]]["id"]))
        peer = probe[case["change"]]
        for field in ("planned_start", "planned_end", "actual_start", "actual_end"):
            assert derived[field] == _dt(peer[field]) == _dt(case[field]), (case["change"], field)


def test_change_time_sentinel_dominates_the_max_columns(doc: dict[str, Any]) -> None:
    """The plan assumed 'min/max ignoring the sentinel'; the MAX columns are blanked instead."""
    expected = doc["questions"]["2"]["expected"]
    assert expected["sentinel"] == "9999-01-01 00:00:00"
    mail = _results(doc)["probe"]["times"]["mail"]
    assert mail["raw_min_max"]["max_planned_end"] == expected["sentinel"]
    assert mail["planned_end"] == "" and mail["planned_start"] == ""
    assert mail["actual_start"] == "2026-11-02 08:05:00"  # the MIN column ignores the sentinel
    # change_item has no time column besides requested_time, which is NULL (not the sentinel)
    items = _rows(doc, "change_item")
    assert items[0]["requested_time"] == "2026-11-01 00:00:00"
    assert items[1]["requested_time"] is None
    # one work order without any time exists: Znuny stores the sentinel, not NULL
    bare = next(r for r in _rows(doc, "change_workorder") if r["title"] == "chg94 review mail")
    assert {bare[k] for k in ("planned_start_time", "planned_end_time", "actual_start_time")} == {
        expected["sentinel"]
    }


# --- 3: history --------------------------------------------------------------------------------


def test_change_history_types_are_the_installed_ones(doc: dict[str, Any], profile: str) -> None:
    expected = doc["questions"]["3"]["expected"]
    names = [r["name"] for r in _rows(doc, "change_history_type")]
    assert names == expected["history_types"] and len(names) == 42
    inserted = re.findall(
        r"INSERT INTO change_history_type \(.*?\) VALUES \(\d+, '(\w+)',",
        (FIXTURES / profile / "initial_insert.sql").read_text(encoding="utf-8"),
    )
    assert inserted == names
    assert [r["id"] for r in _rows(doc, "change_history_type")] == list(range(1, 43))


def test_change_history_rows_follow_the_documented_shapes(doc: dict[str, Any]) -> None:
    expected = doc["questions"]["3"]["expected"]
    by_type: dict[str, set[str | None]] = {}
    for row in _rows(doc, "change_history_by_type"):
        by_type.setdefault(row["type"], set()).add(row["fieldname"])
    assert set(by_type) <= set(expected["history_types"])
    assert by_type["ChangeAdd"] == {None} and by_type["WorkOrderAdd"] == {None}
    assert by_type["ChangeCABUpdate"] == {"CABAgents", "CABCustomers"}
    assert by_type["ChangeUpdate"] == set(expected["update_fieldnames_observed"])
    assert by_type["WorkOrderUpdate"] == set(expected["workorder_update_fieldnames_observed"])
    assert not set(expected["untracked_fields"]) & (
        by_type["ChangeUpdate"] | by_type["WorkOrderUpdate"]
    )
    history = _results(doc)["probe"]["history"]
    entries = history["mail"]
    state = [e for e in entries if e["fieldname"] == "ChangeState"]
    assert [(e["content_old"], e["content_new"]) for e in state] == [("requested", "in progress")]
    link = [e for e in entries if e["type"] == "ChangeLinkAdd"]
    assert [e["content_new"].partition("%%")[0] for e in link] == ["Ticket"]
    cab = {e["fieldname"]: e["content_new"] for e in entries if e["type"] == "ChangeCABUpdate"}
    assert cab["CABCustomers"] == "chg94-cust@example.org"
    work_order_rows = [e for e in entries if e["type"].startswith("WorkOrder")]
    assert work_order_rows and all(e["workorder_id"] for e in work_order_rows)
    assert all(e["workorder_id"] is None for e in entries if e["type"].startswith("Change"))


# --- 4 / 5 / 7: dynamic fields, CAB, template --------------------------------------------------


def test_change_dynamic_field_values_use_the_object_ids(doc: dict[str, Any]) -> None:
    expected = doc["questions"]["4"]["expected"]
    assert expected["object_types"] == ["ITSMChange", "ITSMWorkOrder"]
    stored = _rows(doc, "dynamic_field_value")
    assert stored == expected["observed"]
    seed = _results(doc)["seed"]["changes"]
    assert stored[0]["object_id"] == seed["mail"]["id"]


def test_change_cab_rows_hold_an_agent_or_a_customer(doc: dict[str, Any]) -> None:
    rows = _rows(doc, "change_cab")
    assert rows
    for row in rows:
        assert (row["user_id"] is None) != (row["customer_user_id"] is None), row
    mail = [r for r in rows if r["change_id"] == _results(doc)["seed"]["changes"]["mail"]["id"]]
    assert sorted((r["user_id"] is None) for r in mail) == [False, True]
    assert {r["customer_user_id"] for r in mail} == {None, "chg94-cust@example.org"}


def test_change_template_content_is_perl_data_dumper_text(doc: dict[str, Any]) -> None:
    expected = doc["questions"]["7"]["expected"]
    template = _rows(doc, "change_template")[0]
    assert template["content_prefix"].startswith(expected["prefix"])
    assert not template["content_prefix"].startswith("pst0")  # not Storable
    assert template["content_length"] > 0
    types = {r["id"]: r["name"] for r in _rows(doc, "change_template_type")}
    assert [types[i] for i in sorted(types)] == expected["template_types"]
    assert types[template["type_id"]] == "ITSMChange"


# --- 6: plain text -----------------------------------------------------------------------------


def test_change_plain_columns_follow_the_lines_to_ascii(doc: dict[str, Any], profile: str) -> None:
    items = {r["title"]: r for r in _rows(doc, "change_item")}
    mail = items["chg94 Mail server replacement"]
    cases = {c["html"]: c["plain"] for c in doc["questions"]["6"]["expected"]["cases"]}
    assert mail["description"] in cases and mail["description_plain"] == cases[mail["description"]]
    assert mail["justification_plain"].strip() == "Old hardware out of support"
    # the lines differ in the whitespace around a block element: consumers must strip
    wrapped = mail["description_plain"].startswith("\n")
    assert wrapped is (profile == "itsm-6")
    assert mail["description_plain"].strip() == "Replace the mail server mail01.example.org"
    firmware = items["chg94 Switch firmware rollout"]
    assert firmware["description"] == firmware["description_plain"]  # no markup, unchanged


# --- 8: groups ---------------------------------------------------------------------------------


def test_change_screens_are_gated_by_the_three_groups(doc: dict[str, Any]) -> None:
    expected = doc["questions"]["8"]["expected"]
    groups = set(expected["groups"])
    assert groups == {"itsm-change", "itsm-change-builder", "itsm-change-manager"}
    for screen, needed in expected["screens_group_ro"].items():
        assert needed and set(needed) <= groups, screen
    assert set(expected["permission_checks"].values()) == groups
    assert all(g == ["admin"] for g in expected["admin_screens"].values())
    # the manager creates and deletes, the builder resets, everyone in itsm-change reads and edits
    screens = expected["screens_group_ro"]
    assert screens["AgentITSMChangeAdd"] == ["itsm-change-manager"]
    assert screens["AgentITSMChangeReset"] == ["itsm-change-builder"]
    assert screens["AgentITSMChangeZoom"] == ["itsm-change"]


def test_change_screen_groups_match_the_package_xml(doc: dict[str, Any], profile: str) -> None:
    """The pinned screen table is read from ``Frontend::Module###`` when the sources are there."""
    import xml.etree.ElementTree as ET  # noqa: PLC0415 - only when the sources exist

    package = doc["packages"]["change"]
    path = (
        PACKAGE_SOURCES
        / package
        / "Kernel"
        / "Config"
        / "Files"
        / "XML"
        / "ITSMChangeManagement.xml"
    )
    if not path.is_file():
        pytest.skip("package sources not fetched (just golden-itsm-fetch)")
    screens: dict[str, list[str]] = {}
    for setting in ET.parse(path).getroot().iter("Setting"):  # noqa: S314 - package source
        name = setting.get("Name", "")
        if not name.startswith("Frontend::Module###"):
            continue
        for item in setting.iter("Item"):
            if item.get("Key") == "GroupRo":
                screens[name.split("###", 1)[1]] = [
                    i.text or "" for i in item.iter("Item") if i is not item
                ]
    expected = doc["questions"]["8"]["expected"]["screens_group_ro"]
    for screen, groups in expected.items():
        assert screens[screen] == groups, screen
