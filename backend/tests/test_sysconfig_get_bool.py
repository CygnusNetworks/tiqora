"""Znuny checkbox SysConfig values must be read with Perl truthiness (``'0'`` is false)."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.znuny import followup
from tiqora.znuny.followup import detect_followup
from tiqora.znuny.sysconfig import SysConfig

_GEN = "Kernel::System::Ticket::Number::DateChecksum"


def _cfg(values: dict[str, bytes | None]) -> SysConfig:
    async def fetch(name: str) -> bytes | None:
        return values.get(name)

    return SysConfig(fetch=fetch, ttl_seconds=0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (b"--- '0'\n", False),
        (b"--- '1'\n", True),
        (b"--- 0\n", False),
        (b"--- 1\n", True),
        (b"--- ''\n", False),
        (b"--- '00'\n", True),  # Perl: only "" and "0" are false
    ],
)
async def test_get_bool_perl_truthiness(stored: bytes, expected: bool) -> None:
    # default=True would mask a wrong False, default=False a wrong True -> check both
    for default in (True, False):
        cfg = _cfg({"X::Checkbox": stored})
        assert await cfg.get_bool("X::Checkbox", default) is expected


@pytest.mark.asyncio
async def test_get_bool_default_only_when_absent() -> None:
    cfg = _cfg({})
    assert await cfg.get_bool("X::Missing") is False
    assert await cfg.get_bool("X::Missing", True) is True


@pytest.mark.asyncio
async def test_bounce_as_followup_and_required_lock_respect_string_zero() -> None:
    cfg = _cfg(
        {
            "PostmasterBounceEmailAsFollowUp": b"--- '0'\n",
            "Ticket::Frontend::AgentTicketCompose###RequiredLock": b"--- '0'\n",
            "Ticket::Frontend::AgentTicketClose###RequiredLock": b"--- '1'\n",
        }
    )
    assert await cfg.postmaster_bounce_as_followup() is False
    assert await cfg.required_lock("compose") is False
    assert await cfg.required_lock("close") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("stored", "found"), [(b"--- '0'\n", True), (b"--- '1'\n", False)])
async def test_detect_followup_check_system_id_string_zero(
    monkeypatch: pytest.MonkeyPatch, stored: bytes, found: bool
) -> None:
    async def fake_check(_session: Any, tn: str) -> int | None:
        return 5 if tn else None

    monkeypatch.setattr(followup, "ticket_check_number", fake_check)
    cfg = _cfg(
        {
            "SystemID": b"--- '10'\n",
            "Ticket::NumberGenerator": f"--- {_GEN}\n".encode(),
            "Ticket::NumberGenerator::CheckSystemID": stored,
        }
    )
    # 9th/10th digits are "01", not SystemID "10": only matches while the check is off.
    result = await detect_followup(
        None,  # type: ignore[arg-type]
        cfg,
        subject="[Ticket#202607190123456789]",
        references=[],
        hook="Ticket#",
        hook_divider="",
    )
    assert (result is not None) is found
