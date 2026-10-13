from __future__ import annotations

from pathlib import Path

import pytest

from tests._plugin_points import boot_points_plugin, points_behaviour
from tiqora.plugins.state import RuntimeState, loaded_plugins


def test_points_fixture_boots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    assert registry.frozen
    assert loaded_plugins()["org.example.points"].state == RuntimeState.ACTIVE
    assert points_behaviour().CALLS == []


def test_points_fixture_state_is_reset_between_tests() -> None:
    assert "org.example.points" not in loaded_plugins()
