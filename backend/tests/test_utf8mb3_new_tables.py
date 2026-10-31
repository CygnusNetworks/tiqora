"""Four-byte text on the approval and change tables, on a real ``utf8mb3_general_ci`` schema.

Runs its own MariaDB with that server default, so every table (the Znuny DDL and the additive
``tiqora_*`` set) really is ``utf8mb3``. Each case writes emoji through a route or the service
and reads the stored value back: U+FFFD in place of the emoji, as on the ticket path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._approvals_helpers import (
    AG1,
    AG3,
    T1,
    approvals_session,
    as_client,
    ensure_tiqora_tables,
    installed_registry,
    make_fake_subject_type,
)
from tests._change_fixtures import Q_A, U_FULL, U_MANAGER, ChangeWorld, change_world, make_cab
from tests._fake_redis import FakeRedis
from tests.conftest import docker_available
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.approvals import lists, service, templates, tokens
from tiqora.approvals.contract import Actor, ApprovalDefinition
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalList,
    TiqoraApprovalStep,
    TiqoraApprovalTemplate,
    TiqoraApprovalVote,
)
from tiqora.approvals.settings import ApprovalSettings
from tiqora.bootstrap.schema_loader import load_sql_mysql
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.change.cab import CabIn, create_cab, update_cab
from tiqora.itsm.change.freeze import FreezeIn, create_freeze, update_freeze
from tiqora.itsm.change.models import (
    TiqoraItsmCab,
    TiqoraItsmCabAgendaItem,
    TiqoraItsmCabMeeting,
    TiqoraItsmChange,
    TiqoraItsmChangeEvent,
    TiqoraItsmChangeFreeze,
    TiqoraItsmChangeRiskQuestion,
    TiqoraItsmChangeTemplate,
)
from tiqora.itsm.change.risk import ChangeRiskQuestionIn, create_question, update_question
from tiqora.itsm.change.templates import TemplateIn, create_template, update_template

pytestmark = pytest.mark.db

SETTINGS = Settings(environment="test")
SUBJECT = "test.subject"
EMOJI = "🚀"
STORED = "�"


TEXT = f"Text {EMOJI} Ende"
CLEAN = f"Text {STORED} Ende"


@pytest.fixture(scope="module")
def mb3_url(znuny_schema_dir: Path) -> Iterator[str]:
    if not docker_available():
        pytest.skip("Docker not available")
    from testcontainers.mysql import MySqlContainer

    container = MySqlContainer("mariadb:10.11", dialect="pymysql").with_command(
        "--character-set-server=utf8mb3 --collation-server=utf8mb3_general_ci"
    )
    with container as mysql:
        url = mysql.get_connection_url()
        load_sql_mysql(url, znuny_schema_dir / "schema.mysql.sql")
        load_sql_mysql(url, znuny_schema_dir / "initial_insert.mysql.sql")
        load_sql_mysql(url, znuny_schema_dir / "schema-post.mysql.sql")
        ensure_tiqora_tables(url)
        yield url


def test_the_schema_really_is_utf8mb3_and_refuses_emoji_raw(mb3_url: str) -> None:
    """Guards the module itself: without this the other cases prove nothing."""
    engine = create_engine(mb3_url)
    with engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT default_collation_name FROM information_schema.schemata"
                    " WHERE schema_name = DATABASE()"
                )
            ).scalar_one()
            == "utf8mb3_general_ci"
        )
        for table, column in (("tiqora_approval", "title"), ("tiqora_itsm_change", "reason")):
            assert (
                conn.execute(
                    text(
                        "SELECT character_set_name FROM information_schema.columns WHERE"
                        " table_schema = DATABASE() AND table_name = :t AND column_name = :c"
                    ),
                    {"t": table, "c": column},
                ).scalar_one()
                == "utf8mb3"
            )
        assert "STRICT_TRANS_TABLES" in str(conn.execute(text("SELECT @@sql_mode")).scalar_one())
    engine.dispose()


# --- approvals -------------------------------------------------------------------------------


def _defn(name: str = "Prüfung") -> ApprovalDefinition:
    return ApprovalDefinition.model_validate(
        {"steps": [{"name": name, "approvers": [{"kind": "user", "ref": str(AG3)}]}]}
    )


@asynccontextmanager
async def _approvals(request: pytest.FixtureRequest) -> AsyncIterator[AsyncSession]:
    async with approvals_session(request, "mb3_url") as session:
        with installed_registry(make_fake_subject_type()):
            yield session


async def _approval(session: AsyncSession) -> int:
    info = await service.request_approval(
        session,
        ticket_id=T1,
        subject_type=SUBJECT,
        title=TEXT,
        description=TEXT,
        subject_ref=f"ref{EMOJI}",
        actor=Actor.agent(AG1),
        definition=_defn(f"Stufe {EMOJI}"),
        settings=SETTINGS,
    )
    await session.commit()
    return info.approval_id


async def test_approval_request_and_decision_through_the_routes(
    request: pytest.FixtureRequest,
) -> None:
    async with _approvals(request) as session:
        async with await as_client(session, agent=AG1) as client:
            created = await client.post(
                f"/api/v1/tickets/{T1}/approvals",
                json={
                    "title": TEXT,
                    "description": TEXT,
                    "definition": _defn(f"Stufe {EMOJI}").model_dump(mode="json"),
                },
            )
        assert created.status_code in (200, 201), created.text
        aid = created.json()["id"]
        row = await session.get(TiqoraApproval, aid, populate_existing=True)
        assert row is not None
        assert (row.title, row.description) == (CLEAN, CLEAN)
        step = (
            await session.execute(
                select(TiqoraApprovalStep).where(TiqoraApprovalStep.approval_id == aid)
            )
        ).scalar_one()
        assert step.name == f"Stufe {STORED}"
        assert EMOJI not in step.spec

        async with await as_client(session, agent=AG3) as client:
            voted = await client.post(
                f"/api/v1/approvals/{aid}/decision",
                json={"decision": "approved", "comment": TEXT},
            )
        assert voted.status_code == 200, voted.text
        vote = (
            await session.execute(
                select(TiqoraApprovalVote).where(TiqoraApprovalVote.approval_id == aid)
            )
        ).scalar_one()
        assert vote.comment == CLEAN


async def test_decision_comment_through_the_public_link_page(
    request: pytest.FixtureRequest,
) -> None:
    async with _approvals(request) as session:
        aid = await _approval(session)
        row = (
            await session.execute(
                select(TiqoraApprovalApprover).where(TiqoraApprovalApprover.approval_id == aid)
            )
        ).scalar_one()
        token = await tokens.issue_link(session, row.id, settings=ApprovalSettings())
        await session.commit()

        app = create_app(SETTINGS)

        async def _db() -> Any:
            yield session

        app.dependency_overrides[get_db] = _db
        app.state.redis = FakeRedis()
        transport = ASGITransport(app=app, client=("203.0.113.10", 4711))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            decided = await client.post(
                "/api/v1/approval-links/decide",
                json={"token": token, "decision": "rejected", "comment": TEXT},
            )
        assert decided.status_code == 200, decided.text
        vote = (
            await session.execute(
                select(TiqoraApprovalVote).where(TiqoraApprovalVote.approval_id == aid)
            )
        ).scalar_one()
        assert vote.comment == CLEAN


async def test_auto_decision_keeps_title_and_description_safe(
    request: pytest.FixtureRequest,
) -> None:
    async with _approvals(request) as session:
        info = await service.record_auto_decision(
            session,
            ticket_id=T1,
            subject_type=SUBJECT,
            title=TEXT,
            description=TEXT,
            actor=Actor.agent(AG1),
            status="approved",
            settings=SETTINGS,
        )
        await session.commit()
        row = await session.get(TiqoraApproval, info.approval_id, populate_existing=True)
        assert row is not None
        assert (row.title, row.description) == (CLEAN, CLEAN)


async def test_approval_lists_and_templates(request: pytest.FixtureRequest) -> None:
    async with _approvals(request) as session:
        made = await lists.create_list(
            session,
            name=f"Gruppe {EMOJI}",
            description=TEXT,
            member_ids=[AG3],
            actor_id=AG1,
        )
        await session.commit()
        row = await session.get(TiqoraApprovalList, made.id, populate_existing=True)
        assert row is not None
        assert (row.name, row.description) == (f"Gruppe {STORED}", CLEAN)
        await lists.update_list(
            session, made.id, name=f"Neu {EMOJI}", description=TEXT, actor_id=AG1
        )
        await session.commit()
        await session.refresh(row)
        assert row.name == f"Neu {STORED}"

        tpl = await templates.create_template(
            session,
            key=f"mb3-{EMOJI}",
            name=f"Vorlage {EMOJI}",
            description=TEXT,
            subject_types=[],
            definition=_defn(f"Stufe {EMOJI}"),
            actor_id=AG1,
        )
        await session.commit()
        stored = await session.get(TiqoraApprovalTemplate, tpl.id, populate_existing=True)
        assert stored is not None
        assert (stored.name, stored.description) == (f"Vorlage {STORED}", CLEAN)
        assert json.loads(stored.definition)["steps"][0]["name"] == f"Stufe {STORED}"
        await templates.update_template(
            session,
            tpl.id,
            name=f"Neu {EMOJI}",
            description=TEXT,
            definition=_defn(f"Neu {EMOJI}"),
            actor_id=AG1,
        )
        await session.commit()
        await session.refresh(stored)
        assert stored.name == f"Neu {STORED}"
        assert json.loads(stored.definition)["steps"][0]["name"] == f"Neu {STORED}"


# --- change ----------------------------------------------------------------------------------


def _user(uid: int) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uid, login=f"chg94.{uid}", first_name="E", last_name="B", auth_method="session"
    )


@asynccontextmanager
async def _change_api(
    request: pytest.FixtureRequest, uid: int = U_FULL
) -> AsyncIterator[tuple[ChangeWorld, AsyncClient, dict[str, AuthenticatedUser]]]:
    async with change_world(request, "mb3_url") as world:
        who = {"u": _user(uid)}

        async def _db() -> Any:
            yield world.session

        app = create_app(SETTINGS)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_session_factory_dep] = lambda: world.factory
        app.dependency_overrides[get_current_user] = lambda: who["u"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield world, client, who


async def _new_change(client: AsyncClient) -> int:
    got = await client.post(
        "/api/v1/itsm/changes",
        json={
            "title": f"Titel {EMOJI}",
            "change_type": "normal",
            "queue_id": Q_A,
            "reason": TEXT,
        },
    )
    assert got.status_code == 201, got.text
    return int(got.json()["change"]["ticket_id"])


async def test_change_create_and_plans_through_the_routes(request: pytest.FixtureRequest) -> None:
    async with _change_api(request) as (world, client, _who):
        tid = await _new_change(client)
        patched = await client.patch(
            f"/api/v1/itsm/changes/{tid}",
            json={
                "reason": TEXT,
                "implementation_plan": TEXT,
                "backout_plan": TEXT,
                "test_plan": TEXT,
            },
        )
        assert patched.status_code == 200, patched.text
        row = await world.session.get(TiqoraItsmChange, tid, populate_existing=True)
        assert row is not None
        assert (row.reason, row.implementation_plan, row.backout_plan, row.test_plan) == (
            CLEAN,
            CLEAN,
            CLEAN,
            CLEAN,
        )
        title = (
            await world.session.execute(text("SELECT title FROM ticket WHERE id = :i"), {"i": tid})
        ).scalar_one()
        assert title == f"Titel {STORED}"


async def test_change_risk_override_and_its_event(request: pytest.FixtureRequest) -> None:
    async with _change_api(request) as (world, client, who):
        tid = await _new_change(client)
        who["u"] = _user(U_MANAGER)
        put = await client.put(
            f"/api/v1/itsm/changes/{tid}/risk/override",
            json={"level": "high", "reason": TEXT},
        )
        assert put.status_code == 200, put.text
        row = await world.session.get(TiqoraItsmChange, tid, populate_existing=True)
        assert row is not None and row.risk_override_reason == CLEAN
        details = (
            await world.session.execute(
                select(TiqoraItsmChangeEvent.detail).where(
                    TiqoraItsmChangeEvent.ticket_id == tid,
                    TiqoraItsmChangeEvent.event == "risk_override",
                )
            )
        ).scalar_one()
        assert json.loads(details)["reason"] == CLEAN


async def test_cab_meeting_title_location_minutes_and_agenda(
    request: pytest.FixtureRequest,
) -> None:
    async with _change_api(request) as (world, client, who):
        tid = await _new_change(client)
        moved = await client.post(
            f"/api/v1/itsm/changes/{tid}/transition", json={"to": "submitted"}
        )
        assert moved.status_code == 200, moved.text
        cab_id = 946395
        await make_cab(world.session, cab_id=cab_id, members=[("user", str(U_FULL))])
        who["u"] = _user(U_MANAGER)
        made = await client.post(
            "/api/v1/itsm/cab-meetings",
            json={
                "cab_id": cab_id,
                "title": f"CAB {EMOJI}",
                "location": f"Raum {EMOJI}",
                "starts_at": "2026-11-05T09:00:00Z",
                "ends_at": "2026-11-05T10:00:00Z",
            },
        )
        assert made.status_code == 201, made.text
        mid = made.json()["id"]
        noted = await client.patch(
            f"/api/v1/itsm/cab-meetings/{mid}",
            json={
                "title": f"Neu {EMOJI}",
                "location": f"Neu {EMOJI}",
                "minutes": TEXT,
            },
        )
        assert noted.status_code == 200, noted.text
        agenda = await client.put(
            f"/api/v1/itsm/cab-meetings/{mid}/agenda",
            json={"items": [{"change_ticket_id": tid, "note": TEXT}]},
        )
        assert agenda.status_code == 200, agenda.text
        meeting = await world.session.get(TiqoraItsmCabMeeting, mid, populate_existing=True)
        assert meeting is not None
        assert (meeting.title, meeting.location, meeting.minutes) == (
            f"Neu {STORED}",
            f"Neu {STORED}",
            CLEAN,
        )
        item = (
            await world.session.execute(
                select(TiqoraItsmCabAgendaItem).where(TiqoraItsmCabAgendaItem.meeting_id == mid)
            )
        ).scalar_one()
        assert item.note == CLEAN


async def test_search_with_emoji_finds_the_sanitised_title(
    request: pytest.FixtureRequest,
) -> None:
    async with _change_api(request) as (_world, client, _who):
        await _new_change(client)
        got = await client.get("/api/v1/itsm/changes", params={"q": EMOJI})
        assert got.status_code == 200, got.text
        assert [c["title"] for c in got.json()["items"]] == [f"Titel {STORED}"]


async def test_cab_template_freeze_and_risk_question_admin(
    request: pytest.FixtureRequest,
) -> None:
    async with change_world(request, "mb3_url") as world:
        session = world.session
        cab = await create_cab(
            session,
            data=CabIn.model_validate({"name": f"CAB {EMOJI}", "description": TEXT, "rule": "any"}),
            user_id=1,
        )
        cab_row = await session.get(TiqoraItsmCab, cab.id, populate_existing=True)
        assert cab_row is not None
        assert (cab_row.name, cab_row.description) == (f"CAB {STORED}", CLEAN)

        freeze = await create_freeze(
            session,
            data=FreezeIn.model_validate(
                {
                    "name": f"Freeze {EMOJI}",
                    "note": TEXT,
                    "starts_at": "2026-11-06T18:00:00Z",
                    "ends_at": "2026-11-09T06:00:00Z",
                }
            ),
            user_id=1,
        )
        freeze_row = await session.get(TiqoraItsmChangeFreeze, freeze.id, populate_existing=True)
        assert freeze_row is not None
        assert (freeze_row.name, freeze_row.note) == (f"Freeze {STORED}", CLEAN)

        tpl = await create_template(
            session,
            SETTINGS,
            data=TemplateIn.model_validate(
                {
                    "name": f"Vorlage {EMOJI}",
                    "description": TEXT,
                    "change_type": "normal",
                    "defaults": {"implementation_plan": TEXT},
                    "tasks": [{"key": "t1", "title": f"Aufgabe {EMOJI}", "body": TEXT}],
                }
            ),
            user_id=1,
        )
        tpl_row = await session.get(TiqoraItsmChangeTemplate, tpl.id, populate_existing=True)
        assert tpl_row is not None
        assert (tpl_row.name, tpl_row.description) == (f"Vorlage {STORED}", CLEAN)
        assert json.loads(tpl_row.defaults)["implementation_plan"] == CLEAN
        assert json.loads(tpl_row.tasks)[0]["title"] == f"Aufgabe {STORED}"
        assert json.loads(tpl_row.tasks)[0]["body"] == CLEAN

        question = await create_question(
            session,
            data=ChangeRiskQuestionIn.model_validate(
                {
                    "question_key": "mb3",
                    "question": f"Frage {EMOJI}",
                    "help": TEXT,
                    "answers": [
                        {"key": "no", "label": f"Nein {EMOJI}", "points": 0},
                        {"key": "yes", "label": "Ja", "points": 4},
                    ],
                }
            ),
            user_id=1,
        )
        q_row = await session.get(TiqoraItsmChangeRiskQuestion, question.id, populate_existing=True)
        assert q_row is not None
        assert (q_row.question, q_row.help) == (f"Frage {STORED}", CLEAN)
        assert json.loads(q_row.answers)[0]["label"] == f"Nein {STORED}"

        await update_cab(
            session,
            cab_id=cab.id,
            data=CabIn.model_validate({"name": f"Neu {EMOJI}", "rule": "any"}),
            user_id=1,
        )
        await session.refresh(cab_row)
        assert cab_row.name == f"Neu {STORED}"
        await update_freeze(
            session,
            freeze_id=freeze.id,
            data=FreezeIn.model_validate(
                {
                    "name": f"Neu {EMOJI}",
                    "starts_at": "2026-11-06T18:00:00Z",
                    "ends_at": "2026-11-09T06:00:00Z",
                }
            ),
            user_id=1,
        )
        await session.refresh(freeze_row)
        assert freeze_row.name == f"Neu {STORED}"
        await update_template(
            session,
            SETTINGS,
            template_id=tpl.id,
            data=TemplateIn.model_validate({"name": f"Neu {EMOJI}", "change_type": "normal"}),
            user_id=1,
        )
        await session.refresh(tpl_row)
        assert tpl_row.name == f"Neu {STORED}"
        await update_question(
            session,
            question_id=question.id,
            data=ChangeRiskQuestionIn.model_validate(
                {
                    "question_key": "mb3",
                    "question": f"Neu {EMOJI}",
                    "answers": [
                        {"key": "no", "label": "Nein", "points": 0},
                        {"key": "yes", "label": "Ja", "points": 4},
                    ],
                }
            ),
            user_id=1,
        )
        await session.refresh(q_row)
        assert q_row.question == f"Neu {STORED}"
