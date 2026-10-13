"""CI REST API: CRUD, versions, diff, history, search, form options, saved searches."""

from __future__ import annotations

from typing import Any

import pytest

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    GROUP_RO,
    SERVER_ATTRS,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    _dep,
    _inc,
    client_for,
    make_ci,
    make_class,
    parse_attrs,
)
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.seed import DEPLOYMENT_STATE

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


def _new_ci_body(
    cid: int, dep: int, inc: int, name: str = "cmdb4-web-01", **attrs: Any
) -> dict[str, Any]:
    return {
        "class_id": cid,
        "name": name,
        "deployment_state_id": dep,
        "incident_state_id": inc,
        "attributes": attrs or {"Vendor": [{"v": "Example"}]},
    }


def _put_body(ci: dict[str, Any], **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "expected_lock_version": ci["lock_version"],
        "name": ci["name"],
        "deployment_state_id": ci["deployment_state_id"],
        "incident_state_id": ci["incident_state_id"],
        "attributes": ci["current_version"]["attributes"],
    }
    body.update(over)
    return body


async def _world(db: CmdbWorld, **kw: Any) -> tuple[int, int, int]:
    async with db.factory() as s:
        cid = await make_class(s, **kw)
        return cid, await _dep(s, "production"), await _inc(s, "operational")


async def test_create_get_update_round_trip(cmdb_db: CmdbWorld) -> None:
    cid, dep, inc = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.post("/api/v1/cmdb/cis", json=_new_ci_body(cid, dep, inc))
        assert r.status_code == 201, r.text
        ci = r.json()
        assert ci["number"].startswith("CMDB4SRV-")
        assert ci["rendered"][0]["label"] == "Vendor"
        assert ci["class"]["class_key"] == "cmdb4srv"
        assert ci["can_write"] is True and ci["unknown_keys"] == []
        assert ci["states"]["deployment"]["item_key"] == "production"
        assert ci["states"]["incident_current"]["item_key"] == "operational"
        assert ci["version_count"] == 1 and ci["current_version"]["attributes"] == {
            "Vendor": [{"v": "Example"}]
        }
        got = await c.get(f"/api/v1/cmdb/cis/{ci['id']}")
        assert got.status_code == 200 and got.json() == ci
        same = await c.put(f"/api/v1/cmdb/cis/{ci['id']}", json=_put_body(ci))
        assert same.status_code == 200, same.text
        assert same.json()["changed"] is False
        assert same.json()["ci"]["lock_version"] == ci["lock_version"]
        diff = await c.put(f"/api/v1/cmdb/cis/{ci['id']}", json=_put_body(ci, name="cmdb4-web-02"))
        saved = diff.json()
        assert diff.status_code == 200 and saved["changed"] is True
        assert saved["ci"]["name"] == "cmdb4-web-02"
        assert saved["ci"]["lock_version"] == ci["lock_version"] + 1
        assert saved["ci"]["version_count"] == 2


async def test_put_stale_is_409(cmdb_db: CmdbWorld) -> None:
    cid, dep, inc = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        ci = (await c.post("/api/v1/cmdb/cis", json=_new_ci_body(cid, dep, inc))).json()
        ok = await c.put(f"/api/v1/cmdb/cis/{ci['id']}", json=_put_body(ci, name="cmdb4-new"))
        assert ok.status_code == 200
        stale = await c.put(f"/api/v1/cmdb/cis/{ci['id']}", json=_put_body(ci, name="cmdb4-late"))
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "conflict"


