"""Change impact: stored affected set, job, triggers, refresh tick and the filtered read (both
dialects).

Seed band 946600-946699: tickets 946610-946629, services 946630-946632, classes/CIs come from the
CMDB services (autoincrement, rolled back with the world). Users, groups and queues are the
shared ones of ``tests._change_fixtures`` (``G_B``/``Q_B`` are out of reach of ``U_FULL``).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, delete, select, text, update

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
    URL_FIXTURES,
    ChangeWorld,
    change_world,
    make_change,
)
from tests._cmdb_helpers import make_ci, make_class
from tests._itsm_mode_helpers import make_factory, plain_url
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.seed import seed_defaults
from tiqora.cmdb.service import system_access
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob, TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.settings_store import (
    KEY_ITSM_CHANGE_ENABLED,
    KEY_ITSM_CHANGE_INTERVAL_SECONDS,
)
from tiqora.extensions import Registry, get_registry, set_registry_for_tests
from tiqora.extensions.core import register_core
from tiqora.itsm.change import impact as impact_module
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.impact import (
    ITSM_CHANGE_IMPACT_JOB,
    ITSM_CHANGE_IMPACT_KIND,
    ChangeImpactPayload,
    enqueue_impact,
    impact_is_current,
    read_impact,
    recompute_impact,
    refresh_stale_impacts,
    run_change_impact_job,
)
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeEvent,
    TiqoraItsmChangeImpact,
    TiqoraItsmChangeService,
)
from tiqora.itsm.change.service import (
    CHANGE_LISTENERS,
    ChangeCreateIn,
    ChangeEvent,
    ChangeListener,
    ChangeUpdateIn,
    adopt_ticket,
    create_change,
    update_change,
)
from tiqora.itsm.change.settings import ItsmChangeSettings
from tiqora.jobs.context import JobContext
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.jobs.service import utc_now
from tiqora.worker.itsm_change import run_itsm_change_tick_in
from tiqora.worker.services import DAEMON_SERVICES_BY_SLUG
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

SERVICE_IMPACTED = 946632
T_PLAIN = 946610
TICKET_NO_BASE = 946620
_T = datetime(2026, 10, 1)


@asynccontextmanager
async def _world(request: pytest.FixtureRequest, url_fixture: str) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, url_fixture) as world:
        await seed_defaults(world.session)
        await world.session.execute(
            text(
                "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:id, 'chg94-service-impacted', 1, :t, 1, :t, 1)"
            ),
            {"id": SERVICE_IMPACTED, "t": _T},
        )
        yield world


class _Factory:
    """``session_factory`` handing out the test's one session and never closing it."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def __call__(self) -> _Factory:
        return self

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *exc: object) -> None:
        return None


