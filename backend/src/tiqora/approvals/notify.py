"""Job ``core.approval_mail`` (A11): plain-text mails to approvers, substitutes and the requester.

One job mails one notification (``kind``) to the approver rows named in the payload (plus the
requester for ``decided``/``overdue``, plus an "in Vertretung" copy for active standing
substitutes). Every recipient is delivered in its own transaction and recorded as an audit row
``notified`` carrying the job id, so a retried job (``max_attempts=3``) never mails anybody twice:
approver recipients are found again by ``(approver_id, job_id)``, requester and substitute copies
(``approver_id`` NULL) by ``detail.recipient`` - ids only, no addresses or names in the trail.

Rules the mails follow:

* The decision link is a bearer credential for exactly one approver row. Only that row's mail
  carries it, and only when links are on, the step accepts e-mail decisions, a public base URL is
  configured and the link would not be born expired (step deadline more than 24 h past).
  Substitute copies (F4) and customers without a usable link point to the inbox instead.
* A mail holds the request's title, description, rule, deadline and the subject type's
  ``describe`` lines; never ticket articles, the step name, comments or the ticket title.
* A customer approver is only mailed while the customer-approver setting is on.
* The mail language is the recipient's ``UserLanguage``, else Znuny's ``DefaultLanguage``
  (German when that is not de/en).
* The link row is committed before SMTP, so no row lock is held while the relay is slow; a failed
  send revokes exactly that link. The ``notified`` audit row is committed after a successful send.
* A deleted ticket does not matter: the mail then goes without the ticket-number hook.
* A permanent SMTP failure is recorded and not retried; a temporary one retries the job
  (``JobRetry``, 120 s) until the last attempt, where it is recorded as well.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Literal

import structlog
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import audit, delegation, texts, tokens
from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    JOB_MAIL,
    Actor,
    ApprovalError,
    Principal,
    StepSpec,
)
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalEvent,
    TiqoraApprovalStep,
    TiqoraApprovalToken,
)
from tiqora.approvals.settings import ApprovalSettings, load_approval_settings
from tiqora.channels.email.smtp import build_message, is_permanent_smtp_failure
from tiqora.db.legacy.customer import CustomerPreferences, CustomerUser
from tiqora.db.legacy.ticket import Ticket
from tiqora.db.legacy.user import Users
from tiqora.domain import mail_outbound
from tiqora.domain.mail_log import write_mail_log
from tiqora.domain.notification_sender import resolve_notification_sender_address
from tiqora.domain.subject_hook import SubjectHookConfig, hooked_subject, load_subject_config
from tiqora.domain.user_preferences import bulk_get_preferences
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import Registry
from tiqora.jobs.context import JobContext, JobRetry
from tiqora.jobs.registry import MAIL_LANE, JobHandler
from tiqora.jobs.service import enqueue_job, utc_now
from tiqora.reporting.schedule_schemas import clean_line, normalise_address
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

MAX_ATTEMPTS: Final = 3
_RETRY_DELAY_SECONDS: Final = 120
_DEADLINE_GRACE: Final = timedelta(hours=24)  # the link's own limit past the step deadline
#: kinds whose recipients must still be pending approvers of an active step. ``overdue`` and
#: ``escalation`` are exempt: the sweeper may close the step (timeout) before the job runs, and
#: the notice is still due (the requester gets it, too).
_PENDING_KINDS: Final = frozenset({"request", "reminder", "forwarded"})
#: kinds that may carry the decision link (an overdue notice points to the inbox)
_LINK_KINDS: Final = frozenset({"request", "reminder", "escalation", "forwarded"})

MailKind = Literal[
    "request", "reminder", "escalation", "overdue", "decided", "cancelled", "forwarded"
]


class ApprovalMailPayload(BaseModel):
    approval_id: int
    kind: MailKind
    #: approver rows to mail (request/reminder/escalation/overdue/forwarded/cancelled)
    approver_ids: list[int] = []
    #: decided/overdue
    include_requester: bool = False
    #: active standing substitutes get an "in Vertretung" copy (action kinds only)
    include_substitutes: bool = True


@dataclass(slots=True)
class _Recipient:
    role: Literal["approver", "requester", "substitute"]
    principal: Principal
    approver_id: int | None = None
    #: ``requester`` / ``substitute:<user_id>`` for rows without an approver id
    marker: str | None = None
    #: delegators a substitute acts for, and the delegators' approver rows (the first gives the
    #: rule and deadline the copy shows)
    delegators: list[int] = field(default_factory=list)
    via_approver_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class _Plan:
    sender: str
    settings: ApprovalSettings
    tn: str | None
    hook: SubjectHookConfig | None
    recipients: list[_Recipient]
    #: Znuny ``DefaultLanguage``: the language of recipients without a ``UserLanguage``
    default_language: str = ""
    #: ``UserEmail`` / ``UserLanguage`` of the agent recipients, loaded once
    emails: dict[int, str] = field(default_factory=dict)
    languages: dict[int, str] = field(default_factory=dict)


def _result(
    approval_id: int, sent: int = 0, skipped: int = 0, failed: int = 0, **extra: Any
) -> dict[str, Any]:
    return {"approval_id": approval_id, "sent": sent, "skipped": skipped, "failed": failed, **extra}


async def enqueue_notification(
    session: AsyncSession,
    *,
    approval_id: int,
    kind: MailKind,
    approver_ids: Iterable[int] = (),
    include_requester: bool = False,
    include_substitutes: bool = True,
    registry: Registry | None = None,
) -> int | None:
    """Queues one ``core.approval_mail`` job; its id, or None when there is nobody to mail."""
    ids = list(approver_ids)
    if not ids and not include_requester:
        return None
    payload = ApprovalMailPayload(
        approval_id=approval_id,
        kind=kind,
        approver_ids=ids,
        include_requester=include_requester,
        include_substitutes=include_substitutes,
    )
    job = await enqueue_job(
        session, kind=JOB_MAIL, payload=payload, created_by=None, registry=registry
    )
    return int(job.id)


# ------------------------------------------------------------------ lookups


async def _address_and_language(
    session: AsyncSession, plan: _Plan, principal: Principal
) -> tuple[str | None, texts.TextLocale]:
    """Address and mail language: the recipient's ``UserLanguage``, else ``DefaultLanguage``."""
    if principal.kind == "agent":
        assert principal.user_id is not None  # noqa: S101 - Principal validates this
        mail = plan.emails.get(principal.user_id)
        language = plan.languages.get(principal.user_id)
    else:
        mail = (
            await session.execute(
                select(CustomerUser.email).where(CustomerUser.login == principal.login)
            )
        ).scalar_one_or_none()
        language = (
            await session.execute(
                select(CustomerPreferences.preferences_value).where(
                    CustomerPreferences.user_id == principal.login,
                    CustomerPreferences.preferences_key == "UserLanguage",
                )
            )
        ).scalar_one_or_none()
    return (normalise_address(mail) if mail else None), texts.text_locale(
        language or plan.default_language
    )


