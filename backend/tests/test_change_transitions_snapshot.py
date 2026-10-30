"""The e2e change store reads its phase table from a committed JSON snapshot; this pins it.

Regenerate after changing ``TRANSITIONS`` / ``SYSTEM_ONLY``::

    cd backend && uv run python tests/test_change_transitions_snapshot.py \
        >| ../frontend/e2e/fixtures/change-transitions.json
"""

from __future__ import annotations

import json
from pathlib import Path

from tiqora.itsm.change.phases import SYSTEM_ONLY, TRANSITIONS

SNAPSHOT = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "e2e"
    / "fixtures"
    / "change-transitions.json"
)
REGENERATE = (
    "cd backend && uv run python tests/test_change_transitions_snapshot.py "
    ">| ../frontend/e2e/fixtures/change-transitions.json"
)


def current() -> dict[str, object]:
    return {
        "transitions": {f"{a}>{b}": sorted(types) for (a, b), types in sorted(TRANSITIONS.items())},
        "system_only": sorted(f"{a}>{b}" for a, b in SYSTEM_ONLY),
    }


def test_frontend_transition_snapshot_matches_phases() -> None:
    stored = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert stored == current(), (
        "frontend/e2e/fixtures/change-transitions.json differs from itsm/change/phases.py "
        f"(TRANSITIONS / SYSTEM_ONLY). Regenerate it with: {REGENERATE}"
    )


if __name__ == "__main__":
    print(json.dumps(current(), indent=2))
