"""Click-to-dial: ring the agent's desk phone through Asterisk ARI, then
dial the customer (``POST /api/v1/phone/dial``).

ARI ``POST /channels`` with ``endpoint`` = the agent's extension and
``extension``/``context`` = the number in the PBX's ``[tiqora-dial]``
context: Asterisk rings the desk phone first and runs the dialplan (the
outbound trunk call) once the agent picks up. Settings live in
``channel.phone.originate_*`` (admin: channels).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.common import channel_setting

DEFAULT_INTERNAL = "60,61,62,69"
_DIGITS = re.compile(r"\D")
_EXTERNAL = re.compile(r"^0\d{4,}$")


class OriginateError(Exception):
    """ARI refused or could not be reached."""


@dataclass(frozen=True)
class OriginateConfig:
    ari_url: str
    ari_user: str
    ari_secret: str
    context: str
    endpoint: str
    timeout: int
    internal: frozenset[str]


def normalize_dial_number(raw: str, internal: set[str] | frozenset[str]) -> str:
    """Digits the PBX dials: ``+49…`` → ``0…``, ``+…`` → ``00…``.

    Allowed: an internal extension from ``internal`` or ``0`` plus at least
    four digits (``[outcalls]`` matches ``_*0XXX.``). Raises ValueError.
    """
    text = (raw or "").strip()
    if not text or re.search(r"[A-Za-z*#]", text):
        raise ValueError("not a phone number")
    plus = text.startswith("+")
    digits = _DIGITS.sub("", text)
    if plus:
        digits = "0" + digits[2:] if digits.startswith("49") else "00" + digits
    if digits in internal:
        return digits
    if not _EXTERNAL.match(digits):
        raise ValueError("number too short or not dialable")
    return digits


async def load_originate_config(session: AsyncSession) -> OriginateConfig | None:
    """The originate settings, or None when switched off or incomplete."""
    enabled = (await channel_setting(session, "phone", "originate_enabled") or "").strip()
    if enabled.lower() not in ("1", "true", "yes", "on"):
        return None
    url = (await channel_setting(session, "phone", "originate_ari_url") or "").strip()
    user = (await channel_setting(session, "phone", "originate_ari_user") or "").strip()
    secret = await channel_setting(session, "phone", "originate_ari_secret") or ""
    if not url or not user or not secret:
        return None
    context = await channel_setting(session, "phone", "originate_context", "tiqora-dial")
    endpoint = await channel_setting(session, "phone", "originate_endpoint", "SIP/{extension}")
    timeout_raw = await channel_setting(session, "phone", "originate_timeout", "30")
    internal_raw = await channel_setting(session, "phone", "originate_internal", DEFAULT_INTERNAL)
    try:
        timeout = max(5, min(120, int(timeout_raw or "30")))
    except ValueError:
        timeout = 30
    internal = frozenset(p.strip() for p in (internal_raw or "").split(",") if p.strip())
    return OriginateConfig(
        ari_url=url.rstrip("/"),
        ari_user=user,
        ari_secret=secret,
        context=(context or "tiqora-dial").strip(),
        endpoint=(endpoint or "SIP/{extension}").strip(),
        timeout=timeout,
        internal=internal,
    )


async def originate(
    config: OriginateConfig,
    extension: str,
    number: str,
    caller_name: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Ring ``extension``; on answer Asterisk dials ``number`` in ``context``.

    The callerId is what the desk phone shows while it rings: who is about
    to be called."""
    name = caller_name.replace('"', "").strip()
    params = {
        "endpoint": config.endpoint.format(extension=extension),
        "extension": number,
        "context": config.context,
        "priority": "1",
        "timeout": str(config.timeout),
        "callerId": f'"{name or number}" <{number}>',
    }
    try:
        async with httpx.AsyncClient(
            auth=(config.ari_user, config.ari_secret), timeout=5.0, transport=transport
        ) as client:
            resp = await client.post(f"{config.ari_url}/channels", params=params)
    except httpx.HTTPError as exc:
        raise OriginateError(f"ARI unreachable: {exc}") from exc
    if resp.status_code >= 300:
        raise OriginateError(f"ARI {resp.status_code}: {resp.text[:200]}")
