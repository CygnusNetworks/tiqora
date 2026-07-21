"""Unit tests for the shared outbound-URL SSRF validator (security batch B)."""

from __future__ import annotations

import ipaddress

import pytest

from tiqora.security.outbound import (
    OutboundURLError,
    is_blocked_ip,
    pin_outbound_url,
    validate_outbound_url,
)


def _resolver(mapping: dict[str, list[str]]):
    def resolve(hostname: str, port: int) -> list[str]:
        _ = port
        if hostname not in mapping:
            raise OutboundURLError(f"cannot resolve host {hostname!r}")
        return list(mapping[hostname])

    return resolve


def test_blocks_metadata_ip() -> None:
    with pytest.raises(OutboundURLError, match="blocked"):
        validate_outbound_url("http://169.254.169.254/latest/meta-data/")


def test_blocks_loopback_v4() -> None:
    with pytest.raises(OutboundURLError, match="blocked"):
        validate_outbound_url("http://127.0.0.1:6379/")


def test_blocks_loopback_v6() -> None:
    with pytest.raises(OutboundURLError, match="blocked"):
        validate_outbound_url("http://[::1]/")


def test_blocks_rfc1918_10() -> None:
    with pytest.raises(OutboundURLError, match="blocked"):
        validate_outbound_url("http://10.0.0.5/hook")


def test_blocks_rfc1918_172_16() -> None:
    with pytest.raises(OutboundURLError, match="blocked"):
        validate_outbound_url("http://172.16.1.1/hook")


def test_blocks_rfc1918_192_168() -> None:
    with pytest.raises(OutboundURLError, match="blocked"):
        validate_outbound_url("http://192.168.1.10/hook")


def test_blocks_link_local_fe80() -> None:
    with pytest.raises(OutboundURLError, match="blocked"):
        validate_outbound_url("http://[fe80::1]/")


def test_blocks_unique_local_fc00() -> None:
    with pytest.raises(OutboundURLError, match="blocked"):
        validate_outbound_url("http://[fc00::1]/")


def test_blocks_when_any_resolved_ip_is_private() -> None:
    """If DNS returns a mix of public + private, reject (don't pick the public one)."""
    resolve = _resolver({"evil.example": ["8.8.8.8", "10.0.0.1"]})
    with pytest.raises(OutboundURLError, match="blocked"):
        validate_outbound_url("https://evil.example/hook", resolver=resolve)


def test_allows_public_ip_literal() -> None:
    validate_outbound_url("https://8.8.8.8/health")
    pinned = pin_outbound_url("https://8.8.8.8/health")
    assert pinned.pinned_ip == "8.8.8.8"
    assert pinned.request_url == "https://8.8.8.8/health"


def test_allows_public_hostname_and_pins_ip() -> None:
    resolve = _resolver({"hooks.example.com": ["8.8.8.8"]})
    pinned = pin_outbound_url("https://hooks.example.com/tiqora", resolver=resolve)
    assert pinned.pinned_ip == "8.8.8.8"
    assert pinned.request_url == "https://8.8.8.8/tiqora"
    assert pinned.request_headers()["Host"] == "hooks.example.com"
    assert pinned.request_extensions() == {"sni_hostname": "hooks.example.com"}


def test_require_https_rejects_http() -> None:
    resolve = _resolver({"hooks.example.com": ["8.8.8.8"]})
    with pytest.raises(OutboundURLError, match="https"):
        validate_outbound_url("http://hooks.example.com/hook", require_https=True, resolver=resolve)


def test_rejects_non_http_scheme() -> None:
    with pytest.raises(OutboundURLError, match="scheme"):
        validate_outbound_url("file:///etc/passwd")


def test_rejects_userinfo() -> None:
    resolve = _resolver({"hooks.example.com": ["8.8.8.8"]})
    with pytest.raises(OutboundURLError, match="userinfo"):
        validate_outbound_url("https://user:pass@hooks.example.com/", resolver=resolve)


def test_is_blocked_ip_helpers() -> None:
    assert is_blocked_ip(ipaddress.ip_address("127.0.0.1"))
    assert is_blocked_ip(ipaddress.ip_address("169.254.169.254"))
    assert is_blocked_ip(ipaddress.ip_address("10.1.2.3"))
    assert is_blocked_ip(ipaddress.ip_address("::1"))
    assert is_blocked_ip(ipaddress.ip_address("fe80::1"))
    assert not is_blocked_ip(ipaddress.ip_address("8.8.8.8"))
    assert not is_blocked_ip(ipaddress.ip_address("1.1.1.1"))
