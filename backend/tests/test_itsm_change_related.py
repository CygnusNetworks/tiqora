"""Related incidents/problems of a change and create-change-from-ticket (both dialects).

Seed band 947200-947299 (ticket numbers ``9472...``); users, queues, services and the world come
from ``tests._change_fixtures``. The world is one rolled-back session; routes commit with
``flush``. ``Q_B`` is out of reach for everybody but admin.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from tests._change_fixtures import (
    APP_SETTINGS,
    G_A,
    G_B,
    Q_A,
    Q_B,
    SERVICE,
    SERVICE_B,
    U_FULL,
    U_NONE,
    U_RO,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
)
from tests._itsm_fake_provider import OPERATIONAL, FakeCiProvider
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.ticket_write_service import link_objects
from tiqora.extensions import Registry, set_registry_for_tests
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.impact import ITSM_CHANGE_IMPACT_JOB
from tiqora.itsm.change.models import TiqoraItsmChangeEvent
from tiqora.itsm.change.related import (
    create_from_ticket,
    link_related,
    list_related,
    unlink_related,
)
from tiqora.itsm.change.service import ChangeCreateIn, create_change
from tiqora.itsm.change.tasks import TaskIn, add_task
from tiqora.itsm.providers import CI_PROVIDERS, CiProviderFactory
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.znuny.sysconfig import SysConfig

pytestmark = [pytest.mark.db, pytest.mark.parametrize("url_fixture", URL_FIXTURES)]

TICKET_NO_BASE = 947200
_T = datetime(2026, 10, 1)


def _registry(request: pytest.FixtureRequest, provider: FakeCiProvider | None = None) -> None:
    registry = Registry()
    registry.add(JOB_HANDLERS, ITSM_CHANGE_IMPACT_JOB)
    if provider is not None:
        registry.add(CI_PROVIDERS, CiProviderFactory(mode="tiqora", create=lambda _sc: provider))
    registry.freeze()
    set_registry_for_tests(registry)
    request.addfinalizer(lambda: set_registry_for_tests(None))


async def _ticket(
    w: ChangeWorld,
    *,
    type_name: str = "Incident",
    queue_id: int = Q_A,
    service_id: int | None = None,
    customer_user_id: str | None = None,
) -> int:
    """A bare Znuny ticket of the given type (the type row is created when missing)."""
    tn = f"{TICKET_NO_BASE}{time.time_ns()}"
    type_id = (
        await w.session.execute(
            text("SELECT id FROM ticket_type WHERE name = :n"), {"n": type_name}
        )
    ).scalar_one_or_none()
    if type_id is None:
        type_id = (
            int((await w.session.execute(text("SELECT MAX(id) FROM ticket_type"))).scalar_one()) + 1
        )
        await w.session.execute(
            text(
                "INSERT INTO ticket_type (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:i, :n, 1, :t, 1, :t, 1)"
            ),
            {"i": type_id, "n": type_name, "t": _T},
        )
    state_id = (
        await w.session.execute(text("SELECT id FROM ticket_state WHERE name = 'open'"))
    ).scalar_one()
    await w.session.execute(
        text(
            "INSERT INTO ticket (tn, title, queue_id, ticket_lock_id, type_id, service_id,"
            " customer_user_id, user_id, responsible_user_id, ticket_priority_id,"
            " ticket_state_id, timeout, until_time, escalation_time, escalation_update_time,"
            " escalation_response_time, escalation_solution_time, archive_flag, create_time,"
            " create_by, change_time, change_by) VALUES (:tn, :title, :q, 1, :ty, :svc, :cu, 1,"
            " 1, 3, :s, 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
        ),
        {
            "tn": tn,
            "title": f"chg94 {type_name} {tn}",
            "q": queue_id,
            "ty": type_id,
            "svc": service_id,
            "cu": customer_user_id,
            "s": state_id,
            "t": _T,
        },
    )
    return int(
        (
            await w.session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})
        ).scalar_one()
    )


async def _change(w: ChangeWorld, **fields: object) -> int:
    data = ChangeCreateIn.model_validate(
        {"title": "Switch-Tausch", "change_type": "normal", "queue_id": Q_A, **fields}
    )
    view, _ = await create_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        data=data,
        user_id=U_FULL,
        session_factory=w.factory,
    )
    return view.record.ticket_id


async def _link_history(w: ChangeWorld, ticket_id: int, history_type: str) -> int:
    return int(
        (
            await w.session.execute(
                text(
                    "SELECT count(*) FROM ticket_history h JOIN ticket_history_type ht"
                    " ON ht.id = h.history_type_id WHERE ht.name = :n AND h.ticket_id = :t"
                ),
                {"n": history_type, "t": ticket_id},
            )
        ).scalar_one()
    )


async def _events(w: ChangeWorld, ticket_id: int, event: str) -> list[TiqoraItsmChangeEvent]:
    got = await w.session.execute(
        select(TiqoraItsmChangeEvent)
        .where(TiqoraItsmChangeEvent.ticket_id == ticket_id, TiqoraItsmChangeEvent.event == event)
        .order_by(TiqoraItsmChangeEvent.id)
    )
    return list(got.scalars())


async def _link_rows(w: ChangeWorld, ticket_id: int) -> list[tuple[str, str, str]]:
    rows = await w.session.execute(
        text(
            "SELECT lt.name, lr.source_key, lr.target_key FROM link_relation lr"
            " JOIN link_type lt ON lt.id = lr.type_id"
            " WHERE lr.source_key = :k OR lr.target_key = :k ORDER BY lr.target_key"
        ),
        {"k": str(ticket_id)},
    )
    return [(str(a), str(b), str(c)) for a, b, c in rows.all()]


async def test_link_related_writes_znuny_link(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with change_world(request, url_fixture) as w:
        _registry(request)
        change = await _change(w)
        incident = await _ticket(w, type_name="Incident")

        related = await link_related(
            w.session, APP_SETTINGS, ticket_id=change, other_ticket_id=incident, user_id=U_FULL
        )
        assert [(r.ticket_id, r.role, r.link_type, r.direction) for r in related] == [
            (incident, "incident", "Normal", "source")
        ]
        assert await _link_rows(w, change) == [("Normal", str(change), str(incident))]
        # Znuny's history on both ends, once each
        assert await _link_history(w, change, "TicketLinkAdd") == 1
        assert await _link_history(w, incident, "TicketLinkAdd") == 1
        events = await _events(w, change, "related_linked")
        assert len(events) == 1 and f'"ticket_id": {incident}' in str(events[0].detail)

        # idempotent: no second row, no second history, no second event
        again = await link_related(
            w.session, APP_SETTINGS, ticket_id=change, other_ticket_id=incident, user_id=U_FULL
        )
        assert [r.ticket_id for r in again] == [incident]
        assert len(await _link_rows(w, change)) == 1
        assert await _link_history(w, change, "TicketLinkAdd") == 1
        assert len(await _events(w, change, "related_linked")) == 1

        # a link never moves the phase
        assert (
            await _events(w, change, "phase") != [] and len(await _events(w, change, "phase")) == 1
        )

        # rw on the change, existence before permission, no self link
        for caller, other, code, status in (
            (U_RO, incident, "forbidden", 403),
            (U_NONE, incident, "ticket_not_found", 404),
            (U_FULL, change, "related_self", 422),
            (U_FULL, 946299, "ticket_not_found", 404),
        ):
            with pytest.raises(ChangeError) as err:
                await link_related(
                    w.session, APP_SETTINGS, ticket_id=change, other_ticket_id=other, user_id=caller
                )
            assert (err.value.error_code, err.value.status_code) == (code, status)
        with pytest.raises(ChangeError) as no_change:
            await link_related(
                w.session, APP_SETTINGS, ticket_id=incident, other_ticket_id=change, user_id=U_FULL
            )
        assert (no_change.value.error_code, no_change.value.status_code) == (
            "change_not_found",
            404,
        )


async def test_related_excludes_tasks_and_unreadable(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with change_world(request, url_fixture) as w:
        _registry(request)
        change = await _change(w)
        task = await add_task(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            change_ticket_id=change,
            data=TaskIn.model_validate({"title": "Switch tauschen"}),
            user_id=U_FULL,
            session_factory=w.factory,
        )
        incident = await _ticket(w, type_name="Incident")
        problem = await _ticket(w, type_name="Problem")
        hidden = await _ticket(w, type_name="Incident", queue_id=Q_B)
        for other in (incident, problem):
            await link_related(
                w.session, APP_SETTINGS, ticket_id=change, other_ticket_id=other, user_id=U_FULL
            )
        # the caller has no ro on Q_B: the link exists (made elsewhere) but never shows
        await link_objects(
            w.session,
            source_object="Ticket",
            source_key=str(change),
            target_object="Ticket",
            target_key=str(hidden),
            link_type="Normal",
            user_id=1,
        )

        # the task is linked ParentChild to the change but is no related ticket, and cannot be
        # linked as one
        assert any(row[2] == str(task.ticket_id) for row in await _link_rows(w, change))
        with pytest.raises(ChangeError) as is_task:
            await link_related(
                w.session,
                APP_SETTINGS,
                ticket_id=change,
                other_ticket_id=task.ticket_id,
                user_id=U_FULL,
            )
        assert (is_task.value.error_code, is_task.value.status_code) == ("related_is_task", 409)

        seen = await list_related(w.session, APP_SETTINGS, ticket_id=change, user_id=U_FULL)
        assert [(r.ticket_id, r.role) for r in seen] == [
            (incident, "incident"),
            (problem, "problem"),
        ]
        for gid in (G_A, G_B):
            await w.session.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (1, :g, 'ro', :t, 1, :t, 1)"
                ),
                {"g": gid, "t": _T},
            )
        both = await list_related(w.session, APP_SETTINGS, ticket_id=change, user_id=1)
        assert {r.ticket_id for r in both} == {incident, problem, hidden}

        with pytest.raises(ChangeError) as unreadable:
            await list_related(w.session, APP_SETTINGS, ticket_id=change, user_id=U_NONE)
        assert unreadable.value.status_code == 404


async def test_unlink_related(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with change_world(request, url_fixture) as w:
        _registry(request)
        change = await _change(w)
        incident = await _ticket(w)
        await link_related(
            w.session, APP_SETTINGS, ticket_id=change, other_ticket_id=incident, user_id=U_FULL
        )

        with pytest.raises(ChangeError) as read_only:
            await unlink_related(
                w.session, APP_SETTINGS, ticket_id=change, other_ticket_id=incident, user_id=U_RO
            )
        assert read_only.value.status_code == 403

        left = await unlink_related(
            w.session, APP_SETTINGS, ticket_id=change, other_ticket_id=incident, user_id=U_FULL
        )
        assert left == []
        assert await _link_rows(w, change) == []
        assert await _link_history(w, change, "TicketLinkDelete") == 1
        assert await _link_history(w, incident, "TicketLinkDelete") == 1
        assert len(await _events(w, change, "related_unlinked")) == 1

        with pytest.raises(ChangeError) as gone:
            await unlink_related(
                w.session, APP_SETTINGS, ticket_id=change, other_ticket_id=incident, user_id=U_FULL
            )
        assert (gone.value.error_code, gone.value.status_code) == ("link_not_found", 404)


async def test_create_from_problem_prefills_service_cis_and_links(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with change_world(request, url_fixture) as w:
        provider = FakeCiProvider(source="tiqora")
        ci = provider.add_ci(7, "srv-db-01.example.org", incident=OPERATIONAL)
        _registry(request, provider)
        problem = await _ticket(
            w, type_name="Problem", service_id=SERVICE, customer_user_id="kunde@example.org"
        )
        await provider.link_ticket(
            w.session, ticket_id=problem, ref=ci, link_type="RelevantTo", user_id=U_FULL
        )

        view, tasks_job = await create_from_ticket(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            source_ticket_id=problem,
            data=ChangeCreateIn.model_validate(
                {"title": "Datenbank-Update", "change_type": "normal", "queue_id": Q_A}
            ),
            user_id=U_FULL,
            session_factory=w.factory,
        )
        new_id = view.record.ticket_id
        assert tasks_job is None
        assert view.record.phase == "draft" and view.ticket.service_id == SERVICE
        assert (new_id, 7, "RelevantTo") in provider._links
        customer = (
            await w.session.execute(
                text("SELECT customer_user_id FROM ticket WHERE id = :t"), {"t": new_id}
            )
        ).scalar_one()
        assert customer == "kunde@example.org"
        body = (
            await w.session.execute(
                text(
                    "SELECT m.a_body FROM article a JOIN article_data_mime m ON m.article_id = a.id"
                    " WHERE a.ticket_id = :t"
                ),
                {"t": new_id},
            )
        ).scalar_one()
        assert "Problem" in str(body)

        # change = source of the Normal link, the problem is its related ticket
        assert await _link_rows(w, new_id) == [("Normal", str(new_id), str(problem))]
        assert await _link_history(w, new_id, "TicketLinkAdd") == 1
        assert await _link_history(w, problem, "TicketLinkAdd") == 1
        related = await list_related(w.session, APP_SETTINGS, ticket_id=new_id, user_id=U_FULL)
        assert [(r.ticket_id, r.role) for r in related] == [(problem, "problem")]

        # exactly one phase row (X-1); the link is its own event
        phases = await _events(w, new_id, "phase")
        assert [(e.from_phase, e.to_phase) for e in phases] == [(None, "draft")]
        assert len(await _events(w, new_id, "related_linked")) == 1

        # explicit values win over the source's; a gone service is not copied
        second, _ = await create_from_ticket(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            source_ticket_id=problem,
            data=ChangeCreateIn.model_validate(
                {
                    "title": "Zweiter",
                    "change_type": "normal",
                    "queue_id": Q_A,
                    "service_id": SERVICE_B,
                    "customer_user_id": "andere@example.org",
                }
            ),
            user_id=U_FULL,
            session_factory=w.factory,
        )
        assert second.ticket.service_id == SERVICE_B
        await w.session.execute(
            text("UPDATE service SET valid_id = 2 WHERE id = :s"), {"s": SERVICE}
        )
        third, _ = await create_from_ticket(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            source_ticket_id=problem,
            data=ChangeCreateIn.model_validate(
                {"title": "Dritter", "change_type": "normal", "queue_id": Q_A}
            ),
            user_id=U_FULL,
            session_factory=w.factory,
        )
        assert third.ticket.service_id is None

        # from_ticket_id on the plain create path takes the same road
        plain, _ = await create_change(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            data=ChangeCreateIn.model_validate(
                {
                    "title": "Vierter",
                    "change_type": "normal",
                    "queue_id": Q_A,
                    "from_ticket_id": problem,
                }
            ),
            user_id=U_FULL,
            session_factory=w.factory,
        )
        assert [
            r.ticket_id
            for r in await list_related(
                w.session, APP_SETTINGS, ticket_id=plain.record.ticket_id, user_id=U_FULL
            )
        ] == [problem]


async def test_create_from_needs_ro_on_source(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with change_world(request, url_fixture) as w:
        _registry(request)
        hidden = await _ticket(w, queue_id=Q_B)
        missing = 946299

        async def attempt(source: int) -> ChangeError:
            with pytest.raises(ChangeError) as err:
                await create_from_ticket(
                    w.session,
                    APP_SETTINGS,
                    SysConfig(w.session),
                    source_ticket_id=source,
                    data=ChangeCreateIn.model_validate(
                        {"title": "Nicht erlaubt", "change_type": "normal", "queue_id": Q_A}
                    ),
                    user_id=U_FULL,
                    session_factory=w.factory,
                )
            return err.value

        # unreadable and missing sources answer the same, before the queue is looked at
        for source in (hidden, missing):
            err = await attempt(source)
            assert (err.error_code, err.status_code) == ("ticket_not_found", 404)
        count = (
            await w.session.execute(text("SELECT count(*) FROM tiqora_itsm_change"))
        ).scalar_one()
        assert count == 0

        # readable source but no create on the target queue: 403 and nothing linked
        readable = await _ticket(w)
        with pytest.raises(ChangeError) as no_queue:
            await create_from_ticket(
                w.session,
                APP_SETTINGS,
                SysConfig(w.session),
                source_ticket_id=readable,
                data=ChangeCreateIn.model_validate(
                    {"title": "Nicht erlaubt", "change_type": "normal", "queue_id": Q_B}
                ),
                user_id=U_FULL,
                session_factory=w.factory,
            )
        assert no_queue.value.status_code == 403
        assert await _link_rows(w, readable) == []


# --- the API ---------------------------------------------------------------------------------


def _client(w: ChangeWorld, user_id: int = U_FULL) -> AsyncClient:
    async def _db() -> Any:
        yield w.session

    user = AuthenticatedUser(
        id=user_id,
        login=f"chg94-{user_id}",
        first_name="Erika",
        last_name="Beispiel",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory_dep] = lambda: w.factory
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_related_and_create_from_api(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with change_world(request, url_fixture) as w:
        _registry(request)
        incident = await _ticket(w, service_id=SERVICE)
        async with _client(w) as client:
            created = await client.post(
                f"/api/v1/tickets/{incident}/change/create-from",
                json={"title": "Aus Incident", "change_type": "emergency", "queue_id": Q_A},
            )
            assert created.status_code == 201, created.text
            change = created.json()["change"]
            assert change["service_ids"] == [SERVICE]
            base = f"/api/v1/itsm/changes/{change['ticket_id']}/related"

            listed = await client.get(base)
            assert listed.status_code == 200
            assert [(r["ticket_id"], r["role"], r["direction"]) for r in listed.json()] == [
                (incident, "incident", "source")
            ]

            other = await _ticket(w, type_name="Problem")
            linked = await client.post(base, json={"ticket_id": other})
            assert linked.status_code == 201
            assert {r["ticket_id"] for r in linked.json()} == {incident, other}

            removed = await client.delete(f"{base}/{incident}")
            assert removed.status_code == 200
            assert [r["ticket_id"] for r in removed.json()] == [other]
            assert (await client.delete(f"{base}/{incident}")).status_code == 404

            mismatch = await client.post(
                f"/api/v1/tickets/{incident}/change/create-from",
                json={
                    "title": "x",
                    "change_type": "normal",
                    "queue_id": Q_A,
                    "from_ticket_id": other,
                },
            )
            assert mismatch.status_code == 422
        async with _client(w, U_NONE) as outsider:
            denied = await outsider.post(
                f"/api/v1/tickets/{incident}/change/create-from",
                json={"title": "x", "change_type": "normal", "queue_id": Q_A},
            )
            assert denied.status_code == 404
