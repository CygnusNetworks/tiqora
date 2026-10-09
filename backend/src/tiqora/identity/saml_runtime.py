"""Runtime probe for the optional SAML stack (pysaml2 + xmlsec1 binary).

pysaml2 is imported lazily so the app boots without the ``saml`` extra.
"""

from __future__ import annotations

import functools
import importlib.util
import shutil
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class SamlAvailability:
    available: bool
    missing: Literal["package", "xmlsec1"] | None
    xmlsec_binary: str | None


class SamlUnavailable(Exception):
    """pysaml2 (or the xmlsec1 binary) is not available in this process."""


def _package_present() -> bool:
    return importlib.util.find_spec("saml2") is not None


@functools.lru_cache(maxsize=1)
def saml_availability() -> SamlAvailability:
    if not _package_present():
        return SamlAvailability(False, "package", None)
    binary = shutil.which("xmlsec1")
    if binary is None:
        return SamlAvailability(False, "xmlsec1", None)
    return SamlAvailability(True, None, binary)


def import_saml2() -> Any:
    if not _package_present():
        raise SamlUnavailable(
            "pysaml2 is not installed; install the 'saml' extra (uv sync --extra saml)"
        )
    import saml2  # noqa: PLC0415 — optional dependency

    return saml2
