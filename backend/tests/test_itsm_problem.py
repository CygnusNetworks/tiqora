"""Problem management (Task 19): ``/tickets/{id}/problem...`` and ``itsm.problem``, both dialects.

One rolled-back session carries the world (the routes' ``commit`` becomes ``flush``; the job's
sessions are the same session). Seed band 941400-941499. ``FULL`` holds rw on queue A and only
ro on queue R, ``RO`` ro on queue A, ``NOBODY`` nothing; queue B is out of reach for everybody.
Tickets: ``P`` problem (A), ``I1``/``I2`` open incidents (A), ``IC`` closed incident (A),
``IB`` incident in queue B, ``IR`` incident in queue R (ro only), ``PLAIN`` untyped ticket (A).
"""

from __future__ import annotations

import json
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
from tests.test_itsm_ci_api import (
    FULL,
    NOBODY,
    NOW,
    QUEUE_A,
    QUEUE_B,
    RO,
    _grant,
    _seed_access,
    _user,
)
from tests.test_itsm_linked_articles import _Factory
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.domain.ticket_write_service import link_objects
from tiqora.itsm.linked_articles import LinkedArticlesPayload, run_linked_articles
from tiqora.itsm.mode import SETTING_MODE
from tiqora.itsm.models import TiqoraItsmBulkTarget, TiqoraItsmProblem
from tiqora.itsm.settings import ITSM_SECTION
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.jobs.context import JobContext
from tiqora.kb.models import STATE_PUBLISHED, STATE_REVIEW, TiqoraKbArticle, TiqoraKbLink
from tiqora.kb.schemas import CategoryIn
from tiqora.kb.service import KbService
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

GROUP_R, QUEUE_R = 941410, 941411
P, I1, I2, IC, IB, IR, PLAIN = 941401, 941402, 941403, 941404, 941405, 941406, 941407
DIALECTS = ["mysql", "postgresql"]
STATE_WORKAROUND = "closed with workaround"
SECRET = Settings(environment="test").secret_key


@dataclass
class World:
    client: AsyncClient
    session: AsyncSession
    factory: _Factory
    as_user: dict[str, Any]
    category_id: int

    def login(self, uid: int) -> None:
        self.as_user["u"] = _user(uid)

    def url(self, ticket_id: int = P, tail: str = "") -> str:
        return f"/api/v1/tickets/{ticket_id}/problem{tail}"

    async def link(self, *incident_ids: int) -> None:
        for incident in incident_ids:
            await link_objects(
                self.session,
                source_object="Ticket",
                source_key=str(P),
                target_object="Ticket",
                target_key=str(incident),
                link_type="ParentChild",
                user_id=1,
            )

    async def configure(self, **values: Any) -> None:
        await save_section_values(self.session, ITSM_SECTION, values, secret_key=SECRET)

    async def state_of(self, ticket_id: int) -> str:
        return str(
            (
                await self.session.execute(
                    text(
                        "SELECT s.name FROM ticket t JOIN ticket_state s"
                        " ON s.id = t.ticket_state_id WHERE t.id = :t"
                    ),
                    {"t": ticket_id},
                )
            ).scalar_one()
        )

    async def article_count(self, ticket_id: int) -> int:
        return int(
            (
                await self.session.execute(
                    text("SELECT count(*) FROM article WHERE ticket_id = :t"), {"t": ticket_id}
                )
            ).scalar_one()
        )


async def _type_id(session: AsyncSession, name: str) -> int:
    found = (
        await session.execute(text("SELECT id FROM ticket_type WHERE name = :n"), {"n": name})
    ).scalar_one_or_none()
    if found is None:
        await session.execute(
            text(
                "INSERT INTO ticket_type (name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:n, 1, :t, 1, :t, 1)"
            ),
            {"n": name, "t": NOW},
        )
        found = (
            await session.execute(text("SELECT id FROM ticket_type WHERE name = :n"), {"n": name})
        ).scalar_one()
    return int(found)


async def _state_id(session: AsyncSession, name: str, type_name: str) -> int:
    found = (
        await session.execute(text("SELECT id FROM ticket_state WHERE name = :n"), {"n": name})
    ).scalar_one_or_none()
    if found is None:
        type_id = (
            await session.execute(
                text("SELECT id FROM ticket_state_type WHERE name = :n"), {"n": type_name}
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO ticket_state (name, type_id, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:n, :ty, 1, :t, 1, :t, 1)"
            ),
            {"n": name, "ty": type_id, "t": NOW},
        )
        found = (
            await session.execute(text("SELECT id FROM ticket_state WHERE name = :n"), {"n": name})
        ).scalar_one()
    return int(found)


