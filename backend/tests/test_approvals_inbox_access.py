"""Approval inbox queries and the viewing/requesting rules (A8).

Own ids 971200-971299: ``ADMIN`` is the one extra agent (member of the ``admin`` group); the
helper world supplies everything else. Every test runs inside the rolled-back
``approvals_session``.
"""

from __future__ import annotations

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
    AG7,
    T1,
    T2,
    approvals_session,
    installed_registry,
    make_fake_subject_type,
)
from tests._itsm_adapter_helpers import seed_user
from tiqora.approvals import access, delegation, inbox, service
from tiqora.approvals.contract import Actor, ApprovalDefinition
from tiqora.approvals.models import TiqoraApproval
from tiqora.approvals.settings import APPROVALS_SECTION
from tiqora.config import Settings
from tiqora.jobs.service import utc_now
from tiqora.settings_sections.store import save_section_values

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
ADMIN = 971201
K1, K2 = "appr97-k1", "appr97-k2"


def _user(uid: int) -> dict[str, str]:
    return {"kind": "user", "ref": str(uid)}


def _defn(*steps: dict[str, Any]) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate({"steps": list(steps)})


def _step(name: str, *approvers: dict[str, str], **extra: Any) -> dict[str, Any]:
    return {"name": name, "approvers": list(approvers), **extra}


async def _request(
    session: AsyncSession,
    definition: ApprovalDefinition,
    *,
    requester: int = AG2,
    ticket: int = T1,
    key: str = "default",
    title: str = "Freigabe appr97",
) -> int:
    info = await service.request_approval(
        session,
        ticket_id=ticket,
        subject_type=SUBJECT,
        title=title,
        actor=Actor.agent(requester),
        definition=definition,
        approval_key=key,
        settings=SETTINGS,
    )
    return info.approval_id


async def _decide(session: AsyncSession, aid: int, uid: int, decision: str = "approved") -> None:
    await service.decide(
        session,
        aid,
        actor=Actor.agent(uid),
        decision=decision,  # type: ignore[arg-type]
        channel="ui",
        settings=SETTINGS,
    )


async def _delegate(session: AsyncSession, delegator: int, substitute: int, **kw: Any) -> int:
    now = utc_now()
    row = await delegation.create_delegation(
        session,
        user_id=delegator,
        substitute_user_id=substitute,
        valid_from=now - timedelta(minutes=1),
        valid_until=kw.get("until", now + timedelta(days=1)),
        created_by=delegator,
    )
    return int(row.id)


