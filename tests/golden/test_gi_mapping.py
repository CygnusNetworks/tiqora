"""Golden-master: GenericInterface mapping, TicketGet shape and GI history parity.

A webservice (``webservices/GoldenMappingREST.yml``) with Simple, XSLT and
OutOfOffice mappings is imported ONCE into the real Znuny peer. Because both
sides share the database, Tiqora serves the very same ``gi_webservice_config``
row. Every operation is called over HTTP on both sides with the same input
(Znuny through Apache, Tiqora through ``httpx.ASGITransport`` on its real app)
and the responses are compared after :func:`normalize`.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
import pytest_asyncio
from _helpers import GOLDEN_DIR, tiqora_http_client, znuny_console, znuny_copy_in, znuny_perl_eval

pytestmark = pytest.mark.golden

WEBSERVICE = "GoldenMappingREST"
ZNUNY_BASE = f"http://127.0.0.1:8180/otrs/nph-genericinterface.pl/Webservice/{WEBSERVICE}"
TIQORA_BASE = f"/znuny-compat/Webservice/{WEBSERVICE}"
REMOTE_YML = "/tmp/GoldenMappingREST.yml"  # noqa: S108 - path inside the peer container
DF_NAME = "GoldenGiText"

LOGIN = {"User": "golden.agent", "Pass": "golden-agent-pw"}

# ---------------------------------------------------------------------------
# normalizer
# ---------------------------------------------------------------------------

_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}Z?")
_MESSAGE_ID_RE = re.compile(r"<[^<>@\s]+@[^<>\s]+>")
# Keys whose values are ticket/article ids or numbers allocated per call, so they differ on the
# two sides. TicketNumber / ArticleNumber are here because the ForceArray mapping below renames
# TicketID -> TicketNumber and ArticleID -> ArticleNumber over them (both sides do the same).
# Every other ``*Number`` key (OwnerNumber, QueueNumber, ...) is a shared-DB id and is compared.
_VOLATILE_KEYS = frozenset(
    {
        "TicketID",
        "TicketNumber",
        "ArticleID",
        "ArticleNumber",
        "ArticleIDs",
        "SessionID",
        "Id",
        "Age",
        "MessageID",
    }
)


# Deviation 6: Tiqora does not compute the calendar-based escalation detail keys of
# Znuny's TicketEscalationDateCalculation (Znuny returns them, Tiqora omits them): the
# working-time and destination keys need a calendar working-time Delta. They are dropped on
# BOTH sides. ``EscalationTime`` (seconds to the nearest destination) is NOT dropped: Tiqora
# computes it like Znuny, and the stored Escalation{Response,Update,Solution}Time columns
# stay compared too.
_DEVIATION_6_KEYS = frozenset(
    {
        "EscalationTimeWorkingTime",
        "EscalationDestinationIn",
        "EscalationDestinationTime",
        "EscalationDestinationDate",
    }
    | {
        f"{prefix}{suffix}"
        for prefix in ("FirstResponseTime", "UpdateTime", "SolutionTime")
        for suffix in ("", "WorkingTime", "DestinationTime", "DestinationDate")
    }
)


def normalize(value: Any, key: str = "") -> Any:
    """Compare-friendly view of a response body.

    * every scalar becomes a string (Deviation 9: Perl strings vs Python ints),
    * the Deviation 6 escalation working-time/destination keys are dropped,
    * per-call ids/numbers (``_VOLATILE_KEYS``), timestamps, ``Age`` and
      ``SessionID`` become placeholders,
    * empty containers/strings collapse to ``""`` (XML mapping turns ``{}`` and
      ``""`` into the same empty element).
    """
    if isinstance(value, dict):
        out = {k: normalize(v, k) for k, v in value.items() if k not in _DEVIATION_6_KEYS}
        return out or ""
    if isinstance(value, list):
        return [normalize(v, key) for v in value] or ""
    if value is None:
        return ""
    text = str(value)
    if key in _VOLATILE_KEYS:
        return f"<{key}>" if text else ""
    if _TIMESTAMP_RE.fullmatch(text):
        return "<timestamp>"
    return _MESSAGE_ID_RE.sub("<message-id>", text)


def test_normalize_self_check() -> None:
    body = {
        "TicketID": 12,
        "Age": 3,
        "Created": "2026-03-01 12:00:00",
        "Empty": {},
        "Rows": [{"ArticleID": 5, "Subject": "x", "N": 7}],
        "Stamp": "2026-03-01T12:34:56Z",
        "EscalationDestinationIn": "1h 0m",
    }
    assert normalize(body) == {
        "TicketID": "<TicketID>",
        "Age": "<Age>",
        "Created": "<timestamp>",
        "Empty": "",
        "Rows": [{"ArticleID": "<ArticleID>", "Subject": "x", "N": "7"}],
        "Stamp": "<timestamp>",
    }


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

_ENSURE_DF_PERL = rf"""
use lib qw(/opt/otrs /opt/otrs/Kernel/cpan-lib /opt/otrs/Custom);
use Kernel::System::ObjectManager;
local $Kernel::OM = Kernel::System::ObjectManager->new(
    'Kernel::System::Log' => {{LogPrefix => 'golden-gi'}},
);
$Kernel::OM->Get('Kernel::System::Cache')->CleanUp();
my $DF = $Kernel::OM->Get('Kernel::System::DynamicField');
my $Field = $DF->DynamicFieldGet(Name => '{DF_NAME}');
my $ID = ref $Field ? $Field->{{ID}} : undef;
if (!$ID) {{
    $ID = $DF->DynamicFieldAdd(
        Name => '{DF_NAME}', Label => 'Golden GI text', FieldOrder => 9001,
        FieldType => 'Text', ObjectType => 'Ticket',
        Config => {{DefaultValue => '', Link => '', LinkPreview => ''}},
        ValidID => 1, UserID => 1,
    );
}}
print "id=" . ($ID // 'none');
"""


@pytest.fixture(scope="module", autouse=True)
def _peer_setup() -> None:
    """Import the webservice and create the dynamic field in the peer (idempotent)."""
    znuny_copy_in(GOLDEN_DIR / "webservices" / "GoldenMappingREST.yml", REMOTE_YML)
    try:
        znuny_console(
            "Admin::WebService::Add", f"--name={WEBSERVICE}", f"--source-path={REMOTE_YML}"
        )
    except RuntimeError as exc:
        # only a webservice left by an earlier run is fine; any other failure must surface
        if "already exists" not in str(exc):
            raise
    assert re.fullmatch(r"id=\d+", znuny_perl_eval(_ENSURE_DF_PERL).strip())
    # The seeded agent is only in group admin; the Golden queue belongs to group users.
    for permission in ("ro", "move_into", "create", "owner", "priority", "rw"):
        znuny_console(
            "Admin::Group::UserLink",
            "--user-name=golden.agent",
            "--group-name=users",
            f"--permission={permission}",
        )


@pytest_asyncio.fixture()
async def tiqora(golden_session_factory):
    async with tiqora_http_client(golden_session_factory) as client:
        yield client


@pytest_asyncio.fixture()
async def znuny():
    async with httpx.AsyncClient(timeout=60) as client:
        yield client


async def _call(
    side: str, client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> tuple[int, Any]:
    base = ZNUNY_BASE if side == "znuny" else TIQORA_BASE
    resp = await client.request(method, f"{base}{path}", **kwargs)
    try:
        body = resp.json()
    except ValueError:
        body = {"_text": resp.text}
    return resp.status_code, body


async def _login(side: str, client: httpx.AsyncClient) -> str:
    status, body = await _call(side, client, "POST", "/Login", json=LOGIN)
    assert status == 200, body
    assert "Error" not in body, body
    return str(body["SessionID"])


_TICKET = {
    "Title": "Golden GI mapping ticket",
    "Queue": "Golden",
    "State": "new",
    "Priority": "3 normal",
    "CustomerUser": "golden.customer",
}
_ARTICLE = {
    "CommunicationChannel": "Internal",
    "SenderType": "agent",
    "IsVisibleForCustomer": 0,
    "Subject": "Golden GI note",
    "Body": "Golden GI note body",
    "ContentType": "text/plain; charset=utf8",
}


def _make_payload(session_id: str, **ticket_extra: Any) -> dict[str, Any]:
    return {
        "SessionID": session_id,
        "Ticket": {**_TICKET, **ticket_extra},
        "Article": dict(_ARTICLE),
    }


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_simple_mapping(znuny, tiqora) -> None:
    z = await _call("znuny", znuny, "POST", "/Login", json=LOGIN)
    t = await _call("tiqora", tiqora, "POST", "/Login", json=LOGIN)
    assert z[0] == t[0] == 200, (z, t)
    assert normalize(z[1]) == normalize(t[1]) == {"SessionID": "<SessionID>"}
    # The un-mapped Znuny names are not accepted: the mapping really ran.
    z_bad = await _call("znuny", znuny, "POST", "/Login", json={"UserLogin": "x", "Password": "y"})
    t_bad = await _call(
        "tiqora", tiqora, "POST", "/Login", json={"UserLogin": "x", "Password": "y"}
    )
    assert "Error" in z_bad[1] and "Error" in t_bad[1]
    assert normalize(z_bad[1]) == normalize(t_bad[1])


@pytest.mark.asyncio
async def test_make_xslt_outbound_mapping(znuny, tiqora) -> None:
    z_sid = await _login("znuny", znuny)
    t_sid = await _login("tiqora", tiqora)
    z = await _call("znuny", znuny, "POST", "/Make", json=_make_payload(z_sid))
    t = await _call("tiqora", tiqora, "POST", "/Make", json=_make_payload(t_sid))
    assert z[0] == t[0] == 200, (z, t)
    assert "Error" not in z[1] and "Error" not in t[1], (z, t)
    assert "TicketID" not in z[1], "XSLT must rename TicketID to Id"
    assert normalize(z[1]) == normalize(t[1])
    assert normalize(t[1])["Stamp"] == "<timestamp>"
    # Same constant on both sides, not just "some timestamp".
    assert z[1]["Stamp"] == t[1]["Stamp"] == "2026-03-01T12:34:56Z"


@pytest.mark.asyncio
async def test_fetch_ticket_get_shape(znuny, tiqora) -> None:
    """Both sides serve the SAME Znuny-created ticket through the Fetch mapping.

    Reading one ticket on both sides isolates the TicketGet response shape from any
    difference in how each side creates tickets (see the create-default tests below).
    """
    z_sid = await _login("znuny", znuny)
    t_sid = await _login("tiqora", tiqora)
    made = await _call("znuny", znuny, "POST", "/Make", json=_make_payload(z_sid))
    assert "Error" not in made[1], made
    ticket_id = made[1]["Id"]

    params = {"AllArticles": 1, "DynamicFields": 1}
    z = await _call(
        "znuny", znuny, "GET", f"/Fetch/{ticket_id}", params={**params, "SessionID": z_sid}
    )
    t = await _call(
        "tiqora", tiqora, "GET", f"/Fetch/{ticket_id}", params={**params, "SessionID": t_sid}
    )
    assert z[0] == t[0] == 200, (z, t)
    assert "Error" not in z[1] and "Error" not in t[1], (z, t)
    # ForceArray: a single article is still a list on both sides.
    assert isinstance(z[1]["Ticket"]["Article"], list)
    assert isinstance(t[1]["Ticket"]["Article"], list)
    # PostRegExFilter ^(\D*)ID$ -> $1Number renamed the *ID keys on both sides.
    assert "QueueNumber" in z[1]["Ticket"] and "QueueNumber" in t[1]["Ticket"]
    assert "QueueID" not in z[1]["Ticket"] and "QueueID" not in t[1]["Ticket"]
    zn, tn = normalize(z[1]), normalize(t[1])
    assert zn == tn, f"TicketGet shape differs:\nznuny:  {zn}\ntiqora: {tn}"


async def _created_ticket(side: str, client: httpx.AsyncClient, znuny_client, **ticket_extra: Any):
    """Create a ticket on ``side`` and read it back through the Znuny peer."""
    sid = await _login(side, client)
    made = await _call(side, client, "POST", "/Make", json=_make_payload(sid, **ticket_extra))
    assert "Error" not in made[1], made
    z_sid = await _login("znuny", znuny_client)
    got = await _call(
        "znuny",
        znuny_client,
        "GET",
        f"/Fetch/{made[1]['Id']}",
        params={"SessionID": z_sid, "AllArticles": 1},
    )
    assert "Error" not in got[1], got
    return got[1]["Ticket"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "extract"),
    [
        pytest.param(
            "owner-defaults-to-session-agent",
            lambda t: (t["Owner"], t["OwnerNumber"], t["Lock"]),
            id="owner",
        ),
        pytest.param(
            "customer-id-from-customer-user",
            lambda t: t["CustomerNumber"],
            id="customer-id",
        ),
        pytest.param(
            "article-to-is-queue-name",
            lambda t: t["Article"][0]["To"],
            id="article-to",
        ),
    ],
)
async def test_gi_create_defaults_match_znuny(znuny, tiqora, field, extract) -> None:
    z_ticket = await _created_ticket("znuny", znuny, znuny)
    t_ticket = await _created_ticket("tiqora", tiqora, znuny)
    assert extract(z_ticket) == extract(t_ticket), field


@pytest.mark.asyncio
async def test_away_out_of_office_mapping(znuny, tiqora) -> None:
    csv = "UserLogin,StartDate,EndDate,OutOfOffice\ngolden.agent,2026-03-01,2026-03-02,yes\n"
    z_sid = await _login("znuny", znuny)
    t_sid = await _login("tiqora", tiqora)
    z = await _call(
        "znuny",
        znuny,
        "POST",
        "/Away",
        json={"SessionID": z_sid, "OutOfOfficeEntriesCSVString": csv},
    )
    t = await _call(
        "tiqora",
        tiqora,
        "POST",
        "/Away",
        json={"SessionID": t_sid, "OutOfOfficeEntriesCSVString": csv},
    )
    assert z[0] == t[0] == 200, (z, t)
    assert "Error" not in z[1] and "Error" not in t[1], (z, t)
    assert normalize(z[1]) == normalize(t[1])


# ---------------------------------------------------------------------------
# history parity of a GI TicketCreate
# ---------------------------------------------------------------------------


def _history(golden_conn, ticket_id: int) -> list[tuple[str, str]]:
    """``(history type, name)`` rows of a ticket, ticket id and number masked.

    Only the ticket's own id and number differ between the two sides (everything else, user
    ids, queue/state ids, CustomerID, time units, is a shared-DB value and stays compared).
    ``Misc "Reset of unlock time."`` stays in (see ``_without_lock_unlock_reset`` for the one
    timing-dependent occurrence).
    """
    with golden_conn.cursor() as cur:
        cur.execute("SELECT tn FROM ticket WHERE id = %s", (ticket_id,))
        tn = cur.fetchone()[0]
        cur.execute(
            """
            SELECT t.name, h.name
            FROM ticket_history h
            JOIN ticket_history_type t ON t.id = h.history_type_id
            WHERE h.ticket_id = %s
            ORDER BY h.id ASC
            """,
            (ticket_id,),
        )
        rows = cur.fetchall()
    out = []
    for type_name, name in rows:
        for value, mask in ((tn, "<tn>"), (str(ticket_id), "<ticket-id>")):
            name = re.sub(rf"(?<!\d){re.escape(str(value))}(?!\d)", mask, name)
        out.append((type_name, name))
    return out


def _without_lock_unlock_reset(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop the ``Reset of unlock time.`` row that directly follows a ``Lock`` row.

    The lock restarts the unlock timeout at the current epoch second; Znuny
    (Ticket.pm:1934) writes the row only when that differs from the value the agent article
    stored a moment earlier, i.e. only when the request crossed a second boundary. The row
    that follows the article is deterministic and stays compared; the Lock-then-reset order is
    covered without a peer by backend/tests/test_ticket_lock_parity.py.
    """
    out: list[tuple[str, str]] = []
    for row in rows:
        if row == ("Misc", "Reset of unlock time.") and out and out[-1][0] == "Lock":
            continue
        out.append(row)
    return out


