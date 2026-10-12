"""Search index of the current CI version: row building (pure) and rebuild (both dialects)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from tests import _cmdb_helpers
from tests._cmdb_helpers import ATTRS, CmdbWorld, make_ci, make_class, parse_attrs
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.index import build_index_rows, rebuild_index
from tiqora.db.tiqora.models import TiqoraCmdbCiAttr

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


def test_build_index_rows_only_searchable_with_positions() -> None:
    rows = build_index_rows(
        ATTRS,
        {
            "NIC": [
                {
                    "v": "eth0",
                    "s": {"IPAddress": [{"v": "192.0.2.1"}, {"v": "192.0.2.2"}]},
                }
            ],
            "Model": [{"v": "M1"}],
            "Vendor": [{"v": "Ä Corp"}],
        },
    )
    assert [(r.attr_path, r.position, r.value_key) for r in rows] == [
        ("NIC.IPAddress", 1, "192.0.2.1"),
        ("NIC.IPAddress", 2, "192.0.2.2"),
        ("Vendor", 1, "ä corp"),
    ]


def test_positions_run_across_parent_entries_and_text_is_cut() -> None:
    long = "X" * 600
    rows = build_index_rows(
        ATTRS,
        {
            "NIC": [
                {"v": "eth0", "s": {"IPAddress": [{"v": "192.0.2.1"}]}},
                {"v": "eth1", "s": {"IPAddress": [{"v": "192.0.2.9"}]}},
            ],
            "Vendor": [{"v": long}],
        },
    )
    by = [(r.attr_path, r.position) for r in rows]
    assert by == [("NIC.IPAddress", 1), ("NIC.IPAddress", 2), ("Vendor", 1)]
    vendor = rows[-1]
    assert len(vendor.value_text) == 500
    assert len(vendor.value_key) == 191


def test_typed_columns_for_numbers_and_dates() -> None:
    attrs = parse_attrs(
        [
            _a("CPU", "integer"),
            _a("RAM", "decimal"),
            _a("Purchase", "date"),
            _a("Seen", "datetime"),
            _a("Owner", "customer_user"),
            _a("Peer", "ci_ref"),
        ]
    )
    rows = {
        r.attr_path: r
        for r in build_index_rows(
            attrs,
            {
                "CPU": [{"v": 8}],
                "RAM": [{"v": "16.5"}],
                "Purchase": [{"v": "2026-03-04"}],
                "Seen": [{"v": "2026-03-04T05:06:07"}],
                "Owner": [{"v": "Alice@Example.org"}],
                "Peer": [{"v": 742010}],
            },
        )
    }
    assert rows["CPU"].value_num == Decimal(8)
    assert rows["RAM"].value_num == Decimal("16.5")
    assert rows["Purchase"].value_date == datetime(2026, 3, 4)
    assert rows["Seen"].value_date == datetime(2026, 3, 4, 5, 6, 7)
    assert rows["Owner"].value_num is None and rows["Owner"].value_key == "alice@example.org"
    assert rows["Peer"].value_num == Decimal(742010) and rows["Peer"].value_text == "742010"


def test_values_too_large_for_the_numeric_column_are_not_indexed_numerically() -> None:
    attrs = parse_attrs([_a("RAM", "decimal")])
    (row,) = build_index_rows(attrs, {"RAM": [{"v": "1" + "0" * 27}]})
    assert row.value_num is None and row.value_key == "1" + "0" * 27


def _a(key: str, type_: str) -> dict[str, Any]:
    return {
        "key": key,
        "name": key,
        "type": type_,
        "searchable": True,
        "count_max": 1,
        "count_default": 1,
    }


async def _snapshot(session: Any, ci_id: int) -> list[tuple[Any, ...]]:
    rows = await session.execute(
        select(
            TiqoraCmdbCiAttr.attr_path,
            TiqoraCmdbCiAttr.position,
            TiqoraCmdbCiAttr.value_text,
            TiqoraCmdbCiAttr.value_key,
            TiqoraCmdbCiAttr.value_num,
            TiqoraCmdbCiAttr.value_date,
        )
        .where(TiqoraCmdbCiAttr.ci_id == ci_id)
        .order_by(TiqoraCmdbCiAttr.attr_path, TiqoraCmdbCiAttr.position)
    )
    return [tuple(r) for r in rows.all()]


@pytest.mark.db
async def test_index_matches_rebuild(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(
            s,
            await make_class(s),
            "cmdb4-a",
            attributes={"Vendor": [{"v": "X"}], "SerialNumber": [{"v": "SN-1"}]},
        )
        await s.commit()
        before = await _snapshot(s, ci_id)
        assert [r[:2] for r in before] == [("SerialNumber", 1), ("Vendor", 1)]
        await rebuild_index(s, [ci_id])
        await s.commit()
        assert await _snapshot(s, ci_id) == before


@pytest.mark.db
async def test_rebuild_uses_the_current_definition(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        ci_id = await make_ci(s, cid, "cmdb4-a", attributes={"Model": [{"v": "M1"}]})
        assert await _snapshot(s, ci_id) == []  # Model is not searchable in definition 1
        defs = _server_with_searchable_model()
        await ClassService(s).add_definition(
            cid, parse_attrs(defs), comment=None, user_id=_cmdb_helpers.USER_ADMIN
        )
        await s.commit()
        assert await rebuild_index(s, [ci_id, 999999999]) == 1  # unknown id is skipped
        await s.commit()
        assert [(r[0], r[3]) for r in await _snapshot(s, ci_id)] == [("Model", "m1")]


def _server_with_searchable_model() -> list[dict[str, Any]]:
    out = [dict(a) for a in _cmdb_helpers.SERVER_ATTRS]
    for a in out:
        if a["key"] == "Model":
            a["searchable"] = True
    return out
