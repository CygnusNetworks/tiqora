"""Approval decisions: row lock, authorization, rule progression, finalize (A7b).

Own ids 971000-971149: the helper world supplies the agents/customers/tickets, the requests are
autoincrement rows inside the rolled-back ``approvals_session``. The tests that need real
commits (sessions racing) seed through ``_committed_world`` and clean up after themselves.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._approvals_helpers import (
    AG1,
    AG3,
    AG4,
    AG5,
    AG7,
    ANDERE,
    T1,
    approvals_session,
    delete_world,
    ensure_tiqora_tables,
    installed_registry,
    make_fake_subject_type,
    seed_world,
    to_async_url,
)
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests.approvals_consumer_kit import race_consumer_and_vote, touch_ticket
from tiqora.approvals import delegation, rules, service
from tiqora.approvals.contract import (
    EVENT_DECIDED,
    EVENT_REQUESTED,
    EVENT_VOTED,
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
    TiqoraApprovalVote,
)
from tiqora.approvals.settings import APPROVALS_SECTION
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.history_render import render_history_entry
from tiqora.jobs.service import utc_now
from tiqora.settings_sections.store import save_section_values

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
REQUESTER = Actor.agent(AG1)
K1, K2 = "appr97-k1", "appr97-k2"


def _user(uid: int) -> dict[str, str]:
    return {"kind": "user", "ref": str(uid)}


def _defn(*steps: dict[str, Any]) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate({"steps": list(steps)})


def _step(name: str, *approvers: dict[str, str], **extra: Any) -> dict[str, Any]:
    return {"name": name, "approvers": list(approvers), **extra}


ONE_STEP = _defn(_step("Prüfung", _user(AG3)))
TWO_ANY = _defn(_step("Prüfung", _user(AG3), _user(AG4)))
ALL_THREE = _defn(_step("CAB", _user(AG3), _user(AG4), _user(AG5), rule="all"))


async def _set(session: AsyncSession, **values: Any) -> None:
    await save_section_values(session, APPROVALS_SECTION, values, secret_key=SETTINGS.secret_key)


async def _request(session: AsyncSession, definition: ApprovalDefinition, **overrides: Any) -> int:
    args: dict[str, Any] = {
        "ticket_id": T1,
        "subject_type": SUBJECT,
        "title": "Freigabe appr97",
        "actor": REQUESTER,
        "definition": definition,
        "settings": SETTINGS,
    }
    args.update(overrides)
    info = await service.request_approval(session, **args)
    return info.approval_id


async def _decide(
    session: AsyncSession,
    approval_id: int,
    user_id: int,
    decision: str = "approved",
    **overrides: Any,
) -> service.DecisionResult:
    args: dict[str, Any] = {
        "actor": Actor.agent(user_id),
        "decision": decision,
        "channel": "ui",
        "settings": SETTINGS,
    }
    args.update(overrides)
    return await service.decide(session, approval_id, **args)


async def _refused(session: AsyncSession, approval_id: int, **kwargs: Any) -> ApprovalError:
    with pytest.raises(ApprovalError) as excinfo:
        await service.decide(
            session, approval_id, **{"channel": "ui", "settings": SETTINGS, **kwargs}
        )
    return excinfo.value


async def _row(session: AsyncSession, approval_id: int) -> TiqoraApproval:
    return (
        await session.execute(
            select(TiqoraApproval)
            .where(TiqoraApproval.id == approval_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


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


async def _by_user(session: AsyncSession, approval_id: int) -> dict[int, str]:
    return {
        int(a.user_id): a.status
        for a in await _approvers(session, approval_id)
        if a.user_id is not None
    }


async def _votes(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalVote]:
    rows = await session.execute(
        select(TiqoraApprovalVote)
        .where(TiqoraApprovalVote.approval_id == approval_id)
        .order_by(TiqoraApprovalVote.id)
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


async def _outbox(session: AsyncSession) -> list[tuple[str, dict[str, Any]]]:
    rows = await session.execute(
        text(
            "SELECT event_type, payload FROM tiqora_event_outbox WHERE ticket_id = :t ORDER BY id"
        ),
        {"t": T1},
    )
    return [(str(r[0]), json.loads(r[1])) for r in rows]


async def _jobs(session: AsyncSession, kind: str, approval_id: int) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(TiqoraJob).where(TiqoraJob.kind == kind).order_by(TiqoraJob.id)
    )
    payloads = [json.loads(j.payload) for j in rows.scalars()]
    return [p for p in payloads if p.get("approval_id") == approval_id]


async def _history(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT name FROM ticket_history WHERE ticket_id = :t AND name LIKE 'Approval #%'"
            " ORDER BY id"
        ),
        {"t": T1},
    )
    return [str(r[0]) for r in rows]


async def _add_approver(
    session: AsyncSession,
    approval_id: int,
    step_id: int,
    principal: str,
    *,
    status: str = "pending",
) -> TiqoraApprovalApprover:
    """A raw approver row (what resolution would normally have excluded)."""
    who = Principal.parse(principal)
    row = TiqoraApprovalApprover(
        approval_id=approval_id,
        step_id=step_id,
        ticket_id=T1,
        principal=principal,
        user_id=who.user_id,
        customer_login=who.login,
        source_kind="user",
        decisive=False,
        status=status,
        added_at=utc_now(),
    )
    session.add(row)
    await session.flush()
    return row


async def _add_token(
    session: AsyncSession, approval_id: int, approver_id: int, n: int
) -> TiqoraApprovalToken:
    now = utc_now()
    row = TiqoraApprovalToken(
        approval_id=approval_id,
        approver_id=approver_id,
        ticket_id=T1,
        token_hash=f"{n:064x}",
        created_at=now,
        expires_at=now + timedelta(days=1),
    )
    session.add(row)
    await session.flush()
    return row


async def _token_state(session: AsyncSession, token_id: int) -> tuple[bool, bool]:
    row = (
        await session.execute(
            select(TiqoraApprovalToken)
            .where(TiqoraApprovalToken.id == token_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return row.used_at is not None, row.revoked_at is not None


# --- rule progression ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_any_first_vote_decides(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, TWO_ANY)
            result = await _decide(session, aid, AG3, comment="passt")
            assert result.step_status == "approved"
            assert (result.info.status, result.info.reason) == ("approved", "votes")
            assert result.info.decided_at is not None and result.info.current_stage is None
            assert result.on_behalf is False
            assert result.affected_user_ids == {AG3, AG4}
            assert await _by_user(session, aid) == {AG3: "approved", AG4: "not_needed"}
            (vote,) = await _votes(session, aid)
            assert vote.id == result.vote_id
            assert (vote.decision, vote.comment, vote.decided_by, vote.channel, vote.on_behalf) == (
                "approved",
                "passt",
                f"agent:{AG3}",
                "ui",
                False,
            )
            assert [s.status for s in await _steps(session, aid)] == ["approved"]
            actions = [e.action for e in await _events(session, aid)]
            assert actions[-3:] == ["voted", "step_decided", "decided"]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_all_needs_everyone(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ALL_THREE)
            first = await _decide(session, aid, AG3)
            assert (first.step_status, first.info.status) == ("active", "pending")
            assert first.affected_user_ids == {AG3}
            second = await _decide(session, aid, AG4)
            assert (second.step_status, second.info.status) == ("active", "pending")
            third = await _decide(session, aid, AG5)
            assert (third.step_status, third.info.status, third.info.reason) == (
                "approved",
                "approved",
                "votes",
            )
            assert await _by_user(session, aid) == {
                AG3: "approved",
                AG4: "approved",
                AG5: "approved",
            }
            # one rejection rejects an "all" step at once
            other = await _request(session, ALL_THREE, approval_key="second")
            rejected = await _decide(session, other, AG4, "rejected")
            assert (rejected.step_status, rejected.info.status) == ("rejected", "rejected")
            assert await _by_user(session, other) == {
                AG3: "not_needed",
                AG4: "rejected",
                AG5: "not_needed",
            }


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
@pytest.mark.parametrize(
    ("rule", "quorum", "votes", "expected"),
    [
        ("quorum", 2, ["approved"], ["active"]),
        ("quorum", 2, ["approved", "approved"], ["active", "approved"]),
        ("quorum", 2, ["rejected", "approved", "approved"], ["active", "active", "approved"]),
        ("quorum", 2, ["rejected", "rejected"], ["active", "rejected"]),
        ("majority", None, ["approved", "rejected"], ["active", "active"]),
        ("majority", None, ["approved", "rejected", "approved"], ["active", "active", "approved"]),
        ("majority", None, ["rejected", "rejected"], ["active", "rejected"]),
        ("majority", None, ["approved", "approved"], ["active", "approved"]),
    ],
)
async def test_quorum_and_majority(
    url_fixture: str,
    request: pytest.FixtureRequest,
    rule: str,
    quorum: int | None,
    votes: list[str],
    expected: list[str],
) -> None:
    extra: dict[str, Any] = {"rule": rule}
    if quorum is not None:
        extra["quorum"] = quorum
    definition = _defn(_step("CAB", _user(AG3), _user(AG4), _user(AG5), **extra))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            seen = []
            for voter, decision in zip((AG3, AG4, AG5), votes, strict=False):
                seen.append((await _decide(session, aid, voter, decision)).step_status)
            assert seen == expected


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_reject_in_stage_one_rejects_request(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    definition = _defn(
        _step("Prüfung", _user(AG3)),
        _step("Freigabe", _user(AG4), stage=2),
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            result = await _decide(session, aid, AG3, "rejected", comment="nein")
            assert (result.info.status, result.info.reason) == ("rejected", "votes")
            first, second = await _steps(session, aid)
            assert (first.status, second.status) == ("rejected", "cancelled")
            assert second.activated_at is None
            assert await _by_user(session, aid) == {AG3: "rejected"}  # stage 2 never resolved
            assert result.info.current_stage is None
            assert await _jobs(session, JOB_HOOK, aid) == [
                {"approval_id": aid, "status": "rejected"}
            ]
            kinds = [m["kind"] for m in await _jobs(session, JOB_MAIL, aid)]
            assert kinds == ["request", "decided"]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_parallel_steps_same_stage(url_fixture: str, request: pytest.FixtureRequest) -> None:
    definition = _defn(
        _step("Technik", _user(AG3)),
        _step("Fachbereich", _user(AG4)),
        _step("Leitung", _user(AG5), stage=2),
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            first = await _decide(session, aid, AG3)
            assert (first.step_status, first.info.status, first.info.current_stage) == (
                "approved",
                "pending",
                1,
            )
            assert await _by_user(session, aid) == {AG3: "approved", AG4: "pending"}
            assert [m["kind"] for m in await _jobs(session, JOB_MAIL, aid)] == ["request"]
            second = await _decide(session, aid, AG4)
            assert (second.info.status, second.info.current_stage) == ("pending", 2)
            assert await _by_user(session, aid) == {
                AG3: "approved",
                AG4: "approved",
                AG5: "pending",
            }
            assert second.affected_user_ids == {AG4, AG5}
            (leader,) = [a for a in await _approvers(session, aid) if a.user_id == AG5]
            mails = await _jobs(session, JOB_MAIL, aid)
            assert [(m["kind"], m["approver_ids"]) for m in mails][-1] == ("request", [leader.id])
            assert [s.status for s in await _steps(session, aid)] == [
                "approved",
                "approved",
                "active",
            ]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_last_stage_finalizes(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            result = await _decide(session, aid, AG3)
            row = await _row(session, aid)
            assert (row.status, row.reason, row.current_stage) == ("approved", "votes", None)
            assert row.decided_at is not None
            (step,) = await _steps(session, aid)
            outbox = await _outbox(session)
            assert [name for name, _ in outbox] == [EVENT_REQUESTED, EVENT_VOTED, EVENT_DECIDED]
            base = {
                "approval_id": aid,
                "approval_key": "default",
                "subject_type": SUBJECT,
                "subject_ref": None,
            }
            assert outbox[1][1] == {
                **base,
                "status": "pending",
                "step_id": step.id,
                "decision": "approved",
                "channel": "ui",
            }
            assert outbox[2][1] == {**base, "status": "approved", "reason": "votes"}
            assert await _jobs(session, JOB_HOOK, aid) == [
                {"approval_id": aid, "status": "approved"}
            ]
            assert [m["kind"] for m in await _jobs(session, JOB_MAIL, aid)] == [
                "request",
                "decided",
            ]
            assert await _history(session) == [
                f"Approval #{aid} (default): requested",
                f"Approval #{aid}: voted approved",
                f"Approval #{aid}: decided approved",
            ]
            assert result.info.status == "approved"
            for line in await _history(session):
                assert render_history_entry(history_type="Misc", name=line) == line


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_not_needed_after_step_decided(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    definition = _defn(
        _step("Prüfung", _user(AG3), _user(AG4), _user(AG5)),
        _step("Freigabe", _user(AG5), stage=2),
    )
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            rows = {a.user_id: a for a in await _approvers(session, aid)}
            own = await _add_token(session, aid, rows[AG3].id, 1)
            spare = await _add_token(session, aid, rows[AG3].id, 2)
            others = [
                await _add_token(session, aid, rows[AG4].id, 3),
                await _add_token(session, aid, rows[AG5].id, 4),
            ]
            await _decide(session, aid, AG3, token_id=own.id)
            by_step = [(a.step_id, a.user_id, a.status) for a in await _approvers(session, aid)]
            step1, step2 = await _steps(session, aid)
            assert sorted(by_step) == sorted(
                [
                    (step1.id, AG3, "approved"),
                    (step1.id, AG4, "not_needed"),
                    (step1.id, AG5, "not_needed"),
                    (step2.id, AG5, "pending"),
                ]
            )
            assert await _token_state(session, own.id) == (True, False)  # redeemed
            assert await _token_state(session, spare.id) == (False, True)  # revoked
            for token in others:
                assert await _token_state(session, token.id) == (False, True)
            assert [e.action for e in await _events(session, aid)].count("step_decided") == 1


# --- authorization ------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_requester_cannot_vote(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP, requester=Principal.agent(AG5))
            (step,) = await _steps(session, aid)
            await _add_approver(session, aid, step.id, f"agent:{AG5}")
            error = await _refused(session, aid, actor=Actor.agent(AG5), decision="approved")
            assert (error.code, error.http_status) == ("self_approval_forbidden", 403)
            assert await _votes(session, aid) == []
            # allowed when the request says so
            ok = await _request(
                session,
                _defn(_step("Prüfung", _user(AG5))),
                requester=Principal.agent(AG5),
                allow_self_approval=True,
                approval_key="self",
            )
            assert (await _decide(session, ok, AG5)).info.status == "approved"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_creator_cannot_vote(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(
                session, ONE_STEP, actor=Actor.agent(AG5), requester=Principal.agent(AG1)
            )
            (step,) = await _steps(session, aid)
            await _add_approver(session, aid, step.id, f"agent:{AG5}")
            error = await _refused(session, aid, actor=Actor.agent(AG5), decision="approved")
            assert error.code == "self_approval_forbidden"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_requester_and_creator_compared_by_stored_login(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _set(session, customer_approvers_enabled=True)
            as_requester = await _request(
                session,
                _defn(_step("Prüfung", _user(AG3))),
                actor=Actor.customer(K2.upper()),
                requester=Principal.customer(K2.upper()),
                approval_key="r",
            )
            as_creator = await _request(
                session,
                _defn(_step("Prüfung", _user(AG3))),
                actor=Actor.customer(K2.upper()),
                requester=Principal.agent(AG1),
                approval_key="c",
            )
            for aid in (as_requester, as_creator):
                (step,) = await _steps(session, aid)
                await _add_approver(session, aid, step.id, f"customer:{K2}")
                error = await _refused(
                    session,
                    aid,
                    actor=Actor.customer(K2.title()),  # yet another spelling
                    decision="approved",
                    channel="portal",
                )
                assert (error.code, error.http_status) == ("self_approval_forbidden", 403)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_non_approver_refused(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            for actor in (Actor.agent(AG5), Actor.system()):
                error = await _refused(session, aid, actor=actor, decision="approved")
                assert (error.code, error.http_status) == ("not_an_approver", 403)
            assert await _votes(session, aid) == []
            missing = await _refused(
                session, aid + 1000, actor=Actor.agent(AG3), decision="approved"
            )
            assert (missing.code, missing.http_status) == ("approval_not_found", 404)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_explicit_approver_id_of_other_agent_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ALL_THREE)
            rows = {a.user_id: a for a in await _approvers(session, aid)}
            error = await _refused(
                session,
                aid,
                actor=Actor.agent(AG3),
                decision="approved",
                approver_id=rows[AG4].id,
            )
            assert (error.code, error.http_status) == ("not_an_approver", 403)
            assert await _votes(session, aid) == []
            ok = await _decide(session, aid, AG3, approver_id=rows[AG3].id)
            assert ok.on_behalf is False


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_approver_id_of_other_approval_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            mine = await _request(session, ONE_STEP, approval_key="mine")
            other = await _request(session, ONE_STEP, approval_key="other")
            (other_row,) = await _approvers(session, other)
            error = await _refused(
                session,
                mine,
                actor=Actor.agent(AG3),
                decision="approved",
                approver_id=other_row.id,
            )
            assert (error.code, error.http_status) == ("not_an_approver", 403)
            assert (await _row(session, mine)).status == "pending"
            assert (await _row(session, other)).status == "pending"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_vote_twice_refused(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ALL_THREE)
            await _decide(session, aid, AG3)
            error = await _refused(session, aid, actor=Actor.agent(AG3), decision="rejected")
            assert (error.code, error.http_status) == ("already_decided", 409)
            assert [v.decision for v in await _votes(session, aid)] == ["approved"]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_waiting_step_row_refused(url_fixture: str, request: pytest.FixtureRequest) -> None:
    definition = _defn(_step("Prüfung", _user(AG3)), _step("Freigabe", _user(AG4), stage=2))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            _, waiting = await _steps(session, aid)
            assert waiting.status == "waiting"
            await _add_approver(session, aid, waiting.id, f"agent:{AG5}")
            error = await _refused(session, aid, actor=Actor.agent(AG5), decision="approved")
            assert (error.code, error.http_status) == ("step_not_active", 409)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_closed_request_refused(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, TWO_ANY)
            await _decide(session, aid, AG3)
            error = await _refused(session, aid, actor=Actor.agent(AG4), decision="approved")
            assert (error.code, error.http_status) == ("approval_closed", 409)
            assert len(await _votes(session, aid)) == 1


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_email_channel_needs_allow_email(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    definition = _defn(_step("Prüfung", _user(AG3), _user(AG4), rule="all", allow_email=False))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            error = await _refused(
                session, aid, actor=Actor.agent(AG3), decision="approved", channel="email"
            )
            assert (error.code, error.http_status) == ("forbidden", 403)
            assert await _votes(session, aid) == []
            ok = await _decide(session, aid, AG3)  # the same person may use the UI
            assert ok.step_status == "active"
            open_ = await _request(
                session, _defn(_step("Prüfung", _user(AG3))), approval_key="mail"
            )
            via_mail = await _decide(session, open_, AG3, channel="email")
            assert via_mail.info.status == "approved"
            (vote,) = await _votes(session, open_)
            assert vote.channel == "email"


# --- customers ----------------------------------------------------------------------------------

CUSTOMER_STEP = _defn(_step("Kunde", {"kind": "customer_user", "ref": K2}))


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_vote(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _set(session, customer_approvers_enabled=True)
            aid = await _request(session, CUSTOMER_STEP)
            result = await service.decide(
                session,
                aid,
                actor=Actor.customer(K2),
                decision="approved",
                channel="portal",
                settings=SETTINGS,
            )
            assert result.info.status == "approved"
            assert result.affected_user_ids == frozenset()  # customers have no agent inbox
            (vote,) = await _votes(session, aid)
            assert (vote.decided_by, vote.channel, vote.on_behalf) == (
                f"customer:{K2}",
                "portal",
                False,
            )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_vote_after_company_change_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _set(session, customer_approvers_enabled=True)
            aid = await _request(session, CUSTOMER_STEP)
            await session.execute(
                text("UPDATE ticket SET customer_id = :c WHERE id = :t"), {"c": ANDERE, "t": T1}
            )
            error = await _refused(
                session, aid, actor=Actor.customer(K2), decision="approved", channel="portal"
            )
            assert (error.code, error.http_status) == ("customer_approver_outside_company", 403)
            assert await _votes(session, aid) == []
            assert (await _row(session, aid)).status == "pending"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_vote_refused_when_setting_turned_off(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _set(session, customer_approvers_enabled=True)
            aid = await _request(session, CUSTOMER_STEP)
            await _set(session, customer_approvers_enabled=False)
            error = await _refused(
                session, aid, actor=Actor.customer(K2), decision="approved", channel="portal"
            )
            assert (error.code, error.http_status) == ("forbidden", 403)
            assert await _votes(session, aid) == []


# --- delegation (stubbed here, Task 8 fills it) -------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_on_behalf_vote_with_stubbed_delegation(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def _delegators(session: AsyncSession, substitute_user_id: int, *, at: Any) -> set[int]:
        calls.append(substitute_user_id)
        return {AG3} if substitute_user_id == AG7 else set()

    monkeypatch.setattr(delegation, "active_delegators", _delegators)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            result = await _decide(session, aid, AG7, comment="in Vertretung")
            assert (result.on_behalf, result.info.status) == (True, "approved")
            assert result.affected_user_ids == {AG3, AG7}
            (vote,) = await _votes(session, aid)
            assert (vote.decided_by, vote.on_behalf) == (f"agent:{AG7}", True)
            assert await _by_user(session, aid) == {AG3: "approved"}
            assert AG7 in calls
            # a delegate who is the requester can never vote
            blocked = await _request(
                session, ONE_STEP, requester=Principal.agent(AG7), approval_key="b"
            )
            error = await _refused(session, blocked, actor=Actor.agent(AG7), decision="approved")
            assert error.code == "self_approval_forbidden"


# --- input and ordering -------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decision_comment_length(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ALL_THREE)
            error = await _refused(
                session, aid, actor=Actor.agent(AG3), decision="approved", comment="x" * 2001
            )
            assert (error.code, error.http_status) == ("comment_too_long", 422)
            assert await _votes(session, aid) == []
            await _decide(session, aid, AG3, comment="x" * 2000)
            await _decide(session, aid, AG4, comment="   ")
            comments = [v.comment for v in await _votes(session, aid)]
            assert comments == ["x" * 2000, None]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_votes_ordered_for_any(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``evaluate_step`` gets the voters in vote order, the ones still pending last."""
    seen: list[list[str]] = []
    real = rules.evaluate_step

    def _spy(rule: Any, quorum: Any, voters: Sequence[rules.VoterState]) -> Any:
        seen.append([v.status for v in voters])
        return real(rule, quorum, voters)

    monkeypatch.setattr(rules, "evaluate_step", _spy)
    definition = _defn(_step("CAB", _user(AG3), _user(AG4), _user(AG5), rule="majority"))
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, definition)
            await _decide(session, aid, AG5, "rejected")  # highest approver id votes first
            assert seen[-1] == ["rejected", "pending", "pending"]
            await _decide(session, aid, AG4, "approved")
            # vote order (AG5, AG4) then the pending AG3; approver-id order would be reversed
            assert seen[-1] == ["rejected", "approved", "pending"]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_row_lock_is_the_first_statement(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """F1(a): decide starts with ``SELECT … FROM tiqora_approval … FOR UPDATE``."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            seen: list[str] = []

            def _capture(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
                seen.append(" ".join(statement.split()).lower())

            bind = session.sync_session.get_bind()
            event.listen(bind, "before_cursor_execute", _capture)
            try:
                await _decide(session, aid, AG3)
            finally:
                event.remove(bind, "before_cursor_execute", _capture)
            real = [q for q in seen if not q.startswith(("savepoint", "release", "rollback"))]
            assert real[0].startswith("select")
            assert re.search(r"from tiqora_approval(?![_a-z])", real[0])
            assert real[0].endswith("for update")
            assert not any("tiqora_approval_" in q for q in real[:1])


# --- locking and concurrency --------------------------------------------------------------------

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


async def _decide_and_commit(
    factory: async_sessionmaker[AsyncSession], approval_id: int, user_id: int
) -> str:
    """The outcome of one racing caller: ``ok`` or the error code."""
    async with factory() as session:
        await session.rollback()  # callers roll back before deciding (F1(a))
        try:
            await _decide(session, approval_id, user_id)
        except ApprovalError as exc:
            await session.rollback()
            return exc.code
        except DBAPIError as exc:  # deadlock, lock wait timeout
            await session.rollback()
            return f"db_error:{type(exc.orig).__name__}"
        await session.commit()
        return "ok"


async def _race(
    factory: async_sessionmaker[AsyncSession], approval_id: int, *user_ids: int
) -> list[str]:
    """Runs the callers at once; a lock regression fails fast instead of hanging the suite.

    Cancelling on timeout closes every racer session (rolling back, releasing its locks) before
    the world is cleaned up.
    """
    try:
        return await asyncio.wait_for(
            asyncio.gather(*(_decide_and_commit(factory, approval_id, u) for u in user_ids)),
            timeout=45,
        )
    except TimeoutError:
        pytest.fail("racing decisions did not finish within 45 s (lock regression?)")


async def _final_counts(
    factory: async_sessionmaker[AsyncSession], approval_id: int
) -> tuple[str, int, int, int]:
    """(status, votes, decided events, hook jobs) read through a fresh transaction."""
    async with factory() as session:
        status = (await _row(session, approval_id)).status
        votes = len(await _votes(session, approval_id))
        decided = sum(1 for e in await _events(session, approval_id) if e.action == "decided")
        outbox = [
            p
            for name, p in await _outbox(session)
            if name == EVENT_DECIDED and p["approval_id"] == approval_id
        ]
        hooks = await _jobs(session, JOB_HOOK, approval_id)
        assert decided == len(outbox) == len(hooks)
        return status, votes, len(outbox), len(hooks)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_parallel_votes_one_transition(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Quorum 2 of 3, two callers at once: both votes stored, one decision, one hook job."""
    definition = _defn(_step("CAB", _user(AG3), _user(AG4), _user(AG5), rule="quorum", quorum=2))
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            async with factory() as setup:
                aid = await _request(setup, definition)
                await setup.commit()
            outcomes = await _race(factory, aid, AG3, AG4)
            assert outcomes == ["ok", "ok"]
            assert await _final_counts(factory, aid) == ("approved", 2, 1, 1)
            async with factory() as check:
                assert await _by_user(check, aid) == {
                    AG3: "approved",
                    AG4: "approved",
                    AG5: "not_needed",
                }


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_parallel_votes_rule_any_second_gets_step_not_active(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Rule ``any`` over two stages: the loser's row is ``not_needed`` once the winner committed."""
    definition = _defn(
        _step("Prüfung", _user(AG3), _user(AG4)), _step("Freigabe", _user(AG5), stage=2)
    )
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            async with factory() as setup:
                aid = await _request(setup, definition)
                await setup.commit()
            outcomes = await _race(factory, aid, AG3, AG4)
            assert sorted(outcomes) == ["ok", "step_not_active"]
            async with factory() as check:
                assert len(await _votes(check, aid)) == 1
                assert (await _row(check, aid)).status == "pending"
                assert (await _row(check, aid)).current_stage == 2
                rows = await _approvers(check, aid)
                assert sorted(a.status for a in rows) == ["approved", "not_needed", "pending"]


@pytest.mark.db
@pytest.mark.parametrize("finalise", [False, True])
@pytest.mark.parametrize("url_fixture", URLS)
async def test_cancel_inside_consumer_transaction_sees_late_vote(
    url_fixture: str, request: pytest.FixtureRequest, finalise: bool
) -> None:
    """A consumer's open transaction (snapshot taken) cancels after a real vote committed.

    The vote must stay visible (approver ``approved``, not overwritten by ``not_needed``); when
    the vote finalised the request the cancel is refused ``approval_closed``.
    """
    definition = ONE_STEP if finalise else ALL_THREE
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            async with factory() as a:
                aid = await _request(a, definition)
                await a.commit()
                rows = await _approvers(a, aid)  # starts A's REPEATABLE-READ snapshot
                assert {r.status for r in rows} == {"pending"}
                async with factory() as b:
                    await b.rollback()
                    await _decide(b, aid, AG3)
                    await b.commit()
                if finalise:
                    with pytest.raises(ApprovalError) as excinfo:
                        await service.cancel_approval(
                            a, aid, actor=REQUESTER, check_permission=False, settings=SETTINGS
                        )
                    assert excinfo.value.code == "approval_closed"
                    await a.rollback()
                    assert (await _row(a, aid)).status == "approved"
                    assert len(await _votes(a, aid)) == 1
                else:
                    done = await service.cancel_approval(
                        a, aid, actor=REQUESTER, check_permission=False, settings=SETTINGS
                    )
                    assert done.status == "cancelled"
                    await a.commit()
                    assert await _by_user(a, aid) == {
                        AG3: "approved",
                        AG4: "not_needed",
                        AG5: "not_needed",
                    }
                    assert [v.decision for v in await _votes(a, aid)] == ["approved"]


@pytest.mark.db
@pytest.mark.parametrize("call", ["cancel", "supersede"])
async def test_consumer_order_approval_then_ticket_never_deadlocks(
    request: pytest.FixtureRequest, call: str
) -> None:
    """MariaDB: the contract's lock order (approval service first, then the ticket row).

    A consumer cancels (or supersedes) inside its transaction, a vote arrives and waits for the
    request lock, then the consumer writes the ticket row and commits. No 1213: the vote sees
    the closed request. (The engine's own history line S-locks the ticket after the request,
    so a consumer that wrote the ticket first would deadlock here.)
    """
    async with _committed_world(request.getfixturevalue("mariadb_znuny_url")) as factory:
        with installed_registry(make_fake_subject_type()):
            async with factory() as setup:
                aid = await _request(setup, ALL_THREE)
                await setup.commit()

            async def consumer_call(a: AsyncSession) -> None:
                if call == "cancel":
                    await service.cancel_approval(
                        a, aid, actor=REQUESTER, check_permission=False, settings=SETTINGS
                    )
                else:
                    await _request(a, ALL_THREE)

            outcome = await race_consumer_and_vote(
                factory,
                aid,
                first=consumer_call,
                then=lambda a: touch_ticket(a, T1),
                voter=Actor.agent(AG3),
            )
            assert outcome == ("ok", "approval_closed")
            async with factory() as check:
                assert (await _row(check, aid)).status == "cancelled"
                assert await _votes(check, aid) == []
                history = await check.execute(
                    text("SELECT name FROM ticket_history WHERE ticket_id = :t AND name = :n"),
                    {"t": T1, "n": f"Approval #{aid}: cancelled"},
                )
                assert len(history.all()) == 1
