"""Domain errors shared by the CMDB services; ``api/v1/cmdb/deps.py`` maps them to HTTP."""

from __future__ import annotations


class CmdbError(Exception):
    """Base class of every CMDB domain error."""


class CmdbNotFound(CmdbError):
    """Unknown id, or an object the caller may not even know exists (answered as 404)."""


class CmdbForbidden(CmdbError):
    """Readable but not writable (answered as 403)."""


__all__ = ["CmdbError", "CmdbForbidden", "CmdbNotFound"]
