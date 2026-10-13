"""Golden: GenericInterface ``ConfigItem*`` and the ``ConfigItemUpdate`` process action (Task 28).

Znuny's own ``GenericConfigItemConnectorREST`` (the five ``ConfigItem::*`` operations of the
ITSMConfigurationManagement package, imported from ``webservices/GoldenConfigItemREST.yml``
with ``Admin::WebService::Add``) is called on the peer at
``/otrs/nph-genericinterface.pl/Webservice/GoldenConfigItemREST``. Tiqora serves the same
webservice row (``/znuny-compat/Webservice/GoldenConfigItemREST``, and its canonical routes
``/znuny-compat/ConfigItem...``) after the importer copied the CIs and switched the site to Mode B
(``itsm.mode = tiqora``). Tiqora's CI ids are the importer's (``tiqora_itsm_import_map``); the
answers are compared modulo ids and, after writes, times.

    GOLDEN_PEER=znuny-6.5-itsm just golden-up && GOLDEN_PEER=znuny-6.5-itsm just golden-seed
    cd backend && GOLDEN=1 GOLDEN_PEER=znuny-6.5-itsm uv run python -m pytest -q -m golden \\
        ../tests/golden/test_gi_config_items.py

The read-only cases come first, the writing cases (``ConfigItemUpdate`` on ``db01``, the process
action on ``app01``) last. The module takes a snapshot of the Tiqora tables the importer writes and
of the ``itsm.*`` settings and restores both, deletes its Znuny CIs and ticket and the webservice.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import pytest_asyncio
from _helpers import GOLDEN_DIR, tiqora_http_client, znuny_console, znuny_copy_in
from _itsm_import_support import (
    AGENT_LOGIN,
    AGENT_PASSWORD,
    TiqoraSnapshot,
    create_scenario,
    delete_scenario,
    ensure_agent,
    run_importer_with_id,
    znuny_json,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import TiqoraCmdbCiVersion, TiqoraItsmImportMap
from tiqora.itsm.mode import get_itsm_mode, switch_to_tiqora
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.process.config import TransitionActionConfig
from tiqora.process.engine import execute_transition_action
from tiqora.znuny.sysconfig import SysConfig

pytestmark = [pytest.mark.golden, pytest.mark.itsm_peer]

PREFIX = "goldgi"
WEBSERVICE = "GoldenConfigItemREST"
REMOTE_YML = f"/tmp/{WEBSERVICE}.yml"  # noqa: S108 - path inside the peer container
ZNUNY_BASE = f"http://127.0.0.1:8180/otrs/nph-genericinterface.pl/Webservice/{WEBSERVICE}"
#: Tiqora serves the very same ``gi_webservice_config`` row (shared database) as Znuny does ...
TIQORA_BASE = f"/znuny-compat/Webservice/{WEBSERVICE}"
#: ... and its canonical routes (no webservice needed) answer the same operations
TIQORA_CANONICAL = "/znuny-compat"
AUTH = {"UserLogin": AGENT_LOGIN, "Password": AGENT_PASSWORD}
SessionFactory = async_sessionmaker[AsyncSession]

#: ids that differ between the sides (Tiqora allocates its own); asserted where it matters
_ID_KEYS = frozenset({"ConfigItemID", "VersionID", "LastVersionID", "DefinitionID"})
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


@dataclass
class Flow:
    znuny: dict[str, Any] = field(default_factory=dict)
    imported: bool = False
    ci_map: dict[int, int] = field(default_factory=dict)  # znuny ci id -> tiqora ci id

    def zid(self, name: str) -> int:
        return int(self.znuny["cis"][name]["id"])

    def tid(self, name: str) -> int:
        return self.ci_map[self.zid(name)]

    def to_znuny(self, tiqora_id: int | str) -> int:
        reverse = {t: z for z, t in self.ci_map.items()}
        return reverse[int(tiqora_id)]


# --- fixtures ---------------------------------------------------------------------------------


def _delete_webservice() -> None:
    """Remove the webservice row (``Admin::WebService::Delete`` takes an id, not a name)."""
    znuny_json(
        f"""
