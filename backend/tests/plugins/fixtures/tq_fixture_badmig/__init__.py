"""Fixture plugin ``org.example.badmig``: its only migration touches a Znuny table."""

from __future__ import annotations

from tiqora_sdk import PluginRegistry


class BadMigPlugin:
    def register(self, registry: PluginRegistry) -> None:
        return None


plugin = BadMigPlugin()
