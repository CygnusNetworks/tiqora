"""Usage metering: record LLM calls, list/aggregate for the admin usage view
(plan §3.1/§3.6). Reporting only in Phase A — enforcement of ACL/budget
limits is wired into the agent runtime in later phases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import TiqoraAiUsage, TiqoraLlmProvider


async def _compute_cost_hint(
    session: AsyncSession,
    *,
    provider_id: int | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> float | None:
    """``prompt_tokens * price_input_per_1m/1e6 + completion_tokens *
    price_output_per_1m/1e6`` using the provider's configured prices.

    A missing price component counts as 0 *only* if the other component is
    set; if both are unset (or there is no provider), the result is
    ``None`` — "no pricing configured", not "free".
    """
    if provider_id is None:
        return None
    prices = (
        await session.execute(
            select(
                TiqoraLlmProvider.price_input_per_1m, TiqoraLlmProvider.price_output_per_1m
            ).where(TiqoraLlmProvider.id == provider_id)
        )
    ).first()
    if prices is None:
        return None
    price_input, price_output = prices
    if price_input is None and price_output is None:
        return None
    cost = 0.0
    if price_input is not None:
        cost += prompt_tokens * price_input / 1_000_000
    if price_output is not None:
        cost += completion_tokens * price_output / 1_000_000
    return cost


async def provider_budget_exceeded(
    session: AsyncSession, provider_id: int, *, now: datetime | None = None
) -> str | None:
    """Return ``"day"``/``"week"``/``"month"`` naming the first exceeded
    cost-budget window for this provider's spend (summed ``cost_hint``), or
    ``None`` if every configured window is within budget (or none are
    configured — ``None`` limits mean "no cap", same as unset pricing).

    Windows are calendar-aligned, matching
    ``auto_worker._tokens_used_today``'s naive-UTC-midnight convention: day
    = midnight today, week = midnight of the most recent Monday, month =
    the 1st of the current month. All spend counts toward the budget (no
    ``success`` filter) — a failed call can still have consumed billable
    tokens, same reasoning as the existing token-budget check.
    """
    provider = await session.get(TiqoraLlmProvider, provider_id)
    if provider is None:
        return None
    now = now or datetime.now(UTC).replace(tzinfo=None)
    day_start = datetime(now.year, now.month, now.day)
    week_start = day_start - timedelta(days=day_start.weekday())
    month_start = datetime(now.year, now.month, 1)
    windows = (
        ("day", provider.budget_cost_day, day_start),
        ("week", provider.budget_cost_week, week_start),
        ("month", provider.budget_cost_month, month_start),
    )
    for window_name, limit, window_start in windows:
        if limit is None:
            continue
        spent = (
            await session.execute(
                select(func.coalesce(func.sum(TiqoraAiUsage.cost_hint), 0.0)).where(
                    TiqoraAiUsage.provider_id == provider_id,
                    TiqoraAiUsage.ts >= window_start,
                )
            )
        ).scalar_one()
        if spent >= limit:
            return window_name
    return None


async def record_usage(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    queue_id: int | None = None,
    ticket_id: int | None = None,
    feature: str,
    provider_id: int | None = None,
    model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_hint: float | None = None,
    success: bool = True,
    error: str | None = None,
    extra_json: str | None = None,
) -> TiqoraAiUsage:
    if cost_hint is None:
        cost_hint = await _compute_cost_hint(
            session,
            provider_id=provider_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    row = TiqoraAiUsage(
        user_id=user_id,
        queue_id=queue_id,
        ticket_id=ticket_id,
        feature=feature,
        provider_id=provider_id,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_hint=cost_hint,
        success=success,
        error=error,
        extra_json=extra_json,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@dataclass(frozen=True, slots=True)
class UsagePage:
    items: list[TiqoraAiUsage]
    total: int
    total_prompt_tokens: int
    total_completion_tokens: int


async def list_usage(
    session: AsyncSession,
    *,
    queue_id: int | None = None,
    feature: str | None = None,
    ts_from: datetime | None = None,
    ts_to: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> UsagePage:
    filters = []
    if queue_id is not None:
        filters.append(TiqoraAiUsage.queue_id == queue_id)
    if feature is not None:
        filters.append(TiqoraAiUsage.feature == feature)
    if ts_from is not None:
        filters.append(TiqoraAiUsage.ts >= ts_from)
    if ts_to is not None:
        filters.append(TiqoraAiUsage.ts <= ts_to)

    stmt = select(TiqoraAiUsage).where(*filters)

    total = (
        await session.execute(select(func.count()).select_from(TiqoraAiUsage).where(*filters))
    ).scalar_one()

    agg_stmt = select(
        func.coalesce(func.sum(TiqoraAiUsage.prompt_tokens), 0),
        func.coalesce(func.sum(TiqoraAiUsage.completion_tokens), 0),
    ).where(*filters)
    total_prompt, total_completion = (await session.execute(agg_stmt)).one()

    page = max(1, page)
    page_size = max(1, min(500, page_size))
    rows = (
        (
            await session.execute(
                stmt.order_by(TiqoraAiUsage.ts.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return UsagePage(
        items=list(rows),
        total=int(total),
        total_prompt_tokens=int(total_prompt),
        total_completion_tokens=int(total_completion),
    )


__all__ = ["UsagePage", "list_usage", "provider_budget_exceeded", "record_usage"]
