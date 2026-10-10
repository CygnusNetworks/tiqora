"""Pure grid layout helpers (no I/O)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from tiqora.dashboard.settings_models import SettingsError

GRID_COLS: Final = 12
MAX_ROWS: Final = 1000


class Rect(BaseModel):
    """A widget rectangle in grid cells (12 columns on ``lg``)."""

    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0, le=11)
    y: int = Field(ge=0, le=999)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=1, le=24)


def normalize_rects(items: Sequence[tuple[str, Rect]], *, cols: int = GRID_COLS) -> dict[str, Rect]:
    """Clamp every rect to ``cols`` columns and resolve overlaps.

    Items are placed in ``(y, x, id)`` order; a rect overlapping an already placed one
    is pushed down to the first free ``y`` below it (``x``/``w``/``h`` never change
    beyond the clamp). The result does not depend on the input order and is idempotent.
    Touching edges do not collide. Computed in plain ints; ``Rect`` is built last.
    """
    clamped: list[tuple[str, int, int, int, int]] = []
    for wid, r in items:
        w = max(1, min(r.w, cols))
        x = max(0, min(r.x, cols - w))
        clamped.append((wid, x, r.y, w, max(1, r.h)))
    clamped.sort(key=lambda t: (t[2], t[1], t[0]))

    placed: list[tuple[int, int, int, int]] = []  # x, y, w, h
    out: dict[str, tuple[int, int, int, int]] = {}
    for wid, x, y, w, h in clamped:
        moved = True
        while moved:
            moved = False
            for px, py, pw, ph in placed:
                if x < px + pw and px < x + w and y < py + ph and py < y + h:
                    y = py + ph
                    moved = True
        placed.append((x, y, w, h))
        out[wid] = (x, y, w, h)
        if y + h > MAX_ROWS:
            raise SettingsError("Das Layout ist zu hoch.", path="widgets")
    return {wid: Rect(x=x, y=y, w=w, h=h) for wid, (x, y, w, h) in out.items()}
