"""Fixture plugin (filled in Task 3)."""

from __future__ import annotations

from typing import Any


class HelloPlugin:
    def register(self, registry: Any) -> None:
        return None


plugin = HelloPlugin()
