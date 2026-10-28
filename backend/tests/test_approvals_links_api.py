"""Public decision-link endpoints (A13 ``/api/v1/approval-links``): inspect, decide, throttles.

Band 971800-971849 is reserved for this module; it needs no seeds of its own (the helper world
supplies agents and tickets, requests/links are autoincrement rows inside the rolled-back
``approvals_session``). The app runs without any user override: the routes are public. Redis is
the in-memory ``FakeRedis``; the client's peer address is the rate-limit identity.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tests._approvals_helpers import (
    AG1,
    AG3,
    AG4,
    AG5,
    ANDERE,
    T1,
    approvals_session,
    installed_registry,
    make_fake_subject_type,
)
from tests._fake_redis import FakeRedis
from tiqora.api import app as api_app
from tiqora.api.deps import get_db
from tiqora.approvals import service, tokens
from tiqora.approvals.contract import Actor, ApprovalDefinition, ApprovalError
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalToken,
    TiqoraApprovalVote,
)
from tiqora.approvals.settings import APPROVALS_SECTION, ApprovalSettings
from tiqora.config import Settings
from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.jobs.service import utc_now
from tiqora.security.ratelimit import AuthRateLimiter
from tiqora.settings_sections.store import save_section_values

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
BASE = "/api/v1/approval-links"
IP_A, IP_B = "203.0.113.10", "203.0.113.11"
BAD = "x" * 43  # a well-formed token nobody issued


def _defn(*steps: dict[str, Any]) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate({"steps": list(steps)})


def _step(name: str, *users: int, **extra: Any) -> dict[str, Any]:
    return {"name": name, "approvers": [{"kind": "user", "ref": str(u)} for u in users], **extra}


def _client(
    session: AsyncSession,
    redis: FakeRedis | None = None,
    *,
    settings: Settings = SETTINGS,
    peer: str = IP_A,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> AsyncClient:
    """The real app, no auth override, the DB on *session*, peer address *peer*."""
    app = api_app.create_app(settings)

    async def _db() -> Any:
        yield session

    app.dependency_overrides[get_db] = _db
    app.state.redis = redis if redis is not None else FakeRedis()
    return AsyncClient(
        transport=ASGITransport(app=app, client=(peer, 4711)),
        base_url="http://test",
        cookies=cookies,
        headers=headers,
    )


async def _link(
    session: AsyncSession, *users: int, rule: str = "any", user: int = AG3, key: str = "default"
) -> tuple[int, str]:
    """A request over *users* (default AG3, AG4, rule any) and a live link of *user*'s row."""
    info = await service.request_approval(
        session,
        ticket_id=T1,
        subject_type=SUBJECT,
        title="Freigabe appr97",
        description="Beschreibung appr97",
        actor=Actor.agent(AG1),
        definition=_defn(_step("Prüfung", *(users or (AG3, AG4)), rule=rule)),
        approval_key=key,
        settings=SETTINGS,
    )
    row = (
        await session.execute(
            select(TiqoraApprovalApprover)
            .where(
                TiqoraApprovalApprover.approval_id == info.approval_id,
                TiqoraApprovalApprover.user_id == user,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    token = await tokens.issue_link(session, row.id, settings=ApprovalSettings())
    await session.commit()  # redeem_link rolls back first: the rows must be committed
    return info.approval_id, token


async def _link_for(session: AsyncSession, approval_id: int, user: int) -> str:
    """A live link of *user*'s row on an existing request."""
    row = (
        await session.execute(
            select(TiqoraApprovalApprover)
            .where(
                TiqoraApprovalApprover.approval_id == approval_id,
                TiqoraApprovalApprover.user_id == user,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    token = await tokens.issue_link(session, row.id, settings=ApprovalSettings())
    await session.commit()
    return token


async def _votes(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalVote]:
    rows = await session.execute(
        select(TiqoraApprovalVote)
        .where(TiqoraApprovalVote.approval_id == approval_id)
        .order_by(TiqoraApprovalVote.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def _status(session: AsyncSession, approval_id: int) -> str:
    return str(
        (
            await session.execute(
                select(TiqoraApproval.status)
                .where(TiqoraApproval.id == approval_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
    )


async def _token_row(session: AsyncSession, approval_id: int) -> TiqoraApprovalToken:
    return (
        await session.execute(
            select(TiqoraApprovalToken)
            .where(TiqoraApprovalToken.approval_id == approval_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


def _detail(response: Any) -> dict[str, Any]:
    body = response.json()["detail"]
    assert isinstance(body, dict)
    return body


# --- inspect ---------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inspect_valid(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, token = await _link(session)
            async with _client(session) as client:
                response = await client.post(f"{BASE}/inspect", json={"token": token})
            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {
                "valid", "code", "approver_name", "title", "description", "ticket_number",
                "ticket_title", "step_name", "rule", "required", "due_at", "describe_lines",
            }  # fmt: skip
            assert (body["valid"], body["code"]) == (True, None)
            assert body["approver_name"] == "Appr appr97-agent3"
            assert (body["title"], body["description"]) == (
                "Freigabe appr97",
                "Beschreibung appr97",
            )
            assert body["ticket_number"] == f"20261015{T1}"
            assert body["ticket_title"]
            assert (body["step_name"], body["rule"], body["due_at"]) == ("Prüfung", "any", None)
            assert body["describe_lines"] == []
            assert body["required"] is None
            # nothing was written: no vote, the link is not used, the request is pending
            assert await _votes(session, aid) == []
            assert (await _token_row(session, aid)).used_at is None
            assert await _status(session, aid) == "pending"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inspect_required_for_quorum_and_majority(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            seen = {}
            for key, rule, extra in (
                ("k-all", "all", {}),
                ("k-quorum", "quorum", {"quorum": 2}),
                ("k-majority", "majority", {}),
            ):
                info = await service.request_approval(
                    session,
                    ticket_id=T1,
                    subject_type=SUBJECT,
                    title="Freigabe appr97",
                    description=None,
                    actor=Actor.agent(AG1),
                    definition=_defn(_step("Prüfung", AG3, AG4, AG5, rule=rule, **extra)),
                    approval_key=key,
                    settings=SETTINGS,
                )
                token = await _link_for(session, info.approval_id, AG3)
                async with _client(session) as client:
                    response = await client.post(f"{BASE}/inspect", json={"token": token})
                assert response.status_code == 200, response.text
                seen[rule] = (response.json()["rule"], response.json()["required"])
            assert seen == {
                "all": ("all", None),
                "quorum": ("quorum", 2),
                "majority": ("majority", 2),
            }


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_link_get_never_decides(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, token = await _link(session)
            async with _client(session) as client:
                for path in ("inspect", "decide", token, f"decide?token={token}"):
                    got = await client.get(f"{BASE}/{path}", params={"decision": "approved"})
                    assert got.status_code in (404, 405), (path, got.status_code)
                await client.post(f"{BASE}/inspect", json={"token": token})
                again = await client.post(f"{BASE}/inspect", json={"token": token})
                assert (again.status_code, again.json()["valid"]) == (200, True)
                assert await _votes(session, aid) == []
                assert await _status(session, aid) == "pending"
                decided = await client.post(
                    f"{BASE}/decide", json={"token": token, "decision": "approved"}
                )
            assert decided.status_code == 200, decided.text
            assert len(await _votes(session, aid)) == 1


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inspect_invalid_codes(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            _, expired = await _link(session)
            _, used = await _link(session, user=AG4, key="zweite-freigabe")
            await session.execute(
                update(TiqoraApprovalToken)
                .where(TiqoraApprovalToken.token_hash == tokens.link_digest(expired))
                .values(expires_at=utc_now() - timedelta(minutes=1))
            )
            await session.commit()
            async with _client(session) as client:
                spent = await client.post(
                    f"{BASE}/decide", json={"token": used, "decision": "rejected"}
                )
                assert spent.status_code == 200, spent.text
                unknown = await client.post(f"{BASE}/inspect", json={"token": BAD})
                late = await client.post(f"{BASE}/inspect", json={"token": expired})
                gone = await client.post(f"{BASE}/inspect", json={"token": used})
            # unknown looks like any dead link; expired and already used are distinct codes
            assert unknown.status_code == late.status_code == gone.status_code == 200
            invalid = {
                "valid": False, "code": "link_invalid", "approver_name": None, "title": None,
                "description": None, "ticket_number": None, "ticket_title": None,
                "step_name": None, "rule": None, "required": None, "due_at": None,
                    "describe_lines": [],
            }  # fmt: skip
            assert unknown.json() == invalid
            assert late.json() == {**invalid, "code": "link_expired"}
            assert gone.json() == {**invalid, "code": "link_used"}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inspect_hides_other_approvers_comments(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, token = await _link(session, rule="all")
            other = await _link_for(session, aid, AG4)
            async with _client(session) as client:
                voted = await client.post(
                    f"{BASE}/decide",
                    json={"token": other, "decision": "approved", "comment": "geheim appr97"},
                )
                assert voted.status_code == 200, voted.text
                response = await client.post(f"{BASE}/inspect", json={"token": token})
            assert (response.status_code, response.json()["valid"]) == (200, True)
            text = json.dumps(response.json(), ensure_ascii=False)
            # no comment, no other approver, no requester (F25)
            assert "geheim" not in text
            assert "agent4" not in text
            assert "agent1" not in text
            assert "agent3" in text  # the caller's own name
            assert len(await _votes(session, aid)) == 1  # only the other approver voted


# --- decide ----------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decide_records_email_channel(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, token = await _link(session)
            async with _client(session) as client:
                response = await client.post(
                    f"{BASE}/decide",
                    json={"token": token, "decision": "approved", "comment": "ok appr97"},
                )
            assert response.status_code == 200, response.text
            assert response.json() == {"decision": "approved", "status": "approved"}
            (vote,) = await _votes(session, aid)
            assert (vote.channel, vote.decision, vote.comment) == ("email", "approved", "ok appr97")
            assert vote.decided_by == f"agent:{AG3}"
            assert await _status(session, aid) == "approved"
            assert (await _token_row(session, aid)).used_at is not None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decide_twice_410(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, token = await _link(session)
            async with _client(session) as client:
                first = await client.post(
                    f"{BASE}/decide", json={"token": token, "decision": "rejected"}
                )
                second = await client.post(
                    f"{BASE}/decide", json={"token": token, "decision": "approved"}
                )
                unknown = await client.post(
                    f"{BASE}/decide", json={"token": BAD, "decision": "approved"}
                )
            assert first.status_code == 200, first.text
            assert second.status_code == 410
            assert second.json() == {"detail": {"code": "link_used", "detail": None}}
            assert unknown.status_code == 410
            assert unknown.json() == {"detail": {"code": "link_invalid", "detail": None}}
            assert len(await _votes(session, aid)) == 1  # the second click changed nothing


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decide_refusal_commits_nothing(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vote the engine refuses after the link was consumed rolls ``used_at`` back."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, token = await _link(session)
            real_decide = service.decide
            refusals = [ApprovalError("already_decided", 409)]

            async def flaky(*args: Any, **kwargs: Any) -> Any:
                if refusals:
                    raise refusals.pop()  # raised after redeem_link consumed the link
                return await real_decide(*args, **kwargs)

            monkeypatch.setattr(service, "decide", flaky)
            async with _client(session) as client:
                refused = await client.post(
                    f"{BASE}/decide", json={"token": token, "decision": "approved"}
                )
                assert (await _token_row(session, aid)).used_at is None
                retry = await client.post(
                    f"{BASE}/decide", json={"token": token, "decision": "approved"}
                )
            assert refused.status_code == 409
            assert _detail(refused)["code"] == "already_decided"
            assert retry.status_code == 200, retry.text
            assert len(await _votes(session, aid)) == 1


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inspect_customer_outside_company_answers_like_decide(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """B-I1: once the ticket left the customer's company, inspect refuses the way the vote is
    refused (403 ``customer_approver_outside_company``) and shows nothing of the request."""
    async with approvals_session(request, url_fixture) as session:
        await save_section_values(
            session,
            APPROVALS_SECTION,
            {"customer_approvers_enabled": True},
            secret_key=SETTINGS.secret_key,
        )
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            info = await service.request_approval(
                session,
                ticket_id=T1,
                subject_type=SUBJECT,
                title="Freigabe appr97",
                description="Beschreibung appr97",
                actor=Actor.agent(AG1),
                definition=_defn(
                    {"name": "Kunde", "approvers": [{"kind": "customer_user", "ref": "appr97-k1"}]}
                ),
                settings=SETTINGS,
            )
            row = (
                await session.execute(
                    select(TiqoraApprovalApprover).where(
                        TiqoraApprovalApprover.approval_id == info.approval_id
                    )
                )
            ).scalar_one()
            token = await tokens.issue_link(session, row.id, settings=ApprovalSettings())
            await session.commit()
            async with _client(session) as client:
                before = await client.post(f"{BASE}/inspect", json={"token": token})
            assert (before.status_code, before.json()["valid"]) == (200, True)

            await session.execute(update(Ticket).where(Ticket.id == T1).values(customer_id=ANDERE))
            await session.commit()
            async with _client(session) as client:
                inspect = await client.post(f"{BASE}/inspect", json={"token": token})
                decide = await client.post(
                    f"{BASE}/decide", json={"token": token, "decision": "approved"}
                )
            for response in (inspect, decide):
                assert response.status_code == 403, response.text
                assert _detail(response) == {
                    "code": "customer_approver_outside_company",
                    "detail": None,
                }
            assert "appr97" not in inspect.text  # neither title nor description
            assert await _votes(session, info.approval_id) == []
            assert (await _token_row(session, info.approval_id)).used_at is None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decide_body_is_strict(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            async with _client(session) as client:
                bad_decision = await client.post(
                    f"{BASE}/decide", json={"token": BAD, "decision": "maybe"}
                )
                too_long = await client.post(f"{BASE}/inspect", json={"token": "t" * 201})
                extra = await client.post(f"{BASE}/inspect", json={"token": BAD, "approver_id": 1})
            assert bad_decision.status_code == too_long.status_code == extra.status_code == 422


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decide_comment_length_is_the_engines(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """M2: ``comment_too_long`` (422) like the agent route, counted after trimming."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, token = await _link(session)
            async with _client(session) as client:
                long_comment = await client.post(
                    f"{BASE}/decide",
                    json={"token": token, "decision": "approved", "comment": "x" * 2001},
                )
                padded = await client.post(
                    f"{BASE}/decide",
                    json={"token": token, "decision": "approved", "comment": f" {'x' * 2000} "},
                )
            assert long_comment.status_code == 422
            assert _detail(long_comment)["code"] == "comment_too_long"
            assert padded.status_code == 200, padded.text
            (vote,) = await _votes(session, aid)
            assert vote.comment == "x" * 2000


# --- rate limit ------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rate_limit_after_failures(url_fixture: str, request: pytest.FixtureRequest) -> None:
    settings = Settings(environment="test", approval_link_fail_max=3)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            _, token = await _link(session)
            redis = FakeRedis()
            async with _client(session, redis, settings=settings) as client:
                for _ in range(3):  # inspect answers 200/valid False but counts as a failure
                    bad = await client.post(f"{BASE}/inspect", json={"token": BAD})
                    assert (bad.status_code, bad.json()["valid"]) == (200, False)
                locked = await client.post(f"{BASE}/inspect", json={"token": BAD})
                decide = await client.post(
                    f"{BASE}/decide", json={"token": BAD, "decision": "approved"}
                )
                real = await client.post(f"{BASE}/inspect", json={"token": token})
            for response in (locked, decide, real):  # the lock precedes any work
                assert response.status_code == 429
                assert _detail(response)["code"] == "rate_limited"
                assert int(response.headers["retry-after"]) > 0


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rate_limit_is_per_ip_and_separate_from_login(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    settings = Settings(environment="test", approval_link_fail_max=2)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            _, token = await _link(session)
            redis = FakeRedis()
            async with _client(session, redis, settings=settings, peer=IP_A) as client_a:
                for _ in range(2):
                    await client_a.post(f"{BASE}/inspect", json={"token": BAD})
                blocked = await client_a.post(f"{BASE}/inspect", json={"token": token})
            async with _client(session, redis, settings=settings, peer=IP_B) as client_b:
                other = await client_b.post(f"{BASE}/inspect", json={"token": token})
            assert blocked.status_code == 429
            assert (other.status_code, other.json()["valid"]) == (200, True)
            # the agent login limiter shares nothing with it: neither IP nor a login is locked
            login = AuthRateLimiter(redis, settings)
            assert (await login.check(login="appr97-agent3", ip=IP_A)).allowed
            assert (await login.check(login="appr97-agent1", ip=IP_B)).allowed


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rate_limit_by_token_hash_across_ips(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Rotating the source address does not reset the budget of one link."""
    settings = Settings(environment="test", approval_link_fail_max=3)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            _, token = await _link(session)
            redis = FakeRedis()
            codes: list[int] = []
            for n in range(5):
                async with _client(session, redis, settings=settings, peer=f"198.51.100.{n}") as c:
                    codes.append(
                        (await c.post(f"{BASE}/inspect", json={"token": token})).status_code
                    )
            assert codes == [200, 200, 200, 429, 429]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_used_link_does_not_feed_the_ip_lock_and_locked_ip_spares_the_budget(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    settings = Settings(environment="test", approval_link_fail_max=2)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            _, token = await _link(session)
            redis = FakeRedis()
            async with _client(session, redis, settings=settings) as client:
                await client.post(f"{BASE}/decide", json={"token": token, "decision": "approved"})
                fail_key = "tiqora:approval-link:fail:ip:" + IP_A
                await client.post(f"{BASE}/inspect", json={"token": token})  # link_used
                assert await redis.get(fail_key) is None  # a spent link is no guess
                for _ in range(2):
                    await client.post(f"{BASE}/inspect", json={"token": BAD})
                budget_key = "tiqora:approval-link:rl:token:" + tokens.link_digest(BAD)
                before = await redis.get(budget_key)
                locked = await client.post(f"{BASE}/inspect", json={"token": BAD})
            assert locked.status_code == 429
            assert await redis.get(budget_key) == before  # the lock answered before the budget


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_known_dead_links_do_not_feed_the_ip_lock(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """M3: revoked (another approver decided) and expired links are known hashes, not guesses:
    one company NAT opening yesterday's mail must not lock its live links."""
    settings = Settings(environment="test", approval_link_fail_max=2)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, revoked = await _link(session)  # rule any over AG3, AG4: AG4 decides
            await service.decide(
                session, aid, actor=Actor.agent(AG4), decision="approved", channel="ui",
                settings=SETTINGS,
            )  # fmt: skip
            _, expired = await _link(session, key="zweite-freigabe")
            await session.execute(
                update(TiqoraApprovalToken)
                .where(TiqoraApprovalToken.token_hash == tokens.link_digest(expired))
                .values(expires_at=utc_now() - timedelta(minutes=1))
            )
            await session.commit()
            _, live = await _link(session, key="dritte-freigabe")
            redis = FakeRedis()
            fail_key = "tiqora:approval-link:fail:ip:" + IP_A
            async with _client(session, redis, settings=settings) as client:
                for _ in range(2):  # four dead-link answers: twice the IP limit, each link's budget
                    gone = await client.post(f"{BASE}/inspect", json={"token": revoked})
                    late = await client.post(
                        f"{BASE}/decide", json={"token": expired, "decision": "approved"}
                    )
                    assert gone.json()["code"] == "link_invalid"
                    assert _detail(late)["code"] == "link_expired"
                assert await redis.get(fail_key) is None
                ok = await client.post(f"{BASE}/inspect", json={"token": live})
                assert (ok.status_code, ok.json()["valid"]) == (200, True)
                await client.post(f"{BASE}/inspect", json={"token": BAD})  # unknown: counts
                assert int(await redis.get(fail_key)) == 1


async def test_counter_without_ttl_heals() -> None:
    from tiqora.security.ratelimit import ApprovalLinkRateLimiter

    redis = FakeRedis()
    limiter = ApprovalLinkRateLimiter(redis, Settings(environment="test"))
    await limiter.record_auth_failure(IP_A)
    key = "tiqora:approval-link:fail:ip:" + IP_A
    redis.drop_ttl(key)  # a crash between INCR and EXPIRE
    assert await redis.ttl(key) == -1
    await limiter.record_auth_failure(IP_A)
    assert await redis.ttl(key) > 0


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rate_limit_switch_and_redis_outage(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    off = Settings(environment="test", approval_link_fail_max=1, auth_rate_limit_enabled=False)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            _, token = await _link(session)
            async with _client(session, FakeRedis(), settings=off) as client:
                codes = [
                    (await client.post(f"{BASE}/inspect", json={"token": BAD})).status_code
                    for _ in range(4)
                ]
            assert codes == [200, 200, 200, 200]
            down = FakeRedis()
            down.fail = True  # soft-fail open: a Redis outage must not block a decision
            async with _client(session, down) as client:
                response = await client.post(
                    f"{BASE}/decide", json={"token": token, "decision": "approved"}
                )
            assert response.status_code == 200, response.text


# --- CSRF, auth, scopes, logging ------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_csrf_same_origin_with_cookie_ok(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    cookies = {SETTINGS.session_cookie_name: "stale-agent-session"}
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            _, token = await _link(session)
            async with _client(session, cookies=cookies) as client:
                same = await client.post(
                    f"{BASE}/inspect", json={"token": token}, headers={"Origin": "http://test"}
                )
                foreign = await client.post(
                    f"{BASE}/inspect",
                    json={"token": token},
                    headers={"Origin": "https://evil.example.org"},
                )
            assert (same.status_code, same.json()["valid"]) == (200, True)
            assert foreign.status_code == 403


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_no_auth_required(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            _, token = await _link(session)
            # no cookie and no Authorization header: 200, not 401
            async with _client(session) as client:
                anonymous = await client.post(f"{BASE}/inspect", json={"token": token})
            # a stray API-key header of a scoped key does not turn a link click into 403
            stray = {"Authorization": "Bearer tiqora_stray_key"}
            async with _client(session, headers=stray) as client:
                with_header = await client.post(f"{BASE}/inspect", json={"token": token})
            assert anonymous.status_code == with_header.status_code == 200
    assert path_to_area(f"{BASE}/inspect") is None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_token_not_logged(
    url_fixture: str, request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    settings = Settings(environment="test", approval_link_fail_max=1)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            _, token = await _link(session)
            with structlog.testing.capture_logs() as captured:
                async with _client(session, settings=settings) as client:
                    await client.post(f"{BASE}/inspect", json={"token": BAD})
                    await client.post(f"{BASE}/decide", json={"token": BAD, "decision": "approved"})
                    await client.post(f"{BASE}/inspect", json={"token": token})
                async with _client(session, peer=IP_B) as client:
                    await client.post(f"{BASE}/inspect", json={"token": token})
                    await client.post(
                        f"{BASE}/decide", json={"token": token, "decision": "approved"}
                    )
                    await client.post(
                        f"{BASE}/decide", json={"token": token, "decision": "approved"}
                    )
            everything = caplog.text + json.dumps(captured, default=str)
            for secret in (token, BAD, tokens.link_digest(token), tokens.link_digest(BAD)):
                assert secret not in everything
            assert captured  # the lock was logged (ids only), so the check above saw real records
