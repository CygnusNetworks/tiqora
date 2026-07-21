"""Outbound URL validation and DNS-rebinding-resistant IP pinning.

Server-side HTTP clients (webhooks, SMS gateway, OIDC discovery) must not be
pointed at loopback, link-local, RFC1918, ULA, or metadata addresses. Call
:func:`validate_outbound_url` (or :func:`pin_outbound_url`) before every
egress request, connect to the **resolved IP** (not the hostname), and set
``follow_redirects=False`` so a public host cannot 302 into the internal net.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

# Resolver type: hostname + port → list of IP strings (A/AAAA).
HostResolver = Callable[[str, int], list[str]]


class OutboundURLError(ValueError):
    """Raised when a URL is rejected for server-side outbound use."""


def _default_resolve(hostname: str, port: int) -> list[str]:
    """Resolve *hostname* via ``socket.getaddrinfo`` (all A/AAAA)."""
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OutboundURLError(f"cannot resolve host {hostname!r}: {exc}") from exc
    ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        addr = info[4][0]
        if not isinstance(addr, str):
            continue
        if addr not in seen:
            seen.add(addr)
            ips.append(addr)
    if not ips:
        raise OutboundURLError(f"no addresses resolved for host {hostname!r}")
    return ips


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if *ip* must not be contacted by server-side clients.

    Blocks loopback, link-local (incl. cloud metadata ``169.254.169.254``),
    RFC1918 private, IPv6 unique-local, unspecified, and multicast.
    """
    # Mapped IPv4-in-IPv6 (::ffff:x.x.x.x) — evaluate the embedded v4 address.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _check_ip_string(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise OutboundURLError(f"invalid IP address {raw!r}") from exc
    if is_blocked_ip(ip):
        raise OutboundURLError(f"blocked address {ip}")
    return ip


@dataclass(frozen=True)
class PinnedOutboundURL:
    """A validated outbound URL with a pinned connect IP.

    Use :attr:`request_url` as the httpx URL (host is the IP), merge
    :meth:`request_headers` into the request headers (sets ``Host``), and pass
    :meth:`request_extensions` so TLS SNI/cert verification still uses the
    original hostname.
    """

    original_url: str
    scheme: str
    hostname: str
    port: int
    path: str  # path + optional ?query (fragment stripped)
    pinned_ip: str

    @property
    def request_url(self) -> str:
        host = f"[{self.pinned_ip}]" if ":" in self.pinned_ip else self.pinned_ip
        default_port = 443 if self.scheme == "https" else 80
        netloc = host if self.port == default_port else f"{host}:{self.port}"
        return urlunparse((self.scheme, netloc, self.path, "", "", ""))

    def host_header_value(self) -> str:
        default_port = 443 if self.scheme == "https" else 80
        if self.port == default_port:
            return self.hostname
        return f"{self.hostname}:{self.port}"

    def request_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers: dict[str, str] = {"Host": self.host_header_value()}
        if extra:
            # Caller-supplied Host must not override the pinned original host.
            for key, value in extra.items():
                if key.lower() == "host":
                    continue
                headers[key] = value
        return headers

    def request_extensions(self) -> dict[str, str]:
        if self.scheme == "https":
            return {"sni_hostname": self.hostname}
        return {}


def pin_outbound_url(
    url: str,
    *,
    require_https: bool = False,
    resolver: HostResolver | None = None,
) -> PinnedOutboundURL:
    """Validate *url* and return a :class:`PinnedOutboundURL` for safe egress.

    Raises :class:`OutboundURLError` when the scheme is wrong, the host cannot
    be resolved, or **any** resolved address is blocked.
    """
    if not url or not str(url).strip():
        raise OutboundURLError("URL must not be empty")

    parsed = urlparse(str(url).strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise OutboundURLError(f"URL scheme must be http or https, got {scheme!r}")
    if require_https and scheme != "https":
        raise OutboundURLError("URL scheme must be https")

    if parsed.username is not None or parsed.password is not None:
        raise OutboundURLError("URL must not contain userinfo credentials")

    hostname = parsed.hostname
    if not hostname:
        raise OutboundURLError("URL must include a hostname")

    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80

    # path + query; drop fragment (not sent on the wire for HTTP).
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    resolve = resolver or _default_resolve

    # Literal IP host — validate directly, no DNS.
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        if is_blocked_ip(literal):
            raise OutboundURLError(f"blocked address {literal}")
        pinned_ip = str(literal)
    else:
        resolved = resolve(hostname, port)
        checked: list[str] = []
        for raw in resolved:
            ip = _check_ip_string(raw)
            checked.append(str(ip))
        # Prefer IPv4 for broader reachability; fall back to first address.
        pinned_ip = next((a for a in checked if ":" not in a), checked[0])

    return PinnedOutboundURL(
        original_url=str(url).strip(),
        scheme=scheme,
        hostname=hostname,
        port=port,
        path=path,
        pinned_ip=pinned_ip,
    )


def validate_outbound_url(
    url: str,
    *,
    require_https: bool = False,
    resolver: HostResolver | None = None,
) -> None:
    """Validate *url* for server-side egress; raise :class:`OutboundURLError` if unsafe.

    Resolves the host and rejects the URL when any A/AAAA record is loopback,
    link-local, private (RFC1918), unique-local, unspecified, or multicast.
    Does not perform the HTTP request — call :func:`pin_outbound_url` when the
    pinned connect IP is needed for the client.
    """
    pin_outbound_url(url, require_https=require_https, resolver=resolver)
