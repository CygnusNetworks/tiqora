"""Test switches and call log of the points fixture (module state; ``reset()`` between tests)."""

from __future__ import annotations

#: e.g. MODE["hook.ticket.create"] = "veto" | "hang" | "raise" | "enrich" | "ok"
MODE: dict[str, str] = {}
CALLS: list[tuple[str, object]] = []
#: ticket ids that mode "veto_listed" refuses (every other ticket passes)
VETO_TICKETS: set[int] = set()


def reset() -> None:
    MODE.clear()
    CALLS.clear()
    VETO_TICKETS.clear()
