"""Config-driven retention policies: anonymize article content on old, closed tickets.

Rules are stored as JSON in ``tiqora_settings`` under
:data:`KEY_GDPR_RETENTION_RULES` — a list of objects::

    [{"name": "support-12mo", "queue": "Support", "state_type": "closed",
      "older_than_months": 12}]

A rule matches tickets in the named queue whose current state has the given
state *type* (``closed`` by default) and whose ``change_time`` is older than
``older_than_months``. Unlike :mod:`tiqora.gdpr.anonymize` (which purges a
whole customer), retention operates per-ticket: it scrubs
``article_data_mime`` (from/to/cc address occurrences + body) for the
matched tickets only, leaving ``customer_user`` untouched (the customer may
have other, non-expired tickets). Idempotency is tracked via
``tiqora_gdpr_audit`` rows (``action="retention_anonymize"``,
``target=f"ticket:{ticket_id}"``) so re-running a rule skips tickets already
processed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.legacy.article import Article, ArticleDataMime
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import Ticket, TicketState, TicketStateType
from tiqora.db.tiqora.models import TiqoraGdprAudit
from tiqora.domain.dev_anonymize import ValueMapper
from tiqora.domain.settings_store import get_setting
from tiqora.gdpr.audit import record_audit
from tiqora.gdpr.gate import require_write_gate

KEY_GDPR_RETENTION_RULES = "gdpr.retention.rules"


class RetentionConfigError(ValueError):
    """Raised for malformed retention-rule JSON."""


@dataclass(frozen=True)
class RetentionRule:
    name: str
    queue: str
    older_than_months: int
    state_type: str = "closed"
    seed: int | None = None


@dataclass
class RuleMatch:
    rule: RetentionRule
    ticket_ids: list[int] = field(default_factory=list)
    already_processed: list[int] = field(default_factory=list)

    @property
    def pending_ticket_ids(self) -> list[int]:
        processed = set(self.already_processed)
        return [t for t in self.ticket_ids if t not in processed]


@dataclass
class RetentionReport:
    matches: list[RuleMatch] = field(default_factory=list)

    def render(self) -> str:
        lines = ["Retention dry-run report", "========================="]
        if not self.matches:
            lines.append("No rules configured.")
        for m in self.matches:
            lines.append(
                f"rule={m.rule.name!r} queue={m.rule.queue!r} "
                f"state_type={m.rule.state_type!r} older_than_months={m.rule.older_than_months}"
            )
            lines.append(
                f"  matched tickets: {len(m.ticket_ids)}"
                f" (already processed: {len(m.already_processed)},"
                f" pending: {len(m.pending_ticket_ids)})"
            )
            if m.ticket_ids:
                sample = m.ticket_ids[:20]
                lines.append(f"  sample ticket ids: {sample}")
        return "\n".join(lines)


@dataclass
class RetentionRunResult:
    rules_applied: int = 0
    tickets_anonymized: int = 0
    articles_anonymized: int = 0
    progress: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "Retention run summary",
            "======================",
            f"rules applied:       {self.rules_applied}",
            f"tickets anonymized:  {self.tickets_anonymized}",
            f"articles anonymized: {self.articles_anonymized}",
        ]
        return "\n".join(lines)


def parse_rules(raw: str | None) -> list[RetentionRule]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RetentionConfigError(f"{KEY_GDPR_RETENTION_RULES} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RetentionConfigError(f"{KEY_GDPR_RETENTION_RULES} must be a JSON array")
    rules: list[RetentionRule] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise RetentionConfigError(f"rule #{i} must be a JSON object")
        try:
            rules.append(
                RetentionRule(
                    name=str(item["name"]),
                    queue=str(item["queue"]),
                    older_than_months=int(item["older_than_months"]),
                    state_type=str(item.get("state_type", "closed")),
                    seed=item.get("seed"),
                )
            )
        except KeyError as exc:
            raise RetentionConfigError(f"rule #{i} missing required key: {exc}") from exc
    return rules


def _months_ago(now: datetime, months: int) -> datetime:
    """Subtract *months* calendar months from *now* (day-of-month clamped)."""
    total = now.year * 12 + (now.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(now.day, 28)  # avoid day-overflow across shorter months
    return now.replace(year=year, month=month, day=day)


async def _match_rule(session: AsyncSession, rule: RetentionRule, *, now: datetime) -> list[int]:
    cutoff = _months_ago(now, rule.older_than_months)
    stmt = (
        select(Ticket.id)
        .join(Queue, Queue.id == Ticket.queue_id)
        .join(TicketState, TicketState.id == Ticket.ticket_state_id)
        .join(TicketStateType, TicketStateType.id == TicketState.type_id)
        .where(
            Queue.name == rule.queue,
            TicketStateType.name == rule.state_type,
            Ticket.change_time < cutoff,
        )
    )
    return list((await session.execute(stmt)).scalars().all())


async def _already_processed(session: AsyncSession, ticket_ids: list[int]) -> list[int]:
    if not ticket_ids:
        return []
    targets = [f"ticket:{tid}" for tid in ticket_ids]
    rows = (
        (
            await session.execute(
                select(TiqoraGdprAudit.target).where(
                    TiqoraGdprAudit.action == "retention_anonymize",
                    TiqoraGdprAudit.target.in_(targets),
                )
            )
        )
        .scalars()
        .all()
    )
    return [int(t.split(":", 1)[1]) for t in rows]


async def build_retention_report(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> RetentionReport:
    """Read-only: which tickets would be anonymized by each configured rule."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session:
        raw = await get_setting(session, KEY_GDPR_RETENTION_RULES)
        rules = parse_rules(raw)
        matches: list[RuleMatch] = []
        for rule in rules:
            ticket_ids = await _match_rule(session, rule, now=now)
            processed = await _already_processed(session, ticket_ids)
            matches.append(RuleMatch(rule=rule, ticket_ids=ticket_ids, already_processed=processed))
    return RetentionReport(matches=matches)


