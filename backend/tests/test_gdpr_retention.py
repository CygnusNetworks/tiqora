"""Tests for the config-driven GDPR retention sweep (tiqora/gdpr/retention.py)."""

from __future__ import annotations

import contextlib
import itertools
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.settings_store import set_setting
from tiqora.gdpr.gate import GdprRefusedError
from tiqora.gdpr.retention import (
    KEY_GDPR_RETENTION_RULES,
    RetentionConfigError,
    build_retention_report,
    parse_rules,
    run_retention,
)

NOW = datetime(2026, 1, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


# ---------------------------------------------------------------------------
# parse_rules: pure, no DB
# ---------------------------------------------------------------------------


def test_parse_rules_empty_when_none() -> None:
    assert parse_rules(None) == []
    assert parse_rules("") == []


def test_parse_rules_valid() -> None:
    raw = json.dumps(
        [{"name": "r1", "queue": "Support", "older_than_months": 12, "state_type": "closed"}]
    )
    rules = parse_rules(raw)
    assert len(rules) == 1
    assert rules[0].name == "r1"
    assert rules[0].queue == "Support"
    assert rules[0].older_than_months == 12
    assert rules[0].state_type == "closed"


def test_parse_rules_rejects_non_array() -> None:
    with pytest.raises(RetentionConfigError):
        parse_rules(json.dumps({"not": "a list"}))


def test_parse_rules_rejects_missing_key() -> None:
    with pytest.raises(RetentionConfigError):
        parse_rules(json.dumps([{"name": "r1", "queue": "Support"}]))


def test_parse_rules_rejects_invalid_json() -> None:
    with pytest.raises(RetentionConfigError):
        parse_rules("{not json")


# ---------------------------------------------------------------------------
# DB-marked: dry-run selection + gated run
# ---------------------------------------------------------------------------


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_settings ("
                "`key` VARCHAR(200) PRIMARY KEY, value TEXT)"
            )
        )
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_gdpr_audit ("
                "id BIGINT AUTO_INCREMENT PRIMARY KEY, action VARCHAR(50) NOT NULL,"
                " target VARCHAR(255) NOT NULL, actor VARCHAR(200) NOT NULL,"
                " counts TEXT NOT NULL, force_parallel TINYINT(1) NOT NULL DEFAULT 0,"
                " created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    await session.commit()
    for stmt in (
        "DELETE FROM tiqora_settings WHERE `key` LIKE 'gdpr.%'",
        "DELETE FROM tiqora_gdpr_audit",
        "DELETE FROM article_data_mime",
        "DELETE FROM article",
        "DELETE FROM ticket WHERE customer_id = 'RETENTION-TEST'",
    ):
        try:
            await session.execute(text(stmt))
            await session.commit()
        except Exception:
            await session.rollback()


_tn_counter = itertools.count(1)


async def _insert_ticket(
    session: AsyncSession, *, queue_id: int, state_id: int, change_time: datetime
) -> int:
    t = Ticket(
        tn=f"20260101{next(_tn_counter):08d}",
        title="Retention test",
        queue_id=queue_id,
        ticket_lock_id=1,
        user_id=1,
        responsible_user_id=1,
        ticket_priority_id=3,
        ticket_state_id=state_id,
        customer_id="RETENTION-TEST",
        customer_user_id="retention.customer@example.com",
        timeout=0,
        until_time=0,
        escalation_time=0,
        escalation_update_time=0,
        escalation_response_time=0,
        escalation_solution_time=0,
        create_time=NOW,
        create_by=1,
        change_time=change_time,
        change_by=1,
    )
    async with session.begin():
        session.add(t)
        await session.flush()
        ticket_id = t.id
    return ticket_id


async def _insert_article(session: AsyncSession, *, ticket_id: int, body: str) -> None:
    async with session.begin():
        await session.execute(
            text(
                "INSERT INTO article (ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:ticket_id, 2, 1, 1, :ct, 1, :ct, 1)"
            ),
            {"ticket_id": ticket_id, "ct": NOW},
        )
        article_id = (
            await session.execute(
                text("SELECT MAX(id) FROM article WHERE ticket_id = :tid"), {"tid": ticket_id}
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO article_data_mime (article_id, a_from, a_to, a_body,"
                " incoming_time, create_time, create_by, change_time, change_by)"
                " VALUES (:aid, 'customer@example.com', 'agent@tiqora.test', :body,"
                " 0, :ct, 1, :ct, 1)"
            ),
            {"aid": article_id, "body": body, "ct": NOW},
        )


@pytest.mark.db
@pytest.mark.asyncio
async def test_retention_report_selects_only_old_closed_tickets_in_queue(
    mariadb_znuny_url: str,
) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime(2026, 7, 20, 12, 0, 0)
    old_change = now - timedelta(days=400)  # ~13 months
    recent_change = now - timedelta(days=10)

    async with factory() as session:
        await _seed_tiqora_tables(session)
        await set_setting(
            session,
            KEY_GDPR_RETENTION_RULES,
            json.dumps(
                [
                    {
                        "name": "raw-12mo",
                        "queue": "Raw",
                        "state_type": "closed",
                        "older_than_months": 12,
                    }
                ]
            ),
        )
        old_closed_ticket = await _insert_ticket(
            session,
            queue_id=2,
            state_id=2,
            change_time=old_change,  # Raw / closed successful
        )
        await _insert_ticket(
            session, queue_id=2, state_id=2, change_time=recent_change
        )  # too recent
        await _insert_ticket(
            session, queue_id=2, state_id=4, change_time=old_change
        )  # old but open
        await _insert_ticket(
            session, queue_id=1, state_id=2, change_time=old_change
        )  # wrong queue (Postmaster)

    report = await build_retention_report(factory, now=now)
    assert len(report.matches) == 1
    match = report.matches[0]
    assert match.ticket_ids == [old_closed_ticket]
    assert match.pending_ticket_ids == [old_closed_ticket]
    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_retention_run_refuses_without_ownership_or_force(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _seed_tiqora_tables(session)
        await set_setting(session, KEY_GDPR_RETENTION_RULES, "[]")
    with pytest.raises(GdprRefusedError):
        await run_retention(factory, Settings())
    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_retention_run_anonymizes_matched_ticket_and_is_idempotent(
    mariadb_znuny_url: str,
) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime(2026, 7, 20, 12, 0, 0)
    old_change = now - timedelta(days=400)

    async with factory() as session:
        await _seed_tiqora_tables(session)
        await set_setting(
            session,
            KEY_GDPR_RETENTION_RULES,
            json.dumps(
                [
                    {
                        "name": "raw-12mo",
                        "queue": "Raw",
                        "state_type": "closed",
                        "older_than_months": 12,
                        "seed": 3,
                    }
                ]
            ),
        )
        ticket_id = await _insert_ticket(session, queue_id=2, state_id=2, change_time=old_change)
        await _insert_article(
            session, ticket_id=ticket_id, body="Sensitive body content about a customer."
        )

    result = await run_retention(factory, Settings(), now=now, force_parallel=True, actor="test")
    assert result.tickets_anonymized == 1
    assert result.articles_anonymized == 1

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT a_from, a_body FROM article_data_mime am"
                    " JOIN article a ON a.id = am.article_id WHERE a.ticket_id = :tid"
                ),
                {"tid": ticket_id},
            )
        ).first()
        assert row is not None
        assert row[0] != "customer@example.com"
        assert "Sensitive body content" not in (row[1] or "")

    # Second run: ticket already processed -> no-op (idempotent).
    result2 = await run_retention(factory, Settings(), now=now, force_parallel=True, actor="test")
    assert result2.tickets_anonymized == 0
    await engine.dispose()
