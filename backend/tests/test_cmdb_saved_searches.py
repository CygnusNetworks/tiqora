"""Saved CI searches (both dialects, band 742000-742999)."""

from __future__ import annotations

import pytest
from sqlalchemy import update

from tests import _cmdb_helpers
from tests._cmdb_helpers import USER_A, USER_ADMIN, USER_B, CmdbWorld
from tiqora.cmdb.errors import CmdbForbidden, CmdbNotFound
from tiqora.cmdb.saved_searches import (
    create_saved,
    delete_saved,
    list_saved,
    load_criteria,
    saved_to_out,
    update_saved,
)
from tiqora.cmdb.schemas import CiSearchCriteria, CmdbSavedSearchIn
from tiqora.db.tiqora.models import TiqoraCmdbSavedSearch
from tiqora.filters.model import Filter

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


def _body(name: str, *, shared: bool = False, q: str | None = None) -> CmdbSavedSearchIn:
    flt = Filter.model_validate(
        {"version": 1, "where": {"not": {"field": "name", "op": "eq", "value": "x"}}}
    )
    return CmdbSavedSearchIn(
        name=name,
        shared=shared,
        criteria=CiSearchCriteria(q=q, filter=flt, class_ids=[7], sort="name", order="desc"),
    )


async def test_create_list_own_and_shared(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        mine = await create_saved(s, USER_A, _body("cmdb4 mine", q="srv"))
        shared = await create_saved(s, USER_B, _body("cmdb4 shared", shared=True))
        await create_saved(s, USER_B, _body("cmdb4 private"))
        await s.commit()
        rows = await list_saved(s, USER_A)
        assert [r.id for r in rows] == [mine.id, shared.id]
        out = saved_to_out(rows[0], USER_A)
        assert out.owned is True and out.criteria.q == "srv"
        assert out.criteria.sort == "name" and out.criteria.order == "desc"
        assert out.criteria.class_ids == [7]
        assert out.criteria.filter is not None and out.criteria.filter.where is not None
        assert saved_to_out(rows[1], USER_A).owned is False


async def test_update_and_delete_by_owner(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        row = await create_saved(s, USER_A, _body("cmdb4 one"))
        await s.commit()
        upd = await update_saved(s, USER_A, row.id, _body("cmdb4 two", shared=True, q="new"))
        assert upd.name == "cmdb4 two" and upd.shared is True
        assert load_criteria(upd.criteria).q == "new"
        await delete_saved(s, USER_A, row.id)
        await s.commit()
        assert await list_saved(s, USER_A) == []
        with pytest.raises(CmdbNotFound):
            await delete_saved(s, USER_A, row.id)


async def test_non_owner_cannot_change(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        shared = await create_saved(s, USER_A, _body("cmdb4 shared", shared=True))
        private = await create_saved(s, USER_A, _body("cmdb4 private"))
        await s.commit()
        with pytest.raises(CmdbForbidden):
            await update_saved(s, USER_B, shared.id, _body("cmdb4 hijack"))
        with pytest.raises(CmdbForbidden):
            await delete_saved(s, USER_ADMIN, shared.id)
        with pytest.raises(CmdbNotFound):  # somebody else's private search does not exist for B
            await delete_saved(s, USER_B, private.id)
        assert {r.id for r in await list_saved(s, USER_A)} == {shared.id, private.id}


async def test_broken_criteria_reads_as_empty(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        row = await create_saved(s, USER_A, _body("cmdb4 broken"))
        await s.commit()
        for raw in ("{not json", '{"sort": "bogus"}', "[]"):
            await s.execute(
                update(TiqoraCmdbSavedSearch)
                .where(TiqoraCmdbSavedSearch.id == row.id)
                .values(criteria=raw)
            )
            await s.commit()
            s.expire_all()
            (got,) = await list_saved(s, USER_A)
            crit = saved_to_out(got, USER_A).criteria
            assert crit == CiSearchCriteria()
            assert crit.sort == "number" and crit.filter is None and crit.class_ids == []
