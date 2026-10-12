"""The customer portal's view of the CMDB (Mode B only): CIs linked to the customer or company.

A CI is visible to a customer when its class is valid and ``portal_visible`` and a link of any
type joins it to the customer's login (``customer_user``) or to one of the customer's companies
(``customer_company``: the primary ``customer_id`` plus the ``customer_user_customer`` mapping).
Anything else is answered as not found. Only attributes flagged ``portal`` are rendered, and
rendered values carry no references into the agent UI.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterable, Sequence
from typing import Final

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.errors import CmdbError, CmdbNotFound
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.render import RenderedAttr, render_values
from tiqora.cmdb.schemas import PortalCiDetailOut, PortalCiOut, PortalCiPage, RenderedAttrOut
from tiqora.cmdb.service import CiService, system_access
from tiqora.db.tiqora.models import (
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbClass,
    TiqoraItsmLink,
)
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.domain.portal_ticket_service import PORTAL_SYSTEM_USER_ID

TICKET_LINK_TYPE: Final = "RelevantTo"
_CHUNK: Final = 1000
_Q_MAX: Final = 200


class PortalCiNotFound(CmdbNotFound):
    """The CI does not exist, is not portal visible, or is not the customer's."""


class PortalCiInvalid(CmdbError):
    """A ticket was to be linked to CIs the customer may not use."""


def _chunks(items: Sequence[int]) -> Iterable[Sequence[int]]:
    for i in range(0, len(items), _CHUNK):
        yield items[i : i + _CHUNK]


def _strip_refs(nodes: Iterable[RenderedAttr]) -> builtins.list[RenderedAttrOut]:
    return [
        RenderedAttrOut(
            path=n.path,
            key=n.key,
            label=n.label,
            type=n.type,
            display=n.display,
            ref=None,
            children=_strip_refs(n.children),
            portal=n.portal,
        )
        for n in nodes
    ]


