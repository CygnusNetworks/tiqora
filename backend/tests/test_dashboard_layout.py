import pytest
from pydantic import ValidationError

from tiqora.dashboard.layout import Rect, normalize_rects
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