my $Webservice = $Kernel::OM->Get('Kernel::System::GenericInterface::Webservice');
my $Row = $Webservice->WebserviceGet( Name => '{WEBSERVICE}' );
$Webservice->WebserviceDelete( ID => $Row->{{ID}}, UserID => 1 ) if $Row;
Emit({{ deleted => $Row ? 1 : 0 }});
"""
    )


@pytest.fixture(scope="module")
def flow() -> Flow:
    return Flow()


@pytest.fixture(scope="module", autouse=True)
def _module_setup(golden_pymysql_dsn: dict[str, object], flow: Flow) -> Generator[None, None, None]:
    """Scenario CIs, the agent and the webservice in the peer; undo everything at the end."""
    snapshot = TiqoraSnapshot.take(golden_pymysql_dsn)  # type: ignore[arg-type]
    delete_scenario(PREFIX)  # leftovers of an aborted run
    clear_detect_cache()
    try:
        flow.znuny = create_scenario(PREFIX)
        ensure_agent()
        znuny_copy_in(GOLDEN_DIR / "webservices" / f"{WEBSERVICE}.yml", REMOTE_YML)
        _delete_webservice()  # a row of an aborted run
        znuny_console(
            "Admin::WebService::Add", f"--name={WEBSERVICE}", f"--source-path={REMOTE_YML}"
        )
        yield
    finally:
        try:
            _delete_webservice()
            delete_scenario(PREFIX)
        finally:
            snapshot.restore()
            clear_detect_cache()


@pytest_asyncio.fixture(autouse=True)
async def _imported(golden_session_factory: SessionFactory, flow: Flow) -> None:
    """Once per module: import, then switch the site to Mode B (the importer's own path)."""
    if flow.imported:
        return
    _report, run_id = await run_importer_with_id(golden_session_factory, dry_run=False, page_size=2)
    async with golden_session_factory() as session:
        await switch_to_tiqora(session, user_id=1, import_run_id=run_id)
        await session.commit()
        rows = (await session.execute(select(TiqoraItsmImportMap))).scalars().all()
    flow.ci_map = {int(r.source_id): int(r.target_id) for r in rows if r.source_kind == "ci"}
    flow.imported = True
    clear_detect_cache()
    async with golden_session_factory() as session:
        assert (await get_itsm_mode(session, fresh=True)).mode == "tiqora"


@pytest_asyncio.fixture()
async def znuny() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=60) as client:
        yield client


