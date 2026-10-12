"""Priority matrix (Task 16): lookup vectors, Mode A reader, Tiqora table, import replace.

Seed band 941100-941199 (nothing is committed: sessions are rolled back; the mode row is
removed by ``prepare``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from tests._itsm_adapter_helpers import itsm_session
from tests._itsm_db import ITSM_PARAMS
from tests._itsm_mode_helpers import itsm_url, profile_packages, seed_packages, store
from tiqora.itsm.ci_provider import CiReadOnly
from tiqora.itsm.mode import SETTING_MODE, ItsmModeState, get_itsm_mode
from tiqora.itsm.models import TiqoraItsmPriorityMatrix
from tiqora.itsm.priority import (
    InvalidMatrix,
    MatrixCell,
    changed_matrix_fields,
    load_matrix,
    lookup,
    replace_matrix_from_import,
    save_matrix,
    suggest_priority,
)
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache, detect_znuny_itsm

BEHAVIOUR = Path(__file__).resolve().parent / "fixtures" / "znuny-itsm" / "behaviour"
LEVELS = ("1 very low", "2 low", "3 normal", "4 high", "5 very high")
USER = 941100


def _vector_cells(profile: str) -> list[MatrixCell]:
    doc: dict[str, Any] = json.loads((BEHAVIOUR / f"{profile}.json").read_text(encoding="utf-8"))
    return [MatrixCell(**c) for c in doc["questions"]["1"]["expected"]["cells"]]


@pytest.fixture(params=("itsm-6", "itsm-7"))
def profile(request: pytest.FixtureRequest) -> str:
    return str(request.param)


# --- pure --------------------------------------------------------------------------------------


def test_lookup_vectors(profile: str) -> None:
    """Task 3 question 1: all 25 cells, exact keys; the corners pin the shape of the grid."""
    cells = _vector_cells(profile)
    assert len(cells) == 25
    assert {(c.criticality, c.impact) for c in cells} == {(c, i) for c in LEVELS for i in LEVELS}
    for cell in cells:
        assert lookup(cells, cell.criticality, cell.impact) == cell.priority_id
    assert lookup(cells, "1 very low", "1 very low") == 1
    assert lookup(cells, "3 normal", "3 normal") == 3
    assert lookup(cells, "5 very high", "5 very high") == 5


def test_lookup_is_exact_and_has_no_fallback(profile: str) -> None:
    cells = _vector_cells(profile)
    assert lookup(cells, "3 NORMAL", "3 normal") is None  # case sensitive like MariaDB's compare
    assert lookup(cells, "3 normal", "") is None
    assert lookup(cells, None, "3 normal") is None
    assert lookup(cells, "3 normal", None) is None
    assert lookup(cells[:3], "5 very high", "5 very high") is None  # missing cell: no neighbour
    assert lookup([], "3 normal", "3 normal") is None


def test_changed_matrix_fields_ignores_equal_values() -> None:
    before = {"ITSMImpact": ["3 normal"], "ITSMCriticality": []}
    submitted = {"ITSMImpact": ["3 normal"], "ITSMCriticality": ["4 high"], "Other": ["x"]}
    assert changed_matrix_fields(before, submitted) == {"ITSMCriticality": ["4 high"]}
    assert changed_matrix_fields({"ITSMImpact": []}, {"ITSMImpact": []}) == {}  # both empty
    assert changed_matrix_fields({}, submitted) == {}  # field not part of the snapshot


# --- Mode A reads cip_allocate -------------------------------------------------------------------


async def _mode_a(session: Any) -> ItsmModeState:
    return ItsmModeState(
        mode="znuny_readthrough",
        source="admin",
        stored=True,
        detected=await detect_znuny_itsm(session),
        changed_at=None,
        changed_by=None,
        import_run_id=None,
    )


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_mode_a_matrix_equals_cip_allocate(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    url = itsm_url(request, dialect, profile)
    seed_packages(url, profile_packages(profile))
    try:
        async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
            mode = await _mode_a(session)
            view = await load_matrix(session, mode, ItsmSettings())
            assert view.source == "znuny" and view.editable is False
            assert sorted((c.criticality, c.impact, c.priority_id) for c in view.cells) == sorted(
                (c.criticality, c.impact, c.priority_id) for c in _vector_cells(profile)
            )
            # the dropdown keys of the two fields, in the field's own order
            assert view.criticality_values == LEVELS and view.impact_values == LEVELS
            assert await suggest_priority(
                session, mode, ItsmSettings(), criticality="4 high", impact="3 normal"
            ) == lookup(list(view.cells), "4 high", "3 normal")
    finally:
        clear_detect_cache()


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_mode_a_save_is_read_only(dialect: str, request: pytest.FixtureRequest) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    store(url, SETTING_MODE, "znuny_readthrough")
    try:
        async with itsm_session(request, dialect, "itsm-6") as (session, _reader, _engine):
            with pytest.raises(CiReadOnly):
                await save_matrix(session, [MatrixCell("3 normal", "3 normal", 3)], user_id=USER)
            rows = (await session.execute(select(TiqoraItsmPriorityMatrix))).all()
            assert rows == []
    finally:
        clear_detect_cache()


# --- Tiqora's own matrix -------------------------------------------------------------------------


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_tiqora_matrix_replace_validates_and_suggests(
    dialect: str, request: pytest.FixtureRequest
) -> None:
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    store(url, SETTING_MODE, "tiqora")
    try:
        async with itsm_session(request, dialect, "itsm-6") as (session, _reader, _engine):
            mode = await get_itsm_mode(session)
            assert mode.mode == "tiqora"
            first = [MatrixCell("3 normal", "4 high", 4), MatrixCell("1 very low", "1 very low", 1)]
            await save_matrix(session, first, user_id=USER)
            view = await load_matrix(session, mode, ItsmSettings())
            assert view.source == "tiqora" and view.editable is True
            assert {(c.criticality, c.impact, c.priority_id) for c in view.cells} == {
                ("3 normal", "4 high", 4),
                ("1 very low", "1 very low", 1),
            }
            assert view.criticality_values[:5] == LEVELS  # DF order, then keys only the cells use
            cfg = ItsmSettings()
            assert (
                await suggest_priority(session, mode, cfg, criticality="3 normal", impact="4 high")
                == 4
            )
            assert (
                await suggest_priority(session, mode, cfg, criticality="2 low", impact="4 high")
                is None
            )
            off = ItsmSettings(priority_matrix_enabled=False)
            assert (
                await suggest_priority(session, mode, off, criticality="3 normal", impact="4 high")
                is None
            )
            # a second save replaces everything
            await save_matrix(session, [MatrixCell("5 very high", "5 very high", 5)], user_id=USER)
            again = await load_matrix(session, mode, ItsmSettings())
            assert [(c.criticality, c.impact, c.priority_id) for c in again.cells] == [
                ("5 very high", "5 very high", 5)
            ]
            for bad, reason in (
                ([MatrixCell("a", "b", 1), MatrixCell("a", "b", 2)], "duplicate_cell"),
                ([MatrixCell("a", "b", 940099)], "unknown_priority"),
                ([MatrixCell(" ", "b", 1)], "empty_key"),
            ):
                with pytest.raises(InvalidMatrix) as err:
                    await save_matrix(session, bad, user_id=USER)
                assert err.value.reason == reason
            # a rejected save changed nothing
            still = await load_matrix(session, mode, ItsmSettings())
            assert len(still.cells) == 1
    finally:
        clear_detect_cache()


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
async def test_replace_matrix_from_import(dialect: str, request: pytest.FixtureRequest) -> None:
    """The importer copies ``cip_allocate`` into the Tiqora table; Mode A would refuse a save."""
    url = itsm_url(request, dialect, "itsm-6")
    seed_packages(url, profile_packages("itsm-6"))
    store(url, SETTING_MODE, "znuny_readthrough")
    try:
        async with itsm_session(request, dialect, "itsm-6") as (session, reader, _engine):
            cells = [
                MatrixCell(r.criticality, r.impact, r.priority_id)
                for r in await reader.cip_allocate(session)
            ]
            assert len(cells) == 25
            await replace_matrix_from_import(session, cells, user_id=USER)
            await replace_matrix_from_import(session, cells, user_id=USER)  # idempotent
            rows = (
                await session.execute(
                    select(
                        TiqoraItsmPriorityMatrix.criticality,
                        TiqoraItsmPriorityMatrix.impact,
                        TiqoraItsmPriorityMatrix.priority_id,
                    )
                )
            ).all()
            assert sorted(tuple(r) for r in rows) == sorted(
                (c.criticality, c.impact, c.priority_id) for c in cells
            )
    finally:
        clear_detect_cache()
