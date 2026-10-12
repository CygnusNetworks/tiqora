"""Tiqora-side settings of one GenericInterface webservice (``tiqora_settings``).

The Znuny webservice YAML is shared with Znuny, so everything that only Tiqora knows
(which internal targets the requester may reach, whether TLS checks may be relaxed)
lives in ``tiqora_settings`` under ``gi.webservice.<id>.egress`` as a JSON value.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.domain.settings_store import get_setting, set_setting

KEY_PREFIX: Final = "gi.webservice."
MAX_TARGETS: Final = 50

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
# Networks that can never be allowed (loopback, link-local/metadata, multicast,
# unspecified, reserved): an entry that overlaps one of them is rejected.
_NEVER_NETWORKS: Final = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "fe80::/10",
        "ff00::/8",
    )
)


@dataclass(frozen=True, slots=True)
class WebserviceEgressSettings:
    allowed_targets: tuple[str, ...] = ()
    allow_insecure_tls: bool = False


def _key(ws_id: int) -> str:
    return f"{KEY_PREFIX}{ws_id}.egress"


def validate_targets(raw: Sequence[str]) -> tuple[str, ...]:
    """Normalise allowlist entries: CIDRs and exact hostnames, nothing else."""
    if len(raw) > MAX_TARGETS:
        raise ValueError(f"Höchstens {MAX_TARGETS} Einträge erlaubt")
    out: list[str] = []
    for entry in raw:
        item = str(entry).strip()
        if not item:
            raise ValueError("Leerer Eintrag")
        if any(ch in item for ch in ("*", "?", "@", " ")) or "://" in item:
            raise ValueError(f"Ungültiger Eintrag {item!r}: nur Hostnamen oder CIDR-Netze")
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            net = None
        if net is not None:
            if any(net.version == n.version and net.overlaps(n) for n in _NEVER_NETWORKS):
                raise ValueError(f"Netz {item} darf nicht freigegeben werden")
            out.append(str(net))
            continue
        if "/" in item or ":" in item:
            raise ValueError(f"Ungültiger Eintrag {item!r}: kein Schema, Port oder Pfad erlaubt")
        try:
            host = item.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError(f"Ungültiger Hostname {item!r}") from exc
        if not host or not all(_LABEL_RE.match(lbl) for lbl in host.split(".")):
            raise ValueError(f"Ungültiger Hostname {item!r}")
        out.append(host)
    return tuple(out)


async def get_egress_settings(session: AsyncSession, ws_id: int) -> WebserviceEgressSettings:
    raw = await get_setting(session, _key(ws_id))
    if not raw:
        return WebserviceEgressSettings()
    try:
        data: Any = json.loads(raw)
        targets = validate_targets([str(t) for t in data.get("allowed_targets", [])])
        return WebserviceEgressSettings(targets, data.get("allow_insecure_tls") is True)
    except (ValueError, AttributeError, TypeError):
        # A damaged value must not open anything: fall back to the closed default.
        return WebserviceEgressSettings()


async def set_egress_settings(
    session: AsyncSession, ws_id: int, value: WebserviceEgressSettings
) -> None:
    targets = validate_targets(value.allowed_targets)
    payload = {
        "allowed_targets": list(targets),
        "allow_insecure_tls": bool(value.allow_insecure_tls),
    }
    await set_setting(session, _key(ws_id), json.dumps(payload, sort_keys=True))
