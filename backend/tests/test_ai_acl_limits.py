"""DB tests for tiqora.ai.acl.check_feature_limits (plan §3.6).

Follows the direct-service-call pattern from ``test_ai_admin.py``: local
testcontainer only, real async session, no network.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.ai import usage as ai_usage
from tiqora.ai.acl import AclLimitExceededError, check_feature_limits, create_acl
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tables(sync_url: str, *, user_id: int) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_ai_acl WHERE subject_id = :uid"), {"uid": user_id})
        conn.execute(text("DELETE FROM tiqora_ai_usage WHERE user_id = :uid"), {"uid": user_id})
    engine.dispose()


async def test_no_acl_row_means_unlimited(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url, user_id=9701)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await check_feature_limits(session, 9701, "manual_assist")  # no raise
    finally:
        await engine.dispose()


async def test_null_limit_is_unbounded(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url, user_id=9702)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await create_acl(
                session,
                subject_type="user",
                subject_id=9702,
                feature="manual_assist",
                allowed=True,
                limit_requests_day=None,
                limit_tokens_day=None,
                limit_requests_month=None,
            )
            for _ in range(5):
                await ai_usage.record_usage(
                    session, user_id=9702, feature="manual_assist", prompt_tokens=100
                )
            await check_feature_limits(session, 9702, "manual_assist")  # no raise
    finally:
        await engine.dispose()


async def test_requests_per_day_limit_rejects_once_reached(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url, user_id=9703)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await create_acl(
                session,
                subject_type="user",
                subject_id=9703,
                feature="manual_assist",
                allowed=True,
                limit_requests_day=2,
            )
            await check_feature_limits(session, 9703, "manual_assist")  # 0 used, ok

            await ai_usage.record_usage(session, user_id=9703, feature="manual_assist")
            await check_feature_limits(session, 9703, "manual_assist")  # 1 used, still ok

            await ai_usage.record_usage(session, user_id=9703, feature="manual_assist")
            with pytest.raises(AclLimitExceededError) as exc_info:
                await check_feature_limits(session, 9703, "manual_assist")  # 2 used, limit=2
            assert exc_info.value.limit_kind == "requests_day"
    finally:
        await engine.dispose()


async def test_tokens_per_day_limit_counts_prompt_plus_completion(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url, user_id=9704)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await create_acl(
                session,
                subject_type="user",
                subject_id=9704,
                feature="manual_assist",
                allowed=True,
                limit_tokens_day=150,
            )
            await ai_usage.record_usage(
                session,
                user_id=9704,
                feature="manual_assist",
                prompt_tokens=80,
                completion_tokens=60,
            )
            # 80 + 60 = 140 < 150 -> still ok
            await check_feature_limits(session, 9704, "manual_assist")

            await ai_usage.record_usage(
                session,
                user_id=9704,
                feature="manual_assist",
                prompt_tokens=5,
                completion_tokens=5,
            )
            # 140 + 10 = 150 >= 150 -> exceeded
            with pytest.raises(AclLimitExceededError) as exc_info:
                await check_feature_limits(session, 9704, "manual_assist")
            assert exc_info.value.limit_kind == "tokens_day"
    finally:
        await engine.dispose()


async def test_requests_per_month_limit(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url, user_id=9705)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await create_acl(
                session,
                subject_type="user",
                subject_id=9705,
                feature="manual_assist",
                allowed=True,
                limit_requests_month=1,
            )
            await ai_usage.record_usage(session, user_id=9705, feature="manual_assist")
            with pytest.raises(AclLimitExceededError) as exc_info:
                await check_feature_limits(session, 9705, "manual_assist")
            assert exc_info.value.limit_kind == "requests_month"
    finally:
        await engine.dispose()


async def test_different_feature_is_not_counted(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url, user_id=9706)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await create_acl(
                session,
                subject_type="user",
                subject_id=9706,
                feature="manual_assist",
                allowed=True,
                limit_requests_day=1,
            )
            await ai_usage.record_usage(session, user_id=9706, feature="summary")
            await check_feature_limits(session, 9706, "manual_assist")  # unrelated feature, ok
    finally:
        await engine.dispose()
