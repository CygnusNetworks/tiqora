"""Unit tests for the Znuny session lifetime gate in the compat GenericInterface.

Ports the behaviour of ``Kernel::System::AuthSession::DB::CheckSessionID``
(Znuny 6.x): a row in the ``sessions`` table is only good while it is inside
both ``SessionMaxIdleTime`` and ``SessionMaxTime``, and — when
``SessionCheckRemoteIP`` is on — only from the address it was bound to.

These are pure-function tests; the DB round-trip is covered by
``test_compat_operations.py::test_session_id_auth_rejects_expired``.
"""

from __future__ import annotations

import pytest

from tiqora.api.compat.operations import _peer_addr, _session_epoch, _znuny_session_expired

NOW = 1_800_000_000
MAX_IDLE = 7200  # Znuny SessionMaxIdleTime default (2h)
MAX_TIME = 57600  # Znuny SessionMaxTime default (16h)


def _check(data: dict[str, str]) -> bool:
    return _znuny_session_expired(data, now=NOW, max_idle_time=MAX_IDLE, max_time=MAX_TIME)


def test_fresh_session_is_valid() -> None:
    assert not _check({"UserLastRequest": str(NOW), "UserSessionStart": str(NOW)})


def test_session_just_inside_both_windows_is_valid() -> None:
    """One second short of each limit still passes — the gate is not off by one."""
    assert not _check(
        {
            "UserLastRequest": str(NOW - MAX_IDLE + 1),
            "UserSessionStart": str(NOW - MAX_TIME + 1),
        }
    )


def test_idle_timeout_expires() -> None:
    assert _check(
        {"UserLastRequest": str(NOW - MAX_IDLE - 1), "UserSessionStart": str(NOW - MAX_IDLE - 1)}
    )


def test_idle_boundary_is_inclusive_like_znuny() -> None:
    """Znuny uses ``>=``, so exactly at the limit the session is already dead."""
    assert _check({"UserLastRequest": str(NOW - MAX_IDLE), "UserSessionStart": str(NOW - MAX_IDLE)})


def test_absolute_lifetime_expires_despite_recent_activity() -> None:
    """A continuously used session still dies at SessionMaxTime."""
    assert _check({"UserLastRequest": str(NOW), "UserSessionStart": str(NOW - MAX_TIME - 1)})


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"UserLastRequest": str(NOW)},  # no start stamp
        {"UserSessionStart": str(NOW)},  # no last-request stamp
        {"UserLastRequest": "not-a-number", "UserSessionStart": str(NOW)},
        {"UserLastRequest": str(NOW), "UserSessionStart": ""},
    ],
)
def test_unparseable_or_missing_stamps_are_treated_as_expired(data: dict[str, str]) -> None:
    """Fail closed: a row we cannot date is a row we cannot vouch for."""
    assert _check(data)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("123", 123), (" 123 ", 123), ("", None), ("abc", None), (None, None)],
)
def test_session_epoch_parsing(raw: str | None, expected: int | None) -> None:
    assert _session_epoch(raw) == expected


class _Client:
    def __init__(self, host: str | None) -> None:
        self.host = host


class _Request:
    def __init__(self, host: str | None) -> None:
        self.client = _Client(host) if host is not None else None
        self.headers: dict[str, str] = {}


def test_peer_addr_returns_none_without_request() -> None:
    assert _peer_addr(None) is None


def test_peer_addr_returns_none_for_unknown_peer() -> None:
    """``client_ip`` falls back to the literal "unknown"; comparing that against
    a stored address would fail every remote-IP binding check."""
    assert _peer_addr(_Request(None)) is None


def test_peer_addr_returns_socket_peer() -> None:
    assert _peer_addr(_Request("203.0.113.10")) == "203.0.113.10"
