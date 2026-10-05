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
import unicodedata
from dataclasses import dataclass

import httpx
import phonenumbers
from phonenumbers import PhoneNumberType
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.common import channel_setting

DEFAULT_INTERNAL = "60,61,62,69"
_DIGITS = re.compile(r"[^0-9]")
_BLOCKED_TYPES = frozenset({PhoneNumberType.PREMIUM_RATE})
_NAME_MAX = 40
_NAME_DROP = frozenset('"\\<>')


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
    region: str = "DE"


def normalize_dial_number(raw: str, internal: set[str] | frozenset[str], region: str = "DE") -> str:
    """Digits the PBX dials, checked with libphonenumber.

    Internal extensions from ``internal`` pass unchanged. Anything else must
    be a valid number for libphonenumber (``region`` for numbers without
    ``+``/``00``) and not premium rate; numbers of ``region`` are dialled in
    national format (``0...``), all others as ``00<cc><number>``.
    Raises ValueError.
    """
    text = (raw or "").strip()
    if not text or re.search(r"[A-Za-z*#]", text):
        raise ValueError("not a phone number")
    if any(ord(ch) > 127 and unicodedata.category(ch) == "Nd" for ch in text):
        raise ValueError("not a phone number")
    text = text.replace("(0)", "")
    digits_only = _DIGITS.sub("", text)
    if digits_only in internal and not text.startswith("+"):
        return digits_only
    try:
        parsed = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("not a phone number") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("not a valid phone number")
    if phonenumbers.number_type(parsed) in _BLOCKED_TYPES:
        raise ValueError("premium-rate numbers are not dialled")
    national = str(phonenumbers.national_significant_number(parsed))
    if phonenumbers.region_code_for_number(parsed) == region:
        return "0" + national
    return f"00{parsed.country_code}{national}"


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
    region_raw = (await channel_setting(session, "phone", "originate_region", "DE") or "").strip()
    region = region_raw.upper()
    if not re.fullmatch(r"[A-Z]{2}", region):
        region = "DE"
    internal = frozenset(p.strip() for p in (internal_raw or "").split(",") if p.strip())
    return OriginateConfig(
        ari_url=url.rstrip("/"),
        ari_user=user,
        ari_secret=secret,
        context=(context or "tiqora-dial").strip(),
        endpoint=(endpoint or "SIP/{extension}").strip(),
        timeout=timeout,
        internal=internal,
        region=region,
    )


def _display_name(raw: str) -> str:
    """Name for the callerId: no quotes, backslashes, angle brackets or
    control characters, whitespace collapsed, at most ``_NAME_MAX`` chars."""
    spaced = "".join(" " if ch.isspace() else ch for ch in raw or "")
    kept = "".join(
        ch for ch in spaced if ch not in _NAME_DROP and not unicodedata.category(ch).startswith("C")
    )
    return " ".join(kept.split())[:_NAME_MAX].strip()


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
    name = _display_name(caller_name)
    try:
        endpoint = config.endpoint.format(extension=extension)
    except (KeyError, IndexError, ValueError) as exc:
        raise OriginateError(f"invalid endpoint template: {exc!r}") from exc
    params = {
        "endpoint": endpoint,
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
