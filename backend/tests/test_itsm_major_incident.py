"""Major incidents (Task 20): ``/tickets/{id}/major-incident...`` and ``itsm.major_incident``.

Both dialects. One rolled-back session carries the world (the routes' ``commit`` becomes
``flush``; the job's sessions are the same session). Seed band 941500-941599. ``FULL`` holds rw
on queue A, ``RO`` only ro, ``NOBODY`` nothing; queue B is out of reach for everybody.
Tickets: ``M`` incident (A, gets declared), ``L1``/``L2`` linked tickets (A), ``LB`` linked
ticket in queue B, ``MB`` incident in queue B, ``PLAIN`` unlinked ticket (A).
"""

from __future__ import annotations

import json
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tests._itsm_adapter_helpers import itsm_session
from tests._itsm_mode_helpers import itsm_url, profile_packages, seed_packages, store
from tests.test_itsm_ci_api import FULL, NOBODY, NOW, QUEUE_A, QUEUE_B, RO, _seed_access, _user
from tests.test_itsm_linked_articles import _Factory
from tests.test_itsm_problem import _state_id, _type_id
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.domain.ticket_write_service import TicketWriteService, link_objects
from tiqora.itsm.linked_articles import LinkedArticlesPayload, run_linked_articles
from tiqora.itsm.major_incident import active_banners
from tiqora.itsm.mode import SETTING_MODE
from tiqora.itsm.models import TiqoraItsmMajorIncident, TiqoraItsmMajorIncidentUpdate
from tiqora.itsm.settings import ITSM_SECTION
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.jobs.context import JobContext
from tiqora.jobs.service import utc_now
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

M, L1, L2, LB, MB, PLAIN = 941501, 941502, 941503, 941504, 941505, 941506
DIALECTS = ["mysql", "postgresql"]
SECRET = Settings(environment="test").secret_key
MAJOR_TYPE = "Incident::Major"


@dataclass
class World:
    client: AsyncClient
    session: AsyncSession
    factory: _Factory
    as_user: dict[str, Any]

    def login(self, uid: int) -> None:
        self.as_user["u"] = _user(uid)

    def url(self, ticket_id: int = M, tail: str = "") -> str:
        return f"/api/v1/tickets/{ticket_id}/major-incident{tail}"

    async def link(self, major: int, *others: int) -> None:
        for other in others:
            await link_objects(
                self.session,
                source_object="Ticket",
                source_key=str(major),
                target_object="Ticket",
                target_key=str(other),
                link_type="Normal",
                user_id=1,
            )

    async def configure(self, **values: Any) -> None:
        await save_section_values(self.session, ITSM_SECTION, values, secret_key=SECRET)

    async def type_of(self, ticket_id: int) -> str:
        return str(
            (
                await self.session.execute(
                    text(
                        "SELECT tt.name FROM ticket t JOIN ticket_type tt ON tt.id = t.type_id"
                        " WHERE t.id = :t"
                    ),
                    {"t": ticket_id},
                )
            ).scalar_one()
        )

    async def count(self, sql: str, **params: Any) -> int:
        return int((await self.session.execute(text(sql), params)).scalar_one())

    async def articles(self, ticket_id: int) -> int:
        return await self.count("SELECT count(*) FROM article WHERE ticket_id = :t", t=ticket_id)

    async def declare(self, ticket_id: int = M, **body: Any) -> Any:
        got = await self.client.post(self.url(ticket_id), json=body)
        assert got.status_code == 200, got.text
        return got.json()


