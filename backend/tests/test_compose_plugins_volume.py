"""Compose examples are ready for runtime plugin installs (pure, no Docker)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FILES = [
    "docker-compose.example.yml",
    "docker-compose.quickstart.yml",
    "docker-compose.local.yml",
]
ROLES = {"api", "worker", "ai-worker", "mcp"}
MOUNT = "tiqora_plugins:/var/lib/tiqora/plugins"


def _services(name: str) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    return {
        svc: cfg
        for svc, cfg in data["services"].items()
        if (cfg.get("command") or [None])[0] in ROLES
    }


@pytest.mark.parametrize("name", FILES)
def test_role_services_mount_plugins_volume_and_restart(name: str) -> None:
    services = _services(name)
    assert "tiqora-api" in services and "tiqora-worker" in services
    for svc, cfg in services.items():
        assert MOUNT in (cfg.get("volumes") or []), f"{name}:{svc} lacks {MOUNT}"
        assert cfg.get("restart") in {"unless-stopped", "always"}, f"{name}:{svc} restart"
    top = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    assert "tiqora_plugins" in top["volumes"]


@pytest.mark.parametrize("name", FILES)
def test_followers_wait_for_healthy_api(name: str) -> None:
    services = _services(name)
    followers = {
        s: c for s, c in services.items() if c["command"][0] in {"worker", "ai-worker", "mcp"}
    }
    assert followers
    for svc, cfg in followers.items():
        dep = cfg.get("depends_on")
        assert isinstance(dep, dict), f"{name}:{svc} depends_on must be the long form"
        assert dep["tiqora-api"]["condition"] == "service_healthy", f"{name}:{svc}"


def test_dockerfile_creates_plugin_dirs_without_volume() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.replace("\\\n", " ").splitlines()]
    assert not any(ln.startswith("VOLUME") for ln in lines)
    run = next(
        i for i, ln in enumerate(lines) if ln.startswith("RUN mkdir -p /var/lib/tiqora/plugins")
    )
    assert "/var/lib/tiqora/plugin-repository" in lines[run]
    assert "chown -R tiqora:tiqora /var/lib/tiqora" in lines[run]
    assert run < lines.index("USER tiqora")
