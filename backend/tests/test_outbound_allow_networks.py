"""``pin_outbound_url(allow_networks=...)``: private targets only through an explicit list."""

from __future__ import annotations

import ipaddress

import pytest

from tiqora.security.outbound import HostResolver, OutboundURLError, pin_outbound_url


def fake(ips: dict[str, list[str]]) -> HostResolver:
    return lambda host, port: ips[host]


def net(*cidr: str) -> list:  # type: ignore[type-arg]
    return [ipaddress.ip_network(c) for c in cidr]


def test_private_blocked_by_default_allowed_in_listed_network() -> None:
    r = fake({"crm.example.org": ["10.1.2.3"]})
    with pytest.raises(OutboundURLError):
        pin_outbound_url("https://crm.example.org/x", resolver=r)
    p = pin_outbound_url("https://crm.example.org/x", resolver=r, allow_networks=net("10.0.0.0/8"))
    assert p.pinned_ip == "10.1.2.3"
    # a private address outside the listed network stays blocked
    with pytest.raises(OutboundURLError):
        pin_outbound_url(
            "https://crm.example.org/x", resolver=r, allow_networks=net("192.168.0.0/16")
        )


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "169.254.169.254", "::1", "0.0.0.0", "224.0.0.1", "fe80::1", "::ffff:127.0.0.1"],
)
def test_metadata_never_allowed_even_if_listed(ip: str) -> None:
    with pytest.raises(OutboundURLError):
        pin_outbound_url(
            "http://h.example.org/",
            resolver=fake({"h.example.org": [ip]}),
            allow_networks=net("0.0.0.0/0", "::/0"),
        )


def test_literal_ip_host_follows_the_same_rule() -> None:
    with pytest.raises(OutboundURLError):
        pin_outbound_url("http://10.0.0.5/")
    assert (
        pin_outbound_url("http://10.0.0.5/", allow_networks=net("10.0.0.0/8")).pinned_ip
        == "10.0.0.5"
    )
    with pytest.raises(OutboundURLError):
        pin_outbound_url("http://169.254.169.254/", allow_networks=net("0.0.0.0/0"))


def test_rebinding_host_pinned_and_rejected() -> None:
    # Any one blocked A record rejects the URL: allowlisted doc IP + unlisted 10.0.0.5.
    r = fake({"a.example.org": ["198.51.100.7", "10.0.0.5"]})
    with pytest.raises(OutboundURLError):
        pin_outbound_url(
            "https://a.example.org/", resolver=r, allow_networks=net("198.51.100.0/24")
        )
    # ... and a doc IP alone, without an allowlist, is rejected as well
    with pytest.raises(OutboundURLError):
        pin_outbound_url(
            "https://a.example.org/", resolver=fake({"a.example.org": ["198.51.100.7"]})
        )
    # sanity: the doc IP alone passes once its network is listed
    ok = pin_outbound_url(
        "https://a.example.org/",
        resolver=fake({"a.example.org": ["198.51.100.7"]}),
        allow_networks=net("198.51.100.0/24"),
    )
    assert ok.pinned_ip == "198.51.100.7"


def test_ipv6_documentation_network_listed() -> None:
    r = fake({"v6.example.org": ["2001:db8::5"]})
    with pytest.raises(OutboundURLError):
        pin_outbound_url("https://v6.example.org/", resolver=r)
    p = pin_outbound_url("https://v6.example.org/", resolver=r, allow_networks=net("2001:db8::/32"))
    assert p.pinned_ip == "2001:db8::5"


def test_outbound_itself_leaves_cgnat_alone() -> None:
    """Closing 100.64.0.0/10 is the GI egress layer's job; shared callers stay unchanged."""
    r = fake({"c.example.org": ["100.64.1.1"]})
    assert pin_outbound_url("https://c.example.org/", resolver=r).pinned_ip == "100.64.1.1"