async def _display_name(session: AsyncSession, principal: Principal) -> str:
    if principal.kind == "agent":
        row = (
            await session.execute(
                select(Users.first_name, Users.last_name).where(Users.id == principal.user_id)
            )
        ).first()
    else:
        row = (
            await session.execute(
                select(CustomerUser.first_name, CustomerUser.last_name).where(
                    CustomerUser.login == principal.login
                )
            )
        ).first()
    return " ".join(f"{row[0]} {row[1]}".split()) if row else ""


def _parse(key: str) -> Principal | None:
    try:
        return Principal.parse(key)
    except ValueError:  # "system"
        return None


# ------------------------------------------------------------------ planning


async def _plan(
    session: AsyncSession, ctx: JobContext, payload: ApprovalMailPayload
) -> _Plan | dict[str, Any]:
    """Who gets mail (plain values only), or a finished job result when nothing can be sent."""
    approval = (
        await session.execute(
            select(TiqoraApproval).where(TiqoraApproval.id == payload.approval_id)
        )
    ).scalar_one_or_none()
    if approval is None:  # erased with its ticket (GDPR) since the job was queued
        return _result(payload.approval_id, note="gone")
    settings = await load_approval_settings(session, ctx.settings)
    sender = await resolve_notification_sender_address(session)
    if sender is None:
        logger.warning("approval_mail_no_sender", approval_id=approval.id, kind=payload.kind)
        return _result(approval.id, note="no_sender")
    if not ctx.settings.public_base_url.strip():
        logger.warning("approval_mail_no_base_url", approval_id=approval.id)

    tn = (
        await session.execute(select(Ticket.tn).where(Ticket.id == approval.ticket_id))
    ).scalar_one_or_none()
    hook = await load_subject_config(session, SysConfig(session)) if tn else None

    recipients: list[_Recipient] = []
    rows: list[TiqoraApprovalApprover] = []
    if payload.approver_ids:
        rows = list(
            (
                await session.execute(
                    select(TiqoraApprovalApprover)
                    .where(
                        TiqoraApprovalApprover.approval_id == approval.id,
                        TiqoraApprovalApprover.id.in_(payload.approver_ids),
                    )
                    .order_by(TiqoraApprovalApprover.id)
                )
            ).scalars()
        )
    by_id = {r.id: r for r in rows}
    for approver_id in payload.approver_ids:
        row = by_id.get(approver_id)
        if row is not None and (principal := _parse(row.principal)) is not None:
            recipients.append(_Recipient("approver", principal, approver_id=row.id))

    if payload.include_requester and (principal := _parse(approval.requester)) is not None:
        recipients.append(_Recipient("requester", principal, marker="requester"))

    if payload.include_substitutes and payload.kind in texts.ACTION_KINDS:
        recipients.extend(await _substitutes(session, approval, rows))
    agent_ids = sorted({r.principal.user_id for r in recipients if r.principal.user_id})
    return _Plan(
        sender,
        settings,
        str(tn) if tn else None,
        hook,
        recipients,
        default_language=await SysConfig(session).default_language(),
        emails=await bulk_get_preferences(session, agent_ids, "UserEmail"),
        languages=await bulk_get_preferences(session, agent_ids, "UserLanguage"),
    )


