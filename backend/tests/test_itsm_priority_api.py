"""Priority matrix routes and the PATCH hook (Task 16), both dialects.

Everything is flushed in one rolled-back session (the routes' ``commit`` becomes ``flush`` and
``session.begin()`` a savepoint), so the strict leak check finds nothing. Seed band
941100-941199; the access world (users, queues, tickets) is the one of ``test_itsm_ci_api``:
``FULL`` has ro+rw on queue A where ``T_A`` (priority 3 "normal") lives.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._itsm_adapter_helpers import itsm_session
from tests._itsm_mode_helpers import itsm_url, profile_packages, seed_packages, store
from tests.test_itsm_ci_api import FULL, GROUP_A, T_A, _grant, _seed_access, _user
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.mode import SETTING_MODE
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache

pytestmark = pytest.mark.db

ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
DIALECTS = ["mysql", "postgresql"]
CRIT, IMPACT = "ITSMCriticality", "ITSMImpact"
TICKET = f"/api/v1/tickets/{T_A}"


@dataclass
class World:
    client: AsyncClient
    session: AsyncSession
    url: str


@asynccontextmanager
async def world_for(
    request: pytest.FixtureRequest, dialect: str, *, mode: str, matrix_enabled: bool = True
) -> AsyncIterator[World]:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    store(url, SETTING_MODE, mode)
    if not matrix_enabled:
        store(url, "itsm.priority_matrix_enabled", "0")
    try:
        async with itsm_session(request, dialect, "itsm-6") as (session, _reader, _engine):
            await _seed_access(session)
            await _grant(session, FULL, GROUP_A, "priority")

            async def _db() -> Any:
                yield session

            session.commit = session.flush  # type: ignore[method-assign]
            session.begin = session.begin_nested  # type: ignore[method-assign,assignment]
            app = create_app(Settings(environment="test"))
            app.dependency_overrides[get_db] = _db
            app.dependency_overrides[get_current_user] = lambda: _user(FULL)
            app.dependency_overrides[get_admin_user] = lambda: ADMIN
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                yield World(c, session, url)
    finally:
        clear_detect_cache()


@pytest.fixture(params=DIALECTS)
def dialect(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
async def mode_a(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[World]:
    async with world_for(request, dialect, mode="znuny_readthrough") as w:
        yield w


@pytest.fixture
async def mode_b(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[World]:
    async with world_for(request, dialect, mode="tiqora") as w:
        yield w


async def _priority_of(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text("SELECT ticket_priority_id FROM ticket WHERE id = :t"), {"t": T_A}
            )
        ).scalar_one()
    )


async def _priority_updates(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM ticket_history h JOIN ticket_history_type ht"
                    " ON ht.id = h.history_type_id WHERE h.ticket_id = :t"
                    " AND ht.name = 'PriorityUpdate'"
                ),
                {"t": T_A},
            )
        ).scalar_one()
    )


async def _put_matrix(w: World, cells: list[tuple[str, str, int]]) -> None:
    body = {"cells": [{"criticality": c, "impact": i, "priority_id": p} for c, i, p in cells]}
    got = await w.client.put("/api/v1/admin/itsm/priority-matrix", json=body)
    assert got.status_code == 200, got.text


# --- Mode A: Znuny's cip_allocate, read-only  ----------


async def test_mode_a_reads_cip_allocate_read_only(mode_a: World) -> None:
    c = mode_a.client
    got = await c.get("/api/v1/admin/itsm/priority-matrix")
    assert got.status_code == 200
    body = got.json()
    assert body["source"] == "znuny" and body["editable"] is False and body["enabled"] is True
    assert len(body["cells"]) == 25
    assert body["criticality_values"][0] == "1 very low"
    refused = await c.put(
        "/api/v1/admin/itsm/priority-matrix",
        json={"cells": [{"criticality": "3 normal", "impact": "3 normal", "priority_id": 3}]},
    )
    assert refused.status_code == 409 and refused.json()["detail"] == {"code": "read_only"}
    again = (await c.get("/api/v1/admin/itsm/priority-matrix")).json()
    assert again["cells"] == body["cells"]  # nothing written
    agent = (await c.get("/api/v1/itsm/priority-matrix")).json()
    assert agent["source"] == "znuny" and len(agent["cells"]) == 25


async def test_patch_impact_sets_priority_from_cip_allocate(mode_a: World) -> None:
    """Mode A: criticality "3 normal" x impact "4 high" is priority 4 in Znuny's matrix."""
    w = mode_a
    assert await _priority_of(w.session) == 3
    got = await w.client.patch(
        TICKET, json={"dynamic_fields": {CRIT: ["3 normal"], IMPACT: ["4 high"]}}
    )
    assert got.status_code == 204, got.text
    assert await _priority_of(w.session) == 4
    assert await _priority_updates(w.session) == 1  # written by change_priority


