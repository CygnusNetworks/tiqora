"""Async SysConfig reader for Znuny ``sysconfig_default`` / ``sysconfig_modified``.

Effective value resolution matches Znuny deployment semantics: a valid
system-wide row in ``sysconfig_modified`` (``is_valid=1``, ``user_id IS NULL``)
overrides ``sysconfig_default.effective_value``. Values are YAML-serialized
(see ``Kernel/System/SysConfig/DB.pm``); plain strings are accepted as a
robust fallback.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Documented Znuny defaults (Framework.xml / Ticket.xml / Defaults.pm) used when
# sysconfig tables are empty (fresh schema without Config rebuild).
ZNUNY_SETTING_DEFAULTS: Final[dict[str, Any]] = {
    "SystemID": "10",
    "Ticket::NumberGenerator": "Kernel::System::Ticket::Number::DateChecksum",
    "Ticket::Hook": "Ticket#",
    "Ticket::HookDivider": "",
    # Ticket::SubjectFormat — Left | Right | None (Ticket.xml default Left).
    "Ticket::SubjectFormat": "Left",
    "Ticket::IndexModule": "Kernel::System::Ticket::IndexAccelerator::RuntimeDB",
    "OTRSTimeZone": "UTC",
    "DefaultLanguage": "en",
    "FQDN": "yourhost.example.com",
    # Postmaster (Phase 4a) — Kernel/Config/Files/XML/Ticket.xml defaults.
    "PostmasterMaxEmails": 40,
    "PostmasterMaxEmailsPerAddress": {},
    "PostMasterMaxEmailSize": 16384,
    "PostmasterDefaultQueue": "Raw",
    "PostmasterDefaultPriority": "3 normal",
    "PostmasterDefaultState": "new",
    "PostmasterFollowUpState": "open",
    "PostmasterFollowUpStateClosed": "open",
    "PostmasterBounceEmailAsFollowUp": 1,
    "PostmasterUserID": 1,
    # Envelope-only archive copy of all outgoing mail (Defaults.pm: empty).
    "SendmailBcc": "",
    # Session lifetime — Kernel/Config/Files/XML/Framework.xml defaults. Used by
    # the compat GenericInterface to reject Znuny session ids that Znuny's own
    # Kernel::System::AuthSession::DB::CheckSessionID would already refuse. Rows
    # linger in `sessions` until the cleanup daemon runs, so presence of a row
    # is NOT proof of validity.
    "SessionMaxTime": 57600,
    "SessionMaxIdleTime": 7200,
    # Composer auto-lock (Ticket.xml AgentTicket*###RequiredLock defaults).
    # Opening these screens on an unlocked ticket locks it and makes the agent
    # owner (AgentTicketActionCommon); Note deliberately absent — its default
    # is 0 and Tiqora never asks for it.
    "Ticket::Frontend::AgentTicketCompose###RequiredLock": 1,
    "Ticket::Frontend::AgentTicketForward###RequiredLock": 1,
    "Ticket::Frontend::AgentTicketBounce###RequiredLock": 1,
    "Ticket::Frontend::AgentTicketClose###RequiredLock": 1,
}

# Composer action name (API wire value) → RequiredLock sysconfig key.
REQUIRED_LOCK_ACTIONS: Final[dict[str, str]] = {
    "compose": "Ticket::Frontend::AgentTicketCompose###RequiredLock",
    "forward": "Ticket::Frontend::AgentTicketForward###RequiredLock",
    "bounce": "Ticket::Frontend::AgentTicketBounce###RequiredLock",
    "close": "Ticket::Frontend::AgentTicketClose###RequiredLock",
}

# Settings Tiqora currently needs typed accessors for.
TIQORA_SYSCONFIG_KEYS: Final[tuple[str, ...]] = tuple(ZNUNY_SETTING_DEFAULTS.keys())

FetchFn = Callable[[str], Awaitable[Any | None]]


def decode_effective_value(raw: Any) -> Any:
    """Decode a Znuny ``effective_value`` blob/string into a Python object.

    Znuny stores YAML dumps of scalars and structures. Empty / null YAML becomes
    None. Non-YAML plain strings are returned as-is.
    """
    if raw is None:
        return None
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, bytes | bytearray):
        try:
            text_value = raw.decode("utf-8")
        except UnicodeDecodeError:
            return bytes(raw)
    else:
        text_value = str(raw)

    stripped = text_value.strip()
    if stripped == "":
        return ""

    try:
        loaded = yaml.safe_load(text_value)
    except yaml.YAMLError:
        return text_value

    # yaml.safe_load("Ticket#") → "Ticket#"; bare numbers become int/float.
    return loaded


class SysConfig:
    """Cached async SysConfig reader.

    Parameters
    ----------
    session:
        Active async SQLAlchemy session bound to a Znuny-compatible database.
    ttl_seconds:
        In-memory cache TTL for resolved settings.
    fetch:
        Optional injectable async ``(name) -> raw_effective_value`` for unit tests.
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        *,
        ttl_seconds: float = 60.0,
        fetch: FetchFn | None = None,
    ) -> None:
        self._session = session
        self._ttl = ttl_seconds
        self._fetch = fetch
        self._cache: dict[str, tuple[float, Any]] = {}

    def clear_cache(self) -> None:
        """Drop all cached settings."""
        self._cache.clear()

    async def get(self, name: str, default: Any = None) -> Any:
        """Return the effective value for *name*, or *default* / code defaults."""
        now = time.monotonic()
        cached = self._cache.get(name)
        if cached is not None:
            expires, value = cached
            if now < expires:
                return value

        raw = await self._resolve_raw(name)
        if raw is None:
            value = ZNUNY_SETTING_DEFAULTS.get(name, default)
        else:
            value = decode_effective_value(raw)
            if value is None:
                value = ZNUNY_SETTING_DEFAULTS.get(name, default)

        self._cache[name] = (now + self._ttl, value)
        return value

    async def get_str(self, name: str, default: str | None = None) -> str:
        """Return setting as string."""
        fallback = default if default is not None else str(ZNUNY_SETTING_DEFAULTS.get(name, ""))
        value = await self.get(name, fallback)
        if value is None:
            return fallback
        return str(value)

    async def get_many(self, names: list[str] | tuple[str, ...]) -> dict[str, Any]:
        """Resolve several settings (each may hit cache / DB)."""
        return {name: await self.get(name) for name in names}

    # --- typed getters for settings Tiqora needs now ---

    async def system_id(self) -> str:
        return await self.get_str("SystemID")

    async def ticket_number_generator(self) -> str:
        return await self.get_str("Ticket::NumberGenerator")

    async def ticket_hook(self) -> str:
        return await self.get_str("Ticket::Hook")

    async def ticket_hook_divider(self) -> str:
        return await self.get_str("Ticket::HookDivider")

    async def ticket_subject_format(self) -> str:
        """Return ``Ticket::SubjectFormat`` (``Left`` / ``Right`` / ``None``)."""
        return await self.get_str("Ticket::SubjectFormat")

    async def ticket_index_module(self) -> str:
        return await self.get_str("Ticket::IndexModule")

    async def otrs_time_zone(self) -> str:
        return await self.get_str("OTRSTimeZone")

    async def default_language(self) -> str:
        return await self.get_str("DefaultLanguage")

    async def fqdn(self) -> str:
        return await self.get_str("FQDN")

    async def sendmail_bcc(self) -> str:
        """``SendmailBcc`` — extra envelope recipient for every outgoing mail."""
        return await self.get_str("SendmailBcc", "")

    # --- postmaster (Phase 4a) ---

    async def postmaster_max_emails(self) -> int:
        return int(await self.get("PostmasterMaxEmails", 40) or 40)

    async def postmaster_max_emails_per_address(self) -> dict[str, Any]:
        value = await self.get("PostmasterMaxEmailsPerAddress", {})
        return value if isinstance(value, dict) else {}

    async def postmaster_max_email_size_kb(self) -> int:
        return int(await self.get("PostMasterMaxEmailSize", 16384) or 16384)

    async def postmaster_default_queue(self) -> str:
        return await self.get_str("PostmasterDefaultQueue", "Raw")

    async def postmaster_default_priority(self) -> str:
        return await self.get_str("PostmasterDefaultPriority", "3 normal")

    async def postmaster_default_state(self) -> str:
        return await self.get_str("PostmasterDefaultState", "new")

    async def postmaster_followup_state(self) -> str:
        return await self.get_str("PostmasterFollowUpState", "open")

    async def postmaster_followup_state_closed(self) -> str:
        return await self.get_str("PostmasterFollowUpStateClosed", "open")

    async def postmaster_bounce_as_followup(self) -> bool:
        return bool(await self.get("PostmasterBounceEmailAsFollowUp", 1))

    async def postmaster_user_id(self) -> int:
        return int(await self.get("PostmasterUserID", 1) or 1)

    # --- session lifetime (compat GenericInterface) ---

    async def session_max_time(self) -> int:
        """``SessionMaxTime`` — absolute session lifetime in seconds."""
        return int(await self.get("SessionMaxTime", 57600) or 57600)

    async def session_max_idle_time(self) -> int:
        """``SessionMaxIdleTime`` — idle timeout in seconds."""
        return int(await self.get("SessionMaxIdleTime", 7200) or 7200)

    async def required_lock(self, action: str) -> bool:
        """``Ticket::Frontend::AgentTicket*###RequiredLock`` for a composer action.

        *action* is the API wire value (``compose``/``forward``/``bounce``/
        ``close``). Unknown actions never require a lock.
        """
        key = REQUIRED_LOCK_ACTIONS.get(action)
        if key is None:
            return False
        value = await self.get(key, 1)
        return bool(int(value or 0))

    async def tiqora_settings(self) -> dict[str, Any]:
        """All settings currently required by Tiqora core."""
        return await self.get_many(TIQORA_SYSCONFIG_KEYS)

    async def _resolve_raw(self, name: str) -> Any | None:
        if self._fetch is not None:
            return await self._fetch(name)
        if self._session is None:
            return None
        return await _fetch_effective_from_db(self._session, name)


