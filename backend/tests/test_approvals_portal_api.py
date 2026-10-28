"""Customer portal REST of the approval engine (A13 portal rows, ``/api/portal/approvals``).

Band 971850-971899 is reserved for this module; it needs no seeds of its own: the helper world
supplies the agents, the customers (``appr97-k1``/``appr97-k2`` in company ``appr97-firma``,
``appr97-fremd`` in ``appr97-andere``) and the tickets T1 (company ``appr97-firma``) and T2 (no
company). Requests are autoincrement rows inside the rolled-back ``approvals_session``; the race
test commits a world of its own and removes everything again.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._approvals_helpers import (
    _CUSTOMERS,
    AG1,
    AG3,
    ANDERE,
    FIRMA,
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
from tests._fake_redis import FakeRedis
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.api import app as api_app
from tiqora.api.deps import get_db
from tiqora.api.portal.approvals import require_portal_approvals
from tiqora.api.portal.deps import get_current_customer
from tiqora.approvals import service, tokens
from tiqora.approvals.contract import Actor, ApprovalDefinition
from tiqora.approvals.models import TiqoraApprovalApprover, TiqoraApprovalVote
from tiqora.approvals.settings import APPROVALS_SECTION, ApprovalSettings
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.domain.portal_ticket_service import PORTAL_SYSTEM_USER_ID
from tiqora.settings_sections.store import save_section_values

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test", portal_enabled=True)
SUBJECT = "test.subject"
BASE = "/api/portal/approvals"
K1, K2, FREMD = "appr97-k1", "appr97-k2", "appr97-fremd"
_KEYS = itertools.count(1)
NOT_FOUND = {"detail": {"code": "approval_not_found", "detail": None}}


def _cust(login: str) -> dict[str, str]:
    return {"kind": "customer_user", "ref": login}


def _user(uid: int) -> dict[str, str]:
    return {"kind": "user", "ref": str(uid)}


def _defn(*steps: dict[str, Any]) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate({"steps": list(steps)})


def _step(name: str, *approvers: dict[str, str], **extra: Any) -> dict[str, Any]:
    return {"name": name, "approvers": list(approvers), **extra}


async def _set(session: AsyncSession, **values: Any) -> None:
    await save_section_values(session, APPROVALS_SECTION, values, secret_key=SETTINGS.secret_key)


async def _enable(session: AsyncSession) -> None:
    await _set(session, customer_approvers_enabled=True)


async def _request(
    session: AsyncSession, definition: ApprovalDefinition, *, ticket: int = T1, **extra: Any
) -> int:
    args: dict[str, Any] = {
        "approval_key": f"k{next(_KEYS)}",  # one key would supersede the earlier request
        "ticket_id": ticket,
        "subject_type": SUBJECT,
        "title": "Freigabe appr97",
        "description": "Beschreibung appr97",
        "actor": Actor.agent(AG1),
        "definition": definition,
        "settings": SETTINGS,
    }
    args.update(extra)
    return (await service.request_approval(session, **args)).approval_id


def _client(
    session: AsyncSession,
    login: str | None = K2,
    *,
    settings: Settings = SETTINGS,
    bypass_gate: bool = False,
) -> AsyncClient:
    """The real app on *session*, acting as portal customer
    *login* (None: not logged in). Only the customer lookup is overridden: the router's own
    gate (settings, portal switch) runs for real.
    """
    app = api_app.create_app(settings)

    async def _db() -> Any:
        yield session

    app.dependency_overrides[get_db] = _db
    app.state.redis = FakeRedis()
    if bypass_gate:
        app.dependency_overrides[require_portal_approvals] = lambda: None
    if login is not None:
        email, company = _CUSTOMERS[login]
        user = AuthenticatedCustomer(
            id=1, login=login, email=email, customer_id=company, first_name="Kunde", last_name=login
        )
        app.dependency_overrides[get_current_customer] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- gate -----------------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_gate_404_when_disabled(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            aid = await _request(session, _defn(_step("Kunde", _cust(K2))))
            await session.commit()
            paths = [BASE, f"{BASE}/{aid}"]

            async def statuses() -> list[int]:
                async with _client(session) as client:
                    got = [(await client.get(p)).status_code for p in paths]
                    got.append(
                        (
                            await client.post(
                                f"{BASE}/{aid}/decision", json={"decision": "approved"}
                            )
                        ).status_code
                    )
                    return got

            assert await statuses() == [200, 200, 200]
            for off in (
                {"customer_approvers_enabled": False},
                {"portal_approvals_enabled": False},
            ):
                await _set(session, customer_approvers_enabled=True, portal_approvals_enabled=True)
                await _set(session, **off)
                await session.commit()
                assert await statuses() == [404, 404, 404], off
            # the portal itself switched off by the deployment
            await _set(session, customer_approvers_enabled=True, portal_approvals_enabled=True)
            await session.commit()
            async with _client(session, settings=Settings(environment="test")) as client:
                assert (await client.get(BASE)).status_code == 404


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_requires_login(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _enable(session)
        await session.commit()
        async with _client(session, None) as client:
            assert (await client.get(BASE)).status_code == 401


# --- visibility -----------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_lists_only_own_rows(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            mine = await _request(session, _defn(_step("Kunde", _cust(K2))), title="Meine appr97")
            other = await _request(session, _defn(_step("Kunde", _cust(K1))), title="Fremde appr97")
            both = await _request(
                session, _defn(_step("Beide", _cust(K1), _cust(K2), rule="all")), title="Beide"
            )
            await session.commit()
            async with _client(session, K2) as client:
                page = (await client.get(BASE)).json()
                assert sorted(i["id"] for i in page["items"]) == sorted([mine, both])
                assert page["total"] == 2 and (page["limit"], page["offset"]) == (25, 0)
                item = next(i for i in page["items"] if i["id"] == mine)
                assert (item["title"], item["step_name"], item["status"]) == (
                    "Meine appr97",
                    "Kunde",
                    "pending",
                )
                assert (item["my_status"], item["can_decide"]) == ("pending", True)
                assert item["ticket_number"] == f"20261015{T1}"
                # another customer's request: 404, byte for byte what a missing one answers
                hidden = await client.get(f"{BASE}/{other}")
                missing = await client.get(f"{BASE}/999999999")
                assert hidden.status_code == missing.status_code == 404
                assert hidden.json() == missing.json() == NOT_FOUND
                assert hidden.content == missing.content
                # paging
                second = (await client.get(BASE, params={"limit": 1, "offset": 1})).json()
                assert (second["total"], len(second["items"])) == (2, 1)
                assert (await client.get(BASE, params={"limit": 101})).status_code == 422
            async with _client(session, K1) as client:
                ids = {i["id"] for i in (await client.get(BASE)).json()["items"]}
                assert ids == {other, both}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_hides_requests_outside_the_company(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The ticket's company changed after the request: list and detail drop it (A7b), the
    customer's decision is refused with ``customer_approver_outside_company``."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            aid = await _request(session, _defn(_step("Kunde", _cust(K2))))
            await session.execute(
                text("UPDATE ticket SET customer_id = :c WHERE id = :t"), {"c": ANDERE, "t": T1}
            )
            await session.commit()
            async with _client(session, K2) as client:
                assert (await client.get(BASE)).json()["items"] == []
                assert (await client.get(BASE, params={"state": "decided"})).json()["items"] == []
                assert (await client.get(f"{BASE}/{aid}")).json() == NOT_FOUND
            await session.execute(
                text("UPDATE ticket SET customer_id = :c WHERE id = :t"), {"c": FIRMA, "t": T1}
            )
            await session.commit()
            async with _client(session, K2) as client:
                assert [i["id"] for i in (await client.get(BASE)).json()["items"]] == [aid]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_detail_has_no_other_names_or_comments(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            aid = await _request(
                session, _defn(_step("CAB", _user(AG3), _cust(K1), _cust(K2), rule="all"))
            )
            await service.decide(
                session,
                aid,
                actor=Actor.agent(AG3),
                decision="approved",
                channel="ui",
                comment="geheim-agent appr97",
                settings=SETTINGS,
            )
            await service.decide(
                session,
                aid,
                actor=Actor.customer(K1),
                decision="approved",
                channel="portal",
                comment="geheim-kunde appr97",
                settings=SETTINGS,
            )
            await session.commit()
            async with _client(session, K2) as client:
                response = await client.get(f"{BASE}/{aid}")
            assert response.status_code == 200
            body = response.json()
            assert set(body) == {
                "id",
                "title",
                "description",
                "describe_lines",
                "ticket_number",
                "ticket_title",
                "status",
                "step_name",
                "rule_text_key",
                "required",
                "approved_count",
                "due_at",
                "my_status",
                "my_decision",
                "can_decide",
            }
            assert (body["id"], body["title"], body["description"]) == (
                aid,
                "Freigabe appr97",
                "Beschreibung appr97",
            )
            assert (body["step_name"], body["rule_text_key"]) == ("CAB", "approvals.rule.all")
            assert (body["required"], body["approved_count"]) == (3, 2)
            assert (body["my_status"], body["my_decision"], body["can_decide"]) == (
                "pending",
                None,
                True,
            )
            raw = response.text
            for forbidden in (
                "geheim",
                "appr97-agent",  # agent logins and names (requester AG1, approver AG3)
                K1,
                _CUSTOMERS[K1][0],
                _CUSTOMERS[K2][0],
                "decided_by",
                "events",
                "approvers",
            ):
                assert forbidden not in raw, forbidden


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_cannot_see_agent_only_approval(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            aid = await _request(session, _defn(_step("Agenten", _user(AG3))))
            await session.commit()
            async with _client(session, K2) as client:
                assert (await client.get(BASE)).json()["items"] == []
                for state in ("pending", "decided"):
                    got = await client.get(BASE, params={"state": state})
                    assert got.json()["total"] == 0
                response = await client.get(f"{BASE}/{aid}")
                assert (response.status_code, response.json()) == (404, NOT_FOUND)
                decide = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
                assert (decide.status_code, decide.json()) == (404, NOT_FOUND)
                missing = await client.post(
                    f"{BASE}/999999999/decision", json={"decision": "approved"}
                )
                assert missing.content == decide.content
            assert await _votes(session, aid) == []


# --- deciding -------------------------------------------------------------------------------------


async def _votes(session: AsyncSession, approval_id: int) -> list[TiqoraApprovalVote]:
    rows = await session.execute(
        select(TiqoraApprovalVote)
        .where(TiqoraApprovalVote.approval_id == approval_id)
        .order_by(TiqoraApprovalVote.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_decide(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            aid = await _request(session, _defn(_step("Kunde", _cust(K2))))
            await session.commit()
            async with _client(session, K2) as client:
                response = await client.post(
                    f"{BASE}/{aid}/decision", json={"decision": "approved", "comment": "ok appr97"}
                )
                assert response.status_code == 200, response.text
                body = response.json()
                assert (body["status"], body["my_status"], body["my_decision"]) == (
                    "approved",
                    "approved",
                    "approved",
                )
                assert body["can_decide"] is False
                (vote,) = await _votes(session, aid)
                assert (vote.channel, vote.decided_by, vote.decision, vote.comment) == (
                    "portal",
                    f"customer:{K2}",
                    "approved",
                    "ok appr97",
                )
                history = await session.execute(
                    text(
                        "SELECT create_by FROM ticket_history WHERE ticket_id = :t AND name LIKE :n"
                    ),
                    {"t": T1, "n": f"Approval #{aid}: voted%"},
                )
                assert [int(r[0]) for r in history] == [PORTAL_SYSTEM_USER_ID]
                # the request is closed: a second decision is refused, nothing new is stored
                again = await client.post(f"{BASE}/{aid}/decision", json={"decision": "rejected"})
                assert (again.status_code, again.json()["detail"]["code"]) == (
                    409,
                    "approval_closed",
                )
                assert len(await _votes(session, aid)) == 1
                done = (await client.get(BASE, params={"state": "decided"})).json()
                assert [
                    (i["id"], i["my_decision"], i["my_status"], i["can_decide"])
                    for i in done["items"]
                ] == [(aid, "approved", "approved", False)]
                assert (await client.get(f"{BASE}/{aid}")).json()["my_status"] == "approved"
                assert (await client.get(BASE)).json()["items"] == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_decide_outside_company(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """403 whether the request is open or already closed: the state is not an oracle."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            open_id = await _request(session, _defn(_step("Kunde", _cust(K2))))
            closed_id = await _request(session, _defn(_step("Kunde", _cust(K2))), ticket=T1)
            await service.cancel_approval(
                session, closed_id, actor=Actor.agent(AG1), settings=SETTINGS
            )
            await session.execute(
                text("UPDATE ticket SET customer_id = :c WHERE id = :t"), {"c": ANDERE, "t": T1}
            )
            await session.commit()
            async with _client(session, K2) as client:
                for aid in (open_id, closed_id):
                    response = await client.post(
                        f"{BASE}/{aid}/decision", json={"decision": "approved"}
                    )
                    assert response.status_code == 403, response.text
                    assert response.json()["detail"]["code"] == "customer_approver_outside_company"
            assert await _votes(session, open_id) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_decided_tab_pages_in_sql_inside_the_company(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            ids = []
            for _ in range(3):
                aid = await _request(session, _defn(_step("Kunde", _cust(K2))))
                await service.decide(
                    session,
                    aid,
                    actor=Actor.customer(K2),
                    decision="approved",
                    channel="portal",
                    settings=SETTINGS,
                )
                ids.append(aid)
            await session.execute(
                text("UPDATE ticket SET customer_id = :c WHERE id = :t"), {"c": FIRMA, "t": T2}
            )
            elsewhere = await _request(session, _defn(_step("Kunde", _cust(K2))), ticket=T2)
            await service.decide(
                session,
                elsewhere,
                actor=Actor.customer(K2),
                decision="approved",
                channel="portal",
                settings=SETTINGS,
            )
            await session.execute(  # the company is gone again: this one must not be listed
                text("UPDATE ticket SET customer_id = NULL WHERE id = :t"), {"t": T2}
            )
            await session.commit()
            async with _client(session, K2) as client:
                seen: list[int] = []
                for offset in (0, 2):
                    page = (
                        await client.get(
                            BASE, params={"state": "decided", "limit": 2, "offset": offset}
                        )
                    ).json()
                    assert page["total"] == 3  # the ticket without a company is not counted
                    seen += [i["id"] for i in page["items"]]
                assert sorted(seen) == sorted(ids)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_require_portal_approvals_checks_the_portal_switch(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The dependency's own ``portal_enabled()`` branch (the app-level gate would hide it)."""
    async with approvals_session(request, url_fixture, world=False) as session:
        await _enable(session)
        await session.commit()
        await require_portal_approvals(session, SETTINGS)  # on: passes
        with pytest.raises(HTTPException) as refused:
            await require_portal_approvals(session, Settings(environment="test"))
        assert (refused.value.status_code, refused.value.detail) == (404, "Not Found")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_decide_refused_when_setting_turned_off_meanwhile(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The decision-time refusal of the engine is 403 ``forbidden`` (the router gate is
    bypassed here on purpose: it is the race window between gate and lock that is tested)."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            aid = await _request(session, _defn(_step("Kunde", _cust(K2))))
            await _set(session, customer_approvers_enabled=False)
            await session.commit()
            async with _client(session, K2, bypass_gate=True) as client:
                response = await client.post(
                    f"{BASE}/{aid}/decision", json={"decision": "approved"}
                )
            assert (response.status_code, response.json()["detail"]["code"]) == (403, "forbidden")
            assert await _votes(session, aid) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_decision_input_is_strict(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Comment: the engine's ``comment_too_long`` like every surface, checked after trimming;
    no ``approver_id`` (a customer votes their own row)."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            aid = await _request(session, _defn(_step("Kunde", _cust(K1), _cust(K2), rule="all")))
            other_row = (
                await session.execute(
                    select(TiqoraApprovalApprover.id).where(
                        TiqoraApprovalApprover.approval_id == aid,
                        TiqoraApprovalApprover.principal == f"customer:{K1}",
                    )
                )
            ).scalar_one()
            await session.commit()
            url = f"{BASE}/{aid}/decision"
            async with _client(session, K2) as client:
                too_long = await client.post(
                    url, json={"decision": "approved", "comment": "x" * 2001}
                )
                assert too_long.status_code == 422
                assert too_long.json()["detail"]["code"] == "comment_too_long"
                body = {"decision": "approved", "approver_id": int(other_row)}
                assert (await client.post(url, json=body)).status_code == 422
                assert (await client.post(url, json={"decision": "maybe"})).status_code == 422
                padded = {"decision": "approved", "comment": "  " + "x" * 2000 + "  "}
                exact = await client.post(url, json=padded)
                assert exact.status_code == 200
            (vote,) = await _votes(session, aid)
            assert vote.decided_by == f"customer:{K2}"
            assert vote.comment == "x" * 2000


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_uses_the_stored_login_spelling(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A session login in another letter case still finds and decides the stored row."""
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            aid = await _request(session, _defn(_step("Kunde", _cust(K2))))
            await session.commit()
            app = api_app.create_app(SETTINGS)

            async def _db() -> Any:
                yield session

            app.dependency_overrides[get_db] = _db
            shouting = AuthenticatedCustomer(
                id=1,
                login=K2.upper(),
                email=_CUSTOMERS[K2][0],
                customer_id=FIRMA,
                first_name="Kunde",
                last_name=K2,
            )
            app.dependency_overrides[get_current_customer] = lambda: shouting
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert [i["id"] for i in (await client.get(BASE)).json()["items"]] == [aid]
                done = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
                assert done.status_code == 200, done.text
            (vote,) = await _votes(session, aid)
            assert vote.decided_by == f"customer:{K2}"  # canonical stored login


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_other_company_customer_cannot_decide(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            aid = await _request(session, _defn(_step("Kunde", _cust(K2))))
            await session.commit()
            async with _client(session, FREMD) as client:
                assert (await client.get(BASE)).json()["items"] == []
                decide = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
                assert (decide.status_code, decide.json()) == (404, NOT_FOUND)
            assert await _votes(session, aid) == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_ticket_without_company_hides_everything(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _enable(session)
            aid = await _request(session, _defn(_step("Kunde", _cust(K2))), ticket=T2)
            await session.commit()
            async with _client(session, K2) as client:
                assert (await client.get(BASE)).json()["items"] == []
                assert (await client.get(f"{BASE}/{aid}")).json() == NOT_FOUND


# --- F1(c): portal decision against an e-mail link decision -----------------------------------


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
            await _enable(seed)
            await seed.commit()
        yield factory
    finally:
        delete_above_marks(url, marks)
        async with factory() as cleanup:
            await cleanup.execute(
                delete(TiqoraSettings).where(TiqoraSettings.key.like("approvals.%"))
            )
            await delete_world(cleanup)
            await cleanup.commit()
        await engine.dispose()


async def _race(factory: async_sessionmaker[AsyncSession], call: Any) -> tuple[int | str, Any]:
    """One HTTP call on its own session; a database error (deadlock, lock wait) is reported as
    ``"db_error:<name>"`` instead of escaping, so the test can fail with a clear message."""
    try:
        async with factory() as session:
            response = await call(session)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None
    except DBAPIError as exc:
        return f"db_error:{type(exc.orig).__name__}", None


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_portal_and_link_decision_race_has_one_winner(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """F1(c): the customer's portal decision and the decision link of the same approver row are
    sent at once through two HTTP clients (two sessions). Exactly one wins; the other gets a
    clean 4xx refusal (never a 5xx or a database error), and one vote row exists."""
    async with _committed_world(request.getfixturevalue(url_fixture)) as factory:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            async with factory() as setup:
                aid = await _request(setup, _defn(_step("Kunde", _cust(K2), _cust(K1), rule="all")))
                row = (
                    await setup.execute(
                        select(TiqoraApprovalApprover.id).where(
                            TiqoraApprovalApprover.approval_id == aid,
                            TiqoraApprovalApprover.principal == f"customer:{K2}",
                        )
                    )
                ).scalar_one()
                link = await tokens.issue_link(setup, int(row), settings=ApprovalSettings())
                await setup.commit()

            async def portal(session: AsyncSession) -> Any:
                async with _client(session) as client:
                    return await client.post(
                        f"{BASE}/{aid}/decision", json={"decision": "approved"}
                    )

            async def link_call(session: AsyncSession) -> Any:
                async with _client(session, None) as client:
                    return await client.post(
                        "/api/v1/approval-links/decide",
                        json={"token": link, "decision": "approved"},
                    )

            try:
                results = await asyncio.wait_for(
                    asyncio.gather(_race(factory, portal), _race(factory, link_call)), timeout=45
                )
            except TimeoutError:
                pytest.fail("racing decisions did not finish within 45 s (lock regression?)")
            (p_status, p_body), (l_status, l_body) = results
            assert not isinstance(p_status, str), p_status
            assert not isinstance(l_status, str), l_status
            assert sorted([p_status == 200, l_status == 200]) == [False, True], results
            if p_status == 200:  # the link lost: used or dead, never a vote
                assert l_status == 410 and l_body["detail"]["code"] in ("link_used", "link_invalid")
            else:  # the portal lost: its row is already decided
                assert p_status == 409 and p_body["detail"]["code"] == "already_decided"
            async with factory() as check:
                votes = await _votes(check, aid)
                assert [(v.approver_id, v.channel) for v in votes] == [
                    (int(row), "portal" if p_status == 200 else "email")
                ]
