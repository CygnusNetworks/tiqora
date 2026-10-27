"""Admin REST API of the approval engine (A13 admin rows): overview, templates, approver lists
and delegations of any agent.

Own ids 971900-971949: ``ADMIN`` is the one extra agent (member of the ``admin`` group); the
helper world supplies everything else. Every test runs inside the rolled-back
``approvals_session`` (the routes' commit/rollback act on a savepoint).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._approvals_helpers import (
    AG1,
    AG2,
    AG3,
    AG4,
    AG5,
    T1,
    T2,
    approvals_session,
    as_client,
    installed_registry,
    make_fake_subject_type,
)
from tests._itsm_adapter_helpers import seed_user
from tiqora.api import app as api_app
from tiqora.approvals import service
from tiqora.approvals.contract import Actor, ApprovalDefinition
from tiqora.config import Settings
from tiqora.jobs.service import utc_now

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
ADMIN = 971901
ADMIN_GROUP = 971902
GONE_USER = 971949  # never seeded
BASE = "/api/v1/admin/approvals"
AGENT = "/api/v1/approvals"


def _user(uid: int) -> dict[str, str]:
    return {"kind": "user", "ref": str(uid)}


def _definition(*approvers: dict[str, str], name: str = "Prüfung") -> dict[str, Any]:
    return {"steps": [{"name": name, "approvers": list(approvers)}]}


def _template_body(key: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "template_key": key,
        "name": "appr97 Vorlage",
        "subject_types": [],
        "definition": _definition(_user(AG3)),
    }
    body.update(extra)
    return body


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


async def _request(
    session: AsyncSession,
    *,
    approvers: tuple[int, ...] = (AG3, AG4),
    requester: int = AG1,
    ticket: int = T1,
    key: str = "default",
) -> int:
    info = await service.request_approval(
        session,
        ticket_id=ticket,
        subject_type=SUBJECT,
        title="Freigabe appr97",
        actor=Actor.agent(requester),
        definition=ApprovalDefinition.model_validate(_definition(*(_user(a) for a in approvers))),
        approval_key=key,
        settings=SETTINGS,
    )
    await session.commit()  # the routes roll back first: seeded rows must be committed
    return info.approval_id


def _code(response: Response) -> str:
    return str(response.json()["detail"]["code"])


# --- access ------------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_non_admin_forbidden(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        with installed_registry(make_fake_subject_type()):
            async with await as_client(session, agent=AG5) as client:
                responses = [
                    await client.get(BASE),
                    await client.get(f"{BASE}/templates"),
                    await client.post(f"{BASE}/templates", json=_template_body("appr97.x")),
                    await client.post(
                        f"{BASE}/templates/validate", json={"definition": _definition()}
                    ),
                    await client.get(f"{BASE}/lists"),
                    await client.post(f"{BASE}/lists", json={"name": "appr97 x"}),
                    await client.get(f"{BASE}/delegations"),
                    await client.post(
                        f"{BASE}/delegations", json={"user_id": AG1, "substitute_user_id": AG2}
                    ),
                    await client.delete(f"{BASE}/delegations/1"),
                ]
            assert [r.status_code for r in responses] == [403] * 9
            assert {r.json()["detail"] for r in responses} == {"Admin privileges required"}


# --- overview ----------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_overview_filters(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed_admin(session)
        with installed_registry(make_fake_subject_type()):
            first = await _request(session)
            late = await _request(session, ticket=T2, key="late", approvers=(AG3,))
            gone = await _request(session, key="gone", approvers=(AG4,))
            await session.execute(
                text("UPDATE tiqora_approval_step SET due_at = :t WHERE approval_id = :a"),
                {"t": utc_now() - timedelta(hours=2), "a": late},
            )
            await session.commit()
            async with await as_client(session, agent=AG1) as client:
                assert (await client.post(f"{AGENT}/{gone}/cancel")).status_code == 200
            async with await as_client(session, agent=ADMIN) as client:
                everything = await client.get(BASE)
                cancelled = await client.get(BASE, params={"status": "cancelled"})
                pending = await client.get(BASE, params={"status": "pending"})
                on_t2 = await client.get(BASE, params={"ticket_id": T2})
                by_subject = await client.get(BASE, params={"subject_type": SUBJECT})
                by_other = await client.get(BASE, params={"subject_type": "ticket"})
                overdue = await client.get(BASE, params={"overdue": "true"})
                not_overdue = await client.get(BASE, params={"overdue": "false"})
                paged = await client.get(BASE, params={"limit": 1, "offset": 1})
                too_many = await client.get(BASE, params={"limit": 101})
                bad_status = await client.get(BASE, params={"status": "expired"})

            def ids(response: Response) -> list[int]:
                return [i["id"] for i in response.json()["items"]]

            assert everything.status_code == 200
            assert ids(everything) == [gone, late, first]  # newest first
            assert (everything.json()["total"], everything.json()["limit"]) == (3, 25)
            assert ids(cancelled) == [gone]
            assert ids(pending) == [late, first]
            assert ids(on_t2) == [late]
            assert ids(by_subject) == [gone, late, first]
            assert by_other.json() == {"items": [], "total": 0, "limit": 25, "offset": 0}
            assert ids(overdue) == [late]
            assert ids(not_overdue) == [gone, first]
            assert (ids(paged), paged.json()["total"], paged.json()["offset"]) == ([late], 3, 1)
            assert too_many.status_code == 422
            assert bad_status.status_code == 422

            by_id = {i["id"]: i for i in everything.json()["items"]}
            row = by_id[first]
            assert (row["ticket_id"], row["ticket_number"], row["subject_type"]) == (
                T1,
                f"20261015{T1}",
                SUBJECT,
            )
            assert (row["title"], row["status"], row["step_name"]) == (
                "Freigabe appr97",
                "pending",
                "Prüfung",
            )
            assert (row["open_approvers"], row["overdue"], row["can_cancel"]) == (2, False, True)
            assert row["requester_label"] == "Appr appr97-agent1"
            assert by_id[late]["overdue"] is True and by_id[late]["open_approvers"] == 1
            closed = by_id[gone]
            assert (closed["status"], closed["step_name"], closed["open_approvers"]) == (
                "cancelled",
                None,
                0,
            )
            assert (closed["overdue"], closed["can_cancel"]) == (False, False)


# --- templates ---------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_template_crud_and_validate(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed_admin(session)
        with installed_registry(make_fake_subject_type()):
            async with await as_client(session, agent=ADMIN) as client:
                created = await client.post(
                    f"{BASE}/templates",
                    json=_template_body(
                        "appr97.vorlage",
                        subject_types=[SUBJECT, SUBJECT],
                        definition=_definition(_user(AG3), _user(GONE_USER)),
                    ),
                )
                taken = await client.post(
                    f"{BASE}/templates", json=_template_body("appr97.vorlage")
                )
                bad_resolver = await client.post(
                    f"{BASE}/templates",
                    json=_template_body(
                        "appr97.resolver",
                        definition=_definition({"kind": "resolver", "ref": "nope.resolver"}),
                    ),
                )
                invalid_off = await client.post(
                    f"{BASE}/templates", json=_template_body("appr97.off", valid=False)
                )
                tid = created.json()["id"]
                got = await client.get(f"{BASE}/templates/{tid}")
                listed = await client.get(f"{BASE}/templates")
                renamed = await client.put(
                    f"{BASE}/templates/{tid}",
                    json=_template_body(
                        "appr97.vorlage", name="appr97 Umbenannt", description="Text"
                    ),
                )
                key_change = await client.put(
                    f"{BASE}/templates/{tid}", json=_template_body("appr97.andere")
                )
                missing_put = await client.put(
                    f"{BASE}/templates/{tid + 5000}", json=_template_body("appr97.vorlage")
                )

                good = await client.post(
                    f"{BASE}/templates/validate", json={"definition": _definition(_user(AG3))}
                )
                warned = await client.post(
                    f"{BASE}/templates/validate",
                    json={"definition": _definition(_user(GONE_USER))},
                )
                resolver_check = await client.post(
                    f"{BASE}/templates/validate",
                    json={"definition": _definition({"kind": "resolver", "ref": "nope.resolver"})},
                )
                broken = await client.post(
                    f"{BASE}/templates/validate", json={"definition": {"steps": []}}
                )
                after_validate = await client.get(f"{BASE}/templates")

                deleted = await client.delete(f"{BASE}/templates/{tid}")
                gone = await client.get(f"{BASE}/templates/{tid}")
                gone_delete = await client.delete(f"{BASE}/templates/{tid}")

            assert created.status_code == 201
            body = created.json()
            assert (body["template_key"], body["origin"], body["valid"]) == (
                "appr97.vorlage",
                "admin",
                True,
            )
            assert body["subject_types"] == [SUBJECT]
            assert body["warnings"] == [f"missing_user:{GONE_USER}"]
            assert [a["ref"] for a in body["definition"]["steps"][0]["approvers"]] == [
                str(AG3),
                str(GONE_USER),
            ]
            assert (taken.status_code, _code(taken)) == (409, "template_key_taken")
            assert (bad_resolver.status_code, _code(bad_resolver)) == (422, "invalid_definition")
            assert bad_resolver.json()["detail"]["detail"] == "unknown_resolver:nope.resolver"
            assert invalid_off.status_code == 201 and invalid_off.json()["valid"] is False
            assert got.status_code == 200 and got.json() == body
            # the admin list shows invalid ones too (same name: by id)
            assert [t["template_key"] for t in listed.json()] == ["appr97.vorlage", "appr97.off"]
            assert renamed.status_code == 200
            assert (renamed.json()["name"], renamed.json()["description"]) == (
                "appr97 Umbenannt",
                "Text",
            )
            assert renamed.json()["subject_types"] == []
            assert (key_change.status_code, _code(key_change)) == (422, "invalid_definition")
            assert (missing_put.status_code, _code(missing_put)) == (404, "template_not_found")

            assert (good.status_code, good.json()) == (200, {"errors": [], "warnings": []})
            assert warned.json() == {"errors": [], "warnings": [f"missing_user:{GONE_USER}"]}
            assert resolver_check.json() == {
                "errors": ["unknown_resolver:nope.resolver"],
                "warnings": [],
            }
            assert broken.status_code == 200
            assert broken.json()["errors"] == ["invalid_definition:steps"]
            assert len(after_validate.json()) == 2  # validate saved nothing

            assert deleted.status_code == 204 and deleted.content == b""
            assert (gone.status_code, _code(gone)) == (404, "template_not_found")
            assert (gone_delete.status_code, _code(gone_delete)) == (404, "template_not_found")


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_template_unknown_subject_type_refused(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed_admin(session)
        with installed_registry(make_fake_subject_type()):
            async with await as_client(session, agent=ADMIN) as client:
                created = await client.post(
                    f"{BASE}/templates",
                    json=_template_body("appr97.fremd", subject_types=["nope.subject"]),
                )
                ok = await client.post(f"{BASE}/templates", json=_template_body("appr97.ok"))
                updated = await client.put(
                    f"{BASE}/templates/{ok.json()['id']}",
                    json=_template_body("appr97.ok", subject_types=["nope.subject"]),
                )
                checked = await client.post(
                    f"{BASE}/templates/validate",
                    json={"definition": _definition(_user(AG3)), "subject_type": "nope.subject"},
                )
                listed = await client.get(f"{BASE}/templates")
            assert (created.status_code, _code(created)) == (422, "unknown_subject_type")
            assert created.json()["detail"]["detail"] == "nope.subject"
            assert (updated.status_code, _code(updated)) == (422, "unknown_subject_type")
            assert checked.json() == {
                "errors": ["unknown_subject_type:nope.subject"],
                "warnings": [],
            }
            assert [t["template_key"] for t in listed.json()] == ["appr97.ok"]  # nothing saved


# --- approver lists ----------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_list_crud(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed_admin(session)
        with installed_registry(make_fake_subject_type()):
            async with await as_client(session, agent=ADMIN) as client:
                created = await client.post(
                    f"{BASE}/lists",
                    json={"name": "appr97 CAB", "description": "Board", "member_ids": [AG4, AG3]},
                )
                taken = await client.post(f"{BASE}/lists", json={"name": "appr97 CAB"})
                bad_member = await client.post(
                    f"{BASE}/lists", json={"name": "appr97 Leer", "member_ids": [GONE_USER]}
                )
                off = await client.post(
                    f"{BASE}/lists", json={"name": "appr97 Aus", "valid": False}
                )
                lid = created.json()["id"]
                got = await client.get(f"{BASE}/lists/{lid}")
                updated = await client.put(
                    f"{BASE}/lists/{lid}",
                    json={"name": "appr97 CAB neu", "member_ids": [AG5], "valid": True},
                )
                listed = await client.get(f"{BASE}/lists")
                picker = await client.get(f"{AGENT}/lists")

                template = await client.post(
                    f"{BASE}/templates",
                    json=_template_body(
                        "appr97.cab", definition=_definition({"kind": "list", "ref": str(lid)})
                    ),
                )
                in_use = await client.delete(f"{BASE}/lists/{lid}")
                await client.delete(f"{BASE}/templates/{template.json()['id']}")
                deleted = await client.delete(f"{BASE}/lists/{lid}")
                gone = await client.get(f"{BASE}/lists/{lid}")
                missing_put = await client.put(f"{BASE}/lists/{lid}", json={"name": "appr97 x"})

            assert created.status_code == 201
            body = created.json()
            assert (body["name"], body["description"], body["valid"]) == (
                "appr97 CAB",
                "Board",
                True,
            )
            assert body["member_ids"] == sorted([AG3, AG4])
            assert body["members"] == [
                {"id": AG3, "label": "Appr appr97-agent3"},
                {"id": AG4, "label": "Appr appr97-agent4"},
            ]
            assert (taken.status_code, _code(taken)) == (409, "list_name_taken")
            assert (bad_member.status_code, _code(bad_member)) == (422, "invalid_member")
            assert bad_member.json()["detail"]["detail"] == str(GONE_USER)
            assert off.status_code == 201 and off.json()["valid"] is False
            assert got.json() == body
            assert updated.status_code == 200
            assert (updated.json()["name"], updated.json()["member_ids"]) == (
                "appr97 CAB neu",
                [AG5],
            )
            assert [(x["name"], x["valid"]) for x in listed.json()] == [
                ("appr97 Aus", False),
                ("appr97 CAB neu", True),
            ]
            # the agent picker hides the invalid list
            assert [x["name"] for x in picker.json()] == ["appr97 CAB neu"]
            assert template.status_code == 201
            assert (in_use.status_code, _code(in_use)) == (409, "list_in_use")
            assert in_use.json()["detail"]["detail"] == "appr97.cab"
            assert deleted.status_code == 204
            assert (gone.status_code, _code(gone)) == (404, "list_not_found")
            assert (missing_put.status_code, _code(missing_put)) == (404, "list_not_found")


# --- delegations -------------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_admin_delegation_for_other_user(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed_admin(session)
        with installed_registry(make_fake_subject_type()):
            async with await as_client(session, agent=ADMIN) as client:
                created = await client.post(
                    f"{BASE}/delegations",
                    json={
                        "user_id": AG1,
                        "substitute_user_id": AG2,
                        "valid_from": (utc_now() - timedelta(minutes=5)).isoformat() + "Z",
                        "valid_until": (utc_now() + timedelta(days=3)).isoformat() + "Z",
                    },
                )
                other = await client.post(
                    f"{BASE}/delegations", json={"user_id": AG3, "substitute_user_id": AG4}
                )
                to_self = await client.post(
                    f"{BASE}/delegations", json={"user_id": AG5, "substitute_user_id": AG5}
                )
                overlap = await client.post(
                    f"{BASE}/delegations", json={"user_id": AG1, "substitute_user_id": AG4}
                )
                no_user = await client.post(f"{BASE}/delegations", json={"substitute_user_id": AG4})
                everyone = await client.get(f"{BASE}/delegations")
                of_ag1 = await client.get(f"{BASE}/delegations", params={"user_id": AG1})
                missing = await client.delete(f"{BASE}/delegations/{other.json()['id'] + 5000}")
            assert created.status_code == 201
            row = created.json()
            assert (row["user_id"], row["substitute_user_id"]) == (AG1, AG2)
            assert row["substitute_label"] == "Appr appr97-agent2"
            assert other.status_code == 201 and other.json()["user_id"] == AG3
            assert (to_self.status_code, _code(to_self)) == (422, "forward_target_invalid")
            assert (overlap.status_code, _code(overlap)) == (409, "delegation_overlap")
            assert no_user.status_code == 422
            assert [r["id"] for r in everyone.json()] == [row["id"], other.json()["id"]]
            assert [r["id"] for r in of_ag1.json()] == [row["id"]]
            assert (missing.status_code, _code(missing)) == (404, "delegation_not_found")

            # the delegator sees it in the agent API and the substitute may now decide for them
            async with await as_client(session, agent=AG1) as client:
                own = await client.get(f"{AGENT}/delegations")
            assert [r["id"] for r in own.json()] == [row["id"]]

            async with await as_client(session, agent=ADMIN) as client:
                deleted = await client.delete(f"{BASE}/delegations/{row['id']}")
                after = await client.get(f"{BASE}/delegations", params={"user_id": AG1})
                ended = await client.get(
                    f"{BASE}/delegations", params={"user_id": AG1, "include_ended": "true"}
                )
                twice = await client.delete(f"{BASE}/delegations/{row['id']}")
            assert deleted.status_code == 204 and deleted.content == b""
            assert after.json() == []
            assert [r["id"] for r in ended.json()] == [row["id"]]
            assert (twice.status_code, _code(twice)) == (403, "delegation_not_active")


# --- admin power over a request ------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", URLS)
async def test_admin_can_cancel_but_not_decide_as(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with approvals_session(request, url_fixture) as session:
        await _seed_admin(session)
        with installed_registry(make_fake_subject_type()):
            aid = await _request(session)
            async with await as_client(session, agent=ADMIN) as client:
                detail = (await client.get(f"{AGENT}/{aid}")).json()
                ag3_row = detail["steps"][0]["approvers"][0]["id"]
                own_row = await client.post(
                    f"{AGENT}/{aid}/decision", json={"decision": "approved"}
                )
                foreign_row = await client.post(
                    f"{AGENT}/{aid}/decision",
                    json={"decision": "approved", "approver_id": ag3_row},
                )
                still_open = await client.get(f"{AGENT}/{aid}")
                cancelled = await client.post(f"{AGENT}/{aid}/cancel")
                overview = await client.get(BASE, params={"status": "cancelled"})
            assert detail["can_cancel"] is True and detail["my_rows"] == []
            assert (own_row.status_code, _code(own_row)) == (403, "not_an_approver")
            assert (foreign_row.status_code, _code(foreign_row)) == (403, "not_an_approver")
            assert still_open.json()["status"] == "pending"
            assert still_open.json()["steps"][0]["approved_count"] == 0
            assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
            assert [i["id"] for i in overview.json()["items"]] == [aid]


def test_openapi_has_admin_routes() -> None:
    paths = api_app.create_app(SETTINGS).openapi()["paths"]
    expected: dict[str, set[str]] = {
        "/api/v1/admin/approvals": {"get"},
        "/api/v1/admin/approvals/templates": {"get", "post"},
        "/api/v1/admin/approvals/templates/validate": {"post"},
        "/api/v1/admin/approvals/templates/{template_id}": {"get", "put", "delete"},
        "/api/v1/admin/approvals/lists": {"get", "post"},
        "/api/v1/admin/approvals/lists/{list_id}": {"get", "put", "delete"},
        "/api/v1/admin/approvals/delegations": {"get", "post"},
        "/api/v1/admin/approvals/delegations/{delegation_id}": {"delete"},
    }
    for path, methods in expected.items():
        assert path in paths, path
        assert methods <= set(paths[path]), path
