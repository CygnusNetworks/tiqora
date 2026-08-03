"""GenericAgent executor — feature-flagged takeover of Znuny's GenericAgent daemon task.

Gated by the ``daemon.generic_agent.enabled`` tiqora_settings key (default OFF
— see ``tiqora.domain.settings_store``). Ports a pragmatic subset of
``Kernel::System::GenericAgent`` (``JobGet``/``JobRun``/``_JobRunTicket``):

- Jobs live in ``generic_agent_jobs`` (``job_name``, ``job_key``, ``job_value``
  — see ``scripts/database/schema.xml``), read the same way ``JobGet`` does:
  keys are grouped by name (repeated ``job_key`` rows become a list — Znuny's
  ``$Self->{Map}`` ``ARRAY`` fields), keys starting with ``New`` are actions,
  keys starting with ``DynamicField_`` are dynamic-field *actions* (only when
  paired with a ``New`` job — Tiqora reads them directly as ``New.DynamicField_*``
  regardless, a simplification), everything else is a search criterion.
- Ticket selection (``_JobRunTicket``'s ``TicketSearch`` call) supports the
  criteria subset named in the Phase 4b spec: ``StateIDs``/``QueueIDs``/
  ``PriorityIDs``/``OwnerIDs``/``LockIDs``/``TypeIDs`` (each OR-matched, AND
  across keys — Znuny's ``TicketSearch`` semantics), ``Title``/``CustomerID``
  (SQL ``LIKE``, ``*`` wildcard translated to ``%``), and
  ``Ticket{Create,Change,Close,Pending,Escalation,EscalationResponse,
  EscalationUpdate,EscalationSolution}Time{Older,Newer}Minutes`` (direct
  column range checks; CloseTime via a ``ticket_history`` EXISTS subquery —
  Znuny derives close time from StateUpdate/NewTicket history rows into a
  closed-type state). ``TimePoint`` criteria are translated to Older/Newer
  minutes, gated on the matching ``*SearchType`` key being ``TimePoint``
  (inactive TimePoint rows with an empty SearchType are ignored, like Znuny);
  ``TimeSlot`` is not ported (logged + ignored). ``SearchInArchive``/
  ``ArchiveFlags`` scopes on ``archive_flag`` and defaults to unarchived
  tickets (Znuny's ``Ticket::ArchiveSystem`` search default). A job with zero
  criteria refuses to run (mirrors Znuny's "no search attributes" guard) to
  avoid an accidental full-table sweep.
- Actions (``_JobRunTicket``): ``NewQueueID``/``NewStateID``/``NewPriorityID``/
  ``NewOwnerID``/``NewLockID``/``NewTitle``/``NewArchiveFlag`` via the matching
  ``domain.ticket_write_service`` mutator (so every Znuny invariant — history,
  ticket_index, escalation recompute, cache invalidation, outbox event — fires
  exactly as it would for an interactive edit); ``NewNoteBody``/``NewNoteSubject``
  as an internal note article (``AddNote`` history, matching
  ``_JobRunTicket``'s hard-coded ``HistoryComment``); ``New.DynamicField_*``
  values via ``update_dynamic_field``; ``NewDelete`` only when the
  ``daemon.generic_agent.allow_delete`` safety flag is also set (default OFF —
  Znuny's own console command has no such guard, but an unattended sweep with
  silent ticket deletion is judged too risky to enable by default here).
- Scheduling: ``ScheduleDays``/``ScheduleHours``/``ScheduleMinutes`` (Perl
  ``localtime`` weekday convention: 0=Sun..6=Sat) — a job without all three
  configured is treated as manual-only (never runs from this loop, matching
  Znuny's AdminGenericAgent validation) and skipped every tick.
- ``Valid`` job_key (``0``/``1``) gates whether the job runs at all; per-run
  ticket limit defaults to 4000 (``Ticket::GenericAgentRunLimit``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from prometheus_client import Counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_GENERIC_AGENT_ENABLED, get_setting_bool
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    add_article,
    archive_ticket,
    assign_owner,
    change_priority,
    change_state,
    change_title,
    lock_ticket,
    move_queue,
    unlock_ticket,
    update_dynamic_field,
)
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

KEY_GENERIC_AGENT_ALLOW_DELETE = "daemon.generic_agent.allow_delete"

GENERIC_AGENT_JOBS_RUN = Counter(
    "tiqora_generic_agent_jobs_run_total", "GenericAgent jobs evaluated (schedule matched)"
)
GENERIC_AGENT_TICKETS_MATCHED = Counter(
    "tiqora_generic_agent_tickets_matched_total", "Tickets matched by GenericAgent search criteria"
)
GENERIC_AGENT_TICKETS_ACTED = Counter(
    "tiqora_generic_agent_tickets_acted_total",
    "Tickets that had at least one GenericAgent action applied",
)
GENERIC_AGENT_ERRORS = Counter(
    "tiqora_generic_agent_errors_total", "Per-job or per-ticket GenericAgent errors"
)

_DEFAULT_RUN_LIMIT = 4000

_CRITERIA_ID_COLUMNS: dict[str, str] = {
    "StateIDs": "ticket_state_id",
    "QueueIDs": "queue_id",
    "PriorityIDs": "ticket_priority_id",
    "OwnerIDs": "user_id",
    "LockIDs": "ticket_lock_id",
    "TypeIDs": "type_id",
}

_TIME_RANGE_COLUMNS: dict[str, str] = {
    "TicketCreateTime": "create_time",
    "TicketChangeTime": "change_time",
    "TicketPendingTime": "until_time",
    "TicketEscalationTime": "escalation_time",
    "TicketEscalationResponseTime": "escalation_response_time",
    "TicketEscalationUpdateTime": "escalation_update_time",
    "TicketEscalationSolutionTime": "escalation_solution_time",
    # Pseudo-column: no close_time exists on ticket; Znuny derives it from
    # ticket_history (StateUpdate/NewTicket into a closed-type state) — see
    # the EXISTS subquery in build_ticket_query.
    "TicketCloseTime": "__close_time__",
}

# AdminGenericAgent stores each time criterion's mode in a ``*SearchType`` job
# key (``TimePoint``/``TimeSlot``/empty). Note the create-time oddity: its key
# is plain ``TimeSearchType``. TimePoint rows with an empty/absent SearchType
# are dead data the UI leaves behind and MUST NOT filter (the production
# Archive job carries inactive TicketChangeTimePoint/TicketCreateTimePoint
# rows exactly like that).
_TIME_SEARCH_TYPE_KEYS: dict[str, str] = {
    "TicketCreateTime": "TimeSearchType",
    "TicketChangeTime": "ChangeTimeSearchType",
    "TicketCloseTime": "CloseTimeSearchType",
    "TicketPendingTime": "PendingTimeSearchType",
    "TicketEscalationTime": "EscalationTimeSearchType",
    "TicketEscalationResponseTime": "EscalationResponseTimeSearchType",
    "TicketEscalationUpdateTime": "EscalationUpdateTimeSearchType",
    "TicketEscalationSolutionTime": "EscalationSolutionTimeSearchType",
}

# Znuny's TimePoint unit factors in minutes (month/year approximations match
# Kernel/System/GenericAgent.pm: 30 days / 365 days).
_TIME_POINT_FACTORS: dict[str, int] = {
    "minute": 1,
    "hour": 60,
    "day": 60 * 24,
    "week": 60 * 24 * 7,
    "month": 60 * 24 * 30,
    "year": 60 * 24 * 365,
}

_CLOSE_TIME_EXISTS = (
    "EXISTS (SELECT 1 FROM ticket_history th"
    " WHERE th.ticket_id = ticket.id"
    " AND th.history_type_id IN"
    " (SELECT id FROM ticket_history_type WHERE name IN ('StateUpdate', 'NewTicket'))"
    " AND th.state_id IN"
    " (SELECT s.id FROM ticket_state s"
    "  JOIN ticket_state_type st ON st.id = s.type_id WHERE st.name = 'closed')"
    " AND th.create_time {op} DATE_SUB(current_timestamp, INTERVAL :{pname} MINUTE))"
)


@dataclass
class GenericAgentJob:
    name: str
    valid: bool = True
    criteria: dict[str, list[str]] = field(default_factory=dict)
    actions: dict[str, str] = field(default_factory=dict)
    dynamic_field_actions: dict[str, str] = field(default_factory=dict)
    schedule_days: set[int] = field(default_factory=set)
    schedule_hours: set[int] = field(default_factory=set)
    schedule_minutes: set[int] = field(default_factory=set)

    @property
    def has_schedule(self) -> bool:
        return bool(self.schedule_days and self.schedule_hours and self.schedule_minutes)


def is_due(job: GenericAgentJob, now: datetime) -> bool:
    """Perl ``localtime`` weekday convention: 0=Sun, 1=Mon, ..., 6=Sat."""
    if not job.has_schedule:
        return False
    perl_wday = (now.weekday() + 1) % 7
    return (
        perl_wday in job.schedule_days
        and now.hour in job.schedule_hours
        and now.minute in job.schedule_minutes
    )


async def load_jobs(session: AsyncSession) -> list[GenericAgentJob]:
    """Port of ``JobGet`` grouping, across all distinct ``job_name`` values."""
    rows = (
        await session.execute(
            text("SELECT job_name, job_key, job_value FROM generic_agent_jobs ORDER BY job_name")
        )
    ).fetchall()

    by_name: dict[str, GenericAgentJob] = {}
    for job_name, key, value in rows:
        job = by_name.setdefault(str(job_name), GenericAgentJob(name=str(job_name)))
        key = str(key)
        value = "" if value is None else str(value)

        if key == "Valid":
            job.valid = value not in ("0", "")
        elif key == "ScheduleDays":
            with _ignore_value_error():
                job.schedule_days.add(int(value))
        elif key == "ScheduleHours":
            with _ignore_value_error():
                job.schedule_hours.add(int(value))
        elif key == "ScheduleMinutes":
            with _ignore_value_error():
                job.schedule_minutes.add(int(value))
        elif key.startswith("New"):
            action_key = key[len("New") :]
            job.actions[action_key] = value
        elif key.startswith("DynamicField_"):
            job.dynamic_field_actions[key[len("DynamicField_") :]] = value
        else:
            job.criteria.setdefault(key, []).append(value)

    return list(by_name.values())


class _ignore_value_error:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return exc_type is ValueError


def _like_pattern(value: str) -> str:
    """Znuny ``*`` wildcard → SQL ``%`` (simple translation, no escaping of literal %/_)."""
    return value.replace("*", "%")


def _first(criteria: dict[str, list[str]], key: str) -> str | None:
    values = criteria.get(key)
    return values[0] if values else None


def _time_point_minutes(
    criteria: dict[str, list[str]], prefix: str
) -> tuple[int | None, int | None]:
    """TimePoint criterion → ``(older_minutes, newer_minutes)``.

    Only active when the criterion's ``*SearchType`` key is ``TimePoint``
    (empty/absent SearchType means the TimePoint rows are inactive UI
    leftovers). ``Before`` → older-than, ``Last`` → within-the-last;
    ``Next``/``After`` (pending time futures) and ``TimeSlot`` are not
    ported — logged and ignored.
    """
    search_type = _first(criteria, _TIME_SEARCH_TYPE_KEYS[prefix]) or ""
    if search_type == "TimeSlot":
        logger.warning("generic_agent_time_slot_unsupported", prefix=prefix)
        return None, None
    if search_type != "TimePoint":
        return None, None
    try:
        point = int(_first(criteria, f"{prefix}Point") or "")
        factor = _TIME_POINT_FACTORS[(_first(criteria, f"{prefix}PointFormat") or "").lower()]
    except (ValueError, KeyError):
        logger.warning("generic_agent_time_point_invalid", prefix=prefix)
        return None, None
    minutes = point * factor
    start = _first(criteria, f"{prefix}PointStart") or "Before"
    if start == "Before":
        return minutes, None
    if start == "Last":
        return None, minutes
    logger.warning("generic_agent_time_point_start_unsupported", prefix=prefix, start=start)
    return None, None


def build_ticket_query(criteria: dict[str, list[str]]) -> tuple[str, dict[str, object]] | None:
    """Build a WHERE-clause fragment + params from the supported criteria subset.

    Returns ``None`` if no supported criterion is present (Znuny refuses to run
    a job with no search attributes at all — see module docstring).
    """
    clauses: list[str] = []
    params: dict[str, object] = {}
    n = 0

    for key, column in _CRITERIA_ID_COLUMNS.items():
        values = criteria.get(key)
        if not values:
            continue
        placeholders = []
        for v in values:
            try:
                iv = int(v)
            except ValueError:
                continue
            n += 1
            pname = f"p{n}"
            params[pname] = iv
            placeholders.append(f":{pname}")
        if placeholders:
            clauses.append(f"{column} IN ({', '.join(placeholders)})")

    if criteria.get("Title"):
        value = criteria["Title"][0]
        n += 1
        pname = f"p{n}"
        params[pname] = _like_pattern(value)
        clauses.append(f"title LIKE :{pname}")

    if criteria.get("CustomerID"):
        value = criteria["CustomerID"][0]
        n += 1
        pname = f"p{n}"
        params[pname] = _like_pattern(value)
        clauses.append(f"customer_id LIKE :{pname}")

    for prefix, column in _TIME_RANGE_COLUMNS.items():
        tp_older, tp_newer = _time_point_minutes(criteria, prefix)
        older_raw = _first(criteria, f"{prefix}OlderMinutes")
        newer_raw = _first(criteria, f"{prefix}NewerMinutes")
        older: int | None = tp_older
        newer: int | None = tp_newer
        with _ignore_value_error():
            if older_raw:
                older = int(older_raw)
        with _ignore_value_error():
            if newer_raw:
                newer = int(newer_raw)

        if older is not None:
            n += 1
            pname = f"p{n}"
            params[pname] = older
            if column == "__close_time__":
                clauses.append(_CLOSE_TIME_EXISTS.format(op="<=", pname=pname))
            elif column in ("create_time", "change_time"):
                clauses.append(f"{column} <= DATE_SUB(current_timestamp, INTERVAL :{pname} MINUTE)")
            else:
                # epoch-second columns (escalation_*, until_time)
                clauses.append(f"{column} > 0 AND {column} <= UNIX_TIMESTAMP() - :{pname} * 60")
        if newer is not None:
            n += 1
            pname = f"p{n}"
            params[pname] = newer
            if column == "__close_time__":
                clauses.append(_CLOSE_TIME_EXISTS.format(op=">=", pname=pname))
            elif column in ("create_time", "change_time"):
                clauses.append(f"{column} >= DATE_SUB(current_timestamp, INTERVAL :{pname} MINUTE)")
            else:
                clauses.append(f"{column} > 0 AND {column} >= UNIX_TIMESTAMP() - :{pname} * 60")

    if not clauses:
        return None

    # Archive scoping (Ticket::ArchiveSystem semantics): default to unarchived
    # tickets only — Znuny's TicketSearch default, and what keeps a nightly
    # NewArchiveFlag job from re-matching its own output forever. Deliberately
    # appended AFTER the no-criteria guard so it can't satisfy it by itself.
    archive_mode = _first(criteria, "SearchInArchive") or _first(criteria, "ArchiveFlags")
    if archive_mode == "ArchivedTickets":
        clauses.append("archive_flag = 1")
    elif archive_mode != "AllTickets":
        clauses.append("archive_flag = 0")

    return " AND ".join(clauses), params


async def select_tickets(
    session: AsyncSession, criteria: dict[str, list[str]], limit: int
) -> list[int]:
    built = build_ticket_query(criteria)
    if built is None:
        return []
    where_sql, params = built
    rows = (
        await session.execute(
            text(f"SELECT id FROM ticket WHERE {where_sql} ORDER BY id LIMIT :lim"),
            {**params, "lim": limit},
        )
    ).fetchall()
    return [int(r[0]) for r in rows]


async def apply_actions(
    session: AsyncSession,
    sysconfig: SysConfig,
    ticket_id: int,
    job: GenericAgentJob,
    *,
    user_id: int,
    allow_delete: bool,
) -> list[str]:
    """Apply one job's ``New*`` actions to one ticket. Returns the list of actions applied."""
    applied: list[str] = []
    actions = job.actions

    if actions.get("QueueID"):
        await move_queue(
            session,
            ticket_id=ticket_id,
            new_queue_id=int(actions["QueueID"]),
            user_id=user_id,
            sysconfig=sysconfig,
        )
        applied.append("QueueID")

    if actions.get("StateID"):
        await change_state(
            session,
            ticket_id=ticket_id,
            new_state_id=int(actions["StateID"]),
            user_id=user_id,
            sysconfig=sysconfig,
        )
        applied.append("StateID")

    if actions.get("PriorityID"):
        await change_priority(
            session,
            ticket_id=ticket_id,
            new_priority_id=int(actions["PriorityID"]),
            user_id=user_id,
            sysconfig=sysconfig,
        )
        applied.append("PriorityID")

    if actions.get("OwnerID"):
        await assign_owner(
            session,
            ticket_id=ticket_id,
            new_owner_id=int(actions["OwnerID"]),
            user_id=user_id,
            sysconfig=sysconfig,
        )
        applied.append("OwnerID")

    if actions.get("LockID"):
        lock_id = int(actions["LockID"])
        if lock_id == 2:
            await lock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
        elif lock_id == 1:
            await unlock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
        applied.append("LockID")

    if actions.get("Title"):
        await change_title(
            session, ticket_id=ticket_id, new_title=actions["Title"], user_id=user_id
        )
        applied.append("Title")

    archive_flag = actions.get("ArchiveFlag")
    if archive_flag in ("y", "n"):
        await archive_ticket(
            session,
            ticket_id=ticket_id,
            archive=archive_flag == "y",
            user_id=user_id,
            sysconfig=sysconfig,
        )
        applied.append("ArchiveFlag")

    note_body = actions.get("NoteBody")
    if note_body:
        article = ArticleIn(
            sender_type="agent",
            is_visible_for_customer=actions.get("NoteIsVisibleForCustomer", "0") == "1",
            subject=actions.get("NoteSubject") or "Note",
            body=note_body,
            content_type="text/plain; charset=utf-8",
            channel="note",
            history_type_override="AddNote",
        )
        await add_article(
            session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
        )
        applied.append("NoteBody")

    for field_name, value in job.dynamic_field_actions.items():
        await update_dynamic_field(
            session, ticket_id=ticket_id, field_name=field_name, values=[value], user_id=user_id
        )
        applied.append(f"DynamicField_{field_name}")

    if actions.get("Delete") and actions["Delete"] not in ("0", ""):
        if allow_delete:
            await _delete_ticket(session, ticket_id)
            applied.append("Delete")
        else:
            logger.warning(
                "generic_agent_delete_blocked",
                ticket_id=ticket_id,
                job=job.name,
                hint=f"set {KEY_GENERIC_AGENT_ALLOW_DELETE}=1 to allow",
            )

    return applied


