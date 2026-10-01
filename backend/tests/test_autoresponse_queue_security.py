"""Auto-responses follow the queue's email security defaults (channels/email/autoresponse.py).

"Encryption required" without recipient keys: no mail, a history line and a
failed mail-log row; otherwise the secured message replaces the plain one.
"""

from __future__ import annotations

from email.message import EmailMessage
from typing import Any

import pytest

from tiqora.channels.email import autoresponse
from tiqora.crypto import queue_security as qs


async def _call(
    monkeypatch: pytest.MonkeyPatch, resolve: Any
) -> tuple[Any, list[str], list[dict[str, Any]]]:
    history: list[str] = []
    log: list[dict[str, Any]] = []

    async def fake_history(_session: Any, **kw: Any) -> None:
        history.append(kw["name"])

    async def fake_log(**kw: Any) -> None:
        log.append(kw)

    monkeypatch.setattr(autoresponse, "history_add", fake_history)
    monkeypatch.setattr("tiqora.domain.mail_log.write_mail_log", fake_log)
    monkeypatch.setattr(qs, "resolve_mail_security", resolve)
    result = await autoresponse._secure_auto_response(
        None,  # type: ignore[arg-type]
        EmailMessage(),
        ticket_id=7,
        queue_id=3,
        from_line="Support <support@example.org>",
        to_line="kunde@example.org",
        recipients=["kunde@example.org"],
        subject="Ihre Anfrage",
        message_id="<x@example.org>",
        queue_name="Support",
        user_id=1,
    )
    return result, history, log


async def test_required_without_key_skips_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def blocked(*_a: Any, **_kw: Any) -> None:
        raise qs.QueueSecurityRequiredError("no usable key for: kunde@example.org")

    result, history, log = await _call(monkeypatch, blocked)
    assert result is False
    assert history and "kunde@example.org" in history[0]
    assert log[0]["status"] == "failed" and "not sent" in log[0]["detail"]


async def test_no_security_sends_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(*_a: Any, **_kw: Any) -> None:
        return None

    result, history, log = await _call(monkeypatch, none)
    assert result is None and history == [] and log == []