def _ctx(w: ChangeWorld) -> JobContext:
    return JobContext(
        job_id=946601,
        kind=ITSM_CHANGE_IMPACT_KIND,
        created_by=U_FULL,
        settings=Settings(environment="test"),
        session_factory=_Factory(w.session),  # type: ignore[arg-type]
        attempt=1,
        max_attempts=3,
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


async def _link(
    w: ChangeWorld, source: tuple[str, int], target: tuple[str, int], type_: str
) -> None:
    await LinkService(w.session).add(
        system_access(U_FULL),
        source=LinkEnd(source[0], str(source[1])),  # type: ignore[arg-type]
        target=LinkEnd(target[0], str(target[1])),  # type: ignore[arg-type]
        link_type_key=type_,
        enforce_permissions=False,
    )


async def _ci(w: ChangeWorld, name: str, *, group_id: int, class_key: str) -> int:
    class_id = (
        await w.session.execute(
            text("SELECT id FROM tiqora_cmdb_class WHERE class_key = :k"), {"k": class_key}
        )
    ).scalar_one_or_none() or await make_class(w.session, key=class_key, group_id=group_id)
    return await make_ci(w.session, int(class_id), name, user_id=U_FULL)


async def _rows(w: ChangeWorld, ticket_id: int) -> set[tuple[str, str, bool]]:
    got = await w.session.execute(
        select(
            TiqoraItsmChangeImpact.kind,
            TiqoraItsmChangeImpact.ref_key,
            TiqoraItsmChangeImpact.direct,
        ).where(TiqoraItsmChangeImpact.ticket_id == ticket_id)
    )
    return {(str(k), str(r), bool(d)) for k, r, d in got.all()}


async def _jobs(w: ChangeWorld, ticket_id: int) -> list[TiqoraJob]:
    got = await w.session.execute(
        select(TiqoraJob)
        .where(
            TiqoraJob.kind == ITSM_CHANGE_IMPACT_KIND, TiqoraJob.dedupe_key == f"change:{ticket_id}"
        )
        .order_by(TiqoraJob.id)
    )
    return list(got.scalars())


async def _row(w: ChangeWorld, ticket_id: int) -> TiqoraItsmChange:
    row = await w.session.get(TiqoraItsmChange, ticket_id)
    assert row is not None
    await w.session.refresh(row)
    return row


async def _events(w: ChangeWorld, ticket_id: int, event: str) -> list[TiqoraItsmChangeEvent]:
    got = await w.session.execute(
        select(TiqoraItsmChangeEvent)
        .where(TiqoraItsmChangeEvent.ticket_id == ticket_id, TiqoraItsmChangeEvent.event == event)
        .order_by(TiqoraItsmChangeEvent.id)
    )
    return list(got.scalars())


async def _ticket(
    w: ChangeWorld,
    *,
    queue_id: int,
    type_name: str | None,
    state: str,
    service_id: int | None,
) -> int:
    """A bare Znuny ticket row for the incident / plain-ticket cases; its (auto) id.

    No explicit id: the auto-increment counter of ``ticket`` keeps advancing across rolled-back
    tests, so a fixed id would meet a ticket another test created."""
    tn = f"{TICKET_NO_BASE}{time.time_ns()}"
    type_id = None
    if type_name is not None:
        type_id = (
            await w.session.execute(
                text("SELECT id FROM ticket_type WHERE name = :n"), {"n": type_name}
            )
        ).scalar_one_or_none()
        if type_id is None:
            type_id = (
                int((await w.session.execute(text("SELECT MAX(id) FROM ticket_type"))).scalar_one())
                + 1
            )  # smallint
            await w.session.execute(
                text(
                    "INSERT INTO ticket_type (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:i, :n, 1, :t, 1, :t, 1)"
                ),
                {"i": type_id, "n": type_name, "t": _T},
            )
    state_id = (
        await w.session.execute(text("SELECT id FROM ticket_state WHERE name = :n"), {"n": state})
    ).scalar_one()
    await w.session.execute(
        text(
            "INSERT INTO ticket (tn, title, queue_id, ticket_lock_id, type_id, service_id,"
            " user_id, responsible_user_id, ticket_priority_id, ticket_state_id, timeout,"
            " until_time, escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
            " change_by) VALUES (:tn, :title, :q, 1, :ty, :svc, 1, 1, 3, :s, 0, 0, 0, 0, 0,"
            " 0, 0, :t, 1, :t, 1)"
        ),
        {
            "tn": tn,
            "title": f"chg94 ticket {tn}",
            "q": queue_id,
            "ty": type_id if type_id is not None else 1,
            "svc": service_id,
            "s": state_id,
            "t": _T,
        },
    )
    return int(
        (
            await w.session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})
        ).scalar_one()
    )


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


def _with_listener(request: pytest.FixtureRequest, listener: ChangeListener) -> None:
    """The real registry plus one change listener, for this test only."""
    fresh = Registry()
    register_core(fresh)
    fresh.add(CHANGE_LISTENERS, listener)
    fresh.freeze()
    set_registry_for_tests(fresh)
    request.addfinalizer(lambda: set_registry_for_tests(None))