async def _substitutes(
    session: AsyncSession,
    approval: TiqoraApproval,
    rows: Sequence[TiqoraApprovalApprover],
) -> list[_Recipient]:
    """One copy per active substitute of a mailed agent approver (never requester/creator)."""
    live = [r for r in rows if r.user_id is not None and r.status == "pending"]
    if not live:
        return []
    now = utc_now()
    delegated = await delegation.active_substitutes(
        session, {int(r.user_id) for r in live if r.user_id}, at=now
    )
    excluded: set[int] = {int(r.user_id) for r in rows if r.user_id is not None}
    for key in (approval.requester, await audit.creator_key(session, approval)):
        principal = _parse(key) if key else None
        if principal is not None and principal.user_id is not None:
            excluded.add(principal.user_id)
    row_of = {int(r.user_id): r.id for r in reversed(live) if r.user_id is not None}
    copies: dict[int, list[int]] = {}
    for delegator, substitutes in sorted(delegated.items()):
        for substitute in sorted(substitutes - excluded):
            copies.setdefault(substitute, []).append(delegator)
    if not copies:
        return []
    valid = set(
        (
            await session.execute(
                select(Users.id).where(Users.id.in_(list(copies)), Users.valid_id == 1)
            )
        ).scalars()
    )
    return [
        _Recipient(
            "substitute",
            Principal.agent(substitute),
            marker=f"substitute:{substitute}",
            delegators=delegators,
            via_approver_ids=[row_of[d] for d in delegators],
        )
        for substitute, delegators in sorted(copies.items())
        if substitute in valid
    ]