async def _row(session: AsyncSession, aid: int) -> TiqoraApproval:
    return (
        await session.execute(
            select(TiqoraApproval)
            .where(TiqoraApproval.id == aid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _pending(session: AsyncSession, uid: int) -> inbox.InboxPage:
    return await inbox.inbox(session, Actor.agent(uid), state="pending", limit=50, offset=0)


async def _decided(session: AsyncSession, uid: int, **kw: Any) -> inbox.InboxPage:
    args: dict[str, Any] = {"limit": 50, "offset": 0, **kw}
    return await inbox.inbox(session, Actor.agent(uid), state="decided", **args)


# --- inbox --------------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inbox_pending_includes_on_behalf(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            own = await _request(session, _defn(_step("Eigene", _user(AG1))), key="own")
            behalf = await _request(
                session,
                _defn(_step("CAB", _user(AG5), _user(AG3), rule="all", due_hours=24)),
                key="behalf",
                title="Vertretung",
            )
            locked = await _request(
                session,
                _defn(_step("Ohne", _user(AG5), _user(AG3), allow_delegation=False)),
                key="locked",
            )
            page = await _pending(session, AG1)
            assert page.total == 2
            by_id = {i.approval_id: i for i in page.items}
            assert set(by_id) == {own, behalf}  # the no-delegation step stays out
            assert [i.approval_id for i in page.items] == [behalf, own]  # deadline first, NULL last
            item = by_id[behalf]
            assert item.on_behalf_of == AG5
            assert (item.ticket_id, item.ticket_number, item.ticket_title) == (
                T1,
                f"20261015{T1}",
                "appr97 ticket",
            )
            assert (item.title, item.subject_type, item.step_name) == ("Vertretung", SUBJECT, "CAB")
            assert (item.rule, item.required, item.approved_count) == ("all", 2, 0)
            assert item.due_at is not None and item.overdue is False
            assert (item.status, item.decided_at, item.my_decision) == ("pending", None, None)
            mine = by_id[own]
            assert mine.on_behalf_of is None and mine.due_at is None and mine.required == 1
            assert mine.approver_id != item.approver_id
            # the delegator sees the same row as his own
            page5 = await _pending(session, AG5)
            assert {i.approval_id: i.on_behalf_of for i in page5.items} == {
                behalf: None,
                locked: None,
            }
            # once the delegation ends the on-behalf row leaves the delegate's inbox
            await delegation.revoke_delegation(
                session,
                (await delegation.list_delegations(session, user_id=AG5))[0].id,
                actor=Actor.agent(AG5),
            )
            assert [i.approval_id for i in (await _pending(session, AG1)).items] == [own]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inbox_overdue_and_progress(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(
                session, _defn(_step("CAB", _user(AG3), _user(AG4), _user(AG5), rule="majority"))
            )
            await _decide(session, aid, AG3)
            await session.execute(
                text("UPDATE tiqora_approval_step SET due_at = :d WHERE approval_id = :a"),
                {"d": utc_now() - timedelta(hours=1), "a": aid},
            )
            (item,) = (await _pending(session, AG4)).items
            assert (item.rule, item.required, item.approved_count) == ("majority", 2, 1)
            assert item.overdue is True
            # AG3 voted: the row left the pending tab
            assert (await _pending(session, AG3)).total == 0


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inbox_progress_counts_the_voters_in_the_rule(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """An escalation-upgraded approver still counts; an escalation-only approver does not."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("CAB", _user(AG3), _user(AG4), rule="all")))
            rows = (
                await session.execute(
                    text(
                        "SELECT id, user_id FROM tiqora_approval_approver"
                        " WHERE approval_id = :a ORDER BY id"
                    ),
                    {"a": aid},
                )
            ).all()
            step_id = (
                await session.execute(
                    text("SELECT id FROM tiqora_approval_step WHERE approval_id = :a"), {"a": aid}
                )
            ).scalar_one()
            ag4_row = next(i for i, u in rows if u == AG4)
            await session.execute(
                text("UPDATE tiqora_approval_approver SET decisive = :t WHERE id = :i"),
                {"t": True, "i": ag4_row},
            )
            await session.execute(
                text(
                    "INSERT INTO tiqora_approval_approver (approval_id, step_id, ticket_id,"
                    " principal, user_id, source_kind, decisive, status, added_at)"
                    " VALUES (:a, :s, :t, :p, :u, 'escalation', :d, 'pending', :n)"
                ),
                {
                    "a": aid,
                    "s": step_id,
                    "t": T1,
                    "p": f"agent:{AG5}",
                    "u": AG5,
                    "d": True,
                    "n": utc_now(),
                },
            )
            await session.commit()
            (item,) = (await _pending(session, AG3)).items
            # AG3 and AG4 count (rule all: 2 of 2); AG5 decides alone but is outside the rule
            assert (item.rule, item.required, item.approved_count) == ("all", 2, 0)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inbox_excludes_waiting_and_closed(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            staged = await _request(
                session,
                _defn(
                    _step("Erst", _user(AG3)),
                    _step("Dann", _user(AG1), stage=2),
                ),
                key="staged",
            )
            assert (await _pending(session, AG1)).total == 0  # stage 2 is waiting
            await _decide(session, staged, AG3)
            assert [i.approval_id for i in (await _pending(session, AG1)).items] == [staged]
            cancelled = await _request(session, _defn(_step("X", _user(AG1))), key="c")
            await service.cancel_approval(
                session, cancelled, actor=Actor.agent(AG2), settings=SETTINGS
            )
            decided = await _request(session, _defn(_step("Y", _user(AG1), _user(AG3))), key="d")
            await _decide(session, decided, AG3)  # any: AG1's row becomes not_needed
            assert [i.approval_id for i in (await _pending(session, AG1)).items] == [staged]
            assert (await _pending(session, AG4)).total == 0


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inbox_decided_tab(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            first = await _request(
                session, _defn(_step("A", _user(AG3), _user(AG5), rule="all")), key="a"
            )
            second = await _request(session, _defn(_step("B", _user(AG1))), key="b")
            third = await _request(session, _defn(_step("C", _user(AG1))), key="c")
            await _decide(session, first, AG1)  # on behalf of AG5
            await _decide(session, second, AG1, "rejected")
            assert (await _decided(session, AG1)).total == 2
            page = await _decided(session, AG1)
            by_id = {i.approval_id: i for i in page.items}
            assert set(by_id) == {first, second}
            assert by_id[first].on_behalf_of == AG5 and by_id[first].my_decision == "approved"
            assert by_id[first].status == "pending"  # the request is still open
            assert by_id[second].my_decision == "rejected" and by_id[second].status == "rejected"
            assert by_id[second].decided_at is not None
            assert by_id[second].on_behalf_of is None
            # the delegator sees the vote on the row that is his
            only5 = await _decided(session, AG5)
            assert [i.approval_id for i in only5.items] == [first]
            assert only5.items[0].on_behalf_of is None
            # paging: total stays, items are cut
            cut = await _decided(session, AG1, limit=1, offset=0)
            rest = await _decided(session, AG1, limit=1, offset=1)
            assert (cut.total, rest.total) == (2, 2)
            assert len(cut.items) == len(rest.items) == 1
            assert {cut.items[0].approval_id, rest.items[0].approval_id} == {first, second}
            assert (await _decided(session, AG1, offset=5)).items == []
            # the undecided request is not in the decided tab
            assert third not in by_id
            assert [i.approval_id for i in (await _pending(session, AG1)).items] == [third]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inbox_ticket_fields_loaded_without_join(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            on_t1 = await _request(session, _defn(_step("A", _user(AG4))), key="t1")
            on_t2 = await _request(session, _defn(_step("A", _user(AG4))), key="t2", ticket=T2)
            await session.execute(
                text("UPDATE ticket SET title = 'Zweites Ticket' WHERE id = :t"), {"t": T2}
            )
            page = await _pending(session, AG4)
            by_id = {i.approval_id: i for i in page.items}
            assert (by_id[on_t1].ticket_number, by_id[on_t1].ticket_title) == (
                f"20261015{T1}",
                "appr97 ticket",
            )
            assert (by_id[on_t2].ticket_number, by_id[on_t2].ticket_title) == (
                f"20261015{T2}",
                "Zweites Ticket",
            )
            # the approver has no access to either queue: the inbox lists them anyway
            assert by_id[on_t2].ticket_id == T2


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_pending_count(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            assert await inbox.pending_count(session, Actor.agent(AG1)) == 0
            await _delegate(session, AG5, AG1)
            await _request(session, _defn(_step("A", _user(AG1))), key="a")
            await _request(session, _defn(_step("B", _user(AG5), _user(AG3))), key="b")
            await _request(
                session, _defn(_step("C", _user(AG5), _user(AG3), allow_delegation=False)), key="c"
            )
            assert await inbox.pending_count(session, Actor.agent(AG1)) == 2
            assert await inbox.pending_count(session, Actor.agent(AG5)) == 2
            assert await inbox.pending_count(session, Actor.agent(AG4)) == 0
            assert await inbox.pending_count(session, Actor.system()) == 0


# --- access -------------------------------------------------------------------------------------


async def _seed_admin(session: AsyncSession) -> None:
    await seed_user(session, ADMIN, "appr97-admin", "Appr", "Admin")
    group_id = (
        await session.execute(
            text("SELECT id FROM permission_groups WHERE name = 'admin' AND valid_id = 1")
        )
    ).scalar_one_or_none()
    if group_id is None:
        group_id = 971202
        await session.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'admin', 1, :t, 1, :t, 1)"
            ),
            {"id": group_id, "t": utc_now()},
        )
    await session.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            " change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
        ),
        {"u": ADMIN, "g": group_id, "t": utc_now()},
    )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_can_view_matrix(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _seed_admin(session)
            await save_section_values(
                session,
                APPROVALS_SECTION,
                {"customer_approvers_enabled": True},
                secret_key=SETTINGS.secret_key,
            )
            on_t1 = await _request(session, _defn(_step("A", _user(AG3))), key="t1")
            on_t2 = await _request(
                session, _defn(_step("A", _user(AG4))), key="t2", ticket=T2, requester=AG7
            )
            with_customer = await _request(
                session,
                _defn(_step("A", {"kind": "customer_user", "ref": K1})),
                key="cust",
            )
            r1, r2, r3 = (
                await _row(session, on_t1),
                await _row(session, on_t2),
                await _row(session, with_customer),
            )

            async def view(approval: TiqoraApproval, actor: Actor) -> bool:
                return await access.can_view(session, approval, actor)

            # admin and the system see everything
            assert await view(r1, Actor.agent(ADMIN)) and await view(r2, Actor.agent(ADMIN))
            assert await view(r2, Actor.system())
            # ro on the ticket's queue (AG2 is ro on QA, nothing on QB)
            assert await view(r1, Actor.agent(AG2)) is True
            assert await view(r2, Actor.agent(AG2)) is False
            # an approver without ticket access, the requester, a random agent
            assert await view(r2, Actor.agent(AG4)) is True
            assert await view(r2, Actor.agent(AG7)) is True
            assert await view(r1, Actor.agent(AG7)) is False
            assert await view(r1, Actor.agent(AG5)) is False
            # an approver sees the request after her row is closed (any status)
            await _decide(session, on_t1, AG3, "rejected")
            assert await view(await _row(session, on_t1), Actor.agent(AG3)) is True
            # an active delegate (AG1 has no access to QB), not an ended or revoked one
            did = await _delegate(session, AG4, AG1)
            assert await view(r2, Actor.agent(AG1)) is True
            await delegation.revoke_delegation(session, did, actor=Actor.agent(AG4))
            assert await view(r2, Actor.agent(AG1)) is False
            await _delegate(session, AG4, AG1, until=utc_now() - timedelta(seconds=30))
            assert await view(r2, Actor.agent(AG1)) is False
            # customers only through their own approver row
            assert await view(r3, Actor.customer(K1)) is True
            assert await view(r3, Actor.customer(K2)) is False
            assert await view(r1, Actor.customer(K1)) is False


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_can_request_matrix(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed_admin(session)
        assert await access.can_request(session, T1, Actor.agent(AG1)) is True  # rw
        assert await access.can_request(session, T1, Actor.agent(AG2)) is False  # ro
        assert await access.can_request(session, T1, Actor.agent(AG7)) is False
        assert await access.can_request(session, T2, Actor.agent(AG1)) is False
        assert await access.can_request(session, T2, Actor.agent(ADMIN)) is True
        assert await access.can_request(session, T1, Actor.customer(K1)) is False
        assert await access.can_request(session, 971299, Actor.agent(ADMIN)) is False


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_ticket_link_allowed_only_with_ro(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed_admin(session)
        assert await access.ticket_link_allowed(session, T1, Actor.agent(AG1)) is True  # rw
        assert await access.ticket_link_allowed(session, T1, Actor.agent(AG2)) is True  # ro
        assert await access.ticket_link_allowed(session, T1, Actor.agent(AG7)) is False
        # an approver on a ticket of a foreign queue gets no link
        assert await access.ticket_link_allowed(session, T2, Actor.agent(AG4)) is False
        assert await access.ticket_link_allowed(session, T2, Actor.agent(ADMIN)) is True
        assert await access.ticket_link_allowed(session, T1, Actor.customer(K1)) is False
        assert await access.ticket_link_allowed(session, 971299, Actor.agent(ADMIN)) is False


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inbox_marks_rows_the_actor_cannot_vote_on(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _delegate(session, AG5, AG1)
            # AG1 holds an own row and AG5's; after AG1 votes their own, AG5's row is dead for AG1
            voted = await _request(
                session,
                _defn(_step("CAB", _user(AG1), _user(AG5), _user(AG3), rule="all")),
                key="v",
            )
            # the requester is the delegate: AG5's row can never be voted by AG1
            own = await _request(
                session, _defn(_step("X", _user(AG5), _user(AG3))), key="o", requester=AG1
            )
            open_ = await _request(session, _defn(_step("Y", _user(AG5), _user(AG3))), key="y")
            before = await _pending(session, AG1)
            flags = {i.approval_id: (i.can_vote, i.blocked_reason) for i in before.items}
            assert flags[voted] == (True, None)  # own row preferred, so listed once as votable
            assert flags[own] == (False, "own_request")
            assert flags[open_] == (True, None)
            assert await inbox.pending_count(session, Actor.agent(AG1)) == 3
            await _decide(session, voted, AG1)
            after = await _pending(session, AG1)
            flags = {
                (i.approval_id, i.on_behalf_of): (i.can_vote, i.blocked_reason) for i in after.items
            }
            assert flags[(voted, AG5)] == (False, "already_voted")
            assert after.total == 3  # still listed, just marked
            assert await inbox.pending_count(session, Actor.agent(AG1)) == 1
            decided = await _decided(session, AG1)
            assert all(i.can_vote is False for i in decided.items)
