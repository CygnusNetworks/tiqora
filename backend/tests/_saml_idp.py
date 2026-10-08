"""Shared helpers for SAML tests (Task 9 adds the test IdP here)."""

from __future__ import annotations

import importlib.util
import os
import shutil

import pytest


def require_saml() -> None:
    """Skip (or fail under TIQORA_REQUIRE_SAML_TESTS=1) when pysaml2/xmlsec1 are missing."""
    msg = None
    if importlib.util.find_spec("saml2") is None:
        msg = "pysaml2 missing (uv sync --extra saml)"
    elif shutil.which("xmlsec1") is None:
        msg = "xmlsec1 binary missing (brew install libxmlsec1 / apt install xmlsec1)"
    if msg is None:
        return
    if os.environ.get("TIQORA_REQUIRE_SAML_TESTS") == "1":
        pytest.fail(msg, pytrace=False)
    pytest.skip(msg, allow_module_level=True)
