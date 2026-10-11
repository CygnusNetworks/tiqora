"""Every webchat entry point imports on its own, in a fresh interpreter.

In one pytest process an earlier test module (e.g. one importing ``tiqora.api.app``) loads
everything into ``sys.modules`` and hides an import cycle; the worker process starts from
``tiqora.worker.__main__`` and would crash-loop. Each import here runs in a subprocess.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

MODULES = [
    "tiqora.worker.__main__",
    "tiqora.webchat.hub",
    "tiqora.webchat.notify",
    "tiqora.webchat.sessions",
    "tiqora.webchat.identity",
    "tiqora.webchat.visitor_identity",
    "tiqora.webchat.agent",
    "tiqora.webchat.stream",
    "tiqora.webchat.public_api",
    "tiqora.api.v1.chat",
    "tiqora.api.v1.tickets",
    "tiqora.domain.ticket_write_service",
    "tiqora.main",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_in_a_fresh_interpreter(module: str) -> None:
    result = subprocess.run(  # noqa: S603 - fixed module names, our own interpreter
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
