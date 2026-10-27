"""Agent REST API of the approval engine (A13): inbox, detail, decisions, ticket approvals.

Own ids 971700-971799: ``ADMIN`` is the one extra agent (member of the ``admin`` group); the
helper world supplies everything else. Every test runs inside the rolled-back
``approvals_session`` (the routes' commit/rollback act on a savepoint).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
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
    as_client,
    installed_registry,
    make_fake_subject_type,
)
from tests._itsm_adapter_helpers import seed_user
from tiqora.api import app as api_app
from tiqora.api.v1 import approvals as approvals_api
from tiqora.approvals import delegation, lists, service, templates
from tiqora.approvals.contract import Actor, ApprovalDefinition, ApprovalError, Principal
from tiqora.approvals.models import TiqoraApproval
from tiqora.approvals.settings import APPROVALS_SECTION
from tiqora.config import Settings
from tiqora.domain.api_key_scopes import assert_scope_allows, path_to_area
from tiqora.domain.auth import AuthenticatedUser
from tiqora.jobs.service import utc_now
from tiqora.settings_sections.store import save_section_values

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
ADMIN = 971701
ADMIN_GROUP = 971702
BASE = "/api/v1/approvals"


def _user(uid: int) -> dict[str, str]:
    return {"kind": "user", "ref": str(uid)}


def _step(name: str, *approvers: dict[str, str], **extra: Any) -> dict[str, Any]:
    return {"name": name, "approvers": list(approvers), **extra}


def _defn(*steps: dict[str, Any]) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate({"steps": list(steps)})


async def _request(
    session: AsyncSession,
    *,
    approvers: tuple[int, ...] = (AG3, AG4),
    requester: int = AG1,
    ticket: int = T1,
    key: str = "default",
    **step_extra: Any,
) -> int:
    info = await service.request_approval(
        session,
        ticket_id=ticket,
        subject_type=SUBJECT,
        title="Freigabe appr97",
        description="Beschreibung appr97",
        actor=Actor.agent(requester),
        definition=_defn(_step("Prüfung", *(_user(a) for a in approvers), **step_extra)),
        approval_key=key,
        settings=SETTINGS,
    )
    await session.commit()  # the routes roll back first: seeded rows must be committed
    return info.approval_id


async def _seed_admin(session: AsyncSession) -> None:
    await seed_user(session, ADMIN, "appr97-admin", "Appr", "Admin")
    group_id = (
        await session.execute(
            text("SELECT id FROM permission_groups WHERE name = 'admin' AND valid_id = 1")
        )
    ).scalar_one_or_none()
    if group_id is None:
        group_id = ADMIN_GROUP
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
    await session.commit()


async def _set(session: AsyncSession, **values: Any) -> None:
    await save_section_values(session, APPROVALS_SECTION, values, secret_key=SETTINGS.secret_key)
    await session.commit()


def _code(response: Any) -> str:
    return str(response.json()["detail"]["code"])


class Published:
    """Replaces the SSE publish of the routes; records commits and publishes in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
        self.log: list[tuple[str, frozenset[int]]] = []
        original = session.commit

        async def commit() -> None:
            self.log.append(("commit", frozenset()))
            await original()

        async def publish(_redis: Any, user_ids: Any) -> None:
            self.log.append(("publish", frozenset(user_ids)))

        monkeypatch.setattr(session, "commit", commit)
        monkeypatch.setattr(approvals_api, "get_pubsub_redis", lambda _settings: None)
        monkeypatch.setattr(approvals_api, "publish_approval_changed", publish)

    @property
    def publishes(self) -> list[frozenset[int]]:
        return [users for kind, users in self.log if kind == "publish"]

    def clear(self) -> None:
        self.log.clear()


