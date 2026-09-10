"""Unit tests for tiqora.channels.email.machine_mail. No DB, no network.

The regression these guard: ticket 2026091010000013 — a sipgate newsletter
from ``noreply@sipgate.de`` reached the AI auto-reply and got answered.
"""

from __future__ import annotations

import pytest

from tiqora.channels.email.machine_mail import is_auto_generated


def test_plain_customer_mail_is_not_auto_generated() -> None:
    assert not is_auto_generated(
        {
            "From": "Studi <studi@example.com>",
            "Subject": "WLAN geht nicht",
            "To": "netadmin@stw-bonn.de",
        }
    )


def test_x_otrs_loop_marks_auto_generated() -> None:
    """The header Znuny already folds Precedence/Auto-Submitted/... into."""
    assert is_auto_generated({"X-OTRS-Loop": "yes"})
    assert is_auto_generated({"x-otrs-loop": "yes"})


@pytest.mark.parametrize("header", ["List-Unsubscribe", "List-Id", "List-Post"])
def test_list_headers_mark_auto_generated(header: str) -> None:
    """Newsletters set these; autoresponders and humans do not — this is the
    signal X-OTRS-Loop alone misses."""
    assert is_auto_generated({header: "<https://example.com/unsub>"})


def test_empty_list_header_does_not_match() -> None:
    assert not is_auto_generated({"List-Unsubscribe": "   "})


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("X-Spam-Flag", "YES"),
        ("X-Spam-Status", "Yes, score=9.1 required=5.0"),
    ],
)
def test_spam_flagged_mail_is_auto_generated(header: str, value: str) -> None:
    assert is_auto_generated({header: value})


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("X-Spam-Flag", "NO"),
        ("X-Spam-Status", "No, score=-1.2 required=5.0"),
    ],
)
def test_clean_spam_verdict_is_not_auto_generated(header: str, value: str) -> None:
    assert not is_auto_generated({header: value})


def test_sipgate_newsletter_shape_is_auto_generated() -> None:
    """The exact header shape of the mail that produced ticket
    2026091010000013 (bulk newsletter from a noreply sender)."""
    assert is_auto_generated(
        {
            "From": "sipgate GmbH <noreply@sipgate.de>",
            "Subject": "Produkt-Update August/September 2026",
            "Precedence": "bulk",
            "List-Unsubscribe": "<https://sipgate.de/unsubscribe?id=1>",
            # _build_get_param folds Precedence into X-OTRS-Loop before this
            # function ever runs; asserted here as it arrives in production.
            "X-OTRS-Loop": "yes",
        }
    )
