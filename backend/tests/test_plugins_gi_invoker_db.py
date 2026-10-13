"""GenericInterface invokers from plugins (D14, both dialects).

Seed band 55450-55469: webservice 55450, ticket 55451. The remote is a ``httpx.MockTransport``.
Rows the debugger creates are removed with ``snapshot_marks`` / ``delete_above_marks``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._gi_fixtures import (
    EGRESS_TEST_POLICY,
    GI_MARK_TABLES,
    cleanup_webservices,
    insert_webservice,
    requester_config,
)
from tests._plugin_points import PLUGIN_ID, boot_points_plugin, points_behaviour
from tests._plugins_state_helpers import to_async_url
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.gi.config import parse_requester
from tiqora.gi.error_handling import PastExecutionData
from tiqora.gi.loader import GiWebservice, load_gi_webservice
from tiqora.gi.requester import RequesterOutcome, run_requester
from tiqora.plugins.state import PluginLoadResult, RuntimeState, set_loaded_plugins
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

WS = 55450
TICKET = 55451
T0 = datetime(2026, 1, 1, 10, 0, 0)
NOW = datetime(2026, 10, 13, 8, 0, 0)
HOSTS = {"crm.example.org": ["198.51.100.10"]}
EVENT = {"TicketID": TICKET, "Event": "TicketCreate"}
PLUGIN_TYPE = "Plugin_points::ContractSync"
SYNC = {"Type": PLUGIN_TYPE}
RETRY = {
    "Retry": {
        "Type": "RequestRetry",
        "ScheduleRetry": "1",
        "RetryIntervalStart": "60",
        "RetryIntervalFactor": "1",
        "RetryIntervalMax": "",
        "RetryCountMax": "",
        "RetryPeriodMax": "",
    }
}
_SEEDED: dict[str, dict[str, int]] = {}


def _resolver(host: str, port: int) -> list[str]:
    return HOSTS[host]


def _cleanup(conn: Any) -> None:
    cleanup_webservices(conn, [WS])
    conn.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": TICKET})


def _seed(sync_url: str) -> None:
    if sync_url in _SEEDED:
        return
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    _SEEDED[sync_url] = snapshot_marks(sync_url, GI_MARK_TABLES)
    with engine.begin() as conn:
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " service_id, sla_id, user_id, responsible_user_id, ticket_priority_id,"
                " ticket_state_id, customer_id, customer_user_id, timeout, until_time,"
                " escalation_time, escalation_update_time, escalation_response_time,"
                " escalation_solution_time, archive_flag, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:id, :tn, 'GI55 Start', 1, 1, 1, NULL, NULL, 1, 1, 3, 1, '', '',"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"id": TICKET, "tn": f"{TICKET}01", "t": T0},
        )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url, marks in _SEEDED.items():
        delete_above_marks(sync_url, marks)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _cleanup(conn)
        engine.dispose()
    _SEEDED.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def url_fixture(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    with create_engine(url).begin() as conn:
        cleanup_webservices(conn, [WS])
    boot_points_plugin(tmp_path, monkeypatch)
    return url


@dataclass
class MemorySink:
    rows: list[tuple[str, str]] = field(default_factory=list)  # (level, summary)

    async def add(self, **kwargs: Any) -> None:
        self.rows.append((kwargs["debug_level"], kwargs["summary"]))

    def summaries(self) -> list[str]:
        return [summary for _, summary in self.rows]


@dataclass
class Mock:
    seen: list[httpx.Request] = field(default_factory=list)
    handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
        200, json={"result": "ok"}
    )

    def transport(self) -> httpx.MockTransport:
        def record(request: httpx.Request) -> httpx.Response:
            self.seen.append(request)
            return self.handler(request)

        return httpx.MockTransport(record)


async def _run(
    sync_url: str,
    invoker: dict[str, Any],
    *,
    error_handling: dict[str, Any] | None = None,
    mock: Mock | None = None,
    past: PastExecutionData | None = None,
) -> tuple[RequesterOutcome, Mock, MemorySink]:
    config = requester_config(invokers={"SendTicket": invoker}, error_handling=error_handling)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        cleanup_webservices(conn, [WS])
        insert_webservice(conn, ws_id=WS, name="GI55", config=config)
    engine.dispose()
    mock = mock or Mock()
    sink = MemorySink()
    async_engine = create_async_engine(to_async_url(sync_url))
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    try:
        async with factory() as session:
            ws: GiWebservice | None = await load_gi_webservice(session, SysConfig(session), WS)
        assert ws is not None
        outcome = await run_requester(
            session_factory=factory,
            ws=ws,
            invoker="SendTicket",
            data=dict(EVENT),
            asynchronous=True,
            past=past,
            sysconfig_factory=SysConfig,
            sink=sink,
            settings=get_settings(),
            http_transport=mock.transport(),
            resolver=_resolver,
            policy=EGRESS_TEST_POLICY,
            now=NOW,
        )
    finally:
        await async_engine.dispose()
    return outcome, mock, sink


def _calls(kind: str) -> list[Any]:
    return [c[1] for c in points_behaviour().CALLS if c[0] == kind]


def test_plugin_invoker_supported(url_fixture: str) -> None:
    config = {"Requester": {"Invoker": {"Sync": dict(SYNC), "Other": {"Type": "Nope::Nope"}}}}
    invokers = parse_requester(config, huge_xml=False).invokers
    assert invokers["Sync"].supported is True
    assert invokers["Other"].supported is False
    set_loaded_plugins([PluginLoadResult(PLUGIN_ID, "1.0.0", RuntimeState.DISABLED, None, ())])
    assert parse_requester(config, huge_xml=False).invokers["Sync"].supported is False


async def test_requester_runs_plugin_invoker(url_fixture: str) -> None:
    invoker = {
        **SYNC,
        "MappingInbound": {
            "Type": "Simple",
            "Config": {
                "KeyMapExact": {"result": "Renamed"},
                "KeyMapDefault": {"MapType": "Keep"},
                "ValueMapDefault": {"MapType": "Keep"},
            },
        },
    }
    out, mock, sink = await _run(url_fixture, invoker)
    assert out.success and out.error_message is None and out.reschedule is None
    assert len(mock.seen) == 1
    assert json.loads(mock.seen[0].content) == {"Hello": "Welt"}  # :TicketID went into the URL
    assert mock.seen[0].url.path == f"/Ticket/{TICKET}"
    ((name, ticket_id, event),) = _calls("gi.inv.prepare")
    assert (name, ticket_id) == ("SendTicket", TICKET)
    assert event == EVENT
    assert _calls("gi.inv.response") == [(True, {"Renamed": "ok"})]
    assert out.data == {"Renamed": "ok"}
    assert "Outgoing data before mapping" in sink.summaries()


async def test_plugin_outbound_mapping_applies_to_prepared_data(url_fixture: str) -> None:
    invoker = {**SYNC, "MappingOutbound": {"Type": "Plugin_points_Upper"}}
    out, mock, _ = await _run(url_fixture, invoker)
    assert out.success
    assert json.loads(mock.seen[0].content) == {"Hello": "WELT"}


async def test_prepare_none_is_stop_communication(url_fixture: str) -> None:
    points_behaviour().MODE["gi.inv"] = "skip"
    out, mock, sink = await _run(url_fixture, dict(SYNC), error_handling=RETRY)
    assert out.success and out.error_message is None and out.reschedule is None
    assert mock.seen == []  # nothing sent
    assert ("notice", "Erweiterung hat den Aufruf übersprungen") in sink.rows
    assert _calls("gi.inv.response") == []


@pytest.mark.parametrize("mode", ["raise", "hang"])
async def test_prepare_failure_uses_existing_error_path(url_fixture: str, mode: str) -> None:
    points_behaviour().MODE["gi.inv"] = mode
    started = time.monotonic()
    out, mock, sink = await _run(url_fixture, dict(SYNC), error_handling=RETRY)
    assert time.monotonic() - started < 8  # a hanging prepare is cut at timeout_seconds=1
    assert not out.success
    assert out.error_message == "InvokerObject returned an error, cancelling Request"
    assert mock.seen == []
    assert out.reschedule is not None and out.reschedule.reschedule
    assert (out.reschedule.execution_time - NOW).total_seconds() == 60  # type: ignore[operator]
    assert ("error", "InvokerObject returned an error, cancelling Request") in sink.rows
    assert "Request will be retried again at:" in sink.summaries()
    # the plugin is told about the failure, like a core invoker's HandleError
    (response,) = _calls("gi.inv.response")
    assert response[0] is False


async def test_response_handler_failure_goes_to_error_path_once(url_fixture: str) -> None:
    points_behaviour().MODE["gi.inv.response"] = "raise"
    out, mock, sink = await _run(url_fixture, dict(SYNC), error_handling=RETRY)
    assert len(mock.seen) == 1
    assert not out.success
    assert out.error_message == "InvokerObject returned an error, cancelling Request"
    assert out.reschedule is not None and out.reschedule.reschedule
    assert len(_calls("gi.inv.response")) == 1  # not called again with its own failure
    assert any(level == "error" for level, _ in sink.rows)


async def test_plugin_disabled_after_parse_is_unsupported(url_fixture: str) -> None:
    set_loaded_plugins([PluginLoadResult(PLUGIN_ID, "1.0.0", RuntimeState.DISABLED, None, ())])
    out, mock, sink = await _run(url_fixture, dict(SYNC))
    assert not out.success and out.error_message == "InvokerObject could not be initialized"
    assert mock.seen == [] and _calls("gi.inv.prepare") == []
    assert ("error", "InvokerObject could not be initialized") in sink.rows


async def test_core_generic_invoker_unchanged(url_fixture: str) -> None:
    out, mock, sink = await _run(url_fixture, {"Type": "Ticket::Generic"})
    assert out.success and out.data == {"result": "ok"}
    body = json.loads(mock.seen[0].content)
    assert set(body) == {"Ticket", "Event"} and body["Event"]["Event"] == "TicketCreate"
    assert str(body["Ticket"]["TicketID"]) == str(TICKET)
    assert mock.seen[0].url.path == "/Ticket/:TicketID"
    assert points_behaviour().CALLS == []  # no plugin code ran for a core type
    assert sink.summaries()[-1] == "Incoming data before mapping"