# ------------------------------------------------------------------ delivery


async def _already_notified(
    session: AsyncSession, approval_id: int, job_id: int, recipient: _Recipient
) -> bool:
    events = (
        await session.execute(
            select(TiqoraApprovalEvent.approver_id, TiqoraApprovalEvent.detail).where(
                TiqoraApprovalEvent.approval_id == approval_id,
                TiqoraApprovalEvent.action == "notified",
                TiqoraApprovalEvent.job_id == job_id,
            )
        )
    ).all()
    if recipient.approver_id is not None:
        return any(approver_id == recipient.approver_id for approver_id, _ in events)
    for approver_id, detail in events:
        if (
            approver_id is None
            and detail
            and json.loads(detail).get("recipient") == recipient.marker
        ):
            return True
    return False


async def _describe_lines(session: AsyncSession, approval: TiqoraApproval) -> list[str]:
    subject_type = get_registry().get(APPROVAL_SUBJECT_TYPES, approval.subject_type)
    if subject_type is None or subject_type.describe is None:
        return []
    try:
        async with session.begin_nested():
            return [clean_line(line) for line in await subject_type.describe(session, approval.id)]
    except Exception as exc:  # noqa: BLE001 - consumer code (the ticket may be gone): mail without
        logger.warning(
            "approval_mail_describe_failed", approval_id=approval.id, error=type(exc).__name__
        )
        return []


async def _decision_link(
    session: AsyncSession,
    ctx: JobContext,
    settings: ApprovalSettings,
    row: TiqoraApprovalApprover,
    step: TiqoraApprovalStep,
    now: datetime,
) -> tuple[str, datetime, str] | None:
    """``(url, expires_at, digest)`` of a fresh link, or None when none may be mailed."""
    base = ctx.settings.public_base_url.strip().rstrip("/")
    if not base or not settings.email_links_enabled:
        return None
    if not StepSpec.model_validate_json(step.spec).allow_email:
        return None
    if step.due_at is not None and step.due_at + _DEADLINE_GRACE <= now:
        return None  # born expired: the inbox is the way in
    try:
        token = await tokens.issue_link(session, row.id, settings=settings)
    except ApprovalError:
        return None
    expires_at = (
        await session.execute(
            select(TiqoraApprovalToken.expires_at).where(
                TiqoraApprovalToken.token_hash == tokens.link_digest(token)
            )
        )
    ).scalar_one()
    return f"{base}/approve#t={token}", expires_at, tokens.link_digest(token)


def _inbox_url(ctx: JobContext, settings: ApprovalSettings, principal: Principal) -> str | None:
    base = ctx.settings.public_base_url.strip().rstrip("/")
    if not base:
        return None
    if principal.kind == "agent":
        return f"{base}/agent/approvals"
    return f"{base}/portal/approvals" if settings.portal_approvals_enabled else None


async def _forwarder_name(session: AsyncSession, row: TiqoraApprovalApprover) -> str:
    if row.source_kind != "forward" or not row.source_ref or not row.source_ref.isdigit():
        return ""
    old = (
        await session.execute(
            select(TiqoraApprovalApprover.principal).where(
                TiqoraApprovalApprover.id == int(row.source_ref)
            )
        )
    ).scalar_one_or_none()
    principal = _parse(old) if old else None
    return await _display_name(session, principal) if principal else ""


