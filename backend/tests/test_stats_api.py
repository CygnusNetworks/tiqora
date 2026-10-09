"""DB integration tests for ``/api/v1/stats/*`` endpoints (both dialects).

Reuses the seed of ``test_stats_service.py`` (ids 47870-47899, see its docstring): an allowed
queue with five tickets, a denied queue with one that must never leak through the REST layer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.test_stats_service import cleanup_all, reset_state, seed, to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.reporting.engine import QueryBudget
from tiqora.reporting.jobs import TIMEOUT_MESSAGE
from tiqora.reporting.settings import REPORTING_SECTION
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

#: Znuny seed admin (group "admin" rw)
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Admin", last_name="OTRS", auth_method="session"
)
ROUTES = ("volume", "backlog", "open-snapshot", "sla", "agent-workload")


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    cleanup_all()


@dataclass
class Api:
    client: AsyncClient
    ids: dict[str, Any]
    factory: async_sessionmaker[AsyncSession]
    holder: list[AuthenticatedUser]

    def act_as(self, user: AuthenticatedUser) -> None:
        self.holder[0] = user

    async def set_access(self, access: str) -> None:
        async with self.factory() as session:
            await save_section_values(
                session,
                REPORTING_SECTION,
                {"access": access},
                secret_key=Settings(environment="test").secret_key,
            )
            await session.commit()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def api(request: pytest.FixtureRequest) -> AsyncIterator[Api]:
    sync_url: str = request.getfixturevalue(request.param)
    ids = await seed(sync_url)
    engine = create_async_engine(to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    reader = AuthenticatedUser(
        id=ids["user_id"], login=ids["login"], first_name="Read", last_name="Er",
        auth_method="session",
    )  # fmt: skip
    holder = [reader]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: holder[0]
    app.dependency_overrides[get_db] = _db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield Api(client, ids, factory, holder)
    finally:
        await engine.dispose()
        reset_state(sync_url)


async def test_stats_volume_json(api: Api) -> None:
    resp = await api.client.get("/api/v1/stats/volume", params={"queue_id": api.ids["queue_id"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["granularity"] == "day"
    assert sum(p["created"] for p in body["points"]) == 5
    # one close per transition into a closed state: T2, T5 (not again for its note), T6
    assert sum(p["closed"] for p in body["points"]) == 3


async def test_stats_volume_bare_date_bounds_cover_the_whole_last_day(api: Api) -> None:
    resp = await api.client.get(
        "/api/v1/stats/volume",
        params={"date_from": "2024-06-04", "date_to": "2024-06-04", "tz": "Europe/Berlin"},
    )

    assert resp.status_code == 200
    # T6 was created 2024-06-04 12:00 UTC; nothing else is created that day
    assert [(p["bucket"], p["created"]) for p in resp.json()["points"]] == [("2024-06-04", 1)]


async def test_stats_open_snapshot_permission_filtered(api: Api) -> None:
    resp = await api.client.get("/api/v1/stats/open-snapshot", params={"dimension": "queue"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["dimension"] == "queue"
    queue_ids = [item["id"] for item in body["items"]]
    assert api.ids["queue_id"] in queue_ids
    assert api.ids["denied_queue_id"] not in queue_ids


async def test_stats_sla_json(api: Api) -> None:
    resp = await api.client.get("/api/v1/stats/sla")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert body["escalated"] == 1
    assert body["first_response_breached"] == 1  # T6 breached, then closed


async def test_stats_agent_workload_csv(api: Api) -> None:
    resp = await api.client.get("/api/v1/stats/agent-workload.csv")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    raw = resp.content
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = raw[3:].decode("utf-8").splitlines()
    assert lines[0].split(";") == ["Login", "Name", "OwnedOpen", "ClosedInPeriod"]
    assert [line.split(";") for line in lines[1:]] == [[api.ids["login"], "Read Er", "1", "3"]]


async def test_stats_backlog_csv(api: Api) -> None:
    resp = await api.client.get("/api/v1/stats/backlog.csv")

    assert resp.status_code == 200
    raw = resp.content
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = raw[3:].decode("utf-8").splitlines()
    assert lines[0].split(";") == ["Bucket", "OpenCount"]
    # 5 created - 3 closed; the last bucket with data is T2's close on 2024-06-09
    assert lines[-1].split(";") == ["2024-06-09", "2"]


async def test_wrappers_respect_stats_group_setting(api: Api) -> None:
    await api.set_access("stats_group")

    # the reader has no "stats" group: every route and its CSV sibling answer 403
    for route in ROUTES:
        for suffix in ("", ".csv"):
            resp = await api.client.get(f"/api/v1/stats/{route}{suffix}")
            assert resp.status_code == 403, (route, suffix, resp.text)
            assert "message" in resp.json()["detail"]

    # an admin still passes
    api.act_as(ADMIN)
    resp = await api.client.get("/api/v1/stats/volume")
    assert resp.status_code == 200, resp.text


async def test_every_wrapper_accepts_every_filter(api: Api) -> None:
    params = {
        "date_from": "2024-06-01",
        "date_to": "2024-06-30",
        "queue_id": api.ids["queue_id"],
        "state_id": api.ids["state_open"],
        "priority_id": 3,
        "type_id": 1,
        "customer_id": "rep47-cust-stats",
        "tz": "Europe/Berlin",
    }
    for route in ROUTES:
        for suffix in ("", ".csv"):
            resp = await api.client.get(f"/api/v1/stats/{route}{suffix}", params=params)
            assert resp.status_code == 200, (route, suffix, resp.text)

    # the filters really apply: open tickets of that customer/priority/type in the queue
    resp = await api.client.get("/api/v1/stats/open-snapshot", params=params)
    assert [i["count"] for i in resp.json()["items"]] == [2]
    resp = await api.client.get("/api/v1/stats/open-snapshot", params={**params, "state_id": 2})
    assert resp.json()["items"] == []


async def test_truncated_results_set_the_header(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    import tiqora.reporting.engine as engine
    import tiqora.stats.service as service

    async def get_all() -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for route in ROUTES:
            for suffix in ("", ".csv"):
                # two owners have open tickets, so an owner snapshot has more than one row
                params = {"dimension": "owner"} if route == "open-snapshot" else {}
                resp = await api.client.get(f"/api/v1/stats/{route}{suffix}", params=params)
                assert resp.status_code == 200, (route, suffix)
                out[route + suffix] = resp.headers.get("x-tiqora-truncated")
        return out

    assert set((await get_all()).values()) == {None}, "normal case: no header"

    monkeypatch.setattr(service, "STATS_ROW_CAP", 1)
    monkeypatch.setattr(engine, "PERCENTILE_SAMPLE_CAP", 1)
    flagged = await get_all()
    assert set(flagged.values()) == {"1"}, flagged
    # the body keeps its shape
    body = (await api.client.get("/api/v1/stats/volume")).json()
    assert set(body) == {"granularity", "points"}


async def test_used_up_inline_budget_is_504_with_json_message(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    def spent(cls: type[QueryBudget], reporting: Any, seconds: float = 75, **_: Any) -> QueryBudget:
        return cls(reporting.query_timeout_seconds, deadline=-1.0, clock=lambda: 0.0)

    monkeypatch.setattr(QueryBudget, "inline", classmethod(spent))
    for route in ROUTES:
        resp = await api.client.get(f"/api/v1/stats/{route}")
        assert resp.status_code == 504, (route, resp.text)
        assert resp.json()["detail"] == {"message": TIMEOUT_MESSAGE}


async def test_budget_is_one_per_request_not_per_query(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[QueryBudget] = []
    real = QueryBudget.inline.__func__  # type: ignore[attr-defined]

    def counting(cls: type[QueryBudget], *args: Any, **kwargs: Any) -> QueryBudget:
        budget: QueryBudget = real(cls, *args, **kwargs)
        created.append(budget)
        return budget

    monkeypatch.setattr(QueryBudget, "inline", classmethod(counting))
    # sla runs four queries, workload two: still one budget per request
    for route in ("sla", "agent-workload"):
        created.clear()
        resp = await api.client.get(f"/api/v1/stats/{route}")
        assert resp.status_code == 200
        assert len(created) == 1, route


@pytest.mark.asyncio
async def test_stats_volume_requires_auth() -> None:
    app = create_app(Settings(environment="test"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/stats/volume")
    assert resp.status_code == 401
