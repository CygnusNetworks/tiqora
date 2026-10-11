"""GenericInterface egress policy: allowlist, cert directories, TLS, proxy, client, size cap."""

from __future__ import annotations

import ipaddress
import ssl
from pathlib import Path

import httpx
import pytest

from tests._gi_fixtures import EGRESS_TEST_POLICY
from tiqora.gi.egress import (
    EgressError,
    EgressPolicy,
    build_ssl_context,
    client,
    pin,
    proxy_for,
    read_capped,
    resolve_cert_path,
)
from tiqora.gi.ws_settings import validate_targets


def policy(**kw) -> EgressPolicy:  # type: ignore[no-untyped-def]
    base = dict(
        allow_networks=(),
        allow_hosts=frozenset(),
        allow_insecure_tls=False,
        cert_dirs=(),
        max_timeout=30.0,
        max_response_bytes=1024,
    )
    base.update(kw)
    return EgressPolicy(**base)


def fake(ips: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    return lambda host, port: ips[host]


def test_validate_targets() -> None:
    assert validate_targets(["10.0.0.0/8", "CRM.example.org"]) == ("10.0.0.0/8", "crm.example.org")
    for bad in [
        "127.0.0.0/8",
        "169.254.0.0/16",
        "0.0.0.0/0",
        "::1/128",
        "https://x.example.org",
        "*.example.org",
        "x.example.org:443",
        "x.example.org/path",
        "",
    ]:
        with pytest.raises(ValueError):
            validate_targets([bad])
    with pytest.raises(ValueError):
        validate_targets([f"h{i}.example.org" for i in range(51)])


def test_pin_documentation_ip_needs_allowlist() -> None:
    r = fake({"crm.example.org": ["198.51.100.7"]})
    with pytest.raises(EgressError, match="nicht erlaubt"):
        pin("https://crm.example.org/", policy(), resolver=r)
    assert (
        pin("https://crm.example.org/", EGRESS_TEST_POLICY, resolver=r).pinned_ip == "198.51.100.7"
    )


def test_pin_rebinding_with_unlisted_second_record_rejected() -> None:
    r = fake({"crm.example.org": ["198.51.100.7", "10.0.0.5"]})
    with pytest.raises(EgressError):
        pin("https://crm.example.org/", EGRESS_TEST_POLICY, resolver=r)


def test_host_allowlist_allows_its_private_ip_only() -> None:
    pol = policy(allow_hosts=frozenset({"crm.example.org"}))
    r = fake(
        {
            "crm.example.org": ["10.0.0.5"],
            "other.example.org": ["10.0.0.5"],
            "evil.example.org": ["127.0.0.1"],
        }
    )
    assert pin("https://crm.example.org/", pol, resolver=r).pinned_ip == "10.0.0.5"
    with pytest.raises(EgressError):
        pin("https://other.example.org/", pol, resolver=r)
    pol2 = policy(allow_hosts=frozenset({"evil.example.org"}))
    with pytest.raises(EgressError):
        pin("https://evil.example.org/", pol2, resolver=r)  # loopback never, even for a listed host


def test_cgnat_allowed_only_when_listed() -> None:
    r = fake({"c.example.org": ["100.64.1.1"]})
    with pytest.raises(EgressError):
        pin("https://c.example.org/", policy(), resolver=r)
    with pytest.raises(EgressError):
        pin("http://100.64.1.1/", policy())
    listed = policy(allow_networks=(ipaddress.ip_network("100.64.0.0/10"),))
    assert pin("https://c.example.org/", listed, resolver=r).pinned_ip == "100.64.1.1"
    assert pin("http://100.64.1.1/", listed).pinned_ip == "100.64.1.1"
    by_host = policy(allow_hosts=frozenset({"c.example.org"}))
    assert pin("https://c.example.org/", by_host, resolver=r).pinned_ip == "100.64.1.1"


def test_cert_path_outside_allowed_dirs_rejected(tmp_path: Path) -> None:
    inside = tmp_path / "certs" / "client.pem"
    inside.parent.mkdir()
    inside.write_text("x")
    outside = tmp_path / "other.pem"
    outside.write_text("x")
    link = tmp_path / "certs" / "link.pem"
    link.symlink_to(outside)
    pol = policy(cert_dirs=((tmp_path / "certs").resolve(),))
    assert resolve_cert_path(str(inside), pol) == inside.resolve()
    for p in (str(outside), str(link), "/etc/shadow", str(tmp_path / "certs" / ".." / "other.pem")):
        with pytest.raises(EgressError):
            resolve_cert_path(p, pol)
    with pytest.raises(EgressError):
        resolve_cert_path(str(inside.parent), pol)  # a directory where a file is expected
    assert resolve_cert_path(str(inside.parent), pol, directory=True) == inside.parent.resolve()
    big = tmp_path / "certs" / "big.pem"
    big.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(EgressError):
        resolve_cert_path(str(big), pol)


def test_no_cert_dirs_means_client_certs_disabled(tmp_path: Path) -> None:
    f = tmp_path / "c.pem"
    f.write_text("x")
    with pytest.raises(EgressError, match="deaktiviert"):
        resolve_cert_path(str(f), policy())
    with pytest.raises(EgressError):
        build_ssl_context(
            policy=policy(),
            verify_hostname=True,
            cert_file=str(f),
            key_file=None,
            key_password=None,
            ca_file=None,
            ca_dir=None,
        )


def test_insecure_tls_needs_opt_in() -> None:
    args = dict(cert_file=None, key_file=None, key_password=None, ca_file=None, ca_dir=None)
    with pytest.raises(EgressError, match="SSLNoHostnameVerification"):
        build_ssl_context(policy=policy(), verify_hostname=False, **args)  # type: ignore[arg-type]
    ctx = build_ssl_context(policy=policy(allow_insecure_tls=True), verify_hostname=False, **args)  # type: ignore[arg-type]
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE
    strict = build_ssl_context(policy=policy(), verify_hostname=True, **args)  # type: ignore[arg-type]
    assert strict.check_hostname is True
    assert strict.verify_mode == ssl.CERT_REQUIRED


def test_proxy_host_checked() -> None:
    pol = policy()
    with pytest.raises(EgressError):
        proxy_for({"Proxy": {"UseProxy": "Yes", "ProxyHost": "http://localhost:3128"}}, pol)
    with pytest.raises(EgressError):
        proxy_for({"Proxy": {"UseProxy": "Yes", "ProxyHost": "http://169.254.169.254:3128"}}, pol)
    assert proxy_for(
        {"Proxy": {"UseProxy": "Yes", "ProxyExclude": "Yes", "ProxyHost": "http://localhost:3128"}},
        pol,
    ) == (None, {})
    assert proxy_for({"Proxy": {"UseProxy": "No", "ProxyHost": "http://localhost:3128"}}, pol) == (
        None,
        {},
    )
    assert proxy_for({}, pol) == (None, {})
    url, headers = proxy_for(
        {
            "Proxy": {
                "UseProxy": "Yes",
                "ProxyHost": "http://198.51.100.9:3128",
                "ProxyUser": "u",
                "ProxyPassword": "p",
            }
        },
        EGRESS_TEST_POLICY,
    )
    assert url == "http://198.51.100.9:3128/"
    assert headers == {"Proxy-Authorization": "Basic dTpw"}
    _, no_auth = proxy_for(
        {"Proxy": {"UseProxy": "Yes", "ProxyHost": "http://198.51.100.9:3128", "ProxyUser": "u"}},
        EGRESS_TEST_POLICY,
    )
    assert no_auth == {}


async def test_client_never_follows_redirects_or_env_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/internal"})
        return httpx.Response(200, text="internal")

    c = client(
        policy=policy(), ssl_context=None, proxy=None, transport=httpx.MockTransport(handler)
    )
    async with c:
        resp = await c.get("http://example.org/start")
    assert resp.status_code == 302 and seen == ["http://example.org/start"]
    assert c.follow_redirects is False and c._trust_env is False


async def test_read_capped() -> None:
    ok = httpx.Response(200, content=b"x" * 100)
    assert await read_capped(ok, 100) == b"x" * 100
    big = httpx.Response(200, content=b"x" * 101)
    with pytest.raises(EgressError, match="Antwort größer als"):
        await read_capped(big, 100)
    declared = httpx.Response(200, headers={"content-length": "5000"}, content=b"x")
    with pytest.raises(EgressError):
        await read_capped(declared, 1000)

    async def gen():  # type: ignore[no-untyped-def]
        for _ in range(5):
            yield b"y" * 60

    streamed = httpx.Response(200, content=gen())
    with pytest.raises(EgressError):
        await read_capped(streamed, 200)


def test_gi_cert_dirs_env_is_colon_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.config import Settings

    monkeypatch.setenv("TIQORA_GI_CERT_DIRS", "/etc/a:/etc/b::")
    assert Settings().gi_cert_dirs == ["/etc/a", "/etc/b"]
