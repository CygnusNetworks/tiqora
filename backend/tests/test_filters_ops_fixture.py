"""The frontend copy of ``OPS_BY_TYPE`` must not drift from the backend one (R-OPSPIN)."""

from __future__ import annotations

import json
from pathlib import Path

from tiqora.filters import OPS_BY_TYPE

FIXTURE = (
    Path(__file__).resolve().parents[2] / "frontend/src/components/filters/opsByType.fixture.json"
)


def test_frontend_ops_fixture_matches_backend() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture == {t: sorted(ops) for t, ops in OPS_BY_TYPE.items()}
