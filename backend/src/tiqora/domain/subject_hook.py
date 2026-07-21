"""Effective ticket-subject-hook configuration.

Tiqora override (``tiqora_settings``) takes precedence over live Znuny SysConfig
so parallel-operation deployments can tune the hook without writing Znuny
tables. Empty / missing override strings re-inherit from SysConfig (or the
documented Znuny defaults). Used by both outbound subject building and inbound
follow-up detection so the two paths never diverge.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.domain.settings_store import get_setting
from tiqora.znuny.sysconfig import SysConfig

# tiqora_settings keys (additive only — never written to Znuny SysConfig).
KEY_SUBJECT_HOOK_ENABLED = "ticket.subject_hook_enabled"
KEY_SUBJECT_HOOK = "ticket.subject_hook"
KEY_SUBJECT_HOOK_DIVIDER = "ticket.subject_hook_divider"
KEY_SUBJECT_FORMAT = "ticket.subject_format"

_VALID_SUBJECT_FORMATS = frozenset({"Left", "Right", "None"})


@dataclass(frozen=True, slots=True)
class SubjectHookConfig:
    """Resolved subject-hook settings for build + match."""

    enabled: bool
    hook: str
    divider: str
    subject_format: str  # "Left" | "Right" | "None"


def _parse_enabled(raw: str | None, *, default: bool = True) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _normalize_format(value: str | None, *, default: str = "Left") -> str:
    if not value or not str(value).strip():
        return default
    candidate = str(value).strip()
    for allowed in _VALID_SUBJECT_FORMATS:
        if candidate.lower() == allowed.lower():
            return allowed
    return default


async def load_subject_config(session: AsyncSession, sysconfig: SysConfig) -> SubjectHookConfig:
    """Resolve effective subject-hook config (Tiqora override > Znuny > defaults).

    Empty override string ⇒ fall through to Znuny (clearing a field re-syncs).
    ``enabled`` defaults to True (desired fix); there is no Znuny equivalent.
    """
    znuny_hook = str(await sysconfig.ticket_hook() or "Ticket#")
    znuny_divider = str(await sysconfig.ticket_hook_divider() or "")
    znuny_format = _normalize_format(await sysconfig.ticket_subject_format(), default="Left")

    ov_enabled = await get_setting(session, KEY_SUBJECT_HOOK_ENABLED)
    ov_hook = await get_setting(session, KEY_SUBJECT_HOOK)
    ov_divider = await get_setting(session, KEY_SUBJECT_HOOK_DIVIDER)
    ov_format = await get_setting(session, KEY_SUBJECT_FORMAT)

    enabled = _parse_enabled(ov_enabled, default=True)
    hook = ov_hook.strip() if ov_hook and ov_hook.strip() else znuny_hook
    # Empty / missing divider override re-inherits Znuny (which may itself be "").
    divider = ov_divider if ov_divider is not None and ov_divider != "" else znuny_divider
    if ov_format is not None and ov_format.strip() != "":
        subject_format = _normalize_format(ov_format, default=znuny_format)
    else:
        subject_format = znuny_format

    return SubjectHookConfig(
        enabled=enabled,
        hook=hook,
        divider=divider,
        subject_format=subject_format,
    )


async def load_znuny_subject_baseline(
    sysconfig: SysConfig,
) -> tuple[str, str, str]:
    """Return (hook, divider, subject_format) from Znuny SysConfig / defaults only."""
    hook = str(await sysconfig.ticket_hook() or "Ticket#")
    divider = str(await sysconfig.ticket_hook_divider() or "")
    subject_format = _normalize_format(await sysconfig.ticket_subject_format(), default="Left")
    return hook, divider, subject_format


__all__ = [
    "KEY_SUBJECT_FORMAT",
    "KEY_SUBJECT_HOOK",
    "KEY_SUBJECT_HOOK_DIVIDER",
    "KEY_SUBJECT_HOOK_ENABLED",
    "SubjectHookConfig",
    "load_subject_config",
    "load_znuny_subject_baseline",
]
