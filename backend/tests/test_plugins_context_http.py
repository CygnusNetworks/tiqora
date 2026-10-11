from __future__ import annotations

import httpx
import pytest

from tiqora.plugins.context import OutboundHostNotAllowed, PluginContext, PluginHttp, PluginTickets
from tiqora.security.outbound import OutboundURLError


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        ("api.example.org", True),
        ("API.Example.ORG", True),
        ("a.example.net", True),
        ("x.y.example.net", True),
        ("example.net", False),
        ("evil-example.net", False),
        ("api.example.org.evil.test", False),
        ("", False),
    ],
)
def test_host_allowed(host: str, ok: bool) -> None:
    assert PluginHttp(["api.example.org", "*.example.net"]).host_allowed(host) is ok


async def test_disallowed_host_never_connects() -> None:
    called: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        called.append(r)
        return httpx.Response(200)

    http = PluginHttp(["api.example.org"], transport=httpx.MockTransport(handler))
    with pytest.raises(OutboundHostNotAllowed):
        await http.request("GET", "https://other.example.org/x")
    assert called == []


async def test_private_ip_blocked() -> None:
    called: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        called.append(r)
        return httpx.Response(200)

    http = PluginHttp(
        ["api.example.org"],
        resolver=lambda host, port: ["127.0.0.1"],
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OutboundURLError):
        await http.request("GET", "https://api.example.org/x")
    assert called == []


async def test_request_goes_to_pinned_ip_with_host_header() -> None:
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return httpx.Response(200, json={"ok": True})

    http = PluginHttp(
        ["api.example.org"],
        resolver=lambda h, p: ["93.184.215.14"],
        transport=httpx.MockTransport(handler),
    )
    resp = await http.request("POST", "https://api.example.org/v1/x", json={"a": 1})
    assert resp.json() == {"ok": True}
    assert seen[0].url.host == "93.184.215.14"
    assert seen[0].headers["host"] == "api.example.org"
    assert seen[0].url.path == "/v1/x"
    assert seen[0].extensions["sni_hostname"] == "api.example.org"


async def test_caller_cannot_override_host_header() -> None:
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return httpx.Response(204)

    http = PluginHttp(
        ["api.example.org"],
        resolver=lambda h, p: ["93.184.215.14"],
        transport=httpx.MockTransport(handler),
    )
    await http.request("GET", "https://api.example.org/", headers={"Host": "evil.test"})
    assert seen[0].headers["host"] == "api.example.org"


async def test_redirects_are_not_followed() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

    http = PluginHttp(
        ["api.example.org"],
        resolver=lambda h, p: ["93.184.215.14"],
        transport=httpx.MockTransport(handler),
    )
    resp = await http.request("GET", "https://api.example.org/")
    assert resp.status_code == 302


async def test_resolver_runs_off_the_event_loop() -> None:
    import threading

    threads: list[int] = []

    def resolver(host: str, port: int) -> list[str]:
        threads.append(threading.get_ident())
        return ["93.184.215.14"]

    http = PluginHttp(
        ["api.example.org"],
        resolver=resolver,
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    await http.request("GET", "https://api.example.org/")
    assert threads and threads[0] != threading.get_ident()


def test_context_logger_is_bound_to_plugin() -> None:
    ctx = PluginContext("org.example.hello", "1.0.0", PluginHttp([]), PluginTickets())
    assert ctx.logger is not None
    assert ctx.plugin_id == "org.example.hello"