async def test_put_without_attributes_keeps_the_values(cmdb_db: CmdbWorld) -> None:
    # D11: neither attributes nor attributes_patch means "keep"; an API client that only
    # changes the name must not wipe the attribute values (and the search index)
    cid, dep, inc = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        ci = (await c.post("/api/v1/cmdb/cis", json=_new_ci_body(cid, dep, inc))).json()
        body = _put_body(ci, name="cmdb4-renamed")
        del body["attributes"]
        r = await c.put(f"/api/v1/cmdb/cis/{ci['id']}", json=body)
        assert r.status_code == 200, r.text
        saved = r.json()
        assert saved["changed"] is True and saved["ci"]["name"] == "cmdb4-renamed"
        assert saved["ci"]["current_version"]["attributes"] == {"Vendor": [{"v": "Example"}]}
        null = _put_body(saved["ci"], name="cmdb4-renamed-2", attributes=None)
        r2 = await c.put(f"/api/v1/cmdb/cis/{ci['id']}", json=null)
        assert r2.status_code == 200, r2.text
        assert r2.json()["ci"]["current_version"]["attributes"] == {"Vendor": [{"v": "Example"}]}
        found = await c.post("/api/v1/cmdb/cis/search", json={"q": "Exam"})
        assert [i["id"] for i in found.json()["items"]] == [ci["id"]]


async def test_put_invalid_values_is_422_with_paths(cmdb_db: CmdbWorld) -> None:
    cid, dep, inc = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        ci = (await c.post("/api/v1/cmdb/cis", json=_new_ci_body(cid, dep, inc))).json()
        r = await c.put(
            f"/api/v1/cmdb/cis/{ci['id']}", json=_put_body(ci, attributes={"CPU": [{"v": "abc"}]})
        )
        bad_create = await c.post(
            "/api/v1/cmdb/cis", json=_new_ci_body(cid, dep, inc, CPU=[{"v": "abc"}])
        )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_values"
    assert [i["path"] for i in detail["issues"]][0].startswith("CPU")
    assert all({"path", "code", "message"} <= set(i) for i in detail["issues"])
    assert bad_create.status_code == 422 and bad_create.json()["detail"]["code"] == "invalid_values"