@pytest_asyncio.fixture()
async def tiqora(golden_session_factory: SessionFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with tiqora_http_client(golden_session_factory) as client:
        yield client


# --- normalisation ----------------------------------------------------------------------------


def _without_empty(value: Any) -> Any:
    """Drop empty attribute values: Znuny keeps ``Vendor: ""`` after ``Vendor::1`` was cleared,
    Tiqora removes the value; both mean "no value" (documented in ``README-multi-peer.md``)."""
    if isinstance(value, dict):
        return {k: v for k, v in ((k, _without_empty(v)) for k, v in value.items()) if v != ""}
    if isinstance(value, list):
        return [v for v in (_without_empty(v) for v in value) if v != ""]
    return value


def normalize(value: Any, flow: Flow, *, side: str, times: bool = False) -> Any:
    """Compare-friendly view: ids dropped, scalars as strings, Tiqora's CI references mapped back.

    The ``Parent`` attribute of ``GoldImpAppliance`` is a CI reference: Znuny returns the Znuny
    CI id, Tiqora its own id (the importer converted it); mapped through the import map on the
    Tiqora side it must equal Znuny's. ``times`` hides the timestamps of rows written by the test.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in _ID_KEYS:
                continue
            if key == "Parent" and side == "tiqora" and isinstance(item, str) and item.isdigit():
                item = str(flow.to_znuny(item))
            out[key] = normalize(item, flow, side=side, times=times)
            if key == "CIXMLData":
                out[key] = _without_empty(out[key])
        return out
    if isinstance(value, list):
        return [normalize(v, flow, side=side, times=times) for v in value]
    if value is None:
        return ""
    text = str(value)
    if times and _TIMESTAMP.fullmatch(text):
        return "<time>"
    return text


async def _call(
    client: httpx.AsyncClient, side: str, method: str, path: str, **kwargs: Any
) -> tuple[int, Any]:
    base = ZNUNY_BASE if side == "znuny" else TIQORA_BASE
    resp = await client.request(method, f"{base}{path}", **kwargs)
    return resp.status_code, resp.json()


# --- ConfigItemGet ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["app01", "app02", "db01", "fw01", "fw02"])
async def test_config_item_get_is_equal(
    name: str, flow: Flow, znuny: httpx.AsyncClient, tiqora: httpx.AsyncClient
) -> None:
    z = await _call(znuny, "znuny", "GET", f"/ConfigItem/{flow.zid(name)}", params=AUTH)
    t = await _call(tiqora, "tiqora", "GET", f"/ConfigItem/{flow.tid(name)}", params=AUTH)
    assert z[0] == t[0] == 200, (z, t)
    assert "Error" not in z[1], z
    # the bodies carry the id of their own side; the rest is equal
    assert str(z[1]["ConfigItem"][0]["ConfigItemID"]) == str(flow.zid(name))
    assert str(t[1]["ConfigItem"][0]["ConfigItemID"]) == str(flow.tid(name))
    zn = normalize(z[1], flow, side="znuny")
    tn = normalize(t[1], flow, side="tiqora")
    assert zn == tn, f"ConfigItemGet differs:\nznuny:  {json.dumps(zn)}\ntiqora: {json.dumps(tn)}"
    # sanity: the answer really carries the imported values (not two empty bodies)
    item = zn["ConfigItem"][0]
    assert item["Name"].startswith(PREFIX)
    assert item["CIXMLData"]


async def test_config_item_get_several_ids_and_attachment(
    flow: Flow, znuny: httpx.AsyncClient, tiqora: httpx.AsyncClient
) -> None:
    z_ids = ",".join(str(flow.zid(n)) for n in ("fw01", "app01"))
    t_ids = ",".join(str(flow.tid(n)) for n in ("fw01", "app01"))
    z = await _call(
        znuny, "znuny", "GET", f"/ConfigItem/{z_ids}", params={**AUTH, "Attachments": 1}
    )
    t = await _call(
        tiqora, "tiqora", "GET", f"/ConfigItem/{t_ids}", params={**AUTH, "Attachments": 1}
    )
    assert z[0] == t[0] == 200, (z, t)
    assert len(z[1]["ConfigItem"]) == len(t[1]["ConfigItem"]) == 2
    zn = normalize(z[1], flow, side="znuny")
    tn = normalize(t[1], flow, side="tiqora")
    assert zn == tn, f"znuny:  {json.dumps(zn)}\ntiqora: {json.dumps(tn)}"
    attachments = [a for item in zn["ConfigItem"] for a in item.get("Attachment", [])]
    assert [a["Filename"] for a in attachments] == ["golden-binary.bin"]
    assert attachments[0]["Content"]  # base64 of the same bytes on both sides (compared above)


# --- ConfigItemSearch -------------------------------------------------------------------------


_SEARCHES: list[tuple[str, dict[str, Any]]] = [
    ("class-and-name", {"Class": "Computer", "Name": f"{PREFIX}-*"}),
    ("name-prefix", {"Class": "Computer", "Name": f"{PREFIX}-app*"}),
    ("custom-class", {"Class": "GoldImpAppliance", "Name": f"{PREFIX}-*"}),
    ("class-only", {"Class": "GoldImpAppliance"}),
    ("deployment-state", {"Class": "GoldImpAppliance", "DeplStates": ["Planned"]}),
    ("deployment-state-list", {"Class": "Computer", "DeplStates": ["Production", "Planned"]}),
    ("incident-state", {"Class": "Computer", "Name": f"{PREFIX}-*", "InciStates": ["Incident"]}),
    (
        "order-limit",
        {
            "Class": "Computer",
            "Name": f"{PREFIX}-*",
            "OrderBy": ["Name"],
            "OrderByDirection": ["Down"],
            "Limit": 2,
        },
    ),
    ("number", {"Class": "GoldImpAppliance", "Number": "*"}),
]


@pytest.mark.parametrize(("label", "criteria"), _SEARCHES, ids=[s[0] for s in _SEARCHES])
async def test_config_item_search_returns_the_same_set(
    label: str,
    criteria: dict[str, Any],
    flow: Flow,
    znuny: httpx.AsyncClient,
    tiqora: httpx.AsyncClient,
) -> None:
    body = {**AUTH, "ConfigItem": criteria}
    z = await _call(znuny, "znuny", "POST", "/ConfigItemSearch", json=body)
    t = await _call(tiqora, "tiqora", "POST", "/ConfigItemSearch", json=body)
    assert z[0] == t[0] == 200, (z, t)
    assert "Error" not in z[1], (label, z)
    z_ids = [int(i) for i in z[1].get("ConfigItemIDs", [])]
    t_ids = [flow.to_znuny(i) for i in t[1].get("ConfigItemIDs", [])]
    assert z_ids, f"{label}: the search found nothing in Znuny: {z[1]}"
    if "OrderBy" in criteria:
        assert t_ids == z_ids  # the order counts
    else:
        assert sorted(t_ids) == sorted(z_ids), (label, z[1], t[1])


async def test_config_item_search_by_attribute_value(
    flow: Flow, znuny: httpx.AsyncClient, tiqora: httpx.AsyncClient
) -> None:
    body = {
        **AUTH,
        "ConfigItem": {"Class": "GoldImpAppliance", "CIXMLData": {"Label": "fw one*"}},
    }
    z = await _call(znuny, "znuny", "POST", "/ConfigItemSearch", json=body)
    t = await _call(tiqora, "tiqora", "POST", "/ConfigItemSearch", json=body)
    assert z[0] == t[0] == 200, (z, t)
    assert "Error" not in z[1], z
    z_ids = sorted(int(i) for i in z[1].get("ConfigItemIDs", []))
    t_ids = sorted(flow.to_znuny(i) for i in t[1].get("ConfigItemIDs", []))
    assert z_ids == [flow.zid("fw01")]
    assert t_ids == z_ids


# --- error bodies -----------------------------------------------------------------------------

#: a missing parameter / an unknown id with valid credentials: the bodies are identical
_ERROR_CASES: list[tuple[str, str, str, dict[str, Any]]] = [
    ("get-unknown-id", "GET", "/ConfigItem/987654", {"params": AUTH}),
    ("search-missing-config-item", "POST", "/ConfigItemSearch", {"json": dict(AUTH)}),
    (
        "search-wrong-structure",
        "POST",
        "/ConfigItemSearch",
        {"json": {**AUTH, "ConfigItem": {"Class": "Computer", "OrderBy": {"a": "b"}}}},
    ),
    ("create-missing-config-item", "POST", "/ConfigItem", {"json": dict(AUTH)}),
    ("update-missing-config-item", "PATCH", "/ConfigItem/1", {"json": dict(AUTH)}),
    ("delete-unknown-id", "DELETE", "/ConfigItem/987654", {"json": dict(AUTH)}),
]


@pytest.mark.parametrize(
    ("label", "method", "path", "kwargs"), _ERROR_CASES, ids=[c[0] for c in _ERROR_CASES]
)
async def test_error_bodies_are_identical(
    label: str,
    method: str,
    path: str,
    kwargs: dict[str, Any],
    znuny: httpx.AsyncClient,
    tiqora: httpx.AsyncClient,
) -> None:
    z = await _call(znuny, "znuny", method, path, **kwargs)
    t = await _call(tiqora, "tiqora", method, path, **kwargs)
    assert "Error" in z[1], (label, z)
    # the bodies are identical; the HTTP status is not (Znuny's REST transport answers 200 for an
    # error, Tiqora's compat layer maps the code to 4xx for every operation, tickets included)
    assert z[0] == 200
    assert t[1] == z[1], f"{label}: error differs\nznuny:  {z}\ntiqora: {t}"


async def test_canonical_routes_answer_the_same_body(
    flow: Flow, znuny: httpx.AsyncClient, tiqora: httpx.AsyncClient
) -> None:
    """The routes without a webservice (``/znuny-compat/ConfigItem/<id>``) serve the same body."""
    tid = flow.tid("fw01")
    via_ws = await _call(tiqora, "tiqora", "GET", f"/ConfigItem/{tid}", params=AUTH)
    canonical = await tiqora.get(f"{TIQORA_CANONICAL}/ConfigItem/{tid}", params=AUTH)
    assert canonical.status_code == 200
    assert canonical.json() == via_ws[1]
    z = await _call(znuny, "znuny", "GET", f"/ConfigItem/{flow.zid('fw01')}", params=AUTH)
    assert normalize(canonical.json(), flow, side="tiqora") == normalize(z[1], flow, side="znuny")


#: DOCUMENTED differences in the authentication failures (``tests/fixtures/itsm/gi_shapes.md``):
#: Znuny sends the unexpanded variable ``$Self->{OperationName}`` in the code of ``ConfigItemGet``
#: and ``ConfigItemDelete``, and ``ConfigItemSearch/Create/Update`` check ``UserLogin or SessionID``
#: and ``Password`` themselves before they authenticate. Tiqora runs the compat layer's common
#: authentication (rate limit, 2FA, sessions) and answers ``<Op>.AuthFail`` with its own text.
_AUTH_CASES: list[tuple[str, str, str, dict[str, Any], str, str]] = [
    # label, method, path, kwargs, Znuny code, Tiqora code
    (
        "get-wrong-password",
        "GET",
        "/ConfigItem/1",
        {"params": {"UserLogin": AGENT_LOGIN, "Password": "wrong"}},
        "$Self->{OperationName}.AuthFail",
        "ConfigItemGet.AuthFail",
    ),
    (
        "delete-wrong-password",
        "DELETE",
        "/ConfigItem/1",
        {"json": {"UserLogin": AGENT_LOGIN, "Password": "wrong"}},
        "$Self->{OperationName}.AuthFail",
        "ConfigItemDelete.AuthFail",
    ),
    (
        "search-no-credentials",
        "POST",
        "/ConfigItemSearch",
        {"json": {"ConfigItem": {"Name": "x"}}},
        "ConfigItemSearch.MissingParameter",
        "ConfigItemSearch.AuthFail",
    ),
    (
        "update-wrong-password",
        "PATCH",
        "/ConfigItem/1",
        {"json": {"UserLogin": AGENT_LOGIN, "Password": "wrong", "ConfigItem": {"Name": "x"}}},
        "ConfigItemUpdate.AuthFail",
        "ConfigItemUpdate.AuthFail",
    ),
]


@pytest.mark.parametrize(
    ("label", "method", "path", "kwargs", "z_code", "t_code"),
    _AUTH_CASES,
    ids=[c[0] for c in _AUTH_CASES],
)
async def test_authentication_failures_are_errors_with_documented_codes(
    label: str,
    method: str,
    path: str,
    kwargs: dict[str, Any],
    z_code: str,
    t_code: str,
    znuny: httpx.AsyncClient,
    tiqora: httpx.AsyncClient,
) -> None:
    z = await _call(znuny, "znuny", method, path, **kwargs)
    t = await _call(tiqora, "tiqora", method, path, **kwargs)
    assert z[0] == 200, z  # Znuny answers an error with HTTP 200, Tiqora with 4xx (see above)
    assert z[1]["Error"]["ErrorCode"] == z_code, z
    assert t[1]["Error"]["ErrorCode"] == t_code, t


# --- ConfigItemUpdate -------------------------------------------------------------------------


async def _version_count_znuny(zid: int) -> int:
    return len(znuny_json(f"Emit($CIObject->VersionList( ConfigItemID => {zid} ) || []);"))


async def _version_count_tiqora(factory: SessionFactory, tid: int) -> int:
    async with factory() as session:
        rows = (
            await session.execute(
                select(TiqoraCmdbCiVersion.id).where(TiqoraCmdbCiVersion.ci_id == tid)
            )
        ).all()
    return len(rows)


async def test_config_item_update_creates_one_version_with_equal_attributes(
    flow: Flow,
    golden_session_factory: SessionFactory,
    znuny: httpx.AsyncClient,
    tiqora: httpx.AsyncClient,
) -> None:
    zid, tid = flow.zid("db01"), flow.tid("db01")
    z_before = await _version_count_znuny(zid)
    t_before = await _version_count_tiqora(golden_session_factory, tid)
    assert z_before == t_before == 2
    payload = {
        **AUTH,
        "ConfigItem": {
            "Class": "Computer",
            "Name": f"{PREFIX}-db01-updated.example.org",
            "DeplState": "Planned",
            "InciState": "Operational",
            "CIXMLData": {
                "Vendor": "Vendor Z",
                "Model": "Model 3",
                "CPU": ["x86", "arm"],
                "NIC": [{"NIC": "eth9", "IPoverDHCP": "Yes", "IPAddress": ["192.0.2.50"]}],
            },
        },
    }
    z = await _call(znuny, "znuny", "PATCH", f"/ConfigItem/{zid}", json=payload)
    t = await _call(tiqora, "tiqora", "PATCH", f"/ConfigItem/{tid}", json=payload)
    assert z[0] == t[0] == 200, (z, t)
    assert "Error" not in z[1] and "Error" not in t[1], (z, t)
    assert normalize(z[1], flow, side="znuny", times=True) == normalize(
        t[1], flow, side="tiqora", times=True
    )
    # exactly one new version on each side
    assert await _version_count_znuny(zid) == z_before + 1
    assert await _version_count_tiqora(golden_session_factory, tid) == t_before + 1
    # and the resulting CI reads the same
    zg = await _call(znuny, "znuny", "GET", f"/ConfigItem/{zid}", params=AUTH)
    tg = await _call(tiqora, "tiqora", "GET", f"/ConfigItem/{tid}", params=AUTH)
    zn = normalize(zg[1], flow, side="znuny", times=True)
    tn = normalize(tg[1], flow, side="tiqora", times=True)
    assert zn == tn, f"after the update:\nznuny:  {json.dumps(zn)}\ntiqora: {json.dumps(tn)}"
    item = zn["ConfigItem"][0]
    assert item["Name"] == f"{PREFIX}-db01-updated.example.org"
    assert item["CIXMLData"]["Vendor"] == "Vendor Z"
    assert item["DeplState"] == "Planned"


# --- the process action -----------------------------------------------------------------------

#: scenario 2 of Znuny's ``scripts/test/ProcessManagement/TransitionAction/ConfigItemUpdate.t``
#: (the dynamic field tag left out: the golden database has no ``SerialNumber`` ticket field)
_TA_CONFIG: dict[str, str] = {
    "Vendor::1": "",
    "CPU::": "NEW-2-CPU, NEW-2-CPU2",
    "Ram::1": "NEW-2-Ram",
    "Ram::2": "NEW-2-Ram2",
    "HardDisk::1": "NEW-2-HardDisk1",
    "HardDisk::1::Capacity::1": "12",
    "HardDisk::2": "NEW-2-HardDisk2",
    "HardDisk::2::Capacity::1": "44",
    "Description::1": "<OTRS_TICKET_Title>",
    "DeplStateName": "Planned",
    "InciStateName": "Operational",
}


def _znuny_transition_action(ci_id: int, ticket_id: int, config: dict[str, str]) -> bool:
    payload = json.dumps({"ci": ci_id, "ticket": ticket_id, "config": config})
    result = znuny_json(
        f"""
my $Input = $JSON->Decode( Data => <<'GOLDEN_TA_END' );
{payload}
GOLDEN_TA_END
my %Ticket = $TicketObject->TicketGet(
    TicketID => $Input->{{ticket}}, DynamicFields => 1, UserID => 1,
);
my $Action = $Kernel::OM->Get(
    'Kernel::System::ProcessManagement::TransitionAction::ConfigItemUpdate',
);
my $Result = $Action->Run(
    UserID => 1, Ticket => \\%Ticket,
    ProcessEntityID => 'P123', ActivityEntityID => 'A123',
    TransitionEntityID => 'T123', TransitionActionEntityID => 'TA123',
    Config => {{ ConfigItemID => $Input->{{ci}}, UserID => 1, %{{ $Input->{{config}} }} }},
);
Emit({{ ok => $Result ? 1 : 0 }});
"""
    )
    return bool(result["ok"])


async def test_config_item_update_action_matches_znuny(
    flow: Flow,
    golden_session_factory: SessionFactory,
    znuny: httpx.AsyncClient,
    tiqora: httpx.AsyncClient,
) -> None:
    zid, tid = flow.zid("app01"), flow.tid("app01")
    ticket_id = int(flow.znuny["ticket_id"])
    z_before = await _version_count_znuny(zid)
    t_before = await _version_count_tiqora(golden_session_factory, tid)

    assert _znuny_transition_action(zid, ticket_id, _TA_CONFIG)

    async with golden_session_factory() as session:
        result = await execute_transition_action(
            session,
            action=TransitionActionConfig(
                module="Kernel::System::ProcessManagement::TransitionAction::ConfigItemUpdate",
                config={"ConfigItemID": str(tid), "UserID": "1", **_TA_CONFIG},
            ),
            ticket_id=ticket_id,
            process_entity_id="P123",
            activity_entity_id="A123",
            transition_entity_id="T123",
            user_id=1,
            sysconfig=SysConfig(session),
        )
        await session.commit()
    assert result is None  # the action ran (not "unsupported")

    assert await _version_count_znuny(zid) == z_before + 1
    assert await _version_count_tiqora(golden_session_factory, tid) == t_before + 1
    zg = await _call(znuny, "znuny", "GET", f"/ConfigItem/{zid}", params=AUTH)
    tg = await _call(tiqora, "tiqora", "GET", f"/ConfigItem/{tid}", params=AUTH)
    zn = normalize(zg[1], flow, side="znuny", times=True)
    tn = normalize(tg[1], flow, side="tiqora", times=True)
    assert zn == tn, f"after the action:\nznuny:  {json.dumps(zn)}\ntiqora: {json.dumps(tn)}"
    item = zn["ConfigItem"][0]
    assert item["DeplState"] == "Planned"
    assert item["InciState"] == "Operational"
    xml = item["CIXMLData"]
    assert "Vendor" not in xml  # 'Vendor::1' = '' deletes the value (Znuny: empty text, hidden)
    assert xml["Description"] == f"{PREFIX} ticket"  # <OTRS_TICKET_Title> replaced on both sides
    assert xml["CPU"] == ["NEW-2-CPU", "NEW-2-CPU2"]
