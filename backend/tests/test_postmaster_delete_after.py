"""Postmaster deletes mailbox messages only after a successful process."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from tiqora.channels.email.fetch import FetchedMessage, FetchResult
from tiqora.channels.email.pipeline import PipelineResult
from tiqora.db.legacy.mail_account import MailAccount
from tiqora.worker import postmaster as postmaster_mod
from tiqora.worker.postmaster import process_account

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _account() -> MailAccount:
    return MailAccount(
        id=1,
        login="user",
        pw="secret",
        host="mail.example.com",
        account_type="POP3S",
        queue_id=1,
        trusted=0,
        imap_folder=None,
        authentication_type="password",
        oauth2_token_config_id=None,
        comments=None,
        valid_id=1,
        create_time=NOW,
        create_by=1,
        change_time=NOW,
        change_by=1,
    )


class _DummySession:
    async def __aenter__(self) -> _DummySession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def begin(self) -> _DummySession:
        return self


class _DummyFactory:
    def __call__(self) -> _DummySession:
        return _DummySession()


async def test_process_account_deletes_only_successful_uids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = _account()
    fetched = FetchResult(
        account_id=account.id,
        messages=[
            FetchedMessage(raw=b"ok-mail", uid="UID-OK"),
            FetchedMessage(raw=b"bad-mail", uid="UID-BAD"),
            FetchedMessage(raw=b"ok-mail-2", uid="UID-OK2"),
        ],
        oversized=0,
        errors=[],
    )
    fetch_kwargs: list[dict[str, Any]] = []
    deleted: list[list[str]] = []

    async def fake_fetch(_account: MailAccount, **kwargs: Any) -> FetchResult:
        fetch_kwargs.append(kwargs)
        return fetched

    async def fake_process(_session: Any, *_a: Any, raw: bytes, **_k: Any) -> PipelineResult:
        if raw == b"bad-mail":
            raise RuntimeError("dispatch boom")
        return PipelineResult(outcome="new_ticket", ticket_id=1)

    async def fake_delete(_account: MailAccount, uids: list[str], **_k: Any) -> list[str]:
        deleted.append(list(uids))
        return []

    async def fake_max_size(_factory: Any) -> int:
        return 1024

    monkeypatch.setattr(postmaster_mod, "fetch_account", fake_fetch)
    monkeypatch.setattr(postmaster_mod, "process_message_and_respond", fake_process)
    monkeypatch.setattr(postmaster_mod, "delete_messages", fake_delete)
    monkeypatch.setattr(postmaster_mod, "_max_email_size_kb", fake_max_size)

    class _SysConfig:
        async def postmaster_user_id(self) -> int:
            return 1

    monkeypatch.setattr(postmaster_mod, "SysConfig", lambda _session: _SysConfig())

    stats = await process_account(
        _DummyFactory(),  # type: ignore[arg-type]
        account,
        settings=None,  # type: ignore[arg-type]
        mail_sender=object(),  # type: ignore[arg-type]
        leave_on_server=False,
    )

    assert fetch_kwargs[0]["leave_on_server"] is True
    assert stats["fetched"] == 3
    assert stats["created"] == 2
    assert stats["errors"] == 1
    assert deleted == [["UID-OK", "UID-OK2"]]


async def test_process_account_leave_on_server_skips_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = _account()
    fetched = FetchResult(
        account_id=account.id,
        messages=[FetchedMessage(raw=b"ok-mail", uid="UID-OK")],
        oversized=0,
        errors=[],
    )
    deleted: list[list[str]] = []

    async def fake_fetch(_account: MailAccount, **_k: Any) -> FetchResult:
        return fetched

    async def fake_process(_session: Any, *_a: Any, **_k: Any) -> PipelineResult:
        return PipelineResult(outcome="new_ticket", ticket_id=1)

    async def fake_delete(_account: MailAccount, uids: list[str], **_k: Any) -> list[str]:
        deleted.append(list(uids))
        return []

    async def fake_max_size(_factory: Any) -> int:
        return 1024

    monkeypatch.setattr(postmaster_mod, "fetch_account", fake_fetch)
    monkeypatch.setattr(postmaster_mod, "process_message_and_respond", fake_process)
    monkeypatch.setattr(postmaster_mod, "delete_messages", fake_delete)
    monkeypatch.setattr(postmaster_mod, "_max_email_size_kb", fake_max_size)

    class _SysConfig:
        async def postmaster_user_id(self) -> int:
            return 1

    monkeypatch.setattr(postmaster_mod, "SysConfig", lambda _session: _SysConfig())

    await process_account(
        _DummyFactory(),  # type: ignore[arg-type]
        account,
        settings=None,  # type: ignore[arg-type]
        mail_sender=object(),  # type: ignore[arg-type]
        leave_on_server=True,
    )

    assert deleted == []
