"""DB tests for ``tiqora.ai.usage.record_usage`` cost-hint computation from
provider token pricing.

Follows the direct-service-call pattern from ``test_ai_admin.py``: local
testcontainer only, real async session, no network. Seed ids use the 895xx
range.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.ai import providers as ai_providers
from tiqora.ai import usage as ai_usage
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        for table in ("tiqora_ai_usage", "tiqora_llm_provider"):
            conn.execute(text(f"DELETE FROM {table}"))
    engine.dispose()


async def test_record_usage_computes_cost_hint_with_both_prices(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            provider = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name="priced-89510",
                kind="openai_compat",
                base_url="https://api.example/v1",
                default_model="model-a",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
                price_input_per_1m=2.0,
                price_output_per_1m=8.0,
                price_currency="USD",
            )
            row = await ai_usage.record_usage(
                session,
                queue_id=89510,
                feature="manual_assist",
                provider_id=provider.id,
                prompt_tokens=1_000_000,
                completion_tokens=500_000,
            )
            assert row.cost_hint == pytest.approx(2.0 + 4.0)
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_record_usage_computes_cost_hint_with_one_price_set(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            provider = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name="half-priced-89511",
                kind="openai_compat",
                base_url="https://api.example/v1",
                default_model="model-b",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
                price_input_per_1m=3.0,
                # price_output_per_1m intentionally unset.
            )
            row = await ai_usage.record_usage(
                session,
                queue_id=89511,
                feature="manual_assist",
                provider_id=provider.id,
                prompt_tokens=1_000_000,
                completion_tokens=999_999_999,  # would dominate if it were priced
            )
            # Only the input side is priced; the missing component counts as 0.
            assert row.cost_hint == pytest.approx(3.0)
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_record_usage_cost_hint_none_when_no_pricing(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            provider = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name="unpriced-89512",
                kind="openai_compat",
                base_url="https://api.example/v1",
                default_model="model-c",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
            )
            row = await ai_usage.record_usage(
                session,
                queue_id=89512,
                feature="manual_assist",
                provider_id=provider.id,
                prompt_tokens=100,
                completion_tokens=50,
            )
            assert row.cost_hint is None

            # No provider at all -> also None, never an error.
            row_no_provider = await ai_usage.record_usage(
                session,
                queue_id=89512,
                feature="manual_assist",
                provider_id=None,
                prompt_tokens=100,
                completion_tokens=50,
            )
            assert row_no_provider.cost_hint is None
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_record_usage_respects_explicit_cost_hint_override(mariadb_znuny_url: str) -> None:
    """An explicitly-passed ``cost_hint`` is never recomputed/overwritten."""
    _ensure_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            provider = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name="priced-89513",
                kind="openai_compat",
                base_url="https://api.example/v1",
                default_model="model-d",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
                price_input_per_1m=100.0,
                price_output_per_1m=100.0,
                price_currency="USD",
            )
            row = await ai_usage.record_usage(
                session,
                queue_id=89513,
                feature="manual_assist",
                provider_id=provider.id,
                prompt_tokens=1_000_000,
                completion_tokens=1_000_000,
                cost_hint=0.01,
            )
            assert row.cost_hint == pytest.approx(0.01)
    finally:
        await engine.dispose()
        get_settings.cache_clear()
