import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tiqora.dashboard.layout import Rect, first_free_spot, normalize_rects
from tiqora.dashboard.settings_models import SettingsError


def test_layout_is_clamped_and_overlaps_resolved() -> None:
    out = normalize_rects(
        [
            ("a", Rect(x=10, y=0, w=6, h=2)),  # x + w = 16 -> x becomes 6
            ("b", Rect(x=6, y=0, w=6, h=2)),  # now collides with a at (6,0): pushed below
            ("c", Rect(x=0, y=0, w=3, h=1)),
        ]
    )
    assert out["a"] == Rect(x=6, y=0, w=6, h=2)
    assert out["b"] == Rect(x=6, y=2, w=6, h=2)
    assert out["c"] == Rect(x=0, y=0, w=3, h=1)


def test_normalize_is_deterministic_and_idempotent() -> None:
    items = [("b", Rect(x=0, y=0, w=4, h=2)), ("a", Rect(x=0, y=0, w=4, h=2))]
    first = normalize_rects(items)
    assert normalize_rects(list(reversed(items))) == first
    assert normalize_rects(list(first.items())) == first
    assert first["a"].y == 0 and first["b"].y == 2


def test_touching_rects_do_not_collide() -> None:
    out = normalize_rects(
        [
            ("a", Rect(x=0, y=0, w=6, h=2)),
            ("b", Rect(x=6, y=0, w=6, h=2)),
            ("c", Rect(x=0, y=2, w=12, h=1)),
        ]
    )
    assert out["b"].y == 0 and out["c"].y == 2


def test_zero_and_oversized_sizes_are_rejected_by_the_model() -> None:
    with pytest.raises(ValidationError):
        Rect(x=0, y=0, w=0, h=1)
    with pytest.raises(ValidationError):
        Rect(x=0, y=0, w=13, h=1)
    with pytest.raises(ValidationError):
        Rect(x=12, y=0, w=1, h=1)


def test_cols_8_clamps_x_and_w() -> None:
    out = normalize_rects(
        [("a", Rect(x=6, y=0, w=6, h=1)), ("b", Rect(x=0, y=3, w=12, h=1))], cols=8
    )
    assert out["a"] == Rect(x=2, y=0, w=6, h=1)
    assert out["b"] == Rect(x=0, y=3, w=8, h=1)


def test_push_down_beyond_the_row_limit_is_a_settings_error() -> None:
    items = [(f"w{i:02d}", Rect(x=0, y=999, w=12, h=24)) for i in range(40)]
    with pytest.raises(SettingsError) as e:
        normalize_rects(items)
    assert e.value.message == "Das Layout ist zu hoch."
    assert e.value.path == "widgets"


def test_chain_of_overlaps_stacks_without_gaps() -> None:
    out = normalize_rects([(c, Rect(x=0, y=0, w=12, h=2)) for c in "abc"])
    assert [out[c].y for c in "abc"] == [0, 2, 4]


_CASES_FILE = (
    Path(__file__).resolve().parents[2] / "frontend/src/dashboard/layout/normalizeCases.json"
)
_CASES = json.loads(_CASES_FILE.read_text())["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_matches_the_frontend_cross_check_fixture(case: dict[str, Any]) -> None:
    """Drift guard: the frontend engine (grid.ts normalizeRects) runs the same cases."""
    raw = case["input"]
    if isinstance(raw, dict):
        pairs = [(f"w{i:02d}", raw["rect"]) for i in range(raw["repeat"])]
    else:
        pairs = [(wid, r) for wid, r in raw]
    items = [(wid, Rect(x=r[0], y=r[1], w=r[2], h=r[3])) for wid, r in pairs]
    if "error" in case:
        with pytest.raises(SettingsError) as e:
            normalize_rects(items, cols=case["cols"])
        assert e.value.message == case["error"]
        return
    out = normalize_rects(items, cols=case["cols"])
    assert {k: [v.x, v.y, v.w, v.h] for k, v in out.items()} == case["expected"]


_SPOT_CASES_FILE = (
    Path(__file__).resolve().parents[2] / "frontend/src/dashboard/layout/firstFreeSpotCases.json"
)
_SPOT_CASES = json.loads(_SPOT_CASES_FILE.read_text())["cases"]


@pytest.mark.parametrize("case", _SPOT_CASES, ids=[c["name"] for c in _SPOT_CASES])
def test_first_free_spot_matches_shared_cases(case: dict[str, Any]) -> None:
    """Drift guard: the frontend firstFreeSpot (grid.ts) runs the same cases."""
    rects = [Rect(x=r[0], y=r[1], w=r[2], h=r[3]) for r in case["rects"]]
    spot = first_free_spot(rects, case["w"], case["h"], cols=case["cols"])
    assert list(spot) == case["expected"]


def test_first_free_spot_terminates_below_a_dense_grid() -> None:
    rects = [Rect(x=0, y=i, w=12, h=1) for i in range(20)]
    assert first_free_spot(rects, 12, 3) == (0, 20)
