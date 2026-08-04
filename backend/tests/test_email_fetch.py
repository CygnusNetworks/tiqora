"""Tests for channels/email/fetch.py — IMAP/POP3 fetch loop.

The blocking imaplib/poplib clients are exercised via fakes that swap in for
``imaplib.IMAP4``/``imaplib.IMAP4_SSL``/``poplib.POP3``/``poplib.POP3_SSL``
(module-level monkeypatch, since ``fetch_account`` constructs the client
itself rather than accepting one as a dependency — there is no seam to
inject a mock client directly). Real socket I/O is never exercised.
"""

from __future__ import annotations

import imaplib
import poplib
from dataclasses import dataclass, field
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.fetch import fetch_account, list_valid_mail_accounts
from tiqora.db.legacy.mail_account import MailAccount

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _imap_factory(fake: _FakeImap) -> object:
    """A constructor stand-in for imaplib.IMAP4/IMAP4_SSL.

    fetch.py references ``imaplib.IMAP4.error`` in its exception handler
    independently of which constructor was called, so the replacement
    callable must still expose ``.error`` pointing at the real exception
    class — otherwise ``contextlib.suppress(..., imaplib.IMAP4.error)``
    blows up on a plain lambda/function object.
    """
    real_error = imaplib.IMAP4.error

    def _ctor(*args: object, **kwargs: object) -> _FakeImap:
        return fake

    _ctor.error = real_error  # type: ignore[attr-defined]
    return _ctor


def _account(**overrides: object) -> MailAccount:
    defaults: dict[str, object] = {
        "id": 1,
        "login": "user",
        "pw": "secret",
        "host": "mail.example.com",
        "account_type": "IMAP",
        "queue_id": 1,
        "trusted": 0,
        "imap_folder": None,
        "authentication_type": "password",
        "oauth2_token_config_id": None,
        "comments": None,
        "valid_id": 1,
        "create_time": NOW,
        "create_by": 1,
        "change_time": NOW,
        "change_by": 1,
    }
    defaults.update(overrides)
    return MailAccount(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Fake imaplib.IMAP4 / poplib.POP3 clients
# ---------------------------------------------------------------------------


@dataclass
class _FakeImap:
    """Stand-in for imaplib.IMAP4/IMAP4_SSL with a scripted mailbox."""

    select_status: str = "OK"
    uids: list[bytes] = field(default_factory=list)
    sizes: dict[bytes, int] = field(default_factory=dict)
    bodies: dict[bytes, bytes] = field(default_factory=dict)
    login_calls: list[tuple[str, str]] = field(default_factory=list)
    store_calls: list[tuple[str, str, str]] = field(default_factory=list)
    expunge_called: bool = False
    logout_called: bool = False
    raise_on_fetch_uid: bytes | None = None

    def login(self, login: str, pw: str) -> None:
        self.login_calls.append((login, pw))

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        return (self.select_status, [b"1"])

    def uid(self, command: str, *args: object) -> tuple[str, list[object]]:
        command = command.lower()
        if command == "search":
            return ("OK", [b" ".join(self.uids)] if self.uids else [b""])
        if command == "fetch":
            uid_str = str(args[0])
            uid_b = uid_str.encode("ascii")
            spec = args[1] if len(args) > 1 else ""
            if uid_b == self.raise_on_fetch_uid:
                raise OSError("simulated fetch failure")
            if "RFC822.SIZE" in str(spec):
                size = self.sizes.get(uid_b, 10)
                return ("OK", [f"1 (RFC822.SIZE {size})".encode()])
            body = self.bodies.get(uid_b, b"")
            if not body:
                return ("OK", [])
            return ("OK", [(b"1 (RFC822 {%d}" % len(body), body)])
        if command == "store":
            self.store_calls.append((str(args[0]), str(args[1]), str(args[2])))
            return ("OK", [])
        raise AssertionError(f"unexpected uid command {command}")

    def expunge(self) -> None:
        self.expunge_called = True

    def logout(self) -> None:
        self.logout_called = True


@dataclass
class _FakePop3:
    """Stand-in for poplib.POP3/POP3_SSL with a scripted mailbox (1-indexed)."""

    messages: dict[int, bytes] = field(default_factory=dict)
    user_calls: list[str] = field(default_factory=list)
    pass_calls: list[str] = field(default_factory=list)
    dele_calls: list[int] = field(default_factory=list)
    quit_called: bool = False
    raise_on_retr: int | None = None

    def user(self, login: str) -> None:
        self.user_calls.append(login)

    def pass_(self, pw: str) -> None:
        self.pass_calls.append(pw)

    def stat(self) -> tuple[int, int]:
        return (len(self.messages), sum(len(b) for b in self.messages.values()))

    def top(self, msg_num: int, lines: int) -> tuple[bytes, list[bytes], int]:
        body = self.messages[msg_num]
        return (b"+OK", [body], len(body))

    def retr(self, msg_num: int) -> tuple[bytes, list[bytes], int]:
        if msg_num == self.raise_on_retr:
            raise poplib.error_proto("simulated retr failure")
        body = self.messages[msg_num]
        return (b"+OK", [body], len(body))

    def dele(self, msg_num: int) -> None:
        self.dele_calls.append(msg_num)

    def quit(self) -> None:
        self.quit_called = True


# ---------------------------------------------------------------------------
# IMAP
# ---------------------------------------------------------------------------


async def test_fetch_account_imap_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeImap(
        uids=[b"1", b"2"],
        sizes={b"1": 10, b"2": 20},
        bodies={b"1": b"From: a@example.com\r\n\r\nhi", b"2": b"From: b@example.com\r\n\r\nyo"},
    )
    monkeypatch.setattr(imaplib, "IMAP4", _imap_factory(fake))
    account = _account(account_type="IMAP")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=False)

    assert result.errors == []
    assert result.oversized == 0
    assert {m.uid for m in result.messages} == {"1", "2"}
    assert fake.login_calls == [("user", "secret")]
    # Deleted (marked \Deleted) and expunged since leave_on_server=False.
    assert len(fake.store_calls) == 2
    assert all(call[1] == "+FLAGS" for call in fake.store_calls)
    assert fake.expunge_called is True
    assert fake.logout_called is True