async def _fetch_effective_from_db(session: AsyncSession, name: str) -> Any | None:
    """Prefer sysconfig_modified (system-wide) over sysconfig_default."""
    # Modified system-wide override (user_id IS NULL, is_valid = 1)
    mod_sql = text(
        """
        SELECT effective_value
        FROM sysconfig_modified
        WHERE name = :name
          AND is_valid = 1
          AND user_id IS NULL
        ORDER BY id DESC
        LIMIT 1
        """
    )
    result = await session.execute(mod_sql, {"name": name})
    row = result.first()
    if row is not None:
        return row[0]

    def_sql = text(
        """
        SELECT effective_value
        FROM sysconfig_default
        WHERE name = :name
          AND is_valid = 1
        LIMIT 1
        """
    )
    result = await session.execute(def_sql, {"name": name})
    row = result.first()
    if row is not None:
        return row[0]
    return None


def yaml_encode_effective(value: Any) -> bytes:
    """Encode a Python value the way Znuny stores ``effective_value`` (YAML dump)."""
    dumped = yaml.safe_dump(value, default_flow_style=False, allow_unicode=True)
    return dumped.encode("utf-8")


# Re-export for type checkers / callers that build fixtures
__all__ = [
    "TIQORA_SYSCONFIG_KEYS",
    "ZNUNY_SETTING_DEFAULTS",
    "SysConfig",
    "decode_effective_value",
    "yaml_encode_effective",
]
