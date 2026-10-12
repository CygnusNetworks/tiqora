"""CMDB performance with 100 000 CIs (opt-in: ``TIQORA_PERF_TESTS=1``, both dialects).

The CIs are written with Core ``executemany`` straight into the ``tiqora_cmdb_*`` tables (ids from
7 450 000 000 so they cannot collide with the band's other rows); the timings are printed and
asserted against the budgets of the plan. Never raise a budget: add an index or fix the query.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, func, insert, select, text

from tests import _cmdb_helpers
from tests._cmdb_helpers import USER_A, CmdbWorld, _dep, _inc, make_class
from tiqora.cmdb.access import load_access
from tiqora.cmdb.schemas import CiSearchIn
from tiqora.cmdb.search import lookup_cis, search_cis
from tiqora.cmdb.service import CiService
from tiqora.db.tiqora.models import (
    TiqoraCmdbCi,
    TiqoraCmdbCiAttr,
    TiqoraCmdbCiVersion,
    TiqoraCmdbClass,
)
from tiqora.filters.model import Filter

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        os.environ.get("TIQORA_PERF_TESTS") != "1", reason="set TIQORA_PERF_TESTS=1"
    ),
]

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

TOTAL = 100_000
CHUNK = 5_000
BASE_ID = 7_450_000_000
STAMP = datetime(2026, 10, 1, 8, 0)


def _attr_eq(path: str, value: str) -> Filter:
    return Filter.model_validate(
        {"version": 1, "where": {"field": f"attr.{path}", "op": "eq", "value": value}}
    )


def _doc(i: int) -> str:
    return json.dumps(
        {
            "SerialNumber": [{"v": f"SN-{i:07d}"}],
            "Vendor": [{"v": f"Vendor{i % 50}"}],
            "NIC": [{"v": "eth0", "s": {"IPAddress": [{"v": _ip(i, 0)}, {"v": _ip(i, 1)}]}}],
        }
    )


def _ip(i: int, k: int) -> str:
    # 192.0.2.x is documentation space; widen the host part so 100k CIs stay distinct
    n = i * 2 + k
    return f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}"


def _fill(world: CmdbWorld, class_id: int, definition_id: int, dep: int, inc: int) -> None:
    engine = create_engine(world.sync_url)
    try:
        for start in range(0, TOTAL, CHUNK):
            idx = range(start, min(start + CHUNK, TOTAL))
            cis, versions, attrs = [], [], []
            for i in idx:
                cid = BASE_ID + i
                extra = i % 10 == 0  # 10 % of the CIs have three versions
                cis.append(
                    {
                        "id": cid,
                        "number": f"CMDB4SRV-{i:07d}",
                        "class_id": class_id,
                        "name": f"cmdb4-ci-{i:06d}",
                        "deployment_state_id": dep,
                        "incident_state_id": inc,
                        "cur_incident_state_id": inc,
                        "current_version_id": None,
                        "version_count": 3 if extra else 1,
                        "lock_version": 1,
                        "created_by": USER_A,
                        "created_at": STAMP,
                        "changed_by": USER_A,
                        "changed_at": STAMP,
                    }
                )
                for _ in range(3 if extra else 1):
                    versions.append(
                        {
                            "ci_id": cid,
                            "definition_id": definition_id,
                            "name": f"cmdb4-ci-{i:06d}",
                            "deployment_state_id": dep,
                            "incident_state_id": inc,
                            "attributes": _doc(i),
                            "change_note": None,
                            "created_by": USER_A,
                            "created_at": STAMP,
                        }
                    )
                rows: list[tuple[str, int, str]] = [
                    ("SerialNumber", 1, f"SN-{i:07d}"),
                    ("Vendor", 1, f"Vendor{i % 50}"),
                    ("NIC.IPAddress", 1, _ip(i, 0)),
                    ("NIC.IPAddress", 2, _ip(i, 1)),
                    ("Model", 1, f"Model{i % 20}"),
                    ("Notes", 1, f"note {i}"),
                ]
                attrs.extend(
                    {
                        "ci_id": cid,
                        "attr_path": p,
                        "position": pos,
                        "value_text": v,
                        "value_key": v.casefold(),
                        "value_num": None,
                        "value_date": None,
                    }
                    for p, pos, v in rows
                )
            with engine.begin() as conn:
                conn.execute(insert(TiqoraCmdbCi), cis)
                conn.execute(insert(TiqoraCmdbCiVersion), versions)
                conn.execute(insert(TiqoraCmdbCiAttr), attrs)
        with engine.begin() as conn:
            # the current version = the newest version row of each CI
            conn.execute(
                text(
                    "UPDATE tiqora_cmdb_ci SET current_version_id = (SELECT MAX(v.id) FROM"
                    " tiqora_cmdb_ci_version v WHERE v.ci_id = tiqora_cmdb_ci.id)"
                    " WHERE id >= :b"
                ),
                {"b": BASE_ID},
            )
    finally:
        engine.dispose()


def _explain(world: CmdbWorld, sql: str, **params: Any) -> str:
    rows = world.sql("EXPLAIN " + sql, **params)
    return "\n".join(" | ".join(str(c) for c in r) for r in rows)


def _timed(label: str, fn_result: float, budget: float) -> None:
    print(f"PERF {label}: {fn_result * 1000:.1f} ms (budget {budget * 1000:.0f} ms)")
    assert fn_result < budget, f"{label} took {fn_result:.3f}s"


async def test_100k_cis_search_and_lookup_under_2s(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        dep, inc = await _dep(s, "production"), await _inc(s, "operational")
        definition_id = int(
            (
                await s.execute(
                    select(TiqoraCmdbClass.current_definition_id).where(TiqoraCmdbClass.id == cid)
                )
            ).scalar_one()
        )
    started = time.perf_counter()
    _fill(cmdb_db, cid, definition_id, dep, inc)
    print(f"PERF fill {TOTAL} CIs: {time.perf_counter() - started:.1f} s")
    try:
        async with cmdb_db.factory() as s:
            acc = await load_access(s, USER_A)

            t = time.perf_counter()
            page = await search_cis(s, acc, CiSearchIn(q="10.0.1.77"))
            elapsed = time.perf_counter() - t
            assert page.total >= 1 and any(i.number.startswith("CMDB4SRV-") for i in page.items)
            _timed("q=ip", elapsed, 2.0)

            t = time.perf_counter()
            page = await search_cis(
                s,
                acc,
                CiSearchIn(class_ids=[cid], filter=_attr_eq("SerialNumber", "SN-0099999")),
            )
            elapsed = time.perf_counter() - t
            assert [i.number for i in page.items] == ["CMDB4SRV-0099999"]
            _timed("filter SerialNumber eq", elapsed, 2.0)

            t = time.perf_counter()
            page = await search_cis(
                s, acc, CiSearchIn(class_ids=[cid], sort="name", offset=50_000, limit=50)
            )
            elapsed = time.perf_counter() - t
            assert len(page.items) == 50 and page.items[0].name == "cmdb4-ci-050000"
            _timed("sort name offset 50000", elapsed, 2.0)

            t = time.perf_counter()
            refs = await lookup_cis(s, acc, "CMDB4SRV-0099")
            elapsed = time.perf_counter() - t
            assert refs and all(r.number.startswith("CMDB4SRV-0099") for r in refs)
            _timed("lookup", elapsed, 1.0)

            some_ci = BASE_ID + 10  # 10 % of the CIs have three versions
            t = time.perf_counter()
            vp = await CiService(s).list_versions(
                acc, some_ci, before_id=None, limit=50, include_archived=False
            )
            elapsed = time.perf_counter() - t
            assert len(vp.items) == 3
            _timed("list_versions", elapsed, 0.2)

        # EXPLAIN of the two index-driven paths (recorded in the task report)
        plans = {
            "q prefix": _explain(
                cmdb_db,
                "SELECT ci_id FROM tiqora_cmdb_ci_attr WHERE value_key LIKE '10.0.1.77%'",
            ),
            "attr eq": _explain(
                cmdb_db,
                "SELECT ci_id FROM tiqora_cmdb_ci_attr"
                " WHERE attr_path = 'SerialNumber' AND value_key = 'sn-0099999'",
            ),
            "sort/offset": _explain(
                cmdb_db,
                f"SELECT id FROM tiqora_cmdb_ci WHERE class_id = {cid}"
                " ORDER BY name, id LIMIT 50 OFFSET 50000",
            ),
        }
        for label, plan in plans.items():
            print(f"EXPLAIN {cmdb_db.dialect} {label}:\n{plan}")
    finally:
        engine = create_engine(cmdb_db.sync_url)
        try:
            while True:  # chunked cascade delete: one huge DELETE would hold the locks too long
                with engine.begin() as conn:
                    ids = (
                        conn.execute(
                            select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.class_id == cid).limit(CHUNK)
                        )
                        .scalars()
                        .all()
                    )
                    if not ids:
                        break
                    for model in (TiqoraCmdbCiAttr, TiqoraCmdbCiVersion):
                        conn.execute(delete(model).where(model.ci_id.in_(ids)))
                    conn.execute(delete(TiqoraCmdbCi).where(TiqoraCmdbCi.id.in_(ids)))
            with engine.begin() as conn:
                left = conn.execute(select(func.count()).select_from(TiqoraCmdbCi)).scalar_one()
                assert left == 0
        finally:
            engine.dispose()
