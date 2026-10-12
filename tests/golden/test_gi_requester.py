"""Golden-master: GenericInterface requester (invokers) against the real Znuny 6.5 peer.

Two webservices (``webservices/GoldenRequester{REST,SOAP}.yml``) are imported ONCE into the
peer; Tiqora reads the very same ``gi_webservice_config`` rows. Both sides run the same
invoker on the same ticket and send to the same recording sink (``gi_sink.py``):

* Znuny: ``Kernel::GenericInterface::Requester->Run`` inside the peer, target
  ``http://gisink:8080`` on the compose network.
* Tiqora: :func:`tiqora.gi.requester.run_requester` on the golden DB. The SSRF guard stays
  armed: the resolver maps the configured host to a documentation address, the test egress
  policy allows that range, and only the injected ``http_transport`` forwards the (already
  pinned) request to the sink on ``127.0.0.1:8199``. Nothing is loosened by configuration.

The hosts differ, so requests are compared after dropping ``Host``/``Content-Length``/
``User-Agent`` and masking ids, ticket numbers, timestamps, ``Age`` and ``UntilTime``.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from _helpers import (
    GOLDEN_DIR,
    gisink_clear,
    gisink_requests,
    gisink_respond,
    znuny_console,
    znuny_copy_in,
    znuny_perl_eval,
    znuny_requester_run,
)
from lxml import etree

from tiqora.config import Settings
from tiqora.gi.conditions import condition_check, serialize_config
from tiqora.gi.debug_store import DbDebugSink
from tiqora.gi.deep_get import ticket_deep_get
from tiqora.gi.egress import EgressPolicy
from tiqora.gi.error_handling import PastExecutionData
from tiqora.gi.loader import GiWebservice, load_gi_webservice_by_name
from tiqora.gi.requester import RequesterOutcome, run_requester
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.golden

REST_WS = "GoldenRequesterREST"
SOAP_WS = "GoldenRequesterSOAP"
BASIC_PASSWORD = "example-golden-password"
HEADER_SECRET = "example-golden-header-value"
BASIC_TOKEN = base64.b64encode(f"golden-user:{BASIC_PASSWORD}".encode()).decode()

# Documentation range (RFC 5737): private-but-not-reserved, so the SSRF guard accepts it only
# through this allowlist (same policy as backend/tests/_gi_fixtures.EGRESS_TEST_POLICY).
TEST_POLICY = EgressPolicy(
    allow_networks=(ipaddress.ip_network("198.51.100.0/24"), ipaddress.ip_network("2001:db8::/32")),
    allow_hosts=frozenset(),
    allow_insecure_tls=False,
    cert_dirs=(),
    max_timeout=30.0,
    max_response_bytes=1024 * 1024,
)

# ---------------------------------------------------------------------------
# normaliser
# ---------------------------------------------------------------------------

_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}Z?")
# Per-call values: ids and numbers allocated per ticket, and clock-derived values.
_VOLATILE_KEYS = frozenset(
    {
        "TicketID",
        "TicketNumber",
        "ArticleID",
        "ArticleNumber",
        "ArticleIDs",
        "Age",
        "UntilTime",
        "EscalationTime",
        "EscalationResponseTime",
        "EscalationUpdateTime",
        "EscalationSolutionTime",
    }
)
# Host/User-Agent/Content-Length: different hosts and HTTP client libraries (LWP vs httpx).
# accept/accept-encoding/connection/te: client-library defaults and hop-by-hop headers only
# (Deviation 6: Tiqora opens a fresh pinned connection per request, KeepAlive is ignored).
_DROPPED_HEADERS = frozenset(
    {"host", "content-length", "user-agent", "accept", "accept-encoding", "connection", "te"}
)


# Known deviation (Deviation 14, for T20 compatibility.md): Znuny TicketGet adds the computed
# escalation detail keys (working-time seconds and destination dates per escalation kind);
# Tiqora's TicketDeepGet serialises only the stored ticket.escalation_* columns. The keys are
# asserted present on the Znuny side and absent on the Tiqora side, then dropped for the compare.
_ESCALATION_DETAIL_KEYS = frozenset(
    {
        "EscalationDestinationDate",
        "EscalationDestinationIn",
        "EscalationDestinationTime",
        "EscalationTimeWorkingTime",
    }
    | {
        f"{kind}{suffix}"
        for kind in ("FirstResponseTime", "SolutionTime", "UpdateTime")
        for suffix in ("", "WorkingTime", "DestinationDate", "DestinationTime")
    }
)


def normalize(value: Any, key: str = "") -> Any:
    """Compare-friendly view of a JSON body: scalars become strings, volatile keys and
    timestamps become placeholders, empty containers collapse to ``""``."""
    if isinstance(value, dict):
        out = {k: normalize(v, k) for k, v in value.items()}
        return out or ""
    if isinstance(value, list):
        return [normalize(v, key) for v in value] or ""
    if value is None:
        return ""
    text_value = str(value)
    if key in _VOLATILE_KEYS:
        return f"<{key}>" if text_value else ""
    if _TIMESTAMP_RE.fullmatch(text_value):
        return "<timestamp>"
    return text_value


def test_normalize_self_check() -> None:
    body = {
        "TicketID": 12,
        "Age": 3,
        "Created": "2026-03-01 12:00:00",
        "Empty": {},
        "Rows": [{"ArticleID": 5, "Subject": "x", "N": 7}],
    }
    assert normalize(body) == {
        "TicketID": "<TicketID>",
        "Age": "<Age>",
        "Created": "<timestamp>",
        "Empty": "",
        "Rows": [{"ArticleID": "<ArticleID>", "Subject": "x", "N": "7"}],
    }


def _request_view(record: dict[str, Any]) -> dict[str, Any]:
    """One recorded sink request, reduced to what must be identical on both sides."""
    headers = {k.lower(): v for k, v in record["headers"].items()}
    body: Any = record["body"]
    if "json" in headers.get("content-type", ""):
        body = normalize(json.loads(body)) if body else ""
    return {
        "method": record["method"],
        "path": record["path"],
        "query": sorted(record["query"]),
        "headers": {k: v for k, v in sorted(headers.items()) if k not in _DROPPED_HEADERS},
        "body": body,
    }


# ---------------------------------------------------------------------------
# peer setup and Tiqora-side runner
# ---------------------------------------------------------------------------


def _import_webservice(name: str, source: Path | None = None) -> None:
    remote = f"/tmp/{name}.yml"  # noqa: S108 - path inside the peer container
    znuny_copy_in(source or GOLDEN_DIR / "webservices" / f"{name}.yml", remote)
    try:
        znuny_console("Admin::WebService::Add", f"--name={name}", f"--source-path={remote}")
    except RuntimeError as exc:
        if "already exists" not in str(exc):
            raise
        found = znuny_perl_eval(
            "use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib);"
            "use Kernel::System::ObjectManager;"
            "local $Kernel::OM = Kernel::System::ObjectManager->new();"
            "my $W = $Kernel::OM->Get('Kernel::System::GenericInterface::Webservice')"
            f"->WebserviceGet(Name => '{name}'); print $W->{{ID}};"
        ).strip()
        znuny_console(
            "Admin::WebService::Update", f"--webservice-id={found}", f"--source-path={remote}"
        )


_CREATE_TICKET_PERL = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-gi-requester'},
);
my $TicketObject = $Kernel::OM->Get('Kernel::System::Ticket');
my $TicketID = $TicketObject->TicketCreate(
    Title        => '__TITLE__',
    Queue        => 'Golden',
    Lock         => 'unlock',
    Priority     => '3 normal',
    State        => 'new',
    CustomerID   => 'golden-customer',
    CustomerUser => 'golden.customer',
    OwnerID      => 1,
    UserID       => 1,
);
my $Backend = $Kernel::OM->Get('Kernel::System::Ticket::Article')
    ->BackendForChannel(ChannelName => 'Internal');
my @Articles = ([customer => 'First article'], [agent => 'Second article'])[0 .. __ARTICLES__ - 1];
my $First;
for my $Item (@Articles) {
    my $ArticleID = $Backend->ArticleCreate(
        TicketID             => $TicketID,
        SenderType           => $Item->[0],
        IsVisibleForCustomer => 0,
        From                 => 'golden@example.org',
        Subject              => $Item->[1],
        Body                 => $Item->[1] . ' body',
        ContentType          => 'text/plain; charset=utf8',
        HistoryType          => 'AddNote',
        HistoryComment       => '%% ' . $Item->[1],
        UserID               => 1,
    );
    $First //= $ArticleID;
}
if (__ATTACHMENT__) {
    $Backend->ArticleWriteAttachment(
        Content     => 'golden attachment content',
        ContentType => 'text/plain',
        Filename    => 'golden.txt',
        ArticleID   => $First,
        UserID      => 1,
    );
}
print $TicketID;
"""