async def test_fetch_account_imap_oauth2(monkeypatch: pytest.MonkeyPatch) -> None:
    """XOAUTH2 path uses authenticate instead of login."""
    fake = _FakeImap(
        uids=[b"1"],
        sizes={b"1": 5},
        bodies={b"1": b"From: a@example.com\r\n\r\nhi"},
    )
    auth_calls: list[tuple[str, object]] = []

    def authenticate(
        self: _FakeImap, mechanism: str, authobject: object
    ) -> tuple[str, list[bytes]]:
        auth_calls.append((mechanism, authobject))
        # Simulate imaplib calling the handler with an empty challenge.
        if callable(authobject):
            authobject(b"")
        return ("OK", [b""])

    fake.authenticate = authenticate.__get__(fake, _FakeImap)  # type: ignore[method-assign]
    monkeypatch.setattr(imaplib, "IMAP4", _imap_factory(fake))

    async def _fake_token(*_a: object, **_k: object) -> str:
        return "access-token-xyz"

    monkeypatch.setattr(
        "tiqora.domain.oauth2_mail.get_access_token",
        _fake_token,
    )
    monkeypatch.setattr(
        "tiqora.domain.oauth2_mail.ensure_oauth2_available",
        lambda: None,
    )

    account = _account(
        account_type="IMAP",
        authentication_type="oauth2_token",
        oauth2_token_config_id=42,
        pw="",
    )
    # session is required for oauth but get_access_token is mocked
    result = await fetch_account(
        account, max_size_kb=1024, leave_on_server=True, session=object()  # type: ignore[arg-type]
    )

    assert result.errors == []
    assert len(result.messages) == 1
    assert fake.login_calls == []
    assert auth_calls and auth_calls[0][0] == "XOAUTH2"


async def test_fetch_account_oauth2_missing_config_id() -> None:
    account = _account(
        account_type="IMAP",
        authentication_type="oauth2_token",
        oauth2_token_config_id=None,
    )
    result = await fetch_account(
        account, max_size_kb=1024, leave_on_server=True, session=object()  # type: ignore[arg-type]
    )
    assert result.messages == []
    assert any("oauth2_token_config_id" in e for e in result.errors)


async def test_fetch_account_imap_uses_ssl_for_imaps(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeImap()
    calls: list[tuple[str, int]] = []

    def _ssl_ctor(host: str, port: int, timeout: int) -> _FakeImap:
        calls.append((host, port))
        return fake

    _ssl_ctor.error = imaplib.IMAP4.error  # type: ignore[attr-defined]
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _ssl_ctor)
    account = _account(account_type="IMAPS", host="secure.example.com")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=False)

    assert result.errors == []
    assert calls == [("secure.example.com", 993)]


async def test_fetch_account_imap_leave_on_server_skips_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeImap(uids=[b"1"], sizes={b"1": 5}, bodies={b"1": b"msg body"})
    monkeypatch.setattr(imaplib, "IMAP4", _imap_factory(fake))
    account = _account(account_type="IMAP")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=True)

    assert len(result.messages) == 1
    assert fake.store_calls == []
    assert fake.expunge_called is False


async def test_fetch_account_imap_oversized_message_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeImap(
        uids=[b"1", b"2"],
        sizes={b"1": 2_000_000, b"2": 10},
        bodies={b"1": b"huge", b"2": b"small body"},
    )
    monkeypatch.setattr(imaplib, "IMAP4", _imap_factory(fake))
    account = _account(account_type="IMAP")

    result = await fetch_account(account, max_size_kb=1, leave_on_server=False)  # 1024 bytes max

    assert result.oversized == 1
    assert [m.uid for m in result.messages] == ["2"]
    # Oversized message still gets deleted from the server (not left to re-fetch forever).
    assert ("1", "+FLAGS", r"(\Deleted)") in fake.store_calls


