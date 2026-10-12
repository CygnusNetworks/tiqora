"""CI search: filter fields, compiler, typeahead (both dialects, band 742000-742999)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    GROUP_RO,
    USER_A,
    USER_ADMIN,
    CmdbWorld,
    _attr,
    make_ci,
    make_class,
)
from tiqora.cmdb import search as search_mod
from tiqora.cmdb.access import load_access
from tiqora.cmdb.schemas import CiSearchIn
from tiqora.cmdb.search import cmdb_filter_fields, lookup_cis, search_cis
from tiqora.db.tiqora.models import TiqoraCmdbCi
from tiqora.filters.catalog import FilterError
from tiqora.filters.model import Filter

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


def _flt(cond: dict[str, Any]) -> Filter:
    return Filter.model_validate({"version": 1, "where": cond})


def _ids(page: Any) -> list[int]:
    return [i.id for i in page.items]


async def test_search_scoped_by_class_group(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        rw = await make_class(s, key="cmdb4rw")
        hid = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        await make_ci(s, rw, "cmdb4-alpha", user_id=USER_ADMIN)
        await make_ci(s, hid, "cmdb4-alpha-hidden", user_id=USER_ADMIN)
        acc = await load_access(s, USER_A)
        page = await search_cis(s, acc, CiSearchIn(q="alpha"))
        assert [i.name for i in page.items] == ["cmdb4-alpha"]
        assert page.total == 1 and page.total_capped is False
        page = await search_cis(s, acc, CiSearchIn(class_ids=[hid]))
        assert page.items == [] and page.total == 0
        admin = await search_cis(s, await load_access(s, USER_ADMIN), CiSearchIn(q="alpha"))
        assert admin.total == 2


async def test_attribute_filter_ops(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        a = await make_ci(
            s,
            cid,
            "cmdb4-a",
            attributes={
                "NIC": [{"v": "eth0", "s": {"IPAddress": [{"v": "192.0.2.10"}]}}],
                "Vendor": [{"v": "Example"}],
            },
        )
        b = await make_ci(s, cid, "cmdb4-b", attributes={"Vendor": [{"v": "Other"}]})
        acc = await load_access(s, USER_A)

        def f(cond: dict[str, Any]) -> CiSearchIn:
            return CiSearchIn(class_ids=[cid], filter=_flt(cond))

        eq = {"field": "attr.NIC.IPAddress", "op": "eq", "value": "192.0.2.10"}
        assert _ids(await search_cis(s, acc, f(eq))) == [a]
        sw = {"field": "attr.Vendor", "op": "starts_with", "value": "oth"}
        assert _ids(await search_cis(s, acc, f(sw))) == [b]
        assert _ids(
            await search_cis(s, acc, f({"field": "attr.NIC.IPAddress", "op": "is_empty"}))
        ) == [b]
        assert _ids(
            await search_cis(s, acc, f({"field": "attr.NIC.IPAddress", "op": "is_not_empty"}))
        ) == [a]
        neq = {"field": "attr.Vendor", "op": "neq", "value": "example"}
        assert _ids(await search_cis(s, acc, f(neq))) == [b]
        inn = {"field": "attr.Vendor", "op": "in", "value": ["EXAMPLE", "nope"]}
        assert _ids(await search_cis(s, acc, f(inn))) == [a]
        con = {"field": "attr.Vendor", "op": "contains", "value": "xamp"}
        assert _ids(await search_cis(s, acc, f(con))) == [a]
        both = {"all": [con, {"not": eq}]}
        assert (await search_cis(s, acc, f(both))).items == []


async def test_header_field_filters(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        a = await make_ci(s, cid, "cmdb4-up", deployment="production", incident="operational")
        b = await make_ci(s, cid, "cmdb4-down", deployment="maintenance", incident="incident")
        acc = await load_access(s, USER_A)
        fields = {x.key: x for x in await cmdb_filter_fields(s, None)}
        dep_id = fields["deployment_state"].enum_values
        assert dep_id  # catalog ids as strings
        func = {"field": "incident_functionality", "op": "eq", "value": "incident"}
        assert _ids(await search_cis(s, acc, CiSearchIn(filter=_flt(func)))) == [b]
        name = {"field": "name", "op": "contains", "value": "UP"}
        assert _ids(await search_cis(s, acc, CiSearchIn(filter=_flt(name)))) == [a]
        cls = {"field": "class", "op": "eq", "value": "cmdb4srv"}
        assert sorted(_ids(await search_cis(s, acc, CiSearchIn(filter=_flt(cls))))) == [a, b]
        row = (
            await s.execute(select(TiqoraCmdbCi.deployment_state_id).where(TiqoraCmdbCi.id == b))
        ).scalar_one()
        dep = {"field": "deployment_state", "op": "eq", "value": str(row)}
        assert _ids(await search_cis(s, acc, CiSearchIn(filter=_flt(dep)))) == [b]
        old = {"field": "changed_at", "op": "older_than", "value": {"amount": 1, "unit": "day"}}
        assert (await search_cis(s, acc, CiSearchIn(filter=_flt(old)))).items == []
        new = {"field": "changed_at", "op": "within_last", "value": {"amount": 1, "unit": "day"}}
        assert len((await search_cis(s, acc, CiSearchIn(filter=_flt(new)))).items) == 2


async def test_q_matches_number_name_and_indexed_prefix(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        a = await make_ci(
            s,
            cid,
            "cmdb4-plain",
            attributes={"NIC": [{"v": "e", "s": {"IPAddress": [{"v": "192.0.2.5"}]}}]},
        )
        b = await make_ci(s, cid, "cmdb4_100%_b")
        c = await make_ci(s, cid, "cmdb4-ssx", attributes={"Vendor": [{"v": "Straße"}]})
        number_b = (
            await s.execute(select(TiqoraCmdbCi.number).where(TiqoraCmdbCi.id == b))
        ).scalar_one()
        acc = await load_access(s, USER_A)

        async def q(text_: str) -> list[int]:
            return _ids(await search_cis(s, acc, CiSearchIn(q=text_)))

        assert await q("192.0.2") == [a]  # attribute prefix
        assert await q("192.0.3") == []
        assert await q(number_b.lower()) == [b] and await q(number_b.upper()) == [b]
        assert await q("100%") == [b]  # LIKE wildcards are literal
        assert await q("_100") == [b]
        assert await q("%") == [b]
        assert await q("cmdb4 100") == [b]  # every token must match
        assert await q("STRASSE") == [c]  # casefolded like the index
        assert await q("trasse") == []  # attribute match is a prefix match


async def test_attr_field_requires_single_class(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        c1 = await make_class(s, key="cmdb4one")
        c2 = await make_class(s, key="cmdb4two")
        acc = await load_access(s, USER_A)
        cond = {"field": "attr.Vendor", "op": "eq", "value": "x"}
        for ids in ([], [c1, c2]):
            with pytest.raises(FilterError, match="genau eine Klasse"):
                await search_cis(s, acc, CiSearchIn(class_ids=ids, filter=_flt(cond)))
        with pytest.raises(FilterError):  # unknown field in the single class
            bad = {"field": "attr.Model", "op": "eq", "value": "x"}
            await search_cis(s, acc, CiSearchIn(class_ids=[c1], filter=_flt(bad)))


async def test_unsearchable_attribute_is_not_a_filter_field(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        specs = await cmdb_filter_fields(s, cid)
        keys = [f.key for f in specs]
        assert "attr.Vendor" in keys and "attr.NIC.IPAddress" in keys
        assert "attr.Model" not in keys
        assert "attr.Vendor" not in [f.key for f in await cmdb_filter_fields(s, None)]
        assert "cmdb4srv" in {f.key: f for f in specs}["class"].enum_values


async def test_class_enum_lists_readable_classes_only(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        await make_class(s, key="cmdb4rw")
        hid = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        acc = await load_access(s, USER_A)
        from tiqora.cmdb.access import readable_class_ids

        readable = await readable_class_ids(s, acc)
        assert hid not in readable
        specs = await cmdb_filter_fields(s, None, readable_class_ids=readable)
        enum = {f.key: f for f in specs}["class"].enum_values
        assert "cmdb4rw" in enum and "cmdb4hid" not in enum


async def test_typed_attribute_fields(cmdb_db: CmdbWorld) -> None:
    attrs = [
        _attr("Cores", "Cores", "integer", searchable=True),
        _attr("Price", "Price", "money", searchable=True, options={"currency": "EUR"}),
        _attr("Bought", "Bought", "date", searchable=True),
        _attr("Boss", "Boss", "ci_ref", searchable=True),
    ]
    async with cmdb_db.factory() as s:
        cid = await make_class(s, attributes=attrs)
        a = await make_ci(
            s,
            cid,
            "cmdb4-big",
            attributes={
                "Cores": [{"v": 64}],
                "Price": [{"v": "16.5"}],
                "Bought": [{"v": "2026-03-01"}],
            },
        )
        b = await make_ci(
            s,
            cid,
            "cmdb4-small",
            attributes={
                "Cores": [{"v": 4}],
                "Price": [{"v": "20"}],
                "Bought": [{"v": "2024-01-01"}],
            },
        )
        acc = await load_access(s, USER_A)
        kinds = {f.key: f.type for f in await cmdb_filter_fields(s, cid)}
        assert kinds["attr.Cores"] == "int" and kinds["attr.Price"] == "string"
        assert kinds["attr.Bought"] == "datetime" and kinds["attr.Boss"] == "id"

        def f(cond: dict[str, Any]) -> CiSearchIn:
            return CiSearchIn(class_ids=[cid], filter=_flt(cond))

        assert _ids(
            await search_cis(s, acc, f({"field": "attr.Cores", "op": "gte", "value": 8}))
        ) == [a]
        between = {"field": "attr.Cores", "op": "between", "value": {"from": 1, "to": 8}}
        assert _ids(await search_cis(s, acc, f(between))) == [b]
        assert _ids(
            await search_cis(s, acc, f({"field": "attr.Price", "op": "eq", "value": "16.50"}))
        ) == [a]
        assert _ids(
            await search_cis(s, acc, f({"field": "attr.Price", "op": "neq", "value": "16.5"}))
        ) == [b]
        after = {"field": "attr.Bought", "op": "after", "value": "2025-01-01"}
        assert _ids(await search_cis(s, acc, f(after))) == [a]
        assert (
            await search_cis(s, acc, f({"field": "attr.Boss", "op": "eq", "value": 5}))
        ).items == []
        with pytest.raises(FilterError):
            await search_cis(s, acc, f({"field": "attr.Price", "op": "eq", "value": "abc"}))


async def test_sort_and_paging(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        ids = [await make_ci(s, cid, f"cmdb4-s{n}") for n in (2, 3, 1)]
        acc = await load_access(s, USER_A)
        page = await search_cis(s, acc, CiSearchIn(sort="name", order="desc", limit=2))
        assert [i.name for i in page.items] == ["cmdb4-s3", "cmdb4-s2"] and page.total == 3
        page = await search_cis(s, acc, CiSearchIn(sort="name", offset=2, limit=2))
        assert [i.name for i in page.items] == ["cmdb4-s3"] and page.total == 3
        page = await search_cis(s, acc, CiSearchIn(sort="number"))
        assert _ids(page) == ids
        for key in ("class", "changed_at", "deployment_state", "incident_state"):
            assert (await search_cis(s, acc, CiSearchIn(sort=key))).total == 3  # type: ignore[arg-type]


async def test_count_cap(cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_mod, "COUNT_CAP", 3)
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        for n in range(5):
            await make_ci(s, cid, f"cmdb4-c{n}")
        acc = await load_access(s, USER_A)
        page = await search_cis(s, acc, CiSearchIn(limit=2))
        assert page.total == 3 and page.total_capped is True and len(page.items) == 2


async def test_lookup_exact_number_first(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        other = await make_class(s, key="cmdb4other")
        first = await make_ci(s, cid, "cmdb4-zzz")
        second = await make_ci(s, cid, "cmdb4-aaa")
        await make_ci(s, other, "cmdb4-other-thing")
        number = (
            await s.execute(select(TiqoraCmdbCi.number).where(TiqoraCmdbCi.id == first))
        ).scalar_one()
        acc = await load_access(s, USER_A)
        refs = await lookup_cis(s, acc, number)
        assert [r.id for r in refs] == [first]
        refs = await lookup_cis(s, acc, "cmdb4")
        assert [r.name for r in refs] == ["cmdb4-aaa", "cmdb4-other-thing", "cmdb4-zzz"]
        refs = await lookup_cis(s, acc, "cmdb4", class_keys=["cmdb4srv"])
        assert [r.id for r in refs] == [second, first]
        assert refs[0].class_key == "cmdb4srv" and refs[0].number
        assert [r.id for r in await lookup_cis(s, acc, "cmdb4", limit=1)] == [second]
        # exact number match is listed before alphabetical matches
        both = await lookup_cis(s, acc, number.lower())
        assert both[0].id == first


async def test_lookup_hides_unreadable_classes(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        hid = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        ci = await make_ci(s, hid, "cmdb4-secret", user_id=USER_ADMIN)
        number = (
            await s.execute(select(TiqoraCmdbCi.number).where(TiqoraCmdbCi.id == ci))
        ).scalar_one()
        acc = await load_access(s, USER_A)
        assert await lookup_cis(s, acc, "secret") == []
        assert await lookup_cis(s, acc, number) == []
        admin = await load_access(s, USER_ADMIN)
        assert [r.id for r in await lookup_cis(s, admin, "secret")] == [ci]


async def test_ro_group_user_can_search(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s, key="cmdb4ro", group_id=GROUP_RO)
        ci = await make_ci(s, cid, "cmdb4-ro", user_id=USER_ADMIN)
        page = await search_cis(s, await load_access(s, USER_A), CiSearchIn(q="cmdb4-ro"))
        assert _ids(page) == [ci]
