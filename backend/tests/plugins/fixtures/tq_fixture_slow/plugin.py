"""Fixture plugin whose import blocks for TQ_SLOW_SECONDS."""

from __future__ import annotations

import os
import time

from tiqora_sdk import PluginRegistry

time.sleep(float(os.environ.get("TQ_SLOW_SECONDS", "3")))


class SlowPlugin:
    def register(self, registry: PluginRegistry) -> None:
        return None


plugin = SlowPlugin()