async def _delete_ticket(session: AsyncSession, ticket_id: int) -> None:
    """Best-effort ordered delete of a ticket and its direct dependents.

    Only reached when ``daemon.generic_agent.allow_delete`` is explicitly set —
    see module docstring. Not a full port of ``Ticket.pm::TicketDelete`` (which
    also clears cache, search index, links, etc.); those are best-effort
    follow-ups via the normal outbox/reindex path and are not required for
    correctness of the delete itself.
    """
    article_ids = (
        await session.execute(
            text("SELECT id FROM article WHERE ticket_id = :tid"), {"tid": ticket_id}
        )
    ).fetchall()
    for (article_id,) in article_ids:
        await session.execute(
            text("DELETE FROM article_data_mime WHERE article_id = :aid"), {"aid": article_id}
        )
    await session.execute(text("DELETE FROM article WHERE ticket_id = :tid"), {"tid": ticket_id})
    await session.execute(
        text("DELETE FROM ticket_history WHERE ticket_id = :tid"), {"tid": ticket_id}
    )
    await session.execute(
        text(
            "DELETE FROM dynamic_field_value WHERE object_id = :tid"
            " AND field_id IN (SELECT id FROM dynamic_field WHERE object_type = 'Ticket')"
        ),
        {"tid": ticket_id},
    )
    await session.execute(text("DELETE FROM ticket WHERE id = :tid"), {"tid": ticket_id})