async def test_fetch_account_imap_select_failure_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeImap(select_status="NO")
    monkeypatch.setattr(imaplib, "IMAP4", _imap_factory(fake))
    account = _account(account_type="IMAP")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=False)

    assert result.messages == []
    assert any("cannot select folder" in e for e in result.errors)


async def test_fetch_account_imap_per_message_error_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One message raising OSError must not abort the whole fetch loop."""
    fake = _FakeImap(
        uids=[b"1", b"2"],
        sizes={b"1": 5, b"2": 5},
        bodies={b"1": b"ok", b"2": b"ok2"},
        raise_on_fetch_uid=b"1",
    )
    monkeypatch.setattr(imaplib, "IMAP4", _imap_factory(fake))
    account = _account(account_type="IMAP")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=False)

    assert [m.uid for m in result.messages] == ["2"]
    assert any("uid 1" in e for e in result.errors)


async def test_fetch_account_imap_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a: object, **kw: object) -> _FakeImap:
        raise OSError("connection refused")

    _boom.error = imaplib.IMAP4.error  # type: ignore[attr-defined]
    monkeypatch.setattr(imaplib, "IMAP4", _boom)
    account = _account(account_type="IMAP")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=False)

    assert result.messages == []
    assert any("connection refused" in e for e in result.errors)


# ---------------------------------------------------------------------------
# POP3
# ---------------------------------------------------------------------------


async def test_fetch_account_pop3_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePop3(
        messages={1: b"From: a@example.com\r\n\r\nhi", 2: b"From: b@example.com\r\n\r\nyo"}
    )
    monkeypatch.setattr(poplib, "POP3", lambda *a, **kw: fake)
    account = _account(account_type="POP3")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=False)

    assert result.errors == []
    assert {m.uid for m in result.messages} == {"1", "2"}
    assert fake.user_calls == ["user"]
    assert fake.pass_calls == ["secret"]
    assert fake.dele_calls == [1, 2]
    assert fake.quit_called is True


async def test_fetch_account_pop3s_uses_ssl(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePop3()
    calls: list[tuple[str, int]] = []

    def _ssl_ctor(host: str, port: int, timeout: int) -> _FakePop3:
        calls.append((host, port))
        return fake

    monkeypatch.setattr(poplib, "POP3_SSL", _ssl_ctor)
    account = _account(account_type="POP3S", host="secure-pop.example.com")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=False)

    assert result.errors == []
    assert calls == [("secure-pop.example.com", 995)]


async def test_fetch_account_pop3_leave_on_server_skips_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePop3(messages={1: b"msg body"})
    monkeypatch.setattr(poplib, "POP3", lambda *a, **kw: fake)
    account = _account(account_type="POP3")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=True)

    assert len(result.messages) == 1
    assert fake.dele_calls == []


async def test_fetch_account_pop3_oversized_message_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePop3(messages={1: b"x" * 5000, 2: b"small"})
    monkeypatch.setattr(poplib, "POP3", lambda *a, **kw: fake)
    account = _account(account_type="POP3")

    result = await fetch_account(account, max_size_kb=1, leave_on_server=False)

    assert result.oversized == 1
    assert [m.uid for m in result.messages] == ["2"]
    assert 1 in fake.dele_calls  # oversized message still deleted from server


async def test_fetch_account_pop3_per_message_error_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePop3(messages={1: b"bad", 2: b"good"}, raise_on_retr=1)
    monkeypatch.setattr(poplib, "POP3", lambda *a, **kw: fake)
    account = _account(account_type="POP3")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=False)

    assert [m.uid for m in result.messages] == ["2"]
    assert any("msg 1" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Dispatch / unsupported type
# ---------------------------------------------------------------------------


async def test_fetch_account_unsupported_type_returns_error() -> None:
    account = _account(account_type="EWS")

    result = await fetch_account(account, max_size_kb=1024, leave_on_server=False)

    assert result.messages == []
    assert any("unsupported account_type" in e for e in result.errors)


# ---------------------------------------------------------------------------
# list_valid_mail_accounts (DB)
# ---------------------------------------------------------------------------


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_list_valid_mail_accounts_filters_invalid(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session, session.begin():
        await session.execute(text("DELETE FROM mail_account WHERE login LIKE 'fetchtest-%'"))
        await session.execute(
            text(
                "INSERT INTO mail_account (login, pw, host, account_type, queue_id, trusted,"
                " authentication_type, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES ('fetchtest-valid', 'pw', 'mail.example.com', 'IMAP', 1, 0,"
                " 'password', 1, current_timestamp, 1, current_timestamp, 1)"
            )
        )
        await session.execute(
            text(
                "INSERT INTO mail_account (login, pw, host, account_type, queue_id, trusted,"
                " authentication_type, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES ('fetchtest-invalid', 'pw', 'mail.example.com', 'IMAP', 1, 0,"
                " 'password', 2, current_timestamp, 1, current_timestamp, 1)"
            )
        )

    async with factory() as session:
        accounts = await list_valid_mail_accounts(session)

    logins = {a.login for a in accounts}
    assert "fetchtest-valid" in logins
    assert "fetchtest-invalid" not in logins

    await engine.dispose()
