"""Delegation (Vertretung), forwarding and approver refresh (A7, A8).

Own ids 971150-971199: the helper world supplies agents AG1-AG7, queues, group ``appr97-cab``
(AG3-AG5 rw) and tickets T1/T2; ``NEW_MEMBER`` is the one extra agent. Every test runs inside the
rolled-back ``approvals_session``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._approvals_helpers import (
    AG1,
    AG2,
    AG3,
    AG4,
    AG5,
    AG6,
    AG7,
    GROUP_CAB,
    T1,
    approvals_session,
    installed_registry,
    make_fake_subject_type,
)
from tests._itsm_adapter_helpers import seed_user
from tiqora.approvals import delegation, service
from tiqora.approvals.contract import Actor, ApprovalDefinition, ApprovalError
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalEvent,
    TiqoraApprovalStep,
    TiqoraApprovalToken,
)
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.jobs.service import utc_now

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
NEW_MEMBER = 971150
NO_SUCH_USER = 971199
CAB_GROUP = {"kind": "group", "ref": str(GROUP_CAB)}


def _user(uid: int) -> dict[str, str]:
    return {"kind": "user", "ref": str(uid)}


def _defn(*steps: dict[str, Any]) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate({"steps": list(steps)})


def _step(name: str, *approvers: dict[str, str], **extra: Any) -> dict[str, Any]:
    return {"name": name, "approvers": list(approvers), **extra}


async def _request(
    session: AsyncSession, definition: ApprovalDefinition, *, requester: int = AG2
) -> int:
    info = await service.request_approval(
        session,
        ticket_id=T1,
        subject_type=SUBJECT,
        title="Freigabe appr97",
        actor=Actor.agent(requester),
        definition=definition,
        settings=SETTINGS,
    )
    return info.approval_id


async def _decide(
    session: AsyncSession, approval_id: int, user_id: int, decision: str = "approved", **kw: Any
) -> service.DecisionResult:
    return await service.decide(
        session,
        approval_id,
        actor=Actor.agent(user_id),
        decision=decision,  # type: ignore[arg-type]
        channel="ui",
        settings=SETTINGS,
        **kw,
    )


async def _refused(coro: Any) -> ApprovalError:
    with pytest.raises(ApprovalError) as excinfo:
        await coro
    return excinfo.value


async def _delegate(
    session: AsyncSession,
    delegator: int,
    substitute: int,
    *,
    start: timedelta = timedelta(minutes=-1),
    end: timedelta | None = timedelta(days=1),
) -> int:
    now = utc_now()
    row = await delegation.create_delegation(
        session,
        user_id=delegator,
        substitute_user_id=substitute,
        valid_from=now + start,
        valid_until=None if end is None else now + end,
        created_by=delegator,
    )
    return int(row.id)


async def _approvers(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalApprover]:
    rows = await session.execute(
        select(TiqoraApprovalApprover)
        .where(TiqoraApprovalApprover.approval_id == approval_id)
        .order_by(TiqoraApprovalApprover.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def _status_of(session: AsyncSession, approval_id: int) -> dict[int, str]:
    return {
        int(a.user_id): a.status
        for a in await _approvers(session, approval_id)
        if a.user_id is not None
    }


async def _events(session: AsyncSession, approval_id: int, action: str) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(TiqoraApprovalEvent)
        .where(TiqoraApprovalEvent.approval_id == approval_id, TiqoraApprovalEvent.action == action)
        .order_by(TiqoraApprovalEvent.id)
    )
    return [json.loads(e.detail) if e.detail else {} for e in rows.scalars()]


async def _mail_payloads(
    session: AsyncSession, approval_id: int, kind: str
) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(TiqoraJob).where(TiqoraJob.kind == "core.approval_mail").order_by(TiqoraJob.id)
    )
    payloads = [json.loads(j.payload) for j in rows.scalars()]
    return [p for p in payloads if p["approval_id"] == approval_id and p["kind"] == kind]


async def _approval(session: AsyncSession, approval_id: int) -> TiqoraApproval:
    return (
        await session.execute(
            select(TiqoraApproval)
            .where(TiqoraApproval.id == approval_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _step_rows(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalStep]:
    rows = await session.execute(
        select(TiqoraApprovalStep)
        .where(TiqoraApprovalStep.approval_id == approval_id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


# --- delegation ---------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_create_and_list_delegation(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        now = utc_now()
        did = await _delegate(session, AG5, AG1)
        past = await delegation.create_delegation(
            session,
            user_id=AG5,
            substitute_user_id=AG2,
            valid_from=now - timedelta(days=3),
            valid_until=now - timedelta(days=2),
            created_by=AG5,
        )
        active = await delegation.list_delegations(session, user_id=AG5)
        assert [d.id for d in active] == [did]
        assert (active[0].user_id, active[0].substitute_user_id) == (AG5, AG1)
        assert active[0].revoked_at is None and active[0].created_by == AG5
        both = await delegation.list_delegations(session, user_id=AG5, include_ended=True)
        assert {d.id for d in both} == {did, past.id}
        by_sub = await delegation.list_delegations(session, substitute_user_id=AG1)
        assert [d.id for d in by_sub] == [did]
        assert await delegation.list_delegations(session, substitute_user_id=AG2) == []
        assert await delegation.active_delegators(session, AG1, at=now) == {AG5}
        assert await delegation.active_delegators(session, AG2, at=now) == set()
        assert await delegation.active_substitutes(session, [AG5, AG3], at=now) == {AG5: {AG1}}
        await delegation.revoke_delegation(session, did, actor=Actor.agent(AG5))
        assert await delegation.list_delegations(session, user_id=AG5) == []
        assert await delegation.active_delegators(session, AG1, at=now) == set()


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegation_to_self_or_invalid_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        for target in (AG5, AG6, NO_SUCH_USER):  # self, invalid agent, unknown
            err = await _refused(_delegate(session, AG5, target))
            assert (err.code, err.http_status) == ("forward_target_invalid", 422)
        err = await _refused(
            _delegate(session, AG5, AG1, start=timedelta(days=1), end=timedelta(0))
        )
        assert (err.code, err.http_status) == ("invalid_definition", 422)
        assert await delegation.list_delegations(session, user_id=AG5, include_ended=True) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_overlapping_delegation_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        first = await _delegate(session, AG5, AG1, end=timedelta(days=2))
        # overlaps the first one (even with another substitute)
        err = await _refused(_delegate(session, AG5, AG2, start=timedelta(days=1), end=None))
        assert (err.code, err.http_status) == ("delegation_overlap", 409)
        # a window that begins after the first one ends is fine, and so is another delegator
        await _delegate(session, AG5, AG2, start=timedelta(days=3), end=timedelta(days=4))
        await _delegate(session, AG3, AG1)
        # a revoked delegation no longer blocks
        await delegation.revoke_delegation(session, first, actor=Actor.agent(AG5))
        await _delegate(session, AG5, AG7, start=timedelta(days=1), end=timedelta(days=2))


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_revoke_delegation_permissions(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        did = await _delegate(session, AG5, AG1)
        err = await _refused(delegation.revoke_delegation(session, did, actor=Actor.agent(AG3)))
        assert (err.code, err.http_status) == ("forbidden", 403)
        await delegation.revoke_delegation(session, did, actor=Actor.agent(AG5))
        err = await _refused(delegation.revoke_delegation(session, did, actor=Actor.agent(AG5)))
        assert (err.code, err.http_status) == ("delegation_not_active", 403)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegate_decides_on_behalf(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            aid = await _request(session, _defn(_step("CAB", _user(AG5), _user(AG3), rule="all")))
            result = await _decide(session, aid, AG1)
            assert result.on_behalf is True
            assert result.affected_user_ids >= {AG1, AG5}
            rows = {a.user_id: a for a in await _approvers(session, aid)}
            assert rows[AG5].status == "approved" and rows[AG3].status == "pending"
            vote = (
                await session.execute(
                    text(
                        "SELECT decided_by, on_behalf, approver_id FROM tiqora_approval_vote"
                        " WHERE approval_id = :a"
                    ),
                    {"a": aid},
                )
            ).one()
            assert (vote[0], bool(vote[1]), int(vote[2])) == (f"agent:{AG1}", True, rows[AG5].id)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_both_may_decide_first_wins(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            aid = await _request(session, _defn(_step("CAB", _user(AG5), _user(AG3), rule="all")))
            await _decide(session, aid, AG5)
            err = await _refused(_decide(session, aid, AG1))
            assert (err.code, err.http_status) == ("already_decided", 409)
            assert (await _status_of(session, aid))[AG5] == "approved"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegate_after_valid_until_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1, start=timedelta(hours=-1), end=timedelta(minutes=-1))
            aid = await _request(session, _defn(_step("CAB", _user(AG5), _user(AG3), rule="all")))
            err = await _refused(_decide(session, aid, AG1))
            assert (err.code, err.http_status) == ("not_an_approver", 403)
            # the delegator keeps the right to decide
            assert (await _decide(session, aid, AG5)).on_behalf is False


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegation_not_yet_valid_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1, start=timedelta(hours=1))
            aid = await _request(session, _defn(_step("CAB", _user(AG5), _user(AG3), rule="all")))
            err = await _refused(_decide(session, aid, AG1))
            assert err.code == "not_an_approver"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_revoked_delegation_refused(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            did = await _delegate(session, AG5, AG1)
            aid = await _request(session, _defn(_step("CAB", _user(AG5), _user(AG3), rule="all")))
            await delegation.revoke_delegation(session, did, actor=Actor.agent(AG5))
            err = await _refused(_decide(session, aid, AG1))
            assert (err.code, err.http_status) == ("not_an_approver", 403)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_step_without_delegation_refuses_delegate(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            aid = await _request(
                session,
                _defn(_step("CAB", _user(AG5), _user(AG3), rule="all", allow_delegation=False)),
            )
            err = await _refused(_decide(session, aid, AG1))
            assert (err.code, err.http_status) == ("not_an_approver", 403)
            assert (await _decide(session, aid, AG5)).on_behalf is False


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegate_who_is_requester_cannot_vote(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            aid = await _request(
                session, _defn(_step("CAB", _user(AG5), _user(AG3))), requester=AG1
            )
            err = await _refused(_decide(session, aid, AG1))
            assert (err.code, err.http_status) == ("self_approval_forbidden", 403)
            assert (await _status_of(session, aid))[AG5] == "pending"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegation_not_transitive(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            await _delegate(session, AG1, AG2)
            aid = await _request(
                session, _defn(_step("CAB", _user(AG5), _user(AG3))), requester=AG7
            )
            assert await delegation.active_delegators(session, AG2, at=utc_now()) == {AG1}
            err = await _refused(_decide(session, aid, AG2))
            assert (err.code, err.http_status) == ("not_an_approver", 403)
            # an explicit id of the delegator's row does not help either
            row = next(a for a in await _approvers(session, aid) if a.user_id == AG5)
            err = await _refused(_decide(session, aid, AG2, approver_id=row.id))
            assert err.code == "not_an_approver"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegate_one_vote_per_step(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            await _delegate(session, AG3, AG1)
            aid = await _request(
                session, _defn(_step("CAB", _user(AG3), _user(AG4), _user(AG5), rule="all"))
            )
            await _decide(session, aid, AG1)
            err = await _refused(_decide(session, aid, AG1))
            assert (err.code, err.http_status) == ("already_decided", 409)
            statuses = await _status_of(session, aid)
            assert sorted(v for v in statuses.values() if v != "pending") == ["approved"]
            # the delegator of the second row can still decide for themselves
            assert (await _decide(session, aid, AG5)).on_behalf is False


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_own_vote_then_on_behalf_vote_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG3)
            aid = await _request(
                session, _defn(_step("CAB", _user(AG3), _user(AG4), _user(AG5), rule="all"))
            )
            await _decide(session, aid, AG3)
            err = await _refused(_decide(session, aid, AG3))
            assert (err.code, err.http_status) == ("already_decided", 409)
            assert (await _status_of(session, aid))[AG5] == "pending"


# --- forward ------------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_forward_creates_row_and_marks_old(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", _user(AG3), _user(AG4), rule="all")))
            old = next(a for a in await _approvers(session, aid) if a.user_id == AG3)
            session.add(
                TiqoraApprovalToken(
                    approval_id=aid,
                    approver_id=old.id,
                    ticket_id=T1,
                    token_hash=f"{971150:064x}",
                    created_at=utc_now(),
                    expires_at=utc_now() + timedelta(days=1),
                )
            )
            await session.flush()
            new_id = await service.forward(session, old.id, actor=Actor.agent(AG3), to_user_id=AG1)
            rows = {a.id: a for a in await _approvers(session, aid)}
            assert rows[old.id].status == "forwarded"
            new = rows[new_id]
            assert (new.principal, new.user_id, new.status) == (f"agent:{AG1}", AG1, "pending")
            assert (new.source_kind, new.source_ref, new.step_id) == (
                "forward",
                str(old.id),
                old.step_id,
            )
            assert new.decisive is old.decisive
            (event,) = await _events(session, aid, "forwarded")
            assert event == {"to_user_id": AG1, "new_approver_id": new_id}
            row = (
                await session.execute(
                    select(TiqoraApprovalEvent).where(
                        TiqoraApprovalEvent.approval_id == aid,
                        TiqoraApprovalEvent.action == "forwarded",
                    )
                )
            ).scalar_one()
            assert (row.actor, row.approver_id) == (f"agent:{AG3}", old.id)
            mails = await _mail_payloads(session, aid, "forwarded")
            assert mails == [
                {
                    "approval_id": aid,
                    "kind": "forwarded",
                    "approver_ids": [new_id],
                    "include_requester": False,
                    "include_substitutes": True,
                }
            ]
            dead = (
                await session.execute(
                    select(TiqoraApprovalToken.revoked_at).where(
                        TiqoraApprovalToken.approver_id == old.id
                    )
                )
            ).scalar_one()
            assert dead is not None
            # the forwarded-to agent decides; the original approver no longer can
            err = await _refused(_decide(session, aid, AG3))
            assert err.code == "already_decided"
            await _decide(session, aid, AG1)
            await _decide(session, aid, AG4)
            assert (await _approval(session, aid)).status == "approved"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_forward_not_allowed_by_step(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(
                session, _defn(_step("CAB", _user(AG3), _user(AG4), allow_delegation=False))
            )
            row = next(a for a in await _approvers(session, aid) if a.user_id == AG3)
            err = await _refused(
                service.forward(session, row.id, actor=Actor.agent(AG3), to_user_id=AG1)
            )
            assert (err.code, err.http_status) == ("forward_not_allowed", 409)
            assert (await _status_of(session, aid))[AG3] == "pending"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_forward_twice_refused(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", _user(AG3), _user(AG4), rule="all")))
            old = next(a for a in await _approvers(session, aid) if a.user_id == AG3)
            new_id = await service.forward(session, old.id, actor=Actor.agent(AG3), to_user_id=AG1)
            err = await _refused(
                service.forward(session, new_id, actor=Actor.agent(AG1), to_user_id=AG7)
            )
            assert (err.code, err.http_status) == ("forward_not_allowed", 409)
            # the already forwarded row cannot be forwarded again either
            err = await _refused(
                service.forward(session, old.id, actor=Actor.agent(AG3), to_user_id=AG7)
            )
            assert (err.code, err.http_status) == ("already_decided", 409)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_forward_to_requester_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(
                session, _defn(_step("CAB", _user(AG3), _user(AG4))), requester=AG1
            )
            row = next(a for a in await _approvers(session, aid) if a.user_id == AG3)
            err = await _refused(
                service.forward(session, row.id, actor=Actor.agent(AG3), to_user_id=AG1)
            )
            assert (err.code, err.http_status) == ("self_approval_forbidden", 403)
            assert (await _status_of(session, aid))[AG3] == "pending"
            assert len(await _approvers(session, aid)) == 2


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_forward_to_existing_approver_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", _user(AG3), _user(AG4))))
            row = next(a for a in await _approvers(session, aid) if a.user_id == AG3)
            for target in (
                AG4,
                AG3,
                AG6,
                NO_SUCH_USER,
                1,
            ):  # approver, self, invalid, unknown, root
                err = await _refused(
                    service.forward(session, row.id, actor=Actor.agent(AG3), to_user_id=target)
                )
                assert (err.code, err.http_status) == ("forward_target_invalid", 422)
            assert len(await _approvers(session, aid)) == 2


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_forward_refused_for_foreign_and_closed_rows(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", _user(AG3), _user(AG4), rule="all")))
            row = next(a for a in await _approvers(session, aid) if a.user_id == AG3)
            err = await _refused(
                service.forward(session, row.id, actor=Actor.agent(AG4), to_user_id=AG1)
            )
            assert (err.code, err.http_status) == ("not_an_approver", 403)
            err = await _refused(
                service.forward(session, 971198, actor=Actor.agent(AG3), to_user_id=AG1)
            )
            assert (err.code, err.http_status) == ("not_an_approver", 403)
            await _decide(session, aid, AG3)
            err = await _refused(
                service.forward(session, row.id, actor=Actor.agent(AG3), to_user_id=AG1)
            )
            assert (err.code, err.http_status) == ("already_decided", 409)
            await _decide(session, aid, AG4, "rejected")
            other = next(a for a in await _approvers(session, aid) if a.user_id == AG4)
            err = await _refused(
                service.forward(session, other.id, actor=Actor.agent(AG4), to_user_id=AG1)
            )
            assert (err.code, err.http_status) == ("approval_closed", 409)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_cannot_forward(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", _user(AG3), _user(AG4))))
            row = next(a for a in await _approvers(session, aid) if a.user_id == AG3)
            err = await _refused(
                service.forward(session, row.id, actor=Actor.customer("appr97-k1"), to_user_id=AG1)
            )
            assert (err.code, err.http_status) == ("forbidden", 403)
            err = await _refused(
                service.forward(session, row.id, actor=Actor.system(), to_user_id=AG1)
            )
            assert err.code == "forbidden"


# --- refresh ------------------------------------------------------------------------------------


async def _seed_new_member(session: AsyncSession) -> None:
    await seed_user(session, NEW_MEMBER, "appr97-agent0", "Appr", "appr97-agent0")
    await session.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            " change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
        ),
        {"u": NEW_MEMBER, "g": GROUP_CAB, "t": utc_now()},
    )


async def _leave_cab(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        text("DELETE FROM group_user WHERE user_id = :u AND group_id = :g"),
        {"u": user_id, "g": GROUP_CAB},
    )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_refresh_adds_new_members_and_removes_departed(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", CAB_GROUP, rule="all")))
            assert await _status_of(session, aid) == {
                AG3: "pending",
                AG4: "pending",
                AG5: "pending",
            }
            await _decide(session, aid, AG3)
            await _leave_cab(session, AG5)
            await _leave_cab(session, AG3)  # already voted: stays
            await _seed_new_member(session)
            result = await service.refresh_approvers(session, aid, actor=Actor.agent(AG1))
            assert (result.added, result.removed) == (1, 1)
            assert await _status_of(session, aid) == {
                AG3: "approved",
                AG4: "pending",
                AG5: "removed",
                NEW_MEMBER: "pending",
            }
            new = next(a for a in await _approvers(session, aid) if a.user_id == NEW_MEMBER)
            assert (new.source_kind, new.source_ref) == ("group", str(GROUP_CAB))
            (added,) = [
                e
                for e in await _events(session, aid, "approver_added")
                if e.get("source") == "group"
            ][-1:]
            assert added == {"source": "group"}
            assert await _events(session, aid, "approver_removed") == [{"reason": "departed"}]
            (summary,) = await _events(session, aid, "refreshed")
            assert summary == {"added": 1, "removed": 1}
            mails = await _mail_payloads(session, aid, "request")
            assert mails[-1]["approver_ids"] == [new.id]
            # the step is still open and works with the refreshed set
            await _decide(session, aid, AG4)
            assert (await _approval(session, aid)).status == "pending"
            await _decide(session, aid, NEW_MEMBER)
            assert (await _approval(session, aid)).status == "approved"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_refresh_reevaluates_step(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", CAB_GROUP, rule="all")))
            await _decide(session, aid, AG3)
            await _decide(session, aid, AG4)
            await _leave_cab(session, AG5)
            result = await service.refresh_approvers(session, aid, actor=Actor.system())
            assert (result.added, result.removed) == (0, 1)
            approval = await _approval(session, aid)
            assert (approval.status, approval.reason) == ("approved", "votes")
            assert [s.status for s in await _step_rows(session, aid)] == ["approved"]
            err = await _refused(service.refresh_approvers(session, aid, actor=Actor.system()))
            assert (err.code, err.http_status) == ("approval_closed", 409)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_refresh_without_any_approver_left_applies_on_no_approver(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", CAB_GROUP)))
            for uid in (AG3, AG4, AG5):
                await _leave_cab(session, uid)
            await session.execute(
                text("DELETE FROM group_role WHERE group_id = :g"), {"g": GROUP_CAB}
            )
            result = await service.refresh_approvers(session, aid, actor=Actor.agent(AG1))
            assert (result.added, result.removed) == (0, 3)
            approval = await _approval(session, aid)
            assert (approval.status, approval.reason) == ("rejected", "no_approvers")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_refresh_skips_principals_with_any_row(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", CAB_GROUP, rule="all")))
            old = next(a for a in await _approvers(session, aid) if a.user_id == AG3)
            forwarded_to = await service.forward(
                session, old.id, actor=Actor.agent(AG3), to_user_id=AG1
            )
            # AG3 is still in the group but already has a (forwarded) row in the step
            result = await service.refresh_approvers(session, aid, actor=Actor.agent(AG1))
            assert (result.added, result.removed) == (0, 0)
            rows = await _approvers(session, aid)
            assert sorted(a.principal for a in rows) == sorted(
                [f"agent:{AG1}", f"agent:{AG3}", f"agent:{AG4}", f"agent:{AG5}"]
            )
            by_id = {a.id: a.status for a in rows}
            assert by_id[old.id] == "forwarded" and by_id[forwarded_to] == "pending"
            # departed + back again: the removed row is not duplicated
            await _leave_cab(session, AG5)
            assert (
                await service.refresh_approvers(session, aid, actor=Actor.agent(AG1))
            ).removed == 1
            await _seed_new_member(session)
            await session.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
                ),
                {"u": AG5, "g": GROUP_CAB, "t": utc_now()},
            )
            result = await service.refresh_approvers(session, aid, actor=Actor.agent(AG1))
            assert (result.added, result.removed) == (1, 0)  # NEW_MEMBER only
            assert (await _status_of(session, aid))[AG5] == "removed"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_refresh_audits_unresolved_entries_and_keeps_their_rows(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            missing = {"kind": "group", "ref": str(NO_SUCH_USER)}
            aid = await _request(session, _defn(_step("CAB", _user(AG3), missing, rule="all")))
            before = [e for e in await _events(session, aid, "approver_excluded") if "ref" in e]
            assert before == [{"kind": "group", "ref": str(NO_SUCH_USER), "reason": "unknown"}]
            result = await service.refresh_approvers(session, aid, actor=Actor.agent(AG1))
            assert (result.added, result.removed) == (0, 0)
            after = [e for e in await _events(session, aid, "approver_excluded") if "ref" in e]
            assert after == before * 2
            assert await _status_of(session, aid) == {AG3: "pending"}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_refresh_keeps_escalation_and_forward_rows(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", _user(AG3), _user(AG4), rule="all")))
            step = (await _step_rows(session, aid))[0]
            session.add(
                TiqoraApprovalApprover(
                    approval_id=aid,
                    step_id=step.id,
                    ticket_id=T1,
                    principal=f"agent:{AG7}",
                    user_id=AG7,
                    source_kind="escalation",
                    decisive=True,
                    status="pending",
                    added_at=utc_now(),
                )
            )
            await session.flush()
            result = await service.refresh_approvers(session, aid, actor=Actor.agent(AG1))
            assert (result.added, result.removed) == (0, 0)
            assert (await _status_of(session, aid))[AG7] == "pending"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_refresh_permission(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", CAB_GROUP, rule="all")))
            for actor in (Actor.agent(AG2), Actor.agent(AG7), Actor.customer("appr97-k1")):
                err = await _refused(service.refresh_approvers(session, aid, actor=actor))
                assert (err.code, err.http_status) == ("forbidden", 403)
            err = await _refused(service.refresh_approvers(session, 971197, actor=Actor.agent(AG1)))
            assert (err.code, err.http_status) == ("approval_not_found", 404)
            await service.refresh_approvers(session, aid, actor=Actor.agent(AG1))


# --- F8: no row that nobody can vote on ----------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegate_vote_closes_own_pending_row(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            aid = await _request(
                session, _defn(_step("CAB", _user(AG1), _user(AG5), _user(AG3), rule="all"))
            )
            rows = {a.user_id: a for a in await _approvers(session, aid)}
            # explicit id of the delegator's row while the own row is pending
            result = await _decide(session, aid, AG1, approver_id=rows[AG5].id)
            assert result.on_behalf is True
            assert await _status_of(session, aid) == {
                AG1: "not_needed",
                AG5: "approved",
                AG3: "pending",
            }
            assert {"reason": "already_voted"} in await _events(session, aid, "approver_removed")
            err = await _refused(_decide(session, aid, AG1))
            assert err.code == "already_decided"
            # the step finishes without a timeout
            await _decide(session, aid, AG3)
            assert (await _approval(session, aid)).status == "approved"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_forward_to_target_who_voted_on_behalf_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            aid = await _request(
                session, _defn(_step("CAB", _user(AG5), _user(AG3), _user(AG4), rule="all"))
            )
            await _decide(session, aid, AG1)  # on behalf of AG5
            row = next(a for a in await _approvers(session, aid) if a.user_id == AG3)
            err = await _refused(
                service.forward(session, row.id, actor=Actor.agent(AG3), to_user_id=AG1)
            )
            assert (err.code, err.http_status) == ("already_decided", 409)
            assert len(await _approvers(session, aid)) == 3
            await _decide(session, aid, AG3)
            await _decide(session, aid, AG4)
            assert (await _approval(session, aid)).status == "approved"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_refresh_does_not_add_principal_who_voted_in_step(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            aid = await _request(session, _defn(_step("CAB", CAB_GROUP, rule="all")))
            await _decide(session, aid, AG1)  # on behalf of AG5
            await session.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
                ),
                {"u": AG1, "g": GROUP_CAB, "t": utc_now()},
            )
            result = await service.refresh_approvers(session, aid, actor=Actor.agent(AG1))
            assert (result.added, result.removed) == (0, 0)
            assert AG1 not in await _status_of(session, aid)
            await _decide(session, aid, AG3)
            await _decide(session, aid, AG4)
            assert (await _approval(session, aid)).status == "approved"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegation_for_invalid_delegator_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        for delegator in (AG6, NO_SUCH_USER, 1):
            err = await _refused(_delegate(session, delegator, AG1))
            assert (err.code, err.http_status) == ("invalid_definition", 422)