def _create_ticket(title: str, *, articles: int = 2, attachment: bool = True) -> int:
    code = (
        _CREATE_TICKET_PERL.replace("__TITLE__", title)
        .replace("__ARTICLES__", str(articles))
        .replace("__ATTACHMENT__", "1" if attachment else "0")
    )
    return int(znuny_perl_eval(code).strip())


_ENSURE_DF_PERL = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-gi-requester'},
);
$Kernel::OM->Get('Kernel::System::Cache')->CleanUp();
my $DF = $Kernel::OM->Get('Kernel::System::DynamicField');
my $Field = $DF->DynamicFieldGet(Name => 'GoldenDF');
my $ID = ref $Field ? $Field->{ID} : undef;
if (!$ID) {
    $ID = $DF->DynamicFieldAdd(
        Name => 'GoldenDF', Label => 'Golden requester text', FieldOrder => 9002,
        FieldType => 'Text', ObjectType => 'Ticket',
        Config => {DefaultValue => '', Link => '', LinkPreview => ''},
        ValidID => 1, UserID => 1,
    );
}
print "id=" . ($ID // 'none');
"""


@pytest.fixture(scope="module", autouse=True)
def _peer_setup() -> None:
    _import_webservice(REST_WS)
    _import_webservice(SOAP_WS)
    assert re.fullmatch(r"id=\d+", znuny_perl_eval(_ENSURE_DF_PERL).strip())


@pytest.fixture(autouse=True)
def _clean_sink() -> None:
    gisink_clear()


class _ForwardToSink(httpx.AsyncBaseTransport):
    """Sends the already pinned request (documentation IP, original Host header) to the
    local sink. The SSRF guard ran before this point; the transport only changes the
    socket target."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=8199)
        return await self._inner.handle_async_request(request)


def _resolver(host: str, port: int) -> list[str]:
    return ["198.51.100.10"]


@dataclass
class _MemoryRow:
    debug_level: str
    summary: str
    content: str


@dataclass
class _MemorySink:
    rows: list[_MemoryRow] = field(default_factory=list)

    async def add(self, **kwargs: Any) -> None:
        self.rows.append(_MemoryRow(kwargs["debug_level"], kwargs["summary"], kwargs["content"]))


async def _tiqora_run(
    factory: Any,
    ws_name: str,
    invoker: str,
    data: dict[str, Any],
    *,
    past: PastExecutionData | None = None,
    now: datetime | None = None,
    sink: Any = None,
) -> RequesterOutcome:
    async with factory() as session:
        ws: GiWebservice | None = await load_gi_webservice_by_name(
            session, SysConfig(session), ws_name
        )
    assert ws is not None, f"{ws_name} not loadable by Tiqora"
    transport = _ForwardToSink()
    try:
        return await _run(transport, factory, ws, invoker, data, past, now, sink)
    finally:
        await transport.aclose()


async def _run(
    transport: _ForwardToSink,
    factory: Any,
    ws: GiWebservice,
    invoker: str,
    data: dict[str, Any],
    past: PastExecutionData | None,
    now: datetime | None,
    sink: Any,
) -> RequesterOutcome:
    return await run_requester(
        session_factory=factory,
        ws=ws,
        invoker=invoker,
        data=data,
        asynchronous=True,
        past=past,
        sysconfig_factory=SysConfig,
        sink=sink if sink is not None else _MemorySink(),
        settings=Settings(environment="test"),
        http_transport=transport,
        resolver=_resolver,
        policy=TEST_POLICY,
        now=now,
    )


@pytest.fixture(scope="module")
def ticket_id() -> int:
    return _create_ticket("Golden requester ticket")


def _db_rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def _decode(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_payload_parity(golden_session_factory, golden_conn, ticket_id: int) -> None:
    event = {"TicketID": ticket_id, "Event": "TicketCreate"}
    last_znuny_entry = _db_rows(golden_conn, "SELECT COALESCE(MAX(id), 0) FROM gi_debugger_entry")[
        0
    ][0]

    result = znuny_requester_run(REST_WS, "SendTicket", event)
    assert result["Success"] == 1, result
    z_requests = gisink_requests()
    gisink_clear()

    sink = DbDebugSink(golden_session_factory)
    outcome = await _tiqora_run(
        golden_session_factory, REST_WS, "SendTicket", dict(event), sink=sink
    )
    assert outcome.success, outcome.error_message
    t_requests = gisink_requests()

    assert len(z_requests) == len(t_requests) == 1
    z_view, t_view = _request_view(z_requests[0]), _request_view(t_requests[0])
    assert z_view["method"] == "POST" and z_view["path"].startswith("/Ticket/")
    assert z_view["headers"]["authorization"] == f"Basic {BASIC_TOKEN}"
    assert z_view["headers"]["x-golden-token"] == HEADER_SECRET
    # The XSLT is an identity copy that only renames Ticket/Title -> Subject, so the whole
    # TicketDeepGet serialisation (ticket fields, customer/owner data with the secret cleanup,
    # dynamic fields, articles with attachments) is compared. The body is compared as a dict:
    # Znuny's JSON encoder emits random key order, so key order is not part of the contract.
    body = z_view["body"]
    assert body["Ticket"]["Subject"] == "Golden requester ticket"
    assert "Title" not in body["Ticket"]
    assert len(body["Ticket"]["Articles"]) == 2
    # Positive guard: the attachment really is in the Znuny body (GetAllArticleAttachments=1),
    # so a comparison that is empty on both sides cannot pass.
    znuny_raw = z_requests[0]["body"]
    assert "golden.txt" in znuny_raw
    assert base64.b64encode(b"golden attachment content").decode() in znuny_raw
    tiqora_raw = t_requests[0]["body"]
    assert "golden.txt" in tiqora_raw
    assert base64.b64encode(b"golden attachment content").decode() in tiqora_raw
    # TicketDeepGet cleanup: no credential-like keys leak into either body.
    for raw in (znuny_raw, tiqora_raw):
        assert not re.search(r'"(?:UserPw|[A-Za-z_]*[Pp]assw[a-z]*)"\s*:', raw), raw[:500]
    z_ticket, t_ticket = z_view["body"]["Ticket"], t_view["body"]["Ticket"]
    assert set(z_ticket) >= _ESCALATION_DETAIL_KEYS, "Znuny no longer adds escalation details"
    assert not _ESCALATION_DETAIL_KEYS & set(t_ticket)
    for key in _ESCALATION_DETAIL_KEYS:
        del z_ticket[key]
    # UnlockTimeout/IncomingTime are epoch values taken from the identical stored ticket row.
    assert z_view == t_view, f"REST request differs:\nznuny:  {z_view}\ntiqora: {t_view}"

    # Deviation 4: Znuny logs the raw request params (incl. the Authorization header and the
    # configured header value) into gi_debugger_entry_content; Tiqora's rows never hold a
    # secret. Asserted on both sides on purpose, not normalised away.
    znuny_content = " ".join(
        _decode(row[0])
        for row in _db_rows(
            golden_conn,
            "SELECT c.content FROM gi_debugger_entry_content c"
            " JOIN gi_debugger_entry e ON e.id = c.gi_debugger_entry_id WHERE e.id > %s",
            (last_znuny_entry,),
        )
    )
    assert BASIC_PASSWORD in znuny_content or BASIC_TOKEN in znuny_content, znuny_content[:2000]

    tiqora_content = " ".join(
        _decode(row[0])
        for row in _db_rows(
            golden_conn,
            "SELECT CONCAT(c.subject, ' ', c.content) FROM tiqora_gi_debug_content c"
            " JOIN tiqora_gi_debug_entry e ON e.id = c.entry_id WHERE e.communication_id = %s",
            (outcome.communication_id,),
        )
    )
    assert "Request params from outgoing data" in tiqora_content
    for secret in (BASIC_PASSWORD, BASIC_TOKEN, HEADER_SECRET):
        assert secret not in tiqora_content


@pytest.mark.asyncio
async def test_get_query_parity(golden_session_factory, ticket_id: int) -> None:
    event = {"TicketID": ticket_id, "Event": "TicketStateUpdate"}
    result = znuny_requester_run(REST_WS, "GetStatus", event)
    assert result["Success"] == 1, result
    z_requests = gisink_requests()
    gisink_clear()
    outcome = await _tiqora_run(golden_session_factory, REST_WS, "GetStatus", dict(event))
    assert outcome.success, outcome.error_message
    t_requests = gisink_requests()

    assert len(z_requests) == len(t_requests) == 1
    z_view, t_view = _request_view(z_requests[0]), _request_view(t_requests[0])
    assert z_view["method"] == "GET"
    assert ("State", "new") in [tuple(pair) for pair in z_view["query"]]
    assert z_view == t_view, f"GET request differs:\nznuny:  {z_view}\ntiqora: {t_view}"


_SOAP_RESPONSE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soap:Body>"
    '<SendSoapResponse xmlns="http://ws.example.org/Golden"><Result>ok</Result></SendSoapResponse>'
    "</soap:Body></soap:Envelope>"
)


def _c14n(xml: str) -> str:
    return etree.tostring(etree.fromstring(xml.encode("utf-8")), method="c14n").decode("utf-8")


@pytest.mark.asyncio
async def test_soap_envelope_parity(golden_session_factory, ticket_id: int) -> None:
    event = {"TicketID": ticket_id, "Event": "TicketCreate"}
    gisink_respond(_SOAP_RESPONSE, "text/xml; charset=utf-8")
    result = znuny_requester_run(SOAP_WS, "SendSoap", event)
    assert result["Success"] == 1, result
    z_requests = gisink_requests()
    gisink_clear()
    gisink_respond(_SOAP_RESPONSE, "text/xml; charset=utf-8")
    outcome = await _tiqora_run(golden_session_factory, SOAP_WS, "SendSoap", dict(event))
    assert outcome.success, outcome.error_message
    t_requests = gisink_requests()

    assert len(z_requests) == len(t_requests) == 1
    z_headers = {k.lower(): v for k, v in z_requests[0]["headers"].items()}
    t_headers = {k.lower(): v for k, v in t_requests[0]["headers"].items()}
    assert (
        z_headers["soapaction"]
        == t_headers["soapaction"]
        == '"http://ws.example.org/Golden/SendSoap"'
    )
    assert z_requests[0]["method"] == t_requests[0]["method"] == "POST"
    assert z_requests[0]["path"] == t_requests[0]["path"] == "/soap"

    z_xml, t_xml = _c14n(z_requests[0]["body"]), _c14n(t_requests[0]["body"])
    # The ticket id and number-like values are per-ticket; both sides used the same ticket, so
    # the canonical XML is compared verbatim: namespaces, element order (Sort), values.
    assert z_xml == t_xml, f"SOAP envelope differs:\nznuny:  {z_xml}\ntiqora: {t_xml}"
    root = etree.fromstring(z_requests[0]["body"].encode("utf-8"))
    names = [etree.QName(el).localname for el in root.iter() if el.getparent() is not None]
    order = [n for n in names if n in {"Subject", "TicketID", "Event", "Queue"}]
    assert order == ["Subject", "TicketID", "Event", "Queue"]


def _parse_stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _znuny_deltas(reschedule: dict[str, Any]) -> tuple[int, int, int, int | None]:
    past = reschedule["PastExecutionData"]
    initial = _parse_stamp(past["InitialRequestDateTime"])
    return (
        int((_parse_stamp(reschedule["ExecutionTime"]) - initial).total_seconds()),
        int((_parse_stamp(past["RetryDateTime"]) - initial).total_seconds()),
        int(past["RetryCount"]),
        int(past["RetryInterval"]) if past.get("RetryInterval") is not None else None,
    )


@pytest.mark.asyncio
async def test_retry_schedule_parity(
    golden_session_factory, ticket_id: int, tmp_path: Path
) -> None:
    """503 answers: Znuny's ReSchedule data equals Tiqora's, over a three-step chain with
    start 60 s / factor 2 / max three retries (60, 120, 240, then give up)."""
    # The error handling module only filters the perform stage; /status/503 is a path, so the
    # invoker controller must point there: use a one-off copy of the webservice config.
    event = {"TicketID": ticket_id, "Event": "TicketCreate"}
    _import_status_variant(tmp_path)

    z_past: dict[str, Any] | None = None
    z_steps: list[tuple[int, int, int, int | None]] = []
    t_past: PastExecutionData | None = None
    t_steps: list[tuple[int, int, int, int | None]] = []
    clock = datetime(2026, 10, 12, 8, 0, 0)
    for _step in range(3):
        z = znuny_requester_run(REST_WS + "Status", "SendTicket", event, past=z_past)
        assert z["Success"] == 0 and z["Data"]["ReSchedule"] == 1, z
        z_steps.append(_znuny_deltas(z["Data"]))
        z_past = z["Data"]["PastExecutionData"]

        outcome = await _tiqora_run(
            golden_session_factory,
            REST_WS + "Status",
            "SendTicket",
            dict(event),
            past=t_past,
            now=clock,
        )
        assert not outcome.success
        decision = outcome.reschedule
        assert decision is not None and decision.reschedule
        data = decision.reschedule_data()
        t_steps.append(_znuny_deltas(data))
        t_past = decision.past
        # next call happens when the previous retry is due (Tiqora: that is the new clock)
        assert decision.execution_time is not None
        clock = decision.execution_time

    assert z_steps == t_steps, f"retry schedule differs:\nznuny:  {z_steps}\ntiqora: {t_steps}"
    assert z_steps[:3] == [(60, 60, 1, 60), (180, 180, 2, 120), (420, 420, 3, 240)]

    # fourth failure: RetryCountMax 3 reached -> no reschedule on both sides
    z_last = znuny_requester_run(REST_WS + "Status", "SendTicket", event, past=z_past)
    t_last = await _tiqora_run(
        golden_session_factory,
        REST_WS + "Status",
        "SendTicket",
        dict(event),
        past=t_past,
        now=clock,
    )
    assert z_last["Data"]["ReSchedule"] == 0
    assert t_last.reschedule is None


def _import_status_variant(tmp_dir: Path) -> None:
    """``GoldenRequesterRESTStatus``: the REST webservice with ``SendTicket`` pointed at the
    sink's ``/status/503`` path, so every call fails and the retry module runs."""
    config = yaml.safe_load((GOLDEN_DIR / "webservices" / f"{REST_WS}.yml").read_text())
    mapping = config["Requester"]["Transport"]["Config"]["InvokerControllerMapping"]
    mapping["SendTicket"]["Controller"] = "/status/503"
    source = tmp_dir / f"{REST_WS}Status.yml"
    source.write_text(yaml.safe_dump(config))
    _import_webservice(f"{REST_WS}Status", source)


_CONDITIONS: dict[str, dict[str, Any]] = {
    "string-match": {
        "ConditionLinking": "and",
        "Condition": {
            "1": {
                "Type": "and",
                "Fields": {"Queue": {"Type": "String", "Match": "Golden"}},
            }
        },
    },
    "string-mismatch": {
        "ConditionLinking": "and",
        "Condition": {
            "1": {"Type": "and", "Fields": {"State": {"Type": "String", "Match": "closed"}}}
        },
    },
    "regexp-title": {
        "ConditionLinking": "and",
        "Condition": {
            "1": {
                "Type": "and",
                "Fields": {"Title": {"Type": "Regexp", "Match": "^Golden\\s+requester"}},
            }
        },
    },
    # serialize_config flattens nested data to Articles_0_Subject; Znuny's "Array" field type
    # dies on such scalar keys (strict refs), so it is not part of the parity set.
    "serialized-nested-key": {
        "ConditionLinking": "and",
        "Condition": {
            "1": {
                "Type": "and",
                "Fields": {"Articles_0_Subject": {"Type": "String", "Match": "First article"}},
            }
        },
    },
    "or-linking": {
        "ConditionLinking": "or",
        "Condition": {
            "1": {"Type": "and", "Fields": {"State": {"Type": "String", "Match": "closed"}}},
            "2": {"Type": "and", "Fields": {"Priority": {"Type": "String", "Match": "3 normal"}}},
        },
    },
    "and-two-fields-one-wrong": {
        "ConditionLinking": "and",
        "Condition": {
            "1": {
                "Type": "and",
                "Fields": {
                    "Queue": {"Type": "String", "Match": "Golden"},
                    "Priority": {"Type": "String", "Match": "5 very high"},
                },
            }
        },
    },
}

_CONDITION_PERL = r"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {LogPrefix => 'golden-gi-requester'},
);
my $JSON = $Kernel::OM->Get('Kernel::System::JSON');
my $Conditions = $JSON->Decode(Data => <<'GOLDEN_JSON_END');
__CONDITIONS__
GOLDEN_JSON_END
require Kernel::GenericInterface::Event::Handler;
my $Handler = Kernel::GenericInterface::Event::Handler->new();
my %Ticket = $Kernel::OM->Get('Kernel::System::Ticket')->TicketDeepGet(
    TicketID => __TICKET__, UserID => 1,
);
my %Serialized;
$Handler->_SerializeConfig(Data => \%Ticket, SHash => \%Serialized);
my %Out;
for my $Name (sort keys %{$Conditions}) {
    my $Result = $Handler->_ConditionCheck(%{ $Conditions->{$Name} }, Data => \%Serialized);
    $Out{$Name} = $Result ? 1 : 0;
}
print "GOLDEN_RESULT_BEGIN\n" . $JSON->Encode(Data => \%Out) . "\nGOLDEN_RESULT_END\n";
"""


@pytest.mark.asyncio
async def test_condition_parity(golden_session_factory, ticket_id: int) -> None:
    code = _CONDITION_PERL.replace("__CONDITIONS__", json.dumps(_CONDITIONS)).replace(
        "__TICKET__", str(ticket_id)
    )
    out = znuny_perl_eval(code)
    znuny_results = json.loads(
        out[out.index("GOLDEN_RESULT_BEGIN") : out.index("GOLDEN_RESULT_END")].split("\n", 1)[1]
    )

    async with golden_session_factory() as session:
        deep = await ticket_deep_get(
            session, ticket_id, article_id=None, all_attachments=False, sysconfig=SysConfig(session)
        )
    assert deep is not None
    serialized = serialize_config(deep)
    tiqora_results = {
        name: int(
            condition_check(condition, serialized, invoker="SendTicket", event="TicketCreate")
        )
        for name, condition in _CONDITIONS.items()
    }
    # the conditions are chosen so that both outcomes occur
    assert set(znuny_results.values()) == {0, 1}
    assert znuny_results == tiqora_results, (znuny_results, tiqora_results)


_HISTORY_SQL = (
    "SELECT t.name FROM ticket_history h"
    " JOIN ticket_history_type t ON t.id = h.history_type_id"
    " WHERE h.ticket_id = %s ORDER BY h.id"
)


@pytest.mark.asyncio
async def test_response_actions_parity(golden_session_factory, golden_conn) -> None:
    """The remote's answer drives ticket changes; both sides leave the same history types."""
    answer = {
        "OTRS_TicketTitleUpdate": "Golden",
        "OTRS_TicketDynamicFieldSet_GoldenDF": "x",
        "OTRS_TicketPrioritySet": "4 high",
    }
    z_ticket = _create_ticket("Golden actions znuny", articles=1, attachment=False)
    t_ticket = _create_ticket("Golden actions tiqora", articles=1, attachment=False)
    base_z = len(_db_rows(golden_conn, _HISTORY_SQL, (z_ticket,)))
    base_t = len(_db_rows(golden_conn, _HISTORY_SQL, (t_ticket,)))

    gisink_respond(json.dumps(answer))
    result = znuny_requester_run(
        REST_WS, "SendTicket", {"TicketID": z_ticket, "Event": "TicketCreate"}
    )
    assert result["Success"] == 1, result
    outcome = await _tiqora_run(
        golden_session_factory,
        REST_WS,
        "SendTicket",
        {"TicketID": t_ticket, "Event": "TicketCreate"},
    )
    assert outcome.success, outcome.error_message

    z_types = [row[0] for row in _db_rows(golden_conn, _HISTORY_SQL, (z_ticket,))][base_z:]
    t_types = [row[0] for row in _db_rows(golden_conn, _HISTORY_SQL, (t_ticket,))][base_t:]
    assert z_types, "Znuny applied no response action"
    assert "TicketDynamicFieldUpdate" in z_types and "TitleUpdate" in z_types
    assert z_types == t_types, f"history types differ:\nznuny:  {z_types}\ntiqora: {t_types}"
    for ticket, expected in ((z_ticket, "Golden"), (t_ticket, "Golden")):
        title = _db_rows(golden_conn, "SELECT title FROM ticket WHERE id = %s", (ticket,))[0][0]
        assert _decode(title) == expected
