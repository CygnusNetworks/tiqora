"""Backfill ``tiqora_ai_article_origin.tool_trace_json`` (+ ``run_id``) for
auto-sent AI articles that predate the tool-trace feature (commit 2cd093b
added the column; 20260814_0036/0037 added the trace + run_id columns).

Reconstructs the trace from ``tiqora_ai_audit_log``: the same PII-masked
``messages`` array the runtime itself filters to ``role == "tool"`` when it
writes a *fresh* origin row (see ``tiqora.ai.runtime``) also lives in each
audit row's ``request_json``. Pre-feature origin rows have no ``run_id`` of
their own, so correlation is heuristic — see :func:`_correlate`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.audit import FEATURE_AUTO_REPLY
from tiqora.ai.models import SOURCE_AUTO, TiqoraAiArticleOrigin, TiqoraAiAuditLog
from tiqora.db.legacy.article import Article

# Audit rows are only considered if their timestamp is no more than this far
# *after* the origin row's own ``created`` — an auto-reply run's last audit
# write (the one carrying the full tool-call history) always precedes the
# article/origin insert that follows in the same transaction, but clock
# skew/latency between the audit write (separate connection, see
# tiqora.ai.audit) and the origin insert can put it a hair after.
CORRELATION_WINDOW = timedelta(seconds=5)


@dataclass
class BackfillItem:
    article_id: int
    ticket_id: int
    outcome: str  # "written" | "skipped"
    run_id: str | None = None
    tool_call_count: int | None = None
    reason: str | None = None  # set when outcome == "skipped"


@dataclass
class BackfillResult:
    dry_run: bool = False
    items: list[BackfillItem] = field(default_factory=list)

    @property
    def written(self) -> list[BackfillItem]:
        return [i for i in self.items if i.outcome == "written"]

    @property
    def skipped(self) -> list[BackfillItem]:
        return [i for i in self.items if i.outcome == "skipped"]

    def render(self) -> str:
        lines = [
            f"Tool-trace backfill {'(dry-run) ' if self.dry_run else ''}"
            f"— {len(self.items)} candidate(s)"
        ]
        for item in self.written:
            lines.append(
                f"  WRITE article_id={item.article_id} ticket_id={item.ticket_id} "
                f"run_id={item.run_id!r} tool_calls={item.tool_call_count}"
            )
        for item in self.skipped:
            lines.append(
                f"  SKIP  article_id={item.article_id} ticket_id={item.ticket_id} "
                f"reason={item.reason}"
            )
        lines.append(f"written={len(self.written)} skipped={len(self.skipped)}")
        return "\n".join(lines)


async def _find_candidates(
    session: AsyncSession, *, ticket_id: int | None
) -> list[tuple[TiqoraAiArticleOrigin, int]]:
    """Origin rows missing a trace, joined to their article's ticket_id
    (``TiqoraAiArticleOrigin`` itself has no ``ticket_id`` column)."""
    stmt = (
        select(TiqoraAiArticleOrigin, Article.ticket_id)
        .join(Article, Article.id == TiqoraAiArticleOrigin.article_id)
        .where(
            TiqoraAiArticleOrigin.tool_trace_json.is_(None),
            TiqoraAiArticleOrigin.source == SOURCE_AUTO,
        )
        .order_by(TiqoraAiArticleOrigin.article_id)
    )
    if ticket_id is not None:
        stmt = stmt.where(Article.ticket_id == ticket_id)
    rows = (await session.execute(stmt)).all()
    return [(origin, tid) for origin, tid in rows]


async def _correlate(
    session: AsyncSession, origin: TiqoraAiArticleOrigin, ticket_id: int
) -> tuple[str | None, list[dict[str, Any]] | None, str | None]:
    """Returns ``(run_id, tool_messages, skip_reason)`` — exactly one of
    ``skip_reason`` or the first two is populated."""
    cutoff = origin.created + CORRELATION_WINDOW
    stmt = (
        select(TiqoraAiAuditLog)
        .where(
            TiqoraAiAuditLog.ticket_id == ticket_id,
            TiqoraAiAuditLog.feature == FEATURE_AUTO_REPLY,
            TiqoraAiAuditLog.ts <= cutoff,
            TiqoraAiAuditLog.run_id.is_not(None),
        )
        .order_by(TiqoraAiAuditLog.ts)
    )
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return None, None, "no_audit_rows"

    groups: dict[str, list[TiqoraAiAuditLog]] = {}
    for row in rows:
        assert row.run_id is not None  # noqa: S101 — filtered by the query above
        groups.setdefault(row.run_id, []).append(row)

    # Per run_id group, the row with the latest ts carries the fullest
    # accumulated message history (a run's chat() calls grow the transcript
    # across tool-calling iterations). Rank groups by how close that latest
    # row lands to the origin's own creation time.
    scored: list[tuple[float, str, TiqoraAiAuditLog]] = []
    for run_id, group_rows in groups.items():
        latest = max(group_rows, key=lambda r: r.ts)
        distance = abs((origin.created - latest.ts).total_seconds())
        scored.append((distance, run_id, latest))
    scored.sort(key=lambda t: t[0])

    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, None, "ambiguous"

    _distance, run_id, latest = scored[0]
    try:
        payload = json.loads(latest.request_json)
    except ValueError:
        return None, None, "malformed_request_json"
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        return None, None, "malformed_request_json"
    tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
    return run_id, tool_messages, None


async def run_backfill(
    session: AsyncSession, *, dry_run: bool = False, ticket_id: int | None = None
) -> BackfillResult:
    """Reconstruct ``tool_trace_json``/``run_id`` for every candidate origin
    row (optionally scoped to one ticket). Caller commits — this function
    only mutates ORM objects already attached to ``session``, and does
    nothing at all in ``dry_run`` mode."""
    result = BackfillResult(dry_run=dry_run)
    candidates = await _find_candidates(session, ticket_id=ticket_id)
    for origin, tid in candidates:
        run_id, tool_messages, skip_reason = await _correlate(session, origin, tid)
        if skip_reason is not None:
            result.items.append(
                BackfillItem(
                    article_id=origin.article_id,
                    ticket_id=tid,
                    outcome="skipped",
                    reason=skip_reason,
                )
            )
            continue
        assert tool_messages is not None  # noqa: S101 — guaranteed by _correlate contract
        if not dry_run:
            origin.tool_trace_json = json.dumps(tool_messages)
            origin.run_id = run_id
        result.items.append(
            BackfillItem(
                article_id=origin.article_id,
                ticket_id=tid,
                outcome="written",
                run_id=run_id,
                tool_call_count=len(tool_messages),
            )
        )
    return result