async def _seed(session: AsyncSession) -> None:
    await _seed_access(session)
    await session.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 'prob941-r', 1, :t, 1, :t, 1)"
        ),
        {"id": GROUP_R, "t": NOW},
    )
    await _grant(session, FULL, GROUP_R, "ro")
    await session.execute(
        text(
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, 'prob941-qr', :g, 1, 1, 1, 1, 0, 1, :t, 1,"
            " :t, 1)"
        ),
        {"id": QUEUE_R, "g": GROUP_R, "t": NOW},
    )
    problem, incident = await _type_id(session, "Problem"), await _type_id(session, "Incident")
    plain = await _type_id(session, "Unclassified")
    open_state = await _state_id(session, "open", "open")
    closed_state = await _state_id(session, "closed successful", "closed")
    await _state_id(session, STATE_WORKAROUND, "closed")
    rows = (
        (P, QUEUE_A, problem, open_state, "Netzwerk fällt aus"),
        (I1, QUEUE_A, incident, open_state, "Incident eins"),
        (I2, QUEUE_A, incident, open_state, "Incident zwei"),
        (IC, QUEUE_A, incident, closed_state, "Incident erledigt"),
        (IB, QUEUE_B, incident, open_state, "Incident fremd"),
        (IR, QUEUE_R, incident, open_state, "Incident nur lesbar"),
        (PLAIN, QUEUE_A, plain, open_state, "Kein Incident"),
    )
    for tid, queue_id, type_id, state_id, title in rows:
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
                "ty": type_id,
                "st": state_id,
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
            kb = KbService(session, Settings(environment="test"))
            category = await kb.create_category(FULL, CategoryIn(name="Bekannte Fehler"))
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                yield World(c, session, _Factory(session), who, int(category.id))
    finally:
        clear_detect_cache()


