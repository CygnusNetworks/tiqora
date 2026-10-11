"""Egress policy of the GenericInterface requester.

Every outbound call of a webservice goes through here: the SSRF guard with the
per-webservice allowlist, certificate files only from configured directories, TLS
relaxation only by opt-in, proxy hosts checked like targets, and an HTTP client that
never follows redirects, never reads proxy environment variables and caps the response.
Error messages are German plain words for the debugger and never contain secrets.
"""

from __future__ import annotations

import base64
import ipaddress
import os
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.gi.ws_settings import get_egress_settings
from tiqora.security.outbound import (
    CGNAT_NETWORK,
    HostResolver,
    OutboundURLError,
    PinnedOutboundURL,
    _default_resolve,
    pin_outbound_url,
)

_MAX_CERT_BYTES: Final = 1024 * 1024
_MIB: Final = 1024 * 1024


class EgressError(Exception):
    """A call is refused by the egress policy (message is safe to show)."""


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    allow_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
    allow_hosts: frozenset[str]
    allow_insecure_tls: bool
    cert_dirs: tuple[Path, ...]
    max_timeout: float
    max_response_bytes: int


def _real_dirs(raw: list[str]) -> list[Path]:
    return [Path(os.path.realpath(r)) for r in raw]


async def load_policy(session: AsyncSession, ws_id: int, settings: Settings) -> EgressPolicy:
    ws = await get_egress_settings(session, ws_id)
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    hosts: set[str] = set()
    for target in ws.allowed_targets:
        try:
            nets.append(ipaddress.ip_network(target, strict=False))
        except ValueError:
            hosts.add(target.lower())
    cert_dirs = _real_dirs(settings.gi_cert_dirs)
    return EgressPolicy(
        allow_networks=tuple(nets),
        allow_hosts=frozenset(hosts),
        allow_insecure_tls=ws.allow_insecure_tls,
        cert_dirs=tuple(cert_dirs),
        max_timeout=float(settings.gi_max_timeout_seconds),
        max_response_bytes=int(settings.gi_max_response_bytes),
    )


def _cgnat_guard(
    resolver: HostResolver | None, allowed: bool, policy: EgressPolicy
) -> HostResolver:
    """Wrap the resolver so one carrier-grade-NAT record rejects the URL (unless listed)."""
    base = resolver or _default_resolve

    def resolve(host: str, port: int) -> list[str]:
        ips = base(host, port)
        if not allowed:
            for raw in ips:
                _check_cgnat(raw, policy)
        return ips

    return resolve


def _check_cgnat(raw: str, policy: EgressPolicy) -> None:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        isinstance(ip, ipaddress.IPv4Address)
        and ip in CGNAT_NETWORK
        and not any(n.version == 4 and ip in n for n in policy.allow_networks)
    ):
        raise OutboundURLError(f"Adresse {ip} (Carrier-Grade-NAT) ist nicht freigegeben")


def pin(
    url: str, policy: EgressPolicy, *, resolver: HostResolver | None = None
) -> PinnedOutboundURL:
    host = (urlparse(str(url).strip()).hostname or "").lower()
    host_allowed = host in policy.allow_hosts
    try:
        if not host_allowed:
            # A literal CGNAT address never reaches the resolver, check it here.
            _check_cgnat(host, policy)
        return pin_outbound_url(
            url,
            resolver=_cgnat_guard(resolver, host_allowed, policy),
            allow_private_networks=host_allowed,
            allow_networks=policy.allow_networks,
        )
    except OutboundURLError as exc:
        raise EgressError(
            f"Ziel {host or url} ist nicht erlaubt: {exc}. Interne Ziele können unter "
            "Webservices → Einstellungen freigegeben werden."
        ) from exc


