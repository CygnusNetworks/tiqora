"""Fixture plugin ``org.example.points``: one ``_register_<point>`` function per extension point."""

from __future__ import annotations

from tiqora_sdk import PluginRegistry


class PointsPlugin:
    def register(self, registry: PluginRegistry) -> None:
        # Each task of the plugin-points plan adds its ``_register_<point>(registry)`` call here.
        return None


plugin = PointsPlugin()