def _gated_client(session: AsyncSession, user: AuthenticatedUser) -> AsyncClient:
    """The routes with *user* as caller and the API-key scope gate of ``get_current_user``."""
    from tiqora.api.deps import get_current_user, get_db

    async def _db() -> Any:
        yield session

    async def _current(request: Request) -> AuthenticatedUser:
        if user.api_key_scopes is not None:
            assert_scope_allows(user.api_key_scopes, method=request.method, path=request.url.path)
        return user

    app = api_app.create_app(SETTINGS)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _current
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- inbox -----------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inbox_and_count(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            async with await as_client(session, agent=AG3) as client:
                page = await client.get(f"{BASE}/inbox")
                count = await client.get(f"{BASE}/inbox/count")
                done = await client.get(f"{BASE}/inbox", params={"state": "decided"})
            async with await as_client(session, agent=AG5) as client:
                stranger = await client.get(f"{BASE}/inbox/count")
                too_many = await client.get(f"{BASE}/inbox", params={"limit": 101})
            assert page.status_code == 200
            body = page.json()
            assert (body["total"], body["limit"], body["offset"]) == (1, 25, 0)
            item = body["items"][0]
            assert (item["approval_id"], item["ticket_id"], item["title"]) == (
                aid,
                T1,
                "Freigabe appr97",
            )
            assert (item["step_name"], item["rule"], item["required"]) == ("Prüfung", "any", 1)
            assert (item["can_vote"], item["blocked_reason"], item["on_behalf_of_label"]) == (
                True,
                None,
                None,
            )
            assert item["ticket_number"] == f"20261015{T1}"
            assert count.json() == {"pending": 1}
            assert done.json()["total"] == 0
            assert stranger.json() == {"pending": 0}
            assert too_many.status_code == 422

            async with await as_client(session, agent=AG3) as client:
                voted = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
                assert voted.status_code == 200, voted.text
                after = await client.get(f"{BASE}/inbox/count")
                decided = await client.get(f"{BASE}/inbox", params={"state": "decided"})
            async with await as_client(session, agent=AG4) as client:
                other = await client.get(f"{BASE}/inbox/count")
            assert after.json() == {"pending": 0}
            assert other.json() == {"pending": 0}  # the step is decided
            row = decided.json()["items"][0]
            assert (row["my_decision"], row["status"], row["can_vote"]) == (
                "approved",
                "approved",
                False,
            )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_inbox_delegated_row_names_the_delegator(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            await delegation.create_delegation(
                session,
                user_id=AG3,
                substitute_user_id=AG5,
                valid_from=utc_now() - timedelta(minutes=1),
                valid_until=utc_now() + timedelta(days=1),
                created_by=AG3,
            )
            await session.commit()
            await _request(session, approvers=(AG3,))
            async with await as_client(session, agent=AG5) as client:
                item = (await client.get(f"{BASE}/inbox")).json()["items"][0]
            assert item["on_behalf_of_label"] == "Appr appr97-agent3"
            assert item["can_vote"] is True


# --- detail ----------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_detail_visibility(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            async with await as_client(session, agent=AG3) as client:
                approver = await client.get(f"{BASE}/{aid}")
            async with await as_client(session, agent=AG2) as client:
                reader = await client.get(f"{BASE}/{aid}")
            async with await as_client(session, agent=AG7) as client:
                stranger = await client.get(f"{BASE}/{aid}")
                missing = await client.get(f"{BASE}/{aid + 1000}")
            # an approver without ro on the ticket sees the request, but gets no ticket link
            assert approver.status_code == 200
            assert approver.json()["ticket_link"] is False
            assert approver.json()["ticket_number"] == f"20261015{T1}"
            assert reader.status_code == 200 and reader.json()["ticket_link"] is True
            assert stranger.status_code == 404
            assert stranger.json() == {"detail": {"code": "approval_not_found", "detail": None}}
            assert _code(missing) == "approval_not_found"

            data = approver.json()
            assert (data["status"], data["subject_type"], data["current_stage"]) == (
                "pending",
                SUBJECT,
                1,
            )
            assert data["requester_label"] == "Appr appr97-agent1"
            assert data["description"] == "Beschreibung appr97"
            step = data["steps"][0]
            assert (step["name"], step["rule"], step["required"], step["status"]) == (
                "Prüfung",
                "any",
                1,
                "active",
            )
            assert [a["display_name"] for a in step["approvers"]] == [
                "Appr appr97-agent3",
                "Appr appr97-agent4",
            ]
            assert [e["action"] for e in data["events"]][0] == "requested"
            assert data["events"][0]["actor_label"] == "Appr appr97-agent1"
            assert [r["approver_id"] for r in data["my_rows"]] == [step["approvers"][0]["id"]]
            assert data["my_rows"][0]["can_forward"] is True
            assert (data["can_cancel"], data["can_refresh"]) == (False, False)
            assert reader.json()["my_rows"] == []


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_detail_hides_nothing_needed_but_no_articles(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            async with await as_client(session, agent=AG1) as client:
                data = (await client.get(f"{BASE}/{aid}")).json()
            assert set(data) == {
                "id", "ticket_id", "ticket_number", "ticket_title", "ticket_link",
                "subject_type", "subject_ref", "approval_key", "title", "description",
                "describe_lines", "status", "reason", "current_stage", "requester_label",
                "created_at", "decided_at", "steps", "events", "my_rows", "can_cancel",
                "can_refresh",
            }  # fmt: skip
            assert set(data["steps"][0]) == {
                "id", "position", "stage", "name", "rule", "quorum", "required",
                "approved_count", "status", "reason", "due_at", "overdue", "approvers",
            }  # fmt: skip
            assert set(data["steps"][0]["approvers"][0]) == {
                "id", "kind", "display_name", "source_kind", "source_label", "decisive",
                "status", "vote",
            }  # fmt: skip
            assert (data["can_cancel"], data["can_refresh"]) == (True, True)  # the requester


# --- decide ----------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decision_endpoint(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        published = Published(monkeypatch, session)
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            published.clear()
            async with await as_client(session, agent=AG3) as client:
                response = await client.post(
                    f"{BASE}/{aid}/decision", json={"decision": "approved", "comment": "ok"}
                )
            assert response.status_code == 200
            data = response.json()
            assert (data["status"], data["steps"][0]["status"]) == ("approved", "approved")
            vote = data["steps"][0]["approvers"][0]["vote"]
            assert (vote["decision"], vote["comment"], vote["on_behalf"], vote["channel"]) == (
                "approved",
                "ok",
                False,
                "ui",
            )
            assert vote["decided_by_label"] == "Appr appr97-agent3"
            assert data["steps"][0]["approved_count"] == 1
            assert data["my_rows"] == []
            # SSE goes out after the commit, to the approvers of the request
            assert [kind for kind, _ in published.log] == ["commit", "publish"]
            assert {AG3, AG4} <= published.publishes[0]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_decision_errors_shape(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            # AG3 names the requester AG1 as substitute: AG1 may not vote in AG3's place
            await delegation.create_delegation(
                session,
                user_id=AG3,
                substitute_user_id=AG1,
                valid_from=utc_now() - timedelta(minutes=1),
                valid_until=None,
                created_by=AG3,
            )
            own = await _request(session, approvers=(AG3,), requester=AG1, key="own")
            aid = await _request(session, key="other")
            async with await as_client(session, agent=AG1) as client:
                self_vote = await client.post(
                    f"{BASE}/{own}/decision", json={"decision": "approved"}
                )
            async with await as_client(session, agent=AG5) as client:
                stranger = await client.post(
                    f"{BASE}/{aid}/decision", json={"decision": "approved"}
                )
                missing = await client.post(
                    f"{BASE}/{aid + 1000}/decision", json={"decision": "approved"}
                )
            async with await as_client(session, agent=AG3) as client:
                long_comment = await client.post(
                    f"{BASE}/{aid}/decision", json={"decision": "approved", "comment": "x" * 2001}
                )
                bad_value = await client.post(f"{BASE}/{aid}/decision", json={"decision": "maybe"})
                first = await client.post(f"{BASE}/{aid}/decision", json={"decision": "rejected"})
            async with await as_client(session, agent=AG4) as client:
                late = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
            assert self_vote.status_code == 403
            assert self_vote.json()["detail"]["code"] == "self_approval_forbidden"
            assert set(self_vote.json()["detail"]) == {"code", "detail"}
            assert (stranger.status_code, _code(stranger)) == (404, "approval_not_found")
            assert (missing.status_code, _code(missing)) == (404, "approval_not_found")
            assert (long_comment.status_code, _code(long_comment)) == (422, "comment_too_long")
            assert bad_value.status_code == 422
            assert first.status_code == 200 and first.json()["status"] == "rejected"
            assert (late.status_code, _code(late)) == (409, "approval_closed")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_customer_approver_refused_while_setting_is_off(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type(allow_customer_approvers=True)):
            await _set(session, customer_approvers_enabled=True)
            info = await service.request_approval(
                session,
                ticket_id=T1,
                subject_type=SUBJECT,
                title="Kunde appr97",
                actor=Actor.agent(AG1),
                definition=_defn(_step("Kunde", {"kind": "customer_user", "ref": "appr97-k1"})),
                settings=SETTINGS,
            )
            await _set(session, customer_approvers_enabled=False)
            # the portal decides through its own routes (Task 15); the engine's refusal is
            # what the agent API maps: 403 forbidden with the project's detail body
            with pytest.raises(ApprovalError) as excinfo:
                await service.decide(
                    session,
                    info.approval_id,
                    actor=Actor.customer("appr97-k1"),
                    decision="approved",
                    channel="portal",
                    settings=SETTINGS,
                )
            mapped = approvals_api._http(excinfo.value)
            assert mapped.status_code == 403
            assert mapped.detail == {"code": "forbidden", "detail": excinfo.value.detail}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_explicit_approver_id_of_other_agent_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            elsewhere = await _request(session, key="elsewhere")
            async with await as_client(session, agent=AG1) as client:
                detail = (await client.get(f"{BASE}/{aid}")).json()
                foreign = (await client.get(f"{BASE}/{elsewhere}")).json()
            ag4_row = detail["steps"][0]["approvers"][1]["id"]
            foreign_row = foreign["steps"][0]["approvers"][0]["id"]
            async with await as_client(session, agent=AG3) as client:
                other_agent = await client.post(
                    f"{BASE}/{aid}/decision", json={"decision": "approved", "approver_id": ag4_row}
                )
                other_approval = await client.post(
                    f"{BASE}/{aid}/decision",
                    json={"decision": "approved", "approver_id": foreign_row},
                )
                still_open = await client.get(f"{BASE}/{aid}")
            assert (other_agent.status_code, _code(other_agent)) == (403, "not_an_approver")
            assert (other_approval.status_code, _code(other_approval)) == (403, "not_an_approver")
            assert still_open.json()["status"] == "pending"


# --- forward, cancel, refresh ----------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_forward_cancel_refresh_endpoints(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        published = Published(monkeypatch, session)
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session, approvers=(AG3, AG4))
            other = await _request(session, approvers=(AG3,), key="second")
            async with await as_client(session, agent=AG1) as client:
                before = (await client.get(f"{BASE}/{aid}")).json()
                other_before = (await client.get(f"{BASE}/{other}")).json()
            ag3_row = before["steps"][0]["approvers"][0]["id"]
            ag3_other_row = other_before["steps"][0]["approvers"][0]["id"]

            async with await as_client(session, agent=AG3) as client:
                wrong_path = await client.post(
                    f"{BASE}/{aid}/approvers/{ag3_other_row}/forward", json={"user_id": AG5}
                )
                no_row = await client.post(
                    f"{BASE}/{aid}/approvers/{ag3_row + 5000}/forward", json={"user_id": AG5}
                )
                to_self = await client.post(
                    f"{BASE}/{aid}/approvers/{ag3_row}/forward", json={"user_id": AG3}
                )
                published.clear()
                forwarded = await client.post(
                    f"{BASE}/{aid}/approvers/{ag3_row}/forward", json={"user_id": AG5}
                )
            assert (wrong_path.status_code, _code(wrong_path)) == (403, "not_an_approver")
            assert (no_row.status_code, _code(no_row)) == (403, "not_an_approver")
            assert (to_self.status_code, _code(to_self)) == (422, "forward_target_invalid")
            assert forwarded.status_code == 200
            rows = forwarded.json()["steps"][0]["approvers"]
            assert [(r["display_name"], r["status"], r["source_kind"]) for r in rows] == [
                ("Appr appr97-agent3", "forwarded", "user"),
                ("Appr appr97-agent4", "pending", "user"),
                ("Appr appr97-agent5", "pending", "forward"),
            ]
            assert forwarded.json()["my_rows"] == []
            assert [k for k, _ in published.log] == ["commit", "publish"]
            assert {AG3, AG5} <= published.publishes[0]

            async with await as_client(session, agent=AG5) as client:
                mine = (await client.get(f"{BASE}/{aid}")).json()["my_rows"]
            assert [(r["can_forward"]) for r in mine] == [False]  # a forward row stays put

            # refresh: rw on the ticket only
            async with await as_client(session, agent=AG2) as client:
                refused = await client.post(f"{BASE}/{aid}/refresh-approvers")
            async with await as_client(session, agent=AG1) as client:
                published.clear()
                refreshed = await client.post(f"{BASE}/{aid}/refresh-approvers")
            assert (refused.status_code, _code(refused)) == (403, "forbidden")
            assert refreshed.status_code == 200 and refreshed.json()["status"] == "pending"
            assert [k for k, _ in published.log] == ["commit", "publish"]

            # cancel: an approver without rw on the ticket may not, the requester may
            async with await as_client(session, agent=AG4) as client:
                denied = await client.post(f"{BASE}/{aid}/cancel")
            async with await as_client(session, agent=AG1) as client:
                published.clear()
                cancelled = await client.post(f"{BASE}/{aid}/cancel")
                again = await client.post(f"{BASE}/{aid}/cancel")
                body_refused = await client.post(f"{BASE}/{other}/cancel", json={"note": "x"})
            assert (denied.status_code, _code(denied)) == (403, "forbidden")
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            assert cancelled.json()["steps"][0]["status"] == "cancelled"
            assert (again.status_code, _code(again)) == (409, "approval_closed")
            assert [k for k, _ in published.log][:2] == ["commit", "publish"]
            assert {AG1, AG3, AG4, AG5} <= published.publishes[0]  # requester + all approvers
            assert body_refused.status_code == 200  # no body model: the note is simply ignored


# --- on a ticket -----------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_ticket_approvals_list_needs_ro(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            first = await _request(session, key="first")
            second = await _request(session, key="second", approvers=(AG2,))
            await _request(session, ticket=T2, approvers=(AG3,))  # another ticket: not listed
            async with await as_client(session, agent=AG2) as client:
                ok = await client.get(f"/api/v1/tickets/{T1}/approvals")
            async with await as_client(session, agent=AG5) as client:
                denied = await client.get(f"/api/v1/tickets/{T1}/approvals")
            async with await as_client(session, agent=AG1) as client:
                requester = await client.get(f"/api/v1/tickets/{T1}/approvals")
            assert ok.status_code == 200
            assert [r["id"] for r in ok.json()] == [second, first]  # newest first
            # AG2 is an approver of the newer request and has only ro
            assert [(r["my_pending"], r["can_cancel"]) for r in ok.json()] == [
                (True, False),
                (False, False),
            ]
            assert [(r["my_pending"], r["can_cancel"]) for r in requester.json()] == [
                (False, True),
                (False, True),
            ]
            assert (denied.status_code, _code(denied)) == (404, "ticket_not_found")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_ticket_request_needs_rw(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        published = Published(monkeypatch, session)
        with installed_registry():
            body = {
                "title": "Freigabe appr97",
                "description": "Bitte prüfen",
                "definition": {"steps": [_step("Chef", _user(AG3))]},
            }
            published.clear()
            async with await as_client(session, agent=AG2) as client:
                ro = await client.post(f"/api/v1/tickets/{T1}/approvals", json=body)
            async with await as_client(session, agent=AG5) as client:
                none = await client.post(f"/api/v1/tickets/{T1}/approvals", json=body)
            async with await as_client(session, agent=AG1) as client:
                created = await client.post(f"/api/v1/tickets/{T1}/approvals", json=body)
                missing_ticket = await client.post("/api/v1/tickets/971799/approvals", json=body)
            assert (ro.status_code, _code(ro)) == (403, "forbidden")
            assert (none.status_code, _code(none)) == (404, "ticket_not_found")
            assert (missing_ticket.status_code, _code(missing_ticket)) == (404, "ticket_not_found")
            assert created.status_code == 201
            data = created.json()
            assert (data["subject_type"], data["status"], data["approval_key"]) == (
                "ticket",
                "pending",
                "default",
            )
            assert data["requester_label"] == "Appr appr97-agent1"
            assert data["steps"][0]["approvers"][0]["display_name"] == "Appr appr97-agent3"
            assert data["can_cancel"] is True
            assert [k for k, _ in published.log] == ["commit", "publish"]
            assert {AG1, AG3} <= published.publishes[0]


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_ticket_request_with_template_and_invalid_input(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry():
            await templates.create_template(
                session,
                key="appr97.tpl",
                name="Vorlage appr97",
                definition=_defn(_step("Vorlage", _user(AG4))),
                subject_types=["ticket"],
                actor_id=AG1,
            )
            await session.commit()
            url = f"/api/v1/tickets/{T1}/approvals"
            async with await as_client(session, agent=AG1) as client:
                via_template = await client.post(
                    url, json={"title": "Aus Vorlage", "template_key": "appr97.tpl"}
                )
                unknown_template = await client.post(
                    url, json={"title": "x", "template_key": "appr97.nope"}
                )
                neither = await client.post(url, json={"title": "x"})
                both = await client.post(
                    url,
                    json={
                        "title": "x",
                        "template_key": "appr97.tpl",
                        "definition": {"steps": [_step("S", _user(AG3))]},
                    },
                )
                auto_approve = await client.post(
                    url,
                    json={
                        "title": "x",
                        "definition": {"steps": [_step("S", _user(AG3), on_no_approver="approve")]},
                    },
                )
                timeout_approve = await client.post(
                    url,
                    json={
                        "title": "x",
                        "definition": {
                            "steps": [_step("S", _user(AG3), on_timeout="approve", due_hours=1)]
                        },
                    },
                )
                empty_title = await client.post(
                    url, json={"title": "", "definition": {"steps": [_step("S", _user(AG3))]}}
                )
            assert via_template.status_code == 201
            assert via_template.json()["steps"][0]["approvers"][0]["display_name"] == (
                "Appr appr97-agent4"
            )
            assert (unknown_template.status_code, _code(unknown_template)) == (
                404,
                "template_not_found",
            )
            assert (neither.status_code, _code(neither)) == (422, "invalid_definition")
            assert (both.status_code, _code(both)) == (422, "invalid_definition")
            # F9: an own definition cannot approve on its own
            assert (auto_approve.status_code, _code(auto_approve)) == (422, "invalid_definition")
            assert (timeout_approve.status_code, _code(timeout_approve)) == (
                422,
                "invalid_definition",
            )
            assert empty_title.status_code == 422


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_ticket_request_only_agent_requestable(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            body = {"title": "x", "definition": {"steps": [_step("S", _user(AG3))]}}
            async with await as_client(session, agent=AG1) as client:
                # the endpoint has no subject_type field
                with_subject = await client.post(
                    f"/api/v1/tickets/{T1}/approvals", json={**body, "subject_type": SUBJECT}
                )
                created = await client.post(f"/api/v1/tickets/{T1}/approvals", json=body)
            assert with_subject.status_code == 422
            assert created.status_code == 201
            stored = await session.get(TiqoraApproval, created.json()["id"])
            assert stored is not None and stored.subject_type == "ticket"


# --- pickers ---------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_templates_lists_resolvers_options(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            definition = _defn(_step("Vorlage", _user(AG3)))
            for key, name, subjects in (
                ("appr97.ticket", "B Ticket", ["ticket"]),
                ("appr97.other", "A Anderes", [SUBJECT]),
                ("appr97.any", "C Alle", []),
            ):
                await templates.create_template(
                    session,
                    key=key,
                    name=name,
                    definition=definition,
                    subject_types=subjects,
                    actor_id=AG1,
                )
            gone = await templates.create_template(
                session,
                key="appr97.gone",
                name="D Gelöscht",
                definition=definition,
                actor_id=AG1,
            )
            await session.execute(
                text("UPDATE tiqora_approval_template SET valid = :v WHERE id = :i"),
                {"v": False, "i": gone.id},
            )
            await lists.create_list(
                session, name="appr97 CAB", member_ids=[AG3, AG4, AG5], actor_id=AG1
            )
            await session.commit()
            async with await as_client(session, agent=AG5) as client:
                everything = (await client.get(f"{BASE}/templates")).json()
                for_ticket = (
                    await client.get(f"{BASE}/templates", params={"subject_type": "ticket"})
                ).json()
                lists_out = (await client.get(f"{BASE}/lists")).json()
                resolvers_off = (await client.get(f"{BASE}/resolvers")).json()
                await _set(session, customer_approvers_enabled=True)
                resolvers_on = (await client.get(f"{BASE}/resolvers")).json()
                resolvers_fake = (
                    await client.get(f"{BASE}/resolvers", params={"subject_type": SUBJECT})
                ).json()
                resolvers_ticket = (
                    await client.get(f"{BASE}/resolvers", params={"subject_type": "ticket"})
                ).json()
            assert [t["key"] for t in everything] == ["appr97.other", "appr97.ticket", "appr97.any"]
            assert [t["key"] for t in for_ticket] == ["appr97.ticket", "appr97.any"]
            assert for_ticket[0] == {
                "key": "appr97.ticket",
                "name": "B Ticket",
                "description": None,
                "subject_types": ["ticket"],
            }
            assert [(item["name"], item["member_count"]) for item in lists_out] == [
                ("appr97 CAB", 3)
            ]
            assert [r["key"] for r in resolvers_off] == ["ticket.owner", "ticket.responsible"]
            assert [(r["key"], r["yields_customers"]) for r in resolvers_on] == [
                ("ticket.customer_user", True),
                ("ticket.owner", False),
                ("ticket.responsible", False),
            ]
            assert "ticket.customer_user" not in [r["key"] for r in resolvers_fake]  # no customers
            assert "ticket.customer_user" in [r["key"] for r in resolvers_ticket]
            assert all(r["title_key"].startswith("approvals.resolver.") for r in resolvers_on)


# --- delegations -----------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegations_own_only(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed_admin(session)
        with installed_registry(make_fake_subject_type()):
            theirs_row = await delegation.create_delegation(
                session,
                user_id=AG5,
                substitute_user_id=AG4,
                valid_from=utc_now() - timedelta(minutes=1),
                valid_until=None,
                created_by=AG5,
            )
            theirs = theirs_row.id
            await session.commit()
            async with await as_client(session, agent=AG1) as client:
                created = await client.post(
                    f"{BASE}/delegations",
                    json={
                        "substitute_user_id": AG2,
                        "valid_from": (utc_now() - timedelta(minutes=5)).isoformat() + "Z",
                        "valid_until": (utc_now() + timedelta(days=2)).isoformat() + "Z",
                    },
                )
                overlap = await client.post(f"{BASE}/delegations", json={"substitute_user_id": AG3})
                to_self = await client.post(f"{BASE}/delegations", json={"substitute_user_id": AG1})
                mine = await client.get(f"{BASE}/delegations")
                foreign = await client.delete(f"{BASE}/delegations/{theirs}")
                missing = await client.delete(f"{BASE}/delegations/{theirs + 5000}")
            assert created.status_code == 201
            row = created.json()
            assert (row["user_id"], row["substitute_user_id"]) == (AG1, AG2)
            assert row["substitute_label"] == "Appr appr97-agent2"
            assert (overlap.status_code, _code(overlap)) == (409, "delegation_overlap")
            assert (to_self.status_code, _code(to_self)) == (422, "forward_target_invalid")
            assert [r["id"] for r in mine.json()] == [row["id"]]  # only the caller's own
            assert (foreign.status_code, _code(foreign)) == (404, "delegation_not_found")
            assert (missing.status_code, _code(missing)) == (404, "delegation_not_found")
            async with await as_client(session, agent=AG5) as client:
                still = await client.get(f"{BASE}/delegations")
            assert [r["id"] for r in still.json()] == [theirs]  # untouched

            async with await as_client(session, agent=AG1) as client:
                deleted = await client.delete(f"{BASE}/delegations/{row['id']}")
                after = await client.get(f"{BASE}/delegations")
                twice = await client.delete(f"{BASE}/delegations/{row['id']}")
            assert deleted.status_code == 204 and deleted.content == b""
            assert after.json() == []
            assert (twice.status_code, _code(twice)) == (403, "delegation_not_active")
            # an admin may end a foreign delegation
            async with await as_client(session, agent=ADMIN) as client:
                admin_deleted = await client.delete(f"{BASE}/delegations/{theirs}")
            assert admin_deleted.status_code == 204


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_delegation_changes_notify_both_agents(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with approvals_session(request, url_fixture) as session:
        published = Published(monkeypatch, session)
        with installed_registry(make_fake_subject_type()):
            async with await as_client(session, agent=AG1) as client:
                created = await client.post(f"{BASE}/delegations", json={"substitute_user_id": AG2})
                assert [k for k, _ in published.log] == ["commit", "publish"]
                assert published.publishes[0] == {AG1, AG2}
                published.clear()
                await client.delete(f"{BASE}/delegations/{created.json()['id']}")
            assert [k for k, _ in published.log] == ["commit", "publish"]
            assert published.publishes[0] == {AG1, AG2}


# --- API keys --------------------------------------------------------------------------------


def test_api_key_scope_mapping() -> None:
    assert path_to_area("/api/v1/approvals/inbox") == "tickets"
    assert path_to_area("/api/v1/approvals/12/approvers/3/forward") == "tickets"
    assert path_to_area("/api/v1/tickets/1/approvals") == "tickets"
    # the public e-mail link endpoints are open to a stray key header (CX12)
    assert path_to_area("/api/v1/approval-links/inspect") is None
    assert path_to_area("/api/v1/approval-links/decide") is None
    # a prefix neighbour does not leak into the area
    assert path_to_area("/api/v1/approvalsx") == "_unknown"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_api_key_ro_cannot_decide(url_fixture: str, request: pytest.FixtureRequest) -> None:
    def key(*scopes: str) -> AuthenticatedUser:
        return AuthenticatedUser(
            id=AG3,
            login="appr97-agent3",
            first_name="Appr",
            last_name="Agent",
            auth_method="api_key",
            api_key_scopes=frozenset(scopes),
        )

    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            async with _gated_client(session, key("tickets:ro")) as client:
                read = await client.get(f"{BASE}/inbox")
                vote = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
                forward = await client.post(
                    f"{BASE}/{aid}/approvers/1/forward", json={"user_id": AG5}
                )
                delegate = await client.post(
                    f"{BASE}/delegations", json={"substitute_user_id": AG5}
                )
            async with _gated_client(session, key("stats:rw")) as client:
                wrong_area = await client.get(f"{BASE}/inbox")
            assert read.status_code == 200
            for refused in (vote, forward, delegate):
                assert refused.status_code == 403
                assert refused.json()["detail"] == "API key lacks tickets:rw scope"
            assert wrong_area.status_code == 403
            async with _gated_client(session, key("tickets:rw")) as client:
                ok = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
            assert ok.status_code == 200
            vote_row = ok.json()["steps"][0]["approvers"][0]["vote"]
            assert vote_row["channel"] == "api"  # decided with an API key


def test_openapi_has_routes() -> None:
    paths = api_app.create_app(SETTINGS).openapi()["paths"]
    expected: dict[str, set[str]] = {
        "/api/v1/approvals/inbox": {"get"},
        "/api/v1/approvals/inbox/count": {"get"},
        "/api/v1/approvals/templates": {"get"},
        "/api/v1/approvals/lists": {"get"},
        "/api/v1/approvals/resolvers": {"get"},
        "/api/v1/approvals/delegations": {"get", "post"},
        "/api/v1/approvals/delegations/{delegation_id}": {"delete"},
        "/api/v1/approvals/{approval_id}": {"get"},
        "/api/v1/approvals/{approval_id}/decision": {"post"},
        "/api/v1/approvals/{approval_id}/approvers/{approver_id}/forward": {"post"},
        "/api/v1/approvals/{approval_id}/cancel": {"post"},
        "/api/v1/approvals/{approval_id}/refresh-approvers": {"post"},
        "/api/v1/tickets/{ticket_id}/approvals": {"get", "post"},
    }
    for path, methods in expected.items():
        assert path in paths, path
        assert methods <= set(paths[path]), path
    assert paths["/api/v1/tickets/{ticket_id}/approvals"]["post"]["responses"].keys() >= {"201"}


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_actions_on_invisible_request_answer_404(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            async with await as_client(session, agent=AG1) as client:
                detail = (await client.get(f"{BASE}/{aid}")).json()
            row = detail["steps"][0]["approvers"][0]["id"]
            calls = (
                ("post", f"{BASE}/{{}}/decision", {"decision": "approved"}),
                ("post", f"{BASE}/{{}}/cancel", None),
                ("post", f"{BASE}/{{}}/refresh-approvers", None),
                ("post", f"{BASE}/{{}}/approvers/{row}/forward", {"user_id": AG5}),
            )
            async with await as_client(session, agent=AG7) as client:  # sees nothing
                for _, path, body in calls:
                    real = await client.post(path.format(aid), json=body)
                    gone = await client.post(path.format(aid + 1000), json=body)
                    assert (real.status_code, _code(real)) == (404, "approval_not_found"), path
                    assert real.json() == gone.json(), path
            # a caller who may see the request still gets the precise refusal
            async with await as_client(session, agent=AG2) as client:
                cancel = await client.post(f"{BASE}/{aid}/cancel")
                vote = await client.post(f"{BASE}/{aid}/decision", json={"decision": "approved"})
            assert (cancel.status_code, _code(cancel)) == (403, "forbidden")
            assert (vote.status_code, _code(vote)) == (403, "not_an_approver")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_creator_who_is_not_the_requester_can_view_and_cancel(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            info = await service.request_approval(
                session,
                ticket_id=T1,
                subject_type=SUBJECT,
                title="Im Auftrag appr97",
                actor=Actor.agent(AG5),  # creator, no access to the ticket's queue
                requester=Principal.agent(AG1),
                definition=_defn(_step("Prüfung", _user(AG3))),
                settings=SETTINGS,
            )
            await session.commit()
            async with await as_client(session, agent=AG5) as client:
                seen = await client.get(f"{BASE}/{info.approval_id}")
                cancelled = await client.post(f"{BASE}/{info.approval_id}/cancel")
            assert seen.status_code == 200 and seen.json()["can_cancel"] is True
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["status"] == "cancelled"


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_ticket_approvals_are_paged(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            ids = [await _request(session, key=f"k{i}") for i in range(3)]
            url = f"/api/v1/tickets/{T1}/approvals"
            async with await as_client(session, agent=AG1) as client:
                first = await client.get(url, params={"limit": 2})
                second = await client.get(url, params={"limit": 2, "offset": 2})
                too_big = await client.get(url, params={"limit": 101})
            assert [r["id"] for r in first.json()] == [ids[2], ids[1]]
            assert [r["id"] for r in second.json()] == [ids[0]]
            assert too_big.status_code == 422