async def _seed(session: AsyncSession) -> None:
    await _seed_access(session)
    incident = await _type_id(session, "Incident")
    await _type_id(session, MAJOR_TYPE)
    open_state = await _state_id(session, "open", "open")
    rows = (
        (M, QUEUE_A, "Netzwerk fällt aus"),
        (L1, QUEUE_A, "Mail langsam"),
        (L2, QUEUE_A, "Drucker offline"),
        (LB, QUEUE_B, "Fremdes Ticket"),
        (MB, QUEUE_B, "Fremder Major Incident"),
        (PLAIN, QUEUE_A, "Unverbunden"),
    )
    for tid, queue_id, title in rows:
        await session.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                " responsible_user_id, ticket_priority_id, ticket_state_id, timeout, until_time,"
                " escalation_time, escalation_update_time, escalation_response_time,"
                " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
                " change_by) VALUES (:id, :tn, :title, :q, 1, :ty, 1, 1, 3, :st, 0, 0, 0, 0, 0,"
                " 0, 0, :t, 1, :t, 1)"
            ),
            {
                "id": tid,
                "tn": f"20261013{tid}",
                "title": title,
                "q": queue_id,
                "ty": incident,
                "st": open_state,
                "t": NOW,
            },
        )


@asynccontextmanager
async def world_for(
    request: pytest.FixtureRequest, dialect: str, *, mode: str = "tiqora"
) -> AsyncIterator[World]:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    store(url, SETTING_MODE, mode)
    try:
        async with itsm_session(request, dialect, "itsm-6") as (session, _reader, _engine):
            await _seed(session)
            session.commit = session.flush  # type: ignore[method-assign]

            async def _db() -> Any:
                yield session

            who = {"u": _user(FULL)}
            app = create_app(Settings(environment="test"))
            app.dependency_overrides[get_db] = _db
            app.dependency_overrides[get_current_user] = lambda: who["u"]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                yield World(c, session, _Factory(session), who)
    finally:
        clear_detect_cache()