@pytest_asyncio.fixture()
async def create_histories(znuny, tiqora, golden_conn):
    """History of one GI TicketCreate (Owner, Article.TimeUnit, one DF) on each side."""
    z_sid = await _login("znuny", znuny)
    t_sid = await _login("tiqora", tiqora)

    def payload(sid: str) -> dict[str, Any]:
        body = _make_payload(sid, Owner="golden.agent")
        body["Article"]["TimeUnit"] = 3
        body["DynamicField"] = {"Name": DF_NAME, "Value": "golden value"}
        return body

    z = await _call("znuny", znuny, "POST", "/Make", json=payload(z_sid))
    t = await _call("tiqora", tiqora, "POST", "/Make", json=payload(t_sid))
    assert "Error" not in z[1] and "Error" not in t[1], (z, t)
    return (
        _without_lock_unlock_reset(_history(golden_conn, int(z[1]["Id"]))),
        _without_lock_unlock_reset(_history(golden_conn, int(t[1]["Id"]))),
    )


@pytest.mark.asyncio
async def test_gi_create_history_type_sequence(create_histories) -> None:
    z_hist, t_hist = create_histories
    assert [h[0] for h in z_hist] == [h[0] for h in t_hist], (
        f"history_type sequence differs:\nznuny:  {[h[0] for h in z_hist]}\n"
        f"tiqora: {[h[0] for h in t_hist]}"
    )
    assert any(h[0] == "TicketDynamicFieldUpdate" for h in z_hist)
    assert any(h[0] == "TimeAccounting" for h in z_hist)
    # TicketOwnerSet and the owner lock come after the article (TicketCreate.pm:1853-1870)
    z_types = [h[0] for h in z_hist]
    assert z_types.index("OwnerUpdate") < z_types.index("Lock")


