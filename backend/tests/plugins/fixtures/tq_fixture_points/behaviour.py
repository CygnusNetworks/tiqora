"""Test switches and call log of the points fixture (module state; ``reset()`` between tests)."""

from __future__ import annotations

#: e.g. MODE["hook.ticket.create"] = "veto" | "hang" | "raise" | "enrich" | "ok"
MODE: dict[str, str] = {}
CALLS: list[tuple[str, object]] = []


def reset() -> None:
    MODE.clear()
    CALLS.clear()