class PortalCmdbService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def customer_scope(self, customer: AuthenticatedCustomer) -> tuple[str, set[str]]:
        """``(login, company ids)``: the primary company plus the mapped ones."""
        companies = {customer.customer_id} if customer.customer_id else set()
        rows = await self._s.execute(
            text("SELECT customer_id FROM customer_user_customer WHERE user_id = :u"),
            {"u": customer.login},
        )
        companies.update(str(r[0]) for r in rows.all())
        return customer.login, companies

    async def visible_ci_ids(
        self, customer: AuthenticatedCustomer, *, ci_ids: Sequence[int] | None = None
    ) -> set[int]:
        login, companies = await self.customer_scope(customer)
        owners = [("customer_user", [login])]
        if companies:
            owners.append(("customer_company", sorted(companies)))
        linked: set[int] = set()
        for kind, keys in owners:
            for a, b in (("source", "target"), ("target", "source")):
                ci_kind, ci_key = (
                    getattr(TiqoraItsmLink, f"{a}_kind"),
                    getattr(TiqoraItsmLink, f"{a}_key"),
                )
                o_kind, o_key = (
                    getattr(TiqoraItsmLink, f"{b}_kind"),
                    getattr(TiqoraItsmLink, f"{b}_key"),
                )
                stmt = select(ci_key).where(and_(ci_kind == "ci", o_kind == kind, o_key.in_(keys)))
                linked.update(int(k) for (k,) in (await self._s.execute(stmt)).all() if k.isdigit())
        if ci_ids is not None:
            linked &= {int(i) for i in ci_ids}
        if not linked:
            return set()
        visible: set[int] = set()
        for chunk in _chunks(sorted(linked)):
            rows = await self._s.execute(
                select(TiqoraCmdbCi.id)
                .join(TiqoraCmdbClass, TiqoraCmdbClass.id == TiqoraCmdbCi.class_id)
                .where(
                    TiqoraCmdbCi.id.in_(list(chunk)),
                    TiqoraCmdbClass.portal_visible.is_(True),
                    TiqoraCmdbClass.valid.is_(True),
                )
            )
            visible.update(int(i) for (i,) in rows.all())
        return visible

    async def _summaries(
        self, ci_ids: Sequence[int], *, q: str | None = None
    ) -> builtins.list[PortalCiOut]:
        dep = TiqoraCmdbCatalog.__table__.alias("dep")
        inc = TiqoraCmdbCatalog.__table__.alias("inc")
        out: builtins.list[PortalCiOut] = []
        for chunk in _chunks(sorted(ci_ids)):
            stmt = (
                select(
                    TiqoraCmdbCi.id,
                    TiqoraCmdbCi.number,
                    TiqoraCmdbCi.name,
                    TiqoraCmdbClass.name.label("class_name"),
                    dep.c.name.label("dep_name"),
                    inc.c.name.label("inc_name"),
                    inc.c.functionality.label("inc_func"),
                )
                .join(TiqoraCmdbClass, TiqoraCmdbClass.id == TiqoraCmdbCi.class_id)
                .join(dep, dep.c.id == TiqoraCmdbCi.deployment_state_id)
                .join(inc, inc.c.id == TiqoraCmdbCi.cur_incident_state_id)
                .where(TiqoraCmdbCi.id.in_(list(chunk)))
            )
            if q:
                like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                stmt = stmt.where(
                    or_(
                        func.lower(TiqoraCmdbCi.name).like(like.lower(), escape="\\"),
                        func.lower(TiqoraCmdbCi.number).like(like.lower(), escape="\\"),
                    )
                )
            for r in (await self._s.execute(stmt)).all():
                out.append(
                    PortalCiOut(
                        id=int(r.id),
                        number=str(r.number),
                        name=str(r.name),
                        class_name=str(r.class_name),
                        deployment_state=str(r.dep_name),
                        incident_state=str(r.inc_name),
                        incident_functionality=r.inc_func,
                    )
                )
        return out

    async def list(
        self, customer: AuthenticatedCustomer, *, q: str | None, offset: int, limit: int
    ) -> PortalCiPage:
        ids = await self.visible_ci_ids(customer)
        term = (q or "").strip()[:_Q_MAX] or None
        items = await self._summaries(sorted(ids), q=term)
        items.sort(key=lambda c: (c.name.casefold(), c.id))
        return PortalCiPage(items=items[offset : offset + limit], total=len(items))

    async def get(self, customer: AuthenticatedCustomer, ci_id: int) -> PortalCiDetailOut:
        if ci_id not in await self.visible_ci_ids(customer, ci_ids=[ci_id]):
            raise PortalCiNotFound(f"ci {ci_id}")
        head = (await self._summaries([ci_id]))[0]
        version_id = (
            await self._s.execute(
                select(TiqoraCmdbCi.current_version_id).where(TiqoraCmdbCi.id == ci_id)
            )
        ).scalar_one_or_none()
        rendered: builtins.list[RenderedAttrOut] = []
        if version_id is not None:
            version = await CiService(self._s).get_version(
                system_access(PORTAL_SYSTEM_USER_ID),
                ci_id,
                int(version_id),
                enforce_permissions=False,
            )
            nodes = await render_values(
                self._s,
                version.definition_attrs,
                version.attributes,
                access=None,
                portal_only=True,
            )
            rendered = _strip_refs(nodes)
        return PortalCiDetailOut(**head.model_dump(), rendered=rendered)

    async def link_ticket_cis(
        self, ticket_id: int, ci_ids: Sequence[int], *, customer: AuthenticatedCustomer
    ) -> None:
        """Link the CIs to the (just created) ticket; checks scope again, never commits."""
        wanted = sorted({int(i) for i in ci_ids})
        if not wanted:
            return
        if await self.visible_ci_ids(customer, ci_ids=wanted) != set(wanted):
            raise PortalCiInvalid("configuration item not available")
        links = LinkService(self._s)
        access = system_access(PORTAL_SYSTEM_USER_ID)
        for ci_id in wanted:
            await links.add(
                access,
                source=LinkEnd("ci", str(ci_id)),
                target=LinkEnd("ticket", str(ticket_id)),
                link_type_key=TICKET_LINK_TYPE,
                enforce_permissions=False,
            )


__all__ = ["PortalCiInvalid", "PortalCiNotFound", "PortalCmdbService", "TICKET_LINK_TYPE"]
