"""Usage metering: record LLM calls, list/aggregate for the admin usage view
(plan §3.1/§3.6). Reporting only in Phase A — enforcement of ACL/budget
limits is wired into the agent runtime in later phases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import TiqoraAiUsage


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


__all__ = ["UsagePage", "list_usage", "record_usage"]
