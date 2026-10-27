"""Rule-based change risk (design I11): questionnaire, answers, automatic checks, override.

The risk of a change is a score: the points of the answered questionnaire plus the points of the
findings the registered checks report (``CHANGE_RISK_CHECKS``; the eight core checks come first).
The level follows the thresholds of the settings; a manager may override it (reason required, one
``risk_override`` event). :func:`risk_input` is the stable contract other modules read (X3).

Checks read stored data only and are bounded: the stored impact set, a capped list of overlapping
changes (``load_conflicts``, system view, unfiltered), counts and short id lists. A check that
raises is logged and shows up as a ``check_failed`` finding with 0 points; each check runs in its
own savepoint so a failing statement cannot poison the transaction (ruling F8).

Findings keep numbers in ``params`` and ids only in ``refs`` (``ticket:<id>`` | ``ci:<id>`` |
``service:<id>`` | ``freeze:<id>``), at most :data:`REF_CAP` per finding. The stored findings are
unfiltered (guards need them); every read for a person goes through :func:`read_findings`, which
drops the refs the viewer cannot read and only counts them. A ``block`` finding stops the change
at its guards: ``core.freeze`` blocks on a blocking freeze window and, failing closed, whenever the
freeze occurrences were cut off (``freeze_truncated``).

Services never commit; route handlers do.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.impact import services_reached_from
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraCmdbCatalog, TiqoraCmdbCi
from tiqora.domain.schemas import UtcDateTime
from tiqora.domain.ticket_access import CHUNK, readable_tickets
from tiqora.extensions import ExtensionPoint, get_registry
from tiqora.itsm.change.conflicts import Conflict, load_conflicts
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.gate import is_change_manager, require_change_mode
from tiqora.itsm.change.impact import INCIDENT_SCAN_CAP, open_incident_ids
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeImpact,
    TiqoraItsmChangeRiskAnswer,
    TiqoraItsmChangeRiskQuestion,
)
from tiqora.itsm.change.phases import FINAL_PHASES
from tiqora.itsm.change.service import _row, _ticket, record_event
from tiqora.itsm.change.settings import ItsmChangeSettings, load_change_settings
from tiqora.itsm.ci_provider import CiRef
from tiqora.itsm.models import TiqoraItsmServiceMeta
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.settings import load_itsm_settings
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

RISK_LEVELS: Final = ("low", "medium", "high", "very_high")
Severity = Literal["info", "warn", "block"]
_SEVERITIES: Final = frozenset({"info", "warn", "block"})
#: ids listed in one finding (the count is in ``params``)
REF_CAP: Final = 10
#: findings message keys live under this frontend i18n prefix
_KEY: Final = "itsm.change.risk.findings."
CHECK_FAILED_KEY: Final = _KEY + "check_failed"
_MAX_POINTS: Final = 1000
_EXAMPLE_KEYS: Final = ("downtime", "backout_tested", "users_affected", "experience")


# --- contract (X3) ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskFinding:
    check_key: str
    severity: Severity
    points: int
    #: frontend i18n key under ``itsm.change.risk.findings.*``
    message_key: str
    #: numbers only, no free text and no ids
    params: Mapping[str, Any]
    #: ``ticket:<id>`` | ``ci:<id>`` | ``service:<id>`` | ``freeze:<id>``, filtered at read time
    refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RiskInput:
    """What a check (and itsm-beyond) sees of a change. Field names are fixed (X3)."""

    ticket_id: int
    change_type: str
    phase: str
    planned_start: datetime | None
    planned_end: datetime | None
    #: CIs / services linked to the change
    direct_ci_ids: frozenset[int]
    direct_service_ids: frozenset[int]
    #: CIs / services reached by propagation only (disjoint from the direct ones)
    impacted_ci_ids: frozenset[int]
    impacted_service_ids: frozenset[int]
    impact_truncated: bool
    #: system view, unfiltered
    conflicts: tuple[Conflict, ...]
    #: ``question_key -> answer_key`` of the valid questions with a valid answer
    answers: Mapping[str, str]
    answer_points: int
    has_backout_plan: bool
    settings: ItsmChangeSettings
    #: ticket type names of incidents (additive: ``core.open_incidents`` gets only this input)
    incident_types: tuple[str, ...] = ()


RiskCheckRun = Callable[[AsyncSession, RiskInput], Awaitable[Sequence[RiskFinding]]]


@dataclass(frozen=True, slots=True)
class RiskCheck:
    #: ``core.service_criticality``, ...
    key: str
    run: RiskCheckRun


CHANGE_RISK_CHECKS: Final[ExtensionPoint[RiskCheck]] = ExtensionPoint(
    key="itsm.change_risk_checks",
    item_key=lambda c: c.key,
    description="Rule-based change risk checks (synchronous, bounded reads only).",
    sdk_stable=False,
)


@dataclass(frozen=True, slots=True)
class RiskEvaluation:
    ticket_id: int
    #: ``None`` until the first evaluation
    score: int | None
    level: str | None
    override_level: str | None
    override_reason: str | None
    #: the override wins over the computed level
    effective_level: str | None
    findings: tuple[RiskFinding, ...]
    evaluated_at: datetime | None

    @property
    def blocked(self) -> bool:
        """A ``block`` finding stops the change at its guards."""
        return any(f.severity == "block" for f in self.findings)


def score_level(score: int, cfg: ItsmChangeSettings) -> str:
    """Pure: ``low`` below the medium threshold, then ``medium``, ``high``, ``very_high``."""
    if score >= cfg.risk_very_high_from:
        return "very_high"
    if score >= cfg.risk_high_from:
        return "high"
    if score >= cfg.risk_medium_from:
        return "medium"
    return "low"


# --- questionnaire ---------------------------------------------------------------------------


class ChangeRiskAnswerDef(BaseModel):
    key: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,49}$")
    label: str = Field(..., min_length=1, max_length=200)
    #: negative points are mitigating answers; the score never drops below 0
    points: int = Field(0, ge=-50, le=50)


class ChangeRiskQuestionIn(BaseModel):
    question_key: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,49}$")
    question: str = Field(..., min_length=1, max_length=500)
    #: plain text
    help: str | None = Field(None, max_length=2000)
    answers: list[ChangeRiskAnswerDef] = Field(..., min_length=2, max_length=10)
    valid: bool = True

    @field_validator("question", "help")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _checks(self) -> ChangeRiskQuestionIn:
        if not self.question:
            raise ValueError("Die Frage darf nicht leer sein.")
        keys = [a.key for a in self.answers]
        if len(set(keys)) != len(keys):
            raise ValueError("Die Antwortschlüssel müssen eindeutig sein.")
        return self


class ChangeRiskQuestionOut(BaseModel):
    id: int
    question_key: str
    question: str
    help: str | None
    answers: list[ChangeRiskAnswerDef]
    position: int
    valid: bool


class ChangeRiskQuestionOrderIn(BaseModel):
    #: every question id exactly once
    order: list[int] = Field(..., max_length=500)


class ChangeRiskExamplesOut(BaseModel):
    #: question keys that were inserted (existing ones are left alone)
    created: list[str]


def _question_out(row: TiqoraItsmChangeRiskQuestion) -> ChangeRiskQuestionOut:
    return ChangeRiskQuestionOut(
        id=int(row.id),
        question_key=row.question_key,
        question=row.question,
        help=row.help,
        answers=[ChangeRiskAnswerDef.model_validate(a) for a in json.loads(row.answers)],
        position=int(row.position),
        valid=bool(row.valid),
    )


def _dump_answers(answers: Sequence[ChangeRiskAnswerDef]) -> str:
    return json.dumps([a.model_dump() for a in answers], ensure_ascii=False)


async def _question_rows(
    session: AsyncSession, *, only_valid: bool
) -> list[TiqoraItsmChangeRiskQuestion]:
    stmt = select(TiqoraItsmChangeRiskQuestion).order_by(
        TiqoraItsmChangeRiskQuestion.position, TiqoraItsmChangeRiskQuestion.id
    )
    if only_valid:
        stmt = stmt.where(TiqoraItsmChangeRiskQuestion.valid.is_(True))
    return list((await session.execute(stmt)).scalars())


async def list_questions(
    session: AsyncSession, *, include_invalid: bool = True
) -> list[ChangeRiskQuestionOut]:
    return [_question_out(r) for r in await _question_rows(session, only_valid=not include_invalid)]


async def _question(session: AsyncSession, question_id: int) -> TiqoraItsmChangeRiskQuestion:
    row = await session.get(TiqoraItsmChangeRiskQuestion, question_id)
    if row is None:
        raise ChangeError("risk_question_not_found", 404)
    return row


async def _key_taken(session: AsyncSession, key: str) -> bool:
    found = await session.execute(
        select(TiqoraItsmChangeRiskQuestion.id).where(
            TiqoraItsmChangeRiskQuestion.question_key == key
        )
    )
    return found.first() is not None


async def _next_position(session: AsyncSession) -> int:
    top = (
        await session.execute(select(func.max(TiqoraItsmChangeRiskQuestion.position)))
    ).scalar_one_or_none()
    return 0 if top is None else int(top) + 1


async def create_question(
    session: AsyncSession, *, data: ChangeRiskQuestionIn, user_id: int
) -> ChangeRiskQuestionOut:
    if await _key_taken(session, data.question_key):
        raise ChangeError("risk_question_key_exists", 409)
    now = utc_now()
    row = TiqoraItsmChangeRiskQuestion(
        question_key=data.question_key,
        question=data.question,
        help=data.help,
        answers=_dump_answers(data.answers),
        position=await _next_position(session),
        valid=data.valid,
        created_at=now,
        created_by=user_id,
        updated_at=now,
        updated_by=user_id,
    )
    session.add(row)
    await session.flush()
    return _question_out(row)


async def update_question(
    session: AsyncSession, *, question_id: int, data: ChangeRiskQuestionIn, user_id: int
) -> ChangeRiskQuestionOut:
    """Replace the question. The key stays (answers refer to it)."""
    row = await _question(session, question_id)
    if data.question_key != row.question_key:
        raise ChangeError("risk_question_key_immutable", 422)
    row.question = data.question
    row.help = data.help
    row.answers = _dump_answers(data.answers)
    row.valid = data.valid
    row.updated_at = utc_now()
    row.updated_by = user_id
    await session.flush()
    return _question_out(row)


async def delete_question(session: AsyncSession, *, question_id: int) -> None:
    """Delete the question and the answers given to it (``valid=False`` keeps them)."""
    row = await _question(session, question_id)
    await session.execute(
        delete(TiqoraItsmChangeRiskAnswer).where(
            TiqoraItsmChangeRiskAnswer.question_key == row.question_key
        )
    )
    await session.delete(row)
    await session.flush()


async def reorder_questions(
    session: AsyncSession, *, order: Sequence[int]
) -> list[ChangeRiskQuestionOut]:
    rows = await _question_rows(session, only_valid=False)
    if sorted(order) != sorted(int(r.id) for r in rows):
        raise ChangeError("risk_question_order_mismatch", 422)
    by_id = {int(r.id): r for r in rows}
    for position, question_id in enumerate(order):
        by_id[question_id].position = position
    await session.flush()
    return [_question_out(by_id[i]) for i in order]


def _examples() -> list[ChangeRiskQuestionIn]:
    def q(
        key: str, question: str, help_text: str, answers: list[tuple[str, str, int]]
    ) -> ChangeRiskQuestionIn:
        return ChangeRiskQuestionIn(
            question_key=key,
            question=question,
            help=help_text,
            answers=[ChangeRiskAnswerDef(key=k, label=lbl, points=p) for k, lbl, p in answers],
        )

    return [
        q(
            "downtime",
            "Ist während der Umsetzung mit einem Ausfall zu rechnen?",
            "Gemeint ist ein Ausfall oder eine Einschränkung für Anwender.",
            [("none", "Nein", 0), ("short", "Kurz (unter 30 Minuten)", 3), ("long", "Länger", 6)],
        ),
        q(
            "backout_tested",
            "Wurde der Rückfallplan getestet?",
            "Ein Rückfallplan, der nie ausprobiert wurde, gilt als ungetestet.",
            [("yes", "Ja", 0), ("partly", "Teilweise", 2), ("no", "Nein", 4)],
        ),
        q(
            "users_affected",
            "Wie viele Anwender sind betroffen?",
            "Schätzen Sie die Zahl der Anwender, die den Change bemerken können.",
            [("few", "Wenige", 0), ("many", "Viele", 2), ("all", "Alle", 5)],
        ),
        q(
            "experience",
            "Wurde ein solcher Change schon mehrfach durchgeführt?",
            "Erfahrung mit derselben Art von Änderung senkt das Risiko.",
            [("often", "Ja, regelmäßig", 0), ("seldom", "Selten", 2), ("never", "Erstmalig", 4)],
        ),
    ]


async def insert_examples(session: AsyncSession, *, user_id: int) -> list[str]:
    """Insert the four example questions whose keys do not exist yet; their keys."""
    created: list[str] = []
    for example in _examples():
        if await _key_taken(session, example.question_key):
            continue
        await create_question(session, data=example, user_id=user_id)
        created.append(example.question_key)
    return created


@dataclass(frozen=True, slots=True)
class _Answered:
    answers: dict[str, str]
    points: int
    complete: bool


async def _answered(session: AsyncSession, ticket_id: int) -> _Answered:
    """Answers that still match the valid questions; points come from the current definition."""
    questions = await _question_rows(session, only_valid=True)
    given = {
        str(r.question_key): str(r.answer_key)
        for r in (
            await session.execute(
                select(TiqoraItsmChangeRiskAnswer).where(
                    TiqoraItsmChangeRiskAnswer.ticket_id == ticket_id
                )
            )
        ).scalars()
    }
    answers: dict[str, str] = {}
    points = 0
    for question in questions:
        defs = {a["key"]: int(a["points"]) for a in json.loads(question.answers)}
        answer = given.get(question.question_key)
        if answer is not None and answer in defs:
            answers[question.question_key] = answer
            points += defs[answer]
    return _Answered(answers, points, complete=len(answers) == len(questions))


async def questionnaire_complete(session: AsyncSession, *, ticket_id: int) -> bool:
    """Every valid question is answered (true when there is no valid question)."""
    return (await _answered(session, ticket_id)).complete


# --- risk input and the core checks ----------------------------------------------------------


async def risk_input(session: AsyncSession, settings: Settings, *, ticket_id: int) -> RiskInput:
    """The input of every check; also what itsm-beyond reads (X3). Reads only."""
    cfg = await load_change_settings(session, settings)
    itsm_cfg = await load_itsm_settings(session, settings)
    row = await _row(session, ticket_id)
    impact = (
        await session.execute(
            select(
                TiqoraItsmChangeImpact.kind,
                TiqoraItsmChangeImpact.ref_key,
                TiqoraItsmChangeImpact.direct,
            ).where(TiqoraItsmChangeImpact.ticket_id == ticket_id)
        )
    ).all()

    def ids(kind: str, direct: bool) -> frozenset[int]:
        return frozenset(
            int(k) for kd, k, d in impact if kd == kind and bool(d) is direct and str(k).isdigit()
        )

    conflicts = await load_conflicts(session, ticket_id=ticket_id, user_id=None)
    answered = await _answered(session, ticket_id)
    return RiskInput(
        ticket_id=ticket_id,
        change_type=row.change_type,
        phase=row.phase,
        planned_start=row.planned_start,
        planned_end=row.planned_end,
        direct_ci_ids=ids("ci", True),
        impacted_ci_ids=ids("ci", False),
        direct_service_ids=ids("service", True),
        impacted_service_ids=ids("service", False),
        impact_truncated=bool(row.impact_truncated),
        conflicts=tuple(conflicts),
        answers=answered.answers,
        answer_points=answered.points,
        has_backout_plan=bool((row.backout_plan or "").strip()),
        settings=cfg,
        incident_types=(itsm_cfg.incident_type, itsm_cfg.major_incident_type),
    )


def _finding(
    check: str,
    severity: Severity,
    points: int,
    name: str,
    params: Mapping[str, Any],
    refs: Sequence[str] = (),
) -> RiskFinding:
    return RiskFinding(
        check_key=f"core.{check}",
        severity=severity,
        points=points,
        message_key=_KEY + name,
        params=params,
        refs=tuple(refs[:REF_CAP]),
    )


def _chunks(ids: Sequence[int]) -> list[Sequence[int]]:
    return [ids[i : i + CHUNK] for i in range(0, len(ids), CHUNK)]


_CRITICALITY_RE: Final = re.compile(r"^\s*(\d)")


async def _service_criticality(session: AsyncSession, inp: RiskInput) -> list[RiskFinding]:
    ids = sorted(inp.direct_service_ids | inp.impacted_service_ids)
    level: dict[int, int] = {}
    for chunk in _chunks(ids):
        rows = await session.execute(
            select(TiqoraItsmServiceMeta.service_id, TiqoraItsmServiceMeta.criticality).where(
                TiqoraItsmServiceMeta.service_id.in_(chunk)
            )
        )
        for service_id, criticality in rows:
            match = _CRITICALITY_RE.match(criticality or "")
            if match is not None:
                level[int(service_id)] = int(match.group(1))
    top = max(level.values(), default=0)
    if top < 3:
        return []
    critical = sorted(s for s, d in level.items() if d >= 3)
    refs = [f"service:{s}" for s in sorted(s for s, d in level.items() if d == top)]
    return [
        _finding(
            "service_criticality",
            "warn",
            (top - 2) * inp.settings.weight_service_criticality,
            "service_criticality",
            {"criticality": top, "services": len(critical)},
            refs,
        )
    ]


async def _ci_incident(session: AsyncSession, inp: RiskInput) -> list[RiskFinding]:
    ids = sorted(inp.direct_ci_ids | inp.impacted_ci_ids)
    by_function: dict[str, list[int]] = {"incident": [], "warning": []}
    for chunk in _chunks(ids):
        rows = await session.execute(
            select(TiqoraCmdbCi.id, TiqoraCmdbCatalog.functionality)
            .join(TiqoraCmdbCatalog, TiqoraCmdbCatalog.id == TiqoraCmdbCi.cur_incident_state_id)
            .where(
                TiqoraCmdbCi.id.in_(chunk), TiqoraCmdbCatalog.functionality.in_(list(by_function))
            )
        )
        for ci_id, functionality in rows:
            by_function[str(functionality)].append(int(ci_id))
    if not by_function["incident"] and not by_function["warning"]:
        return []
    refs = [f"ci:{i}" for i in sorted(by_function["incident"]) + sorted(by_function["warning"])]
    return [
        _finding(
            "ci_incident",
            "warn",
            inp.settings.weight_ci_incident,
            "ci_incident",
            {"incident": len(by_function["incident"]), "warning": len(by_function["warning"])},
            refs,
        )
    ]


async def _open_incidents(session: AsyncSession, inp: RiskInput) -> list[RiskFinding]:
    days = inp.settings.incident_lookback_days
    found = await open_incident_ids(
        session,
        inp.incident_types,
        sorted(inp.direct_service_ids | inp.impacted_service_ids),
        since=utc_now() - timedelta(days=days),
        cap=INCIDENT_SCAN_CAP,
    )
    if not found:
        return []
    return [
        _finding(
            "open_incidents",
            "warn",
            inp.settings.weight_open_incidents,
            "open_incidents",
            {"count": len(found), "lookback_days": days},
            [f"ticket:{i}" for i in found],
        )
    ]


async def _overlap(_session: AsyncSession, inp: RiskInput) -> list[RiskFinding]:
    overlaps = [c for c in inp.conflicts if c.kind == "change_overlap"]
    truncated = any(c.kind == "overlap_truncated" for c in inp.conflicts)
    if not overlaps and not truncated:
        return []
    refs = [f"ticket:{c.ref.split(':', 1)[1]}" for c in overlaps if c.ref is not None]
    return [
        _finding(
            "overlap",
            "warn",
            inp.settings.weight_overlap,
            "overlap",
            {"count": len(overlaps), "truncated": int(truncated)},
            refs,
        )
    ]


async def _freeze(_session: AsyncSession, inp: RiskInput) -> list[RiskFinding]:
    freezes = [c for c in inp.conflicts if c.kind == "freeze"]
    truncated = any(c.kind == "freeze_truncated" for c in inp.conflicts)
    if not freezes and not truncated:
        return []
    blocking = sum(1 for c in freezes if c.severity == "block")
    # fail closed: freeze windows that could not be examined completely block as well
    severity: Severity = "block" if blocking or truncated else "warn"
    refs = [c.ref for c in freezes if c.ref is not None]
    return [
        _finding(
            "freeze",
            severity,
            inp.settings.weight_freeze,
            "freeze_truncated" if truncated else "freeze",
            {"count": len(freezes), "blocking": blocking, "truncated": int(truncated)},
            refs,
        )
    ]


async def _failed_history(session: AsyncSession, inp: RiskInput) -> list[RiskFinding]:
    days = inp.settings.failed_change_lookback_days
    since = utc_now() - timedelta(days=days)
    closed = func.coalesce(TiqoraItsmChange.actual_end, TiqoraItsmChange.updated_at)
    found: set[int] = set()
    for kind, wanted in (("ci", inp.direct_ci_ids), ("service", inp.direct_service_ids)):
        for chunk in _chunks(sorted(wanted)):
            rows = await session.execute(
                select(TiqoraItsmChange.ticket_id)
                .join(
                    TiqoraItsmChangeImpact,
                    TiqoraItsmChangeImpact.ticket_id == TiqoraItsmChange.ticket_id,
                )
                .where(
                    TiqoraItsmChange.ticket_id != inp.ticket_id,
                    TiqoraItsmChange.phase == "closed",
                    TiqoraItsmChange.outcome.in_(("failed", "backed_out")),
                    closed >= since,
                    TiqoraItsmChangeImpact.kind == kind,
                    TiqoraItsmChangeImpact.direct.is_(True),
                    TiqoraItsmChangeImpact.ref_key.in_([str(i) for i in chunk]),
                )
            )
            found.update(int(r[0]) for r in rows)
    if not found:
        return []
    return [
        _finding(
            "failed_history",
            "warn",
            inp.settings.weight_failed_history,
            "failed_history",
            {"count": len(found), "lookback_days": days},
            [f"ticket:{i}" for i in sorted(found, reverse=True)],
        )
    ]


async def _emergency(_session: AsyncSession, inp: RiskInput) -> list[RiskFinding]:
    if inp.change_type != "emergency":
        return []
    return [_finding("emergency", "info", inp.settings.weight_emergency, "emergency", {})]


async def _missing_backout(_session: AsyncSession, inp: RiskInput) -> list[RiskFinding]:
    if inp.change_type not in ("normal", "emergency") or inp.has_backout_plan:
        return []
    return [
        _finding(
            "missing_backout", "warn", inp.settings.weight_missing_backout, "missing_backout", {}
        )
    ]


def core_risk_checks() -> list[RiskCheck]:
    """The eight checks Tiqora ships (registered first, so their findings come first)."""
    return [
        RiskCheck("core.service_criticality", _service_criticality),
        RiskCheck("core.ci_incident", _ci_incident),
        RiskCheck("core.open_incidents", _open_incidents),
        RiskCheck("core.overlap", _overlap),
        RiskCheck("core.freeze", _freeze),
        RiskCheck("core.failed_history", _failed_history),
        RiskCheck("core.emergency", _emergency),
        RiskCheck("core.missing_backout", _missing_backout),
    ]


# --- evaluation ------------------------------------------------------------------------------


def _sanitize(found: RiskFinding, check_key: str) -> RiskFinding:
    severity: Severity = found.severity if found.severity in _SEVERITIES else "warn"
    return replace(
        found,
        check_key=found.check_key or check_key,
        severity=severity,
        points=max(0, min(_MAX_POINTS, int(found.points))),
        refs=tuple(found.refs),
    )


async def _run_checks(session: AsyncSession, inp: RiskInput) -> list[RiskFinding]:
    out: list[RiskFinding] = []
    for check in get_registry().items(CHANGE_RISK_CHECKS):
        try:
            async with session.begin_nested():
                found = await check.run(session, inp)
        except Exception:
            logger.exception(
                "change_risk_check_failed", extra={"check": check.key, "ticket_id": inp.ticket_id}
            )
            out.append(RiskFinding(check.key, "warn", 0, CHECK_FAILED_KEY, {}, ()))
            continue
        out.extend(_sanitize(f, check.key) for f in found)
    return out


def _dump_findings(findings: Sequence[RiskFinding]) -> str:
    return json.dumps(
        [
            {
                "check_key": f.check_key,
                "severity": f.severity,
                "points": f.points,
                "message_key": f.message_key,
                "params": dict(f.params),
                "refs": list(f.refs),
            }
            for f in findings
        ],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _load_findings(raw: str | None) -> tuple[RiskFinding, ...]:
    if not raw:
        return ()
    return tuple(
        RiskFinding(
            check_key=str(item["check_key"]),
            severity=item["severity"] if item["severity"] in _SEVERITIES else "warn",
            points=int(item["points"]),
            message_key=str(item["message_key"]),
            params=dict(item.get("params") or {}),
            refs=tuple(str(r) for r in item.get("refs") or ()),
        )
        for item in json.loads(raw)
    )


def stored_evaluation(row: TiqoraItsmChange) -> RiskEvaluation:
    """The last evaluation as stored (no recomputation)."""
    return RiskEvaluation(
        ticket_id=int(row.ticket_id),
        score=row.risk_score,
        level=row.risk_level,
        override_level=row.risk_override_level,
        override_reason=row.risk_override_reason,
        effective_level=row.risk_override_level or row.risk_level,
        findings=_load_findings(row.risk_findings),
        evaluated_at=row.risk_evaluated_at,
    )


async def _evaluate(
    session: AsyncSession, settings: Settings, row: TiqoraItsmChange
) -> RiskEvaluation:
    inp = await risk_input(session, settings, ticket_id=int(row.ticket_id))
    findings = await _run_checks(session, inp)
    score = max(0, inp.answer_points + sum(f.points for f in findings))
    row.risk_score = score
    row.risk_level = score_level(score, inp.settings)
    row.risk_findings = _dump_findings(findings)
    row.risk_evaluated_at = utc_now()
    await session.flush()
    return stored_evaluation(row)


async def evaluate_risk(
    session: AsyncSession, settings: Settings, *, ticket_id: int, user_id: int
) -> RiskEvaluation:
    """Run the checks and store score, level, findings and time.

    The caller has checked the caller's rights (the route, or the transition that calls this
    first); ``user_id`` only names who asked. Final changes are not evaluated.
    """
    _ = user_id
    await require_change_mode(session, settings)
    row = await _row(session, ticket_id, lock=True)
    if row.phase in FINAL_PHASES:
        raise ChangeError("change_final", 409)
    return await _evaluate(session, settings, row)


async def set_answers(
    session: AsyncSession,
    settings: Settings,
    *,
    ticket_id: int,
    answers: Mapping[str, str],
    user_id: int,
) -> RiskEvaluation:
    """Replace the answers (``question_key -> answer_key``) and evaluate again. Needs ``rw``."""
    await require_change_mode(session, settings)
    await _ticket(session, ticket_id, user_id=user_id, write=True)
    row = await _row(session, ticket_id, lock=True)
    if row.phase in FINAL_PHASES:
        raise ChangeError("change_final", 409)
    questions = {q.question_key: q for q in await _question_rows(session, only_valid=True)}
    points: dict[str, tuple[str, int]] = {}
    for question_key, answer_key in answers.items():
        question = questions.get(question_key)
        if question is None:
            raise ChangeError("risk_question_unknown", 422, question=question_key)
        defs = {a["key"]: int(a["points"]) for a in json.loads(question.answers)}
        if answer_key not in defs:
            raise ChangeError("risk_answer_unknown", 422, question=question_key)
        points[question_key] = (answer_key, defs[answer_key])

    now = utc_now()
    existing = {
        str(r.question_key): r
        for r in (
            await session.execute(
                select(TiqoraItsmChangeRiskAnswer).where(
                    TiqoraItsmChangeRiskAnswer.ticket_id == ticket_id
                )
            )
        ).scalars()
    }
    for question_key, old in existing.items():
        if question_key not in points:
            await session.delete(old)
    for question_key, (answer_key, answer_points) in points.items():
        current = existing.get(question_key)
        if current is None:
            session.add(
                TiqoraItsmChangeRiskAnswer(
                    ticket_id=ticket_id,
                    question_key=question_key,
                    answer_key=answer_key,
                    points=answer_points,
                    answered_at=now,
                    answered_by=user_id,
                )
            )
        elif (current.answer_key, current.points) != (answer_key, answer_points):
            current.answer_key = answer_key
            current.points = answer_points
            current.answered_at = now
            current.answered_by = user_id
    await session.flush()
    return await _evaluate(session, settings, row)


async def override_level(
    session: AsyncSession,
    settings: Settings,
    *,
    ticket_id: int,
    level: str | None,
    reason: str,
    user_id: int,
) -> RiskEvaluation:
    """A change manager sets (or, with ``level=None``, removes) the risk level by hand.

    The reason is required either way and is recorded in a ``risk_override`` event.
    """
    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    await _ticket(session, ticket_id, user_id=user_id)
    if not await is_change_manager(session, user_id, cfg):
        raise ChangeError("forbidden", 403)
    if level is not None and level not in RISK_LEVELS:
        raise ChangeError("risk_level_invalid", 422)
    text_reason = reason.strip()
    if not text_reason:
        raise ChangeError("risk_override_reason_required", 422)
    if len(text_reason) > 1000:
        raise ChangeError("risk_override_reason_too_long", 422)
    row = await _row(session, ticket_id, lock=True)
    if row.phase in FINAL_PHASES:
        raise ChangeError("change_final", 409)
    previous = row.risk_override_level
    row.risk_override_level = level
    row.risk_override_reason = None if level is None else text_reason
    row.risk_override_by = None if level is None else user_id
    row.updated_at = utc_now()
    row.updated_by = user_id
    await session.flush()
    await record_event(
        session,
        ticket_id=ticket_id,
        event="risk_override",
        user_id=user_id,
        detail={"from": previous, "to": level, "reason": text_reason},
    )
    return stored_evaluation(row)


# --- filtered read ---------------------------------------------------------------------------


class ChangeRiskFindingOut(BaseModel):
    check_key: str
    severity: Severity
    points: int
    message_key: str
    params: dict[str, Any]
    #: only references the caller may read
    refs: list[str]
    #: references left out because the caller cannot read them (a number only)
    hidden_ref_count: int


class ChangeRiskThresholdsOut(BaseModel):
    medium_from: int
    high_from: int
    very_high_from: int


class ChangeRiskOut(BaseModel):
    questions: list[ChangeRiskQuestionOut]
    #: ``question_key -> answer_key``
    answers: dict[str, str]
    answer_points: int
    questionnaire_complete: bool
    score: int | None
    level: str | None
    override_level: str | None
    override_reason: str | None
    effective_level: str | None
    evaluated_at: UtcDateTime | None
    findings: list[ChangeRiskFindingOut]
    blocked: bool
    thresholds: ChangeRiskThresholdsOut
    can_answer: bool
    can_override: bool


def _parse_ref(ref: str) -> tuple[str, int] | None:
    prefix, _, rest = ref.partition(":")
    return (prefix, int(rest)) if rest.isdigit() else None


async def _visible_refs(
    session: AsyncSession, ticket_id: int, user_id: int, findings: Sequence[RiskFinding]
) -> set[str]:
    """The refs among the findings' that ``user_id`` may see."""
    wanted: dict[str, set[int]] = {"ticket": set(), "ci": set(), "service": set(), "freeze": set()}
    for finding in findings:
        for ref in finding.refs:
            parsed = _parse_ref(ref)
            if parsed is not None and parsed[0] in wanted:
                wanted[parsed[0]].add(parsed[1])
    visible = {f"freeze:{i}" for i in wanted["freeze"]}  # freeze windows are listed for everyone

    tickets = await readable_tickets(session, sorted(wanted["ticket"]), user_id=user_id)
    visible |= {f"ticket:{i}" for i in tickets}

    if not wanted["ci"] and not wanted["service"]:
        return visible
    stored = (
        await session.execute(
            select(
                TiqoraItsmChangeImpact.kind,
                TiqoraItsmChangeImpact.ref_key,
                TiqoraItsmChangeImpact.direct,
            ).where(TiqoraItsmChangeImpact.ticket_id == ticket_id)
        )
    ).all()
    direct_services = {
        int(k) for kind, k, d in stored if kind == "service" and d and str(k).isdigit()
    }
    visible |= {f"service:{i}" for i in wanted["service"] & direct_services}
    indirect = wanted["service"] - direct_services
    # services reached only through CIs the caller cannot read stay unnamed (like read_impact)
    check_cis = (
        {int(k) for kind, k, _d in stored if kind == "ci" and str(k).isdigit()}
        if indirect
        else set(wanted["ci"])
    )
    provider = get_ci_provider("tiqora", SysConfig(session))
    if provider is None or not check_cis:
        return visible
    summaries = await provider.get_many(
        session, [CiRef("tiqora", i) for i in sorted(check_cis)], user_id=user_id
    )
    readable = {ref.id for ref in summaries}
    visible |= {f"ci:{i}" for i in wanted["ci"] & readable}
    if indirect and readable:
        reached = await services_reached_from(session, sorted(readable))
        visible |= {f"service:{i}" for i in indirect & reached}
    return visible


