"""Kerberos/SPNEGO ("Negotiate") authentication.

The ``gssapi`` package is a *sync*, C-extension-backed library, so every
call into it is dispatched via ``run_in_executor``. Import is indirected
through :func:`_import_gssapi` so tests can substitute a fake module instead
of requiring a real KDC/keytab.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from tiqora.config import Settings


class SpnegoUnavailable(Exception):
    """Raised when gssapi is not installed, or negotiation cannot complete."""


def _import_gssapi() -> Any:
    try:
        import gssapi
    except ImportError as exc:
        raise SpnegoUnavailable(
            "gssapi is not installed; install the 'kerberos' extra "
            "(uv sync --extra kerberos) to enable SPNEGO auth"
        ) from exc
    return gssapi


class SpnegoService:
    """Accepts a client's Negotiate token and returns the Kerberos principal."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _accept_sync(self, token: bytes) -> str:
        gssapi = _import_gssapi()
        if self._settings.krb5_ktname:
            os.environ["KRB5_KTNAME"] = self._settings.krb5_ktname
        server_creds = gssapi.Credentials(usage="accept")
        ctx = gssapi.SecurityContext(creds=server_creds, usage="accept")
        ctx.step(token)
        if not ctx.complete:
            raise SpnegoUnavailable("multi-leg SPNEGO negotiation is not supported")
        return str(ctx.initiator_name)

    async def accept(self, token: bytes) -> str:
        """Return the full Kerberos principal (``user@REALM``) for *token*."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._accept_sync, token)


def principal_to_login(principal: str) -> str:
    """Map a Kerberos principal's primary part to ``users.login``."""
    return principal.split("@", 1)[0].split("/", 1)[0]
