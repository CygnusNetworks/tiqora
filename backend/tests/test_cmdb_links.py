"""CMDB links: link types (read side), ticket/CI/service links, permissions, history, outbox."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select, text

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    CUSTOMER_A,
    GROUP_HIDDEN,
    GROUP_RO,
    SERVICE_ID,
    TICKET_HIDDEN,
    TICKET_OPEN,
    USER_A,
    USER_B,
    CmdbWorld,
    client_for,
    make_ci,
    make_class,
)
from tiqora.cmdb import links as links_mod
from tiqora.cmdb.errors import CmdbNotFound
from tiqora.cmdb.links import LinkEnd, LinkInvalid, LinkService, LinkTypeService
from tiqora.cmdb.service import system_access
from tiqora.db.tiqora.models import TiqoraCmdbCiHistory, TiqoraItsmLink
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.itsm.models import TiqoraItsmLinkType
from tiqora.kb.models import TiqoraKbArticle

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

_TICKET_IDS = (TICKET_OPEN, TICKET_HIDDEN)


@pytest.fixture(autouse=True)
def _clean_outbox(cmdb_db: CmdbWorld) -> Any:
    for table in ("tiqora_event_outbox", "tiqora_cache_invalidation"):
        cmdb_db.sql(f"DELETE FROM {table} WHERE ticket_id BETWEEN 740300 AND 740399")  # noqa: S608
    yield
    for table in ("tiqora_event_outbox", "tiqora_cache_invalidation"):
        cmdb_db.sql(f"DELETE FROM {table} WHERE ticket_id BETWEEN 740300 AND 740399")  # noqa: S608


def _body(sk: str, skey: Any, tk: str, tkey: Any, lt: str = "RelevantTo") -> dict[str, str]:
    return {
        "source_kind": sk,
        "source_key": str(skey),
        "target_kind": tk,
        "target_key": str(tkey),
        "link_type": lt,
    }


async def _outbox(s: Any, ticket_id: int) -> list[tuple[str, str]]:
    import json

    rows = await s.execute(
        text(
            "SELECT event_type, payload FROM tiqora_event_outbox WHERE ticket_id = :t ORDER BY id"
        ),
        {"t": ticket_id},
    )
    return [(str(e), json.loads(p)) for e, p in rows.all()]


async def _history(s: Any, ci_id: int) -> list[tuple[str, str]]:
    rows = await s.execute(
        select(TiqoraCmdbCiHistory.event, TiqoraCmdbCiHistory.detail)
        .where(TiqoraCmdbCiHistory.ci_id == ci_id, TiqoraCmdbCiHistory.event.like("Link%"))
        .order_by(TiqoraCmdbCiHistory.id)
    )
    return [(str(e), str(d)) for e, d in rows.all()]


async def _link_count(s: Any) -> int:
    return int((await s.execute(select(func.count()).select_from(TiqoraItsmLink))).scalar_one())


async def _two_cis(db: CmdbWorld, **kw: Any) -> tuple[int, int]:
    async with db.factory() as s:
        cid = await make_class(s, **kw)
        a = await make_ci(s, cid, "cmdb4-a")
        b = await make_ci(s, cid, "cmdb4-b")
    return a, b


async def test_add_ticket_link_needs_rw_on_queue(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a")
    async with client_for(cmdb_db, USER_A) as c:
        ok = await c.post("/api/v1/cmdb/links", json=_body("ci", ci_id, "ticket", TICKET_OPEN))
        assert ok.status_code == 201, ok.text
        assert ok.json()["other"] == {
            "kind": "ticket",
            "key": str(TICKET_OPEN),
            "label": f"2026101074{TICKET_OPEN} cmdb4 ticket {TICKET_OPEN}",
            "hidden": False,
            "url": f"/agent/tickets/{TICKET_OPEN}",
        }
        hidden = await c.post(
            "/api/v1/cmdb/links", json=_body("ci", ci_id, "ticket", TICKET_HIDDEN)
        )
        unknown = await c.post("/api/v1/cmdb/links", json=_body("ci", ci_id, "ticket", 740399))
        assert hidden.status_code == 404
        assert hidden.json() == unknown.json()  # a queue without rw looks like an unknown ticket
    async with cmdb_db.factory() as s:
        assert await _outbox(s, TICKET_OPEN) == [
            ("LinkAdd", {"target_object": "ITSMConfigItem", "target_key": str(ci_id)})
        ]
        assert await _outbox(s, TICKET_HIDDEN) == []
        assert [e for e, _ in await _history(s, ci_id)] == ["LinkAdd"]
        cache = await s.execute(
            text("SELECT COUNT(*) FROM tiqora_cache_invalidation WHERE ticket_id = :t"),
            {"t": TICKET_OPEN},
        )
        assert int(cache.scalar_one()) == 1


async def test_ticket_link_needs_only_ro_on_the_ci_class(cmdb_db: CmdbWorld) -> None:
    # R12: rw on the ticket's queue + ro on the CI's class is enough; a CI-only link needs rw
    async with cmdb_db.factory() as s:
        cid = await make_class(s, group_id=GROUP_RO)
        a = await make_ci(s, cid, "cmdb4-a")
        b = await make_ci(s, cid, "cmdb4-b")
    async with client_for(cmdb_db, USER_A) as c:
        ticket = await c.post("/api/v1/cmdb/links", json=_body("ci", a, "ticket", TICKET_OPEN))
        assert ticket.status_code == 201, ticket.text
        ci_ci = await c.post("/api/v1/cmdb/links", json=_body("ci", a, "ci", b, "DependsOn"))
        assert ci_ci.status_code == 403
    async with client_for(cmdb_db, USER_B) as c:  # ro on the class, nothing on the queue
        denied = await c.post("/api/v1/cmdb/links", json=_body("ci", a, "ticket", TICKET_OPEN))
        assert denied.status_code == 404
    async with cmdb_db.factory() as s:
        assert await _link_count(s) == 1


async def test_add_is_idempotent_and_self_link_is_422(cmdb_db: CmdbWorld) -> None:
    a, b = await _two_cis(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        first = await c.post("/api/v1/cmdb/links", json=_body("ci", a, "ci", b, "DependsOn"))
        again = await c.post("/api/v1/cmdb/links", json=_body("ci", a, "ci", b, "DependsOn"))
        assert (first.status_code, again.status_code) == (201, 201)
        assert first.json()["id"] == again.json()["id"]
        own = await c.post("/api/v1/cmdb/links", json=_body("ci", a, "ci", a, "DependsOn"))
        assert own.status_code == 422
        no_ci = await c.post(
            "/api/v1/cmdb/links", json=_body("ticket", TICKET_OPEN, "service", SERVICE_ID)
        )
        assert no_ci.status_code == 422
    async with cmdb_db.factory() as s:
        assert await _link_count(s) == 1
        assert [e for e, _ in await _history(s, a)] == ["LinkAdd"]  # the repeat wrote nothing


async def test_unknown_or_invalid_link_type_is_422(cmdb_db: CmdbWorld) -> None:
    a, b = await _two_cis(cmdb_db)
    cmdb_db.sql("UPDATE tiqora_itsm_link_type SET valid = :v WHERE name = 'AlternativeTo'", v=False)
    try:
        async with client_for(cmdb_db, USER_A) as c:
            for name in ("NoSuchType", "AlternativeTo"):
                r = await c.post("/api/v1/cmdb/links", json=_body("ci", a, "ci", b, name))
                assert r.status_code == 422, name
                assert r.json()["detail"]["code"] == "unknown_link_type"
    finally:
        cmdb_db.sql(
            "UPDATE tiqora_itsm_link_type SET valid = :v WHERE name = 'AlternativeTo'", v=True
        )
    async with cmdb_db.factory() as s:
        assert await _link_count(s) == 0


async def test_orientation_of_stored_links(cmdb_db: CmdbWorld) -> None:
    a, b = await _two_cis(cmdb_db)
    small, big = sorted((a, b))
    async with client_for(cmdb_db, USER_A) as c:
        undirected = await c.post(
            "/api/v1/cmdb/links", json=_body("ci", big, "ci", small, "ConnectedTo")
        )
        pointed = await c.post(
            "/api/v1/cmdb/links", json=_body("ci", big, "ci", small, "DependsOn")
        )
        ticket = await c.post("/api/v1/cmdb/links", json=_body("ticket", TICKET_OPEN, "ci", a))
        assert {r.status_code for r in (undirected, pointed, ticket)} == {201}
    async with cmdb_db.factory() as s:
        rows = {
            r.link_type: (r.source_kind, r.source_key, r.target_kind, r.target_key)
            for r in (await s.execute(select(TiqoraItsmLink))).scalars()
        }
    assert rows["ConnectedTo"] == ("ci", str(small), "ci", str(big))  # smaller id first
    assert rows["DependsOn"] == ("ci", str(big), "ci", str(small))  # pointed: as given
    assert rows["RelevantTo"] == ("ci", str(a), "ticket", str(TICKET_OPEN))  # CI is the source


async def test_list_hides_tickets_without_read_and_marks_hidden_cis(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        own = await make_ci(s, await make_class(s), "cmdb4-own")
        secret = await make_ci(
            s, await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN), "cmdb4-secret"
        )
        svc = LinkService(s)
        sysa = system_access(USER_A)
        for target in (
            LinkEnd("ticket", str(TICKET_HIDDEN)),
            LinkEnd("ticket", str(TICKET_OPEN)),
            LinkEnd("ci", str(secret)),
            LinkEnd("service", str(SERVICE_ID)),
            LinkEnd("customer_user", CUSTOMER_A),
        ):
            lt = "DependsOn" if target.kind == "ci" else "RelevantTo"
            await svc.add(
                sysa,
                source=LinkEnd("ci", str(own)),
                target=target,
                link_type_key=lt,
                enforce_permissions=False,
            )
        await s.commit()
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.get("/api/v1/cmdb/links", params={"object_kind": "ci", "object_key": own})
        assert r.status_code == 200, r.text
        others = [(x["other"]["kind"], x["other"]["key"], x["other"]["hidden"]) for x in r.json()]
        assert ("ticket", str(TICKET_HIDDEN), False) not in others
        assert ("ticket", str(TICKET_OPEN), False) in others
        assert ("ci", "", True) in others  # the CI of the unreadable class: no key, no label
        hidden_row = next(x for x in r.json() if x["other"]["hidden"])
        assert hidden_row["other"]["label"] == "Kein Zugriff"
        assert hidden_row["other"]["url"] is None
        assert hidden_row["direction"] == "outgoing"  # DependsOn is pointed
        assert ("service", str(SERVICE_ID), False) in others
        assert ("customer_user", CUSTOMER_A, False) in others
        assert len(others) == 4
        # the ticket the caller cannot read is not even addressable
        gone = await c.get(
            "/api/v1/cmdb/links", params={"object_kind": "ticket", "object_key": TICKET_HIDDEN}
        )
        assert gone.status_code == 404
        # seen from the ticket: the CI row, label with number and name
        back = await c.get(
            "/api/v1/cmdb/links", params={"object_kind": "ticket", "object_key": TICKET_OPEN}
        )
        assert [x["other"]["kind"] for x in back.json()] == ["ci"]
        assert back.json()[0]["other"]["label"].endswith("cmdb4-own")
    async with client_for(cmdb_db, USER_B) as c:  # no right on the class: the CI is unknown
        r = await c.get("/api/v1/cmdb/links", params={"object_kind": "ci", "object_key": own})
        assert r.status_code == 404


async def test_direction_and_label_follow_the_viewpoint(cmdb_db: CmdbWorld) -> None:
    a, b = await _two_cis(cmdb_db)
    cmdb_db.sql(
        "UPDATE tiqora_itsm_link_type SET source_label = 'cmdb4 depends on',"
        " target_label = 'cmdb4 needed by' WHERE name = 'DependsOn'"
    )
    try:
        async with client_for(cmdb_db, USER_A) as c:
            made = await c.post("/api/v1/cmdb/links", json=_body("ci", a, "ci", b, "DependsOn"))
            assert made.status_code == 201
            assert (made.json()["direction"], made.json()["link_type_label"]) == (
                "outgoing",
                "cmdb4 depends on",
            )
            back = await c.get("/api/v1/cmdb/links", params={"object_kind": "ci", "object_key": b})
            assert (back.json()[0]["direction"], back.json()[0]["link_type_label"]) == (
                "incoming",
                "cmdb4 needed by",
            )
    finally:
        cmdb_db.sql(
            "UPDATE tiqora_itsm_link_type SET source_label = NULL, target_label = NULL"
            " WHERE name = 'DependsOn'"
        )


async def test_delete_writes_history_and_outbox(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a")
    async with client_for(cmdb_db, USER_A) as c:
        link = (
            await c.post("/api/v1/cmdb/links", json=_body("ci", ci_id, "ticket", TICKET_OPEN))
        ).json()
        assert (await c.delete(f"/api/v1/cmdb/links/{link['id']}")).status_code == 204
        assert (await c.delete(f"/api/v1/cmdb/links/{link['id']}")).status_code == 404
    async with cmdb_db.factory() as s:
        assert await _link_count(s) == 0
        assert [e for e, _ in await _history(s, ci_id)] == ["LinkAdd", "LinkDelete"]
        assert await _outbox(s, TICKET_OPEN) == [
            ("LinkAdd", {"target_object": "ITSMConfigItem", "target_key": str(ci_id)}),
            ("LinkDelete", {"target_object": "ITSMConfigItem", "target_key": str(ci_id)}),
        ]


async def test_delete_needs_the_same_rights_as_add(cmdb_db: CmdbWorld) -> None:
    a, b = await _two_cis(cmdb_db, group_id=GROUP_RO)
    async with cmdb_db.factory() as s:
        rec = await LinkService(s).add(
            system_access(USER_A),
            source=LinkEnd("ci", str(a)),
            target=LinkEnd("ci", str(b)),
            link_type_key="DependsOn",
            enforce_permissions=False,
        )
        await s.commit()
    async with client_for(cmdb_db, USER_A) as c:  # ro only
        assert (await c.delete(f"/api/v1/cmdb/links/{rec.id}")).status_code == 403
    async with client_for(cmdb_db, USER_B) as c:
        assert (await c.delete(f"/api/v1/cmdb/links/{rec.id}")).status_code == 403
    async with cmdb_db.factory() as s:
        assert await _link_count(s) == 1


async def test_other_end_must_exist(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a")
    async with client_for(cmdb_db, USER_A) as c:
        for kind, key in (
            ("service", "749999"),
            ("customer_user", "cmdb4-nobody@example.org"),
            ("customer_company", "cmdb4-no-co"),
            ("kb_article", "749999"),
            ("ci", "749999"),
            ("service", "not-a-number"),
        ):
            r = await c.post("/api/v1/cmdb/links", json=_body("ci", ci_id, kind, key))
            assert r.status_code == 404, (kind, r.text)
        ok = await c.post(
            "/api/v1/cmdb/links", json=_body("ci", ci_id, "customer_company", "cmdb4-co-a")
        )
        assert ok.status_code == 201
        assert ok.json()["other"]["label"] == "cmdb4-co-a Example"
    async with cmdb_db.factory() as s:
        assert await _link_count(s) == 1


async def test_kb_article_link(cmdb_db: CmdbWorld) -> None:
    from datetime import datetime

    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a")
        art = TiqoraKbArticle(
            category_id=1,
            title="cmdb4 article",
            slug="cmdb4-links-article",
            content_md="x",
            create_by=1,
            change_by=1,
            create_time=datetime(2026, 10, 1),
            change_time=datetime(2026, 10, 1),
        )
        s.add(art)
        await s.commit()
        art_id = int(art.id)
    try:
        async with client_for(cmdb_db, USER_A) as c:
            r = await c.post(
                "/api/v1/cmdb/links", json=_body("ci", ci_id, "kb_article", art_id, "RelevantTo")
            )
            assert r.status_code == 201, r.text
            assert r.json()["other"]["label"] == "cmdb4 article"
    finally:
        cmdb_db.sql("DELETE FROM tiqora_itsm_link WHERE target_kind = 'kb_article'")
        cmdb_db.sql("DELETE FROM tiqora_kb_article WHERE id = :i", i=art_id)


async def test_link_types_route_lists_the_offered_valid_types(cmdb_db: CmdbWorld) -> None:
    cmdb_db.sql("UPDATE tiqora_itsm_link_type SET valid = :v WHERE name = 'AlternativeTo'", v=False)
    try:
        async with client_for(cmdb_db, USER_A) as c:
            r = await c.get("/api/v1/cmdb/link-types")
    finally:
        cmdb_db.sql(
            "UPDATE tiqora_itsm_link_type SET valid = :v WHERE name = 'AlternativeTo'", v=True
        )
    assert r.status_code == 200, r.text
    by_name = {t["name"]: t for t in r.json()}
    assert set(by_name) == {"DependsOn", "Includes", "ConnectedTo", "RelevantTo"}
    assert by_name["DependsOn"]["propagation"] == "source"
    assert by_name["DependsOn"]["pointed"] is True
    assert by_name["RelevantTo"]["offer_ticket_ci"] is True
    assert [t["name"] for t in r.json()] == ["DependsOn", "Includes", "ConnectedTo", "RelevantTo"]


async def test_link_type_service_reads_the_step0_table(cmdb_db: CmdbWorld) -> None:
    cmdb_db.sql("UPDATE tiqora_itsm_link_type SET valid = :v WHERE name = 'Includes'", v=False)
    try:
        async with cmdb_db.factory() as s:
            svc = LinkTypeService(s)
            assert "Includes" not in [t.name for t in await svc.list_types()]
            assert "Includes" in [t.name for t in await svc.list_types(include_invalid=True)]
            assert await svc.propagating() == {"DependsOn": "source"}
            row = await svc.get_by_name("DependsOn")
            assert isinstance(row, TiqoraItsmLinkType)
    finally:
        cmdb_db.sql("UPDATE tiqora_itsm_link_type SET valid = :v WHERE name = 'Includes'", v=True)


async def test_ci_neighbours_filters_propagating_types(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        a, b, c3, d = [await make_ci(s, cid, f"cmdb4-n{i}") for i in range(4)]
        svc = LinkService(s)
        sysa = system_access(USER_A)
        for src, tgt, lt in ((a, b, "DependsOn"), (b, c3, "ConnectedTo"), (c3, d, "DependsOn")):
            await svc.add(
                sysa,
                source=LinkEnd("ci", str(src)),
                target=LinkEnd("ci", str(tgt)),
                link_type_key=lt,
                enforce_permissions=False,
            )
        await svc.add(
            sysa,
            source=LinkEnd("ci", str(a)),
            target=LinkEnd("service", str(SERVICE_ID)),
            link_type_key="DependsOn",
            enforce_permissions=False,
        )
        await s.commit()
        assert await svc.ci_neighbours([b], propagating_only=True) == [(a, b, "DependsOn")]
        every = await svc.ci_neighbours([b], propagating_only=False)
        assert sorted(every) == sorted([(a, b, "DependsOn"), (b, c3, "ConnectedTo")])
        assert await svc.ci_neighbours([], propagating_only=False) == []
        assert sorted(await svc.ci_neighbours([a, d], propagating_only=True)) == sorted(
            [(a, b, "DependsOn"), (c3, d, "DependsOn")]
        )


async def test_recompute_hook_runs_for_propagating_ci_links_only(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[int]] = []

    async def hook(_session: Any, ci_ids: list[int]) -> None:
        calls.append(sorted(ci_ids))

    monkeypatch.setattr(links_mod, "_recompute_hook", hook)
    a, b = await _two_cis(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        assert (
            await c.post("/api/v1/cmdb/links", json=_body("ci", a, "ci", b, "ConnectedTo"))
        ).status_code == 201
        assert calls == []  # does not propagate
        made = await c.post("/api/v1/cmdb/links", json=_body("ci", a, "ci", b, "DependsOn"))
        assert calls == [sorted((a, b))]
        svc = await c.post(
            "/api/v1/cmdb/links", json=_body("ci", a, "service", SERVICE_ID, "DependsOn")
        )
        assert svc.status_code == 201
        assert calls == [sorted((a, b))]  # a service end is read on demand, no recompute
        assert (await c.delete(f"/api/v1/cmdb/links/{made.json()['id']}")).status_code == 204
        assert calls == [sorted((a, b))] * 2


async def test_service_level_errors_are_domain_errors(cmdb_db: CmdbWorld) -> None:
    a, _ = await _two_cis(cmdb_db)
    async with cmdb_db.factory() as s:
        svc = LinkService(s)
        sysa = system_access(USER_A)
        with pytest.raises(LinkInvalid):
            await svc.add(
                sysa,
                source=LinkEnd("ci", str(a)),
                target=LinkEnd("ci", str(a)),
                link_type_key="DependsOn",
            )
        with pytest.raises(CmdbNotFound):
            await svc.add(
                sysa,
                source=LinkEnd("ci", str(a)),
                target=LinkEnd("ticket", "749999"),
                link_type_key="RelevantTo",
                enforce_permissions=False,
            )


def test_api_key_area_cmdb_maps_paths() -> None:
    assert path_to_area("/api/v1/cmdb/links") == "cmdb"
    assert path_to_area("/api/v1/cmdb/link-types") == "cmdb"


async def test_deleting_a_ci_signals_its_ticket_links(cmdb_db: CmdbWorld) -> None:
    # assumption 7: a ticket↔CI link that goes with a deleted CI is a LinkDelete for the ticket
    from tiqora.cmdb.service import CiService

    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a")
        await LinkService(s).add(
            system_access(USER_A),
            source=LinkEnd("ci", str(ci_id)),
            target=LinkEnd("ticket", str(TICKET_OPEN)),
            link_type_key="RelevantTo",
            enforce_permissions=False,
        )
        await s.commit()
        await CiService(s).delete(system_access(USER_A), ci_id, enforce_permissions=False)
        await s.commit()
        payload = {"target_object": "ITSMConfigItem", "target_key": str(ci_id)}
        assert await _outbox(s, TICKET_OPEN) == [("LinkAdd", payload), ("LinkDelete", payload)]
        cache = await s.execute(
            text("SELECT COUNT(*) FROM tiqora_cache_invalidation WHERE ticket_id = :t"),
            {"t": TICKET_OPEN},
        )
        assert int(cache.scalar_one()) == 2
