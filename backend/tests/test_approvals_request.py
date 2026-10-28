"""Approval requests: activation, supersede, auto decision, cancel, audit/outbox/history (A7).

Own rows 970850-970999 (the helper world supplies the ids; the requests themselves are
autoincrement rows inside the rolled-back ``approvals_session``). The one test that needs real
commits (a second connection racing) cleans up after itself.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._approvals_helpers import (
    AG1,
    AG2,
    AG3,
    AG4,
    AG5,
    AG6,
    GROUP_CAB,
    T1,
    T2,
    approvals_session,
    delete_world,
    ensure_tiqora_tables,
    installed_registry,
    make_fake_subject_type,
    seed_world,
    to_async_url,
)
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.approvals import _engine, access, audit, service
from tiqora.approvals.contract import (
    EVENT_CANCELLED,
    EVENT_DECIDED,
    EVENT_REQUESTED,
    JOB_HOOK,
    JOB_MAIL,
    Actor,
    ApprovalDefinition,
    ApprovalError,
    Principal,
)
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalEvent,
    TiqoraApprovalStep,
    TiqoraApprovalToken,
)
from tiqora.approvals.settings import APPROVALS_SECTION, ApprovalSettings
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.history_render import render_history_entry
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.settings_sections.store import save_section_values

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
ACTOR = Actor.agent(AG1)


def _user(uid: int) -> dict[str, str]:
    return {"kind": "user", "ref": str(uid)}


def _defn(*steps: dict[str, Any]) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate({"steps": list(steps)})


def _step(name: str, *approvers: dict[str, str], **extra: Any) -> dict[str, Any]:
    return {"name": name, "approvers": list(approvers), **extra}


ONE_STEP = _defn(_step("Prüfung", _user(AG3)))
CAB_STEP = _defn(_step("CAB", {"kind": "group", "ref": str(GROUP_CAB)}, rule="all"))


async def _set(session: AsyncSession, **values: Any) -> None:
    await save_section_values(session, APPROVALS_SECTION, values, secret_key=SETTINGS.secret_key)


async def _request(session: AsyncSession, **overrides: Any) -> Any:
    args: dict[str, Any] = {
        "ticket_id": T1,
        "subject_type": SUBJECT,
        "title": "Freigabe appr97",
        "actor": ACTOR,
        "definition": ONE_STEP,
        "settings": SETTINGS,
    }
    args.update(overrides)
    return await service.request_approval(session, **args)


async def _row(session: AsyncSession, approval_id: int) -> TiqoraApproval:
    row = (
        await session.execute(
            select(TiqoraApproval)
            .where(TiqoraApproval.id == approval_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return row


async def _steps(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalStep]:
    rows = await session.execute(
        select(TiqoraApprovalStep)
        .where(TiqoraApprovalStep.approval_id == approval_id)
        .order_by(TiqoraApprovalStep.position)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def _approvers(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalApprover]:
    rows = await session.execute(
        select(TiqoraApprovalApprover)
        .where(TiqoraApprovalApprover.approval_id == approval_id)
        .order_by(TiqoraApprovalApprover.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def _events(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalEvent]:
    rows = await session.execute(
        select(TiqoraApprovalEvent)
        .where(TiqoraApprovalEvent.approval_id == approval_id)
        .order_by(TiqoraApprovalEvent.id)
    )
    return list(rows.scalars())


def _actions(events: list[TiqoraApprovalEvent]) -> list[str]:
    return [e.action for e in events]


async def _outbox(session: AsyncSession, ticket_id: int = T1) -> list[tuple[str, dict[str, Any]]]:
    rows = await session.execute(
        text(
            "SELECT event_type, payload FROM tiqora_event_outbox WHERE ticket_id = :t ORDER BY id"
        ),
        {"t": ticket_id},
    )
    return [(str(r[0]), json.loads(r[1])) for r in rows]


async def _jobs(session: AsyncSession, kind: str, approval_id: int) -> list[TiqoraJob]:
    rows = await session.execute(
        select(TiqoraJob).where(TiqoraJob.kind == kind).order_by(TiqoraJob.id)
    )
    return [j for j in rows.scalars() if json.loads(j.payload).get("approval_id") == approval_id]


async def _history(session: AsyncSession, ticket_id: int = T1) -> list[tuple[str, str]]:
    rows = await session.execute(
        text(
            "SELECT tht.name, th.name FROM ticket_history th"
            " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
            " WHERE th.ticket_id = :t AND th.name LIKE 'Approval #%' ORDER BY th.id"
        ),
        {"t": ticket_id},
    )
    return [(str(r[0]), str(r[1])) for r in rows]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_request_with_definition_activates_stage_one(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    definition = _defn(
        _step("Prüfung", _user(AG3)),
        _step("Freigabe", _user(AG4), _user(AG5), stage=2, rule="all"),
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(session, definition=definition, approval_key="default")
            assert (info.status, info.current_stage, info.reason) == ("pending", 1, None)
            steps = await _steps(session, info.approval_id)
            assert [(s.position, s.stage, s.status) for s in steps] == [
                (1, 1, "active"),
                (2, 2, "waiting"),
            ]
            assert json.loads(steps[0].spec)["name"] == "Prüfung"
            approvers = await _approvers(session, info.approval_id)
            assert [(a.step_id, a.principal, a.user_id, a.status) for a in approvers] == [
                (steps[0].id, f"agent:{AG3}", AG3, "pending")
            ]
            events = await _events(session, info.approval_id)
            assert _actions(events) == ["requested", "step_activated", "approver_added"]
            assert events[2].approver_id == approvers[0].id
            outbox = await _outbox(session)
            assert [name for name, _ in outbox] == [EVENT_REQUESTED]
            assert outbox[0][1] == {
                "approval_id": info.approval_id,
                "approval_key": "default",
                "subject_type": SUBJECT,
                "subject_ref": None,
                "status": "pending",
            }
            assert await _history(session) == [
                ("Misc", f"Approval #{info.approval_id} (default): requested")
            ]
            mails = await _jobs(session, JOB_MAIL, info.approval_id)
            assert len(mails) == 1
            assert json.loads(mails[0].payload) == {
                "approval_id": info.approval_id,
                "kind": "request",
                "approver_ids": [approvers[0].id],
                "include_requester": False,
                "include_substitutes": True,
            }
            assert mails[0].created_by is None
            assert await _jobs(session, JOB_HOOK, info.approval_id) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_history_line_renders_as_plain_text(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(session)
            (_, name), *_ = await _history(session)
            assert render_history_entry(history_type="Misc", name=name) == (
                f"Approval #{info.approval_id} (default): requested"
            )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_request_with_template_key(url_fixture: str, request: pytest.FixtureRequest) -> None:
    from tiqora.approvals import templates

    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            template = await templates.ensure_template(
                session,
                key="appr97.req",
                name="Request template",
                definition=ONE_STEP,
                subject_types=[SUBJECT],
                origin="test",
            )
            info = await _request(session, definition=None, template_key="appr97.req")
            assert (await _row(session, info.approval_id)).template_id == template.id
            assert [s.name for s in await _steps(session, info.approval_id)] == ["Prüfung"]
            with pytest.raises(ApprovalError) as excinfo:
                await _request(session, definition=None, template_key="appr97.nope")
            assert (excinfo.value.code, excinfo.value.http_status) == ("template_not_found", 404)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_template_must_fit_subject_type(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    from tiqora.approvals import templates

    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(), make_fake_subject_type("test.other")):
            await templates.ensure_template(
                session,
                key="appr97.only",
                name="Only for test.subject",
                definition=ONE_STEP,
                subject_types=[SUBJECT],
                origin="test",
            )
            with pytest.raises(ApprovalError) as excinfo:
                await _request(
                    session, subject_type="test.other", definition=None, template_key="appr97.only"
                )
            assert (excinfo.value.code, excinfo.value.http_status) == ("invalid_definition", 422)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_exactly_one_of_definition_or_template(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            for kwargs in ({"template_key": "appr97.req"}, {"definition": None}):
                with pytest.raises(ApprovalError) as excinfo:
                    await _request(session, **kwargs)
                assert (excinfo.value.code, excinfo.value.http_status) == (
                    "invalid_definition",
                    422,
                )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_unknown_subject_type(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(), pytest.raises(ApprovalError) as excinfo:
            await _request(session, subject_type="nope.nope")
        assert (excinfo.value.code, excinfo.value.http_status) == ("unknown_subject_type", 422)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_missing_ticket(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()), pytest.raises(ApprovalError) as excinfo:
            await _request(session, ticket_id=970999)
        assert (excinfo.value.code, excinfo.value.http_status) == ("ticket_not_found", 404)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_entries_need_subject_permission(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    customer = _defn(_step("Kunde", {"kind": "customer_user", "ref": "appr97-k1"}))
    resolver = _defn(_step("Kunde", {"kind": "resolver", "ref": "ticket.customer_user"}))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(
            make_fake_subject_type(), make_fake_subject_type("test.cust", True)
        ):
            await _set(session, customer_approvers_enabled=True)
            for defn in (customer, resolver):  # subject without allow_customer_approvers
                with pytest.raises(ApprovalError) as excinfo:
                    await _request(session, definition=defn)
                assert (excinfo.value.code, excinfo.value.http_status) == (
                    "customer_approvers_not_allowed",
                    422,
                )
            await _set(session, customer_approvers_enabled=False)
            for defn in (customer, resolver):  # subject allows, the setting does not
                with pytest.raises(ApprovalError) as excinfo:
                    await _request(session, subject_type="test.cust", definition=defn)
                assert excinfo.value.code == "customer_approvers_not_allowed"
            await _set(session, customer_approvers_enabled=True)
            info = await _request(session, subject_type="test.cust", definition=customer)
            assert [a.principal for a in await _approvers(session, info.approval_id)] == [
                "customer:appr97-k1"
            ]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_setting_off_excludes_at_activation(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The setting can be switched off between request and activation of a later stage."""
    defn = _defn(
        _step("Prüfung", _user(AG3)),
        _step("Kunde", {"kind": "customer_user", "ref": "appr97-k1"}, stage=2),
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)) as reg:
            await _set(session, customer_approvers_enabled=True)
            info = await _request(session, definition=defn)
            first = (await _steps(session, info.approval_id))[0]
            first.status = "approved"
            await session.flush()
            approval = await _row(session, info.approval_id)
            new_ids = await _engine.activate_next_stage(
                session, approval, settings=ApprovalSettings(), registry=reg
            )
            assert new_ids == []
            approval = await _row(session, info.approval_id)
            assert (approval.status, approval.reason) == ("rejected", "no_approvers")
            excluded = [
                json.loads(e.detail or "{}")
                for e in await _events(session, info.approval_id)
                if e.action == "approver_excluded"
            ]
            assert excluded == [{"kind": "customer", "reason": "customers_not_allowed"}]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_unresolved_entries_leave_an_audit_trace(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    defn = _defn(_step("CAB", {"kind": "group", "ref": "970998"}, _user(AG3)))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(session, definition=defn)
            excluded = [
                json.loads(e.detail or "{}")
                for e in await _events(session, info.approval_id)
                if e.action == "approver_excluded"
            ]
            assert excluded == [{"kind": "group", "ref": "970998", "reason": "unknown"}]
            assert [a.principal for a in await _approvers(session, info.approval_id)] == [
                f"agent:{AG3}"
            ]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_requester_and_creator_excluded_unless_allowed(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(
                session,
                definition=CAB_STEP,
                actor=Actor.agent(AG3),
                requester=Principal.agent(AG4),
                approval_key="strict",
            )
            assert [a.principal for a in await _approvers(session, info.approval_id)] == [
                f"agent:{AG5}"
            ]
            reasons = {
                json.loads(e.detail or "{}").get("reason")
                for e in await _events(session, info.approval_id)
                if e.action == "approver_excluded"
            }
            assert reasons == {"self"}
            info = await _request(
                session,
                definition=CAB_STEP,
                actor=Actor.agent(AG3),
                requester=Principal.agent(AG4),
                allow_self_approval=True,
                approval_key="lax",
            )
            assert sorted(a.principal for a in await _approvers(session, info.approval_id)) == [
                f"agent:{AG3}",
                f"agent:{AG4}",
                f"agent:{AG5}",
            ]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_requester_is_compared_by_stored_login(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    defn = _defn(_step("Kunde", {"kind": "customer_user", "ref": "appr97-k1"}, _user(AG3)))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _set(session, customer_approvers_enabled=True)
            info = await _request(
                session, definition=defn, requester=Principal.customer("APPR97-K1")
            )
            assert (await _row(session, info.approval_id)).requester == "customer:appr97-k1"
            assert [a.principal for a in await _approvers(session, info.approval_id)] == [
                f"agent:{AG3}"
            ]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_no_approver_rejects_by_default(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(session, definition=_defn(_step("Prüfung", _user(AG6))))
            assert (info.status, info.reason, info.current_stage) == (
                "rejected",
                "no_approvers",
                None,
            )
            (step,) = await _steps(session, info.approval_id)
            assert (step.status, step.reason) == ("rejected", "no_approvers")
            assert await _approvers(session, info.approval_id) == []
            hooks = await _jobs(session, JOB_HOOK, info.approval_id)
            assert [json.loads(j.payload) for j in hooks] == [
                {"approval_id": info.approval_id, "status": "rejected"}
            ]
            assert hooks[0].dedupe_key == f"approval:{info.approval_id}:rejected"
            assert hooks[0].created_by is None
            names = [n for n, _ in await _outbox(session)]
            assert names == [EVENT_REQUESTED, EVENT_DECIDED]
            actions = _actions(await _events(session, info.approval_id))
            assert actions == [
                "requested",
                "step_activated",
                "approver_excluded",
                "step_decided",
                "decided",
            ]
            # the requester (an agent) is told about the result, no approver is mailed
            mails = [
                json.loads(j.payload) for j in await _jobs(session, JOB_MAIL, info.approval_id)
            ]
            assert mails == [
                {
                    "approval_id": info.approval_id,
                    "kind": "decided",
                    "approver_ids": [],
                    "include_requester": True,
                    "include_substitutes": True,
                }
            ]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_no_approver_approve_when_configured(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    defn = _defn(
        _step("Prüfung", _user(AG6), on_no_approver="approve"),
        _step("Freigabe", _user(AG3), stage=2),
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(session, definition=defn)
            assert (info.status, info.current_stage) == ("pending", 2)
            first, second = await _steps(session, info.approval_id)
            assert (first.status, first.reason) == ("approved", "no_approvers")
            assert second.status == "active"
            assert [a.principal for a in await _approvers(session, info.approval_id)] == [
                f"agent:{AG3}"
            ]
            # a request whose every step is waved through is approved at once
            only = await _request(
                session,
                definition=_defn(_step("Prüfung", _user(AG6), on_no_approver="approve")),
                approval_key="auto",
            )
            assert (only.status, only.reason) == ("approved", "no_approvers")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_quorum_unreachable_at_activation(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    # AG3 requests: the CAB group yields AG4 and AG5 (2 valid), quorum 3 cannot be reached
    defn = _defn(_step("CAB", {"kind": "group", "ref": str(GROUP_CAB)}, rule="quorum", quorum=3))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(session, definition=defn, actor=Actor.agent(AG3))
            assert (info.status, info.reason) == ("rejected", "no_approvers")
            (step,) = await _steps(session, info.approval_id)
            assert (step.status, step.reason) == ("rejected", "no_approvers")
            assert await _approvers(session, info.approval_id) == []
            detail = [
                json.loads(e.detail or "{}")
                for e in await _events(session, info.approval_id)
                if e.action == "step_decided"
            ]
            assert detail == [{"decision": "rejected", "reason": "no_approvers", "valid": 2}]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_due_at_from_step_or_default(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    cases: list[tuple[int | None, int, int | None]] = [
        (4, 0, 4),
        (None, 0, None),
        (None, 48, 48),
        (0, 48, None),
    ]
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            for n, (due_hours, default, expected) in enumerate(cases):
                await _set(session, default_due_hours=default)
                defn = _defn(_step("Prüfung", _user(AG3), due_hours=due_hours))
                info = await _request(session, definition=defn, approval_key=f"due{n}")
                (step,) = await _steps(session, info.approval_id)
                assert step.activated_at is not None
                if expected is None:
                    assert step.due_at is None, (due_hours, default)
                else:
                    assert step.due_at == step.activated_at + timedelta(hours=expected)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_supersede_cancels_pending_same_key(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            first = await _request(session)
            (approver,) = await _approvers(session, first.approval_id)
            now = utc_now()
            session.add(
                TiqoraApprovalToken(
                    approval_id=first.approval_id,
                    approver_id=approver.id,
                    ticket_id=T1,
                    token_hash="a" * 64,
                    created_at=now,
                    expires_at=now + timedelta(days=1),
                )
            )
            await session.flush()
            second = await _request(session)
            old = await _row(session, first.approval_id)
            assert (old.status, old.reason, old.superseded_by) == (
                "cancelled",
                "superseded",
                second.approval_id,
            )
            assert [s.status for s in await _steps(session, first.approval_id)] == ["cancelled"]
            assert [a.status for a in await _approvers(session, first.approval_id)] == [
                "not_needed"
            ]
            token = (await session.execute(select(TiqoraApprovalToken))).scalars().one()
            assert token.revoked_at is not None and token.used_at is None
            assert "superseded" in _actions(await _events(session, first.approval_id))
            assert (await _row(session, second.approval_id)).status == "pending"
            hooks = await _jobs(session, JOB_HOOK, first.approval_id)
            assert [json.loads(j.payload)["status"] for j in hooks] == ["cancelled"]
            # a different key is untouched
            other = await _request(session, approval_key="other")
            assert (await _row(session, second.approval_id)).status == "pending"
            assert (await _row(session, other.approval_id)).status == "pending"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_supersede_false_keeps_both(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            first = await _request(session)
            second = await _request(session, supersede=False)
            assert (await _row(session, first.approval_id)).status == "pending"
            assert (await _row(session, second.approval_id)).status == "pending"
            assert (await _row(session, first.approval_id)).superseded_by is None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_approval_status_newest_per_key(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            assert (
                await service.approval_status(session, ticket_id=T1, approval_key="default") is None
            )
            first = await _request(session)
            assert (
                await service.approval_status(session, ticket_id=T1, approval_key="default")
                == "pending"
            )
            await service.cancel_approval(
                session, first.approval_id, actor=ACTOR, settings=SETTINGS
            )
            assert (
                await service.approval_status(session, ticket_id=T1, approval_key="default")
                == "cancelled"
            )
            second = await _request(session, supersede=False)
            await service.record_auto_decision(
                session,
                ticket_id=T1,
                subject_type=SUBJECT,
                title="Vorab",
                actor=ACTOR,
                status="approved",
                supersede=False,
                settings=SETTINGS,
            )
            # the newest request (highest id) wins, not the pending one
            assert (
                await service.approval_status(session, ticket_id=T1, approval_key="default")
                == "approved"
            )
            assert second.approval_id > first.approval_id


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_approval_statuses_map(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            assert await service.approval_statuses(session, ticket_id=T1) == {}
            await _request(session)
            cab = await _request(session, approval_key="cab")
            await service.cancel_approval(session, cab.approval_id, actor=ACTOR, settings=SETTINGS)
            newer = await _request(session, approval_key="cab")
            assert await service.approval_statuses(session, ticket_id=T1) == {
                "default": "pending",
                "cab": "pending",
            }
            listing = await service.list_for_ticket(session, T1)
            assert [i.approval_id for i in listing] == sorted(
                (i.approval_id for i in listing), reverse=True
            )
            assert listing[0].approval_id == newer.approval_id
            assert len(listing) == 3
            assert await service.list_for_ticket(session, T2) == []
            with pytest.raises(ApprovalError) as excinfo:
                await service.get_info(session, 970999)
            assert (excinfo.value.code, excinfo.value.http_status) == ("approval_not_found", 404)
            got = await service.get_info(session, newer.approval_id)
            # MariaDB DATETIME drops the microseconds the in-memory value still has
            assert replace(got, created_at=newer.created_at) == newer


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_record_auto_decision(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await service.record_auto_decision(
                session,
                ticket_id=T1,
                subject_type=SUBJECT,
                title="Standardänderung",
                actor=Actor.system(),
                status="approved",
                subject_ref="chg-1",
                settings=SETTINGS,
            )
            assert (info.status, info.reason, info.current_stage) == ("approved", "auto", None)
            assert info.decided_at is not None and info.subject_ref == "chg-1"
            assert await _steps(session, info.approval_id) == []
            events = await _events(session, info.approval_id)
            assert _actions(events) == ["auto_decided"]
            assert events[0].actor == "system"
            assert [n for n, _ in await _outbox(session)] == [EVENT_DECIDED]
            hooks = await _jobs(session, JOB_HOOK, info.approval_id)
            assert [json.loads(j.payload) for j in hooks] == [
                {"approval_id": info.approval_id, "status": "approved"}
            ]
            assert await _jobs(session, JOB_MAIL, info.approval_id) == []
            assert await _history(session) == [
                ("Misc", f"Approval #{info.approval_id}: decided approved (auto)")
            ]
            with pytest.raises(ApprovalError) as excinfo:
                await service.record_auto_decision(
                    session,
                    ticket_id=T1,
                    subject_type=SUBJECT,
                    title="x",
                    actor=Actor.system(),
                    status="cancelled",  # type: ignore[arg-type]
                    settings=SETTINGS,
                )
            assert excinfo.value.code == "invalid_definition"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_cancel_by_requester(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(session, definition=CAB_STEP, actor=Actor.agent(AG3))
            assert len(await _approvers(session, info.approval_id)) == 2
            await session.execute(
                text(
                    "UPDATE tiqora_approval_approver SET last_notified_at = :t"
                    " WHERE approval_id = :a"
                ),
                {"t": utc_now(), "a": info.approval_id},
            )
            done = await service.cancel_approval(
                session, info.approval_id, actor=Actor.agent(AG3), settings=SETTINGS
            )
            assert (done.status, done.reason, done.current_stage) == (
                "cancelled",
                "cancelled",
                None,
            )
            assert done.decided_at is not None
            assert [s.status for s in await _steps(session, info.approval_id)] == ["cancelled"]
            assert {a.status for a in await _approvers(session, info.approval_id)} == {"not_needed"}
            assert _actions(await _events(session, info.approval_id))[-1] == "cancelled"
            assert [n for n, _ in await _outbox(session)] == [EVENT_REQUESTED, EVENT_CANCELLED]
            hooks = await _jobs(session, JOB_HOOK, info.approval_id)
            assert [json.loads(j.payload)["status"] for j in hooks] == ["cancelled"]
            kinds = [
                json.loads(j.payload) for j in await _jobs(session, JOB_MAIL, info.approval_id)
            ]
            assert [k["kind"] for k in kinds] == ["request", "cancelled"]
            assert len(kinds[1]["approver_ids"]) == 2 and kinds[1]["include_requester"] is False
            assert [h[1] for h in await _history(session)][-1] == (
                f"Approval #{info.approval_id}: cancelled"
            )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_cancel_closed_refused(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(session)
            await service.cancel_approval(session, info.approval_id, actor=ACTOR, settings=SETTINGS)
            with pytest.raises(ApprovalError) as excinfo:
                await service.cancel_approval(
                    session, info.approval_id, actor=ACTOR, settings=SETTINGS
                )
            assert (excinfo.value.code, excinfo.value.http_status) == ("approval_closed", 409)
            with pytest.raises(ApprovalError) as missing:
                await service.cancel_approval(session, 970999, actor=ACTOR, settings=SETTINGS)
            assert missing.value.code == "approval_not_found"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_cancel_permission(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(
                session, actor=Actor.agent(AG3), approval_key="p", definition=CAB_STEP
            )
            for actor in (Actor.agent(AG2), Actor.customer("appr97-k1")):
                with pytest.raises(ApprovalError) as excinfo:
                    await service.cancel_approval(
                        session, info.approval_id, actor=actor, settings=SETTINGS
                    )
                assert (excinfo.value.code, excinfo.value.http_status) == ("forbidden", 403)
            assert (await _row(session, info.approval_id)).status == "pending"
            # AG1 holds rw on the ticket's queue group
            done = await service.cancel_approval(
                session, info.approval_id, actor=Actor.agent(AG1), settings=SETTINGS
            )
            assert done.status == "cancelled"
            # a consumer cancels its own request without a permission check
            other = await _request(
                session, actor=Actor.agent(AG3), approval_key="q", definition=CAB_STEP
            )
            done = await service.cancel_approval(
                session,
                other.approval_id,
                actor=Actor.agent(AG2),
                check_permission=False,
                settings=SETTINGS,
            )
            assert done.status == "cancelled"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_outbox_payload_has_no_pii(url_fixture: str, request: pytest.FixtureRequest) -> None:
    allowed = {
        "approval_id",
        "approval_key",
        "subject_type",
        "subject_ref",
        "status",
        "step_id",
        "decision",
        "channel",
        "reason",
    }
    title = "Freigabe für kunde1@example.org Geheimtitel"
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _set(session, customer_approvers_enabled=True)
            defn = _defn(_step("Kunde", {"kind": "customer_user", "ref": "appr97-k1"}, _user(AG3)))
            first = await _request(
                session,
                title=title,
                description="Geheimer Text kunde1@example.org",
                definition=defn,
            )
            await service.cancel_approval(
                session, first.approval_id, actor=ACTOR, settings=SETTINGS
            )
            second = await _request(session, title=title, definition=defn, approval_key="b")
            auto = await service.record_auto_decision(
                session,
                ticket_id=T1,
                subject_type=SUBJECT,
                title=title,
                actor=ACTOR,
                status="rejected",
                approval_key="c",
                settings=SETTINGS,
            )
            rows = await _outbox(session)
            assert [n for n, _ in rows] == [
                EVENT_REQUESTED,
                EVENT_CANCELLED,
                EVENT_REQUESTED,
                EVENT_DECIDED,
            ]
            for _, payload in rows:
                assert set(payload) <= allowed
                blob = json.dumps(payload)
                assert "@" not in blob and "Geheim" not in blob
            for hist in await _history(session):
                assert "@" not in hist[1] and "Geheim" not in hist[1]
            for approval_id in (first.approval_id, second.approval_id, auto.approval_id):
                for event in await _events(session, approval_id):
                    assert "@" not in (event.detail or "") + event.actor
                    assert "Geheim" not in (event.detail or "")
            with pytest.raises(ValueError):
                await audit.emit(
                    session, await _row(session, first.approval_id), EVENT_DECIDED, title="x"
                )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_history_switch_off(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _set(session, history_entries=False)
            info = await _request(session)
            await service.cancel_approval(session, info.approval_id, actor=ACTOR, settings=SETTINGS)
            assert await _history(session) == []
            await _set(session, history_entries=True)
            await _request(session, approval_key="on")
            assert len(await _history(session)) == 1


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_ticket_deleted_cancel_skips_history_and_fanout(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await _request(session, ticket_id=T2)
            await session.execute(
                text("DELETE FROM ticket_history WHERE ticket_id = :t"), {"t": T2}
            )
            await session.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": T2})
            done = await service.cancel_approval(
                session,
                info.approval_id,
                actor=Actor.system(),
                reason="ticket_deleted",
                check_permission=False,
                settings=SETTINGS,
            )
            assert (done.status, done.reason) == ("cancelled", "ticket_deleted")
            assert [n for n, _ in await _outbox(session, T2)] == [EVENT_REQUESTED]
            assert await _history(session, T2) == []
            assert len(await _jobs(session, JOB_HOOK, info.approval_id)) == 1


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_service_never_commits(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):

            async def _boom() -> None:
                raise AssertionError("service committed")

            monkeypatch.setattr(session, "commit", _boom)
            info = await _request(session)
            await service.cancel_approval(session, info.approval_id, actor=ACTOR, settings=SETTINGS)
            await session.rollback()
            mine = await session.execute(
                text("SELECT COUNT(*) FROM tiqora_approval WHERE ticket_id = :t"), {"t": T1}
            )
            assert mine.scalar_one() == 0
            assert await _jobs(session, JOB_HOOK, info.approval_id) == []
            assert await _outbox(session) == []


_MARKS = (
    "tiqora_approval_event",
    "tiqora_approval_token",
    "tiqora_approval_vote",
    "tiqora_approval_approver",
    "tiqora_approval_step",
    "tiqora_approval",
    "tiqora_job",
    "tiqora_event_outbox",
    "ticket_history",
)


@asynccontextmanager
async def _committed_world(url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Committed helper world on a real engine; everything the test wrote is removed again."""
    ensure_tiqora_tables(url)
    marks = snapshot_marks(url, _MARKS)
    engine = create_async_engine(to_async_url(url), poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as seed:
            await seed_world(seed)
            await seed.commit()
        yield factory
    finally:
        delete_above_marks(url, marks)
        async with factory() as cleanup:
            await delete_world(cleanup)
            await cleanup.commit()
        await engine.dispose()


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_cancel_inside_consumer_transaction_sees_late_decision(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Session A read the request, session B decides and commits, A cancels: A sees B's result.

    On MariaDB (REPEATABLE READ) a plain read would still show ``pending`` and the cancel would
    overwrite the decision; the locking read returns the latest committed version. (Task 7 adds
    the vote-based variant ``test_cancel_inside_consumer_transaction_sees_late_vote``.)
    """
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            async with factory() as a:
                info = await _request(a)
                await a.commit()
                assert (await service.get_info(a, info.approval_id)).status == "pending"
                async with factory() as b:
                    await b.execute(
                        text(
                            "UPDATE tiqora_approval SET status = 'approved', reason = 'votes',"
                            " current_stage = NULL, decided_at = :t WHERE id = :i"
                        ),
                        {"t": utc_now(), "i": info.approval_id},
                    )
                    await b.commit()
                with pytest.raises(ApprovalError) as excinfo:
                    await service.cancel_approval(
                        a, info.approval_id, actor=ACTOR, check_permission=False, settings=SETTINGS
                    )
                assert excinfo.value.code == "approval_closed"
                await a.rollback()
                assert (await _row(a, info.approval_id)).status == "approved"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_cancel_keeps_a_late_approver_vote(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The approver-row half of CX8: a vote committed after A's snapshot is not overwritten."""
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            async with factory() as a:
                info = await _request(a, definition=CAB_STEP)
                await a.commit()
                rows = await _approvers(a, info.approval_id)  # starts A's snapshot
                assert {r.status for r in rows} == {"pending"} and len(rows) == 3
                async with factory() as b:
                    await b.execute(
                        text(
                            "UPDATE tiqora_approval_approver SET status = 'approved' WHERE id = :i"
                        ),
                        {"i": rows[0].id},
                    )
                    await b.commit()
                done = await service.cancel_approval(
                    a, info.approval_id, actor=ACTOR, check_permission=False, settings=SETTINGS
                )
                assert done.status == "cancelled"
                await a.commit()  # a fresh snapshot, so the plain read below sees B's vote
                got = {r.id: r.status for r in await _approvers(a, info.approval_id)}
                assert got == {
                    rows[0].id: "approved",
                    rows[1].id: "not_needed",
                    rows[2].id: "not_needed",
                }


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_auto_decision_reason_is_a_short_code(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            for bad in ("x" * 33, "Kunde kunde1@example.org", "", "has space"):
                with pytest.raises(ApprovalError) as excinfo:
                    await service.record_auto_decision(
                        session,
                        ticket_id=T1,
                        subject_type=SUBJECT,
                        title="Vorab",
                        actor=Actor.system(),
                        status="approved",
                        reason=bad,
                        settings=SETTINGS,
                    )
                assert (excinfo.value.code, excinfo.value.http_status) == (
                    "invalid_definition",
                    422,
                )
            ok = await service.record_auto_decision(
                session,
                ticket_id=T1,
                subject_type=SUBJECT,
                title="Vorab",
                actor=Actor.system(),
                status="approved",
                reason="standard_change",
                settings=SETTINGS,
            )
            assert ok.reason == "standard_change"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_approval_key_is_stripped(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            first = await _request(session, approval_key=" default ")
            assert (await _row(session, first.approval_id)).approval_key == "default"
            await _request(session)
            assert (await _row(session, first.approval_id)).status == "cancelled"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_system_and_portal_creators_do_not_make_agent_1_a_creator(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no(*args: object, **kwargs: object) -> bool:
        return False

    # agent 1 is an admin in a stock Znuny; take that and queue rights away to test "creator" alone
    monkeypatch.setattr(PermissionEngine, "is_admin", _no)
    monkeypatch.setattr(PermissionEngine, "check", _no)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            by_system = await _request(session, actor=Actor.system(), approval_key="sys")
            by_portal = await _request(
                session,
                actor=Actor.customer("appr97-k1"),
                requester=Principal.customer("appr97-k1"),
                approval_key="portal",
            )
            by_agent = await _request(session, actor=Actor.agent(AG3), approval_key="agent")
            for info in (by_system, by_portal):
                row = await _row(session, info.approval_id)
                assert row.created_by == 1
                assert await audit.creator_key(session, row) in (None, "customer:appr97-k1")
                excluded = await _engine.excluded_keys(session, row)
                assert "agent:1" not in excluded
                assert not await access.can_cancel(session, row, Actor(Principal.agent(1), 1))
            row = await _row(session, by_agent.approval_id)
            assert await audit.creator_key(session, row) == f"agent:{AG3}"
            assert await _engine.excluded_keys(session, row) == {f"agent:{AG3}"}
            assert await access.can_cancel(session, row, Actor.agent(AG3))
            # the portal creator is excluded as a creator in later stages (not only as requester)
            row = await _row(session, by_portal.approval_id)
            assert await _engine.excluded_keys(session, row) == {"customer:appr97-k1"}
