"""Customer portal CMDB view and CI picker on portal tickets (band 745000-745499, ``cmdb4``)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import AsyncSession

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    CUSTOMER_A,
    CUSTOMER_B,
    SERVER_ATTRS,
    CmdbWorld,
    make_ci,
    make_class,
    portal_client_for,
    set_mode,
)
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.portal import PortalCmdbService
from tiqora.cmdb.service import system_access
from tiqora.domain.customer_auth import AuthenticatedCustomer

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


def _purge_portal_tickets(sync_url: str) -> None:
    """Delete the tickets the portal tests created (title ``cmdb4 St...``) and their rows.

    Children are found through the foreign keys that point at ``ticket``/``article*``; each
    delete runs in its own transaction, repeated until the parents are gone.
    """
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            ids = [
                int(r[0])
                for r in conn.execute(text("SELECT id FROM ticket WHERE title LIKE 'cmdb4 St%'"))
            ]
            insp = inspect(conn)
            edges: list[tuple[str, str, str, str]] = []
            for table in insp.get_table_names():
                for fk in insp.get_foreign_keys(table):
                    ref = fk["referred_table"]
                    if ref == "ticket" or ref.startswith("article"):
                        edges.append(
                            (table, fk["constrained_columns"][0], ref, fk["referred_columns"][0])
                        )
            conn.rollback()
        if not ids:
            return
        marks = ", ".join(str(i) for i in ids)
        scope = {"ticket": f"SELECT id FROM ticket WHERE id IN ({marks})"}
        scope["article"] = f"SELECT id FROM article WHERE ticket_id IN ({marks})"
        scope["article_data_mime"] = scope["article"]
        for _ in range(4):
            for table, col, ref, _ref_col in edges:
                inner = scope.get(ref)
                if inner is None:
                    continue
                with engine.connect() as conn:
                    try:
                        conn.execute(
                            text(f"DELETE FROM {table} WHERE {col} IN ({inner})")  # noqa: S608
                        )
                        conn.commit()
                    except DatabaseError:
                        conn.rollback()
        with engine.begin() as conn:
            for table in ("tiqora_event_outbox", "tiqora_cache_invalidation"):
                conn.execute(text(f"DELETE FROM {table} WHERE ticket_id IN ({marks})"))  # noqa: S608
            conn.execute(text(f"DELETE FROM ticket WHERE id IN ({marks})"))  # noqa: S608
    finally:
        engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _portal_ticket_cleanup(cmdb_db: CmdbWorld) -> Iterator[None]:
    """Tickets the portal tests create get ids outside the band; delete them afterwards."""
    counter = int(cmdb_db.sql("SELECT COALESCE(MAX(id), 0) FROM ticket_number_counter")[0][0])
    yield
    _purge_portal_tickets(cmdb_db.sync_url)
    cmdb_db.sql("DELETE FROM ticket_number_counter WHERE id > :m", m=counter)


async def _link_kind(
    s: AsyncSession, ci: tuple[str, str], other: tuple[str, str], link_type: str
) -> None:
    await LinkService(s).add(
        system_access(740102),
        source=LinkEnd(ci[0], ci[1]),  # type: ignore[arg-type]
        target=LinkEnd(other[0], other[1]),  # type: ignore[arg-type]
        link_type_key=link_type,
        enforce_permissions=False,
    )


async def _world(world: CmdbWorld, *, portal_visible: bool = True) -> dict[str, int]:
    async with world.factory() as s:
        cid = await make_class(s, portal_visible=portal_visible)
        ids = {
            n: await make_ci(s, cid, f"cmdb4-{n}") for n in ("mine", "company", "mapped", "other")
        }
        await _link_kind(s, ("ci", str(ids["mine"])), ("customer_user", CUSTOMER_A), "RelevantTo")
        await _link_kind(
            s, ("ci", str(ids["company"])), ("customer_company", "cmdb4-co-a"), "RelevantTo"
        )
        await _link_kind(
            s, ("ci", str(ids["mapped"])), ("customer_company", "cmdb4-co-c"), "RelevantTo"
        )
        await _link_kind(
            s, ("ci", str(ids["other"])), ("customer_company", "cmdb4-co-b"), "RelevantTo"
        )
        await s.commit()
    return ids


def _ticket_count(world: CmdbWorld) -> int:
    return int(world.sql("SELECT COUNT(*) FROM ticket WHERE title LIKE 'cmdb4 St%'")[0][0])


async def test_customer_sees_own_and_company_cis_only(cmdb_db: CmdbWorld) -> None:
    await _world(cmdb_db)
    async with portal_client_for(cmdb_db, CUSTOMER_A) as c:
        r = await c.get("/api/portal/config-items")
    assert r.status_code == 200
    body = r.json()
    names = sorted(x["name"] for x in body["items"])
    assert names == ["cmdb4-company", "cmdb4-mapped", "cmdb4-mine"]
    assert body["total"] == 3
    first = body["items"][0]
    assert set(first) == {
        "id",
        "number",
        "name",
        "class_name",
        "deployment_state",
        "incident_state",
        "incident_functionality",
    }
    assert first["incident_state"] == "Operational"


async def test_list_filter_and_paging(cmdb_db: CmdbWorld) -> None:
    await _world(cmdb_db)
    async with portal_client_for(cmdb_db, CUSTOMER_A) as c:
        found = (await c.get("/api/portal/config-items", params={"q": "COMPAN"})).json()
        page = (await c.get("/api/portal/config-items", params={"limit": 1, "offset": 1})).json()
    assert [x["name"] for x in found["items"]] == ["cmdb4-company"]
    assert found["total"] == 1
    assert [x["name"] for x in page["items"]] == ["cmdb4-mapped"]
    assert page["total"] == 3


async def test_other_company_ci_is_404(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    async with portal_client_for(cmdb_db, CUSTOMER_A) as c:
        foreign = await c.get(f"/api/portal/config-items/{ids['other']}")
        unknown = await c.get("/api/portal/config-items/987654321")
        own = await c.get(f"/api/portal/config-items/{ids['mine']}")
    assert foreign.status_code == 404
    assert unknown.status_code == 404
    assert foreign.json() == unknown.json()
    assert own.status_code == 200
    assert own.json()["name"] == "cmdb4-mine"


async def test_non_portal_class_hidden(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db, portal_visible=False)
    async with portal_client_for(cmdb_db, CUSTOMER_A) as c:
        listed = (await c.get("/api/portal/config-items")).json()
        one = await c.get(f"/api/portal/config-items/{ids['mine']}")
    assert listed == {"items": [], "total": 0}
    assert one.status_code == 404


async def test_visible_ci_ids_scope(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    customer = AuthenticatedCustomer(
        id=1,
        login=CUSTOMER_A,
        email=CUSTOMER_A,
        customer_id="cmdb4-co-a",
        first_name="C",
        last_name="C",
    )
    async with cmdb_db.factory() as s:
        svc = PortalCmdbService(s)
        login, companies = await svc.customer_scope(customer)
        everything = await svc.visible_ci_ids(customer)
        narrowed = await svc.visible_ci_ids(customer, ci_ids=[ids["mine"], ids["other"]])
    assert login == CUSTOMER_A
    assert companies == {"cmdb4-co-a", "cmdb4-co-c"}
    assert everything == {ids["mine"], ids["company"], ids["mapped"]}
    assert narrowed == {ids["mine"]}


async def test_only_portal_attributes_returned(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s, portal_visible=True)
        loc = await make_ci(s, cid, "cmdb4-location")
        ci = await make_ci(
            s,
            cid,
            "cmdb4-device",
            attributes={
                "Owner": [{"v": CUSTOMER_A}],
                "Vendor": [{"v": "ACME"}],
                "Location": [{"v": str(loc)}],
            },
        )
        await _link_kind(s, ("ci", str(ci)), ("customer_user", CUSTOMER_A), "RelevantTo")
        await s.commit()
    assert any(a["key"] == "Location" for a in SERVER_ATTRS)
    async with portal_client_for(cmdb_db, CUSTOMER_A) as c:
        r = await c.get(f"/api/portal/config-items/{ci}")
    assert r.status_code == 200
    rendered: list[dict[str, Any]] = r.json()["rendered"]
    assert [x["key"] for x in rendered] == ["Owner"]
    assert rendered[0]["ref"] is None
    assert rendered[0]["display"]


async def test_ticket_create_rejects_foreign_ci_and_creates_nothing(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    before = _ticket_count(cmdb_db)
    async with portal_client_for(cmdb_db, CUSTOMER_A) as c:
        r = await c.post(
            "/api/portal/tickets",
            json={
                "title": "cmdb4 Störung",
                "body": "Geht nicht.",
                "config_item_ids": [ids["other"]],
            },
        )
    assert r.status_code == 422
    assert _ticket_count(cmdb_db) == before
    assert (
        cmdb_db.sql("SELECT COUNT(*) FROM tiqora_itsm_link WHERE target_kind = 'ticket'")[0][0] == 0
    )


async def test_ticket_create_links_own_ci(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    before = _ticket_count(cmdb_db)
    async with portal_client_for(cmdb_db, CUSTOMER_A) as c:
        r = await c.post(
            "/api/portal/tickets",
            json={
                "title": "cmdb4 Störung",
                "body": "Geht nicht.",
                "config_item_ids": [ids["mine"], ids["company"], ids["mine"]],
            },
        )
    assert r.status_code == 201
    tid = r.json()["ticket_id"]
    assert _ticket_count(cmdb_db) == before + 1
    rows = cmdb_db.sql(
        "SELECT source_key, link_type FROM tiqora_itsm_link"
        " WHERE target_kind = 'ticket' AND target_key = :t AND source_kind = 'ci'",
        t=str(tid),
    )
    assert sorted((int(k), t) for k, t in rows) == sorted(
        [(ids["mine"], "RelevantTo"), (ids["company"], "RelevantTo")]
    )


async def test_ticket_create_without_cis_still_works(cmdb_db: CmdbWorld) -> None:
    await _world(cmdb_db)
    async with portal_client_for(cmdb_db, CUSTOMER_B) as c:
        r = await c.post(
            "/api/portal/tickets", json={"title": "cmdb4 Störung", "body": "Ohne Gerät."}
        )
    assert r.status_code == 201
    assert (
        cmdb_db.sql(
            "SELECT COUNT(*) FROM tiqora_itsm_link WHERE target_kind = 'ticket'"
            " AND target_key = :t",
            t=str(r.json()["ticket_id"]),
        )[0][0]
        == 0
    )


async def test_ticket_create_rejects_too_many_cis(cmdb_db: CmdbWorld) -> None:
    async with portal_client_for(cmdb_db, CUSTOMER_A) as c:
        r = await c.post(
            "/api/portal/tickets",
            json={"title": "cmdb4 Störung", "body": "x", "config_item_ids": list(range(11))},
        )
    assert r.status_code == 422


async def test_portal_routes_404_outside_mode_b(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    set_mode(cmdb_db, "off")
    before = _ticket_count(cmdb_db)
    async with portal_client_for(cmdb_db, CUSTOMER_A) as c:
        listed = await c.get("/api/portal/config-items")
        one = await c.get(f"/api/portal/config-items/{ids['mine']}")
        with_cis = await c.post(
            "/api/portal/tickets",
            json={"title": "cmdb4 Störung", "body": "x", "config_item_ids": [ids["mine"]]},
        )
        plain = await c.post("/api/portal/tickets", json={"title": "cmdb4 Störung", "body": "y"})
    assert listed.status_code == 404
    assert one.status_code == 404
    assert with_cis.status_code == 422
    assert plain.status_code == 201
    assert _ticket_count(cmdb_db) == before + 1