# --- Tiqora's own matrix ----------


async def test_tiqora_matrix_save_and_load(mode_b: World) -> None:
    c = mode_b.client
    empty = (await c.get("/api/v1/admin/itsm/priority-matrix")).json()
    assert empty["source"] == "tiqora" and empty["editable"] is True and empty["cells"] == []
    assert empty["criticality_values"][:5] == [
        "1 very low",
        "2 low",
        "3 normal",
        "4 high",
        "5 very high",
    ]
    await _put_matrix(mode_b, [("3 normal", "4 high", 5), ("1 very low", "1 very low", 1)])
    agent = (await c.get("/api/v1/itsm/priority-matrix")).json()
    assert agent["source"] == "tiqora"
    assert sorted((x["criticality"], x["impact"], x["priority_id"]) for x in agent["cells"]) == [
        ("1 very low", "1 very low", 1),
        ("3 normal", "4 high", 5),
    ]
    await _put_matrix(mode_b, [("2 low", "2 low", 2)])  # replaces
    assert len((await c.get("/api/v1/admin/itsm/priority-matrix")).json()["cells"]) == 1


async def test_tiqora_matrix_rejects_bad_cells(mode_b: World) -> None:
    c = mode_b.client
    url = "/api/v1/admin/itsm/priority-matrix"
    dup = {"cells": [{"criticality": "a", "impact": "b", "priority_id": 1}] * 2}
    r = await c.put(url, json=dup)
    assert r.status_code == 422 and r.json()["detail"] == {"code": "duplicate_cell"}
    unknown = {"cells": [{"criticality": "a", "impact": "b", "priority_id": 941101}]}
    r = await c.put(url, json=unknown)
    assert r.status_code == 422 and r.json()["detail"] == {"code": "unknown_priority"}
    assert (
        await c.put(url, json={"cells": [{"impact": "b", "priority_id": 1}]})
    ).status_code == 422


async def test_patch_impact_sets_priority(mode_b: World) -> None:
    w = mode_b
    await _put_matrix(w, [("3 normal", "4 high", 5)])
    assert await _priority_of(w.session) == 3
    got = await w.client.patch(
        TICKET, json={"dynamic_fields": {CRIT: ["3 normal"], IMPACT: ["4 high"]}}
    )
    assert got.status_code == 204, got.text
    assert await _priority_of(w.session) == 5
    assert await _priority_updates(w.session) == 1  # PriorityUpdate history by change_priority
    # the same values again are no change: no second history row, priority stays
    again = await w.client.patch(TICKET, json={"dynamic_fields": {IMPACT: ["4 high"]}})
    assert again.status_code == 204
    assert await _priority_updates(w.session) == 1


