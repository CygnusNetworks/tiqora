"""Job ``core.approval_mail`` (A11, Task 11): recipients, links, languages, retry safety.

Seed band 971500-971599 is reserved; the tests need only the helper world (970100-970599). Every
test runs inside the rolled-back ``approvals_session``; requests, jobs, tokens, audit and
mail-log rows are autoincrement rows that vanish with it.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from email.message import EmailMessage
from typing import Any

import aiosmtplib
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._approvals_helpers import (
    AG1,
    AG3,
    AG4,
    AG5,
    CUSTOMER_1,
    T1,
    CapturingMail,
    approvals_session,
    installed_registry,
    make_fake_subject_type,
)
from tests.approvals_consumer_kit import shared_session_factory
from tiqora.approvals import delegation, service, tokens
from tiqora.approvals.contract import JOB_MAIL, Actor, ApprovalDefinition
from tiqora.approvals.models import (
    TiqoraApprovalApprover,
    TiqoraApprovalEvent,
    TiqoraApprovalToken,
)
from tiqora.approvals.notify import (
    APPROVAL_MAIL_JOB,
    ApprovalMailPayload,
    enqueue_notification,
    run_approval_mail,
)
from tiqora.approvals.settings import APPROVALS_SECTION
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob, TiqoraMailLog
from tiqora.domain import mail_outbound
from tiqora.domain.settings_store import KEY_NOTIFICATION_SENDER_EMAIL, set_setting
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.jobs.context import JobContext, JobRetry
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.jobs.service import utc_now
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
BASE = "https://tiqora.example.org"
SETTINGS = Settings(environment="test", public_base_url=BASE)
SUBJECT = "test.subject"
REQUESTER = Actor.agent(AG1)
TN = f"20261015{T1}"
EMAIL = {
    AG1: "appr97-agent1@example.org",
    AG3: "appr97-agent3@example.org",
    AG4: "appr97-agent4@example.org",
    AG5: "appr97-agent5@example.org",
}
FROM = "tiqora@example.org"


def _step(name: str, *users: int, **extra: Any) -> dict[str, Any]:
    return {"name": name, "approvers": [{"kind": "user", "ref": str(u)} for u in users], **extra}


def _defn(*steps: dict[str, Any]) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate({"steps": list(steps)})


TWO_ANY = _defn(_step("Prüfung appr97", AG3, AG4))
TWO_ALL = _defn(_step("Prüfung appr97", AG3, AG4, rule="all"))
ONE = _defn(_step("Prüfung appr97", AG3))


async def _set(session: AsyncSession, **values: Any) -> None:
    await save_section_values(session, APPROVALS_SECTION, values, secret_key=SETTINGS.secret_key)


async def _prepare(
    session: AsyncSession, *, sender: bool = True, languages: dict[int, str] | None = None
) -> None:
    """Addresses (and ``UserLanguage`` de unless given) for the agents, the sender."""
    languages = {**dict.fromkeys(EMAIL, "de"), **(languages or {})}
    for user_id, address in EMAIL.items():
        await session.execute(
            text(
                "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (:u, 'UserEmail', :v)"
            ),
            {"u": user_id, "v": address},
        )
    for user_id, language in languages.items():
        await session.execute(
            text(
                "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (:u, 'UserLanguage', :v)"
            ),
            {"u": user_id, "v": language},
        )
    if sender:
        await set_setting(session, KEY_NOTIFICATION_SENDER_EMAIL, FROM)
    await session.commit()


async def _request(
    session: AsyncSession,
    definition: ApprovalDefinition,
    *,
    subject_type: str = SUBJECT,
    title: str = "Freigabe appr97",
    description: str | None = None,
    approval_key: str = "default",
) -> int:
    info = await service.request_approval(
        session,
        ticket_id=T1,
        subject_type=subject_type,
        title=title,
        actor=REQUESTER,
        definition=definition,
        description=description,
        approval_key=approval_key,
        settings=SETTINGS,
    )
    await session.commit()
    return info.approval_id


async def _approver_ids(session: AsyncSession, approval_id: int) -> dict[str, int]:
    rows = await session.execute(
        select(TiqoraApprovalApprover.principal, TiqoraApprovalApprover.id)
        .where(TiqoraApprovalApprover.approval_id == approval_id)
        .order_by(TiqoraApprovalApprover.id)
    )
    return {str(p): int(i) for p, i in rows}


async def _run(
    session: AsyncSession,
    approval_id: int,
    kind: str,
    approver_ids: list[int] | tuple[int, ...] = (),
    *,
    include_requester: bool = False,
    include_substitutes: bool = True,
    job_id: int = 7001,
    attempt: int = 1,
    settings: Settings = SETTINGS,
) -> dict[str, Any]:
    ctx = JobContext(
        job_id=job_id,
        kind=JOB_MAIL,
        created_by=None,
        settings=settings,
        session_factory=shared_session_factory(session),  # type: ignore[arg-type]
        attempt=attempt,
        max_attempts=3,
    )
    payload = ApprovalMailPayload(
        approval_id=approval_id,
        kind=kind,  # type: ignore[arg-type]
        approver_ids=list(approver_ids),
        include_requester=include_requester,
        include_substitutes=include_substitutes,
    )
    return await run_approval_mail(ctx, payload) or {}


def _to(message: EmailMessage) -> str:
    return str(message["To"])


def _body(message: EmailMessage) -> str:
    return str(message.get_content())


def _by_to(sent: list[EmailMessage]) -> dict[str, EmailMessage]:
    return {_to(m): m for m in sent}


async def _notified(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalEvent]:
    rows = await session.execute(
        select(TiqoraApprovalEvent)
        .where(
            TiqoraApprovalEvent.approval_id == approval_id,
            TiqoraApprovalEvent.action == "notified",
        )
        .order_by(TiqoraApprovalEvent.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def _tokens(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalToken]:
    rows = await session.execute(
        select(TiqoraApprovalToken)
        .where(TiqoraApprovalToken.approval_id == approval_id)
        .order_by(TiqoraApprovalToken.id)
    )
    return list(rows.scalars())


class _Sender:
    """A mail sender that fails on chosen calls (counted over its whole life)."""

    last_smtp_code: int | None = None
    last_smtp_detail: str | None = None

    def __init__(self, *, fail_on: set[int], exc: Exception) -> None:
        self.fail_on, self.exc = fail_on, exc
        self.calls = 0
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage, *, envelope_from: str | None = None) -> None:
        self.calls += 1
        if self.calls in self.fail_on:
            raise self.exc
        self.sent.append(message)


def _install_sender(monkeypatch: pytest.MonkeyPatch, sender: _Sender) -> None:
    async def _build(session: Any, **kwargs: Any) -> _Sender:
        return sender

    monkeypatch.setattr(mail_outbound, "build_outbound_sender", _build)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_request_mail_per_approver(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, TWO_ANY, description="Bitte prüfen appr97.")
            ids = await _approver_ids(session, approval_id)
            result = await _run(session, approval_id, "request", list(ids.values()))

            assert (result["sent"], result["failed"]) == (2, 0)
            by_to = _by_to(mail.sent)
            assert set(by_to) == {EMAIL[AG3], EMAIL[AG4]}
            for message in mail.sent:
                assert message["From"] == FROM
                subject = str(message["Subject"])
                assert f"Ticket#{TN}" in subject
                assert subject.endswith("Freigabe erbeten: Freigabe appr97")
                body = _body(message)
                assert f"{BASE}/approve#t=" in body
                assert "Bitte prüfen appr97." in body
                assert "Es genügt eine Freigabe" in body
                assert "Appr appr97-agent1" in body  # who asks
                assert "Sie" in body
                # no ticket text, no step name
                assert "appr97 ticket" not in body and "Prüfung appr97" not in body
            tokens_for = await _tokens(session, approval_id)
            assert len(tokens_for) == 2
            for message in mail.sent:
                plaintext = re.search(r"#t=([\w-]+)", _body(message))
                assert plaintext is not None
                assert tokens.link_digest(plaintext.group(1)) in {t.token_hash for t in tokens_for}

            notified = await _notified(session, approval_id)
            assert sorted(e.approver_id for e in notified if e.approver_id) == sorted(ids.values())
            assert {e.job_id for e in notified} == {7001}
            assert all(json.loads(e.detail or "{}") == {"kind": "request"} for e in notified)
            for row in (
                (
                    await session.execute(
                        select(TiqoraApprovalApprover)
                        .where(TiqoraApprovalApprover.approval_id == approval_id)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            ):
                assert row.last_notified_at is not None
            logs = (
                (await session.execute(select(TiqoraMailLog).where(TiqoraMailLog.ticket_id == T1)))
                .scalars()
                .all()
            )
            assert sorted(log.to_addr for log in logs) == sorted([EMAIL[AG3], EMAIL[AG4]])
            assert {log.status for log in logs} == {"sent"}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_link_omitted_when_disabled(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            await _set(session, email_links_enabled=False)
            await session.commit()
            ids = await _approver_ids(session, approval_id)
            await _run(session, approval_id, "request", list(ids.values()))

            (message,) = mail.sent
            body = _body(message)
            assert "/approve#t=" not in body
            assert f"{BASE}/agent/approvals" in body
            assert "funktioniert einmal" not in body
            assert await _tokens(session, approval_id) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_step_allow_email_false_no_link(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, _defn(_step("Nur UI", AG3, allow_email=False)))
            ids = await _approver_ids(session, approval_id)
            await _run(session, approval_id, "request", list(ids.values()))

            (message,) = mail.sent
            assert "/approve#t=" not in _body(message)
            assert f"{BASE}/agent/approvals" in _body(message)
            assert await _tokens(session, approval_id) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_no_base_url_means_no_link_and_no_url(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            ids = await _approver_ids(session, approval_id)
            await _run(
                session,
                approval_id,
                "request",
                list(ids.values()),
                settings=Settings(environment="test", public_base_url=""),
            )
            (message,) = mail.sent
            body = _body(message)
            assert "http" not in body and "Meine Freigaben" in body
            assert await _tokens(session, approval_id) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_no_link_when_it_would_be_born_expired(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            await session.execute(
                text("UPDATE tiqora_approval_step SET due_at = :d WHERE approval_id = :a"),
                {"d": utc_now() - timedelta(hours=30), "a": approval_id},
            )
            await session.commit()
            ids = await _approver_ids(session, approval_id)
            await _run(session, approval_id, "request", list(ids.values()))

            (message,) = mail.sent
            assert "/approve#t=" not in _body(message)
            assert f"{BASE}/agent/approvals" in _body(message)
            assert await _tokens(session, approval_id) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_mail_points_to_portal_or_link(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    customer = {"kind": "customer_user", "ref": "appr97-k1"}
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type("test.cust", allow_customer_approvers=True)):
            await _set(session, customer_approvers_enabled=True)
            await session.commit()
            with_link = await _request(
                session, _defn({"name": "Kunde", "approvers": [customer]}), subject_type="test.cust"
            )
            portal_only = await _request(
                session,
                _defn({"name": "Kunde", "approvers": [customer], "allow_email": False}),
                subject_type="test.cust",
                title="Portal appr97",
                approval_key="portal",
            )
            for approval_id in (with_link, portal_only):
                ids = await _approver_ids(session, approval_id)
                await _run(session, approval_id, "request", list(ids.values()), job_id=approval_id)

            first, second = mail.sent
            assert _to(first) == _to(second) == CUSTOMER_1
            assert f"{BASE}/approve#t=" in _body(first)
            assert "/approve#t=" not in _body(second)
            assert f"{BASE}/portal/approvals" in _body(second)

            # the setting is off now: a customer approver gets no notification at all
            await _set(session, customer_approvers_enabled=False)
            await session.commit()
            ids = await _approver_ids(session, with_link)
            result = await _run(session, with_link, "reminder", list(ids.values()), job_id=99)
            assert result["sent"] == 0 and len(mail.sent) == 2


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_language_preference(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session, languages={AG4: "en"})
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, TWO_ANY)
            ids = await _approver_ids(session, approval_id)
            await _run(session, approval_id, "request", list(ids.values()))

            by_to = _by_to(mail.sent)
            assert str(by_to[EMAIL[AG3]]["Subject"]).endswith("Freigabe erbeten: Freigabe appr97")
            assert str(by_to[EMAIL[AG4]]["Subject"]).endswith("Approval requested: Freigabe appr97")
            assert "One approval is enough" in _body(by_to[EMAIL[AG4]])
            assert "Es genügt eine Freigabe" in _body(by_to[EMAIL[AG3]])


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_substitute_gets_copy(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            now = utc_now()
            await delegation.create_delegation(
                session,
                user_id=AG3,
                substitute_user_id=AG5,
                valid_from=now - timedelta(hours=1),
                valid_until=now + timedelta(days=1),
                created_by=AG1,
            )
            await session.commit()
            approval_id = await _request(session, ONE)
            ids = await _approver_ids(session, approval_id)
            result = await _run(session, approval_id, "request", list(ids.values()))

            assert result["sent"] == 2
            by_to = _by_to(mail.sent)
            assert "in Vertretung für" not in _body(by_to[EMAIL[AG3]])
            assert "/approve#t=" in _body(by_to[EMAIL[AG3]])
            copy = _body(by_to[EMAIL[AG5]])
            assert "in Vertretung für Appr appr97-agent3" in copy
            # F4: a copy never carries a decision link; it points to the inbox
            assert "/approve#t=" not in copy and f"{BASE}/agent/approvals" in copy
            assert len(await _tokens(session, approval_id)) == 1

            notified = await _notified(session, approval_id)
            by_recipient = {
                (e.approver_id, (json.loads(e.detail or "{}")).get("recipient")) for e in notified
            }
            assert by_recipient == {
                (ids[f"agent:{AG3}"], None),
                (None, f"substitute:{AG5}"),
            }
            for event in notified:  # ids only: no address, no name in the audit trail
                assert "@" not in (event.detail or "") and "Appr" not in (event.detail or "")

            # a retry of the same job sends nothing again
            again = await _run(session, approval_id, "request", list(ids.values()))
            assert again["sent"] == 0 and len(mail.sent) == 2


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_substitute_who_is_requester_gets_no_copy(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            now = utc_now()
            await delegation.create_delegation(
                session,
                user_id=AG3,
                substitute_user_id=AG1,  # the requester
                valid_from=now - timedelta(hours=1),
                valid_until=now + timedelta(days=1),
                created_by=AG1,
            )
            await session.commit()
            approval_id = await _request(session, ONE)
            ids = await _approver_ids(session, approval_id)
            await _run(session, approval_id, "request", list(ids.values()))
            assert [_to(m) for m in mail.sent] == [EMAIL[AG3]]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_retry_does_not_resend(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _Sender(fail_on={2}, exc=OSError("connection reset"))
    _install_sender(monkeypatch, sender)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, TWO_ANY)
            ids = await _approver_ids(session, approval_id)
            ordered = [ids[f"agent:{AG3}"], ids[f"agent:{AG4}"]]

            with pytest.raises(JobRetry) as excinfo:
                await _run(session, approval_id, "request", ordered)
            assert excinfo.value.delay_seconds == 120
            assert [_to(m) for m in sender.sent] == [EMAIL[AG3]]
            # the failed send's link was committed before SMTP and is revoked again
            live = [t for t in await _tokens(session, approval_id) if t.revoked_at is None]
            assert len(live) == 1

            result = await _run(session, approval_id, "request", ordered, attempt=2)
            assert result["sent"] == 1
            assert [_to(m) for m in sender.sent] == [EMAIL[AG3], EMAIL[AG4]]
            notified = await _notified(session, approval_id)
            assert sorted(e.approver_id for e in notified if e.approver_id) == sorted(ordered)
            assert len(await _tokens(session, approval_id)) == 3
            live = [t for t in await _tokens(session, approval_id) if t.revoked_at is None]
            assert sorted(t.approver_id for t in live) == sorted(ordered)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_skips_decided_approver(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, TWO_ALL)
            ids = await _approver_ids(session, approval_id)
            await service.decide(
                session,
                approval_id,
                actor=Actor.agent(AG3),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            await session.commit()
            result = await _run(session, approval_id, "request", list(ids.values()))

            assert result["sent"] == 1
            assert [_to(m) for m in mail.sent] == [EMAIL[AG4]]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_closed_request_sends_no_request_mail(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            ids = await _approver_ids(session, approval_id)
            await service.cancel_approval(session, approval_id, actor=REQUESTER)
            await session.commit()
            result = await _run(session, approval_id, "request", list(ids.values()))
            assert result["sent"] == 0 and mail.sent == []
            assert await _tokens(session, approval_id) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_no_sender_configured(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session, sender=False)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            ids = await _approver_ids(session, approval_id)
            result = await _run(session, approval_id, "request", list(ids.values()))

            assert result["sent"] == 0 and result["note"] == "no_sender"
            assert mail.sent == []
            assert await _notified(session, approval_id) == []
            assert await _tokens(session, approval_id) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decided_mail_to_requester(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            await service.decide(
                session,
                approval_id,
                actor=Actor.agent(AG3),
                decision="rejected",
                channel="ui",
                comment="Nein, wegen appr97.",
                settings=SETTINGS,
            )
            await session.commit()
            result = await _run(session, approval_id, "decided", include_requester=True)

            assert result["sent"] == 1
            (message,) = mail.sent
            assert _to(message) == EMAIL[AG1]
            assert str(message["Subject"]).endswith("Freigabe abgelehnt: Freigabe appr97")
            body = _body(message)
            assert "/approve#t=" not in body
            assert "Nein, wegen appr97." not in body  # the vote's comment stays in the engine
            (event,) = await _notified(session, approval_id)
            assert event.approver_id is None
            assert json.loads(event.detail or "{}") == {"kind": "decided", "recipient": "requester"}
            assert event.job_id == 7001


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_cancelled_mail_survives_deleted_ticket(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            ids = list((await _approver_ids(session, approval_id)).values())
            await _run(session, approval_id, "request", ids, job_id=1)  # notified once
            await service.cancel_approval(
                session, approval_id, actor=Actor.system(), reason="ticket_deleted"
            )
            await session.execute(
                text("DELETE FROM ticket_history WHERE ticket_id = :t"), {"t": T1}
            )
            await session.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": T1})
            await session.commit()

            result = await _run(session, approval_id, "cancelled", ids, job_id=2)

            assert result["sent"] == 1 and result["failed"] == 0
            message = mail.sent[-1]
            assert str(message["Subject"]) == "Freigabe zurückgezogen: Freigabe appr97"
            assert "/approve#t=" not in _body(message)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_permanent_smtp_failure_recorded(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _Sender(fail_on={1}, exc=aiosmtplib.SMTPResponseException(550, "no such user"))
    _install_sender(monkeypatch, sender)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            ids = await _approver_ids(session, approval_id)
            result = await _run(session, approval_id, "request", list(ids.values()))

            assert (result["sent"], result["failed"]) == (0, 1)
            (event,) = await _notified(session, approval_id)
            assert json.loads(event.detail or "{}") == {"kind": "request", "error": "permanent"}
            approver = (
                await session.execute(
                    select(TiqoraApprovalApprover)
                    .where(TiqoraApprovalApprover.approval_id == approval_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            assert approver.last_notified_at is None
            (dead,) = await _tokens(session, approval_id)  # issued before SMTP, revoked after
            assert dead.revoked_at is not None
            log = (
                await session.execute(select(TiqoraMailLog).where(TiqoraMailLog.ticket_id == T1))
            ).scalar_one()
            assert log.status == "failed"

            again = await _run(session, approval_id, "request", list(ids.values()))  # no retry loop
            assert again["sent"] == 0 and sender.calls == 1


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_temporary_failure_on_last_attempt_is_recorded(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _Sender(fail_on={1}, exc=OSError("timeout"))
    _install_sender(monkeypatch, sender)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            ids = await _approver_ids(session, approval_id)
            result = await _run(session, approval_id, "request", list(ids.values()), attempt=3)
            assert (result["sent"], result["failed"]) == (0, 1)
            (event,) = await _notified(session, approval_id)
            assert json.loads(event.detail or "{}") == {"kind": "request", "error": "temporary"}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_reminder_and_forwarded_texts(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            await session.execute(
                text("UPDATE tiqora_approval_step SET activated_at = :a WHERE approval_id = :i"),
                {"a": utc_now() - timedelta(days=3, hours=2), "i": approval_id},
            )
            await session.commit()
            ids = await _approver_ids(session, approval_id)
            await _run(session, approval_id, "reminder", list(ids.values()), job_id=11)
            assert str(mail.sent[-1]["Subject"]).endswith(
                "Erinnerung: Freigabe wartet seit 3 Tagen"
            )
            assert "/approve#t=" in _body(mail.sent[-1])

            new_id = await service.forward(
                session, ids[f"agent:{AG3}"], actor=Actor.agent(AG3), to_user_id=AG4
            )
            await session.commit()
            await _run(session, approval_id, "forwarded", [new_id], job_id=12)
            message = mail.sent[-1]
            assert _to(message) == EMAIL[AG4]
            assert str(message["Subject"]).endswith(
                "Freigabe an Sie weitergeleitet von Appr appr97-agent3"
            )
            assert "/approve#t=" in _body(message)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_enqueue_notification_returns_job_id(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            assert (
                await enqueue_notification(session, approval_id=approval_id, kind="request") is None
            )
            job_id = await enqueue_notification(
                session, approval_id=approval_id, kind="request", approver_ids=[5, 6]
            )
            assert job_id is not None
            job = (
                await session.execute(select(TiqoraJob).where(TiqoraJob.id == job_id))
            ).scalar_one()
            assert job.kind == JOB_MAIL and job.created_by is None
            assert json.loads(job.payload)["approver_ids"] == [5, 6]


def test_job_handler_is_registered_on_the_mail_lane() -> None:
    registry = Registry()
    register_core(registry)
    handler = registry.get(JOB_HANDLERS, JOB_MAIL)
    assert handler is APPROVAL_MAIL_JOB
    assert (handler.kind, handler.lane, handler.max_attempts) == (
        "core.approval_mail",
        "mail",
        3,
    )
    assert handler.title_key == "jobs.kinds.core.approval_mail"
    assert handler.requeue_if_interrupted is True
    assert handler.payload_model is ApprovalMailPayload


def _default_language(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    async def _value(self: SysConfig) -> str:
        return value

    monkeypatch.setattr(SysConfig, "default_language", _value)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
@pytest.mark.parametrize(
    ("default", "expected"),
    [("en", "Approval requested: "), ("de", "Freigabe erbeten: "), ("fr", "Freigabe erbeten: ")],
)
async def test_missing_language_uses_default_language(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    default: str,
    expected: str,
) -> None:
    mail = CapturingMail(monkeypatch)
    _default_language(monkeypatch, default)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session, languages={AG4: ""})  # AG4 has no usable preference
        await session.execute(
            text(
                "DELETE FROM user_preferences"
                " WHERE user_id = :u AND preferences_key = 'UserLanguage'"
            ),
            {"u": AG4},
        )
        await session.commit()
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, TWO_ANY)
            ids = await _approver_ids(session, approval_id)
            await _run(session, approval_id, "request", list(ids.values()))
            by_to = _by_to(mail.sent)
            # AG3 has a preference (de) and keeps it; AG4 follows the site default
            assert str(by_to[EMAIL[AG3]]["Subject"]).endswith("Freigabe erbeten: Freigabe appr97")
            assert f"{expected}Freigabe appr97" in str(by_to[EMAIL[AG4]]["Subject"])


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_substitute_copy_has_no_link(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            now = utc_now()
            await delegation.create_delegation(
                session,
                user_id=AG3,
                substitute_user_id=AG5,
                valid_from=now - timedelta(hours=1),
                valid_until=now + timedelta(days=1),
                created_by=AG1,
            )
            await session.commit()
            approval_id = await _request(session, ONE)
            ids = await _approver_ids(session, approval_id)
            for job_id, kind in enumerate(("request", "reminder", "escalation"), start=1):
                await _run(session, approval_id, kind, list(ids.values()), job_id=job_id)
            copies = [m for m in mail.sent if _to(m) == EMAIL[AG5]]
            assert len(copies) == 3
            for message in copies:
                assert "/approve#t=" not in _body(message)
            # one live link per approver mail, none for the substitute: tokens bind the approver row
            assert {t.approver_id for t in await _tokens(session, approval_id)} == {
                ids[f"agent:{AG3}"]
            }


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_without_portal_gets_no_inbox_line(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    customer = {"kind": "customer_user", "ref": "appr97-k1"}
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type("test.cust", allow_customer_approvers=True)):
            await _set(session, customer_approvers_enabled=True, portal_approvals_enabled=False)
            await session.commit()
            approval_id = await _request(
                session,
                _defn({"name": "Kunde", "approvers": [customer], "allow_email": False}),
                subject_type="test.cust",
            )
            ids = await _approver_ids(session, approval_id)
            await _run(session, approval_id, "request", list(ids.values()))
            (message,) = mail.sent
            body = _body(message)
            assert "Meine Freigaben" not in body and "/portal/approvals" not in body
            assert "/approve#t=" not in body


async def _close_by_timeout(session: AsyncSession, approval_id: int) -> None:
    """What the sweeper leaves behind: step and request rejected by timeout, rows not_needed."""
    now = utc_now().replace(microsecond=0)
    await session.execute(
        text(
            "UPDATE tiqora_approval_step SET status = 'rejected', reason = 'timeout',"
            " decided_at = :n WHERE approval_id = :a"
        ),
        {"n": now, "a": approval_id},
    )
    await session.execute(
        text("UPDATE tiqora_approval_approver SET status = 'not_needed' WHERE approval_id = :a"),
        {"a": approval_id},
    )
    await session.execute(
        text(
            "UPDATE tiqora_approval SET status = 'rejected', reason = 'timeout', decided_at = :n,"
            " current_stage = NULL WHERE id = :a"
        ),
        {"n": now, "a": approval_id},
    )
    await session.commit()


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_overdue_notice_survives_step_closed_by_timeout(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            ids = await _approver_ids(session, approval_id)
            await _close_by_timeout(session, approval_id)

            result = await _run(
                session, approval_id, "overdue", list(ids.values()), include_requester=True
            )

            assert (result["sent"], result["skipped"]) == (2, 0)
            by_to = _by_to(mail.sent)
            assert set(by_to) == {EMAIL[AG1], EMAIL[AG3]}
            for message in mail.sent:
                assert str(message["Subject"]).endswith("Frist überschritten: Freigabe appr97")
                assert "/approve#t=" not in _body(message)
            assert await _tokens(session, approval_id) == []

            # a plain request mail for the same closed request is still skipped
            again = await _run(session, approval_id, "request", list(ids.values()), job_id=2)
            assert again["sent"] == 0


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_escalation_mail_with_and_without_open_step(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        await _prepare(session)
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session, ONE)
            ids = await _approver_ids(session, approval_id)

            await _run(session, approval_id, "escalation", list(ids.values()), job_id=1)
            (first,) = mail.sent
            assert str(first["Subject"]).endswith(
                "Eskalation: Sie können über diese Freigabe jetzt allein entscheiden"
            )
            assert "/approve#t=" in _body(first)
            assert len(await _tokens(session, approval_id)) == 1

            await _close_by_timeout(session, approval_id)
            result = await _run(session, approval_id, "escalation", list(ids.values()), job_id=2)
            assert result["sent"] == 1
            assert "/approve#t=" not in _body(mail.sent[-1])
            assert f"{BASE}/agent/approvals" in _body(mail.sent[-1])
