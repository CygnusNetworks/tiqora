"""Webservice model, route matching and REST request-data rules (pure, no DB)."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.requests import Request

from tiqora.api.compat.rest_request import (
    X_OTRS_HEADERS,
    RestRequestError,
    build_rest_request_data,
    parse_query_string,
    rest_request_path,
    znuny_json_decode,
)
from tiqora.api.compat.webservices import (
    RouteMatchError,
    Webservice,
    match_rest_route,
    parse_webservice,
    replace_config_placeholders,
    run_mapper,
)
from tiqora.znuny.sysconfig import SysConfig

ZNUNY = Path(__file__).resolve().parents[2] / "znuny-6.5.22"
REST_YAML = ZNUNY / "var/webservices/examples/GenericTicketConnectorREST.yml"
SOAP_YAML = ZNUNY / "scripts/test/Console/Command/Admin/WebService/GenericTicketConnectorSOAP.yml"

needs_znuny_tree = pytest.mark.skipif(not REST_YAML.exists(), reason="Znuny source tree missing")


def _ws(cfg: dict[str, Any]) -> Webservice:
    return parse_webservice(7, "Example", cfg, huge_xml=False)


def _rest_cfg(routes: dict[str, Any], **transport_config: Any) -> dict[str, Any]:
    return {
        "Provider": {
            "Transport": {
                "Type": "HTTP::REST",
                "Config": {"RouteOperationMapping": routes, **transport_config},
            },
            "Operation": {name: {"Type": "Ticket::TicketGet"} for name in routes},
        }
    }


@needs_znuny_tree
def test_routes_sorted_by_operation_and_first_match_wins() -> None:
    ws = _ws(yaml.safe_load(REST_YAML.read_text()))
    assert match_rest_route(ws, "/Ticket/42", "GET") == ("TicketGet", {"TicketID": "42"})
    # Znuny quirk: GET /Ticket/Search hits TicketGet (sorted names, TicketGet < TicketSearch)
    assert match_rest_route(ws, "/Ticket/Search", "GET") == ("TicketGet", {"TicketID": "Search"})
    assert match_rest_route(ws, "/Ticket/Search", "POST") == ("TicketSearch", {})
    assert match_rest_route(ws, "/Ticket/History/5", "GET") == (
        "TicketHistoryGet",
        {"TicketID": "5"},
    )
    assert match_rest_route(ws, "/Ticket/42", "PATCH") == ("TicketUpdate", {"TicketID": "42"})
    assert match_rest_route(ws, "/Nope", "GET") is None
    assert [r.operation for r in ws.routes] == sorted(r.operation for r in ws.routes)
    assert ws.max_length == 100000000
    assert ws.transport == "HTTP::REST"


@needs_znuny_tree
def test_rest_example_operations_dispatch_by_type() -> None:
    ws = _ws(yaml.safe_load(REST_YAML.read_text()))
    assert ws.operations["SessionDelete"].handler == "SessionRemove"
    assert ws.operations["TicketHistoryGet"].handler == "TicketHistoryGet"
    assert ws.operations["SessionDelete"].include_ticket_data is False
    # "MappingInbound: {}" means no mapping (Provider.pm only maps non-empty hashes)
    assert ws.operations["TicketGet"].mapping_inbound is None
    assert ws.operations["TicketGet"].mapping_error is None


@pytest.mark.skipif(not SOAP_YAML.exists(), reason="Znuny source tree missing")
def test_soap_example_has_namespace_and_no_routes() -> None:
    ws = _ws(yaml.safe_load(SOAP_YAML.read_text()))
    assert ws.transport == "HTTP::SOAP"
    assert ws.namespace == "http://www.otrs.org/TicketConnector/"
    assert ws.routes == ()
    assert ws.max_length == 100000000
    assert ws.operations["TicketCreate"].handler == "TicketCreate"


def test_namespace_defaults_when_not_configured() -> None:
    ws = _ws({"Provider": {"Transport": {"Type": "HTTP::SOAP", "Config": {}}}})
    assert ws.namespace == "http://www.otrs.org/TicketConnector/"
    assert ws.max_length is None


def test_route_without_method_list_matches_any_method() -> None:
    ws = _ws(
        _rest_cfg({"Op": {"Route": "/Any"}, "Other": {"Route": "/Str", "RequestMethod": "GET"}})
    )
    assert ws.routes[0].methods == ()
    for method in ("GET", "POST", "DELETE", "PATCH"):
        assert match_rest_route(ws, "/Any", method) == ("Op", {})
    # Znuny filters only for a LIST; a bare string is "no filter" too
    assert match_rest_route(ws, "/Str", "POST") == ("Other", {})


def test_route_vars_are_percent_decoded_and_multiple() -> None:
    ws = _ws(_rest_cfg({"Op": {"Route": "/A/:X/B/:Y", "RequestMethod": ["GET"]}}))
    assert match_rest_route(ws, "/A/a%20b/B/2", "GET") == ("Op", {"X": "a b", "Y": "2"})
    assert match_rest_route(ws, "/A/a/B/2", "POST") is None


def test_route_vars_are_unescaped_exactly_once() -> None:
    ws = _ws(_rest_cfg({"Op": {"Route": "/T/:ID", "RequestMethod": ["GET"]}}))
    # %2F stays inside one path segment instead of splitting it
    assert match_rest_route(ws, "/T/a%2Fb", "GET") == ("Op", {"ID": "a/b"})
    # %2520 is "%20" after ONE unescape, not a space
    assert match_rest_route(ws, "/T/a%2520b", "GET") == ("Op", {"ID": "a%20b"})
    assert match_rest_route(ws, "/T/a/b", "GET") is None


def test_route_whitespace_is_insignificant_like_znuny_xms() -> None:
    ws = _ws(_rest_cfg({"Op": {"Route": "/A /:X", "RequestMethod": ["GET"]}}))
    assert match_rest_route(ws, "/A/1", "GET") == ("Op", {"X": "1"})


def test_uncompilable_route_never_matches_but_others_do() -> None:
    ws = _ws(
        _rest_cfg(
            {
                "Bad": {"Route": "/X/(unclosed", "RequestMethod": ["GET"]},
                "Good": {"Route": "/X/:Id", "RequestMethod": ["GET"]},
            }
        )
    )
    assert ws.routes[0].pattern is None
    assert match_rest_route(ws, "/X/(unclosed", "GET") == ("Good", {"Id": "(unclosed"})


def test_operation_dispatch_by_type_not_name() -> None:
    ws = _ws(
        {
            "Provider": {
                "Transport": {
                    "Type": "HTTP::REST",
                    "Config": {
                        "RouteOperationMapping": {
                            "CreateTicket": {"Route": "/T", "RequestMethod": ["POST"]}
                        }
                    },
                },
                "Operation": {
                    "CreateTicket": {"Type": "Ticket::TicketCreate", "IncludeTicketData": "1"}
                },
            }
        }
    )
    op = ws.operations["CreateTicket"]
    assert op.handler == "TicketCreate" and op.include_ticket_data is True


def test_unknown_operation_type_has_no_handler() -> None:
    ws = _ws(_rest_cfg({}) | {"Provider": {"Operation": {"X": {"Type": "Foo::Bar"}}}})
    assert ws.operations["X"].handler is None
    assert ws.operations["X"].type == "Foo::Bar"


def test_bad_mapping_is_recorded_not_raised() -> None:
    ws = _ws(
        {
            "Provider": {
                "Transport": {"Type": "HTTP::REST", "Config": {}},
                "Operation": {
                    "X": {
                        "Type": "Ticket::TicketGet",
                        "MappingInbound": {"Type": "Znuny", "Config": {"a": 1}},
                    }
                },
            }
        }
    )
    assert "Znuny" in (ws.operations["X"].mapping_error or "")
    assert ws.operations["X"].mapping_inbound is None


def test_valid_mapping_is_built_on_the_operation() -> None:
    ws = _ws(
        {
            "Provider": {
                "Transport": {"Type": "HTTP::REST", "Config": {}},
                "Operation": {
                    "X": {
                        "Type": "Ticket::TicketGet",
                        "MappingOutbound": {
                            "Type": "Simple",
                            "Config": {
                                "KeyMapDefault": {"MapType": "Keep", "MapTo": ""},
                                "ValueMapDefault": {"MapType": "Keep", "MapTo": ""},
                            },
                        },
                    }
                },
            }
        }
    )
    op = ws.operations["X"]
    assert op.mapping_error is None and op.mapping_inbound is None
    assert op.mapping_outbound is not None
    assert op.mapping_outbound.map({"A": "b"}) == {"A": "b"}


async def test_run_mapper_executes_in_a_worker_thread() -> None:
    seen: list[int] = []

    class Probe:
        def map(self, data: Any, data_include: Any = None) -> Any:
            seen.append(threading.get_ident())
            return {"echo": data, "include": data_include}

    result = await run_mapper(Probe(), {"a": 1}, {"k": "v"})
    assert result == {"echo": {"a": 1}, "include": {"k": "v"}}
    assert seen and seen[0] != threading.get_ident()


async def test_slow_mapper_does_not_block_the_event_loop() -> None:
    release = threading.Event()

    class Slow:
        def map(self, data: Any, data_include: Any = None) -> Any:
            release.wait(5)
            return "done"

    task = asyncio.create_task(run_mapper(Slow(), {}))
    await asyncio.sleep(0.05)  # the loop is still responsive while the mapper blocks
    assert not task.done()
    release.set()
    assert await task == "done"


@pytest.mark.parametrize(
    ("max_length", "expected"),
    [
        ("100", 100),
        (100, 100),
        (0, 0),
        ("0", 0),
        ("-5", -5),
        (" 100", None),
        ("01", None),
        ("1.5", None),
        ("abc", None),
        (None, None),
        ("", None),
    ],
)
def test_max_length_follows_znuny_is_integer(max_length: Any, expected: int | None) -> None:
    ws = _ws(_rest_cfg({}, MaxLength=max_length))
    assert ws.max_length == expected


async def test_max_length_zero_rejects_any_body() -> None:
    with pytest.raises(RestRequestError) as err:
        await build_rest_request_data(_request(body=b"{}"), _rest_ws(MaxLength=0), {})
    assert err.value.status == 413


def test_route_regex_timeout_fails_instead_of_falling_through() -> None:
    ws = _ws(
        _rest_cfg(
            {
                "A": {"Route": "/(a|aa)+", "RequestMethod": ["GET"]},
                "B": {"Route": "/:Rest", "RequestMethod": ["GET"]},
            }
        )
    )
    with pytest.raises(RouteMatchError):
        match_rest_route(ws, "/" + "a" * 40 + "b", "GET")
    assert match_rest_route(ws, "/ok", "GET") == ("B", {"Rest": "ok"})


def test_query_string_rules() -> None:
    assert parse_query_string("a=1&a=2;b=x+y&c&d=%C3%BC") == {
        "a": ["1", "2"],
        "b": "x y",
        "c": "",
        "d": "ü",
    }


def test_query_string_znuny_quirks() -> None:
    # only the first two "=" pieces count; first value "0" becomes "" (Perl: $Value || '')
    assert parse_query_string("a=b=c") == {"a": "b"}
    assert parse_query_string("z=0") == {"z": ""}
    assert parse_query_string("z=1&z=0") == {"z": ["1", "0"]}
    # an empty part is stored as key "" (Perl: split yields no pieces, undef key)
    assert parse_query_string("a=1&&b=2") == {"a": "1", "": "", "b": "2"}
    assert parse_query_string("a=1&&") == {"a": "1"}  # trailing empty fields are dropped
    assert parse_query_string("k%20ey=a%2Bb") == {"k ey": "a+b"}  # "+" only mapped in values


def test_json_booleans_become_ints() -> None:
    decoded = znuny_json_decode(b'{"a": true, "b": [false]}')
    assert decoded == {"a": 1, "b": [0]}
    assert type(decoded["a"]) is int and type(decoded["b"][0]) is int


def test_json_numbers_are_normalised_like_perl() -> None:
    decoded = znuny_json_decode(b'{"a": 1.0, "b": 2.50, "c": 1e3, "d": 7, "e": [3.0], "f": 0.1}')
    assert decoded == {"a": 1, "b": 2.5, "c": 1000, "d": 7, "e": [3], "f": 0.1}
    assert type(decoded["a"]) is int and type(decoded["c"]) is int
    assert type(decoded["e"][0]) is int and type(decoded["b"]) is float


@pytest.mark.parametrize("body", [b"{", b"NaN", b'{"a": Infinity}', b""])
def test_json_garbage_and_non_json_constants_raise(body: bytes) -> None:
    with pytest.raises(ValueError):
        znuny_json_decode(body)


class _FakeSysConfig(SysConfig):
    def __init__(self) -> None:
        async def _fetch(name: str) -> Any:
            return {"FQDN": "tiqora.example.org", "Port": 8080, "Nested": {"a": 1}}.get(name)

        super().__init__(fetch=_fetch)


@pytest.fixture
def fake_sysconfig() -> SysConfig:
    return _FakeSysConfig()


async def test_placeholders_replaced(fake_sysconfig: SysConfig) -> None:
    cfg = {"a": "https://<OTRS_CONFIG_FQDN>/x", "b": ["&lt;OTRS_CONFIG_Missing&gt;"]}
    assert await replace_config_placeholders(cfg, fake_sysconfig) == {
        "a": "https://tiqora.example.org/x",
        "b": [""],
    }


async def test_placeholders_numbers_nested_and_non_strings(fake_sysconfig: SysConfig) -> None:
    cfg = {
        "u": "<OTRS_CONFIG_FQDN>:<OTRS_CONFIG_Port>/<OTRS_CONFIG_FQDN>",
        "n": {"deep": ["x &lt;OTRS_CONFIG_Nested&gt; y"], "keep": 5, "none": None},
    }
    assert await replace_config_placeholders(cfg, fake_sysconfig) == {
        "u": "tiqora.example.org:8080/tiqora.example.org",
        "n": {"deep": ["x  y"], "keep": 5, "none": None},
    }


async def test_placeholder_lookup_errors_propagate() -> None:
    async def _fail(name: str) -> Any:
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await replace_config_placeholders({"a": "<OTRS_CONFIG_FQDN>"}, SysConfig(fetch=_fail))


# ---------------------------------------------------------------------------
# build_rest_request_data
# ---------------------------------------------------------------------------


def _request(
    method: str = "POST",
    path: str = "/Webservice/Example/Ticket/1",
    *,
    query: str = "",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    raw_path: bytes | None = None,
    content_length: int | None = None,
) -> Request:
    header_map = dict(headers or {})
    if body or content_length is not None:
        header_map.setdefault(
            "content-length", str(len(body) if content_length is None else content_length)
        )
    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in header_map.items()],
    }
    if raw_path is not None:
        scope["raw_path"] = raw_path
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _rest_ws(**transport_config: Any) -> Webservice:
    return _ws(_rest_cfg({"Op": {"Route": "/Ticket/:TicketID"}}, **transport_config))


async def test_uri_data_beats_body() -> None:
    request = _request(body=b'{"TicketID": "1", "Other": "x"}')
    data = await build_rest_request_data(request, _rest_ws(), {"TicketID": "2"})
    assert data == {"TicketID": "2", "Other": "x"}


async def test_path_variable_beats_query_and_query_beats_body() -> None:
    request = _request(query="TicketID=9&Q=1", body=b'{"Q": "body", "B": "b"}')
    data = await build_rest_request_data(request, _rest_ws(), {"TicketID": "2"})
    assert data == {"TicketID": "2", "Q": "1", "B": "b"}


async def test_get_sets_request_method_and_ignores_body() -> None:
    request = _request("GET", query="UserLogin=u", body=b'{"x": 1}')
    data = await build_rest_request_data(request, _rest_ws(), {"TicketID": "3"})
    assert data == {"UserLogin": "u", "TicketID": "3", "RequestMethod": "GET"}


async def test_missing_content_length_returns_uri_data_only() -> None:
    data = await build_rest_request_data(_request("POST"), _rest_ws(), {"TicketID": "3"})
    assert data == {"TicketID": "3", "RequestMethod": "POST"}


async def test_x_otrs_header_overrides_query_login() -> None:
    request = _request(
        "GET",
        query="UserLogin=fromquery&Password=q",
        headers={"X-OTRS-Header-UserLogin": "fromheader", "x-otrs-header-password": ""},
    )
    data = await build_rest_request_data(request, _rest_ws(), {})
    assert data["UserLogin"] == "fromheader"
    assert data["Password"] == "q"  # an empty header does not override


def test_x_otrs_header_table_is_complete() -> None:
    assert set(X_OTRS_HEADERS.values()) == {
        "UserLogin",
        "CustomerUserLogin",
        "SessionID",
        "Password",
        "ImpersonateAsUser",
        "TwoFactorToken",
    }


async def test_json_list_body_gets_uri_data_merged_into_each_dict() -> None:
    request = _request(body=b'[{"a": 1, "TicketID": "x"}, 5, {}, "s"]')
    data = await build_rest_request_data(request, _rest_ws(), {"TicketID": "2"})
    # empty dicts are not "hash with data" in Znuny, so they stay untouched
    assert data == [{"a": 1, "TicketID": "2"}, 5, {}, "s"]


async def test_body_numbers_and_booleans_are_normalised() -> None:
    request = _request(body=b'{"Limit": 5.0, "Flag": true, "Rate": 1.5}')
    data = await build_rest_request_data(request, _rest_ws(), {})
    assert data == {"Limit": 5, "Flag": 1, "Rate": 1.5}
    assert type(data["Limit"]) is int and type(data["Flag"]) is int


async def test_max_length_rejects_with_413() -> None:
    request = _request(body=b"x" * 20)
    with pytest.raises(RestRequestError) as err:
        await build_rest_request_data(request, _rest_ws(MaxLength="10"), {})
    assert err.value.status == 413
    assert err.value.message == "Request Entity Too Large"


async def test_actual_body_over_max_length_is_rejected_even_with_lying_header() -> None:
    request = _request(body=b"x" * 20, content_length=5)
    with pytest.raises(RestRequestError) as err:
        await build_rest_request_data(request, _rest_ws(MaxLength=10), {})
    assert err.value.status == 413


async def test_invalid_json_is_500() -> None:
    with pytest.raises(RestRequestError) as err:
        await build_rest_request_data(_request(body=b"{nope"), _rest_ws(), {})
    assert (err.value.status, err.value.message) == (500, "Error while decoding request content.")


@pytest.mark.parametrize("body", [b"0", b'""', b"null", b"false", b'"0"'])
async def test_falsy_json_is_decode_error(body: bytes) -> None:
    with pytest.raises(RestRequestError) as err:
        await build_rest_request_data(_request(body=body), _rest_ws(), {})
    assert err.value.message == "Error while decoding request content."


@pytest.mark.parametrize("body", [b"5", b'"abc"', b"{}", b"[]"])
async def test_unsupported_json_structure_is_500(body: bytes) -> None:
    with pytest.raises(RestRequestError) as err:
        await build_rest_request_data(_request(body=body), _rest_ws(), {})
    assert (err.value.status, err.value.message) == (500, "Unsupported request content structure.")


async def test_bom_and_latin1_charset_are_handled() -> None:
    bom = _request(body=b'\xef\xbb\xbf{"a": "b"}')
    assert await build_rest_request_data(bom, _rest_ws(), {}) == {"a": "b"}
    latin = _request(
        body='{"n": "Grüße"}'.encode("iso-8859-1"),
        headers={"content-type": "application/json; charset=iso-8859-1"},
    )
    assert await build_rest_request_data(latin, _rest_ws(), {}) == {"n": "Grüße"}
    utf8 = _request(
        body='{"n": "Grüße"}'.encode(),
        headers={"content-type": "application/json; charset=UTF-8"},
    )
    assert await build_rest_request_data(utf8, _rest_ws(), {}) == {"n": "Grüße"}


async def test_unknown_charset_falls_back_to_latin1_like_znuny() -> None:
    request = _request(
        body='{"n": "Grüße"}'.encode("iso-8859-1"),
        headers={"content-type": "application/json; charset=nope-9"},
    )
    assert await build_rest_request_data(request, _rest_ws(), {}) == {"n": "Grüße"}


# ---------------------------------------------------------------------------
# request path (raw_path, unescape once)
# ---------------------------------------------------------------------------


def test_request_path_comes_from_raw_path_not_decoded_path() -> None:
    request = _request(
        "GET",
        path="/api/v1/compat/Webservice/Example/T/a/b",  # Starlette already decoded %2F
        raw_path=b"/api/v1/compat/Webservice/Example/T/a%2Fb",
    )
    assert rest_request_path(request) == "/T/a%2Fb"


def test_request_path_end_to_end_percent_cases() -> None:
    ws = _ws(_rest_cfg({"Op": {"Route": "/T/:ID", "RequestMethod": ["GET"]}}))
    for raw, expected in (
        (b"/Webservice/Example/T/a%2Fb", "a/b"),
        (b"/WebserviceID/7/T/a%2520b", "a%20b"),
    ):
        request = _request("GET", raw_path=raw)
        assert match_rest_route(ws, rest_request_path(request), "GET") == ("Op", {"ID": expected})


def test_request_path_root_and_trailing_slash() -> None:
    assert rest_request_path(_request("GET", raw_path=b"/Webservice/Example/")) == "/"
    assert rest_request_path(_request("GET", raw_path=b"/Webservice/Example")) == "/"
    assert rest_request_path(_request("GET", raw_path=b"/Webservice/Example/Ticket")) == "/Ticket"


def test_request_path_falls_back_to_quoted_path_without_raw_path() -> None:
    request = _request("GET", path="/Webservice/Example/T/a b")
    assert rest_request_path(request) == "/T/a%20b"