async def _deliver(
    session: AsyncSession,
    ctx: JobContext,
    payload: ApprovalMailPayload,
    plan: _Plan,
    recipient: _Recipient,
) -> Literal["sent", "skipped", "failed"]:
    """One recipient, one transaction (the caller rolls back on an exception)."""
    kind = payload.kind
    approval = (
        await session.execute(
            select(TiqoraApproval)
            .where(TiqoraApproval.id == payload.approval_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if approval is None or await _already_notified(session, approval.id, ctx.job_id, recipient):
        return "skipped"
    if kind in _PENDING_KINDS and approval.status != "pending":
        return "skipped"

    row: TiqoraApprovalApprover | None = None
    step: TiqoraApprovalStep | None = None
    if recipient.approver_id is not None:
        row = (
            await session.execute(
                select(TiqoraApprovalApprover)
                .where(
                    TiqoraApprovalApprover.id == recipient.approver_id,
                    TiqoraApprovalApprover.approval_id == approval.id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return "skipped"
        step = (
            await session.execute(
                select(TiqoraApprovalStep)
                .where(TiqoraApprovalStep.id == row.step_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if kind in _PENDING_KINDS and (
            row.status != "pending" or step is None or step.status != "active"
        ):
            return "skipped"
    elif recipient.via_approver_ids:
        step_id = (
            await session.execute(
                select(TiqoraApprovalApprover.step_id).where(
                    TiqoraApprovalApprover.id == recipient.via_approver_ids[0]
                )
            )
        ).scalar_one_or_none()
        if step_id is not None:
            step = (
                await session.execute(
                    select(TiqoraApprovalStep).where(TiqoraApprovalStep.id == step_id)
                )
            ).scalar_one_or_none()

    settings = plan.settings
    # a customer approver is only mailed while customer approvers are enabled
    if (
        recipient.role == "approver"
        and recipient.principal.kind == "customer"
        and not settings.customer_approvers_enabled
    ):
        return "skipped"
    address, locale = await _address_and_language(session, plan, recipient.principal)
    if address is None:
        logger.info("approval_mail_no_address", approval_id=approval.id, role=recipient.role)
        return "skipped"

    now = utc_now().replace(microsecond=0)
    link: tuple[str, datetime, str] | None = None
    if (
        recipient.role == "approver"
        and kind in _LINK_KINDS
        and row is not None
        and step is not None
    ):
        link = await _decision_link(session, ctx, settings, row, step, now)
    action = kind in texts.ACTION_KINDS
    requester = ""
    if kind == "request" and (requester_principal := _parse(approval.requester)) is not None:
        requester = await _display_name(session, requester_principal)
    substitute_for = ""
    if recipient.role == "substitute":
        names = [await _display_name(session, Principal.agent(d)) for d in recipient.delegators]
        substitute_for = ", ".join(n for n in names if n)
    age = ""
    if kind == "reminder" and step is not None:
        since = step.activated_at or approval.created_at
        age = texts.age_text(now - since, locale)
    name = await _forwarder_name(session, row) if kind == "forwarded" and row is not None else ""

    subject, body = texts.render(
        kind,
        locale,
        title=approval.title,
        requester=requester or None,
        rule=(
            texts.rule_line(step.rule, step.quorum, locale) if action and step is not None else None
        ),
        deadline=texts.moment_text(step.due_at, locale)
        if action and step and step.due_at
        else None,
        description=approval.description if action else None,
        lines=await _describe_lines(session, approval) if action else (),
        link=link[0] if link else None,
        expires=texts.moment_text(link[1], locale) if link else None,
        inbox_url=_inbox_url(ctx, settings, recipient.principal) if action else None,
        inbox_hint=recipient.principal.kind == "agent" or settings.portal_approvals_enabled,
        age=age or None,
        name=name or None,
        status="rejected" if approval.status == "rejected" else "approved",
        substitute_for=substitute_for or None,
    )
    subject = clean_line(subject)
    subject = hooked_subject(subject, hook_cfg=plan.hook, tn=plan.tn or "")
    message = build_message(
        from_addr=plan.sender,
        to_addrs=address,
        cc_addrs=None,
        subject=subject,
        body=body,
        content_type="text/plain",
        in_reply_to=None,
        loop_hint=True,
    )

    approval_id, ticket_id = approval.id, approval.ticket_id
    detail: dict[str, Any] = {"kind": kind}
    if recipient.marker:
        detail["recipient"] = recipient.marker
    mailer = await mail_outbound.build_outbound_sender(session, settings=ctx.settings)
    # the link row is committed before SMTP: no row lock is held while talking to the relay
    await session.commit()
    try:
        await mailer.send(message, envelope_from="")
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed silently
        await session.rollback()  # expires the ORM rows: reload what is needed below
        if link is not None:  # the link that was never mailed
            await session.execute(
                update(TiqoraApprovalToken)
                .where(TiqoraApprovalToken.token_hash == link[2])
                .values(revoked_at=utc_now().replace(microsecond=0))
            )
            await session.commit()
        permanent = is_permanent_smtp_failure(exc)
        await write_mail_log(
            session,
            direction="out",
            status="failed",
            from_addr=plan.sender,
            to_addr=address,
            subject=subject,
            ticket_id=ticket_id,
            smtp_code=getattr(mailer, "last_smtp_code", None),
            detail=type(exc).__name__,
        )
        logger.warning(
            "approval_mail_send_failed",
            approval_id=approval_id,
            kind=kind,
            permanent=permanent,
            error=type(exc).__name__,
            attempt=ctx.attempt,
        )
        if not permanent and ctx.attempt < ctx.max_attempts:
            raise JobRetry(
                "Die Freigabe-Mail konnte nicht zugestellt werden und wird erneut versucht.",
                delay_seconds=_RETRY_DELAY_SECONDS,
            ) from exc
        detail["error"] = "permanent" if permanent else "temporary"
        failed_for = (
            await session.execute(select(TiqoraApproval).where(TiqoraApproval.id == approval_id))
        ).scalar_one()
        await audit.record(
            session,
            approval=failed_for,
            action="notified",
            actor=Actor.system(),
            approver_id=recipient.approver_id,
            detail=detail,
            job_id=ctx.job_id,
        )
        await session.commit()
        return "failed"

    await write_mail_log(
        session,
        direction="out",
        status="sent",
        from_addr=plan.sender,
        to_addr=address,
        subject=subject,
        ticket_id=ticket_id,
        smtp_code=getattr(mailer, "last_smtp_code", None),
    )
    sent_for = (
        await session.execute(
            select(TiqoraApproval)
            .where(TiqoraApproval.id == approval_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    await audit.record(
        session,
        approval=sent_for,
        action="notified",
        actor=Actor.system(),
        approver_id=recipient.approver_id,
        detail=detail,
        job_id=ctx.job_id,
    )
    if recipient.approver_id is not None:
        await session.execute(
            update(TiqoraApprovalApprover)
            .where(TiqoraApprovalApprover.id == recipient.approver_id)
            .values(last_notified_at=now)
        )
    await session.commit()
    return "sent"


async def run_approval_mail(ctx: JobContext, payload: ApprovalMailPayload) -> dict[str, Any]:
    async with ctx.session_factory() as session:
        plan = await _plan(session, ctx, payload)
        if isinstance(plan, dict):
            await session.rollback()
            return plan
        await session.rollback()  # the planning reads end here; every recipient has its own
    counts = {"sent": 0, "skipped": 0, "failed": 0}
    for recipient in plan.recipients:
        async with ctx.session_factory() as session:
            try:
                counts[await _deliver(session, ctx, payload, plan, recipient)] += 1
            except BaseException:
                await session.rollback()
                raise
    return _result(payload.approval_id, **counts)


APPROVAL_MAIL_JOB = JobHandler(
    kind=JOB_MAIL,
    payload_model=ApprovalMailPayload,
    run=run_approval_mail,
    title_key="jobs.kinds.core.approval_mail",
    timeout_seconds=600,
    lane=MAIL_LANE,
    max_attempts=MAX_ATTEMPTS,
    requeue_if_interrupted=True,
)
