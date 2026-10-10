"""Built-in default template: reproduces the classic home page (D5)."""

from __future__ import annotations

import uuid
from typing import Any, Final

from tiqora.dashboard.layout import Rect
from tiqora.dashboard.schemas import WidgetIn, WidgetLayouts

DEFAULT_TEMPLATE_KEY: Final = "core.default"
DEFAULT_TEMPLATE_VERSION: Final = 1


def _preset(name: str) -> dict[str, Any]:
    return {"kind": "preset", "preset": name}


def _w(
    type_key: str,
    rect: tuple[int, int, int, int],
    settings: dict[str, Any],
    title_override: str | None = None,
) -> WidgetIn:
    x, y, w, h = rect
    return WidgetIn(
        id=uuid.uuid4().hex,
        type_key=type_key,
        type_version=1,
        title_override=title_override,
        settings=settings,
        layout=WidgetLayouts(lg=Rect(x=x, y=y, w=w, h=h)),
    )


def default_widgets() -> list[WidgetIn]:
    """The eleven default widgets (fresh ids per call), in phone order."""
    return [
        _w("core.counter", (0, 0, 3, 2), {"source": _preset("mine_open")}),
        _w("core.counter", (3, 0, 3, 2), {"source": _preset("mine_new")}),
        _w("core.counter", (6, 0, 3, 2), {"source": _preset("unowned_new")}),
        _w("core.counter", (9, 0, 3, 2), {"source": _preset("escalated"), "tone": "danger"}),
        _w("core.escalations", (0, 2, 12, 3), {"limit": 8, "within_hours": 0}),
        _w(
            "core.ticket-list",
            (0, 5, 6, 5),
            {"source": _preset("mine_new"), "sort": "age", "order": "asc", "limit": 8},
        ),
        _w(
            "core.ticket-list",
            (6, 5, 6, 5),
            {"source": _preset("mine_open"), "sort": "age", "order": "desc", "limit": 8},
        ),
        _w(
            "core.ticket-list",
            (0, 10, 6, 5),
            {"source": _preset("unowned_new"), "sort": "age", "order": "asc", "limit": 8},
            title_override="Team-Eingang (zum Übernehmen)",
        ),
        _w("core.reminders", (6, 10, 6, 5), {"window_hours": 24, "limit": 6}),
        _w("core.recent-views", (0, 15, 12, 2), {"limit": 8}),
        _w("core.queue-overview", (0, 17, 12, 3), {"limit": 8, "only_with_work": True}),
    ]
