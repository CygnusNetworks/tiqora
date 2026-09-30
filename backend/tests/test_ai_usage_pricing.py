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

from tests._llm_routing_helpers import make_model
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
                api_key=None,
                extra_json=None,
                eu_hosted=False,
                price_currency="USD",
            )
            await make_model(
                session, provider, "model-a", price_input_per_1m=2.0, price_output_per_1m=8.0
            )
            row = await ai_usage.record_usage(
                session,
                queue_id=89510,
                feature="manual_assist",
                provider_id=provider.id,
                model="model-a",
                prompt_tokens=1_000_000,
                completion_tokens=500_000,
            )
            assert row.cost_hint == pytest.approx(2.0 + 4.0)
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_cost_hint_is_priced_per_model(mariadb_znuny_url: str) -> None:
    """Prices live on the model: two models of one provider cost differently,
    a served name with a version suffix matches its configured model, and a
    model without a row is unpriced (None, not 0)."""
    _ensure_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            provider = await ai_providers.create_provider(
                session,
                settings=get_settings(),
                change_by=1,
                name="per-model-89515",
                kind="openai_compat",
                base_url="https://api.example/v1",
                api_key=None,
                price_currency="EUR",
            )
            await make_model(session, provider, "big", price_input_per_1m=10.0)
            await make_model(session, provider, "big-mini", price_input_per_1m=1.0)

            async def _cost(model: str | None) -> float | None:
                row = await ai_usage.record_usage(
                    session,
                    queue_id=89515,
                    feature="manual_assist",
                    provider_id=provider.id,
                    model=model,
                    prompt_tokens=1_000_000,
                )
                return row.cost_hint

            assert await _cost("big") == pytest.approx(10.0)
            assert await _cost("big-mini") == pytest.approx(1.0)
            # Echoed with a version suffix: longest configured prefix wins.
            assert await _cost("big-mini-2026-09-01") == pytest.approx(1.0)
            assert await _cost("big-2026-09-01") == pytest.approx(10.0)
            assert await _cost("unknown") is None
            assert await _cost(None) is None
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_cost_hint_uses_the_serving_model_row_when_known(mariadb_znuny_url: str) -> None:
    """With the serving model row known (llm_model_id), its price applies even
    when the echoed name matches nothing; the name match is only the
    fallback for rows without it."""
    _ensure_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            provider = await ai_providers.create_provider(
                session,
                settings=get_settings(),
                change_by=1,
                name="by-id-89516",
                kind="openai_compat",
                base_url="https://api.example/v1",
                api_key=None,
                price_currency="EUR",
            )
            cheap = await make_model(session, provider, "cheap", price_input_per_1m=1.0)
            await make_model(session, provider, "dear", price_input_per_1m=50.0)

            async def _cost(model: str | None, llm_model_id: int | None) -> float | None:
                row = await ai_usage.record_usage(
                    session,
                    queue_id=89516,
                    feature="manual_assist",
                    provider_id=provider.id,
                    model=model,
                    llm_model_id=llm_model_id,
                    prompt_tokens=1_000_000,
                )
                assert row.llm_model_id == llm_model_id
                return row.cost_hint

            # Echoed name unrelated to any configured id: priced by the row.
            assert await _cost("vendor/renamed", cheap.id) == pytest.approx(1.0)
            # The row wins over a name that would match another model.
            assert await _cost("dear", cheap.id) == pytest.approx(1.0)
            # Unknown row id (deleted model) → name match fallback.
            assert await _cost("dear", 999_999) == pytest.approx(50.0)
            assert await _cost("vendor/renamed", None) is None
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
                api_key=None,
                extra_json=None,
                eu_hosted=False,
                # price_output_per_1m intentionally unset.
            )
            await make_model(session, provider, "model-b", price_input_per_1m=3.0)
            row = await ai_usage.record_usage(
                session,
                queue_id=89511,
                feature="manual_assist",
                provider_id=provider.id,
                model="model-b",
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
                api_key=None,
                extra_json=None,
                eu_hosted=False,
            )
            await make_model(session, provider, "model-c")
            row = await ai_usage.record_usage(
                session,
                queue_id=89512,
                feature="manual_assist",
                provider_id=provider.id,
                model="model-c",
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
                api_key=None,
                extra_json=None,
                eu_hosted=False,
                price_currency="USD",
            )
            await make_model(
                session, provider, "model-d", price_input_per_1m=100.0, price_output_per_1m=100.0
            )
            row = await ai_usage.record_usage(
                session,
                queue_id=89513,
                feature="manual_assist",
                provider_id=provider.id,
                model="model-d",
                prompt_tokens=1_000_000,
                completion_tokens=1_000_000,
                cost_hint=0.01,
            )
            assert row.cost_hint == pytest.approx(0.01)
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_provider_budget_exceeded_returns_none_under_limit(mariadb_znuny_url: str) -> None:
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
                name="under-budget-89520",
                kind="openai_compat",
                base_url="https://api.example/v1",
                api_key=None,
                extra_json=None,
                eu_hosted=False,
                budget_cost_day=10.0,
                budget_cost_week=50.0,
                budget_cost_month=100.0,
            )
            await make_model(session, provider, "model-a")
            await ai_usage.record_usage(
                session,
                queue_id=89520,
                feature="manual_assist",
                provider_id=provider.id,
                cost_hint=1.0,
            )
            assert await ai_usage.provider_budget_exceeded(session, provider.id) is None
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_provider_budget_exceeded_returns_day_when_day_limit_hit(
    mariadb_znuny_url: str,
) -> None:
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
                name="day-budget-89521",
                kind="openai_compat",
                base_url="https://api.example/v1",
                api_key=None,
                extra_json=None,
                eu_hosted=False,
                budget_cost_day=5.0,
                budget_cost_week=100.0,
                budget_cost_month=100.0,
            )
            await make_model(session, provider, "model-a")
            await ai_usage.record_usage(
                session,
                queue_id=89521,
                feature="manual_assist",
                provider_id=provider.id,
                cost_hint=6.0,
            )
            assert await ai_usage.provider_budget_exceeded(session, provider.id) == "day"
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_provider_budget_exceeded_checks_week_when_only_week_configured(
    mariadb_znuny_url: str,
) -> None:
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
                name="week-budget-89522",
                kind="openai_compat",
                base_url="https://api.example/v1",
                api_key=None,
                extra_json=None,
                eu_hosted=False,
                budget_cost_week=3.0,
            )
            await make_model(session, provider, "model-a")
            await ai_usage.record_usage(
                session,
                queue_id=89522,
                feature="manual_assist",
                provider_id=provider.id,
                cost_hint=4.0,
            )
            assert await ai_usage.provider_budget_exceeded(session, provider.id) == "week"
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_provider_budget_exceeded_none_when_no_budget_configured(
    mariadb_znuny_url: str,
) -> None:
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
                name="no-budget-89523",
                kind="openai_compat",
                base_url="https://api.example/v1",
                api_key=None,
                extra_json=None,
                eu_hosted=False,
            )
            await make_model(session, provider, "model-a")
            await ai_usage.record_usage(
                session,
                queue_id=89523,
                feature="manual_assist",
                provider_id=provider.id,
                cost_hint=1_000_000.0,
            )
            assert await ai_usage.provider_budget_exceeded(session, provider.id) is None
    finally:
        await engine.dispose()
        get_settings.cache_clear()


async def test_provider_budget_exceeded_ignores_other_providers_spend(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tables(mariadb_znuny_url)
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            budgeted = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name="budgeted-89524",
                kind="openai_compat",
                base_url="https://api.example/v1",
                api_key=None,
                extra_json=None,
                eu_hosted=False,
                budget_cost_day=1.0,
            )
            await make_model(session, budgeted, "model-a")
            other = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name="other-89525",
                kind="openai_compat",
                base_url="https://api.example/v1",
                api_key=None,
                extra_json=None,
                eu_hosted=False,
            )
            await make_model(session, other, "model-b")
            await ai_usage.record_usage(
                session,
                queue_id=89524,
                feature="manual_assist",
                provider_id=other.id,
                cost_hint=1_000.0,
            )
            assert await ai_usage.provider_budget_exceeded(session, budgeted.id) is None
    finally:
        await engine.dispose()
        get_settings.cache_clear()
