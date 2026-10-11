"""REST requester transport (G7): httpx MockTransport + fake resolver, no network.

Seed band 51060-51079: the OAuth2 token configs 51071 (valid token) and 51072 (no token row).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._gi_fixtures import EGRESS_TEST_POLICY
from tiqora.config import Settings
from tiqora.gi.config import DebuggerConfig
from tiqora.gi.debugger import GiDebugger
from tiqora.gi.egress import EgressPolicy
from tiqora.gi.transport_rest import (
    TransportResult,
    build_query,
    build_rest_request,
    flatten_data_structure,
    interpolate_controller,
    rest_perform_request,
)

HOSTS = {"crm.example.org": ["198.51.100.10"]}
JSON = {"content-type": "application/json"}
NO_SESSION = cast(AsyncSession, None)  # only the OAuth2 token lookup uses the session


def resolver(host: str, port: int) -> list[str]:
    return HOSTS[host]


@dataclass
class Row:
    communication_id: str
    communication_type: str
    webservice_id: int
    remote_ip: str | None
    name: str | None
    ticket_id: int | None
    debug_level: str
    summary: str
    content: str
    replay: Any


@dataclass
class MemorySink:
    rows: list[Row] = field(default_factory=list)

    async def add(self, **kwargs: Any) -> None:
        self.rows.append(Row(**kwargs))

    def dump(self) -> str:
        return "\n".join(f"{r.summary}\n{r.content}" for r in self.rows)


@dataclass
class Rig:
    sink: MemorySink
    debugger: GiDebugger
    seen: list[httpx.Request]


def rig(
    handler: Callable[[httpx.Request], httpx.Response] | None = None, **dbg: Any
) -> tuple[Rig, httpx.MockTransport]:
    sink = MemorySink()
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if handler is None:
            return httpx.Response(200, json={"ok": 1})
        return handler(request)

    debugger = GiDebugger(
        sink=sink,
        config=DebuggerConfig("debug", False),
        webservice_id=51061,
        communication_type="Requester",
        **dbg,
    )
    return Rig(sink, debugger, seen), httpx.MockTransport(record)


def config(**extra: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "Host": "https://crm.example.org/api",
        "DefaultCommand": "POST",
        "Timeout": "30",
        "InvokerControllerMapping": {"SendTicket": {"Controller": "/Ticket/:TicketID"}},
    }
    cfg.update(extra)
    return cfg


async def call(
    cfg: dict[str, Any],
    data: Any,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    *,
    policy: EgressPolicy = EGRESS_TEST_POLICY,
    session: AsyncSession = NO_SESSION,
) -> tuple[TransportResult, Rig]:
    r, transport = rig(handler)
    res = await rest_perform_request(
        transport_config=cfg,
        operation="SendTicket",
        data=data,
        debugger=r.debugger,
        policy=policy,
        session=session,
        http_transport=transport,
        resolver=resolver,
    )
    return res, r


async def test_post_json_with_controller_interpolation() -> None:
    cfg = config(
        InvokerControllerMapping={
            "SendTicket": {"Controller": "/Ticket/:TicketID?x=:Ticket.TicketNumber"}
        }
    )
    data = {"TicketID": 7, "Ticket": {"TicketNumber": "2026100110000017"}, "Title": "Hallo"}
    res, r = await call(cfg, data)
    assert res.success and res.data == {"ok": 1} and res.status_code == 200
    req = r.seen[0]
    assert req.method == "POST" and req.url.path == "/api/Ticket/7"
    assert req.url.params["x"] == "2026100110000017"
    # TicketID is used up and removed, the dotted key stays in the body
    assert json.loads(req.content) == {
        "Ticket": {"TicketNumber": "2026100110000017"},
        "Title": "Hallo",
    }
    assert req.headers["host"] == "crm.example.org"  # pinned IP, original Host header
    assert req.url.host == "198.51.100.10"
    assert req.headers["content-type"] == "application/json; charset=UTF-8"
    assert data["TicketID"] == 7  # the caller's data is not consumed


async def test_get_puts_data_into_query() -> None:
    cfg = config(
        InvokerControllerMapping={
            "SendTicket": {"Controller": "/Ticket/:TicketID", "Command": "get"}
        }
    )
    res, r = await call(cfg, {"TicketID": 7, "Title": "a b&c", "Tags": ["x", "y"]})
    assert res.success
    req = r.seen[0]
    assert req.method == "GET" and req.content == b""
    assert req.url.path == "/api/Ticket/7"
    assert req.url.query == b"Title=a+b%26c&Tags=x&Tags=y"
    rows = {row.summary for row in r.sink.rows}
    assert "URI after adding Query params from outgoing data" in rows

    # a controller that already has a (filled) query gets "&" instead of "?"
    cfg = config(
        InvokerControllerMapping={
            "SendTicket": {"Controller": "/Ticket?id=:TicketID", "Command": "GET"}
        }
    )
    _, r = await call(cfg, {"TicketID": 7, "Title": "T"})
    assert r.seen[0].url.query == b"id=7&Title=T"


async def test_content_type_form_and_xml() -> None:
    form = config(ContentType="FORM")
    res, r = await call(form, {"TicketID": 7, "Title": "a b", "Note": "ä&="})
    assert res.success
    req = r.seen[0]
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"
    assert req.content == b"Title=a+b&Note=%C3%A4%26%3D"

    xml_cfg = config(ContentType="XML")
    res, r = await call(
        xml_cfg, {"TicketID": 7, "Title": "Hallo", "Ticket": {"Number": "1", "Q": "2"}}
    )
    assert res.success
    req = r.seen[0]
    assert req.headers["content-type"] == "application/xml"
    # NoAttr => 0 and no root element: top-level scalars are elements, nested scalars attributes
    assert req.content.decode() == (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '  <Ticket Number="1"\n'
        '          Q="2" />\n'
        "  <Title>Hallo</Title>\n"
    )

    # the content type only applies to POST/PUT/PATCH
    get = config(
        ContentType="FORM",
        InvokerControllerMapping={"SendTicket": {"Controller": "/T", "Command": "GET"}},
    )
    _, r = await call(get, {"a": "1"})
    assert r.seen[0].headers["content-type"] == "application/json; charset=UTF-8"


async def test_request_headers_replace_and_append() -> None:
    _, r = await call(
        config(), {"TicketID": 7, "__RequestHeaders": {"X-Own": "1", "Accept": "a/b"}}
    )
    h = r.seen[0].headers
    assert h["x-own"] == "1" and h["accept"] == "a/b"
    assert "content-type" not in h  # replaced, not merged

    _, r = await call(
        config(),
        {"TicketID": 7, "__RequestHeadersAppend": {"X-More": "2", "content-type": "text/x-own"}},
    )
    h = r.seen[0].headers
    assert h["x-more"] == "2"
    assert h.get_list("content-type") == ["text/x-own"]  # replaced case-insensitively
    # the control keys never reach the body
    assert b"__Request" not in r.seen[0].content


async def test_basic_auth_header_and_debugger_never_contains_basic_auth() -> None:
    cfg = config(
        Authentication={
            "AuthType": "BasicAuth",
            "BasicAuthUser": "gi51user",
            "BasicAuthPassword": "gi51-secret",
        }
    )
    res, r = await call(cfg, {"TicketID": 7, "Title": "x"})
    wire = base64.b64encode(b"gi51user:gi51-secret").decode()
    assert r.seen[0].headers["authorization"] == f"Basic {wire}"
    dump = r.sink.dump() + str(res.error_message)
    assert "gi51-secret" not in dump and wire not in dump
    request_row = next(x for x in r.sink.rows if x.summary == "Request params from outgoing data")
    assert json.loads(request_row.content)["headers"]["Authorization"] == "***"
    assert {"gi51-secret", wire} <= set(res.secret_values)

    # incomplete credentials send no header
    half = config(Authentication={"AuthType": "BasicAuth", "BasicAuthUser": "u"})
    _, r = await call(half, {"TicketID": 7})
    assert "authorization" not in r.seen[0].headers


async def test_secrets_in_additional_headers_and_urls_are_masked() -> None:
    cfg = config(
        AdditionalHeaders={"X-Api-Key": "gi51-header-key", "X-Short": "abc", "X-Empty": ""},
        InvokerControllerMapping={"SendTicket": {"Controller": "/Ticket/:TicketID?token=:Token"}},
    )
    res, r = await call(
        cfg, {"TicketID": 7, "Token": "gi51-query-token", "Body": "gi51-header-key"}
    )
    assert res.success
    assert r.seen[0].headers["x-api-key"] == "gi51-header-key"
    assert r.seen[0].url.params["token"] == "gi51-query-token"
    dump = r.sink.dump()
    assert "gi51-header-key" not in dump and "gi51-query-token" not in dump
    assert "gi51-header-key" in res.secret_values and "abc" not in res.secret_values
    assert "" not in res.secret_values


async def test_oauth2_token_missing_config_id_and_placeholder_removal() -> None:
    # without a usable token config the unknown placeholders are simply removed
    cfg = config(
        AdditionalHeaders={"X-Other": "<OTRS_SOMETHING>", "X-Mixed": "a<OTRS_X>b"},
    )
    res, r = await call(cfg, {"TicketID": 7})
    assert res.success
    assert r.seen[0].headers["x-other"] == "" and r.seen[0].headers["x-mixed"] == "ab"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_oauth2_token_placeholder(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    monkeypatch.setattr(
        "tiqora.db.legacy.profile._cached_profile", SimpleNamespace(mail_account_has_oauth=True)
    )

    def clean() -> None:
        engine = create_engine(sync_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM oauth2_token WHERE token_config_id IN (51071, 51072)")
                )
                conn.execute(text("DELETE FROM oauth2_token_config WHERE id IN (51071, 51072)"))
        finally:
            engine.dispose()

    clean()
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            for cfg_id in (51071, 51072):
                conn.execute(
                    text(
                        "INSERT INTO oauth2_token_config (id, name, config, valid_id, create_time,"
                        " create_by, change_time, change_by)"
                        " VALUES (:id, :name, '{}', 1, '2026-01-01 10:00:00', 1,"
                        " '2026-01-01 10:00:00', 1)"
                    ),
                    {"id": cfg_id, "name": f"gi51-{cfg_id}"},
                )
            conn.execute(
                text(
                    "INSERT INTO oauth2_token (id, token_config_id, token, token_expiration_date,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (51071, 51071, 'tok-gi51-abcdef', '2099-01-01 00:00:00',"
                    " '2026-01-01 10:00:00', 1, '2026-01-01 10:00:00', 1)"
                )
            )
    finally:
        engine.dispose()

    async_url = (
        sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
        .replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    )
    aengine = create_async_engine(async_url)
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    try:
        headers = {"Authorization": "Bearer <OTRS_OAUTH2_TOKEN>", "X-Other": "<OTRS_SOMETHING>"}
        async with factory() as session:
            cfg = config(
                Authentication={"AuthType": "OAuth2Token", "OAuth2TokenConfigID": 51071},
                AdditionalHeaders=headers,
            )
            res, r = await call(cfg, {"TicketID": 7}, session=session)
            assert res.success, res.error_message
            assert r.seen[0].headers["authorization"] == "Bearer tok-gi51-abcdef"
            assert r.seen[0].headers["x-other"] == ""
            assert "tok-gi51-abcdef" not in r.sink.dump() + str(res.error_message)
            assert "tok-gi51-abcdef" in res.secret_values

            missing = config(
                Authentication={"AuthType": "OAuth2Token", "OAuth2TokenConfigID": 51072},
                AdditionalHeaders=headers,
            )
            res, r = await call(missing, {"TicketID": 7}, session=session)
            assert not res.success and r.seen == []
            assert (
                res.error_message == "OAuth2 token for config with ID 51072 could not be retrieved."
            )
            assert any(row.debug_level == "error" for row in r.sink.rows)
    finally:
        await aengine.dispose()
        clean()


async def test_jwt_is_config_error() -> None:
    cfg = config(Authentication={"AuthType": "JWT", "JWTAuthKeyFilePath": "/x"})
    res, r = await call(cfg, {"TicketID": 7})
    assert not res.success and r.seen == []
    assert res.error_message is not None and "JWT" in res.error_message
    assert any(row.debug_level == "error" for row in r.sink.rows)


async def test_config_errors_use_znuny_texts() -> None:
    for key in ("Host", "DefaultCommand", "Timeout"):
        cfg = config()
        del cfg[key]
        res, r = await call(cfg, {})
        assert res.error_message == f"REST Transport: Have no {key} in config" and r.seen == []
    res, _ = await call(config(), "nope")
    assert res.error_message == "REST Transport: Invalid Data"
    cfg = config(InvokerControllerMapping={})
    res, _ = await call(cfg, {})
    assert res.error_message == (
        "REST Transport: Have no Invoker <-> Controller mapping for Invoker 'SendTicket'."
    )
    cfg = config(DefaultCommand="FETCH")
    res, _ = await call(cfg, {})
    assert res.error_message == "'FETCH' is not a valid REST command."
    for command in ("CONNECT", "trace"):
        cfg = config(DefaultCommand=command)
        res, r = await call(cfg, {})
        assert not res.success and r.seen == []
        assert res.error_message is not None and command.upper() in res.error_message


async def test_non_2xx_error_message_znuny_text() -> None:
    res, r = await call(
        config(), {"TicketID": 7}, lambda req: httpx.Response(500, text="boom backend")
    )
    expected = (
        "Error while performing REST 'POST' request to Controller '/Ticket/7' on Host "
        "'https://crm.example.org/api'. Response code '500'."
    )
    assert not res.success and res.error_message == expected and res.status_code == 500
    err = [row for row in r.sink.rows if row.debug_level == "error"]
    assert err[0].summary == expected and "Response content: 'boom backend'" in err[0].content

    res, _ = await call(config(), {"TicketID": 7}, lambda req: httpx.Response(502))
    assert res.error_message == expected.replace("'500'", "'502'") + " No content provided."


async def test_redirect_not_followed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://10.0.0.1/internal"})

    res, r = await call(config(), {"TicketID": 7}, handler)
    assert not res.success and res.status_code == 302
    assert "Response code '302'" in (res.error_message or "")
    assert len(r.seen) == 1
    assert any("10.0.0.1" in row.content for row in r.sink.rows)  # Location is logged


async def test_204_and_text_plain_and_bad_json() -> None:
    res, _ = await call(config(), {"TicketID": 7}, lambda req: httpx.Response(204))
    assert res.success and res.data is None and res.status_code == 204

    res, _ = await call(
        config(),
        {"TicketID": 7},
        lambda req: httpx.Response(200, text="plain body", headers={"content-type": "text/plain"}),
    )
    assert res.success and res.data == {"Body": "plain body"}

    # JSON true/false become 1/0 (Znuny JSON->Decode)
    res, _ = await call(
        config(), {"TicketID": 7}, lambda req: httpx.Response(200, json={"a": True, "b": False})
    )
    assert res.data == {"a": 1, "b": 0}

    res, r = await call(
        config(), {"TicketID": 7}, lambda req: httpx.Response(200, text="{nope", headers=JSON)
    )
    assert not res.success
    assert res.error_message is not None and res.error_message.endswith(
        "Error while parsing JSON data."
    )
    assert any(row.debug_level == "error" for row in r.sink.rows)

    # a JSON value that is false in Perl counts as a parse failure; an empty hash does not
    res, _ = await call(
        config(), {"TicketID": 7}, lambda req: httpx.Response(200, text="0", headers=JSON)
    )
    assert not res.success
    res, _ = await call(
        config(), {"TicketID": 7}, lambda req: httpx.Response(200, text="{}", headers=JSON)
    )
    assert res.success and res.data == {}


@pytest.mark.parametrize(
    "extra, data",
    [
        ({"AdditionalHeaders": {"X-Name": "Müller"}}, {"TicketID": 7}),
        ({}, {"TicketID": 7, "__RequestHeaders": {"X-Name": "Müller"}}),
        ({}, {"TicketID": 7, "__RequestHeadersAppend": {"X-Name": "Müller"}}),
    ],
)
async def test_non_ascii_header_values_are_sent_as_utf8(
    extra: dict[str, Any], data: dict[str, Any]
) -> None:
    res, r = await call(config(**extra), data)
    assert res.success
    assert ("X-Name".lower().encode(), "Müller".encode()) in [
        (k.lower(), v) for k, v in r.seen[0].headers.raw
    ]


@pytest.mark.parametrize("bad", ["a\r\nX-Evil: 1"])
async def test_unsendable_header_value_is_a_failure_not_an_exception(bad: str) -> None:
    res, r = await call(config(), {"TicketID": 7, "__RequestHeadersAppend": {"X-Bad": bad}})
    assert not res.success and res.error_message
    assert any(row.summary.startswith("Error while performing REST") for row in r.sink.rows)
    assert not r.seen


async def test_lone_surrogate_in_header_is_replaced_not_raised() -> None:
    res, r = await call(config(), {"TicketID": 7, "__RequestHeadersAppend": {"X-S": "a\ud800b"}})
    assert res.success and r.seen[0].headers["x-s"] == "a?b"


async def test_nan_timeout_is_a_config_error() -> None:
    res, _ = await call(config(Timeout="nan"), {"TicketID": 7})
    assert not res.success and "Invalid Timeout" in (res.error_message or "")


async def test_lowercase_content_type_append_still_picks_the_encoder() -> None:
    form = {"content-type": "application/x-www-form-urlencoded"}
    _, r = await call(config(), {"TicketID": 7, "a": "1", "__RequestHeadersAppend": form})
    assert r.seen[0].content == b"a=1"


async def test_response_size_cap_and_logging_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    small = EgressPolicy(
        allow_networks=EGRESS_TEST_POLICY.allow_networks,
        allow_hosts=frozenset(),
        allow_insecure_tls=False,
        cert_dirs=(),
        max_timeout=30.0,
        max_response_bytes=2048,
    )
    res, _ = await call(
        config(),
        {"TicketID": 7},
        lambda req: httpx.Response(200, content=b"x" * 5000),
        policy=small,
    )
    assert not res.success and "Antwort größer als" in (res.error_message or "")

    # a big (but allowed) answer is decoded and flagged; the debugger row only has a note
    monkeypatch.setattr(
        "tiqora.gi.transport_rest.get_settings", lambda: Settings(gi_debug_max_content_bytes=1024)
    )
    body = json.dumps({"pad": "y" * 1500})
    res, r = await call(
        config(), {"TicketID": 7}, lambda req: httpx.Response(200, text=body, headers=JSON)
    )
    assert res.success and res.size_exceeded and len(res.data["pad"]) == 1500
    assert "yyyy" not in r.sink.dump()
    assert any("too large for logging" in row.summary for row in r.sink.rows)


async def test_timeout_is_failure_with_redacted_message() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out for gi51-secret", request=req)

    cfg = config(
        Authentication={
            "AuthType": "BasicAuth",
            "BasicAuthUser": "gi51user",
            "BasicAuthPassword": "gi51-secret",
        }
    )
    res, r = await call(cfg, {"TicketID": 7}, handler)
    assert not res.success and res.status_code is None
    assert res.error_message == (
        "Error while performing REST 'POST' request to Controller '/Ticket/7' on Host "
        "'https://crm.example.org/api'."
    )
    err = [row for row in r.sink.rows if row.debug_level == "error"]
    assert "ReadTimeout" in err[0].content and "gi51-secret" not in r.sink.dump()


async def test_connect_and_trace_refused_and_egress_blocked() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    res, r = await call(config(), {"TicketID": 7}, handler)
    assert not res.success and "ConnectError" in r.sink.dump()

    # a host that resolves outside the allowlist never gets a request
    blocked = config(Host="https://other.example.org")
    HOSTS["other.example.org"] = ["10.0.0.5"]
    try:
        res, r = await call(blocked, {"TicketID": 7})
    finally:
        del HOSTS["other.example.org"]
    assert not res.success and r.seen == []
    assert "nicht erlaubt" in (res.error_message or "")
    assert any(row.debug_level == "error" for row in r.sink.rows)


async def test_dry_run_builds_without_sending() -> None:
    r, _ = rig()
    prepared = await build_rest_request(
        transport_config=config(
            Authentication={
                "AuthType": "BasicAuth",
                "BasicAuthUser": "gi51user",
                "BasicAuthPassword": "gi51-secret",
            }
        ),
        operation="SendTicket",
        data={"TicketID": 7, "Title": "Hallo"},
        debugger=r.debugger,
        policy=EGRESS_TEST_POLICY,
        session=NO_SESSION,
        resolver=resolver,
    )
    assert not isinstance(prepared, TransportResult)
    assert prepared.method == "POST" and prepared.url == "https://crm.example.org/api/Ticket/7"
    assert prepared.pinned is not None and prepared.pinned.pinned_ip == "198.51.100.10"
    assert prepared.body == b'{"Title":"Hallo"}'
    shown = json.dumps(prepared.display())
    assert "gi51-secret" not in shown and "Basic ***" in shown
    assert prepared.display()["body_bytes"] == len(prepared.body)


def test_flatten_and_interpolate_quirks() -> None:
    assert flatten_data_structure({"A": {"B": "1"}, "L": ["x", "0", "y"], "Z": ""}) == {
        "A.B": "1",
        "L.0": "x",
        "L.1": "y",
        "Z": "",
    }
    # falsy keys vanish, containers inside arrays are not addressable (but advance the counter)
    assert flatten_data_structure({"0": "a", "": "b", "K": [{"n": 1}, "v"]}) == {"K.1": "v"}

    # path placeholders need a following "/", "?" or the end; names are exact
    uri, used = interpolate_controller("/T/:Id/:IdX/x", {"Id": 5, "IdX": "a b/c"})
    assert uri == "/T/5/a%20b%2Fc/x" and used == {"Id", "IdX"}
    # dotted (nested) keys are filled but not consumed
    uri, used = interpolate_controller("/T/:A.B", {"A": {"B": "1"}})
    assert uri == "/T/1" and used == set()
    # a query part without a hit is dropped (Znuny quirk); with a hit it is appended
    assert interpolate_controller("/T?a=:Nope", {"X": "1"}) == ("/T", set())
    assert interpolate_controller("/T?a=:X&b=:Y", {"X": "1", "Y": "é"}) == (
        "/T?a=1&b=%C3%A9",
        {"X", "Y"},
    )
    # undefined values are not substituted
    assert interpolate_controller("/T/:X", {"X": None}) == ("/T/:X", set())

    assert build_query({"a": "1 2", "b": None, "c": ["x", "y"], "d": {"n": 1}}) == (
        "?a=1+2&b=&c=x&c=y"
    )
    assert build_query({}) == ""
    assert build_query({"k": "a/b:c@d!*'()~"}) == "?k=a%2Fb%3Ac%40d!*'()~"