@pytest.fixture(params=DIALECTS)
def dialect(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
async def w(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[World]:
    async with world_for(request, dialect) as world:
        yield world


async def test_save_problem_only_on_problem_type(w: World) -> None:
    refused = await w.client.put(w.url(I1), json={"root_cause": "x"})
    assert refused.status_code == 409 and refused.json()["detail"] == {"code": "not_a_problem"}
    assert (await w.client.get(w.url(PLAIN))).json()["detail"]["code"] == "not_a_problem"

    saved = await w.client.put(
        w.url(), json={"root_cause": " Kabelbruch ", "workaround": "Umstecken"}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["root_cause"] == "Kabelbruch" and saved.json()["workaround"] == "Umstecken"
    assert saved.json()["known_error"] is False and saved.json()["change_by"] == FULL
    again = await w.client.put(w.url(), json={"root_cause": "   ", "workaround": "Neu"})
    assert again.json()["root_cause"] is None and again.json()["workaround"] == "Neu"
    assert (await w.client.get(w.url())).json()["workaround"] == "Neu"
    assert path_to_area(w.url()) == "tickets"

    w.login(RO)
    assert (await w.client.get(w.url())).status_code == 200
    forbidden = await w.client.put(w.url(), json={"root_cause": "x"})
    assert forbidden.status_code == 403
    w.login(NOBODY)
    assert (await w.client.get(w.url())).status_code == 404  # 404_without_ro
    assert (await w.client.put(w.url(), json={})).status_code == 404


async def test_link_incidents_parentchild(w: World) -> None:
    got = await w.client.post(w.url(tail="/incidents"), json={"incident_ticket_ids": [I1, I2]})
    assert got.status_code == 200, got.text
    assert [i["ticket_id"] for i in got.json()["incidents"]] == [I1, I2]
    assert got.json()["incidents"][0]["closed"] is False
    rows = (
        await w.session.execute(
            text(
                "SELECT lr.source_key, lr.target_key, lt.name FROM link_relation lr"
                " JOIN link_type lt ON lt.id = lr.type_id WHERE lr.source_key = :p ORDER BY 2"
            ),
            {"p": str(P)},
        )
    ).all()
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (str(P), str(I1), "ParentChild"),
        (str(P), str(I2), "ParentChild"),
    ]
    history = (
        await w.session.execute(
            text(
                "SELECT count(*) FROM ticket_history h JOIN ticket_history_type ht"
                " ON ht.id = h.history_type_id WHERE ht.name = 'TicketLinkAdd' AND h.ticket_id = :t"
            ),
            {"t": I1},
        )
    ).scalar_one()
    assert history == 1  # same history Znuny writes
    # a repeat is idempotent
    from tiqora.itsm.problem import link_incidents
    from tiqora.itsm.settings import ItsmSettings

    assert await link_incidents(w.session, P, [I1, I2], user_id=FULL, cfg=ItsmSettings()) == 0


async def test_link_incidents_refusals(w: World) -> None:
    body = {"incident_ticket_ids": [I2]}
    assert (await w.client.post(w.url(I1, "/incidents"), json=body)).status_code == 409
    # no incident, unreadable and missing collapse to one answer, nothing is written
    for bad in (PLAIN, IB, 941499):
        got = await w.client.post(w.url(tail="/incidents"), json={"incident_ticket_ids": [I1, bad]})
        assert got.status_code == 404 and got.json()["detail"] == {"code": "incident_not_found"}
        assert str(bad) not in got.text
    too_many = await w.client.post(
        w.url(tail="/incidents"), json={"incident_ticket_ids": list(range(1, 52))}
    )
    assert too_many.status_code == 422
    count = (
        await w.session.execute(
            text("SELECT count(*) FROM link_relation WHERE source_key = :p"), {"p": str(P)}
        )
    ).scalar_one()
    assert count == 0
    w.login(RO)
    assert (await w.client.post(w.url(tail="/incidents"), json=body)).status_code == 403
    # a Normal link already joins the pair: Znuny's type group refuses ParentChild
    w.login(FULL)
    await link_objects(
        w.session,
        source_object="Ticket",
        source_key=str(P),
        target_object="Ticket",
        target_key=str(I2),
        link_type="Normal",
        user_id=1,
    )
    conflict = await w.client.post(w.url(tail="/incidents"), json={"incident_ticket_ids": [I1, I2]})
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "link_conflict"
    assert (
        await w.session.execute(
            text("SELECT count(*) FROM link_relation WHERE target_key = :i"), {"i": str(I1)}
        )
    ).scalar_one() == 0  # the first link was rolled back with the failed call


async def test_linked_incidents_ro_filtered(w: World) -> None:
    await w.link(I1, IB, IR, IC)
    listed = await w.client.get(w.url())
    assert listed.status_code == 200
    by_id = {i["ticket_id"]: i for i in listed.json()["incidents"]}
    assert sorted(by_id) == [I1, IC, IR]  # IB (queue B) is invisible
    assert by_id[IC]["closed"] is True and by_id[IC]["state_type"] == "closed"
    assert "941405" not in listed.text and by_id[I1]["type_name"] == "Incident"
    w.login(RO)
    assert sorted(i["ticket_id"] for i in (await w.client.get(w.url())).json()["incidents"]) == [
        I1,
        IC,
    ]


async def test_known_error_creates_review_article_and_kb_link(w: World) -> None:
    await w.configure(known_error_kb_category_id=w.category_id)
    await w.client.put(w.url(), json={"root_cause": "Kabelbruch", "workaround": "Umstecken"})
    got = await w.client.post(w.url(tail="/known-error"))
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["known_error"] is True and body["known_error_since"] is not None
    assert body["known_error_article_state"] == STATE_REVIEW
    article = await w.session.get(TiqoraKbArticle, body["known_error_article_id"])
    assert article is not None and article.state == STATE_REVIEW
    assert article.category_id == w.category_id and article.title == "Netzwerk fällt aus"
    assert "Kabelbruch" in article.content_md and "Umstecken" in article.content_md
    links = (
        (await w.session.execute(select(TiqoraKbLink).where(TiqoraKbLink.article_id == article.id)))
        .scalars()
        .all()
    )
    assert [(lk.object_type, lk.object_id) for lk in links] == [("ticket", P)]


async def test_known_error_updates_existing_article(
    w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    deindexed: list[int] = []

    async def fake_deindex(_self: KbService, article_id: int) -> None:  # search stays stubbed
        deindexed.append(article_id)

    monkeypatch.setattr(KbService, "_deindex_article", fake_deindex)
    await w.configure(known_error_kb_category_id=w.category_id)
    await w.client.put(w.url(), json={"root_cause": "Kabelbruch", "workaround": "Umstecken"})
    first = (await w.client.post(w.url(tail="/known-error"))).json()
    article = await w.session.get(TiqoraKbArticle, first["known_error_article_id"])
    assert article is not None
    article.state = STATE_PUBLISHED  # a reviewer published it
    await w.session.flush()

    # nothing changed: the published article stays published
    unchanged = (await w.client.post(w.url(tail="/known-error"))).json()
    assert unchanged["known_error_article_id"] == article.id
    assert unchanged["known_error_article_state"] == STATE_PUBLISHED

    await w.client.put(w.url(), json={"root_cause": "Kabelbruch", "workaround": "Anderes Kabel"})
    second = (await w.client.post(w.url(tail="/known-error"))).json()
    assert second["known_error_article_id"] == article.id  # same article, no duplicate
    assert second["known_error_since"] == first["known_error_since"]
    await w.session.refresh(article)
    assert "Anderes Kabel" in article.content_md and "Umstecken" not in article.content_md
    assert article.version == 2 and article.state == STATE_REVIEW  # changed text needs review
    assert deindexed == [article.id]  # the published version left the search index
    count = (
        await w.session.execute(select(func.count()).select_from(TiqoraKbArticle))
    ).scalar_one()
    links = (await w.session.execute(select(func.count()).select_from(TiqoraKbLink))).scalar_one()
    assert count == 1 and links == 1


async def test_known_error_needs_category(w: World) -> None:
    missing = await w.client.post(w.url(tail="/known-error"))
    assert missing.status_code == 409
    assert missing.json()["detail"] == {"code": "known_error_category_missing"}
    await w.configure(known_error_kb_category_id=w.category_id + 1000)  # configured but gone
    gone = await w.client.post(w.url(tail="/known-error"))
    assert gone.json()["detail"]["code"] == "known_error_category_missing"
    assert (
        await w.session.execute(select(func.count()).select_from(TiqoraKbArticle))
    ).scalar_one() == 0
    assert await w.session.get(TiqoraItsmProblem, P) is None
    # other tickets and missing rights come first
    assert (await w.client.post(w.url(I1, "/known-error"))).json()["detail"][
        "code"
    ] == "not_a_problem"
    w.login(RO)
    assert (await w.client.post(w.url(tail="/known-error"))).status_code == 403


async def _run_close_job(
    w: World, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    got = await w.client.post(w.url(tail="/close-incidents"), json=body or {})
    assert got.status_code == 202, got.text
    job_id = int(got.json()["job_id"])
    await w.session.execute(
        update(TiqoraJob).where(TiqoraJob.id == job_id).values(status="running", attempts=1)
    )
    raw = (
        await w.session.execute(select(TiqoraJob.payload).where(TiqoraJob.id == job_id))
    ).scalar_one()
    payload = LinkedArticlesPayload.model_validate_json(raw)
    ctx = JobContext(
        job_id=job_id,
        kind="core.itsm_linked_articles",
        created_by=FULL,
        settings=Settings(environment="test"),
        session_factory=w.factory,  # type: ignore[arg-type]
        attempt=1,
        max_attempts=2,
    )
    return job_id, await run_linked_articles(ctx, payload)


async def test_close_incidents_job_sets_workaround_state(w: World) -> None:
    await w.client.put(w.url(), json={"workaround": "Umstecken <b>jetzt</b>"})
    await w.link(I1, I2)
    job_id, result = await _run_close_job(w)
    assert sorted(result["done"]) == [I1, I2] and result["skipped"] == []
    for tid in (I1, I2):
        assert await w.state_of(tid) == STATE_WORKAROUND
        assert await w.article_count(tid) == 1
    note = (
        await w.session.execute(
            text(
                "SELECT m.a_subject, m.a_body, a.is_visible_for_customer FROM article a"
                " JOIN article_data_mime m ON m.article_id = a.id WHERE a.ticket_id = :t"
            ),
            {"t": I1},
        )
    ).one()
    assert note[0] == "Geschlossen mit Workaround: Netzwerk fällt aus"  # default subject (F29)
    assert "Umstecken" in note[1] and "<b>" not in note[1]  # escaped, not interpreted
    assert not note[2]
    job = (await w.session.execute(select(TiqoraJob).where(TiqoraJob.id == job_id))).scalar_one()
    payload = json.loads(job.payload)
    assert payload["origin"] == "problem_close_incidents" and payload["source_ticket_id"] == P
    assert payload["set_state_name"] == STATE_WORKAROUND and payload["mode"] == "note"


async def test_close_incidents_custom_note(w: World) -> None:
    await w.link(I1)
    _job, result = await _run_close_job(
        w, {"note_html": "<p>Eigener Text</p><script>alert(1)</script>"}
    )
    assert result["done"] == [I1]
    body = (
        await w.session.execute(
            text(
                "SELECT m.a_body FROM article a JOIN article_data_mime m ON m.article_id = a.id"
                " WHERE a.ticket_id = :t"
            ),
            {"t": I1},
        )
    ).scalar_one()
    assert "Eigener Text" in body and "<script" not in body


async def test_close_incidents_skips_closed_and_no_rw(w: World) -> None:
    await w.link(I1, IC, IR, IB)
    got = await w.client.post(w.url(tail="/close-incidents"), json={})
    assert got.status_code == 202
    job_id = int(got.json()["job_id"])
    payload = json.loads(
        (
            await w.session.execute(select(TiqoraJob.payload).where(TiqoraJob.id == job_id))
        ).scalar_one()
    )
    # closed and unreadable (queue B) incidents are not even targets
    assert payload["target_ticket_ids"] == [I1, IR]
    await w.session.execute(
        update(TiqoraJob).where(TiqoraJob.id == job_id).values(status="running", attempts=1)
    )
    ctx = JobContext(
        job_id=job_id,
        kind="core.itsm_linked_articles",
        created_by=FULL,
        settings=Settings(environment="test"),
        session_factory=w.factory,  # type: ignore[arg-type]
        attempt=1,
        max_attempts=2,
    )
    result = await run_linked_articles(ctx, LinkedArticlesPayload.model_validate(payload))
    assert result["done"] == [I1]
    assert result["skipped"] == [{"ticket_id": IR, "reason": "no_rw", "detail": None}]
    assert await w.state_of(I1) == STATE_WORKAROUND
    assert await w.state_of(IR) == "open" and await w.state_of(IC) == "closed successful"
    assert await w.article_count(IC) == 0
    rows = (await w.session.execute(select(TiqoraItsmBulkTarget.ticket_id))).scalars().all()
    assert sorted(int(r) for r in rows) == [I1, IR]


async def test_close_incidents_refusals(w: World) -> None:
    none_open = await w.client.post(w.url(tail="/close-incidents"), json={})
    assert none_open.status_code == 409 and none_open.json()["detail"] == {
        "code": "no_open_incidents"
    }
    await w.link(IC)  # a closed one does not count
    assert (await w.client.post(w.url(tail="/close-incidents"), json={})).status_code == 409
    await w.link(I1, I2)
    await w.configure(linked_articles_max_tickets=1)
    big = await w.client.post(w.url(tail="/close-incidents"), json={})
    assert big.status_code == 422 and big.json()["detail"]["code"] == "too_many_targets"
    await w.configure(linked_articles_max_tickets=200, workaround_state="Nicht vorhanden")
    gone = await w.client.post(w.url(tail="/close-incidents"), json={})
    assert gone.json()["detail"]["code"] == "workaround_state_missing"
    await w.configure(workaround_state=STATE_WORKAROUND)
    w.login(RO)
    assert (await w.client.post(w.url(tail="/close-incidents"), json={})).status_code == 403
    w.login(NOBODY)
    assert (await w.client.post(w.url(tail="/close-incidents"), json={})).status_code == 404
    jobs = (await w.session.execute(select(func.count()).select_from(TiqoraJob))).scalar_one()
    assert jobs == 0


async def test_off_returns_409(request: pytest.FixtureRequest, dialect: str) -> None:
    async with world_for(request, dialect, mode="off") as w:
        calls = (
            w.client.get(w.url()),
            w.client.put(w.url(), json={}),
            w.client.post(w.url(tail="/incidents"), json={"incident_ticket_ids": [I1]}),
            w.client.post(w.url(tail="/known-error")),
            w.client.post(w.url(tail="/close-incidents"), json={}),
        )
        for call in calls:
            got = await call
            assert got.status_code == 409 and got.json()["detail"] == {"code": "itsm_off"}
