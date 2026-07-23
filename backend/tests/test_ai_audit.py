"""DB + unit tests for tiqora.ai.audit (LLM-Request-Audit).

Seed ids use the 99xx range — disjoint from 96xx (``test_ai_runtime.py``),
97xx (``test_ai_summary.py``) and 98xx (``test_ai_auto_worker.py``) — so the
session-scoped testcontainer DB is shared safely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_ai_runtime import ScriptedLlm, _mysql_async, _propose_response, _seed_ticket
from tiqora.ai import policies as ai_policies
from tiqora.ai import providers as ai_providers
from tiqora.ai.audit import (
    AuditContext,
    AuditingLlmClient,
    PiiRevealError,
    audit_log_stats,
    cleanup_audit_log,
    compute_entry_cost,
    get_audit_log_entry,
    list_audit_log,
    load_provider_prices,
    reveal_pii,
    write_audit_log,
)
from tiqora.ai.gate import OPERATION_MODE_TIQORA_PRIMARY, set_operation_mode
from tiqora.ai.llm import LlmError, LlmHttpError, LlmMessage, LlmResponse, LlmUsage
from tiqora.ai.models import TiqoraAiAuditLog
from tiqora.ai.pii import PiiMapper
from tiqora.ai.runtime import TRIGGER_MANUAL, run_ticket_agent
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _cleanup_audit_rows(sync_url: str, *, run_id_prefix: str) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM tiqora_ai_audit_log WHERE run_id LIKE :p"),
            {"p": f"{run_id_prefix}%"},
        )
    engine.dispose()


class _FailingLlm:
    async def chat(self, **kwargs: Any) -> LlmResponse:
        raise LlmHttpError(500, "boom")


class _OkLlm:
    def __init__(self, response: LlmResponse) -> None:
        self._response = response

    async def chat(self, **kwargs: Any) -> LlmResponse:
        return self._response


# ---------------------------------------------------------------------------
# AuditingLlmClient — write on success / failure, PII map, image redaction
# ---------------------------------------------------------------------------


async def test_auditing_client_writes_row_on_success_with_pii_map(mariadb_znuny_url: str) -> None:
    run_id = "audit-test-ok-1"
    _cleanup_audit_rows(mariadb_znuny_url, run_id_prefix=run_id)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        pii = PiiMapper()
        masked_body = pii.mask("Contact me at agent@example.com please")
        inner = _OkLlm(
            LlmResponse(
                content="Sure, I will help.",
                usage=LlmUsage(prompt_tokens=12, completion_tokens=6),
                model="fake-model",
            )
        )
        async with factory() as session:
            client = AuditingLlmClient(
                inner,
                settings=settings,
                context=AuditContext(feature="draft", run_id=run_id, ticket_id=1),
                session=session,
                pii_mapper=pii,
            )
            response = await client.chat(
                messages=[LlmMessage(role="user", content=masked_body)], max_tokens=64
            )
            assert response.content == "Sure, I will help."

        async with factory() as session:
            row = (
                await session.execute(
                    select(TiqoraAiAuditLog).where(TiqoraAiAuditLog.run_id == run_id)
                )
            ).scalar_one()
            assert row.status_code == 200
            assert row.error is None
            assert row.feature == "draft"
            assert row.prompt_tokens == 12
            assert row.completion_tokens == 6
            assert "agent@example.com" not in row.request_json
            assert "[EMAIL_1]" in row.request_json
            assert row.pii_map_enc is not None
            assert row.pii_counts_json is not None
            import json

            assert json.loads(row.pii_counts_json) == {"EMAIL": 1}
    finally:
        await engine.dispose()


async def test_auditing_client_writes_row_on_failure(mariadb_znuny_url: str) -> None:
    run_id = "audit-test-fail-1"
    _cleanup_audit_rows(mariadb_znuny_url, run_id_prefix=run_id)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            client = AuditingLlmClient(
                _FailingLlm(),
                settings=settings,
                context=AuditContext(feature="draft", run_id=run_id),
                session=session,
            )
            with pytest.raises(LlmError):
                await client.chat(messages=[LlmMessage(role="user", content="hi")])

        async with factory() as session:
            row = (
                await session.execute(
                    select(TiqoraAiAuditLog).where(TiqoraAiAuditLog.run_id == run_id)
                )
            ).scalar_one()
            assert row.status_code == 500
            assert row.error is not None
            assert row.response_json is None
    finally:
        await engine.dispose()


async def test_auditing_client_redacts_image_data_urls(mariadb_znuny_url: str) -> None:
    run_id = "audit-test-vision-1"
    _cleanup_audit_rows(mariadb_znuny_url, run_id_prefix=run_id)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    data_url = "data:image/png;base64," + ("A" * 500)
    try:
        inner = _OkLlm(LlmResponse(content="a photo", usage=LlmUsage()))
        async with factory() as session:
            client = AuditingLlmClient(
                inner,
                settings=settings,
                context=AuditContext(feature="vision", run_id=run_id),
                session=session,
            )
            await client.chat(
                messages=[
                    LlmMessage(
                        role="user",
                        content=[
                            {"type": "text", "text": "describe this"},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    )
                ]
            )

        async with factory() as session:
            row = (
                await session.execute(
                    select(TiqoraAiAuditLog).where(TiqoraAiAuditLog.run_id == run_id)
                )
            ).scalar_one()
            assert "base64" not in row.request_json
            assert "[image: " in row.request_json
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Wired into the real run: Manual Assist writes an audit row (feature=draft)
# ---------------------------------------------------------------------------


async def test_manual_assist_run_writes_masked_audit_row(mariadb_znuny_url: str) -> None:
    seed = _seed_ticket(mariadb_znuny_url, ns=91)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    run_id = "audit-manual-run-1"
    try:
        async with factory() as session:
            await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
            provider = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name=f"fake-audit-provider-{seed['queue_id']}",
                kind="openai_compat",
                base_url="https://llm.example/v1",
                default_model="fake-model",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=False,
                eu_hosted=True,
            )
            await ai_policies.create_queue_policy(
                session,
                change_by=1,
                queue_id=seed["queue_id"],
                enabled_manual_assist=True,
                system_prompt="You are a helpful support agent.",
                autonomy="full",
                llm_provider_id=provider.id,
                pii_masking=True,
            )

        llm = ScriptedLlm([_propose_response("reply", "Here is the answer.")])
        async with factory() as session:
            result = await run_ticket_agent(
                session,
                settings=settings,
                llm=llm,
                ticket_id=seed["ticket_id"],
                trigger=TRIGGER_MANUAL,
                acting_user_id=seed["agent_id"],
                run_id=run_id,
            )
        assert result.status == "drafted"

        async with factory() as session:
            row = (
                await session.execute(
                    select(TiqoraAiAuditLog).where(TiqoraAiAuditLog.run_id == run_id)
                )
            ).scalar_one()
            assert row.feature == "draft"
            assert row.ticket_id == seed["ticket_id"]
            assert row.provider_id == provider.id
            assert row.provider_name == f"fake-audit-provider-{seed['queue_id']}"
            assert row.status_code == 200
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# List / stats / detail / reveal / cleanup
# ---------------------------------------------------------------------------


async def _write_row(
    session: AsyncSession,
    *,
    settings: Any,
    run_id: str,
    feature: str,
    ticket_id: int | None = None,
    error: str | None = None,
    ts_override: datetime | None = None,
    pii_mapping: dict[str, str] | None = None,
    provider_id: int | None = None,
    prompt_tokens: int = 1,
    completion_tokens: int = 1,
) -> None:
    await write_audit_log(
        session,
        settings=settings,
        context=AuditContext(
            feature=feature, run_id=run_id, ticket_id=ticket_id, provider_id=provider_id
        ),
        request_json='{"messages": []}',
        response_json=None if error else '{"content": "ok"}',
        status_code=None if error else 200,
        error=error,
        duration_ms=5,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        pii_mapping=pii_mapping,
    )
    if ts_override is not None:
        row = (
            await session.execute(
                select(TiqoraAiAuditLog)
                .where(TiqoraAiAuditLog.run_id == run_id)
                .order_by(TiqoraAiAuditLog.id.desc())
            )
        ).scalar_one()
        row.ts = ts_override
        await session.commit()


async def test_list_and_stats_filters(mariadb_znuny_url: str) -> None:
    run_prefix = "audit-list-1"
    _cleanup_audit_rows(mariadb_znuny_url, run_id_prefix=run_prefix)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _write_row(session, settings=settings, run_id=f"{run_prefix}-ok", feature="draft")
            await _write_row(
                session,
                settings=settings,
                run_id=f"{run_prefix}-err",
                feature="summary",
                error="boom",
            )

        async with factory() as session:
            all_page = await list_audit_log(session, page=1, page_size=50)
            run_ids = {r.run_id for r in all_page.items}
            assert f"{run_prefix}-ok" in run_ids
            assert f"{run_prefix}-err" in run_ids

            ok_page = await list_audit_log(session, status="ok", page=1, page_size=50)
            assert all(r.error is None for r in ok_page.items)

            err_page = await list_audit_log(session, status="error", page=1, page_size=50)
            assert all(r.error is not None for r in err_page.items)

            feature_page = await list_audit_log(session, feature="summary", page=1, page_size=50)
            assert all(r.feature == "summary" for r in feature_page.items)

            stats = await audit_log_stats(session, feature="draft")
            assert stats.total_requests >= 1
    finally:
        await engine.dispose()


async def test_reveal_pii_and_missing_map(mariadb_znuny_url: str) -> None:
    run_prefix = "audit-reveal-1"
    _cleanup_audit_rows(mariadb_znuny_url, run_id_prefix=run_prefix)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _write_row(
                session,
                settings=settings,
                run_id=f"{run_prefix}-with-pii",
                feature="draft",
                pii_mapping={"[EMAIL_1]": "real@example.com"},
            )
            await _write_row(
                session, settings=settings, run_id=f"{run_prefix}-no-pii", feature="draft"
            )

        async with factory() as session:
            with_pii = (
                await session.execute(
                    select(TiqoraAiAuditLog).where(
                        TiqoraAiAuditLog.run_id == f"{run_prefix}-with-pii"
                    )
                )
            ).scalar_one()
            mapping = await reveal_pii(session, with_pii, settings=settings, admin_user_id=1)
            assert mapping == {"[EMAIL_1]": "real@example.com"}

            without_pii = (
                await session.execute(
                    select(TiqoraAiAuditLog).where(
                        TiqoraAiAuditLog.run_id == f"{run_prefix}-no-pii"
                    )
                )
            ).scalar_one()
            with pytest.raises(PiiRevealError):
                await reveal_pii(session, without_pii, settings=settings, admin_user_id=1)
    finally:
        await engine.dispose()


async def test_get_audit_log_entry_returns_full_payload(mariadb_znuny_url: str) -> None:
    run_id = "audit-detail-1"
    _cleanup_audit_rows(mariadb_znuny_url, run_id_prefix=run_id)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            await _write_row(session, settings=settings, run_id=run_id, feature="draft")
            entry_id = (
                await session.execute(
                    select(TiqoraAiAuditLog.id).where(TiqoraAiAuditLog.run_id == run_id)
                )
            ).scalar_one()

        async with factory() as session:
            entry = await get_audit_log_entry(session, entry_id)
            assert entry is not None
            assert entry.request_json == '{"messages": []}'
            assert entry.response_json == '{"content": "ok"}'
    finally:
        await engine.dispose()


async def test_cleanup_deletes_only_rows_past_retention(mariadb_znuny_url: str) -> None:
    run_prefix = "audit-cleanup-1"
    _cleanup_audit_rows(mariadb_znuny_url, run_id_prefix=run_prefix)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    old_ts = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=60)
    try:
        async with factory() as session:
            await _write_row(
                session,
                settings=settings,
                run_id=f"{run_prefix}-old",
                feature="draft",
                ts_override=old_ts,
            )
            await _write_row(
                session, settings=settings, run_id=f"{run_prefix}-recent", feature="draft"
            )

        async with factory() as session:
            deleted = await cleanup_audit_log(session, retention_days=30)
            assert deleted >= 1

            remaining = (
                (
                    await session.execute(
                        select(TiqoraAiAuditLog.run_id).where(
                            TiqoraAiAuditLog.run_id.in_(
                                [f"{run_prefix}-old", f"{run_prefix}-recent"]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert f"{run_prefix}-old" not in remaining
            assert f"{run_prefix}-recent" in remaining
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Cost (token pricing on the provider)
# ---------------------------------------------------------------------------


async def test_list_cost_uses_bulk_loaded_provider_prices(mariadb_znuny_url: str) -> None:
    run_prefix = "audit-cost-89520"
    _cleanup_audit_rows(mariadb_znuny_url, run_id_prefix=run_prefix)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            priced = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name=f"{run_prefix}-priced",
                kind="openai_compat",
                base_url="https://api.example/v1",
                default_model="model-a",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
                price_input_per_1m=2.0,
                price_output_per_1m=4.0,
                price_currency="USD",
            )
            unpriced = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name=f"{run_prefix}-unpriced",
                kind="openai_compat",
                base_url="https://api.example/v1",
                default_model="model-b",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
            )
            await _write_row(
                session,
                settings=settings,
                run_id=f"{run_prefix}-priced",
                feature=f"{run_prefix}-feature",
                provider_id=priced.id,
                prompt_tokens=1_000_000,
                completion_tokens=500_000,
            )
            await _write_row(
                session,
                settings=settings,
                run_id=f"{run_prefix}-unpriced",
                feature=f"{run_prefix}-feature",
                provider_id=unpriced.id,
                prompt_tokens=1_000_000,
                completion_tokens=500_000,
            )

        async with factory() as session:
            page = await list_audit_log(
                session, feature=f"{run_prefix}-feature", page=1, page_size=50
            )
            by_run_id = {r.run_id: r for r in page.items}
            priced_row = by_run_id[f"{run_prefix}-priced"]
            unpriced_row = by_run_id[f"{run_prefix}-unpriced"]

            prices = await load_provider_prices(session, [r.provider_id for r in page.items])
            priced_cost, priced_currency = compute_entry_cost(priced_row, prices)
            assert priced_cost == pytest.approx(2.0 + 2.0)
            assert priced_currency == "USD"

            unpriced_cost, unpriced_currency = compute_entry_cost(unpriced_row, prices)
            assert unpriced_cost is None
            assert unpriced_currency is None
    finally:
        await engine.dispose()


async def test_stats_total_cost_single_currency_vs_mixed(mariadb_znuny_url: str) -> None:
    run_prefix = "audit-cost-89521"
    _cleanup_audit_rows(mariadb_znuny_url, run_id_prefix=run_prefix)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()
    try:
        async with factory() as session:
            usd_a = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name=f"{run_prefix}-usd-a",
                kind="openai_compat",
                base_url="https://api.example/v1",
                default_model="model-a",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
                price_input_per_1m=1.0,
                price_output_per_1m=1.0,
                price_currency="USD",
            )
            usd_b = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name=f"{run_prefix}-usd-b",
                kind="openai_compat",
                base_url="https://api.example/v1",
                default_model="model-b",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=False,
                price_input_per_1m=1.0,
                price_output_per_1m=1.0,
                price_currency="USD",
            )
            await _write_row(
                session,
                settings=settings,
                run_id=f"{run_prefix}-a1",
                feature=f"{run_prefix}-feature",
                provider_id=usd_a.id,
                prompt_tokens=1_000_000,
                completion_tokens=0,
            )
            await _write_row(
                session,
                settings=settings,
                run_id=f"{run_prefix}-b1",
                feature=f"{run_prefix}-feature",
                provider_id=usd_b.id,
                prompt_tokens=1_000_000,
                completion_tokens=0,
            )

        async with factory() as session:
            stats = await audit_log_stats(session, feature=f"{run_prefix}-feature")
            assert stats.total_cost == pytest.approx(2.0)
            assert stats.cost_currency == "USD"

            eur = await ai_providers.create_provider(
                session,
                settings=settings,
                change_by=1,
                name=f"{run_prefix}-eur",
                kind="openai_compat",
                base_url="https://api.example/v1",
                default_model="model-c",
                api_key=None,
                extra_json=None,
                supports_tools=True,
                supports_streaming=True,
                eu_hosted=True,
                price_input_per_1m=1.0,
                price_output_per_1m=1.0,
                price_currency="EUR",
            )
            await _write_row(
                session,
                settings=settings,
                run_id=f"{run_prefix}-c1",
                feature=f"{run_prefix}-feature",
                provider_id=eur.id,
                prompt_tokens=1_000_000,
                completion_tokens=0,
            )

        async with factory() as session:
            mixed_stats = await audit_log_stats(session, feature=f"{run_prefix}-feature")
            assert mixed_stats.total_cost is None
            assert mixed_stats.cost_currency is None
    finally:
        await engine.dispose()
