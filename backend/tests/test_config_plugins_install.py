"""Settings of the runtime plugin install (pure, no database)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tiqora.config import Settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert (s.plugins_dir, s.plugins_runtime_install, s.plugins_allow_unsigned) == (
        "/var/lib/tiqora/plugins",
        True,
        False,
    )
    assert s.plugins_trusted_keys == ""
    # F11: 15 MB default (the database packet limit is the real cap on MariaDB)
    assert s.plugins_max_package_mb == 15
    assert s.plugins_max_package_bytes == 15 * 1024 * 1024
    assert s.plugins_coordinated_restart is True
    assert s.plugins_restart_timeout_seconds == 180
    assert s.plugin_repositories == ""
    assert s.plugin_repository_file_root == "/var/lib/tiqora/plugin-repository"
    assert s.plugin_repositories_interval_seconds == 21600


def test_package_cap_follows_the_env_and_never_exceeds_upload_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIQORA_PLUGINS_MAX_PACKAGE_MB", "25")
    assert Settings(_env_file=None).plugins_max_package_bytes == 25 * 1024 * 1024
    monkeypatch.setenv("TIQORA_PLUGINS_MAX_PACKAGE_MB", "26")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TIQORA_PLUGINS_RESTART_TIMEOUT_SECONDS", "29"),
        ("TIQORA_PLUGINS_RESTART_TIMEOUT_SECONDS", "1801"),
        ("TIQORA_PLUGIN_REPOSITORIES_INTERVAL_SECONDS", "899"),
        ("TIQORA_PLUGINS_MAX_PACKAGE_MB", "0"),
    ],
)
def test_out_of_range_values_are_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIQORA_PLUGINS_DIR", "/tmp/plugins.test")
    monkeypatch.setenv("TIQORA_PLUGINS_RUNTIME_INSTALL", "false")
    monkeypatch.setenv("TIQORA_PLUGINS_ALLOW_UNSIGNED", "true")
    s = Settings(_env_file=None)
    assert s.plugins_dir == "/tmp/plugins.test"
    assert s.plugins_runtime_install is False
    assert s.plugins_allow_unsigned is True
