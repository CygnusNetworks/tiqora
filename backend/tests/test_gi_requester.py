"""Requester pipeline (G10) against real Znuny tables (both dialects); the remote is a
``httpx.MockTransport`` and the resolver returns documentation addresses.

Seed band 51220-51269: webservice 51220, tickets 51221-51229. Rows the write services and the
debugger create are removed with ``snapshot_marks`` / ``delete_above_marks``.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import pytest
import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._gi_fixtures import (
    EGRESS_TEST_POLICY,
    GI_MARK_TABLES,
    cleanup_webservices,
    insert_webservice,
    requester_config,
)
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.gi.debug_store import DbDebugSink, get_replay
from tiqora.gi.error_handling import PastExecutionData
from tiqora.gi.loader import GiWebservice, load_gi_webservice
from tiqora.gi.requester import RequesterOutcome, run_requester
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

WS = 51220
TICKET = 51221
T0 = datetime(2026, 1, 1, 10, 0, 0)
NOW = datetime(2026, 10, 12, 8, 0, 0)
HOSTS = {"crm.example.org": ["198.51.100.10"]}
PASSWORD = "example-password-two"
HEADER_SECRET = "example-header-value-one"
_SEEDED: dict[str, dict[str, int]] = {}

EVENT = {"TicketID": TICKET, "Event": "TicketCreate"}
GENERIC = {"Type": "Ticket::Generic"}


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _resolver(host: str, port: int) -> list[str]:
    return HOSTS[host]


def _cleanup(conn: Any) -> None:
    cleanup_webservices(conn, [WS])
    conn.execute(text("DELETE FROM ticket WHERE id BETWEEN 51221 AND 51229"))


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
                " VALUES (:id, :tn, 'GI52 Start', 1, 1, 1, NULL, NULL, 1, 1, 3, 1, '', '',"
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
def url_fixture(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    with create_engine(url).begin() as conn:
        cleanup_webservices(conn, [WS])
        conn.execute(text("UPDATE ticket SET title = 'GI52 Start' WHERE id = :t"), {"t": TICKET})
    return url


@dataclass
class Row:
    communication_id: str
    summary: str
    debug_level: str
    content: str
    replay: Any
    ticket_id: int | None


@dataclass
class MemorySink:
    rows: list[Row] = field(default_factory=list)

    async def add(self, **kwargs: Any) -> None:
        self.rows.append(
            Row(
                kwargs["communication_id"],
                kwargs["summary"],
                kwargs["debug_level"],
                kwargs["content"],
                kwargs["replay"],
                kwargs["ticket_id"],
            )
        )

    def subjects(self) -> list[str]:
        return [r.summary for r in self.rows]

    def dump(self) -> str:
        return "\n".join(f"{r.summary}\n{r.content}" for r in self.rows)


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


def _install(sync_url: str, config: dict[str, Any]) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        cleanup_webservices(conn, [WS])
        insert_webservice(conn, ws_id=WS, name="GI52", config=config)
    engine.dispose()


async def _run(
    sync_url: str,
    config: dict[str, Any],
    *,
    mock: Mock | None = None,
    sink: Any = None,
    invoker: str = "SendTicket",
    data: dict[str, Any] | None = None,
    asynchronous: bool = True,
    past: PastExecutionData | None = None,
    before_send: Callable[..., Any] | None = None,
    dry_run: bool = False,
    enqueue: Callable[..., Any] | None = None,
) -> tuple[RequesterOutcome, Mock, Any]:
    _install(sync_url, config)
    mock = mock or Mock()
    sink = sink if sink is not None else MemorySink()
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            ws: GiWebservice | None = await load_gi_webservice(session, SysConfig(session), WS)
        assert ws is not None
        outcome = await run_requester(
            session_factory=factory,
            ws=ws,
            invoker=invoker,
            data=dict(EVENT) if data is None else data,
            asynchronous=asynchronous,
            past=past,
            sysconfig_factory=SysConfig,
            sink=sink,
            settings=get_settings(),
            before_send=before_send,
            dry_run=dry_run,
            http_transport=mock.transport(),
            resolver=_resolver,
            policy=EGRESS_TEST_POLICY,
            enqueue=enqueue,
            now=NOW,
        )
    finally:
        await engine.dispose()
    return outcome, mock, sink


def _title(sync_url: str) -> str:
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT title FROM ticket WHERE id = :t"), {"t": TICKET}
        ).scalar_one()
    engine.dispose()
    return str(value)


def _cfg(invoker: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    return requester_config(invokers={"SendTicket": invoker or dict(GENERIC)}, **kwargs)


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


async def test_happy_path_debug_sequence(url_fixture: str) -> None:
    out, mock, sink = await _run(url_fixture, _cfg())
    assert out.success and out.error_message is None and out.reschedule is None
    assert out.data == {"result": "ok"}
    assert sink.subjects() == [
        "Communication sequence started",
        "Using invoker 'SendTicket'",
        "Outgoing data before mapping",
        "URI after interpolating URI params from outgoing data",
        "Remaining outgoing data to be sent",
        "Request params from outgoing data",
        "JSON data received from remote system",
        "Incoming data before mapping",
    ]
    assert {r.communication_id for r in sink.rows} == {out.communication_id}
    assert all(r.ticket_id == TICKET for r in sink.rows)
    body = json.loads(mock.seen[0].content)
    assert set(body) == {"Ticket", "Event"} and body["Event"]["Event"] == "TicketCreate"
    assert str(body["Ticket"]["TicketID"]) == str(TICKET)
    # Znuny: :TicketID is not a top-level key of the invoker output, so it stays literal
    assert mock.seen[0].url.path == "/Ticket/:TicketID"


async def test_outbound_and_inbound_mapping_with_data_include(url_fixture: str) -> None:
    template = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">\n'
        '<xsl:output method="xml" encoding="utf-8" indent="yes"/>\n'
        '<xsl:template match="/RootElement"><Out>'
        '<FromInput><xsl:value-of select="DataInclude/RequesterRequestInput/Event"/></FromInput>'
        '<FromPrepare><xsl:value-of select="Event/Event"/></FromPrepare>'
        "<FromPrepareOutput><xsl:value-of"
        ' select="DataInclude/RequesterRequestPrepareOutput/Event/TicketID"/></FromPrepareOutput>'
        "</Out></xsl:template>\n</xsl:stylesheet>"
    )
    invoker = {
        **GENERIC,
        "MappingOutbound": {
            "Type": "XSLT",
            "Config": {
                "Template": template,
                "DataInclude": ["RequesterRequestInput", "RequesterRequestPrepareOutput"],
            },
        },
        "MappingInbound": {
            "Type": "Simple",
            "Config": {
                "KeyMapExact": {"result": "Renamed"},
                "KeyMapDefault": {"MapType": "Keep"},
                "ValueMapDefault": {"MapType": "Keep"},
            },
        },
    }
    out, mock, sink = await _run(url_fixture, _cfg(invoker))
    assert out.success
    body = json.loads(mock.seen[0].content)
    assert body == {
        "FromInput": "TicketCreate",
        "FromPrepare": "TicketCreate",
        "FromPrepareOutput": str(TICKET),
    }
    assert out.data == {"Renamed": "ok"}
    subjects = sink.subjects()
    assert "Outgoing data after mapping" in subjects and "Incoming data after mapping" in subjects


async def test_transport_failure_runs_error_handling_and_reschedules(url_fixture: str) -> None:
    mock = Mock(handler=lambda request: httpx.Response(503, json={"error": "down"}))
    out, _, sink = await _run(url_fixture, _cfg(error_handling=RETRY), mock=mock)
    assert not out.success and out.error_message
    assert out.reschedule is not None and out.reschedule.reschedule
    assert (out.reschedule.execution_time - NOW).total_seconds() == 60  # type: ignore[operator]
    assert out.reschedule.past is not None and out.reschedule.past.retry_count == 1
    notices = [r for r in sink.rows if r.debug_level == "notice"]
    assert any(r.summary == "Request will be retried again at:" for r in notices)
    assert any(r.debug_level == "error" for r in sink.rows)


async def test_retry_chain_uses_past_execution_data(url_fixture: str) -> None:
    mock = Mock(handler=lambda request: httpx.Response(503, json={"error": "down"}))
    past = PastExecutionData(NOW.replace(hour=7), 1, 60, NOW.replace(hour=7, minute=1))
    out, _, _ = await _run(url_fixture, _cfg(error_handling=RETRY), mock=mock, past=past)
    assert out.reschedule is not None and out.reschedule.past is not None
    assert out.reschedule.past.retry_count == 2
    assert out.reschedule.past.initial_request_at == NOW.replace(hour=7)


async def test_mapping_error_stage_and_no_send(url_fixture: str) -> None:
    invoker = {
        **GENERIC,
        "MappingOutbound": {
            "Type": "XSLT",
            "Config": {"Template": "this is no xsl"},
        },
    }
    staged = {"Retry": {**RETRY["Retry"], "ErrorStageFilter": ["RequesterRequestMap"]}}
    out, mock, sink = await _run(url_fixture, _cfg(invoker, error_handling=staged))
    assert not out.success and mock.seen == []
    assert out.error_message
    # rescheduled only because the stage filter matches RequesterRequestMap
    assert out.reschedule is not None
    other = {"Retry": {**RETRY["Retry"], "ErrorStageFilter": ["RequesterRequestPerform"]}}
    out2, mock2, _ = await _run(url_fixture, _cfg(invoker, error_handling=other))
    assert not out2.success and mock2.seen == [] and out2.reschedule is None
    assert "Error data before mapping" in sink.subjects()


async def test_unsupported_invoker_type_is_error_without_send(url_fixture: str) -> None:
    out, mock, sink = await _run(url_fixture, _cfg({"Type": "Test::TestSimple"}))
    assert not out.success and mock.seen == []
    assert out.error_message == "InvokerObject could not be initialized"
    assert out.reschedule is None
    errors = [r for r in sink.rows if r.debug_level == "error"]
    assert errors and "nicht unterstützt" in errors[0].content


async def test_unknown_invoker_is_error_without_send(url_fixture: str) -> None:
    out, mock, _ = await _run(url_fixture, _cfg(), invoker="Nope")
    assert not out.success and mock.seen == []
    assert out.error_message == "InvokerObject could not be initialized"


async def test_response_actions_run_after_inbound_mapping(url_fixture: str) -> None:
    invoker = {
        **GENERIC,
        "MappingInbound": {
            "Type": "Simple",
            "Config": {
                "KeyMapExact": {"newtitle": "OTRS_TicketTitleUpdate"},
                "KeyMapDefault": {"MapType": "Ignore"},
                "ValueMapDefault": {"MapType": "Keep"},
            },
        },
    }
    mock = Mock(handler=lambda request: httpx.Response(200, json={"newtitle": "Set by remote"}))
    out, _, _ = await _run(url_fixture, _cfg(invoker), mock=mock)
    assert out.success and out.data == {"OTRS_TicketTitleUpdate": "Set by remote"}
    assert _title(url_fixture) == "Set by remote"


async def test_failing_response_action_is_stage_response_process(url_fixture: str) -> None:
    mock = Mock(handler=lambda request: httpx.Response(200, json={"OTRS_TicketQueueSet": "None"}))
    out, _, sink = await _run(url_fixture, _cfg(error_handling=RETRY), mock=mock)
    assert not out.success
    assert out.error_message and "Failed to execute" in out.error_message
    assert out.reschedule is not None
    assert any(r.debug_level == "error" for r in sink.rows)


async def test_dry_run_sends_nothing_and_masks(url_fixture: str) -> None:
    cfg = _cfg(
        transport_extra={
            "Authentication": {
                "AuthType": "BasicAuth",
                "BasicAuthUser": "apiuser",
                "BasicAuthPassword": PASSWORD,
            },
            "AdditionalHeaders": {"X-Api-Key": HEADER_SECRET},
        }
    )
    out, mock, sink = await _run(url_fixture, cfg, dry_run=True)
    assert out.success and mock.seen == []
    assert sink.rows == []  # a dry run leaves no debugger trail
    request = out.data["request"]
    assert request["method"] == "POST" and "crm.example.org" in request["url"]
    shown = json.dumps(out.data)
    assert PASSWORD not in shown and HEADER_SECRET not in shown
    assert request["headers"]["Authorization"].startswith("Basic ")
    assert "***" in request["headers"]["Authorization"]
    assert json.loads(request["body"])["Event"]["Event"] == "TicketCreate"


async def test_soap_dry_run_sends_nothing_and_masks(url_fixture: str) -> None:
    proxy_password = "example-proxy-secret-three"
    template = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">\n'
        '<xsl:output method="xml" encoding="utf-8" indent="yes"/>\n'
        '<xsl:template match="/RootElement"><Out>'
        '<Event><xsl:value-of select="Event/Event"/></Event>'
        "</Out></xsl:template>\n</xsl:stylesheet>"
    )
    invoker = {**GENERIC, "MappingOutbound": {"Type": "XSLT", "Config": {"Template": template}}}
    cfg = requester_config(
        invokers={"SendTicket": invoker},
        transport="HTTP::SOAP",
        transport_extra={
            "Endpoint": "https://crm.example.org/soap",
            "NameSpace": "https://example.org/ns",
            "Authentication": {
                "AuthType": "BasicAuth",
                "BasicAuthUser": "apiuser",
                "BasicAuthPassword": PASSWORD,
            },
            "Proxy": {
                "UseProxy": "Yes",
                "ProxyHost": "http://198.51.100.20:3128",
                "ProxyUser": "proxyuser",
                "ProxyPassword": proxy_password,
            },
        },
    )
    out, mock, sink = await _run(url_fixture, cfg, dry_run=True)
    assert out.success, out.error_message
    assert mock.seen == []  # nothing sent
    assert sink.rows == []  # a dry run leaves no debugger trail
    request = out.data["request"]
    assert request["method"] == "POST" and "crm.example.org" in request["url"]
    shown = json.dumps(out.data)
    for secret in (
        PASSWORD,
        proxy_password,
        base64.b64encode(f"apiuser:{PASSWORD}".encode()).decode(),
    ):
        assert secret not in shown
    assert request["headers"]["Authorization"].startswith("Basic ")
    assert "***" in request["headers"]["Authorization"]
    assert "Envelope" in request["body"] and "TicketCreate" in request["body"]


async def test_before_send_called_exactly_once_before_network(url_fixture: str) -> None:
    calls: list[int] = []
    mock = Mock()

    async def before() -> None:
        calls.append(len(mock.seen))

    out, _, _ = await _run(url_fixture, _cfg(), mock=mock, before_send=before)
    assert out.success and calls == [0] and len(mock.seen) == 1


async def test_before_send_not_called_when_invoker_fails_before_transport(url_fixture: str) -> None:
    calls: list[int] = []

    async def before() -> None:
        calls.append(1)

    out, mock, _ = await _run(url_fixture, _cfg({"Type": "Other"}), before_send=before)
    assert not out.success and mock.seen == [] and calls == []


async def test_job_payload_and_result_contain_no_secret(url_fixture: str) -> None:
    cfg = _cfg(
        error_handling=RETRY,
        transport_extra={
            "Authentication": {
                "AuthType": "BasicAuth",
                "BasicAuthUser": "apiuser",
                "BasicAuthPassword": PASSWORD,
            },
            "AdditionalHeaders": {"X-Api-Key": HEADER_SECRET},
        },
    )
    mock = Mock(
        handler=lambda request: httpx.Response(
            500, text=f"boom {request.headers['x-api-key']} {PASSWORD}"
        )
    )
    with structlog.testing.capture_logs() as logs:
        out, _, sink = await _run(url_fixture, cfg, mock=mock)
    assert not out.success
    blob = repr(out) + sink.dump() + json.dumps(logs, default=str)
    assert PASSWORD not in blob and HEADER_SECRET not in blob
    assert "apiuser:" + PASSWORD not in blob


async def test_replay_column_written_without_secrets(url_fixture: str) -> None:
    cfg = _cfg(
        debug_threshold="error",
        error_handling={},
        transport_extra={
            "Authentication": {
                "AuthType": "BasicAuth",
                "BasicAuthUser": "apiuser",
                "BasicAuthPassword": PASSWORD,
            }
        },
    )
    mock = Mock(handler=lambda request: httpx.Response(503, text="down"))
    engine = create_async_engine(_async_url(url_fixture))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # threshold=error and the transport fails: the entry is created by the error line and
        # must carry the replay, so that "Erneut senden" is offered for exactly this case.
        out, _, _ = await _run(
            url_fixture, cfg, mock=mock, sink=DbDebugSink(factory), asynchronous=False
        )
        assert not out.success
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, replay FROM tiqora_gi_debug_entry WHERE communication_id = :c"
                    ),
                    {"c": out.communication_id},
                )
            ).one()
            replay = await get_replay(session, entry_id=int(row[0]), webservice_id=WS)
    finally:
        await engine.dispose()
    assert replay == {"invoker": "SendTicket", "data": EVENT, "asynchronous": False}
    assert PASSWORD not in str(row[1])


async def test_asynchronous_invoker_execution_goes_to_enqueue(url_fixture: str) -> None:
    queued: list[dict[str, Any]] = []

    async def enqueue(payload: dict[str, Any]) -> None:
        queued.append(payload)

    item = [{"WebserviceID": WS, "Invoker": "SendTicket", "Data": {"TicketID": TICKET}}]
    mock = Mock(
        handler=lambda request: httpx.Response(
            200, json={"OTRS_AsynchronousInvokerExecution": item}
        )
    )
    out, _, _ = await _run(url_fixture, _cfg(), mock=mock, enqueue=enqueue)
    assert out.success
    assert [q["invoker"] for q in queued] == ["SendTicket"] and queued[0]["asynchronous"] is True


async def test_missing_data_is_rejected_before_any_row(url_fixture: str) -> None:
    out, mock, sink = await _run(url_fixture, _cfg(), data={})
    assert not out.success and out.error_message == "Got no Data!"
    assert mock.seen == [] and sink.rows == []
