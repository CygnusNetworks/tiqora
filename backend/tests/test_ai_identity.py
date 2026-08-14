"""Unit + DB tests for tiqora.ai.identity (Task 6: identity_mode=clarify_schema).

Runtime end-to-end wiring (unidentified Telegram chat -> clarify question,
correct/incorrect identity_claim, escalation after MAX_IDENTITY_ATTEMPTS,
identity_mode=off/email-source regressions) lives in test_ai_runtime.py,
next to the rest of the run_ticket_agent scenarios and reusing its
seed/ScriptedLlm fixtures.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.ai import policies as ai_policies
from tiqora.ai.identity import (
    ClarifySchemaField,
    get_customer_user_columns,
    parse_clarify_schema,
    valid_column_name,
    verify_identity_claim,
)
from tiqora.ai.models import TiqoraAiQueuePolicy
from tiqora.ai.policies import QueuePolicyValidationError
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    """``tiqora_*`` tables are Alembic-managed in prod; the Znuny-only DDL
    fixture used by these tests doesn't include them (same approach as
    test_channels_sms.py)."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()


# ---------------------------------------------------------------------------
# parse_clarify_schema — pure unit tests
# ---------------------------------------------------------------------------


def _policy(clarify_schema_json: str | None) -> TiqoraAiQueuePolicy:
    return TiqoraAiQueuePolicy(
        system_prompt="", autonomy="off", clarify_schema_json=clarify_schema_json
    )


def test_parse_clarify_schema_none_when_unset() -> None:
    assert parse_clarify_schema(_policy(None)) is None
    assert parse_clarify_schema(_policy("")) is None


def test_parse_clarify_schema_returns_fields() -> None:
    raw = (
        '{"fields": [{"column": "phone", "label": "Phone number"},'
        ' {"column": "email", "label": "Email"}]}'
    )
    fields = parse_clarify_schema(_policy(raw))
    assert fields == [
        ClarifySchemaField(column="phone", label="Phone number"),
        ClarifySchemaField(column="email", label="Email"),
    ]


def test_parse_clarify_schema_none_when_malformed() -> None:
    assert parse_clarify_schema(_policy("not json")) is None
    assert parse_clarify_schema(_policy("[]")) is None
    assert parse_clarify_schema(_policy('{"fields": []}')) is None
    assert parse_clarify_schema(_policy('{"fields": [{"column": "phone"}]}')) is None


def test_valid_column_name() -> None:
    assert valid_column_name("phone")
    assert valid_column_name("first_name")
    assert not valid_column_name("Phone")
    assert not valid_column_name("phone; DROP TABLE customer_user")
    assert not valid_column_name("")


# ---------------------------------------------------------------------------
# Policy validation (tiqora.ai.policies.create_queue_policy /
# update_queue_policy) against real customer_user columns.
# ---------------------------------------------------------------------------


