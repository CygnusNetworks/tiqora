"""Legacy (Znuny ITSMChangeManagement) change reader and the pure mapping rules (Task 18).

Pure tests replay the Task 1 vectors (``behaviour/change-itsm-*.json``). DB tests run on both
dialects x both profiles (``itsm_db_url``) in a session that is rolled back, so the strict leak
check has nothing to find. The seeded world and its band (946060-946098) are in
``tests/_itsm_legacy_helpers.py``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest

from tests._itsm_adapter_helpers import itsm_session
from tests._itsm_db import ITSM_FIXTURES, ITSM_PARAMS
from tests._itsm_fixtures import CHANGE_TIME_SENTINEL, znuny_change_times
from tests._itsm_legacy_helpers import (
    AGENT,
    CUSTOMER,
    FIRMWARE,
    GONE_CI,
    MAIL,
    MANAGER,
    MEMBER,
    SERVICE,
    TICKET_A,
    TICKET_B,
    UNKNOWN_STATE_ID,
    UPTIME,
    WO_INSTALL,
    at,
    seed_legacy_world,
)
from tiqora.itsm.change.legacy import reader
from tiqora.itsm.change.legacy.mapping import (
    SENTINEL,
    change_window,
    dynamic_field_text,
    history_display,
    history_ids,
    plain_text,
    znuny_time,
)
from tiqora.itsm.change.legacy.model import LegacyLink
from tiqora.reporting.sql import ReportQueryTimeout

PROFILES = ("itsm-6", "itsm-7")
WINDOW_KEYS = ("planned_start", "planned_end", "actual_start", "actual_end")


def _vectors(profile: str) -> dict[str, Any]:
    path = ITSM_FIXTURES / "behaviour" / f"change-{profile}.json"
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _when(raw: str) -> datetime | None:
    return None if not raw else datetime.fromisoformat(raw)


WINDOW_KEYS = ("planned_start", "planned_end", "actual_start", "actual_end")


# --- pure -----------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_mapping_sentinels_and_plain_text(profile: str) -> None:
    assert znuny_time(None) is None
    assert znuny_time(SENTINEL) is None
    assert SENTINEL == CHANGE_TIME_SENTINEL
    assert znuny_time(SENTINEL.replace(microsecond=5)) is None
    assert znuny_time(at(2, 8, 5)) == at(2, 8, 5)

    # survey Q6: the *_plain column is ToAscii of the text; the lines differ in their newlines
    cases = _vectors(profile)["questions"]["6"]["expected"]["cases"]
    assert cases
    for case in cases:
        assert plain_text(case["plain"], case["html"]) == case["plain"].strip()
    assert plain_text("\ndone\n\n") == "done"  # 6.5
    assert plain_text("done\n") == "done"  # 7.3
    # only an empty plain column falls back to the text, tags dropped, never rendered
    assert plain_text(None, "<p>Tom &amp; <b>Jerry</b></p><p>next</p>") == "Tom & Jerry\nnext"
    assert plain_text("   ", "<script>x</script>") == "x"
    assert plain_text("", None) is None and plain_text(None) is None

    assert dynamic_field_text("chg94", None, None) == "chg94"
    assert dynamic_field_text(None, datetime(2026, 11, 2, 8, 0, 0), None) == "2026-11-02 08:00:00"
    assert dynamic_field_text(None, None, 1) == "1"
    assert dynamic_field_text(None, None, None) is None


def test_history_display_resolves_the_id_fields() -> None:
    catalog = {7: "closed", 8: "4 high"}
    logins = {5: "chg94.a", 6: "chg94.b"}
    assert history_ids("WorkOrderStateID", "7") == ({7}, set())
    assert history_ids("ImpactID", "8") == ({8}, set())
    assert history_ids("WorkOrderAgentID", "5") == (set(), {5})
    assert history_ids("CABAgents", "5%%6%%x") == (set(), {5, 6})
    assert history_ids("ChangeTitle", "7") == (set(), set())
    assert history_ids("CategoryID", "") == (set(), set())
    assert history_display("WorkOrderStateID", "7", catalog, logins) == "closed"
    assert history_display("PriorityID", "8", catalog, logins) == "4 high"
    assert history_display("ChangeBuilderID", "6", catalog, logins) == "chg94.b"
    assert history_display("CABAgents", "5%%6", catalog, logins) == "chg94.a, chg94.b"
    assert history_display("CABCustomers", "a@example.org%%b@example.org", catalog, logins) == (
        "a@example.org, b@example.org"
    )
    # unknown or unresolvable values stay raw; "" means "was unset"
    assert history_display("CategoryID", "99", catalog, logins) == "99"
    assert history_display("ChangeManagerID", "99", catalog, logins) == "99"
    assert history_display("CABAgents", "5%%99", catalog, logins) == "chg94.a, 99"
    assert history_display("ChangeManagerID", "", catalog, logins) == ""
    assert history_display("ChangeTitle", "7", catalog, logins) == "7"
    assert history_display(None, None, catalog, logins) is None


@pytest.mark.parametrize("profile", PROFILES)
def test_planned_window_derived_from_workorders(profile: str) -> None:
    """Task 1 vector 2: the change window from the peer's work orders equals Znuny's."""
    doc = _vectors(profile)
    peer = doc["peer"]["results"]["probe"]["times"]
    expected = {c["change"]: c for c in doc["questions"]["2"]["expected"]["cases"]}
    assert set(expected) == {"mail", "firmware", "database"}
    for name, case in expected.items():
        orders = peer[name]["workorders"]
        times = [
            (
                _when(o["planned_start"]),
                _when(o["planned_end"]),
                _when(o["actual_start"]),
                _when(o["actual_end"]),
            )
            for o in orders
        ]
        got = change_window(times)
        assert got == tuple(_when(case[k]) for k in WINDOW_KEYS), name
        reference = znuny_change_times(
            [
                dict(
                    zip(
                        ("planned_start", "planned_end", "actual_start", "actual_end"),
                        t,
                        strict=True,
                    )
                )
                for t in times
            ]
        )
        assert got == tuple(reference.values()), name
        # the sentinel itself is accepted for "not set" as well
        raw = [tuple(SENTINEL if v is None else v for v in t) for t in times]
        assert change_window(raw) == got, name  # type: ignore[arg-type]
    # mail: one work order without a planned end blanks the change's planned end (S2)
    assert expected["mail"]["planned_end"] == "" and expected["mail"]["actual_start"]
    assert change_window([]) == (None, None, None, None)


# --- DB ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_read_changes_full(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        world = await seed_legacy_world(session, profile)
        assert await reader.change_tables_present(session) is True

        got = await reader.read_changes(session, [FIRMWARE, 946099, MAIL])
        assert [c.znuny_change_id for c in got] == [
            FIRMWARE,
            MAIL,
        ]  # order of ids, missing left out
        firmware, mail = got

        assert (mail.number, mail.title) == ("2026110194000070", "chg94 Mail relay replacement")
        assert (mail.state, mail.category, mail.impact, mail.priority) == (
            "in progress",
            "3 normal",
            "4 high",
            "4 high",
        )
        assert (mail.manager_login, mail.builder_login) == ("chg94.lmanager", "chg94.lagent")
        assert mail.requested_time == at(1, 12)
        assert (mail.created_at, mail.changed_at) == (at(1, 8), at(3, 8))
        # S3: the plain column, stripped on both lines; never the HTML
        assert mail.description == "Replace the relay mail01.example.org"
        assert mail.justification == "End of support"

        # S2: the pir has no times -> no planned start/end; the backout is unfinished -> no
        # actual end; the install started -> actual start
        assert (mail.planned_start, mail.planned_end) == (None, None)
        assert (mail.actual_start, mail.actual_end) == (at(2, 8, 5), None)

        install, backout, pir = mail.workorders
        assert (install.number, install.title, install.work_order_type, install.state) == (
            1,
            "chg94 install",
            "workorder",
            "closed",
        )
        assert install.agent_login == "chg94.lagent"
        assert (install.planned_start, install.planned_end) == (at(2, 8), at(2, 12))
        assert (install.actual_start, install.actual_end) == (at(2, 8, 5), at(2, 12, 30))
        assert (install.planned_effort, install.accounted_time) == (4.0, 4.5)
        assert (install.instruction, install.report) == ("Install the relay", "done")
        assert install.dynamic_fields == {"chg94LWorkOrderRef": ("WO-94-1",)}
        assert (backout.work_order_type, backout.agent_login, backout.actual_start) == (
            "backout",
            None,
            None,
        )
        assert backout.dynamic_fields == {}
        assert (pir.work_order_type, pir.planned_start, pir.planned_end) == ("pir", None, None)

        # CAB: agents (user id order) then the customer user
        assert mail.cab == (
            ("user", "chg94.lmanager"),
            ("user", "chg94.lagent"),
            ("customer_user", CUSTOMER),
        )
        # displayed like Znuny's history screen: catalog ids as names, user ids as logins (also a
        # former manager who appears nowhere else), CAB lists comma-separated; unknown -> raw
        shown = [
            (h.history_type, h.field, h.old_display, h.new_display, h.workorder_number)
            for h in mail.history
        ]
        assert shown == [
            ("ChangeAdd", None, None, str(MAIL), 0),
            ("ChangeUpdate", "ChangeTitle", "chg94 Mail relay", "chg94 Mail relay replacement", 0),
            ("WorkOrderAdd", None, None, str(WO_INSTALL), 1),
            ("WorkOrderUpdate", "WorkOrderStateID", "created", "closed", 1),
            ("ChangeUpdate", "ChangeManagerID", "chg94.lmember", "chg94.lmanager", 0),
            ("ChangeCABUpdate", "CABAgents", "", "chg94.lagent, chg94.lmanager", 0),
            ("ChangeCABUpdate", "CABCustomers", "", CUSTOMER, 0),
            ("ChangeUpdate", "ChangeStateID", "requested", str(UNKNOWN_STATE_ID), 0),
        ]
        # the raw values stay as Znuny stores them (Task 28 compares them with HistoryGet)
        raw = {h.field: (h.old, h.new) for h in mail.history if h.field}
        assert raw["ChangeManagerID"] == (str(MEMBER), str(MANAGER))
        assert raw["CABAgents"] == ("", f"{AGENT}%%{MANAGER}")
        assert raw["CABCustomers"] == ("", CUSTOMER)
        assert all(v.isdigit() for v in raw["WorkOrderStateID"])
        assert raw["ChangeStateID"][1] == str(UNKNOWN_STATE_ID)
        assert raw["ChangeTitle"] == ("chg94 Mail relay", "chg94 Mail relay replacement")
        assert [h.user_login for h in mail.history][:3] == [
            "chg94.lagent",
            "chg94.lmanager",
            "chg94.lagent",
        ]
        assert mail.history[0].created_at == at(1, 8)
        # only valid fields of the change's own object type, all values in row order
        assert mail.dynamic_fields == {"chg94LChangeRef": ("CHG-94-MAIL", "CHG-94-MAIL-2")}
        # S1: CIs and the service hang off work orders; the temporary link is not read
        expected_links = [
            LegacyLink("Ticket", str(TICKET_A), "Normal", "source", 0),
            LegacyLink("Ticket", str(TICKET_B), "Normal", "target", 0),
            LegacyLink("ITSMConfigItem", str(world.ci_visible), "DependsOn", "source", 1),
            LegacyLink("ITSMConfigItem", str(world.ci_hidden), "DependsOn", "source", 1),
            LegacyLink("ITSMConfigItem", str(GONE_CI), "Normal", "source", 1),
            LegacyLink("ITSMConfigItem", str(GONE_CI), "Normal", "source", 2),
            LegacyLink("Service", str(SERVICE), "Normal", "source", 2),
            LegacyLink("Ticket", str(TICKET_B), "Normal", "source", 2),
        ]
        assert sorted(mail.links, key=repr) == sorted(expected_links, key=repr)
        # stable order: work order number, object, key
        assert [(li.workorder_number, li.object) for li in mail.links][-3:] == [
            (2, "ITSMConfigItem"),
            (2, "Service"),
            (2, "Ticket"),
        ]
        assert [li.workorder_number for li in mail.links][:2] == [0, 0]

        assert (firmware.state, firmware.manager_login, firmware.description) == (
            "requested",
            None,
            "Plain text description",
        )
        assert (firmware.workorders, firmware.cab, firmware.history, firmware.links) == (
            (),
            (),
            (),
            (),
        )
        assert firmware.dynamic_fields == {}
        assert (firmware.planned_start, firmware.actual_start) == (None, None)

        assert await reader.read_changes(session, []) == []
        with pytest.raises(ValueError):
            await reader.read_changes(session, list(range(1, reader.MAX_READ + 2)))
        assert await reader.iter_change_ids(session, after_id=MAIL - 1, limit=2) == [MAIL, FIRMWARE]
        assert await reader.iter_change_ids(session, after_id=FIRMWARE, limit=5) == [UPTIME]


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_search_by_text_ticket_ci(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, dialect: str, profile: str
) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        world = await seed_legacy_world(session, profile)

        async def ids(**kw: Any) -> tuple[list[int], int]:
            params: dict[str, Any] = {"text": None, "ticket_id": None, "ci_znuny_id": None}
            params.update(kw)
            items, total = await reader.search(
                session, limit=params.pop("limit", 25), offset=params.pop("offset", 0), **params
            )
            return [i.znuny_change_id for i in items], total

        # newest change first
        assert await ids() == ([UPTIME, FIRMWARE, MAIL], 3)
        assert await ids(limit=1, offset=1) == ([FIRMWARE], 3)
        # number, title, description, justification; case-insensitive on both dialects
        assert await ids(text="MAIL RELAY") == ([MAIL], 1)
        assert await ids(text="2026110194000071") == ([FIRMWARE], 1)
        assert await ids(text="plain text DESCRIPTION") == ([FIRMWARE], 1)
        assert await ids(text="end of support") == ([MAIL], 1)
        # % and _ are literal (F36)
        assert await ids(text="100%") == ([UPTIME], 1)
        assert await ids(text="chg94_") == ([], 0)
        assert await ids(text="%") == ([UPTIME], 1)
        # a ticket: linked to the change (either direction) or to one of its work orders
        assert await ids(ticket_id=TICKET_A) == ([MAIL], 1)
        assert await ids(ticket_id=TICKET_B) == ([UPTIME, MAIL], 2)
        assert await ids(ticket_id=TICKET_B, text="uptime") == ([UPTIME], 1)
        assert await ids(ticket_id=TICKET_B, limit=1, offset=1) == ([MAIL], 2)
        assert await ids(ticket_id=946099) == ([], 0)  # the temporary link does not count
        # a CI: through the work orders (S1)
        assert await ids(ci_znuny_id=world.ci_hidden) == ([MAIL], 1)
        assert await ids(ci_znuny_id=world.ci_visible, ticket_id=TICKET_A) == ([MAIL], 1)
        # both filters: the intersection (UPTIME is linked to TICKET_B but to no CI)
        assert await ids(ci_znuny_id=world.ci_visible, ticket_id=TICKET_B) == ([MAIL], 1)
        assert await ids(ci_znuny_id=GONE_CI) == ([MAIL], 1)  # the link outlives the CI
        assert await ids(ci_znuny_id=946093) == ([], 0)

        items, _ = await reader.search(
            session, text="relay", ticket_id=None, ci_znuny_id=None, limit=10, offset=0
        )
        (summary,) = items
        assert (summary.number, summary.title, summary.state) == (
            "2026110194000070",
            "chg94 Mail relay replacement",
            "in progress",
        )
        assert (summary.planned_start, summary.planned_end, summary.workorder_count) == (
            None,
            None,
            3,
        )
        assert summary.changed_at == at(3, 8)
        (uptime,), _ = await reader.search(
            session, text="uptime", ticket_id=None, ci_znuny_id=None, limit=10, offset=0
        )
        assert (uptime.planned_start, uptime.planned_end) == (at(9, 18), at(9, 20))
        # the page size is capped at MAX_PAGE (lowered here so three rows exceed it); the total
        # still counts every match, so the caller sees that more exist
        monkeypatch.setattr(reader, "MAX_PAGE", 2)
        assert await ids(limit=1000) == ([UPTIME, FIRMWARE], 3)
        monkeypatch.undo()
        assert await ids(limit=1000) == ([UPTIME, FIRMWARE, MAIL], 3)

        # like the CI search: description/justification only from three characters on
        assert await ids(text="of") == ([], 0)  # only in MAIL's justification
        assert await ids(text="end of") == ([MAIL], 1)
        assert await ids(text="94") == ([UPTIME, FIRMWARE, MAIL], 3)  # number and title: always

        # a text search over its time limit is reported, not a 500
        @asynccontextmanager
        async def timed_out(_session: Any, _seconds: int) -> AsyncIterator[None]:
            raise ReportQueryTimeout("statement timeout")
            yield

        monkeypatch.setattr(reader, "statement_timeout", timed_out)
        with pytest.raises(reader.LegacySearchTooBroad):
            await ids(text="relay")
        assert await ids(ticket_id=TICKET_A) == ([MAIL], 1)  # without text: no time limit
