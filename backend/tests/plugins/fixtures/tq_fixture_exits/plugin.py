"""Fixture plugin that tries to exit/interrupt the process (TQ_EXIT_MODE)."""

from __future__ import annotations

import os
import sys

from tiqora_sdk import PluginRegistry

MODE = os.environ.get("TQ_EXIT_MODE", "")
if MODE == "import-exit":
    sys.exit(3)
if MODE == "import-kbd":
    raise KeyboardInterrupt


class ExitsPlugin:
    def register(self, registry: PluginRegistry) -> None:
        if MODE == "register-exit":
            sys.exit(4)
        if MODE == "register-kbd":
            raise KeyboardInterrupt


plugin = ExitsPlugin()
