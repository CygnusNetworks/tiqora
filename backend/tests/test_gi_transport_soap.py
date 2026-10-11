"""SOAP requester transport (G8): httpx MockTransport + fake resolver, no network."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from tests._gi_fixtures import EGRESS_TEST_POLICY
from tiqora.gi import transport_soap
from tiqora.gi.config import DebuggerConfig
from tiqora.gi.debugger import GiDebugger
from tiqora.gi.egress import EgressPolicy
from tiqora.gi.soap_envelope import ENVELOPE_HEAD, ENVELOPE_TAIL
from tiqora.gi.transport_rest import TransportResult
from tiqora.gi.transport_soap import soap_perform_request

NS = "http://ws.example.org/Conn"
HOSTS = {"crm.example.org": ["198.51.100.10"]}
XML = {"content-type": "text/xml; charset=utf-8"}


def resolver(host: str, port: int) -> list[str]:
    return HOSTS.get(host, ["10.0.0.5"])  # unknown host: internal address, not allowlisted


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


def reply(inner: str, *, decl: str = '<?xml version="1.0" encoding="UTF-8"?>') -> bytes:
    return (
        decl
        + '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        + f"<soap:Body>{inner}</soap:Body></soap:Envelope>"
    ).encode()


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, content=reply(f'<OpResponse xmlns="{NS}"><Id>7</Id></OpResponse>'), headers=XML
    )


def config(**extra: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "Endpoint": "https://crm.example.org/soap",
        "NameSpace": NS,
        "Timeout": "30",
    }
    cfg.update(extra)
    return cfg


async def call(
    cfg: dict[str, Any],
    data: Any = None,
    handler: Callable[[httpx.Request], httpx.Response] = ok_handler,
    *,
    policy: EgressPolicy = EGRESS_TEST_POLICY,
    **kw: Any,
) -> tuple[TransportResult, list[httpx.Request], MemorySink]:
    sink = MemorySink()
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    debugger = GiDebugger(
        sink=sink,
        config=DebuggerConfig("debug", False),
        webservice_id=51081,
        communication_type="Requester",
    )
    res = await soap_perform_request(
        transport_config=cfg,
        operation="Inv",
        operation_name="Op",
        data={"Title": "Hallo"} if data is None else data,
        debugger=debugger,
        policy=policy,
        http_transport=httpx.MockTransport(record),
        resolver=resolver,
        **kw,
    )
    return res, seen, sink


async def test_soap_post_with_basic_auth_masked() -> None:
    cfg = config(
        Authentication={
            "AuthType": "BasicAuth",
            "BasicAuthUser": "gi51user",
            "BasicAuthPassword": "gi51-secret",
        }
    )
    res, seen, sink = await call(cfg, {"Title": "Hallo & <Welt>", "A": ["1", "2"]})
    assert res.success, res.error_message
    assert res.data == {"Id": "7"} and res.status_code == 200
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/soap"
    assert req.headers["host"] == "crm.example.org" and req.url.host == "198.51.100.10"
    assert req.headers["content-type"] == "text/xml; charset=utf-8"
    assert req.headers["soapaction"] == f'"{NS}#Op"'
    token = base64.b64encode(b"gi51user:gi51-secret").decode()
    assert req.headers["authorization"] == f"Basic {token}"
    assert req.content.decode() == (
        ENVELOPE_HEAD + f'<namesp1:Op xmlns:namesp1="{NS}"><A>1</A><A>2</A>'
        "<Title>Hallo &amp; &lt;Welt&gt;</Title></namesp1:Op>" + ENVELOPE_TAIL
    )
    dump = sink.dump()
    assert "gi51-secret" not in dump and token not in dump
    assert "XML data sent to remote system" in dump
    assert "XML data received from remote system" in dump
    assert any("Hallo &amp; &lt;Welt&gt;" in r.content for r in sink.rows)


async def test_soap_action_no_and_fixed_prefix_and_sort() -> None:
    cfg = config(SOAPAction="No", FixedNameSpacePrefix="ns1", Sort=[{"B": None}, {"A": None}])
    res, seen, _ = await call(cfg, {"A": "1", "B": "2"})
    assert res.success
    assert seen[0].headers["soapaction"] == '""'
    body = seen[0].content.decode()
    assert f'<ns1:Op xmlns:ns1="{NS}"><B>2</B><A>1</A></ns1:Op>' in body


async def test_ntlm_is_config_error() -> None:
    cfg = config(
        Authentication={"AuthType": "NTLM", "BasicAuthUser": "u", "BasicAuthPassword": "p"}
    )
    res, seen, sink = await call(cfg)
    assert not res.success and "NTLM" in (res.error_message or "")
    assert seen == []
    assert any(r.debug_level == "error" for r in sink.rows)


async def test_non_utf8_encoding_converted() -> None:
    raw = (
        reply(
            f'<OpResponse xmlns="{NS}"><Name>Grüße</Name></OpResponse>',
            decl='<?xml version="1.0" encoding="iso-8859-1"?>',
        )
        .decode("utf-8")
        .encode("iso-8859-1")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw, headers={"content-type": "text/xml"})

    res, _, sink = await call(config(Encoding="iso-8859-1"), None, handler)
    assert res.success, res.error_message
    assert res.data == {"Name": "Grüße"}
    assert any("Grüße" in r.content for r in sink.rows)


async def test_fault_and_missing_response_and_http_errors() -> None:
    fault = reply(
        "<soap:Fault><faultcode>Server</faultcode><faultstring>boom</faultstring></soap:Fault>"
    )

    res, _, _ = await call(
        config(), None, lambda r: httpx.Response(500, content=fault, headers=XML)
    )
    assert not res.success and res.error_message == "faultcode: Server, faultstring: boom"
    assert res.status_code == 500

    res, _, _ = await call(
        config(), None, lambda r: httpx.Response(200, content=reply("<Other/>"), headers=XML)
    )
    assert (
        res.error_message == "No response data found for specified operation 'Op' in soap response"
    )

    res, _, _ = await call(
        config(), None, lambda r: httpx.Response(503, content=b"<html>down</html>")
    )
    assert not res.success and res.error_message == "Error in SOAP call: 503 Service Unavailable"

    res, _, _ = await call(config(), None, lambda r: httpx.Response(200, content=b""))
    assert res.error_message == "Could not get XML data received from remote system"

    res, _, _ = await call(config(), None, lambda r: httpx.Response(200, content=b"nope"))
    assert res.error_message == "SOAP Transport: Could not deserialize received XML data"


async def test_config_errors() -> None:
    for missing in ("Endpoint", "NameSpace", "Timeout"):
        cfg = config()
        del cfg[missing]
        res, seen, _ = await call(cfg)
        assert res.error_message == f"SOAP Transport: Have no {missing} in config"
        assert seen == []
    # NoNameSpace makes the namespace optional and the method element plain
    cfg = config(NoNameSpace="1")
    del cfg["NameSpace"]
    res, seen, _ = await call(cfg)
    assert res.success
    assert "<Op><Title>Hallo</Title></Op>" in seen[0].content.decode()
    res, _, _ = await call(config(), "not a dict")
    assert res.error_message == "SOAP Transport: Invalid Data"
    res, _, _ = await call(config(), {"Var": [1, [2]]})
    assert res.error_message is not None
    assert res.error_message.startswith("Error in SOAPOutputRecursion:")


async def test_internal_target_refused_and_redirect_not_followed() -> None:
    res, seen, _ = await call(config(Endpoint="http://10.0.0.5/soap"))
    assert not res.success and seen == []

    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://10.0.0.1/x"})

    res, seen, _ = await call(config(), None, redirect)
    assert not res.success and len(seen) == 1
    assert res.error_message == "Error in SOAP call: 302 Found"


async def test_timeout_is_failure_and_size_cap() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    res, _, sink = await call(config(), None, boom)
    assert not res.success and res.error_message == "Error in SOAP call: ReadTimeout"
    assert any("ReadTimeout" in r.content for r in sink.rows)

    small = EGRESS_TEST_POLICY.__class__(
        allow_networks=EGRESS_TEST_POLICY.allow_networks,
        allow_hosts=EGRESS_TEST_POLICY.allow_hosts,
        allow_insecure_tls=False,
        cert_dirs=(),
        max_timeout=30.0,
        max_response_bytes=64,
    )
    res, _, _ = await call(config(), None, ok_handler, policy=small)
    assert not res.success


async def test_proxy_pins_only_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    # httpx would use the proxy instead of the mock transport: record the proxy and drop it
    used: dict[str, Any] = {}
    real_client = transport_soap.client

    def fake_client(**kwargs: Any) -> httpx.AsyncClient:
        used["proxy"] = kwargs["proxy"]
        return real_client(**{**kwargs, "proxy": None})

    monkeypatch.setattr(transport_soap, "client", fake_client)
    cfg = config(
        Endpoint="https://unresolvable.example.org/soap",
        Proxy={
            "UseProxy": "Yes",
            "ProxyHost": "http://198.51.100.20:3128",
            "ProxyUser": "gi51proxy",
            "ProxyPassword": "gi51-proxy-secret",
        },
    )
    res, seen, sink = await call(cfg)
    assert res.success, res.error_message
    # the endpoint was neither resolved nor pinned (the resolver has no such host) ...
    assert len(seen) == 1 and seen[0].url.host == "unresolvable.example.org"
    # ... only the proxy URL is pinned, and its credentials never reach the debugger
    assert str(used["proxy"].url).startswith("http://198.51.100.20:3128")
    assert "gi51-proxy-secret" not in sink.dump()
    # without a proxy the same endpoint is refused: it does not resolve
    monkeypatch.undo()
    res, seen, _ = await call(config(Endpoint="https://unresolvable.example.org/soap"))
    assert not res.success and seen == []


async def test_secrets_echoed_by_the_remote_are_masked() -> None:
    cfg = config(
        Authentication={
            "AuthType": "BasicAuth",
            "BasicAuthUser": "gi51user",
            "BasicAuthPassword": "gi51-secret",
        }
    )
    fault = reply(
        "<soap:Fault><faultcode>Client</faultcode>"
        "<faultstring>login gi51user/gi51-secret refused</faultstring></soap:Fault>"
    )
    res, _, sink = await call(cfg, None, lambda r: httpx.Response(500, content=fault, headers=XML))
    assert not res.success
    assert "gi51-secret" not in (res.error_message or "")
    assert "gi51-secret" not in sink.dump()
    assert res.secret_values and "gi51-secret" in res.secret_values