def resolve_cert_path(raw: str, policy: EgressPolicy, *, directory: bool = False) -> Path:
    """Resolve *raw* and require it to lie inside a configured certificate directory."""
    if not policy.cert_dirs:
        raise EgressError(
            "Client-Zertifikate und CA-Dateien sind deaktiviert "
            "(kein Zertifikatsverzeichnis konfiguriert, TIQORA_GI_CERT_DIRS)."
        )
    try:
        path = Path(str(raw)).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise EgressError("Zertifikatsdatei nicht gefunden oder nicht lesbar.") from exc
    if not any(path.is_relative_to(d) for d in policy.cert_dirs):
        raise EgressError("Zertifikatsdatei liegt nicht in einem freigegebenen Verzeichnis.")
    if directory:
        if not path.is_dir():
            raise EgressError("Zertifikatsverzeichnis ist kein Verzeichnis.")
        return path
    if not path.is_file():
        raise EgressError("Zertifikatspfad ist keine reguläre Datei.")
    if path.stat().st_size > _MAX_CERT_BYTES:
        raise EgressError("Zertifikatsdatei ist größer als 1 MiB.")
    return path


def build_ssl_context(
    *,
    policy: EgressPolicy,
    verify_hostname: bool,
    cert_file: str | None,
    key_file: str | None,
    key_password: str | None,
    ca_file: str | None,
    ca_dir: str | None,
) -> ssl.SSLContext:
    if not verify_hostname and not policy.allow_insecure_tls:
        raise EgressError(
            "Die Prüfung des Hostnamens abzuschalten (SSLNoHostnameVerification) ist für "
            "diesen Webservice nicht freigegeben."
        )
    ca_path = resolve_cert_path(ca_file, policy) if ca_file else None
    ca_dir_path = resolve_cert_path(ca_dir, policy, directory=True) if ca_dir else None
    cert_path = resolve_cert_path(cert_file, policy) if cert_file else None
    key_path = resolve_cert_path(key_file, policy) if key_file else None
    try:
        ctx = ssl.create_default_context()
        if ca_path or ca_dir_path:
            ctx.load_verify_locations(
                cafile=str(ca_path) if ca_path else None,
                capath=str(ca_dir_path) if ca_dir_path else None,
            )
        if cert_path:
            ctx.load_cert_chain(
                str(cert_path), str(key_path) if key_path else None, key_password or ""
            )
    except (ssl.SSLError, OSError, ValueError) as exc:
        raise EgressError("Zertifikat oder Schlüssel konnte nicht geladen werden.") from exc
    if not verify_hostname:
        # Znuny parity (REST.pm SSL_verify_mode => 0): the opt-in disables peer verification
        # entirely, so self-signed and private-CA endpoints work. check_hostname must go first.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def proxy_for(
    transport_config: Mapping[str, Any], policy: EgressPolicy
) -> tuple[str | None, dict[str, str]]:
    proxy = transport_config.get("Proxy")
    if not isinstance(proxy, Mapping) or str(proxy.get("UseProxy", "")) != "Yes":
        return None, {}
    if str(proxy.get("ProxyExclude", "")) == "Yes":
        return None, {}
    host = str(proxy.get("ProxyHost") or "").strip()
    if not host:
        return None, {}
    pinned = pin(host, policy)
    # An http proxy is reached by the pinned IP; an https proxy needs its name for TLS.
    url = pinned.request_url if pinned.scheme == "http" else pinned.original_url
    headers: dict[str, str] = {}
    user, password = str(proxy.get("ProxyUser") or ""), str(proxy.get("ProxyPassword") or "")
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        headers["Proxy-Authorization"] = f"Basic {token}"
    return url, headers


def client(
    *,
    policy: EgressPolicy,
    ssl_context: ssl.SSLContext | None,
    proxy: str | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        verify=ssl_context if ssl_context is not None else True,
        proxy=proxy,
        transport=transport,
        timeout=policy.max_timeout,
    )


async def read_capped(response: httpx.Response, limit: int) -> bytes:
    def too_big() -> EgressError:
        return EgressError(f"Antwort größer als {limit / _MIB:g} MiB")

    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise too_big()
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            raise too_big()
    return bytes(body)