async def _findings_out(
    session: AsyncSession, ticket_id: int, user_id: int, findings: Sequence[RiskFinding]
) -> list[ChangeRiskFindingOut]:
    visible = await _visible_refs(session, ticket_id, user_id, findings)
    return [
        ChangeRiskFindingOut(
            check_key=f.check_key,
            severity=f.severity,
            points=f.points,
            message_key=f.message_key,
            params=dict(f.params),
            refs=[r for r in f.refs if r in visible],
            hidden_ref_count=sum(1 for r in f.refs if r not in visible),
        )
        for f in findings
    ]


async def read_findings(
    session: AsyncSession, *, ticket_id: int, user_id: int
) -> list[ChangeRiskFindingOut]:
    """The stored findings of a readable change, references the caller cannot read dropped."""
    await _ticket(session, ticket_id, user_id=user_id)
    row = await _row(session, ticket_id)
    return await _findings_out(session, ticket_id, user_id, stored_evaluation(row).findings)


async def read_risk(
    session: AsyncSession, settings: Settings, *, ticket_id: int, user_id: int
) -> ChangeRiskOut:
    """Questionnaire, answers and the stored evaluation as the caller may see them."""
    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    facts = await _ticket(session, ticket_id, user_id=user_id)
    row = await _row(session, ticket_id)
    evaluation = stored_evaluation(row)
    answered = await _answered(session, ticket_id)
    can_write = await PermissionEngine(session).check(user_id, facts.queue_id, "rw")
    open_phase = row.phase not in FINAL_PHASES
    return ChangeRiskOut(
        questions=await list_questions(session, include_invalid=False),
        answers=answered.answers,
        answer_points=answered.points,
        questionnaire_complete=answered.complete,
        score=evaluation.score,
        level=evaluation.level,
        override_level=evaluation.override_level,
        override_reason=evaluation.override_reason,
        effective_level=evaluation.effective_level,
        evaluated_at=evaluation.evaluated_at,
        findings=await _findings_out(session, ticket_id, user_id, evaluation.findings),
        blocked=evaluation.blocked,
        thresholds=ChangeRiskThresholdsOut(
            medium_from=cfg.risk_medium_from,
            high_from=cfg.risk_high_from,
            very_high_from=cfg.risk_very_high_from,
        ),
        can_answer=can_write and open_phase,
        can_override=open_phase and await is_change_manager(session, user_id, cfg),
    )


__all__ = [
    "CHANGE_RISK_CHECKS",
    "CHECK_FAILED_KEY",
    "RISK_LEVELS",
    "ChangeRiskAnswerDef",
    "ChangeRiskExamplesOut",
    "ChangeRiskFindingOut",
    "ChangeRiskOut",
    "ChangeRiskQuestionIn",
    "ChangeRiskQuestionOrderIn",
    "ChangeRiskQuestionOut",
    "ChangeRiskThresholdsOut",
    "RiskCheck",
    "RiskEvaluation",
    "RiskFinding",
    "RiskInput",
    "core_risk_checks",
    "create_question",
    "delete_question",
    "evaluate_risk",
    "insert_examples",
    "list_questions",
    "override_level",
    "questionnaire_complete",
    "read_findings",
    "read_risk",
    "reorder_questions",
    "risk_input",
    "score_level",
    "set_answers",
    "stored_evaluation",
    "update_question",
]