# --- affected set ----------------------------------------------------------------------------


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_impact_job_replaces_rows_and_sets_time(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _world(request, url_fixture) as w:
        tid = await _change(w, service_id=SERVICE)
        app = await _ci(w, "srv-app-01.example.org", group_id=G_A, class_key="chg94srv")
        db = await _ci(w, "srv-db-01.example.org", group_id=G_A, class_key="chg94srv")
        await _link(w, ("ci", app), ("ci", db), "DependsOn")
        await _link(w, ("service", SERVICE_IMPACTED), ("ci", db), "DependsOn")
        await _link(w, ("ci", app), ("ticket", tid), "RelevantTo")
        assert await _rows(w, tid) == set()  # nothing before the job ran

        before = utc_now()
        result = await run_change_impact_job(
            _ctx(w), ChangeImpactPayload(ticket_id=tid, user_id=U_FULL)
        )

        assert result["passes"] == 1 and result["cis"] == 2 and result["services"] == 2
        assert await _rows(w, tid) == {
            ("ci", str(app), True),
            ("ci", str(db), False),  # reached through DependsOn
            ("service", str(SERVICE), True),  # the ticket's own service
            ("service", str(SERVICE_IMPACTED), False),  # reached through the CI
        }
        row = await _row(w, tid)
        assert row.impact_computed_at is not None
        # a request stamp is always in whole seconds and can run a few seconds ahead
        assert before - timedelta(seconds=3) <= row.impact_computed_at
        assert row.impact_computed_at <= utc_now() + timedelta(seconds=5)
        assert row.impact_truncated is False
        events = await _events(w, tid, "impact")
        assert [(e.created_by, json.loads(e.detail or "{}")) for e in events] == [
            (U_FULL, {"cis": 2, "services": 2, "truncated": False})
        ]  # counts only, no ids (ruling F26)


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_impact_set_is_replaced_and_listener_notified(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _world(request, url_fixture) as w:
        tid = await _change(w, service_id=SERVICE)
        app = await _ci(w, "srv-app-01.example.org", group_id=G_A, class_key="chg94srv")
        await _link(w, ("ci", app), ("ticket", tid), "RelevantTo")
        seen: list[ChangeEvent] = []

        async def on_impact(_s: Any, event: ChangeEvent) -> None:
            seen.append(event)

        _with_listener(
            request,
            ChangeListener(key="chg94.impact", events=frozenset({"impact"}), on_event=on_impact),
        )

        await recompute_impact(w.session, ticket_id=tid, user_id=None)
        assert [(e.event, e.ticket_id, e.user_id) for e in seen] == [("impact", tid, 1)]
        assert (await _events(w, tid, "impact"))[0].created_by == 1  # the system actor

        # unchanged set: no new event, no listener call
        await recompute_impact(w.session, ticket_id=tid, user_id=None)
        assert len(seen) == 1 and len(await _events(w, tid, "impact")) == 1

        # the CI link goes away: the set is replaced as a whole
        await w.session.execute(
            text("DELETE FROM tiqora_itsm_link WHERE target_kind = 'ticket' AND target_key = :t"),
            {"t": str(tid)},
        )
        await recompute_impact(w.session, ticket_id=tid, user_id=None)
        assert await _rows(w, tid) == {("service", str(SERVICE), True)}
        assert len(seen) == 2


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_impact_is_job_and_idempotent(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _world(request, url_fixture) as w:
        tid = await _change(w, service_id=SERVICE)
        handler = get_registry().get(JOB_HANDLERS, ITSM_CHANGE_IMPACT_KIND)
        assert handler is ITSM_CHANGE_IMPACT_JOB
        assert handler.title_key == "jobs.kinds.core.itsm_change_impact"

        # create_change queued one job; asking again joins it (one active job per change)
        (queued,) = await _jobs(w, tid)
        assert queued.status == "queued" and queued.created_by == U_FULL
        assert json.loads(queued.payload) == {"ticket_id": tid, "user_id": U_FULL}
        assert await enqueue_impact(w.session, ticket_id=tid, user_id=U_FULL) == queued.id
        assert len(await _jobs(w, tid)) == 1
        row = await _row(w, tid)
        assert row.impact_requested_at is not None and not impact_is_current(row)

        await run_change_impact_job(_ctx(w), ChangeImpactPayload(ticket_id=tid, user_id=U_FULL))
        first = await _rows(w, tid)
        row = await _row(w, tid)
        assert first == {("service", str(SERVICE), True)} and impact_is_current(row)
        stamp = row.impact_computed_at

        again = await run_change_impact_job(
            _ctx(w), ChangeImpactPayload(ticket_id=tid, user_id=U_FULL)
        )
        assert again["passes"] == 1
        assert await _rows(w, tid) == first  # the same rows
        assert len(await _events(w, tid, "impact")) == 1  # nothing new to tell
        assert (await _row(w, tid)).impact_computed_at >= stamp  # type: ignore[operator]


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_request_during_a_pass_runs_it_again_up_to_three_passes(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _world(request, url_fixture) as w:
        tid = await _change(w, service_id=SERVICE)
        real = impact_module.recompute_impact
        bumps = {"left": 1}

        async def bumping(session: Any, *, ticket_id: int, user_id: int | None = None) -> Any:
            out = await real(session, ticket_id=ticket_id, user_id=user_id)
            if bumps["left"] > 0:  # a link arrives while the pass is running
                bumps["left"] -= 1
                row = await session.get(TiqoraItsmChange, ticket_id)
                row.impact_requested_at = impact_module._next_stamp(row.impact_requested_at)
            return out

        monkeypatch.setattr(impact_module, "recompute_impact", bumping)
        payload = ChangeImpactPayload(ticket_id=tid, user_id=U_FULL)

        assert (await run_change_impact_job(_ctx(w), payload))["passes"] == 2
        bumps["left"] = 99  # requests never stop: the job gives up after three passes
        assert (await run_change_impact_job(_ctx(w), payload))["passes"] == 3
        row = await _row(w, tid)
        assert not impact_is_current(row)  # the newest request is still open

        # the job gave up: the next refresh tick queues the change again, no 24 h wait
        await w.session.execute(
            update(TiqoraJob)
            .where(TiqoraJob.dedupe_key == f"change:{tid}")
            .values(status="succeeded")
        )
        assert await refresh_stale_impacts(w.session, ItsmChangeSettings()) == 1
        assert len(await _jobs(w, tid)) == 2


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_create_and_update_enqueue_impact(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _world(request, url_fixture) as w:
        tid = await _change(w, service_id=SERVICE)
        (job,) = await _jobs(w, tid)
        assert (await _row(w, tid)).impact_requested_at is not None

        # a window change does not move the affected set
        mark = (await _row(w, tid)).impact_requested_at
        await update_change(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            ticket_id=tid,
            data=ChangeUpdateIn(
                planned_start=datetime(2026, 11, 7, 20), planned_end=datetime(2026, 11, 7, 22)
            ),
            user_id=U_FULL,
        )
        assert (await _row(w, tid)).impact_requested_at == mark

        # other services do; the queued job is joined, the request is stamped again
        w.session.info.pop(impact_module._STAMPED_KEY, None)  # a new request = a new transaction
        await update_change(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            ticket_id=tid,
            data=ChangeUpdateIn(service_ids=[SERVICE_B]),
            user_id=U_FULL,
        )
        assert [j.id for j in await _jobs(w, tid)] == [job.id]
        assert (await _row(w, tid)).impact_requested_at > mark  # type: ignore[operator]

        # once the job is done a new request queues a new one
        w.session.info.pop(impact_module._STAMPED_KEY, None)
        await w.session.execute(
            update(TiqoraJob).where(TiqoraJob.id == job.id).values(status="succeeded")
        )
        await update_change(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            ticket_id=tid,
            data=ChangeUpdateIn(service_ids=[]),
            user_id=U_FULL,
        )
        assert len(await _jobs(w, tid)) == 2

        # adopting an existing change ticket queues one as well
        adopt = await _change(w)
        await w.session.execute(delete(TiqoraItsmChange).where(TiqoraItsmChange.ticket_id == adopt))
        await w.session.execute(delete(TiqoraJob).where(TiqoraJob.dedupe_key == f"change:{adopt}"))
        w.session.expunge_all()
        await adopt_ticket(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            ticket_id=adopt,
            change_type="normal",
            user_id=U_FULL,
        )
        assert len(await _jobs(w, adopt)) == 1


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_link_change_triggers_impact(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _world(request, url_fixture) as w:
        tid = await _change(w)
        await w.session.execute(
            update(TiqoraJob)
            .where(TiqoraJob.dedupe_key == f"change:{tid}")
            .values(status="succeeded")
        )
        plain = await _ticket(w, queue_id=Q_A, type_name=None, state="open", service_id=None)
        ci = await _ci(w, "srv-app-01.example.org", group_id=G_A, class_key="chg94srv")

        async with _client(w) as client:
            # the change: the new link queues a job and stamps the request
            res = await client.post(f"/api/v1/tickets/{tid}/cis", json={"ci": f"tiqora:{ci}"})
            assert res.status_code == 201, res.text
            assert len(await _jobs(w, tid)) == 2
            # a plain ticket: nothing
            res = await client.post(f"/api/v1/tickets/{plain}/cis", json={"ci": f"tiqora:{ci}"})
            assert res.status_code == 201, res.text
            assert await _jobs(w, plain) == []

            # taking the link away asks for a new set as well
            await w.session.execute(
                update(TiqoraJob)
                .where(TiqoraJob.dedupe_key == f"change:{tid}")
                .values(status="succeeded")
            )
            res = await client.delete(f"/api/v1/tickets/{tid}/cis/tiqora/{ci}")
            assert res.status_code == 204, res.text
            assert len(await _jobs(w, tid)) == 3


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_final_phase_not_recomputed(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with _world(request, url_fixture) as w:
        closed, cancelled, rejected = T_PLAIN, T_PLAIN + 1, T_PLAIN + 2
        await make_change(w.session, closed, phase="closed")
        await make_change(w.session, cancelled, phase="cancelled")
        await make_change(w.session, rejected, phase="rejected")  # not final: reopenable (F11)

        assert await enqueue_impact(w.session, ticket_id=closed, user_id=U_FULL) is None
        assert await enqueue_impact(w.session, ticket_id=cancelled, user_id=U_FULL) is None
        assert await enqueue_impact(w.session, ticket_id=999999, user_id=U_FULL) is None
        assert await enqueue_impact(w.session, ticket_id=rejected, user_id=U_FULL) is not None
        assert await _jobs(w, closed) == [] and (await _row(w, closed)).impact_requested_at is None

        # a job that was queued before the change closed does nothing
        out = await run_change_impact_job(
            _ctx(w), ChangeImpactPayload(ticket_id=closed, user_id=U_FULL)
        )
        assert out == {"passes": 0, "skipped": True}
        assert await _rows(w, closed) == set()
        assert (await _row(w, closed)).impact_computed_at is None


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_refresh_stale_impacts_limit(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _world(request, url_fixture) as w:
        now = utc_now().replace(microsecond=0)  # MariaDB DATETIME drops fractions
        old = now - timedelta(hours=30)
        older = now - timedelta(hours=90)
        a, b, c, d, e, f, g = (T_PLAIN + i for i in range(7))
        await make_change(w.session, a, phase="draft", impact_computed_at=old)
        await make_change(w.session, b, phase="approval", impact_computed_at=older)
        await make_change(w.session, c, phase="rejected", impact_computed_at=None)
        await make_change(w.session, d, phase="closed", impact_computed_at=older)  # final
        await make_change(w.session, e, phase="cancelled", impact_computed_at=None)  # final
        await make_change(w.session, f, phase="scheduled", impact_computed_at=now)  # fresh
        await make_change(w.session, g, phase="implementing", impact_computed_at=old)
        # computed recently, but a request is newer than the computation (a missed trigger)
        h = T_PLAIN + 7
        await make_change(
            w.session,
            h,
            phase="scheduled",
            impact_computed_at=now - timedelta(minutes=5),
            impact_requested_at=now - timedelta(minutes=1),
        )
        cfg = ItsmChangeSettings()  # impact_refresh_hours = 24

        monkeypatch.setattr(impact_module, "REFRESH_LIMIT", 3)
        assert await refresh_stale_impacts(w.session, cfg) == 3
        # never computed and pending requests first, then the oldest
        assert [bool(await _jobs(w, t)) for t in (a, b, c, d, e, f, g, h)] == [
            False,
            True,
            True,
            False,
            False,
            False,
            False,
            True,
        ]
        # the stored set stays valid while it is refreshed: no request stamp
        assert (await _row(w, b)).impact_requested_at is None
        assert (await _row(w, h)).impact_requested_at == now - timedelta(minutes=1)

        monkeypatch.setattr(impact_module, "REFRESH_LIMIT", 200)
        # b and c are joined (their jobs are still queued), a and g are new
        assert await refresh_stale_impacts(w.session, cfg) == 5
        assert [bool(await _jobs(w, t)) for t in (a, b, c, d, e, f, g, h)] == [
            True,
            True,
            True,
            False,
            False,
            False,
            True,
            True,
        ]


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_refresh_tick_respects_switch_and_mode(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _world(request, url_fixture) as w:
        await make_change(w.session, T_PLAIN, phase="draft")
        assert await run_itsm_change_tick_in(w.session, APP_SETTINGS) == {"queued": 1}
        assert len(await _jobs(w, T_PLAIN)) == 1

        await w.session.execute(
            update(TiqoraJob)
            .where(TiqoraJob.dedupe_key == f"change:{T_PLAIN}")
            .values(status="succeeded")
        )
        w.session.add(TiqoraSettings(key=KEY_ITSM_CHANGE_ENABLED, value="false"))
        await w.session.flush()
        assert await run_itsm_change_tick_in(w.session, APP_SETTINGS) == {"skipped": 1}
        assert len(await _jobs(w, T_PLAIN)) == 1  # switched off: nothing queued

    async with change_world(request, url_fixture, mode="znuny_readthrough", setup=False) as w:
        await make_change(w.session, T_PLAIN, phase="draft")
        assert await run_itsm_change_tick_in(w.session, APP_SETTINGS) == {"skipped": 1}
        assert await _jobs(w, T_PLAIN) == []


def test_daemon_service_row() -> None:
    row = DAEMON_SERVICES_BY_SLUG["itsm_change"]
    assert (row.enabled_key, row.default_enabled, row.toggleable) == (
        KEY_ITSM_CHANGE_ENABLED,
        True,
        True,
    )
    assert (row.interval_key, row.interval_settings_attr) == (
        KEY_ITSM_CHANGE_INTERVAL_SECONDS,
        "itsm_change_interval_seconds",
    )
    assert Settings(environment="test").itsm_change_interval_seconds == 3600


# --- filtered read ---------------------------------------------------------------------------


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_impact_omits_unreadable_ci(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with _world(request, url_fixture) as w:
        tid = await _change(w, service_id=SERVICE)
        shown = await _ci(w, "srv-app-01.example.org", group_id=G_A, class_key="chg94srv")
        secret = await _ci(w, "srv-vault-01.example.org", group_id=G_B, class_key="chg94vault")
        await _link(w, ("ci", shown), ("ticket", tid), "RelevantTo")
        await _link(w, ("ci", secret), ("ticket", tid), "RelevantTo")
        await _link(w, ("ci", shown), ("ci", secret), "DependsOn")
        # a service that only the hidden CI leads to
        await _link(w, ("service", SERVICE_IMPACTED), ("ci", secret), "DependsOn")
        # a readable incident on that hidden service must not name the service either
        on_hidden = await _ticket(
            w, queue_id=Q_A, type_name="Incident", state="open", service_id=SERVICE_IMPACTED
        )
        await run_change_impact_job(_ctx(w), ChangeImpactPayload(ticket_id=tid, user_id=U_FULL))
        assert len(await _rows(w, tid)) == 4  # both CIs and both services are stored

        async with _client(w) as client:
            res = await client.get(f"/api/v1/itsm/changes/{tid}/impact")
            assert res.status_code == 200, res.text
            body = res.json()
            assert [c["ci_id"] for c in body["cis"]] == [shown]
            assert body["cis"][0]["direct"] is True and body["cis"][0]["name"] == (
                "srv-app-01.example.org"
            )
            assert body["hidden_ci_count"] == 1  # a number, no name, no id
            assert "srv-vault-01" not in res.text and str(secret) not in {
                str(c["ci_id"]) for c in body["cis"]
            }
            assert body["stale"] is False and body["truncated"] is False
            # the service reached only through the hidden CI is left out and counted
            assert [s["service_id"] for s in body["services"]] == [SERVICE]
            assert body["hidden_service_count"] == 1
            assert "chg94-service-impacted" not in res.text
            assert on_hidden not in {i["ticket_id"] for i in body["incidents"]}
            assert str(SERVICE_IMPACTED) not in {str(i["service_id"]) for i in body["incidents"]}

            # a user without ro on the change ticket gets the same 404 as for a missing one
        async with _client(w, U_NONE) as outsider:
            denied = await outsider.get(f"/api/v1/itsm/changes/{tid}/impact")
            assert denied.status_code == 404


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_impact_lists_only_readable_open_incidents(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _world(request, url_fixture) as w:
        tid = await _change(w, service_id=SERVICE, service_ids=[SERVICE_IMPACTED])
        make = _ticket  # (queue, type, state, service)
        open_incident = await make(
            w, queue_id=Q_A, type_name="Incident", state="open", service_id=SERVICE
        )
        major = await make(
            w, queue_id=Q_A, type_name="Incident::Major", state="new", service_id=SERVICE_IMPACTED
        )
        hidden = await make(w, queue_id=Q_B, type_name="Incident", state="open", service_id=SERVICE)
        await make(
            w,
            queue_id=Q_A,
            type_name="Incident",
            state="closed successful",
            service_id=SERVICE,
        )
        await make(w, queue_id=Q_A, type_name="Unclassified", state="open", service_id=SERVICE)
        await make(w, queue_id=Q_A, type_name="Incident", state="open", service_id=SERVICE_B)
        await run_change_impact_job(_ctx(w), ChangeImpactPayload(ticket_id=tid, user_id=U_FULL))

        view = await read_impact(
            w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL, sysconfig=SysConfig(w.session)
        )

        # open incidents of the change's services, newest first; not the unreadable queue, not the
        # closed one, not other ticket types, not another service
        assert [i.ticket_id for i in view.incidents] == [major, open_incident]
        assert view.incidents_truncated is False
        assert {i.queue_id for i in view.incidents} == {Q_A}
        assert {s.service_id: s.direct for s in view.services} == {
            SERVICE: True,
            SERVICE_IMPACTED: True,
        }
        assert f'"ticket_id":{hidden}' not in view.model_dump_json().replace(" ", "")


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_recompute_endpoint_queues_job(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _world(request, url_fixture) as w:
        tid = await _change(w)
        async with _client(w) as client:
            res = await client.post(f"/api/v1/itsm/changes/{tid}/impact/recompute")
            assert res.status_code == 202, res.text
            (job,) = await _jobs(w, tid)
            assert res.json() == {"job_id": job.id, "status": "queued"}

            done = await _ticket(w, queue_id=Q_A, type_name=None, state="open", service_id=None)
            await make_change(w.session, done, phase="closed")
            final = await client.post(f"/api/v1/itsm/changes/{done}/impact/recompute")
            assert (final.status_code, final.json()["detail"]["code"]) == (409, "change_final")

        async with _client(w, U_NONE) as outsider:
            nope = await outsider.post(f"/api/v1/itsm/changes/{tid}/impact/recompute")
            assert nope.status_code == 404
        with pytest.raises(ChangeError):
            await read_impact(
                w.session,
                APP_SETTINGS,
                ticket_id=999999,
                user_id=U_FULL,
                sysconfig=SysConfig(w.session),
            )


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_request_stamped_once_per_transaction(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _world(request, url_fixture) as w:
        refs = []
        for i in range(8):
            ci = await _ci(w, f"srv-app-{i:02d}.example.org", group_id=G_A, class_key="chg94srv")
            refs.append(f"tiqora:{ci}")
        tid = await _change(w, ci_refs=refs)  # eight links + the explicit request
        stamp = (await _row(w, tid)).impact_requested_at
        assert stamp is not None
        # one stamp, not nine seconds ahead of the clock
        assert stamp <= utc_now().replace(microsecond=0) + timedelta(seconds=1)
        assert len(await _jobs(w, tid)) == 1


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_edit_before_the_lock_is_in_the_set(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Committed sessions, not the test's one: a plain read before ``recompute_impact`` would fix
    the MariaDB snapshot, the lock would return the edit's stamp and the set would count as current
    without the edit's service. The phase comes from the locked row as well."""
    url = plain_url(request, url_fixture)
    marks = snapshot_marks(url, ("ticket_history", "ticket"))
    engine, factory = make_factory(url)
    tn = f"{TICKET_NO_BASE}{time.time_ns()}"
    tid = 0
    try:
        async with factory() as s:
            await s.execute(
                text(
                    "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                    " change_by) VALUES (:id, 'chg94-service-impacted', 1, :t, 1, :t, 1)"
                ),
                {"id": SERVICE_IMPACTED, "t": _T},
            )
            await s.execute(
                text(
                    "INSERT INTO ticket (tn, title, queue_id, ticket_lock_id, type_id, user_id,"
                    " responsible_user_id, ticket_priority_id, ticket_state_id, timeout,"
                    " until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by) VALUES (:tn, 'chg94 race',"
                    " 1, 1, 1, 1, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"tn": tn, "t": _T},
            )
            tid = int(
                (
                    await s.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})
                ).scalar_one()
            )
            await make_change(s, tid)
            await s.commit()

        real = impact_module.recompute_impact
        edit: list[Any] = []

        async def edit_first(session: Any, *, ticket_id: int, user_id: int | None = None) -> Any:
            if edit:
                async with factory() as other:
                    await edit.pop()(other)
                    await other.commit()
            return await real(session, ticket_id=ticket_id, user_id=user_id)

        async def add_service(other: Any) -> None:
            other.add(TiqoraItsmChangeService(ticket_id=tid, service_id=SERVICE_IMPACTED))
            row = await other.get(TiqoraItsmChange, tid)
            row.impact_requested_at = impact_module._next_stamp(row.impact_requested_at)

        async def cancel(other: Any) -> None:
            (await other.get(TiqoraItsmChange, tid)).phase = "cancelled"

        monkeypatch.setattr(impact_module, "recompute_impact", edit_first)
        context = JobContext(
            job_id=946601,
            kind=ITSM_CHANGE_IMPACT_KIND,
            created_by=U_FULL,
            settings=Settings(environment="test"),
            session_factory=factory,
            attempt=1,
            max_attempts=3,
        )
        payload = ChangeImpactPayload(ticket_id=tid, user_id=None)

        edit.append(add_service)
        result = await run_change_impact_job(context, payload)
        assert result["services"] == 1
        async with factory() as s:
            row = await s.get(TiqoraItsmChange, tid)
            assert row is not None and impact_is_current(row)
            stored = await s.execute(
                select(TiqoraItsmChangeImpact.ref_key).where(
                    TiqoraItsmChangeImpact.ticket_id == tid
                )
            )
            assert {str(k) for (k,) in stored} == {str(SERVICE_IMPACTED)}

        edit.append(cancel)
        assert (await run_change_impact_job(context, payload)).get("skipped") is True
    finally:
        await engine.dispose()
        sync = create_engine(url)
        with sync.begin() as conn:
            for table in (
                "tiqora_itsm_change_event",
                "tiqora_itsm_change_impact",
                "tiqora_itsm_change_service",
            ):
                conn.execute(text(f"DELETE FROM {table} WHERE ticket_id = :t"), {"t": tid})  # noqa: S608
            conn.execute(text("DELETE FROM tiqora_itsm_change WHERE ticket_id = :t"), {"t": tid})
            conn.execute(text("DELETE FROM service WHERE id = :i"), {"i": SERVICE_IMPACTED})
        sync.dispose()
        delete_above_marks(url, marks)