async def run_job(
    session_factory: async_sessionmaker[AsyncSession],
    job: GenericAgentJob,
    *,
    user_id: int,
    run_limit: int,
    allow_delete: bool,
) -> dict[str, int]:
    stats = {"matched": 0, "acted": 0, "errors": 0}
    async with session_factory() as session:
        ticket_ids = await select_tickets(session, job.criteria, run_limit)
    stats["matched"] = len(ticket_ids)
    GENERIC_AGENT_TICKETS_MATCHED.inc(len(ticket_ids))

    for ticket_id in ticket_ids:
        try:
            async with session_factory() as session, session.begin():
                sysconfig = SysConfig(session)
                applied = await apply_actions(
                    session, sysconfig, ticket_id, job, user_id=user_id, allow_delete=allow_delete
                )
            if applied:
                stats["acted"] += 1
                GENERIC_AGENT_TICKETS_ACTED.inc()
        except Exception:  # noqa: BLE001 — one broken ticket must not stop the job
            logger.exception("generic_agent_ticket_failed", job=job.name, ticket_id=ticket_id)
            stats["errors"] += 1
            GENERIC_AGENT_ERRORS.inc()

    return stats


async def run_generic_agent_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """One scheduler tick: check the feature flag, run every due+valid job."""
    _ = settings or get_settings()
    factory = session_factory or get_session_factory()
    current_time = now or datetime.now(UTC)

    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_GENERIC_AGENT_ENABLED, False)
        if not enabled:
            logger.debug("generic_agent_disabled")
            return {"enabled": 0}
        allow_delete = await get_setting_bool(session, KEY_GENERIC_AGENT_ALLOW_DELETE, False)
        sysconfig = SysConfig(session)
        user_id = await sysconfig.postmaster_user_id()
        run_limit_raw = await sysconfig.get("Ticket::GenericAgentRunLimit")
        run_limit = int(run_limit_raw) if run_limit_raw else _DEFAULT_RUN_LIMIT
        jobs = await load_jobs(session)

    totals = {"jobs": 0, "matched": 0, "acted": 0, "errors": 0}
    for job in jobs:
        if not job.valid or not is_due(job, current_time):
            continue
        GENERIC_AGENT_JOBS_RUN.inc()
        totals["jobs"] += 1
        try:
            stats = await run_job(
                factory, job, user_id=user_id, run_limit=run_limit, allow_delete=allow_delete
            )
        except Exception:  # noqa: BLE001 — one broken job must not stop the others
            logger.exception("generic_agent_job_failed", job=job.name)
            totals["errors"] += 1
            GENERIC_AGENT_ERRORS.inc()
            continue
        totals["matched"] += stats["matched"]
        totals["acted"] += stats["acted"]
        totals["errors"] += stats["errors"]

    logger.info("generic_agent_tick", **totals)
    return totals


__all__ = [
    "GenericAgentJob",
    "apply_actions",
    "build_ticket_query",
    "is_due",
    "load_jobs",
    "run_generic_agent_tick",
    "select_tickets",
]