async def test_create_queue_policy_accepts_valid_clarify_schema(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = 9800"))
            await session.commit()
            row = await ai_policies.create_queue_policy(
                session,
                change_by=1,
                queue_id=9800,
                system_prompt="",
                identity_mode="clarify_schema",
                clarify_schema_json='{"fields": [{"column": "phone", "label": "Phone"}]}',
            )
            assert row.clarify_schema_json is not None
            await session.execute(text("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = 9800"))
            await session.commit()
    finally:
        await engine.dispose()


async def test_create_queue_policy_rejects_unknown_column(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = 9801"))
            await session.commit()
            with pytest.raises(QueuePolicyValidationError, match="does not exist"):
                await ai_policies.create_queue_policy(
                    session,
                    change_by=1,
                    queue_id=9801,
                    system_prompt="",
                    identity_mode="clarify_schema",
                    clarify_schema_json=(
                        '{"fields": [{"column": "not_a_real_column", "label": "X"}]}'
                    ),
                )
    finally:
        await engine.dispose()


async def test_create_queue_policy_rejects_missing_fields(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("DELETE FROM tiqora_ai_queue_policy WHERE queue_id = 9802"))
            await session.commit()
            with pytest.raises(QueuePolicyValidationError):
                await ai_policies.create_queue_policy(
                    session,
                    change_by=1,
                    queue_id=9802,
                    system_prompt="",
                    identity_mode="clarify_schema",
                    clarify_schema_json="{}",
                )
            with pytest.raises(QueuePolicyValidationError):
                await ai_policies.create_queue_policy(
                    session,
                    change_by=1,
                    queue_id=9802,
                    system_prompt="",
                    identity_mode="clarify_schema",
                    clarify_schema_json='{"fields": []}',
                )
    finally:
        await engine.dispose()


async def test_get_customer_user_columns_includes_standard_columns(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            columns = await get_customer_user_columns(session)
            assert {"login", "phone", "email", "customer_id", "valid_id"} <= columns
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# verify_identity_claim
# ---------------------------------------------------------------------------


async def _insert_customer_user(
    session: AsyncSession,
    *,
    login: str,
    phone: str,
    valid_id: int = 1,
) -> None:
    await session.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
            " phone, pw, valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:login, :email, :login, 'Test', 'Customer', :phone, 'x', :valid_id,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"login": login, "email": f"{login}@example.com", "phone": phone, "valid_id": valid_id},
    )


_PHONE_FIELD = [ClarifySchemaField(column="phone", label="Phone number")]


async def test_verify_identity_claim_exact_match(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("DELETE FROM customer_user WHERE login = 'idclaim1'"))
            await _insert_customer_user(session, login="idclaim1", phone="+491234567")
            await session.commit()

            login = await verify_identity_claim(session, _PHONE_FIELD, {"phone": "+491234567"})
            assert login == "idclaim1"
            await session.execute(text("DELETE FROM customer_user WHERE login = 'idclaim1'"))
            await session.commit()
    finally:
        await engine.dispose()


async def test_verify_identity_claim_no_match(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            login = await verify_identity_claim(session, _PHONE_FIELD, {"phone": "+49nope"})
            assert login is None
    finally:
        await engine.dispose()


async def test_verify_identity_claim_ambiguous_match_returns_none(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(
                text("DELETE FROM customer_user WHERE login IN ('idclaim2a', 'idclaim2b')")
            )
            await _insert_customer_user(session, login="idclaim2a", phone="+490000000")
            await _insert_customer_user(session, login="idclaim2b", phone="+490000000")
            await session.commit()

            login = await verify_identity_claim(session, _PHONE_FIELD, {"phone": "+490000000"})
            assert login is None
            await session.execute(
                text("DELETE FROM customer_user WHERE login IN ('idclaim2a', 'idclaim2b')")
            )
            await session.commit()
    finally:
        await engine.dispose()


async def test_verify_identity_claim_case_and_trim_insensitive(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("DELETE FROM customer_user WHERE login = 'idclaim3'"))
            await session.execute(
                text(
                    "INSERT INTO customer_user (login, email, customer_id, first_name,"
                    " last_name, phone, pw, valid_id, create_time, create_by, change_time,"
                    " change_by) VALUES ('idclaim3', 'idclaim3@example.com', 'idclaim3',"
                    " 'Jane', 'Doe', '+49XyZ', 'x', 1, current_timestamp, 1,"
                    " current_timestamp, 1)"
                )
            )
            await session.commit()

            login = await verify_identity_claim(session, _PHONE_FIELD, {"phone": "  +49xyz  "})
            assert login == "idclaim3"
            await session.execute(text("DELETE FROM customer_user WHERE login = 'idclaim3'"))
            await session.commit()
    finally:
        await engine.dispose()


async def test_verify_identity_claim_ignores_invalid_customer(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("DELETE FROM customer_user WHERE login = 'idclaim4'"))
            await _insert_customer_user(session, login="idclaim4", phone="+499999999", valid_id=2)
            await session.commit()

            login = await verify_identity_claim(session, _PHONE_FIELD, {"phone": "+499999999"})
            assert login is None
            await session.execute(text("DELETE FROM customer_user WHERE login = 'idclaim4'"))
            await session.commit()
    finally:
        await engine.dispose()


async def test_verify_identity_claim_requires_all_fields() -> None:
    fields = [
        ClarifySchemaField(column="phone", label="Phone"),
        ClarifySchemaField(column="email", label="Email"),
    ]

    # Missing 'email' entirely -> no DB call needed, must short-circuit None.
    class _NoCallSession:
        async def execute(self, *_a: Any, **_k: Any) -> None:
            raise AssertionError("must not query when a field value is missing")

    result = await verify_identity_claim(_NoCallSession(), fields, {"phone": "123"})  # type: ignore[arg-type]
    assert result is None
