"""Agent CMDB API, mounted at ``/api/v1/cmdb`` (API-key area ``cmdb``).

``GET /status`` is the only ungated route. Every other router includes itself into
:data:`gated`, whose router-level dependency answers 404 (CMDB off) or 409 ``cmdb_in_znuny``
(Znuny keeps the CMDB) before the body is validated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from tiqora.api.v1.cmdb import attachments, cis, classes, links, searches, transfer
from tiqora.api.v1.cmdb.deps import cmdb_gate

router = APIRouter(prefix="/cmdb", tags=["cmdb"])
gated = APIRouter(dependencies=[Depends(cmdb_gate)])

router.include_router(classes.status_router)
gated.include_router(classes.router)
gated.include_router(cis.router)
gated.include_router(searches.router)
gated.include_router(attachments.router)
gated.include_router(links.router)
gated.include_router(transfer.router)
# later tasks: gated.include_router(<their router>) above the next line
router.include_router(gated)

__all__ = ["gated", "router"]
