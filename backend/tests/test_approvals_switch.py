"""The module switch ``approvals.enabled``: off hides and refuses, deletes nothing (both dialects).

No seeds of its own: the helper world supplies agents and tickets, requests are autoincrement rows
inside the rolled-back ``approvals_session``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastmcp import Client
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests import test_approvals_links_api as links_kit
from tests import test_approvals_mcp as mcp_kit
from tests import test_approvals_portal_api as portal_kit
from tests._approvals_helpers import (
    AG1,
    AG3,
    AG4,
    T1,
    CapturingMail,
    approvals_session,
    as_client,
    installed_registry,
    make_fake_subject_type,
)
from tests._fake_redis import FakeRedis
from tests.approvals_consumer_kit import approve_all, run_hook_now, shared_session_factory
from tiqora.api.v1 import approvals as approvals_api
from tiqora.approvals import service, sweep
from tiqora.approvals.contract import (
    JOB_MAIL,
    Actor,
    ApprovalDefinition,
    ApprovalError,
)
from tiqora.approvals.notify import ApprovalMailPayload, run_approval_mail
from tiqora.approvals.settings import APPROVALS_SECTION, ApprovalSettings, approvals_enabled
from tiqora.config import Settings
from tiqora.jobs.context import JobContext
from tiqora.jobs.service import utc_now
from tiqora.mcp_server.server import mcp
from tiqora.security.ratelimit import ApprovalLinkRateLimiter
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test", public_base_url="https://tiqora.example.org")
SUBJECT = "test.subject"
BASE = "/api/v1/approvals"
DISABLED = {"detail": {"code": "approvals_disabled", "detail": None}}


def _defn(**step: Any) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate(
        {
            "steps": [
                {
                    "name": "Prüfung",
                    "approvers": [{"kind": "user", "ref": str(a)} for a in (AG3, AG4)],
                    **step,
                }
            ]
        }
    )


async def _switch(session: AsyncSession, on: bool, **values: Any) -> None:
    await save_section_values(
        session,
        APPROVALS_SECTION,
        {"enabled": on, **values},
        secret_key=SETTINGS.secret_key,
    )
    await session.commit()


async def _request(session: AsyncSession, **step: Any) -> int:
    info = await service.request_approval(
        session,
        ticket_id=T1,
        subject_type=SUBJECT,
        title="Freigabe appr97",
        actor=Actor.agent(AG1),
        definition=_defn(**step),
        settings=SETTINGS,
    )
    await session.commit()
    return info.approval_id


async def _status(session: AsyncSession, approval_id: int) -> str:
    return str(
        (
            await session.execute(
                text("SELECT status FROM tiqora_approval WHERE id = :a"), {"a": approval_id}
            )
        ).scalar_one()
    )


async def _sweep(session: AsyncSession, now: datetime) -> dict[str, int]:
    await session.commit()
    return await sweep.run_approvals_sweep(
        settings=SETTINGS,
        session_factory=shared_session_factory(session),  # type: ignore[arg-type]
        now=now,
    )


def test_default_is_on_and_the_section_stores_it() -> None:
    assert ApprovalSettings().enabled is True
    assert APPROVALS_SECTION.storage_key("enabled") == "approvals.enabled"


@pytest.mark.parametrize("url_fixture", URLS)
async def test_unset_means_on_and_off_is_read_fresh(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """An upgraded site has no row: on. A switch is seen by the next read, there is no cache."""
    async with approvals_session(request, url_fixture) as session:
        assert await approvals_enabled(session) is True
        await _switch(session, False)
        assert await approvals_enabled(session) is False
        await _switch(session, True)
        assert await approvals_enabled(session) is True


@pytest.mark.parametrize("url_fixture", URLS)
async def test_service_refuses_new_requests_and_writes_nothing(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _switch(session, False)
            with pytest.raises(ApprovalError) as first:
                await _request(session)
            with pytest.raises(ApprovalError) as second:
                await service.record_auto_decision(
                    session,
                    ticket_id=T1,
                    subject_type=SUBJECT,
                    title="Auto appr97",
                    actor=Actor.agent(AG1),
                    status="approved",
                    settings=SETTINGS,
                )
            assert (first.value.code, first.value.http_status) == ("approvals_disabled", 404)
            assert (second.value.code, second.value.http_status) == ("approvals_disabled", 404)
            count = await session.execute(
                text("SELECT COUNT(*) FROM tiqora_approval WHERE ticket_id = :t"), {"t": T1}
            )
            assert count.scalar_one() == 0
            jobs = await session.execute(
                text("SELECT COUNT(*) FROM tiqora_job WHERE kind = :k"), {"k": JOB_MAIL}
            )
            assert jobs.scalar_one() == 0


@pytest.mark.parametrize("url_fixture", URLS)
async def test_every_agent_route_answers_404_but_the_status(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Walks the routers, so a route added later is covered without touching this test."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session)
            await _switch(session, False)
            async with await as_client(session, agent=AG3) as client:
                got = await client.get(f"{BASE}/status")
                assert (got.status_code, got.json()) == (200, {"enabled": False})
                routes = [
                    r
                    for r in (
                        *approvals_api.router.routes,
                        *approvals_api.ticket_router.routes,
                        *approvals_api.admin_readable_router.routes,
                    )
                    if isinstance(r, APIRoute)
                ]
                assert len(routes) >= 15
                for route in routes:
                    path = (
                        route.path.replace("{approval_id:int}", str(approval_id))
                        .replace("{ticket_id}", str(T1))
                        .replace("{delegation_id:int}", "1")
                        .replace("{approver_id:int}", "1")
                    )
                    for method in route.methods:
                        got = await client.request(method, f"/api/v1{path}", json={})
                        assert (got.status_code, got.json()) == (404, DISABLED), (method, path)
            assert await _status(session, approval_id) == "pending"


@pytest.mark.parametrize("url_fixture", URLS)
async def test_on_again_brings_everything_back(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session)
            await _switch(session, False)
            await _switch(session, True)
            async with await as_client(session, agent=AG3) as client:
                assert (await client.get(f"{BASE}/status")).json() == {"enabled": True}
                inbox = await client.get(f"{BASE}/inbox")
                assert [i["approval_id"] for i in inbox.json()["items"]] == [approval_id]
                voted = await client.post(
                    f"{BASE}/{approval_id}/decision", json={"decision": "approved"}
                )
                assert voted.status_code == 200, voted.text


@pytest.mark.parametrize("url_fixture", URLS)
async def test_status_needs_a_login(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _switch(session, False)
        async with links_kit._client(session) as client:
            assert (await client.get(f"{BASE}/status")).status_code == 401
            assert (await client.get(f"{BASE}/inbox")).status_code == 401


@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_routes_answer_the_code(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _switch(session, False, customer_approvers_enabled=True)
        async with portal_kit._client(session) as client:
            got = await client.get("/api/portal/approvals")
            assert (got.status_code, got.json()) == (404, DISABLED)
            got = await client.post(
                "/api/portal/approvals/1/decision", json={"decision": "approved"}
            )
            assert (got.status_code, got.json()) == (404, DISABLED)


@pytest.mark.parametrize("url_fixture", URLS)
async def test_public_links_answer_409_before_the_token_and_count_nothing(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            settings = Settings(environment="test", approval_link_fail_max=1)
            approval_id, token = await links_kit._link(session)
            await _switch(session, False)
            redis = FakeRedis()
            async with links_kit._client(session, redis, settings=settings) as client:
                # an unknown token is no guess while off: far beyond fail_max, still 409
                for sent in (links_kit.BAD, links_kit.BAD, links_kit.BAD, token):
                    for path, extra in (("inspect", {}), ("decide", {"decision": "approved"})):
                        got = await client.post(
                            f"{links_kit.BASE}/{path}", json={"token": sent, **extra}
                        )
                        assert (got.status_code, got.json()) == (409, DISABLED)
            limiter = ApprovalLinkRateLimiter(redis, settings)
            assert (await limiter.check_ip(links_kit.IP_A)).allowed
            assert not await redis.keys("tiqora:approval-link:*")
            assert await _status(session, approval_id) == "pending"

            await _switch(session, True)  # the link survived the pause
            async with links_kit._client(session, redis, settings=settings) as client:
                got = await client.post(f"{links_kit.BASE}/inspect", json={"token": token})
                assert (got.status_code, got.json()["valid"]) == (200, True)


@pytest.mark.parametrize("url_fixture", URLS)
async def test_mcp_tools_refuse(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session)
            await _switch(session, False, mcp_decide_enabled=True)
            async with mcp_kit._as(session, AG3, frozenset({"*"})), Client(mcp) as client:
                for name, args in (
                    ("approval_list_mine", {}),
                    ("approval_get", {"approval_id": approval_id}),
                    ("approval_decide", {"approval_id": approval_id, "decision": "approved"}),
                ):
                    result = await client.call_tool(name, args)
                    assert result.data == {"error": "approvals_disabled"}, name
            assert await _status(session, approval_id) == "pending"


@pytest.mark.parametrize("url_fixture", URLS)
async def test_timers_pause_and_due_ones_run_after_switching_on(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            now = utc_now().replace(microsecond=0)
            overdue = await _request(session, due_hours=1, on_timeout="reject")
            await session.execute(
                text("UPDATE tiqora_approval_step SET due_at = :d WHERE approval_id = :a"),
                {"d": now - timedelta(hours=1), "a": overdue},
            )
            await session.commit()
            await _switch(session, False)

            paused = await _sweep(session, now)
            assert paused["paused"] == 1
            assert (paused["timeout_decided"], paused["reminded"], paused["removed"]) == (0, 0, 0)
            assert await _status(session, overdue) == "pending"

            await _switch(session, True)
            resumed = await _sweep(session, now + timedelta(minutes=1))
            assert resumed["timeout_decided"] == 1
            assert "paused" not in resumed
            assert await _status(session, overdue) == "rejected"


@pytest.mark.parametrize("url_fixture", URLS)
async def test_orphan_is_not_cancelled_while_off(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A request whose ticket is gone keeps its state too; the first tick after it cancels it."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session)
            await session.execute(
                text("UPDATE tiqora_approval SET ticket_id = 999999999 WHERE id = :a"),
                {"a": approval_id},
            )
            await session.commit()
            await _switch(session, False)
            assert (await _sweep(session, utc_now()))["cancelled"] == 0
            assert await _status(session, approval_id) == "pending"
            await _switch(session, True)
            assert (await _sweep(session, utc_now()))["cancelled"] == 1


@pytest.mark.parametrize("url_fixture", URLS)
async def test_link_purge_keeps_running_while_off(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Retention does not pause: a link long past its expiry goes, a live one stays."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            _, _token = await links_kit._link(session)
            now = utc_now().replace(microsecond=0)
            await _switch(session, False)
            assert (await _sweep(session, now))["links_purged"] == 0
            future = now + timedelta(days=400)
            assert (await _sweep(session, future))["links_purged"] == 1


@pytest.mark.parametrize("url_fixture", URLS)
async def test_decided_request_finishes_its_hook_while_off(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        subject = make_fake_subject_type()
        with installed_registry(subject):
            approval_id = await _request(session)
            await approve_all(session, approval_id)
            await session.commit()
            await _switch(session, False)

            result = await run_hook_now(
                shared_session_factory(session),  # type: ignore[arg-type]
                approval_id,
                "approved",
            )
            assert result["hook"] == "called"
            assert [o.approval_id for o in subject.decided] == [approval_id]


@pytest.mark.parametrize("url_fixture", URLS)
async def test_queued_mail_is_not_sent_while_off(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = CapturingMail(monkeypatch)
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            approval_id = await _request(session)
            await _switch(session, False)
            ctx = JobContext(
                job_id=0,
                kind=JOB_MAIL,
                created_by=None,
                settings=SETTINGS,
                session_factory=shared_session_factory(session),  # type: ignore[arg-type]
                attempt=1,
                max_attempts=3,
            )
            result = await run_approval_mail(
                ctx,
                ApprovalMailPayload(
                    approval_id=approval_id,
                    kind="request",
                    approver_ids=[],
                    include_requester=False,
                    include_substitutes=False,
                ),
            )
            assert result is not None and result["note"] == "approvals_disabled"
            assert mail.sent == []


@pytest.mark.parametrize("url_fixture", URLS)
async def test_an_open_request_blocks_nothing_on_its_ticket(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """No ticket write looks at approvals, on or off: state, close and ACL have no block to lift.

    The only blocks of an open request live in the change phases (tested in
    ``test_itsm_change_switch.py``: the whole module answers 404 then).
    """
    from tiqora.domain.ticket_write_service import change_state

    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await _request(session)
            await _switch(session, False)
            closed = (
                await session.execute(
                    text("SELECT id FROM ticket_state WHERE name = 'closed successful'")
                )
            ).scalar_one()
            await change_state(
                session,
                ticket_id=T1,
                new_state_id=int(closed),
                user_id=AG1,
                sysconfig=SysConfig(session),
            )
            state = await session.execute(
                text("SELECT ticket_state_id FROM ticket WHERE id = :t"), {"t": T1}
            )
            assert state.scalar_one() == closed
