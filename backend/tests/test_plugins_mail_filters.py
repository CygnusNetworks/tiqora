"""``OutboundMail.add_header`` rules (D9): only harmless ``X-`` headers."""

from __future__ import annotations

import pytest

from tiqora.plugins.mail_filters import OutboundMail, OutboundMailVetoed


def _mail() -> OutboundMail:
    return OutboundMail(
        to=("a@example.org",),
        cc=(),
        subject="Hallo",
        from_address="support@example.org",
        message_id=None,
        headers={"Subject": "Hallo"},
    )


def test_add_header_records_valid_headers() -> None:
    mail = _mail()
    mail.add_header("X-Points-Checked", "1")
    mail.add_header("X-Points-Checked", "2")  # same name: the later value wins
    assert mail.added == {"X-Points-Checked": "2"}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("Subject", "x"),
        ("To", "x"),
        ("X-OTRS-Queue", "x"),
        ("x-otrs-queue", "x"),
        ("X-", "x"),
        ("X-Bad Name", "x"),
        ("X-Ok", "a\r\nBcc: evil@example.org"),
        ("X-Ok", "a\nb"),
        ("X-Ok", "a" * 999),
    ],
)
def test_add_header_rejects(name: str, value: str) -> None:
    mail = _mail()
    with pytest.raises(ValueError):
        mail.add_header(name, value)
    assert mail.added == {}


def test_veto_is_a_permanent_smtp_rejection() -> None:
    exc = OutboundMailVetoed("Versand abgelehnt")
    assert exc.code == 550
    assert "Versand abgelehnt" in str(exc)
