"""Shared dependencies of the CMDB routers: the mode gate, the access context, error mapping.

The gate is a router-level dependency (``APIRouter(dependencies=[Depends(cmdb_gate)])``) so it
answers 404/409 before any body validation. Later routers include themselves into
:data:`tiqora.api.v1.cmdb.gated`; the walk in ``tests/test_cmdb_gate_routes.py`` then covers them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.cmdb.access import CmdbAccess, load_access
from tiqora.cmdb.attachments import AttachmentInUse
from tiqora.cmdb.catalog import CatalogItemExists, CatalogMisconfigured
from tiqora.cmdb.classes import (
    ClassConflict,
    ClassKeyInvalid,
    ClassKeyTaken,
    DefinitionInvalid,
)
from tiqora.cmdb.errors import CmdbError, CmdbForbidden, CmdbNotFound
from tiqora.cmdb.gate import CmdbUnavailable, require_tiqora_cmdb
from tiqora.cmdb.links import UnknownLinkType
from tiqora.cmdb.service import CiConflict, InvalidValues, UnknownAttributes
from tiqora.cmdb.transfer.templates import TemplateConflict
from tiqora.cmdb.znuny_definition import DefinitionParseError
from tiqora.domain.auth import AuthenticatedUser
from tiqora.filters import FilterError

NOT_FOUND: Final = "Nicht gefunden"
CONFLICT: Final = {"code": "conflict", "message": "Inzwischen geändert. Bitte neu laden."}
IN_ZNUNY: Final = {"code": "cmdb_in_znuny", "message": "Die CMDB wird in Znuny geführt."}


def cmdb_http_error(exc: Exception) -> HTTPException:
    """Map a CMDB domain error to its HTTP answer (every CMDB router uses this)."""
    if isinstance(exc, CmdbUnavailable):
        if exc.mode == "znuny_readthrough":
            return HTTPException(status.HTTP_409_CONFLICT, IN_ZNUNY)
        return HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if isinstance(exc, AttachmentInUse):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "attachment_in_use",
                "message": "Der Anhang wird noch in einem Attribut verwendet.",
            },
        )
    if isinstance(exc, CmdbNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if isinstance(exc, CmdbForbidden):
        return HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"code": "forbidden", "message": "Dafür fehlt die Berechtigung."},
        )
    if isinstance(exc, ClassConflict | CiConflict | TemplateConflict):
        return HTTPException(status.HTTP_409_CONFLICT, dict(CONFLICT))
    if isinstance(exc, DefinitionInvalid):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "code": "invalid_definition",
                "issues": [
                    {"path": i.path, "code": i.code, "message": i.message} for i in exc.issues
                ],
            },
        )
    if isinstance(exc, DefinitionParseError):
        position = getattr(exc.__cause__, "position", None)
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "parse_error", "message": str(exc), "position": position},
        )
    if isinstance(exc, ClassKeyInvalid):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "invalid_class_key", "message": "Der Klassenschlüssel ist ungültig."},
        )
    if isinstance(exc, ClassKeyTaken):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "class_key_taken", "message": "Dieser Klassenschlüssel ist schon vergeben."},
        )
    if isinstance(exc, UnknownLinkType):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "unknown_link_type", "message": "Diesen Verknüpfungstyp gibt es nicht."},
        )
    if isinstance(exc, CatalogItemExists):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "catalog_item_exists", "message": "Diesen Eintrag gibt es schon."},
        )
    if isinstance(exc, CatalogMisconfigured):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "catalog_misconfigured", "message": "Der Katalog ist unvollständig."},
        )
    if isinstance(exc, UnknownAttributes):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "unknown_attributes", "keys": list(exc.keys)},
        )
    if isinstance(exc, InvalidValues):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "code": "invalid_values",
                "issues": [
                    {"path": i.path, "code": i.code, "message": i.message} for i in exc.issues
                ],
            },
        )
    if isinstance(exc, FilterError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "invalid_filter", "message": exc.message, "path": exc.path},
        )
    if isinstance(exc, CmdbError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": "invalid", "message": str(exc)}
        )
    raise exc


async def cmdb_gate(session: DbSession, user: CurrentUser) -> None:
    """404 when the CMDB is off, 409 ``cmdb_in_znuny`` while Znuny keeps it; else passes."""
    _ = user  # authentication first: an anonymous caller learns nothing about the mode
    try:
        await require_tiqora_cmdb(session)
    except CmdbUnavailable as exc:
        raise cmdb_http_error(exc) from exc


@dataclass(frozen=True, slots=True)
class CmdbContext:
    session: AsyncSession
    user: AuthenticatedUser
    access: CmdbAccess


async def get_cmdb_context(
    session: DbSession, user: CurrentUser, _gate: Annotated[None, Depends(cmdb_gate)]
) -> CmdbContext:
    return CmdbContext(session=session, user=user, access=await load_access(session, user.id))


Ctx = Annotated[CmdbContext, Depends(get_cmdb_context)]

__all__ = ["CONFLICT", "Ctx", "CmdbContext", "cmdb_gate", "cmdb_http_error", "get_cmdb_context"]
