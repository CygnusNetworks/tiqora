"""Unit tests for the customer-portal on/off resolution (no DB needed)."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.config import Settings
from tiqora.domain.portal_gate import portal_enabled, portal_locked_by_env


class _Result:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


class _FakeSession:
    """Stands in for AsyncSession: every execute() returns the same stored value."""

    def __init__(self, stored: str | None) -> None:
        self.stored = stored

    async def execute(self, stmt: Any) -> _Result:
        del stmt
        return _Result(self.stored)


def _settings(*, portal: bool) -> Settings:
    return Settings(environment="test", portal_enabled=portal)


def test_locked_by_env_is_true_only_when_env_disables_the_portal() -> None:
    assert portal_locked_by_env(_settings(portal=False)) is True
    assert portal_locked_by_env(_settings(portal=True)) is False


@pytest.mark.asyncio
async def test_enabled_by_default_when_neither_env_nor_db_say_otherwise() -> None:
    assert await portal_enabled(_FakeSession(None), _settings(portal=True)) is True


@pytest.mark.asyncio
async def test_db_row_can_switch_the_portal_off() -> None:
    assert await portal_enabled(_FakeSession("0"), _settings(portal=True)) is False


@pytest.mark.asyncio
async def test_db_row_can_switch_the_portal_on() -> None:
    assert await portal_enabled(_FakeSession("1"), _settings(portal=True)) is True


@pytest.mark.asyncio
async def test_env_hard_off_beats_an_enabling_db_row() -> None:
    assert await portal_enabled(_FakeSession("1"), _settings(portal=False)) is False
