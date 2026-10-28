"""E-mail decision links: issue, resolve, single-use redeem, revocation, purge (A10).

Band 971400-971499 is reserved for this module; it needs no seeds of its own - the helper world
supplies agents and tickets, requests and tokens are autoincrement rows inside the rolled-back
``approvals_session``. The race test commits for real (``_committed_world``) and cleans up.
Sessions handed to ``redeem_link`` are committed first: the redeem rolls back (lock-first, F1(b)).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator
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
from tiqora.approvals import delegation, service, tokens
from tiqora.approvals.contract import Actor, ApprovalDefinition, ApprovalError, Principal
from tiqora.approvals.models import (
    TiqoraApprovalApprover,
    TiqoraApprovalEvent,
    TiqoraApprovalStep,
    TiqoraApprovalToken,
    TiqoraApprovalVote,
)
from tiqora.approvals.settings import APPROVALS_SECTION, ApprovalSettings
from tiqora.config import Settings
from tiqora.jobs.service import utc_now
from tiqora.settings_sections.store import save_section_values

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
REQUESTER = Actor.agent(AG1)
DEFAULTS = ApprovalSettings()


def _defn(*steps: dict[str, Any]) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate({"steps": list(steps)})


def _step(name: str, *users: int, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "approvers": [{"kind": "user", "ref": str(u)} for u in users],
        **extra,
    }


ONE_STEP = _defn(_step("Prüfung", AG3))
TWO_ANY = _defn(_step("Prüfung", AG3, AG4))
ALL_TWO = _defn(_step("CAB", AG3, AG4, rule="all"))


async def _request(session: AsyncSession, definition: ApprovalDefinition) -> int:
    info = await service.request_approval(
        session,
        ticket_id=T1,
        subject_type=SUBJECT,
        title="Freigabe appr97",
        actor=REQUESTER,
        definition=definition,
        settings=SETTINGS,
    )
    return info.approval_id


async def _approver(session: AsyncSession, approval_id: int, user_id: int) -> int:
    row = (
        await session.execute(
            select(TiqoraApprovalApprover)
            .where(
                TiqoraApprovalApprover.approval_id == approval_id,
                TiqoraApprovalApprover.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return row.id


async def _tokens(session: AsyncSession, approver_id: int) -> list[TiqoraApprovalToken]:
    rows = await session.execute(
        select(TiqoraApprovalToken)
        .where(TiqoraApprovalToken.approver_id == approver_id)
        .order_by(TiqoraApprovalToken.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def _issue(session: AsyncSession, approver_id: int, **settings: Any) -> str:
    return await tokens.issue_link(
        session, approver_id, settings=ApprovalSettings.model_validate(settings)
    )


async def _expect(session: AsyncSession, token: str, code: str) -> None:
    with pytest.raises(ApprovalError) as excinfo:
        await tokens.resolve_link(session, token)
    assert (excinfo.value.code, excinfo.value.http_status) == (code, 410)


async def _votes(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalVote]:
    rows = await session.execute(
        select(TiqoraApprovalVote)
        .where(TiqoraApprovalVote.approval_id == approval_id)
        .order_by(TiqoraApprovalVote.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def _set_settings(session: AsyncSession, **values: Any) -> None:
    await save_section_values(session, APPROVALS_SECTION, values, secret_key=SETTINGS.secret_key)


# --- issue ---------------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_issue_stores_only_hash(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            approver = await _approver(session, aid, AG3)
            token = await _issue(session, approver)
            assert len(token) >= 43 and token == token.strip()
            (row,) = await _tokens(session, approver)
            assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
            assert row.token_hash == tokens.link_digest(token) and len(row.token_hash) == 64
            columns = {c.name: getattr(row, c.name) for c in TiqoraApprovalToken.__table__.columns}
            assert token not in {str(v) for v in columns.values()}
            assert (row.approval_id, row.approver_id, row.ticket_id) == (aid, approver, T1)
            assert row.used_at is None and row.revoked_at is None
            # a second link is a different random token
            assert await _issue(session, approver) != token
            events = (
                await session.execute(
                    select(TiqoraApprovalEvent).where(
                        TiqoraApprovalEvent.approval_id == aid,
                        TiqoraApprovalEvent.action == "link_issued",
                    )
                )
            ).scalars()
            assert [e.approver_id for e in events] == [approver, approver]
            assert all(token not in (e.detail or "") for e in events)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_expiry_default_is_ttl(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)  # no deadline
            approver = await _approver(session, aid, AG3)
            before = utc_now()
            await _issue(session, approver, link_ttl_hours=48)
            (row,) = await _tokens(session, approver)
            assert before + timedelta(hours=48) - timedelta(seconds=2) <= row.expires_at
            assert row.expires_at <= utc_now() + timedelta(hours=48)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_expiry_capped_by_deadline(url_fixture: str, request: pytest.FixtureRequest) -> None:
    """link_ttl 168 h, step due in 2 h -> expires_at = due_at + 24 h."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("Prüfung", AG3, due_hours=2)))
            approver = await _approver(session, aid, AG3)
            await _issue(session, approver, link_ttl_hours=168)
            (row,) = await _tokens(session, approver)
            step_due = (
                await session.execute(
                    select(TiqoraApprovalApprover.step_id).where(
                        TiqoraApprovalApprover.id == approver
                    )
                )
            ).scalar_one()
            due = (
                await session.execute(
                    select(TiqoraApprovalStep.due_at).where(TiqoraApprovalStep.id == step_due)
                )
            ).scalar_one()
            assert due is not None
            assert row.expires_at == due + timedelta(hours=24)
            assert row.expires_at < utc_now() + timedelta(hours=27)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_cap_revokes_oldest(url_fixture: str, request: pytest.FixtureRequest) -> None:
    """max_links_per_approver 2 -> the third issue revokes the first."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            approver = await _approver(session, aid, AG3)
            first = await _issue(session, approver, max_links_per_approver=2)
            second = await _issue(session, approver, max_links_per_approver=2)
            assert [t.revoked_at is None for t in await _tokens(session, approver)] == [True, True]
            third = await _issue(session, approver, max_links_per_approver=2)
            rows = await _tokens(session, approver)
            assert [t.revoked_at is not None for t in rows] == [True, False, False]
            await _expect(session, first, "link_invalid")
            assert (await tokens.resolve_link(session, second)).approver_id == approver
            assert (await tokens.resolve_link(session, third)).approver_id == approver


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_issue_refuses_non_pending_row(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A forwarded / not_needed / voted approver row gets no link and no row is inserted."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, TWO_ANY)
            voter, other = await _approver(session, aid, AG3), await _approver(session, aid, AG4)
            await service.decide(
                session,
                aid,
                actor=Actor.agent(AG3),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            for approver in (voter, other):  # voted / not_needed
                with pytest.raises(ApprovalError) as excinfo:
                    await _issue(session, approver)
                assert excinfo.value.code == "not_an_approver"
                assert await _tokens(session, approver) == []

            fwd = await _request(session, ONE_STEP)
            row = await _approver(session, fwd, AG3)
            await service.forward(session, row, actor=Actor.agent(AG3), to_user_id=AG5)
            with pytest.raises(ApprovalError) as excinfo:
                await _issue(session, row)
            assert excinfo.value.code == "not_an_approver"
            assert await _tokens(session, row) == []

            with pytest.raises(ApprovalError) as excinfo:
                await _issue(session, 987654321)
            assert excinfo.value.code == "not_an_approver"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_issue_refuses_customer_outside_company(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """B-I1: no link for a customer approver once the ticket left their company."""
    async with approvals_session(request, url_fixture) as session:
        await _set_settings(session, customer_approvers_enabled=True)
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            aid = await _request(
                session,
                _defn(
                    {"name": "Kunde", "approvers": [{"kind": "customer_user", "ref": "appr97-k1"}]}
                ),
            )
            row = (
                await session.execute(
                    select(TiqoraApprovalApprover.id).where(
                        TiqoraApprovalApprover.approval_id == aid
                    )
                )
            ).scalar_one()
            assert await _issue(session, row)  # in the company: a link
            await session.execute(
                text("UPDATE ticket SET customer_id = :c WHERE id = :t"), {"c": ANDERE, "t": T1}
            )
            with pytest.raises(ApprovalError) as excinfo:
                await _issue(session, row)
            assert (excinfo.value.code, excinfo.value.http_status) == (
                "customer_approver_outside_company",
                403,
            )
            assert len(await _tokens(session, row)) == 1


# --- resolve -------------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_resolve_valid(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            approver = await _approver(session, aid, AG3)
            token = await _issue(session, approver)
            target = await tokens.resolve_link(session, token)
            (row,) = await _tokens(session, approver)
            assert target == tokens.LinkTarget(
                token_id=row.id,
                approval_id=aid,
                approver_id=approver,
                principal=Principal.agent(AG3),
            )
            # reading never consumes the link
            assert (await tokens.resolve_link(session, token, for_update=True)) == target
            assert (await _tokens(session, approver))[0].used_at is None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_unknown_token(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _expect(session, "not-a-token", "link_invalid")
        await _expect(session, "", "link_invalid")
        await _expect(session, "x" * 5000, "link_invalid")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_expired_token(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            approver = await _approver(session, aid, AG3)
            token = await _issue(session, approver)
            (row,) = await _tokens(session, approver)
            row.expires_at = utc_now() - timedelta(seconds=5)
            await session.commit()
            await _expect(session, token, "link_expired")
            with pytest.raises(ApprovalError) as excinfo:
                await tokens.redeem_link(
                    session, token, decision="approved", comment=None, settings=SETTINGS
                )
            assert excinfo.value.code == "link_expired"
            await session.rollback()
            assert await _votes(session, aid) == []
            assert (await _tokens(session, approver))[0].used_at is None


# --- redeem --------------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_redeem_approves(url_fixture: str, request: pytest.FixtureRequest) -> None:
    """Vote channel email, decided_by the approver, used_at set, the other links revoked."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            approver = await _approver(session, aid, AG3)
            token = await _issue(session, approver)
            other = await _issue(session, approver)
            await session.commit()
            result = await tokens.redeem_link(
                session, token, decision="approved", comment="  ok per Mail  ", settings=SETTINGS
            )
            await session.commit()
            assert (result.info.status, result.info.reason, result.on_behalf) == (
                "approved",
                "votes",
                False,
            )
            (vote,) = await _votes(session, aid)
            assert (vote.decision, vote.channel, vote.decided_by, vote.comment) == (
                "approved",
                "email",
                f"agent:{AG3}",
                "ok per Mail",
            )
            assert vote.approver_id == approver and vote.id == result.vote_id
            used, revoked = await _tokens(session, approver)
            assert used.used_at is not None and used.revoked_at is None
            assert revoked.used_at is None and revoked.revoked_at is not None
            await _expect(session, other, "link_invalid")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_redeem_rejects(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            token = await _issue(session, await _approver(session, aid, AG3))
            await session.commit()
            result = await tokens.redeem_link(
                session, token, decision="rejected", comment=None, settings=SETTINGS
            )
            await session.commit()
            assert (result.info.status, result.info.reason) == ("rejected", "votes")
            assert [v.decision for v in await _votes(session, aid)] == ["rejected"]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_redeem_twice(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            token = await _issue(session, await _approver(session, aid, AG3))
            await session.commit()
            await tokens.redeem_link(
                session, token, decision="approved", comment=None, settings=SETTINGS
            )
            await session.commit()
            for _ in range(2):
                with pytest.raises(ApprovalError) as excinfo:
                    await tokens.redeem_link(
                        session, token, decision="rejected", comment=None, settings=SETTINGS
                    )
                assert (excinfo.value.code, excinfo.value.http_status) == ("link_used", 410)
                await session.rollback()
            await _expect(session, token, "link_used")
            assert [v.decision for v in await _votes(session, aid)] == ["approved"]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_failed_decide_leaves_link_unused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """decide raises (comment too long) -> the caller's rollback also undoes used_at."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            approver = await _approver(session, aid, AG3)
            token = await _issue(session, approver)
            await session.commit()
            with pytest.raises(ApprovalError) as excinfo:
                await tokens.redeem_link(
                    session, token, decision="approved", comment="x" * 2001, settings=SETTINGS
                )
            assert excinfo.value.code == "comment_too_long"
            await session.commit()  # a careless caller committing anyway consumes nothing
            assert (await _tokens(session, approver))[0].used_at is None
            assert (await tokens.resolve_link(session, token)).approver_id == approver
            assert await _votes(session, aid) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_step_without_email_decisions_refuses(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, _defn(_step("Prüfung", AG3, allow_email=False)))
            approver = await _approver(session, aid, AG3)
            with pytest.raises(ApprovalError) as excinfo:  # issue refuses ...
                await _issue(session, approver)
            assert excinfo.value.code == "forbidden"
            token = "appr97-raw-token"  # ... so a stray row is planted to prove redeem refuses too
            session.add(
                TiqoraApprovalToken(
                    approval_id=aid,
                    approver_id=approver,
                    ticket_id=T1,
                    token_hash=tokens.link_digest(token),
                    created_at=utc_now(),
                    expires_at=utc_now() + timedelta(days=1),
                )
            )
            await session.commit()
            with pytest.raises(ApprovalError) as excinfo:
                await tokens.redeem_link(
                    session, token, decision="approved", comment=None, settings=SETTINGS
                )
            assert excinfo.value.code == "forbidden"
            await session.commit()
            assert (await _tokens(session, approver))[0].used_at is None
            assert await _votes(session, aid) == []
            with pytest.raises(ApprovalError) as excinfo:  # and no new link is issued either
                await _issue(session, approver)
            assert excinfo.value.code == "forbidden"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_token_authorizes_only_its_own_row(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """AG3 is a delegate of AG4 in the same step: AG3's link votes AG3's row, never AG4's."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await delegation.create_delegation(
                session,
                user_id=AG4,
                substitute_user_id=AG3,
                valid_from=utc_now() - timedelta(hours=1),
                valid_until=utc_now() + timedelta(days=1),
                created_by=AG4,
            )
            aid = await _request(session, ALL_TWO)
            mine, delegated = await _approver(session, aid, AG3), await _approver(session, aid, AG4)
            token = await _issue(session, mine)
            await session.commit()
            result = await tokens.redeem_link(
                session, token, decision="approved", comment=None, settings=SETTINGS
            )
            await session.commit()
            assert result.on_behalf is False and result.step_status == "active"
            (vote,) = await _votes(session, aid)
            assert (vote.approver_id, vote.decided_by) == (mine, f"agent:{AG3}")
            statuses = {
                r.id: r.status
                for r in (
                    await session.execute(
                        select(TiqoraApprovalApprover)
                        .where(TiqoraApprovalApprover.approval_id == aid)
                        .execution_options(populate_existing=True)
                    )
                ).scalars()
            }
            assert statuses == {mine: "approved", delegated: "pending"}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_links_switched_off_kill_sent_links(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            approver = await _approver(session, aid, AG3)
            token = await _issue(session, approver)
            await _set_settings(session, email_links_enabled=False)
            await session.commit()
            with pytest.raises(ApprovalError) as excinfo:
                await tokens.redeem_link(
                    session, token, decision="approved", comment=None, settings=SETTINGS
                )
            assert (excinfo.value.code, excinfo.value.http_status) == ("link_invalid", 410)
            await session.rollback()
            assert await _votes(session, aid) == []
            with pytest.raises(ApprovalError) as excinfo:
                await _issue(session, approver, email_links_enabled=False)
            assert excinfo.value.code == "forbidden"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_lock_held_still_locks_the_request(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """decide(lock_held=True) on a fresh session starts with SELECT … FOR UPDATE anyway."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            await session.commit()
            session.expunge_all()
            seen: list[str] = []

            def _capture(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
                seen.append(" ".join(statement.split()).lower())

            bind = session.sync_session.get_bind()
            event.listen(bind, "before_cursor_execute", _capture)
            try:
                await service.decide(
                    session,
                    aid,
                    actor=Actor.agent(AG3),
                    decision="approved",
                    channel="ui",
                    lock_held=True,
                    settings=SETTINGS,
                )
            finally:
                event.remove(bind, "before_cursor_execute", _capture)
            real = [q for q in seen if not q.startswith(("savepoint", "release", "rollback"))]
            assert real[0].startswith("select") and real[0].endswith("for update")
            assert re.search(r"from tiqora_approval(?![_a-z])", real[0])


# --- revocation ----------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_token_dead_after_ui_vote(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ALL_TWO)
            approver = await _approver(session, aid, AG3)
            token = await _issue(session, approver)
            assert (await tokens.resolve_link(session, token)).approver_id == approver
            await service.decide(
                session,
                aid,
                actor=Actor.agent(AG3),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            await _expect(session, token, "link_invalid")
            (row,) = await _tokens(session, approver)
            assert row.revoked_at is not None and row.used_at is None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_token_dead_after_request_cancel(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, TWO_ANY)
            tok3 = await _issue(session, await _approver(session, aid, AG3))
            tok4 = await _issue(session, await _approver(session, aid, AG4))
            await service.cancel_approval(
                session, aid, actor=REQUESTER, check_permission=False, settings=SETTINGS
            )
            await _expect(session, tok3, "link_invalid")
            await _expect(session, tok4, "link_invalid")
            await session.commit()
            with pytest.raises(ApprovalError) as excinfo:
                await tokens.redeem_link(
                    session, tok3, decision="approved", comment=None, settings=SETTINGS
                )
            assert excinfo.value.code == "link_invalid"
            await session.rollback()
            assert await _votes(session, aid) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_token_dead_after_step_decided_by_others(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """AG3 decides a rule-any step; AG4's row is not_needed and its link dies."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, TWO_ANY)
            other = await _approver(session, aid, AG4)
            token = await _issue(session, other)
            await service.decide(
                session,
                aid,
                actor=Actor.agent(AG3),
                decision="approved",
                channel="ui",
                settings=SETTINGS,
            )
            await _expect(session, token, "link_invalid")
            (row,) = await _tokens(session, other)
            assert row.revoked_at is not None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_token_for_forwarded_row_dead(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            row = await _approver(session, aid, AG3)
            token = await _issue(session, row)
            await service.forward(session, row, actor=Actor.agent(AG3), to_user_id=AG5)
            await _expect(session, token, "link_invalid")
            await session.commit()
            with pytest.raises(ApprovalError) as excinfo:
                await tokens.redeem_link(
                    session, token, decision="approved", comment=None, settings=SETTINGS
                )
            assert excinfo.value.code == "link_invalid"
            await session.rollback()
            assert await _votes(session, aid) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_revoke_links_counts_live_only(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, TWO_ANY)
            a3, a4 = await _approver(session, aid, AG3), await _approver(session, aid, AG4)
            await _issue(session, a3)
            await _issue(session, a3)
            keep = await _issue(session, a4)
            assert await tokens.revoke_links(session, approver_ids=[a3]) == 2
            assert await tokens.revoke_links(session, approver_ids=[a3]) == 0
            assert await tokens.revoke_links(session, approver_ids=[]) == 0
            assert (await tokens.resolve_link(session, keep)).approver_id == a4


# --- purge ---------------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_purge_links(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dead rows older than the cutoff go (in batches of 2 here); live and recent ones stay."""
    monkeypatch.setattr(tokens, "_PURGE_BATCH", 2)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, ONE_STEP)
            approver = await _approver(session, aid, AG3)
            now = utc_now().replace(microsecond=0)
            cutoff = now - timedelta(days=30)
            old, recent = now - timedelta(days=40), now - timedelta(days=2)
            specs = {
                "old_used": {"used_at": old, "expires_at": now + timedelta(days=1)},
                "old_revoked": {"revoked_at": old, "expires_at": now + timedelta(days=1)},
                "old_expired": {"expires_at": old},
                "recent_used": {"used_at": recent, "expires_at": now + timedelta(days=1)},
                "recent_revoked": {"revoked_at": recent, "expires_at": now + timedelta(days=1)},
                "recent_expired": {"expires_at": recent},
                "live": {"expires_at": now + timedelta(days=1)},
            }
            ids: dict[str, int] = {}
            for n, (name, fields) in enumerate(specs.items()):
                row = TiqoraApprovalToken(
                    approval_id=aid,
                    approver_id=approver,
                    ticket_id=T1,
                    token_hash=f"{971400 + n:064x}",
                    created_at=old,
                    **fields,
                )
                session.add(row)
                await session.flush()
                ids[name] = row.id
            assert await tokens.purge_links(session, older_than=cutoff) == 3
            left = {t.id for t in await _tokens(session, approver)}
            assert left == {
                ids[n] for n in ("recent_used", "recent_revoked", "recent_expired", "live")
            }
            assert await tokens.purge_links(session, older_than=cutoff) == 0


# --- concurrency ---------------------------------------------------------------------------------

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


async def _redeem_and_commit(factory: async_sessionmaker[AsyncSession], token: str) -> str:
    async with factory() as session:
        await session.rollback()
        try:
            await tokens.redeem_link(
                session, token, decision="approved", comment=None, settings=SETTINGS
            )
        except ApprovalError as exc:
            await session.rollback()
            return exc.code
        except DBAPIError as exc:  # deadlock, lock wait timeout
            await session.rollback()
            return f"db_error:{type(exc.orig).__name__}"
        await session.commit()
        return "ok"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_concurrent_redeem_single_winner(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Two sessions redeem the same link at once: one decision, one link_used, one vote row."""
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            async with factory() as setup:
                aid = await _request(setup, ALL_TWO)
                approver = await _approver(setup, aid, AG3)
                token = await _issue(setup, approver)
                await setup.commit()
            try:
                outcomes = await asyncio.wait_for(
                    asyncio.gather(*(_redeem_and_commit(factory, token) for _ in range(2))),
                    timeout=45,
                )
            except TimeoutError:
                pytest.fail("racing redeems did not finish within 45 s (lock regression?)")
            assert sorted(outcomes) == ["link_used", "ok"]
            async with factory() as check:
                votes = await _votes(check, aid)
                assert [(v.approver_id, v.channel) for v in votes] == [(approver, "email")]
                (row,) = await _tokens(check, approver)
                assert row.used_at is not None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_redeem_races_ui_vote(url_fixture: str, request: pytest.FixtureRequest) -> None:
    """The same approver votes in the UI and via link at once: exactly one vote is stored."""
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type()):
            async with factory() as setup:
                aid = await _request(setup, ALL_TWO)
                token = await _issue(setup, await _approver(setup, aid, AG3))
                await setup.commit()

            async def ui_vote() -> str:
                async with factory() as session:
                    await session.rollback()
                    try:
                        await service.decide(
                            session,
                            aid,
                            actor=Actor.agent(AG3),
                            decision="rejected",
                            channel="ui",
                            settings=SETTINGS,
                        )
                    except ApprovalError as exc:
                        await session.rollback()
                        return exc.code
                    await session.commit()
                    return "ok"

            try:
                outcomes = await asyncio.wait_for(
                    asyncio.gather(_redeem_and_commit(factory, token), ui_vote()), timeout=45
                )
            except TimeoutError:
                pytest.fail("racing decisions did not finish within 45 s (lock regression?)")
            assert outcomes.count("ok") == 1, outcomes
            assert {o for o in outcomes if o != "ok"} <= {
                "link_invalid",
                "link_used",
                "already_decided",
            }
            async with factory() as check:
                assert len(await _votes(check, aid)) == 1