@pytest.mark.asyncio
async def test_gi_create_history_owner_update_name(create_histories) -> None:
    z_hist, t_hist = create_histories
    assert [h for h in z_hist if h[0] == "OwnerUpdate"] == [
        h for h in t_hist if h[0] == "OwnerUpdate"
    ]


@pytest.mark.asyncio
async def test_gi_create_history_customer_update_name(create_histories) -> None:
    z_hist, t_hist = create_histories
    z_names = [h for h in z_hist if h[0] == "CustomerUpdate"]
    assert z_names, "the golden ticket template names a customer user"
    assert z_names == [h for h in t_hist if h[0] == "CustomerUpdate"]


@pytest.mark.asyncio
async def test_gi_create_history_lock_and_unlock_time_rows(create_histories) -> None:
    z_hist, t_hist = create_histories
    keep = {("Lock", "%%lock"), ("Misc", "Reset of unlock time.")}
    assert [h for h in z_hist if h in keep] == [h for h in t_hist if h in keep]
    assert ("Misc", "Reset of unlock time.") in z_hist  # the article's reset is always written


@pytest.mark.asyncio
async def test_gi_create_history_remaining_names(create_histories) -> None:
    z_hist, t_hist = create_histories
    special = {"OwnerUpdate", "CustomerUpdate", "Lock"}
    assert [h for h in z_hist if h[0] not in special] == [h for h in t_hist if h[0] not in special]
