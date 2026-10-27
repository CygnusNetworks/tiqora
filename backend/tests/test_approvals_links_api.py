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
    T1,
    approvals_session,
    installed_registry,
    make_fake_subject_type,
)
from tests._fake_redis import FakeRedis
from tiqora.api import app as api_app
from tiqora.api.deps import get_db
from tiqora.approvals import service, tokens
from tiqora.approvals.contract import Actor, ApprovalDefinition
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalToken,
    TiqoraApprovalVote,
)
from tiqora.approvals.settings import ApprovalSettings
from tiqora.config import Settings
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.jobs.service import utc_now
from tiqora.security.ratelimit import AuthRateLimiter

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
    session: AsyncSession, *users: int, rule: str = "any", user: int = AG3
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
                "ticket_title", "step_name", "rule", "due_at", "describe_lines",
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
            # nothing was written: no vote, the link is not used, the request is pending
            assert await _votes(session, aid) == []
            assert (await _token_row(session, aid)).used_at is None
            assert await _status(session, aid) == "pending"


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
            _, used = await _link(session, user=AG4)
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
            # unknown and expired links cannot be told apart; only "already used" is distinct
            assert unknown.status_code == late.status_code == gone.status_code == 200
            invalid = {
                "valid": False, "code": "link_invalid", "approver_name": None, "title": None,
                "description": None, "ticket_number": None, "ticket_title": None,
                "step_name": None, "rule": None, "due_at": None, "describe_lines": [],
            }  # fmt: skip
            assert unknown.json() == invalid
            assert late.json() == invalid
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
            assert aid


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
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A refused vote (comment too long) leaves the link usable: used_at is rolled back."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid, token = await _link(session)
            async with _client(session) as client:
                refused = await client.post(
                    f"{BASE}/decide",
                    json={"token": token, "decision": "approved", "comment": "x" * 2001},
                )
                retry = await client.post(
                    f"{BASE}/decide", json={"token": token, "decision": "approved"}
                )
            assert refused.status_code == 422
            assert _detail(refused)["code"] == "comment_too_long"
            assert retry.status_code == 200, retry.text
            assert len(await _votes(session, aid)) == 1


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
