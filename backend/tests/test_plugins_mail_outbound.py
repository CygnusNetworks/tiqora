"""Outbound mail filters from plugins (D9): ``X-`` headers and veto only, no DB.

``SmtpMailSender`` is exercised with ``aiosmtplib.send`` patched to capture what would go out.
"""

from __future__ import annotations

import time
from email import message_from_bytes
from email import policy as email_policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import aiosmtplib
import pytest

from tests._plugin_points import boot_points_plugin, points_behaviour
from tiqora.channels.email.smtp import (
    SmtpMailSender,
    build_message,
    is_permanent_smtp_failure,
    smtp_reply_codes,
)
from tiqora.crypto.mime_build import secured_message_from_raw
from tiqora.plugins import mail_filters
from tiqora.plugins.mail_filters import OutboundMailVetoed


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """What ``aiosmtplib.send`` received (the message, or the raw bytes of a secured mail)."""
    sent: list[Any] = []

    async def fake_send(message: Any, **kwargs: Any) -> tuple[dict[str, Any], str]:
        sent.append(message)
        return {}, "ok"

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    return sent


def _msg(to: str = "kunde@example.org", subject: str = "Hallo") -> EmailMessage:
    return build_message(
        from_addr="support@example.org",
        to_addrs=to,
        cc_addrs=None,
        subject=subject,
        body="Guten Tag",
        content_type="text/plain",
        in_reply_to=None,
        message_id="<fixed.out@example.org>",
    )


def _sender() -> SmtpMailSender:
    return SmtpMailSender(host="smtp.example.org", port=25)


@pytest.fixture(autouse=True)
def _plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot_points_plugin(tmp_path, monkeypatch)


async def test_stamp_header_added(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch)
    original = _msg()
    await _sender().send(original)
    assert len(sent) == 1
    assert sent[0]["X-Points-Checked"] == "1"
    # the caller's message is not mutated (a retry must not stamp twice)
    assert "X-Points-Checked" not in original


async def test_veto_is_permanent_550(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch)
    with pytest.raises(OutboundMailVetoed) as exc:
        await _sender().send(_msg(to="x@blocked.example.net"))
    assert smtp_reply_codes(exc.value) == [550]
    assert is_permanent_smtp_failure(exc.value)
    assert "Empfänger gesperrt" in str(exc.value)
    # D9: the agent must see that an extension refused the mail, not the relay
    text = str(exc.value)
    assert text.startswith("Versand durch Erweiterung „")
    assert "abgelehnt: Empfänger gesperrt" in text
    assert "(550" not in text
    assert sent == []


@pytest.mark.parametrize("mode", ["raise", "hang"])
async def test_filter_crash_sends_unchanged(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    sent = _capture(monkeypatch)
    points_behaviour().MODE["mail.out"] = mode
    started = time.monotonic()
    await _sender().send(_msg())
    elapsed = time.monotonic() - started
    assert len(sent) == 1
    assert sent[0]["X-Points-Checked"] == "1"  # the healthy filter still ran
    assert "X-Broken" not in sent[0]  # nothing from the broken one
    # "hang": broken_out stops at its 1 s limit, broken_late at its own 2 s limit
    assert elapsed < 8
    # identical to a mail without any plugin filter, except for the stamp
    reference = _msg()
    reference["X-Points-Checked"] = "1"
    got = {k: v for k, v in sent[0].items() if k != "Date"}
    want = {k: v for k, v in reference.items() if k != "Date"}
    assert got == want
    assert sent[0].get_content() == reference.get_content()


async def test_only_x_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch)
    points_behaviour().MODE["mail.out"] = "bad_header"
    await _sender().send(_msg(subject="Original"))
    assert len(sent) == 1
    assert sent[0].get_all("Subject") == ["Original"]
    assert "X-Broken" not in sent[0]  # the filter failed as a whole
    assert sent[0]["Bcc"] is None


async def test_budget_exhausted_sends_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two hanging filters share one budget: the chain is bounded, the mail still goes."""
    sent = _capture(monkeypatch)
    monkeypatch.setattr(mail_filters, "OUTBOUND_BUDGET_SECONDS", 0.4)
    points_behaviour().MODE["mail.out"] = "hang"
    started = time.monotonic()
    await _sender().send(_msg())
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    assert len(sent) == 1
    assert sent[0]["X-Points-Checked"] == "1"
    assert "X-Broken" not in sent[0]


_SIGNED = (
    b"From: support@example.org\r\n"
    b"To: kunde@example.org\r\n"
    b"Subject: Signiert\r\n"
    b"Message-ID: <signed.out@example.org>\r\n"
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: multipart/signed; protocol="application/pkcs7-signature";\r\n'
    b' micalg=sha-256; boundary="BND"\r\n'
    b"\r\n"
    b"--BND\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Guten Tag\r\n"
    b"--BND\r\n"
    b"Content-Type: application/pkcs7-signature; name=smime.p7s\r\n"
    b"Content-Transfer-Encoding: base64\r\n"
    b"\r\n"
    b"QUJDREVGRw==\r\n"
    b"--BND--\r\n"
)


async def test_signed_message_body_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch)
    signed = secured_message_from_raw(_SIGNED, None)
    await _sender().send(signed)
    assert len(sent) == 1
    raw = sent[0]
    assert isinstance(raw, bytes)
    assert raw != _SIGNED
    head, _, body = raw.partition(b"\r\n\r\n")
    assert b"X-Points-Checked: 1" in head
    # everything after the header block is byte-identical, so the signature stays valid
    assert body == _SIGNED.partition(b"\r\n\r\n")[2]
    before = message_from_bytes(_SIGNED, policy=email_policy.SMTP)
    after = message_from_bytes(raw, policy=email_policy.SMTP)
    parts_before = [p.as_bytes() for p in before.walk()][1:]
    parts_after = [p.as_bytes() for p in after.walk()][1:]
    assert parts_after == parts_before
    assert after["X-Points-Checked"] == "1"
    assert after["Subject"] == "Signiert"
    # the original object keeps its bytes
    assert signed.tiqora_raw == _SIGNED
