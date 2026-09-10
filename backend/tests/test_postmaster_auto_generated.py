"""Machine-generated inbound mail is flagged on the ArticleCreate outbox event.

End-to-end complement to ``tests/test_email_machine_mail.py`` (which covers the
detector alone) and ``tests/test_ai_auto_worker.py`` (which covers the consumer
alone): this asserts the flag actually survives the pipeline into the payload
the AI auto-worker reads.

Regression for ticket 2026091010000013 — a sipgate newsletter from
``noreply@sipgate.de`` reached the AI runtime and was answered.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from email.message import EmailMessage

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.pipeline import process_message
from tiqora.db.legacy.mail_account import MailAccount

from ._row_cleanup import cleanup_module
from .test_mail_log import NOW, _ensure_tables, _make_sysconfig, _to_async_url

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True, scope="module")
def _cleanup(mariadb_znuny_url: str) -> Iterator[None]:
    yield from cleanup_module(mariadb_znuny_url)


def _account() -> MailAccount:
    return MailAccount(
        id=1,
        login="postmaster@example.com",
        pw="x",
        host="localhost",
        account_type="IMAP",
        queue_id=1,
        trusted=0,
        valid_id=1,
        create_time=NOW,
        create_by=1,
        change_time=NOW,
        change_by=1,
    )


def _raw_email(*, subject: str, from_addr: str, headers: dict[str, str]) -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = "support@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{subject.replace(' ', '-').lower()}@example.com>"
    for name, value in headers.items():
        msg[name] = value
    msg.set_content("Body")
    return msg.as_bytes()


async def _article_create_payload(session: AsyncSession, ticket_id: int) -> dict[str, object]:
    row = (
        await session.execute(
            text(
                "SELECT payload FROM tiqora_event_outbox"
                " WHERE ticket_id = :tid AND event_type = 'ArticleCreate' ORDER BY id LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    assert row is not None, "no ArticleCreate event emitted"
    payload: dict[str, object] = json.loads(row[0])
    return payload


async def _run(mariadb_znuny_url: str, raw: bytes) -> tuple[int, dict[str, object]]:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            result = await process_message(
                session,
                factory,
                _make_sysconfig(),
                raw=raw,
                account=_account(),
                user_id=1,
            )
        assert result.outcome == "new_ticket"
        assert result.ticket_id is not None
        async with factory() as session:
            return result.ticket_id, await _article_create_payload(session, result.ticket_id)
    finally:
        await engine.dispose()


async def test_newsletter_marks_article_create_event_auto_generated(
    mariadb_znuny_url: str,
) -> None:
    _, payload = await _run(
        mariadb_znuny_url,
        _raw_email(
            subject="Produkt-Update August September 2026",
            from_addr="sipgate GmbH <noreply@sipgate.de>",
            headers={
                "Precedence": "bulk",
                "List-Unsubscribe": "<https://sipgate.de/unsubscribe?id=1>",
            },
        ),
    )
    assert payload["auto_generated"] is True


async def test_plain_customer_mail_is_not_flagged(mariadb_znuny_url: str) -> None:
    _, payload = await _run(
        mariadb_znuny_url,
        _raw_email(
            subject="WLAN im Zimmer geht nicht",
            from_addr="Studi <studi@example.com>",
            headers={},
        ),
    )
    assert payload["auto_generated"] is False