async def _anonymize_ticket_articles(
    session_factory: async_sessionmaker[AsyncSession],
    ticket_id: int,
    mapper: ValueMapper,
) -> int:
    async with session_factory() as session:
        article_ids = (
            (await session.execute(select(Article.id).where(Article.ticket_id == ticket_id)))
            .scalars()
            .all()
        )
    if not article_ids:
        return 0
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    ArticleDataMime.id,
                    ArticleDataMime.a_from,
                    ArticleDataMime.a_to,
                    ArticleDataMime.a_cc,
                    ArticleDataMime.a_body,
                ).where(ArticleDataMime.article_id.in_(article_ids))
            )
        ).all()
    count = 0
    for row in rows:
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE article_data_mime SET a_from=:a_from, a_to=:a_to,"
                    " a_cc=:a_cc, a_body=:a_body WHERE id=:id"
                ),
                {
                    "id": row.id,
                    "a_from": mapper.anonymize_address_field(row.a_from),
                    "a_to": mapper.anonymize_address_field(row.a_to),
                    "a_cc": mapper.anonymize_address_field(row.a_cc),
                    "a_body": mapper.anonymize_body(row.a_body),
                },
            )
        count += 1
    return count


async def run_retention(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: datetime | None = None,
    force_parallel: bool = False,
    actor: str = "cli",
) -> RetentionRunResult:
    """Apply every configured rule, anonymizing article content on matched,
    not-yet-processed tickets. Refuses unless schema-ownership is active or
    ``force_parallel=True``.
    """
    async with session_factory() as session:
        await require_write_gate(
            session, settings, force_parallel=force_parallel, operation="retention_run"
        )

    report = await build_retention_report(session_factory, now=now)
    result = RetentionRunResult()

    for match in report.matches:
        pending = match.pending_ticket_ids
        if not pending:
            continue
        mapper = ValueMapper(seed=match.rule.seed)
        rule_articles = 0
        for ticket_id in pending:
            n = await _anonymize_ticket_articles(session_factory, ticket_id, mapper)
            rule_articles += n
            async with session_factory() as session:
                await record_audit(
                    session,
                    action="retention_anonymize",
                    target=f"ticket:{ticket_id}",
                    actor=actor,
                    counts={"article_data_mime": n},
                    force_parallel=force_parallel,
                )
            result.tickets_anonymized += 1
        result.articles_anonymized += rule_articles
        result.rules_applied += 1
        result.progress.append(
            f"rule={match.rule.name!r}: {len(pending)} tickets, {rule_articles} articles"
        )

    return result