async def test_patch_with_explicit_priority_wins(mode_b: World) -> None:
    w = mode_b
    await _put_matrix(w, [("3 normal", "4 high", 5)])
    got = await w.client.patch(
        TICKET,
        json={"priority_id": 2, "dynamic_fields": {CRIT: ["3 normal"], IMPACT: ["4 high"]}},
    )
    assert got.status_code == 204, got.text
    assert await _priority_of(w.session) == 2  # the agent's choice, not the matrix' 5
    assert await _priority_updates(w.session) == 1  # only the explicit change


async def test_patch_unrelated_df_no_recompute(mode_b: World) -> None:
    w = mode_b
    await _put_matrix(w, [("3 normal", "4 high", 5)])
    first = await w.client.patch(
        TICKET, json={"dynamic_fields": {CRIT: ["3 normal"], IMPACT: ["4 high"]}}
    )
    assert first.status_code == 204
    assert await _priority_of(w.session) == 5
    # the agent lowers the priority by hand; a write that touches neither field leaves it
    assert (await w.client.patch(TICKET, json={"priority_id": 2})).status_code == 204
    other = await w.client.patch(
        TICKET,
        json={"field_name": "ITSMReviewRequired", "field_values": ["Yes"]},
    )
    assert other.status_code == 204, other.text
    assert await _priority_of(w.session) == 2
    # and a field-name/values write of an ITSM field does recompute
    third = await w.client.patch(TICKET, json={"field_name": IMPACT, "field_values": ["3 normal"]})
    assert third.status_code == 204
    assert await _priority_of(w.session) == 2  # ("3 normal","3 normal") has no Tiqora cell


async def test_patch_unknown_pair_keeps_priority(mode_b: World) -> None:
    w = mode_b
    await _put_matrix(w, [("1 very low", "1 very low", 1)])
    got = await w.client.patch(
        TICKET, json={"dynamic_fields": {CRIT: ["5 very high"], IMPACT: ["5 very high"]}}
    )
    assert got.status_code == 204
    assert await _priority_of(w.session) == 3 and await _priority_updates(w.session) == 0


async def test_matrix_disabled_or_off_no_change(
    request: pytest.FixtureRequest, dialect: str
) -> None:
    fields = {"dynamic_fields": {CRIT: ["3 normal"], IMPACT: ["4 high"]}}
    async with world_for(request, dialect, mode="tiqora", matrix_enabled=False) as w:
        await _put_matrix(w, [("3 normal", "4 high", 5)])
        assert (await w.client.patch(TICKET, json=fields)).status_code == 204
        assert await _priority_of(w.session) == 3
        sug = await w.client.get(
            "/api/v1/itsm/priority-suggestion",
            params={"criticality": "3 normal", "impact": "4 high"},
        )
        assert sug.json() == {"priority_id": None}
        assert (await w.client.get("/api/v1/itsm/priority-matrix")).json()["enabled"] is False
    async with world_for(request, dialect, mode="off") as w:
        assert (await w.client.patch(TICKET, json=fields)).status_code == 204
        assert await _priority_of(w.session) == 3  # ITSM off: a plain DF write
        assert (await w.client.get("/api/v1/itsm/priority-matrix")).status_code == 409


async def test_suggestion_endpoint(mode_b: World) -> None:
    w = mode_b
    await _put_matrix(w, [("3 normal", "4 high", 5)])
    url = "/api/v1/itsm/priority-suggestion"
    hit = await w.client.get(url, params={"criticality": "3 normal", "impact": "4 high"})
    assert hit.status_code == 200 and hit.json() == {"priority_id": 5}
    miss = await w.client.get(url, params={"criticality": "2 low", "impact": "4 high"})
    assert miss.json() == {"priority_id": None}
    assert (await w.client.get(url, params={"criticality": "3 normal"})).json() == {
        "priority_id": None
    }
    assert (await w.client.get(url)).json() == {"priority_id": None}


def test_itsm_routes_are_api_key_area_cmdb() -> None:
    assert path_to_area("/api/v1/itsm/priority-matrix") == "cmdb"
    assert path_to_area("/api/v1/itsm/priority-suggestion") == "cmdb"
