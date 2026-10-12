"""Scheduled report delivery by mail (plan D12): the ``core.report_delivery`` job.

One job sends one run (``tiqora_report_run``): it evaluates everything again when it runs
(owner still valid, report access, share right and share groups of the stored recipients,
every report key for the owner), renders the files, stores them with the run and mails them
in one message with all recipients in ``Bcc`` (the ``To`` header is the empty group
``undisclosed-recipients:;``, so the envelope holds exactly the recipients and nothing goes
to the sender mailbox).

Failure handling (rulings 9, 13): every error path ends in :func:`_record_failure`, which
writes the German reason to the run (``error`` is at most 1000 characters, scrubbed of
addresses), counts scheduled failures and auto-pauses the schedule, and mails the owner on a
best-effort basis. Free text that reaches ``detail`` / ``error`` / ``paused_reason`` passes
:func:`scrub_text` first, so the run history holds no addresses (D14: nothing to erase).

Retry: a temporary SMTP error before the last attempt raises :class:`JobRetry`. The run
stays ``running`` and its files stay stored, so the next attempt sends them without
rendering again. After the ``sending`` checkpoint an interrupted job is never sent a second
time (it may have reached the relay); that case, and a timeout or restart on the last attempt,
end in :func:`on_delivery_interrupted`, which records the failure like any other.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal

import structlog
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from tiqora.channels.email.smtp import build_message, is_permanent_smtp_failure
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import (
    TiqoraJob,
    TiqoraReport,
    TiqoraReportRun,
    TiqoraReportRunFile,
    TiqoraReportSchedule,
)
from tiqora.domain.mail_log import write_mail_log
from tiqora.domain.mail_outbound import build_outbound_sender
from tiqora.domain.settings_store import KEY_NOTIFICATION_SENDER_EMAIL, get_setting
from tiqora.domain.user_preferences import bulk_get_preferences
from tiqora.filters import FilterError
from tiqora.jobs.context import JobCancelled, JobContext, JobFailed, JobRetry
from tiqora.jobs.registry import DEFAULT_LANE, JobHandler
from tiqora.jobs.service import enqueue_job, utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.permissions.sharing import ShareGroupError, check_share_groups
from tiqora.reporting.access import has_report_access
from tiqora.reporting.definition import DefinitionError, ReportDefinition
from tiqora.reporting.engine import run_report
from tiqora.reporting.export import report_sheet, write_report_csv, write_xlsx_workbook
from tiqora.reporting.jobs import (
    CSV_CONTENT_TYPE,
    PDF_CONTENT_TYPE,
    PDF_UNAVAILABLE_MESSAGE,
    XLSX_CONTENT_TYPE,
    export_slug,
    local_now,
    until_cancelled,
)
from tiqora.reporting.pdf import PdfMeta, PdfUnavailable, ReportSection, write_report_pdf
from tiqora.reporting.rights import ReportRights, report_rights
from tiqora.reporting.run_service import RequestError, RunOverrides, prepare_ref
from tiqora.reporting.schedule_schemas import (
    ScheduleRunFileOut,
    ScheduleRunOut,
    clean_line,
    normalise_address,
)
from tiqora.reporting.schedules import (
    FAILURES_BEFORE_PAUSE,
    ReportSchedule,
    load_schedule,
    person_label,
    resolve_recipients,
)
from tiqora.reporting.schemas import ReportResultOut
from tiqora.reporting.settings import ReportingSettings, load_reporting_settings
from tiqora.reporting.sql import ReportQueryTimeout
from tiqora.reporting.texts import ReportTexts, format_date_bucket, load_texts, server_export_labels
from tiqora.worker.notification_templates import usable_sender_email
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

REPORT_DELIVERY_KIND: Final = "core.report_delivery"
CHECKPOINT_SENDING: Final = "sending"
#: temporary SMTP errors are tried again, like ``gi_mail``
MAX_ATTEMPTS: Final = 3
RETRY_DELAY_SECONDS_PER_ATTEMPT: Final = 120

ZIP_CONTENT_TYPE: Final = "application/zip"
#: column lengths of ``tiqora_report_run``
_ERROR_MAX: Final = 1000
_PAUSED_MAX: Final = 500
_DETAIL_MAX: Final = 4000
_FINISHED: Final = ("succeeded", "partial", "failed", "skipped")

SCHEDULE_DELETED_MESSAGE: Final = "Der Zeitplan wurde gelöscht."
OWNER_NO_ACCESS_MESSAGE: Final = "Der Ersteller hat keinen Zugriff mehr auf Berichte."
SHARE_LOST_MESSAGE: Final = (
    "Für das Versenden an andere fehlt dem Ersteller inzwischen die Berechtigung."
)
NO_RECIPIENT_ADDRESS_MESSAGE: Final = "Keiner der Empfänger hat eine E-Mail-Adresse."
NO_SENDER_MESSAGE: Final = (
    "Kein Absender für Berichte eingerichtet. Bitte wenden Sie sich an einen Administrator."
)
UNEXPECTED_MESSAGE: Final = "Der Versand ist unerwartet fehlgeschlagen."
CANCELLED_MESSAGE: Final = "Der Versand wurde abgebrochen."
INTERRUPTED_MESSAGE: Final = (
    "Der Versand wurde unterbrochen; die E-Mail ist möglicherweise nicht angekommen."
)
#: cut off by the job timeout before anything was sent
TIMED_OUT_MESSAGE: Final = (
    "Der Versand hat zu lange gedauert und wurde abgebrochen; es wurde nichts versendet. "
    "Bitte wählen Sie weniger Berichte oder einen kürzeren Zeitraum."
)
#: interrupted (worker restart) on the last attempt, before anything was sent
ABORTED_MESSAGE: Final = (
    "Der Versand wurde wiederholt unterbrochen und abgebrochen; es wurde nichts versendet."
)
PAUSED_MISSING_MESSAGE: Final = "Zeitplan pausiert."
NOTHING_TO_SEND_MESSAGE: Final = "Keine Daten – nichts versendet."
ALREADY_ACTIVE_MESSAGE: Final = "Ein Versand läuft bereits."

#: anything that looks like an addr-spec (SMTP replies and library errors quote recipients)
_ADDRESS: Final = re.compile(r"[^\s<>\"',;:()\[\]]+@[^\s<>\"',;()\[\]]+")
_PLACEHOLDER: Final = re.compile(r"\{\{(\w+)\}\}")


class ReportDeliveryPayload(BaseModel):
    run_id: int


class DeliveryAlreadyActive(Exception):  # noqa: N818 - reads as a state, not an error class
    """:func:`enqueue_delivery` joined a job that is already active for the dedupe key."""


# ------------------------------------------------------------------ small helpers


def scrub_text(text: str, limit: int) -> str:
    """``text`` without anything that looks like an address, cut to ``limit`` characters.

    SMTP replies and library errors quote recipients; the run history must not keep them.
    """
    cleaned = _ADDRESS.sub("…", text).strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def apply_placeholders(template: str, values: Mapping[str, str]) -> str:
    """Replace ``{{name}}`` in ONE pass; substituted text is never scanned again and unknown
    names stay as written."""

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        return values[name] if name in values else match.group(0)

    return _PLACEHOLDER.sub(repl, template)


async def resolve_report_sender(session: AsyncSession) -> str | None:
    """Bare sender address: ``notification.sender_email``, else Znuny's
    ``NotificationSenderEmail`` when it is a real address; ``None`` when neither is."""
    override = usable_sender_email(await get_setting(session, KEY_NOTIFICATION_SENDER_EMAIL))
    configured = usable_sender_email(await SysConfig(session).get_str("NotificationSenderEmail"))
    for candidate in (override, configured):
        if candidate:
            address = normalise_address(candidate)
            if address:
                return address
    return None


def _day(value: date, texts: ReportTexts) -> str:
    return format_date_bucket(value.isoformat(), None, texts)


def _link(base_url: str, schedule_id: int | None, texts: ReportTexts) -> str:
    base = base_url.strip().rstrip("/")
    if base and schedule_id is not None:
        return f"{base}/agent/reports/schedules/{schedule_id}"
    return texts.fixed("mail.link_fallback")


async def _owner_name(session: AsyncSession, owner_id: int) -> str:
    row = (
        await session.execute(select(Users.first_name, Users.last_name).where(Users.id == owner_id))
    ).first()
    return person_label(row[0] if row else None, row[1] if row else None, owner_id)


async def _owner_address(session: AsyncSession, owner_id: int) -> str | None:
    prefs = await bulk_get_preferences(session, [owner_id], "UserEmail")
    raw = prefs.get(owner_id)
    return normalise_address(raw) if raw else None


# ------------------------------------------------------------------ run rows


async def create_run(
    session: AsyncSession,
    schedule: ReportSchedule,
    *,
    run_kind: Literal["schedule", "manual"],
    owner_only: bool,
    due_at: datetime | None,
    now: datetime,
) -> TiqoraReportRun:
    """Insert a ``queued`` run for ``schedule`` (flushed, not committed)."""
    run = TiqoraReportRun(
        schedule_id=schedule.id,
        schedule_name=schedule.name[:200],
        owner_user_id=schedule.owner_user_id,
        run_kind=run_kind,
        owner_only=owner_only,
        due_at=due_at,
        status="queued",
        report_keys=json.dumps([item.report_key for item in schedule.items]),
        created_at=now,
    )
    session.add(run)
    await session.flush()
    return run


async def enqueue_delivery(
    session: AsyncSession,
    run: TiqoraReportRun,
    *,
    dedupe_key: str,
    created_by: int | None = None,
) -> TiqoraJob:
    """Queue the delivery job of ``run`` and link it (``run.job_id``).

    A dedupe key that already has an active job for ANOTHER run raises
    :class:`DeliveryAlreadyActive` (the caller rolls the new run back).
    """
    job = await enqueue_job(
        session,
        kind=REPORT_DELIVERY_KIND,
        payload=ReportDeliveryPayload(run_id=int(run.id)),
        created_by=created_by if created_by is not None else int(run.owner_user_id),
        dedupe_key=dedupe_key,
    )
    if ReportDeliveryPayload.model_validate_json(job.payload).run_id != int(run.id):
        raise DeliveryAlreadyActive(dedupe_key)
    run.job_id = int(job.id)
    await session.flush()
    return job


def run_out(row: TiqoraReportRun, files: list[ScheduleRunFileOut]) -> ScheduleRunOut:
    return ScheduleRunOut(
        id=int(row.id),
        schedule_id=int(row.schedule_id) if row.schedule_id is not None else None,
        schedule_name=row.schedule_name,
        run_kind="manual" if row.run_kind == "manual" else "schedule",
        owner_only=bool(row.owner_only),
        status=row.status,  # type: ignore[arg-type]
        due_at=row.due_at,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        row_count=row.row_count,
        recipient_count=row.recipient_count,
        detail=row.detail,
        error=row.error,
        job_id=int(row.job_id) if row.job_id is not None else None,
        files=files,
    )


async def list_runs(session: AsyncSession, schedule_id: int, *, limit: int) -> list[ScheduleRunOut]:
    """Newest runs of a schedule with their file metadata (never the file contents)."""
    rows = list(
        (
            await session.execute(
                select(TiqoraReportRun)
                .where(TiqoraReportRun.schedule_id == schedule_id)
                .order_by(TiqoraReportRun.id.desc())
                .limit(limit)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    ids = [int(r.id) for r in rows]
    files: dict[int, list[ScheduleRunFileOut]] = {}
    if ids:
        file_rows = await session.execute(
            select(
                TiqoraReportRunFile.id,
                TiqoraReportRunFile.run_id,
                TiqoraReportRunFile.name,
                TiqoraReportRunFile.content_type,
                TiqoraReportRunFile.size_bytes,
            )
            .where(TiqoraReportRunFile.run_id.in_(ids))
            .order_by(TiqoraReportRunFile.id)
        )
        for file_id, run_id, name, content_type, size in file_rows.all():
            files.setdefault(int(run_id), []).append(
                ScheduleRunFileOut(
                    id=int(file_id), name=name, content_type=content_type, size_bytes=int(size)
                )
            )
    return [run_out(r, files.get(int(r.id), [])) for r in rows]


async def get_run_file(
    session: AsyncSession, run_id: int, file_id: int
) -> tuple[TiqoraReportRun, TiqoraReportRunFile] | None:
    """The run and one of its files (with the content), ``None`` when either is unknown."""
    run = await session.get(TiqoraReportRun, run_id)
    if run is None:
        return None
    file = (
        await session.execute(
            select(TiqoraReportRunFile)
            .options(undefer(TiqoraReportRunFile.data))
            .where(TiqoraReportRunFile.id == file_id, TiqoraReportRunFile.run_id == run_id)
        )
    ).scalar_one_or_none()
    return (run, file) if file is not None else None


async def share_problem(
    session: AsyncSession, schedule: ReportSchedule, owner_rights: ReportRights
) -> str | None:
    """German reason when the OWNER may no longer send to the stored recipients, else None.

    Evaluated when the data leaves (run time and "send to all"), not when the schedule was
    saved: recipients other than the owner need the share right, groups must still be share
    groups of the owner. Stale agents and roles are not an authorisation problem: they are
    reported per entry by :func:`resolve_recipients`.
    """
    rec = schedule.recipients
    owner = schedule.owner_user_id
    others = bool(rec.group_ids or rec.role_ids or rec.emails) or any(
        user_id != owner for user_id in rec.user_ids
    )
    if others and not owner_rights.share:
        return SHARE_LOST_MESSAGE
    if rec.group_ids:
        # the session was in autobegin already: the check only reads
        try:
            await check_share_groups(session, owner, rec.group_ids)
        except ShareGroupError:
            return SHARE_LOST_MESSAGE
    return None


# ------------------------------------------------------------------ failure path


async def _owner_mail(
    ctx: JobContext,
    *,
    owner_id: int,
    schedule_id: int | None,
    schedule_name: str,
    language: str,
    reason: str,
    paused: bool,
) -> None:
    """Best-effort plain mail to the owner (no attachment, no other recipient)."""
    try:
        texts = load_texts(language)
        async with ctx.session_factory() as session:
            sender = await resolve_report_sender(session)
            address = await _owner_address(session, owner_id)
            if sender is None or address is None:
                return
            link = _link(ctx.settings.public_base_url, schedule_id, texts)
            body_key = "mail.paused_body" if paused else "mail.failed_body"
            message = build_message(
                from_addr=sender,
                to_addrs=address,
                cc_addrs=None,
                subject=clean_line(texts.fixed("mail.failed_subject", name=schedule_name)),
                body=texts.fixed(body_key, name=schedule_name, reason=reason, link=link),
                content_type="text/plain",
                in_reply_to=None,
                loop_hint=True,
            )
            mailer = await build_outbound_sender(session, settings=ctx.settings)
            await mailer.send(message, envelope_from="")
    except Exception as exc:  # noqa: BLE001 - the failure is already recorded in the run
        logger.warning("report_delivery_owner_mail_failed", error=scrub_text(str(exc), 300))


async def _record_failure(
    ctx: JobContext,
    run_id: int,
    message: str,
    *,
    detail: str | None = None,
    count: bool = True,
    only_open: bool = False,
) -> None:
    """Mark the run failed, count it on the schedule (scheduled runs), pause after three in a
    row, and tell the owner. Never raises.

    ``count=False`` (a deliberate cancel) only ends the run: no failure count, no auto-pause,
    no owner mail. ``only_open`` leaves a run alone that already finished (interrupt hook).
    """
    text = scrub_text(message, _ERROR_MAX)
    owner_id: int | None = None
    schedule_id: int | None = None
    schedule_name = ""
    language = "de"
    paused = False
    try:
        async with ctx.session_factory() as session:
            run = await session.get(TiqoraReportRun, run_id)
            if run is None or (only_open and run.status not in ("queued", "running")):
                return
            now = utc_now()
            run.status = "failed"
            run.error = text
            if detail:
                run.detail = scrub_text(detail, _DETAIL_MAX)
            run.finished_at = now
            owner_id = int(run.owner_user_id)
            schedule_id = int(run.schedule_id) if run.schedule_id is not None else None
            schedule_name = run.schedule_name
            schedule = (
                await session.get(TiqoraReportSchedule, schedule_id)
                if schedule_id is not None
                else None
            )
            if schedule is not None:
                language = schedule.language
                if count and run.run_kind == "schedule":
                    schedule.failure_count = int(schedule.failure_count) + 1
                    schedule.last_status = "failed"
                    schedule.last_run_at = now
                    if schedule.failure_count >= FAILURES_BEFORE_PAUSE and schedule.active:
                        schedule.active = False
                        schedule.next_run_at = None
                        schedule.paused_reason = scrub_text(
                            f"Nach {FAILURES_BEFORE_PAUSE} Fehlversuchen angehalten: {text}",
                            _PAUSED_MAX,
                        )
                        paused = True
            await session.commit()
    except Exception:  # noqa: BLE001 - the job ends failed either way
        logger.exception("report_delivery_failure_not_recorded", run_id=run_id)
        return
    if count and owner_id is not None:
        await _owner_mail(
            ctx,
            owner_id=owner_id,
            schedule_id=schedule_id,
            schedule_name=schedule_name,
            language=language,
            reason=text,
            paused=paused,
        )


async def _fail(
    ctx: JobContext, run_id: int, message: str, *, detail: str | None = None
) -> JobFailed:
    await _record_failure(ctx, run_id, message, detail=detail)
    return JobFailed(scrub_text(message, _ERROR_MAX))


async def on_delivery_interrupted(ctx: JobContext, payload: ReportDeliveryPayload) -> None:
    """The job was cut off after its ``sending`` checkpoint, or before it on the last attempt
    (job timeout or worker restart). Ends the run like any other failure (count, auto-pause,
    owner mail); the message says whether the mail may have left."""
    async with ctx.session_factory() as session:
        checkpoint = (
            await session.execute(select(TiqoraJob.checkpoint).where(TiqoraJob.id == ctx.job_id))
        ).scalar_one_or_none()
    if checkpoint == CHECKPOINT_SENDING:
        message = INTERRUPTED_MESSAGE
    elif ctx.interrupted_by == "timeout":
        message = TIMED_OUT_MESSAGE
    else:
        message = ABORTED_MESSAGE
    await _record_failure(ctx, payload.run_id, message, only_open=True)


# ------------------------------------------------------------------ the job


@dataclass(frozen=True, slots=True)
class _Plan:
    """What the first step learned; plain values, no ORM rows."""

    schedule: ReportSchedule
    reporting: ReportingSettings
    texts: ReportTexts
    owner_name: str
    owner_only: bool
    run_kind: str
    existing_files: bool


@dataclass(frozen=True, slots=True)
class _Rendered:
    title: str
    definition: ReportDefinition
    result: ReportResultOut


@dataclass(frozen=True, slots=True)
class _File:
    name: str
    content_type: str
    data: bytes


async def _item_title(session: AsyncSession, key: str, texts: ReportTexts) -> str:
    if key.startswith("builtin."):
        name = key.removeprefix("builtin.")
        return texts.t(f"reports.builtin.{name}.title", default=name)
    if key.isdigit():
        found = (
            await session.execute(select(TiqoraReport.name).where(TiqoraReport.id == int(key)))
        ).scalar_one_or_none()
        if found:
            return str(found)
    return key


async def _prepare(ctx: JobContext, run_id: int) -> _Plan | dict[str, Any]:
    """Steps 1 to 3: load, mark running, re-check the owner. A dict is an early result."""
    async with ctx.session_factory() as session:
        run = await session.get(TiqoraReportRun, run_id)
        if run is None:
            return {"skipped": "run_missing"}
        if run.status in _FINISHED:
            return {"skipped": "run_finished"}
        schedule_id = int(run.schedule_id) if run.schedule_id is not None else None
        owner_only = bool(run.owner_only)
        run_kind = run.run_kind
        owner_id = int(run.owner_user_id)
        run.status = "running"
        if run.started_at is None:
            run.started_at = utc_now()
        await session.commit()
        schedule = await load_schedule(session, schedule_id) if schedule_id is not None else None
        if schedule is None:
            raise await _fail(ctx, run_id, SCHEDULE_DELETED_MESSAGE)
        if run_kind == "schedule" and not schedule.active:
            await session.execute(
                update(TiqoraReportRun)
                .where(TiqoraReportRun.id == run_id)
                .values(status="skipped", detail=PAUSED_MISSING_MESSAGE, finished_at=utc_now())
            )
            await session.commit()
            return {"skipped": "paused"}
        reporting = await load_reporting_settings(session, ctx.settings)
        if not await PermissionEngine(session).is_valid_user(
            owner_id
        ) or not await has_report_access(session, owner_id, reporting):
            raise await _fail(ctx, run_id, OWNER_NO_ACCESS_MESSAGE)
        if not owner_only:
            owner_rights = await report_rights(session, owner_id, reporting)
            problem = await share_problem(session, schedule, owner_rights)
            if problem is not None:
                raise await _fail(ctx, run_id, problem)
        files = (
            await session.execute(
                select(TiqoraReportRunFile.id).where(TiqoraReportRunFile.run_id == run_id).limit(1)
            )
        ).first()
        return _Plan(
            schedule=schedule,
            reporting=reporting,
            texts=load_texts(schedule.language),
            owner_name=await _owner_name(session, owner_id),
            owner_only=owner_only,
            run_kind=run_kind,
            existing_files=files is not None,
        )


async def _compute(ctx: JobContext, run_id: int, plan: _Plan) -> list[_Rendered]:
    """Step 5: every report of the bundle, for the owner, without the cache."""
    schedule, reporting = plan.schedule, plan.reporting
    cap = reporting.pdf_row_limit if schedule.format == "pdf" else reporting.export_row_limit
    out: list[_Rendered] = []
    async with ctx.session_factory() as session:
        total = len(schedule.items)
        for index, item in enumerate(schedule.items):
            await ctx.progress(index, total, "Berichte werden berechnet …")
            title = await _item_title(session, item.report_key, plan.texts)
            overrides = RunOverrides(
                time_range=item.overrides.time_range,
                grain=item.overrides.grain,
                time_zone=schedule.time_zone,
                parameters=dict(item.overrides.parameters),
                no_cache=True,
            )
            try:
                prepared = await prepare_ref(
                    session,
                    schedule.owner_user_id,
                    report_key=item.report_key,
                    definition=None,
                    overrides=overrides,
                )
                result = await until_cancelled(
                    ctx,
                    run_report(
                        session,
                        defn=prepared.definition,
                        params=prepared.params,
                        user_id=schedule.owner_user_id,
                        report_key=item.report_key,
                        reporting=reporting,
                        row_cap=cap,
                        use_cache=False,
                        budget=None,
                    ),
                )
            except (RequestError, DefinitionError, FilterError) as exc:
                raise await _fail(
                    ctx, run_id, f"Bericht „{title}“ ist nicht mehr verfügbar: {exc.message}"
                ) from exc
            except ReportQueryTimeout as exc:
                raise await _fail(
                    ctx,
                    run_id,
                    f"Bericht „{title}“ hat zu lange gebraucht. "
                    "Bitte wählen Sie einen kürzeren Zeitraum.",
                ) from exc
            if prepared.saved is not None:
                title = prepared.saved.name
            out.append(_Rendered(title, prepared.definition, result))
    return out


def _period(rendered: list[_Rendered], plan: _Plan) -> str:
    for entry in rendered:
        window = entry.result.time_window
        if window is not None:
            return f"{_day(window.start_date, plan.texts)} – {_day(window.end_date, plan.texts)}"
    return _day(local_now(plan.schedule.time_zone).date(), plan.texts)


def _info_lines(entry: _Rendered, texts: ReportTexts) -> tuple[str, ...]:
    window = entry.result.time_window
    if window is None:
        return ()
    return (
        texts.fixed(
            "pdf.period", start=_day(window.start_date, texts), end=_day(window.end_date, texts)
        ),
    )


async def _render(rendered: list[_Rendered], plan: _Plan, stamp: str) -> tuple[list[_File], int]:
    """Step 7: the attachment files and the total row count."""
    schedule, texts = plan.schedule, plan.texts
    rows = sum(len(entry.result.rows) for entry in rendered)
    stem = f"{export_slug(schedule.name)}-{stamp}"
    labels = [server_export_labels(entry.result, texts) for entry in rendered]
    if schedule.format == "pdf":
        sections = [
            ReportSection(
                title=entry.title,
                result=entry.result,
                visualization=entry.definition.visualization,
                labels=label,
                info_lines=_info_lines(entry, texts),
                row_limit=plan.reporting.pdf_row_limit,
            )
            for entry, label in zip(rendered, labels, strict=True)
        ]
        meta = PdfMeta(
            title=schedule.name,
            generated_at_local=local_now(schedule.time_zone),
            owner_line=texts.fixed("pdf.owner", owner=plan.owner_name),
        )
        # CPU bound: keep the event loop (heartbeats, cancel polling) running
        pdf = await asyncio.to_thread(write_report_pdf, sections, meta, texts)
        return [_File(f"{stem}.pdf", PDF_CONTENT_TYPE, pdf)], rows
    if schedule.format == "xlsx":
        sheets = [
            report_sheet(
                entry.result,
                title=entry.title,
                column_labels=label.column_labels,
                value_labels=label.value_labels,
                totals_label=label.totals_label,
                info_lines=_info_lines(entry, texts),
            )
            for entry, label in zip(rendered, labels, strict=True)
        ]
        book = await asyncio.to_thread(write_xlsx_workbook, sheets)
        return [_File(f"{stem}.xlsx", XLSX_CONTENT_TYPE, book)], rows
    csv_files: list[_File] = []
    used: set[str] = set()
    for entry, label in zip(rendered, labels, strict=True):
        data = write_report_csv(
            entry.result,
            column_labels=label.column_labels,
            value_labels=label.value_labels,
            totals_label=label.totals_label,
        )
        name = f"{export_slug(entry.title)}-{stamp}"
        unique, counter = name, 2
        while unique in used:
            unique, counter = f"{name}-{counter}", counter + 1
        used.add(unique)
        csv_files.append(_File(f"{unique}.csv", CSV_CONTENT_TYPE, data))
    if len(csv_files) == 1:
        return csv_files, rows
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in csv_files:
            archive.writestr(file.name, file.data)
    return [_File(f"{stem}.zip", ZIP_CONTENT_TYPE, buf.getvalue())], rows


async def _store_files(run_id: int, ctx: JobContext, files: list[_File], rows: int) -> None:
    async with ctx.session_factory() as session:
        now = utc_now()
        for file in files:
            session.add(
                TiqoraReportRunFile(
                    run_id=run_id,
                    name=file.name[:255],
                    content_type=file.content_type[:100],
                    size_bytes=len(file.data),
                    data=file.data,
                    created_at=now,
                )
            )
        await session.execute(
            update(TiqoraReportRun).where(TiqoraReportRun.id == run_id).values(row_count=rows)
        )
        await session.commit()


async def _load_files(ctx: JobContext, run_id: int) -> list[_File]:
    async with ctx.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(TiqoraReportRunFile)
                    .options(undefer(TiqoraReportRunFile.data))
                    .where(TiqoraReportRunFile.run_id == run_id)
                    .order_by(TiqoraReportRunFile.id)
                )
            )
            .scalars()
            .all()
        )
        return [_File(r.name, r.content_type, bytes(r.data)) for r in rows]


async def _skip_empty(ctx: JobContext, run_id: int, plan: _Plan) -> dict[str, Any]:
    now = utc_now()
    async with ctx.session_factory() as session:
        await session.execute(
            update(TiqoraReportRun)
            .where(TiqoraReportRun.id == run_id)
            .values(status="skipped", detail=NOTHING_TO_SEND_MESSAGE, row_count=0, finished_at=now)
        )
        await session.execute(
            update(TiqoraReportSchedule)
            .where(TiqoraReportSchedule.id == plan.schedule.id)
            .values(last_status="skipped", last_run_at=now, failure_count=0)
        )
        await session.commit()
    return {"skipped": "empty"}


def _compose(
    plan: _Plan,
    ctx: JobContext,
    *,
    titles: list[str],
    period: str,
    sender: str,
    addresses: tuple[str, ...],
    files: list[_File],
) -> tuple[Any, str]:
    schedule, texts = plan.schedule, plan.texts
    values = {
        "schedule": clean_line(schedule.name),
        "period": clean_line(period),
        "date": _day(local_now(schedule.time_zone).date(), texts),
        "reports": clean_line(", ".join(titles)),
        "owner": clean_line(plan.owner_name),
    }
    subject_template = schedule.subject.strip() or texts.fixed("mail.default_subject")
    subject = clean_line(apply_placeholders(subject_template, values)) or values["schedule"]
    body_template = schedule.body.strip() or texts.fixed("mail.default_body")
    footer = texts.fixed(
        "mail.footer",
        owner=plan.owner_name,
        link=_link(ctx.settings.public_base_url, schedule.id, texts),
    )
    body = f"{apply_placeholders(body_template, values)}\n\n-- \n{footer}\n"
    message = build_message(
        from_addr=sender,
        # the empty group: no recipient in the envelope but the Bcc list, none shown to anybody
        to_addrs="undisclosed-recipients:;",
        cc_addrs=None,
        bcc_addrs=", ".join(addresses),
        subject=subject,
        body=body,
        content_type="text/plain",
        in_reply_to=None,
        loop_hint=True,
        attachments=[(f.name, f.content_type, f.data) for f in files],
    )
    return message, subject


async def _send(
    ctx: JobContext, run_id: int, plan: _Plan, message: Any, subject: str, sender: str, count: int
) -> None:
    """Steps 10 and 11 (send, then the books)."""
    async with ctx.session_factory() as session:
        mailer = await build_outbound_sender(session, settings=ctx.settings)
    await ctx.checkpoint(CHECKPOINT_SENDING)
    try:
        await mailer.send(message, envelope_from="")
    except Exception as exc:  # noqa: BLE001 - SMTP, TLS, DNS, timeouts: all end in retry or fail
        reason = scrub_text(str(exc) or exc.__class__.__name__, 300)
        temporary = not is_permanent_smtp_failure(exc)
        logger.warning(
            "report_delivery_send_failed",
            run_id=run_id,
            attempt=ctx.attempt,
            temporary=temporary,
            error=reason,
        )
        message_text = f"Die E-Mail konnte nicht versendet werden: {reason}"
        if temporary and not ctx.last_attempt:
            raise JobRetry(
                scrub_text(message_text, _ERROR_MAX),
                delay_seconds=ctx.attempt * RETRY_DELAY_SECONDS_PER_ATTEMPT,
            ) from exc
        raise await _fail(ctx, run_id, message_text) from exc
    async with ctx.session_factory() as session:
        await write_mail_log(
            session,
            direction="out",
            status="sent",
            from_addr=sender,
            to_addr=f"{count} Empfänger (Bcc)",
            subject=subject,
            smtp_code=getattr(mailer, "last_smtp_code", None),
            detail=f"report_run {run_id}",
        )


async def _finish(
    ctx: JobContext, run_id: int, plan: _Plan, *, count: int, problems: tuple[str, ...]
) -> dict[str, Any]:
    now = utc_now()
    status = "partial" if problems else "succeeded"
    detail = scrub_text("\n".join(problems), _DETAIL_MAX) if problems else None
    async with ctx.session_factory() as session:
        await session.execute(
            update(TiqoraReportRun)
            .where(TiqoraReportRun.id == run_id)
            .values(
                status=status,
                recipient_count=count,
                detail=detail,
                error=None,
                finished_at=now,
            )
        )
        await session.execute(
            update(TiqoraReportSchedule)
            .where(TiqoraReportSchedule.id == plan.schedule.id)
            .values(last_status=status, last_run_at=now, failure_count=0)
        )
        await session.commit()
    return {"status": status, "recipients": count}


async def _deliver(ctx: JobContext, run_id: int) -> dict[str, Any]:
    planned = await _prepare(ctx, run_id)
    if isinstance(planned, dict):
        return planned
    plan = planned
    schedule = plan.schedule
    stamp = local_now(schedule.time_zone).date().isoformat()
    titles: list[str] = []
    period = ""
    if not plan.existing_files:
        rendered = await _compute(ctx, run_id, plan)
        titles = [entry.title for entry in rendered]
        period = _period(rendered, plan)
        if schedule.only_if_nonempty and all(not entry.result.rows for entry in rendered):
            return await _skip_empty(ctx, run_id, plan)
        await ctx.progress(len(rendered), len(rendered), "Dateien werden erstellt …")
        try:
            files, rows = await _render(rendered, plan, stamp)
        except PdfUnavailable as exc:
            raise await _fail(ctx, run_id, PDF_UNAVAILABLE_MESSAGE) from exc
        limit = plan.reporting.delivery_max_attachment_mb
        if sum(len(f.data) for f in files) > limit * 1024 * 1024:
            # kept for the owner when the database takes it (max_allowed_packet); the run
            # fails with the size message either way
            try:
                await _store_files(run_id, ctx, files, rows)
            except Exception as exc:  # noqa: BLE001 - the size message is the real reason
                logger.warning(
                    "report_delivery_oversized_file_not_stored",
                    run_id=run_id,
                    error=scrub_text(str(exc), 300),
                )
            raise await _fail(
                ctx,
                run_id,
                f"Der Anhang ist größer als {limit} MB. "
                "Bitte wählen Sie weniger Berichte oder einen kürzeren Zeitraum.",
            )
        await _store_files(run_id, ctx, files, rows)
    else:
        files = await _load_files(ctx, run_id)
        async with ctx.session_factory() as session:
            for item in schedule.items:
                titles.append(await _item_title(session, item.report_key, plan.texts))
        period = _day(local_now(schedule.time_zone).date(), plan.texts)
    async with ctx.session_factory() as session:
        resolved = await resolve_recipients(
            session, schedule, plan.reporting, owner_only=plan.owner_only
        )
        sender = await resolve_report_sender(session)
    problems = tuple(scrub_text(line, 300) for line in resolved.problems)
    if not resolved.addresses:
        raise await _fail(
            ctx, run_id, NO_RECIPIENT_ADDRESS_MESSAGE, detail="\n".join(problems) or None
        )
    if sender is None:
        raise await _fail(ctx, run_id, NO_SENDER_MESSAGE)
    message, subject = _compose(
        plan,
        ctx,
        titles=titles,
        period=period,
        sender=sender,
        addresses=resolved.addresses,
        files=files,
    )
    await _send(ctx, run_id, plan, message, subject, sender, len(resolved.addresses))
    return await _finish(ctx, run_id, plan, count=len(resolved.addresses), problems=problems)


async def run_delivery_job(ctx: JobContext, payload: ReportDeliveryPayload) -> dict[str, Any]:
    """Deliver one run; see the module docstring."""
    try:
        return await _deliver(ctx, payload.run_id)
    except (JobFailed, JobRetry):
        raise
    except JobCancelled:
        # a deliberate cancel: the run ends, but it is no failure of the schedule
        await _record_failure(ctx, payload.run_id, CANCELLED_MESSAGE, count=False)
        raise
    except Exception:
        # A CancelledError (runner timeout, shutdown) is deliberately not caught: before the
        # checkpoint the runner requeues the job, and the run must stay open for the next try.
        logger.exception("report_delivery_unexpected_error", run_id=payload.run_id)
        await _record_failure(ctx, payload.run_id, UNEXPECTED_MESSAGE)
        raise


REPORT_DELIVERY_JOB = JobHandler(
    kind=REPORT_DELIVERY_KIND,
    payload_model=ReportDeliveryPayload,
    run=run_delivery_job,
    title_key="jobs.kinds.core.report_delivery",
    timeout_seconds=1800,
    # NOT the one-slot mail lane: rendering must not hold up article mails
    lane=DEFAULT_LANE,
    max_attempts=MAX_ATTEMPTS,
    requeue_if_interrupted=True,
    on_interrupted=on_delivery_interrupted,
    result_private=True,
)