async def test_put_unknown_attributes_is_422_with_keys(cmdb_db: CmdbWorld) -> None:
    cid, dep, inc = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        ci = (
            await c.post(
                "/api/v1/cmdb/cis",
                json=_new_ci_body(cid, dep, inc, Vendor=[{"v": "Example"}], Model=[{"v": "X1"}]),
            )
        ).json()
        async with cmdb_db.factory() as s:
            kept = [a for a in SERVER_ATTRS if a["key"] != "Model"]
            await ClassService(s).add_definition(
                cid, parse_attrs(kept), comment=None, user_id=USER_ADMIN
            )
            await s.commit()
        again = (await c.get(f"/api/v1/cmdb/cis/{ci['id']}")).json()
        assert again["unknown_keys"] == ["Model"]
        body = _put_body(again, attributes={"Vendor": [{"v": "Other"}]})
        r = await c.put(f"/api/v1/cmdb/cis/{ci['id']}", json=body)
        assert r.status_code == 422
        assert r.json()["detail"] == {"code": "unknown_attributes", "keys": ["Model"]}
        ok = await c.put(f"/api/v1/cmdb/cis/{ci['id']}", json={**body, "drop_unknown": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["changed"] is True and ok.json()["ci"]["unknown_keys"] == []


async def test_unreadable_ci_is_404_same_as_unknown(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        hid = await make_ci(
            s,
            await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN),
            "cmdb4-x",
            user_id=USER_ADMIN,
        )
    async with client_for(cmdb_db, USER_A) as c:
        r1 = await c.get(f"/api/v1/cmdb/cis/{hid}")
        r2 = await c.get("/api/v1/cmdb/cis/987654321")
        sub = [
            await c.get(f"/api/v1/cmdb/cis/{hid}/versions"),
            await c.get(f"/api/v1/cmdb/cis/{hid}/history"),
            await c.delete(f"/api/v1/cmdb/cis/{hid}"),
        ]
    assert r1.status_code == r2.status_code == 404 and r1.json() == r2.json()
    assert [r.status_code for r in sub] == [404, 404, 404]


async def test_read_only_user_cannot_write(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s, key="cmdb4ro", group_id=GROUP_RO)
        ci_id = await make_ci(s, cid, "cmdb4-ro", user_id=USER_ADMIN)
        dep, inc = await _dep(s, "production"), await _inc(s, "operational")
    async with client_for(cmdb_db, USER_B) as c:
        got = await c.get(f"/api/v1/cmdb/cis/{ci_id}")
        assert got.status_code == 200 and got.json()["can_write"] is False
        put = await c.put(f"/api/v1/cmdb/cis/{ci_id}", json=_put_body(got.json(), name="cmdb4-no"))
        post = await c.post("/api/v1/cmdb/cis", json=_new_ci_body(cid, dep, inc))
        rm = await c.delete(f"/api/v1/cmdb/cis/{ci_id}")
    assert (put.status_code, post.status_code, rm.status_code) == (403, 403, 403)
    assert put.json()["detail"]["code"] == "forbidden"


async def test_delete_removes_the_ci(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-del")
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.delete(f"/api/v1/cmdb/cis/{ci_id}")
        assert r.status_code == 204 and r.content == b""
        assert (await c.get(f"/api/v1/cmdb/cis/{ci_id}")).status_code == 404
        assert (await c.delete(f"/api/v1/cmdb/cis/{ci_id}")).status_code == 404


async def test_search_route_and_invalid_filter_422(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        hid = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        a = await make_ci(s, cid, "cmdb4-alpha", attributes={"Vendor": [{"v": "Example"}]})
        await make_ci(s, cid, "cmdb4-beta")
        await make_ci(s, hid, "cmdb4-alpha-hidden", user_id=USER_ADMIN)
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.post("/api/v1/cmdb/cis/search", json={"q": "alpha"})
        assert r.status_code == 200, r.text
        page = r.json()
        assert [i["id"] for i in page["items"]] == [a] and page["total"] == 1
        assert page["total_capped"] is False
        flt = {"version": 1, "where": {"field": "attr.Vendor", "op": "eq", "value": "Example"}}
        one = await c.post("/api/v1/cmdb/cis/search", json={"class_ids": [cid], "filter": flt})
        assert [i["id"] for i in one.json()["items"]] == [a]
        no_class = await c.post("/api/v1/cmdb/cis/search", json={"filter": flt})
        assert no_class.status_code == 422
        assert no_class.json()["detail"]["code"] == "invalid_filter"
        bad_field = {"version": 1, "where": {"field": "nope", "op": "eq", "value": "x"}}
        unknown = await c.post("/api/v1/cmdb/cis/search", json={"filter": bad_field})
        assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "invalid_filter"
        over = await c.post("/api/v1/cmdb/cis/search", json={"limit": 500})
        assert over.status_code == 422


async def test_lookup_route(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        first = await make_ci(s, cid, "cmdb4-lookup-one")
        await make_ci(s, cid, "cmdb4-other")
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.get("/api/v1/cmdb/cis/lookup", params={"q": "lookup", "limit": 5})
        assert r.status_code == 200
        assert [i["id"] for i in r.json()] == [first]
        assert r.json()[0]["class_key"] == "cmdb4srv"
        none = await c.get("/api/v1/cmdb/cis/lookup", params={"q": "lookup", "class_keys": "zzz"})
        assert none.json() == []


async def test_form_options_only_for_used_types(cmdb_db: CmdbWorld) -> None:
    cid, _, _ = await _world(cmdb_db)

    def attr(key: str, type_: str, **options: Any) -> dict[str, Any]:
        return {
            "key": key,
            "name": key,
            "type": type_,
            "required": False,
            "searchable": False,
            "portal": False,
            "count_min": 0,
            "count_max": 1,
            "count_default": 1,
            "default": None,
            "help": None,
            "options": options,
            "sub": [],
        }

    async with cmdb_db.factory() as s:
        used = await make_class(
            s,
            key="cmdb4opt",
            attributes=[
                attr("Svc", "service"),
                attr("Prio", "priority"),
                attr("Who", "agent"),
                attr("Env", "catalog", catalog_class=DEPLOYMENT_STATE),
            ],
        )
    async with client_for(cmdb_db, USER_A) as c:
        plain = await c.get("/api/v1/cmdb/form-options", params={"class_id": cid})
        assert plain.status_code == 200, plain.text
        # SERVER_ATTRS uses customer_user, ci_ref, money ... but none of the option types
        assert plain.json() == {}
        r = await c.get("/api/v1/cmdb/form-options", params={"class_id": used})
        body = r.json()
        assert set(body) == {"service", "priority", "agent", "catalog"}
        assert "production" in [o["label"].lower() for o in body["catalog"][DEPLOYMENT_STATE]]
        assert {"value", "label"} == set(body["priority"][0])
        assert str(USER_A) in [o["value"] for o in body["agent"]]
        hidden = await make_hidden_class(cmdb_db)
        assert (
            await c.get("/api/v1/cmdb/form-options", params={"class_id": hidden})
        ).status_code == 404


async def make_hidden_class(db: CmdbWorld) -> int:
    async with db.factory() as s:
        return await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)


async def test_filter_fields_route(cmdb_db: CmdbWorld) -> None:
    cid, _, _ = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.get("/api/v1/cmdb/filter-fields", params={"class_id": cid})
        keys = [f["key"] for f in r.json()]
        assert r.status_code == 200
        assert "attr.Vendor" in keys and "attr.Model" not in keys
        assert all({"key", "type", "ops", "enum_values"} <= set(f) for f in r.json())
        generic = [f["key"] for f in (await c.get("/api/v1/cmdb/filter-fields")).json()]
        assert generic and not any(k.startswith("attr.") for k in generic)
        hidden = await make_hidden_class(cmdb_db)
        miss = await c.get("/api/v1/cmdb/filter-fields", params={"class_id": hidden})
        assert miss.status_code == 404


async def test_companies_lookup(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_A) as c:
        by_name = await c.get("/api/v1/cmdb/companies/lookup", params={"q": "CO-B EXAMPLE"})
        assert by_name.status_code == 200, by_name.text
        assert [o["id"] for o in by_name.json()] == ["cmdb4-co-b"]
        assert "cmdb4-co-b" in by_name.json()[0]["label"]
        by_id = await c.get("/api/v1/cmdb/companies/lookup", params={"q": "CMDB4-CO-"})
        assert sorted(o["id"] for o in by_id.json()) == ["cmdb4-co-a", "cmdb4-co-b", "cmdb4-co-c"]
        limited = await c.get(
            "/api/v1/cmdb/companies/lookup", params={"q": "cmdb4-co-", "limit": 2}
        )
        assert len(limited.json()) == 2
        wild = await c.get("/api/v1/cmdb/companies/lookup", params={"q": "%"})
        assert wild.json() == []
        assert (
            await c.get("/api/v1/cmdb/companies/lookup", params={"q": "x", "limit": 51})
        ).status_code == 422


async def test_versions_diff_history_routes(cmdb_db: CmdbWorld) -> None:
    cid, dep, inc = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        ci = (await c.post("/api/v1/cmdb/cis", json=_new_ci_body(cid, dep, inc))).json()
        upd = await c.put(
            f"/api/v1/cmdb/cis/{ci['id']}",
            json=_put_body(
                ci, attributes={"Vendor": [{"v": "Other"}]}, change_note="neuer Hersteller"
            ),
        )
        assert upd.status_code == 200
        base = f"/api/v1/cmdb/cis/{ci['id']}"
        vs = (await c.get(f"{base}/versions")).json()
        assert [v["change_note"] for v in vs["items"]] == ["neuer Hersteller", None]
        assert vs["has_more"] is False and vs["archived_count"] == 0
        newest, oldest = vs["items"][0]["id"], vs["items"][1]["id"]
        one = await c.get(f"{base}/versions/{oldest}")
        assert one.status_code == 200
        assert one.json()["attributes"] == {"Vendor": [{"v": "Example"}]}
        assert one.json()["rendered"][0]["display"] == "Example"
        page = (await c.get(f"{base}/versions", params={"limit": 1})).json()
        assert len(page["items"]) == 1 and page["has_more"] is True
        older = await c.get(f"{base}/versions", params={"before_id": newest})
        assert [v["id"] for v in older.json()["items"]] == [oldest]
        d = await c.get(f"{base}/diff", params={"from_version": oldest, "to_version": newest})
        assert d.status_code == 200
        assert d.json()["rows"] == [
            {
                "path": "Vendor[1]",
                "label": "Vendor",
                "kind": "changed",
                "old": "Example",
                "new": "Other",
            }
        ]
        h = (await c.get(f"{base}/history")).json()
        assert [e["event"] for e in h["items"]][0] == "ValueUpdate"
        assert {"ConfigItemCreate", "VersionCreate"} <= {e["event"] for e in h["items"]}
        assert h["items"][0]["created_by_name"] == "Anna Alpha"
        other_ci = await make_ci_for(cmdb_db, cid)
        foreign = await c.get(f"/api/v1/cmdb/cis/{other_ci}/versions/{oldest}")
        assert foreign.status_code == 404
        bad = await c.get(f"{base}/diff", params={"from_version": oldest, "to_version": 987654321})
        assert bad.status_code == 404


async def make_ci_for(db: CmdbWorld, class_id: int) -> int:
    async with db.factory() as s:
        return await make_ci(s, class_id, "cmdb4-second")


async def test_saved_search_routes(cmdb_db: CmdbWorld) -> None:
    crit = {"q": "alpha", "sort": "name", "order": "desc"}
    async with client_for(cmdb_db, USER_A) as a, client_for(cmdb_db, USER_B) as b:
        mine = await a.post(
            "/api/v1/cmdb/saved-searches", json={"name": "Meine", "shared": False, "criteria": crit}
        )
        assert mine.status_code == 201, mine.text
        assert mine.json()["owned"] is True and mine.json()["criteria"]["q"] == "alpha"
        shared = await a.post(
            "/api/v1/cmdb/saved-searches",
            json={"name": "Geteilt", "shared": True, "criteria": {"q": "beta"}},
        )
        sid, pid = shared.json()["id"], mine.json()["id"]
        seen_a = (await a.get("/api/v1/cmdb/saved-searches")).json()
        seen_b = (await b.get("/api/v1/cmdb/saved-searches")).json()
        assert [s["name"] for s in seen_a] == ["Geteilt", "Meine"]
        assert [(s["name"], s["owned"]) for s in seen_b] == [("Geteilt", False)]
        body = {"name": "x", "shared": True, "criteria": {}}
        assert (await b.put(f"/api/v1/cmdb/saved-searches/{sid}", json=body)).status_code == 403
        assert (await b.put(f"/api/v1/cmdb/saved-searches/{pid}", json=body)).status_code == 404
        assert (await b.delete(f"/api/v1/cmdb/saved-searches/{sid}")).status_code == 403
        upd = await a.put(
            f"/api/v1/cmdb/saved-searches/{pid}",
            json={"name": "Umbenannt", "shared": True, "criteria": crit},
        )
        assert upd.status_code == 200 and upd.json()["name"] == "Umbenannt"
        assert (await a.delete(f"/api/v1/cmdb/saved-searches/{pid}")).status_code == 204
        assert (await a.delete(f"/api/v1/cmdb/saved-searches/{pid}")).status_code == 404
        names = [s["name"] for s in (await a.get("/api/v1/cmdb/saved-searches")).json()]
    assert names == ["Geteilt"]