@pytest.fixture(params=DIALECTS)
def dialect(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
async def w(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[World]:
    async with world_for(request, dialect) as world:
        yield world


def _ctx(w: World, job_id: int) -> JobContext:
    return JobContext(
        job_id=job_id,
        kind="core.itsm_linked_articles",
        created_by=FULL,
        settings=Settings(environment="test"),
        session_factory=w.factory,  # type: ignore[arg-type]
        attempt=1,
        max_attempts=2,
    )


async def test_declare_changes_type_and_records(w: World) -> None:
    assert await w.type_of(M) == "Incident"
    body = await w.declare(summary="  Core-Switch ausgefallen  ")
    assert body["active"] is True and body["summary"] == "Core-Switch ausgefallen"
    assert body["declared_by"] == FULL and body["ended_at"] is None
    assert [e["kind"] for e in body["timeline"]] == ["declared"]
    assert await w.type_of(M) == MAJOR_TYPE
    history = await w.count(
        "SELECT count(*) FROM ticket_history h JOIN ticket_history_type ht"
        " ON ht.id = h.history_type_id WHERE ht.name = 'TypeUpdate' AND h.ticket_id = :t",
        t=M,
    )
    assert history == 1  # the history Znuny writes, from change_type only
    row = await w.session.get(TiqoraItsmMajorIncident, M)
    assert row is not None and row.declared_by == FULL and row.ended_at is None
    assert path_to_area(w.url()) == "tickets"
    # refusals: no rw, no ro, missing type
    w.login(RO)
    assert (await w.client.post(w.url(PLAIN), json={})).status_code == 403
    w.login(NOBODY)
    assert (await w.client.post(w.url(PLAIN), json={})).status_code == 404
    w.login(FULL)
    await w.configure(major_incident_type="Gibt es nicht")
    missing = await w.client.post(w.url(PLAIN), json={})
    assert missing.status_code == 409 and missing.json()["detail"] == {"code": "major_type_missing"}
    assert await w.session.get(TiqoraItsmMajorIncident, PLAIN) is None


async def test_declare_summary_is_one_line(w: World) -> None:
    """The summary is the reply subject; a line break in it would fail every e-mail."""
    body = await w.declare(summary="Core-Switch\r\nausgefallen\t im  RZ ")
    assert body["summary"] == "Core-Switch ausgefallen im RZ"


async def test_declare_twice_is_idempotent(w: World) -> None:
    first = await w.declare(summary="Erste")
    second = await w.declare(summary="Zweite")
    assert second["declared_at"] == first["declared_at"] and second["summary"] == "Erste"
    assert (
        await w.count(
            "SELECT count(*) FROM ticket_history h JOIN ticket_history_type ht"
            " ON ht.id = h.history_type_id WHERE ht.name = 'TypeUpdate' AND h.ticket_id = :t",
            t=M,
        )
        == 1
    )
    rows = (
        await w.session.execute(select(func.count()).select_from(TiqoraItsmMajorIncident))
    ).scalar_one()
    assert rows == 1
    # without a JSON body at all
    other = await w.client.post(w.url(L1))
    assert other.status_code == 200 and other.json()["summary"] is None


async def test_end_keeps_type(w: World) -> None:
    await w.declare()
    ended = await w.client.delete(w.url())
    assert ended.status_code == 200, ended.text
    body = ended.json()
    assert body["active"] is False and body["ended_by"] == FULL and body["ended_at"] is not None
    assert [e["kind"] for e in body["timeline"]] == ["declared", "ended"]
    assert await w.type_of(M) == MAJOR_TYPE  # Znuny has no "end"
    again = await w.client.delete(w.url())
    assert again.status_code == 409 and again.json()["detail"] == {"code": "not_active"}
    assert (await w.client.get(w.url())).json()["active"] is False
    assert (await w.client.delete(w.url(PLAIN))).json()["detail"] == {"code": "no_major_incident"}
    # an ended incident takes no more updates; declaring again starts a fresh run
    refused = await w.client.post(
        w.url(tail="/updates"), json={"mode": "internal", "body_html": "x"}
    )
    assert refused.status_code == 409 and refused.json()["detail"] == {"code": "not_active"}
    again = await w.declare(summary="Neu")
    assert again["active"] is True and [e["kind"] for e in again["timeline"]] == ["declared"]


async def test_internal_update_only_on_major_ticket(w: World) -> None:
    await w.link(M, L1, L2)
    nothing = await w.client.post(
        w.url(tail="/updates"), json={"mode": "internal", "body_html": "<p>x</p>"}
    )
    assert nothing.status_code == 404 and nothing.json()["detail"] == {"code": "no_major_incident"}
    assert await w.articles(M) == 0
    await w.declare(summary="Ausfall")
    got = await w.client.post(
        w.url(tail="/updates"),
        json={"mode": "internal", "body_html": "<p>Ursache <b>gefunden</b></p>"},
    )
    assert got.status_code == 201, got.text
    body = got.json()
    assert body["job_id"] is None and body["update"]["mode"] == "internal"
    assert body["update"]["target_count"] == 0
    assert await w.articles(M) == 1  # internal note on the major ticket ...
    assert await w.articles(L1) == 0 and await w.articles(L2) == 0  # ... and nowhere else
    visible = await w.count(
        "SELECT count(*) FROM article WHERE ticket_id = :t AND is_visible_for_customer = 1", t=M
    )
    assert visible == 0
    assert (await w.session.execute(select(func.count()).select_from(TiqoraJob))).scalar_one() == 0
    empty = await w.client.post(
        w.url(tail="/updates"), json={"mode": "internal", "body_html": "<p> </p>"}
    )
    assert empty.status_code == 422 and empty.json()["detail"] == {"code": "empty_body"}
    w.login(RO)
    forbidden = await w.client.post(
        w.url(tail="/updates"), json={"mode": "internal", "body_html": "x"}
    )
    assert forbidden.status_code == 403


async def test_note_update_enqueues_job_for_linked(w: World) -> None:
    await w.link(M, L1, L2, LB)
    await w.declare(summary="Ausfall")
    got = await w.client.post(
        w.url(tail="/updates"),
        json={"mode": "note", "body_html": "<p>Wir arbeiten daran.</p><script>alert(1)</script>"},
    )
    assert got.status_code == 201, got.text
    job_id = int(got.json()["job_id"])
    assert got.json()["update"]["target_count"] == 2  # LB (queue B) is not even counted
    assert "<script" not in got.json()["update"]["body"]
    raw = (
        await w.session.execute(select(TiqoraJob.payload).where(TiqoraJob.id == job_id))
    ).scalar_one()
    payload = json.loads(raw)
    assert payload["origin"] == "major_incident_update" and payload["mode"] == "note"
    assert payload["target_ticket_ids"] == [L1, L2] and payload["source_ticket_id"] == M
    assert payload["visible_for_customer"] is False
    stored = (await w.session.execute(select(TiqoraItsmMajorIncidentUpdate))).scalar_one()
    assert stored.job_id == job_id and payload["major_update_id"] == stored.id
    await w.session.execute(
        update(TiqoraJob).where(TiqoraJob.id == job_id).values(status="running", attempts=1)
    )
    result = await run_linked_articles(
        _ctx(w, job_id), LinkedArticlesPayload.model_validate(payload)
    )
    assert sorted(result["done"]) == [L1, L2] and result["skipped"] == []
    assert await w.articles(L1) == 1 and await w.articles(LB) == 0
    assert await w.articles(M) == 1  # the internal note on the major ticket


async def test_reply_update_refused_when_setting_off(w: World) -> None:
    await w.link(M, L1)
    await w.declare()
    refused = await w.client.post(
        w.url(tail="/updates"), json={"mode": "reply", "body_html": "<p>Hallo</p>"}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == {"code": "customer_reply_disabled"}
    assert await w.articles(M) == 0
    assert (
        await w.session.execute(select(func.count()).select_from(TiqoraItsmMajorIncidentUpdate))
    ).scalar_one() == 0
    assert (await w.session.execute(select(func.count()).select_from(TiqoraJob))).scalar_one() == 0
    await w.configure(major_incident_customer_reply=True)
    allowed = await w.client.post(
        w.url(tail="/updates"), json={"mode": "reply", "body_html": "<p>Hallo</p>"}
    )
    assert allowed.status_code == 201, allowed.text
    raw = (
        await w.session.execute(
            select(TiqoraJob.payload).where(TiqoraJob.id == allowed.json()["job_id"])
        )
    ).scalar_one()
    assert json.loads(raw)["mode"] == "reply"
    await w.configure(linked_articles_max_tickets=1)
    await w.link(M, L2)
    big = await w.client.post(
        w.url(tail="/updates"), json={"mode": "note", "body_html": "<p>x</p>"}
    )
    assert big.status_code == 422 and big.json()["detail"]["code"] == "too_many_targets"


async def test_timeline_order(w: World) -> None:
    await w.declare(summary="Start")
    for text_ in ("<p>Eins</p>", "<p>Zwei</p><script>x</script>", "<p>Drei</p>"):
        got = await w.client.post(
            w.url(tail="/updates"), json={"mode": "internal", "body_html": text_}
        )
        assert got.status_code == 201
    await w.client.delete(w.url())
    timeline = (await w.client.get(w.url())).json()["timeline"]
    assert [e["kind"] for e in timeline] == ["declared", "update", "update", "update", "ended"]
    assert timeline[0]["summary"] == "Start"
    bodies = [e["body"] for e in timeline if e["kind"] == "update"]
    assert "Eins" in bodies[0] and "Zwei" in bodies[1] and "Drei" in bodies[2]
    assert "<script" not in bodies[1]  # sanitised on write (F12)
    ids = [e["update_id"] for e in timeline if e["kind"] == "update"]
    assert ids == sorted(ids)
    # reading needs ro; a ticket that was never declared has no major incident
    w.login(RO)
    assert (await w.client.get(w.url())).status_code == 200
    w.login(NOBODY)
    assert (await w.client.get(w.url())).status_code == 404  # 404_without_ro
    w.login(FULL)
    assert (await w.client.get(w.url(PLAIN))).json()["detail"] == {"code": "no_major_incident"}


async def test_banner_for_linked_ticket(w: World) -> None:
    await w.link(M, L1)
    assert await active_banners(w.session, [L1, L2, PLAIN], user_id=FULL) == {}
    await w.declare(summary="Core-Switch")
    banners = await active_banners(w.session, [L1, L2, M, PLAIN], user_id=FULL)
    assert sorted(banners) == [L1]  # not the unlinked ones, not the major ticket itself
    b = banners[L1]
    assert (b.ticket_id, b.title, b.summary) == (M, "Netzwerk fällt aus", "Core-Switch")
    assert b.tn == f"20261013{M}"
    assert await active_banners(w.session, [], user_id=FULL) == {}
    # link direction does not matter
    await w.link(L2, M)
    assert sorted(await active_banners(w.session, [L1, L2], user_id=FULL)) == [L1, L2]


async def test_banner_hidden_without_ro_on_major_ticket(w: World) -> None:
    await w.link(MB, L1)
    w.session.add(
        TiqoraItsmMajorIncident(
            ticket_id=MB, summary="geheim", declared_at=utc_now(), declared_by=1
        )
    )
    await w.session.flush()
    assert await active_banners(w.session, [L1], user_id=FULL) == {}
    assert await active_banners(w.session, [L1], user_id=NOBODY) == {}
    # the ticket itself answers 404, not 403, so its existence stays hidden
    assert (await w.client.get(w.url(MB))).status_code == 404
    assert (
        await w.client.post(w.url(MB, "/updates"), json={"mode": "internal", "body_html": "x"})
    ).status_code == 404


async def test_banner_gone_after_end(w: World) -> None:
    await w.link(M, L1)
    await w.declare()
    assert sorted(await active_banners(w.session, [L1], user_id=FULL)) == [L1]
    await w.client.delete(w.url())
    assert await active_banners(w.session, [L1], user_id=FULL) == {}


async def test_update_uses_write_service_for_note(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[int, int]] = []
    original = TicketWriteService.add_article

    async def spy(self: TicketWriteService, user_id: int, ticket_id: int, article: Any) -> int:
        seen.append((user_id, ticket_id))
        return await original(self, user_id, ticket_id, article)

    monkeypatch.setattr(TicketWriteService, "add_article", spy)
    await w.declare()
    await w.client.post(w.url(tail="/updates"), json={"mode": "internal", "body_html": "<p>x</p>"})
    assert seen == [(FULL, M)]


async def test_off_returns_409(request: pytest.FixtureRequest, dialect: str) -> None:
    async with world_for(request, dialect, mode="off") as w:
        calls = (
            w.client.get(w.url()),
            w.client.post(w.url(), json={}),
            w.client.delete(w.url()),
            w.client.post(w.url(tail="/updates"), json={"mode": "internal", "body_html": "x"}),
        )
        for call in calls:
            got = await call
            assert got.status_code == 409 and got.json()["detail"] == {"code": "itsm_off"}


async def test_vetoed_update_note_is_422_and_stores_nothing(
    w: World, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R16: the update note goes through TicketWriteService.add_article, so a plugin's
    ``article.create`` veto refuses it (422 with the German text), not a 500."""
    from tests._plugin_points import boot_points_plugin, points_behaviour
    from tiqora.plugins.sync_hooks import SyncBreaker

    await w.link(M, L1)
    await w.declare(summary="Ausfall")
    SyncBreaker.reset_for_tests()
    SyncBreaker.persistence = False
    boot_points_plugin(tmp_path, monkeypatch)
    try:
        points_behaviour().MODE["hook.article.create"] = "veto"
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            got = await w.client.post(
                w.url(tail="/updates"), json={"mode": "note", "body_html": "<p>Stand</p>"}
            )
        assert got.status_code == 422 and got.json()["detail"] == "Vertragsnummer fehlt"
        # m7: no deprecated status constant on this path
        assert not [c for c in caught if "HTTP_422_UNPROCESSABLE_ENTITY" in str(c.message)]
        assert await w.articles(M) == 0
        updates = select(func.count()).select_from(TiqoraItsmMajorIncidentUpdate)
        assert (await w.session.execute(updates)).scalar_one() == 0
        jobs = select(func.count()).select_from(TiqoraJob)
        assert (await w.session.execute(jobs)).scalar_one() == 0
    finally:
        SyncBreaker.reset_for_tests()
