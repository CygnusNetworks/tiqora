"""ITSM links of the Tiqora CMDB (D8): link types (read side) and ``tiqora_itsm_link`` rows.

The link-type table is itsm1's (``tiqora_itsm_link_type``, step 0); cmdb only reads it here and
never edits a type (cross ruling R2). ``source``/``target`` of a link follow the type's
``pointed``/``propagation``: ``propagation == "source"`` means the SOURCE end gets the incident
warning when the TARGET is in incident.

Permissions (cross ruling R12): a link with a ticket end needs ``rw`` on the ticket's queue and
``ro`` on the CI's class; every other link needs ``rw`` on the class of each CI end. A ticket or
CI the caller may not see is answered like an unknown one (:class:`CmdbNotFound`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, get_args

from sqlalchemy import and_, bindparam, delete, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.access import CmdbAccess, readable_class_ids, require_class_access
from tiqora.cmdb.errors import CmdbError, CmdbNotFound
from tiqora.cmdb.schemas import LinkedObjectOut, LinkKind, LinkOut
from tiqora.db.tiqora.models import (
    TiqoraCmdbCi,
    TiqoraCmdbCiHistory,
    TiqoraItsmLink,
)
from tiqora.domain.ticket_write_service import _emit_event
from tiqora.itsm.models import TiqoraItsmLinkType
from tiqora.jobs.service import utc_now
from tiqora.kb.models import TiqoraKbArticle
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.cache_invalidation import invalidate_ticket_cache

LINK_KINDS: Final[frozenset[str]] = frozenset(get_args(LinkKind))

#: set by the propagation module (Task 12): ``await hook(session, ci_ids)`` recomputes the
#: derived incident state of the CIs and everything downstream. ``None`` = no propagation yet.
_recompute_hook: Callable[[AsyncSession, list[int]], Awaitable[None]] | None = None

HIDDEN_LABEL: Final = "Kein Zugriff"
_CHUNK: Final = 1000
_LIST_MAX: Final = 1000
_DETAIL_MAX: Final = 1000
_CI_TICKET_PAYLOAD: Final = "ITSMConfigItem"


class LinkInvalid(CmdbError):
    """The link itself is not allowed (self link, no CI end, unknown kind)."""


class UnknownLinkType(CmdbError):
    """The link type does not exist or is not valid."""

    def __init__(self, name: str) -> None:
        super().__init__(f"link type {name!r}")
        self.name = name


@dataclass(frozen=True, slots=True)
class LinkEnd:
    kind: LinkKind
    key: str


@dataclass(frozen=True, slots=True)
class LinkRecord:
    id: int
    source: LinkEnd
    target: LinkEnd
    link_type: str
    created_by: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _Resolved:
    exists: bool
    label: str
    queue_id: int | None = None
    class_id: int | None = None


def _end(kind: str, key: str) -> LinkEnd:
    if kind not in LINK_KINDS:
        raise LinkInvalid(f"unknown link kind {kind!r}")
    return LinkEnd(kind, str(key))  # type: ignore[arg-type]


def _record(row: TiqoraItsmLink) -> LinkRecord:
    return LinkRecord(
        id=int(row.id),
        source=_end(row.source_kind, row.source_key),
        target=_end(row.target_kind, row.target_key),
        link_type=str(row.link_type),
        created_by=int(row.created_by),
        created_at=row.created_at,
    )


def _chunks(items: Sequence[Any], size: int = _CHUNK) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _ids(keys: Iterable[str]) -> list[int]:
    return sorted({int(k) for k in keys if str(k).isdigit()})


def _url(kind: str, key: str) -> str | None:
    if kind == "ci":
        return f"/agent/cmdb/{key}"
    if kind == "ticket":
        return f"/agent/tickets/{key}"
    if kind == "customer_user":
        return f"/agent/customers/{key}"
    return None


# --- link types (read side) ------------------------------------------------------------


class LinkTypeService:
    """Read access to ``tiqora_itsm_link_type``; the rows are edited under "ITSM" (itsm1)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_types(self, *, include_invalid: bool = False) -> list[TiqoraItsmLinkType]:
        stmt = select(TiqoraItsmLinkType).order_by(
            TiqoraItsmLinkType.position, TiqoraItsmLinkType.name
        )
        if not include_invalid:
            stmt = stmt.where(TiqoraItsmLinkType.valid.is_(True))
        return list((await self._s.execute(stmt)).scalars().all())

    async def list_offered(self) -> list[TiqoraItsmLinkType]:
        """Valid types offered for CI to CI, CI to service or ticket to CI links."""
        return [
            t
            for t in await self.list_types()
            if t.offer_ci_ci or t.offer_ci_service or t.offer_ticket_ci
        ]

    async def get_by_name(self, name: str, *, valid_only: bool = True) -> TiqoraItsmLinkType:
        stmt = select(TiqoraItsmLinkType).where(TiqoraItsmLinkType.name == name)
        if valid_only:
            stmt = stmt.where(TiqoraItsmLinkType.valid.is_(True))
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise UnknownLinkType(name)
        return row

    async def propagating(self) -> dict[str, str]:
        """Name -> propagation of every valid type whose propagation is not ``none``."""
        rows = (
            await self._s.execute(
                select(TiqoraItsmLinkType.name, TiqoraItsmLinkType.propagation).where(
                    TiqoraItsmLinkType.valid.is_(True), TiqoraItsmLinkType.propagation != "none"
                )
            )
        ).all()
        return {str(n): str(p) for n, p in rows}


# --- links -----------------------------------------------------------------------------


async def signal_ticket_link(
    session: AsyncSession, event: Literal["LinkAdd", "LinkDelete"], ticket_id: int, ci_key: str
) -> None:
    """Outbox event + ticket cache signal for a ticket↔CI link change (assumption 7)."""
    await _emit_event(
        session, event, ticket_id, {"target_object": _CI_TICKET_PAYLOAD, "target_key": ci_key}
    )
    await invalidate_ticket_cache(session, ticket_id)


class LinkService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._perms = PermissionEngine(session)

    # --- end resolution ------------------------------------------------------------

    async def _resolve(self, ends: Iterable[LinkEnd]) -> dict[LinkEnd, _Resolved]:
        """Existence, label, queue (ticket) and class (CI) of every end, one query per kind."""
        ends = list(ends)
        by_kind: dict[str, set[str]] = {}
        for e in ends:
            by_kind.setdefault(e.kind, set()).add(e.key)
        out: dict[LinkEnd, _Resolved] = {}

        def put(kind: str, key: str, res: _Resolved) -> None:
            out[_end(kind, key)] = res

        s = self._s
        if ids := _ids(by_kind.get("ci", ())):
            for r_ci in (
                await s.execute(
                    select(
                        TiqoraCmdbCi.id,
                        TiqoraCmdbCi.number,
                        TiqoraCmdbCi.name,
                        TiqoraCmdbCi.class_id,
                    ).where(TiqoraCmdbCi.id.in_(ids))
                )
            ).all():
                put("ci", str(r_ci[0]), _Resolved(True, f"{r_ci[1]} {r_ci[2]}", None, int(r_ci[3])))
        if ids := _ids(by_kind.get("ticket", ())):
            for r_ticket in (
                await s.execute(
                    text("SELECT id, tn, title, queue_id FROM ticket WHERE id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": ids},
                )
            ).all():
                put(
                    "ticket",
                    str(r_ticket[0]),
                    _Resolved(True, f"{r_ticket[1]} {r_ticket[2]}", int(r_ticket[3])),
                )
        if ids := _ids(by_kind.get("service", ())):
            for r_service in (
                await s.execute(
                    text("SELECT id, name FROM service WHERE id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": ids},
                )
            ).all():
                put("service", str(r_service[0]), _Resolved(True, str(r_service[1])))
        if ids := _ids(by_kind.get("kb_article", ())):
            for r_kb in (
                await s.execute(
                    select(TiqoraKbArticle.id, TiqoraKbArticle.title).where(
                        TiqoraKbArticle.id.in_(ids)
                    )
                )
            ).all():
                put("kb_article", str(r_kb[0]), _Resolved(True, str(r_kb[1])))
        if keys := sorted(by_kind.get("customer_user", ())):
            for r_cu in (
                await s.execute(
                    text(
                        "SELECT login, first_name, last_name FROM customer_user"
                        " WHERE login IN :keys"
                    ).bindparams(bindparam("keys", expanding=True)),
                    {"keys": keys},
                )
            ).all():
                name = f"{r_cu[1] or ''} {r_cu[2] or ''}".strip()
                put("customer_user", str(r_cu[0]), _Resolved(True, name or str(r_cu[0])))
        if keys := sorted(by_kind.get("customer_company", ())):
            for r_co in (
                await s.execute(
                    text(
                        "SELECT customer_id, name FROM customer_company WHERE customer_id IN :keys"
                    ).bindparams(bindparam("keys", expanding=True)),
                    {"keys": keys},
                )
            ).all():
                put("customer_company", str(r_co[0]), _Resolved(True, str(r_co[1] or r_co[0])))
        for e in ends:
            out.setdefault(e, _Resolved(False, e.key))
        return out

    async def _check_write_ends(
        self,
        access: CmdbAccess,
        source: LinkEnd,
        target: LinkEnd,
        resolved: dict[LinkEnd, _Resolved],
    ) -> None:
        """Existence of every end and the R12 permission rule; failures are ``CmdbNotFound``."""
        has_ticket = "ticket" in (source.kind, target.kind)
        for end in (source, target):
            res = resolved[end]
            if not res.exists:
                raise CmdbNotFound(f"{end.kind} {end.key}")
            if end.kind == "ci":
                assert res.class_id is not None
                await require_class_access(
                    self._s, access, res.class_id, "ro" if has_ticket else "rw"
                )
            elif end.kind == "ticket":
                assert res.queue_id is not None
                if not await self._perms.check(access.user_id, res.queue_id, "rw"):
                    raise CmdbNotFound(f"ticket {end.key}")

    # --- add / delete --------------------------------------------------------------

    async def add(
        self,
        access: CmdbAccess,
        *,
        source: LinkEnd,
        target: LinkEnd,
        link_type_key: str,
        enforce_permissions: bool = True,
    ) -> LinkRecord:
        """Create the link (idempotent); the returned record is the stored orientation."""
        for end in (source, target):
            _end(end.kind, end.key)
        if source == target:
            raise LinkInvalid("a link needs two different objects")
        if "ci" not in (source.kind, target.kind):
            raise LinkInvalid("a link needs at least one configuration item")
        ltype = await LinkTypeService(self._s).get_by_name(link_type_key)
        if not ltype.pointed:
            # undirected: the CI is the source; CI to CI: the smaller id is
            ci_first = source.kind == "ci" and (
                target.kind != "ci" or _num(source.key) <= _num(target.key)
            )
            if not ci_first:
                source, target = target, source
        resolved = await self._resolve([source, target])
        if enforce_permissions:
            await self._check_write_ends(access, source, target, resolved)
        else:
            for end in (source, target):
                if not resolved[end].exists:
                    raise CmdbNotFound(f"{end.kind} {end.key}")
        existing = await self._find(source, target, ltype.name)
        if existing is not None:
            return _record(existing)
        now = utc_now()
        row = TiqoraItsmLink(
            source_kind=source.kind,
            source_key=source.key,
            target_kind=target.kind,
            target_key=target.key,
            link_type=ltype.name,
            created_by=access.user_id,
            created_at=now,
        )
        try:
            async with self._s.begin_nested():
                self._s.add(row)
                await self._s.flush()
        except IntegrityError:
            raced = await self._find(source, target, ltype.name)
            if raced is None:
                raise
            return _record(raced)
        await self._after_change(
            "LinkAdd",
            access.user_id,
            now,
            source,
            target,
            ltype.name,
            resolved,
            propagates=ltype.propagation != "none",
        )
        return _record(row)

    async def _find(self, source: LinkEnd, target: LinkEnd, name: str) -> TiqoraItsmLink | None:
        return (
            await self._s.execute(
                select(TiqoraItsmLink)
                .where(
                    TiqoraItsmLink.source_kind == source.kind,
                    TiqoraItsmLink.source_key == source.key,
                    TiqoraItsmLink.target_kind == target.kind,
                    TiqoraItsmLink.target_key == target.key,
                    TiqoraItsmLink.link_type == name,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def delete(
        self, access: CmdbAccess, link_id: int, *, enforce_permissions: bool = True
    ) -> None:
        row = (
            await self._s.execute(
                select(TiqoraItsmLink)
                .where(TiqoraItsmLink.id == link_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            raise CmdbNotFound(f"link {link_id}")
        rec = _record(row)
        resolved = await self._resolve([rec.source, rec.target])
        if enforce_permissions:
            await self._check_write_ends(access, rec.source, rec.target, resolved)
        ltype = (
            await self._s.execute(
                select(TiqoraItsmLinkType).where(TiqoraItsmLinkType.name == rec.link_type)
            )
        ).scalar_one_or_none()
        await self._s.execute(
            delete(TiqoraItsmLink)
            .where(TiqoraItsmLink.id == link_id)
            .execution_options(synchronize_session=False)
        )
        self._s.expunge(row)
        await self._after_change(
            "LinkDelete",
            access.user_id,
            utc_now(),
            rec.source,
            rec.target,
            rec.link_type,
            resolved,
            propagates=ltype is not None and ltype.propagation != "none",
        )

    async def _after_change(
        self,
        event: Literal["LinkAdd", "LinkDelete"],
        user_id: int,
        at: datetime,
        source: LinkEnd,
        target: LinkEnd,
        link_type: str,
        resolved: dict[LinkEnd, _Resolved],
        *,
        propagates: bool,
    ) -> None:
        """History on each CI end, outbox + cache signal on a ticket end, propagation."""
        ends = (source, target)
        for end, other in ((source, target), (target, source)):
            if end.kind == "ci" and end.key.isdigit():
                detail = f"{link_type}: {other.kind} {resolved[other].label}"[:_DETAIL_MAX]
                self._s.add(
                    TiqoraCmdbCiHistory(
                        ci_id=int(end.key),
                        event=event,
                        detail=detail,
                        version_id=None,
                        created_by=user_id,
                        created_at=at,
                    )
                )
        for end, other in ((source, target), (target, source)):
            if end.kind == "ticket" and end.key.isdigit() and other.kind == "ci":
                await signal_ticket_link(self._s, event, int(end.key), other.key)
        if propagates and all(e.kind == "ci" and e.key.isdigit() for e in ends):
            hook = _recompute_hook
            if hook is not None:
                await hook(self._s, [int(source.key), int(target.key)])

    # --- reading -------------------------------------------------------------------

    async def _check_subject(self, access: CmdbAccess, end: LinkEnd) -> None:
        resolved = (await self._resolve([end]))[end]
        if end.kind == "ci":
            if not resolved.exists or resolved.class_id is None:
                raise CmdbNotFound(f"ci {end.key}")
            await require_class_access(self._s, access, resolved.class_id, "ro")
        elif end.kind == "ticket":
            if (
                not resolved.exists
                or resolved.queue_id is None
                or not await self._perms.check(access.user_id, resolved.queue_id, "ro")
            ):
                raise CmdbNotFound(f"ticket {end.key}")

    async def list_for(
        self,
        access: CmdbAccess,
        kind: str,
        key: str,
        *,
        enforce_permissions: bool = True,
    ) -> list[LinkOut]:
        """Links of one object seen from it. Tickets without ``ro`` are omitted, CIs of
        unreadable classes appear as ``hidden`` rows without key or label."""
        subject = _end(kind, key)
        if enforce_permissions:
            await self._check_subject(access, subject)
        rows = (
            (
                await self._s.execute(
                    select(TiqoraItsmLink)
                    .where(
                        or_(
                            and_(
                                TiqoraItsmLink.source_kind == subject.kind,
                                TiqoraItsmLink.source_key == subject.key,
                            ),
                            and_(
                                TiqoraItsmLink.target_kind == subject.kind,
                                TiqoraItsmLink.target_key == subject.key,
                            ),
                        )
                    )
                    .order_by(TiqoraItsmLink.id)
                    .limit(_LIST_MAX)
                )
            )
            .scalars()
            .all()
        )
        return await self._to_out([_record(r) for r in rows], access, subject, enforce_permissions)

    async def get_out(
        self, access: CmdbAccess, link_id: int, *, viewpoint: LinkEnd | None = None
    ) -> LinkOut:
        """One link as :class:`LinkOut`, seen from ``viewpoint`` (default: its source)."""
        row = (
            await self._s.execute(select(TiqoraItsmLink).where(TiqoraItsmLink.id == link_id))
        ).scalar_one_or_none()
        if row is None:
            raise CmdbNotFound(f"link {link_id}")
        rec = _record(row)
        outs = await self._to_out([rec], access, viewpoint or rec.source, False)
        return outs[0]

    async def _to_out(
        self,
        records: list[LinkRecord],
        access: CmdbAccess,
        subject: LinkEnd,
        enforce: bool,
    ) -> list[LinkOut]:
        if not records:
            return []
        types = {t.name: t for t in await LinkTypeService(self._s).list_types(include_invalid=True)}
        others = [r.target if r.source == subject else r.source for r in records]
        resolved = await self._resolve(others)
        readable: set[int] | None = None
        if enforce and not access.is_admin:
            readable = set(await readable_class_ids(self._s, access))
        queue_ok: dict[int, bool] = {}
        users = await self._user_names({r.created_by for r in records})
        out: list[LinkOut] = []
        for rec, other in zip(records, others, strict=True):
            res = resolved[other]
            is_source = rec.source == subject
            ltype = types.get(rec.link_type)
            pointed = bool(ltype and ltype.pointed)
            direction: Literal["outgoing", "incoming", "undirected"] = (
                "undirected" if not pointed else "outgoing" if is_source else "incoming"
            )
            own = (
                ltype.source_label if ltype and is_source else ltype.target_label if ltype else None
            )
            alt = (
                ltype.target_label if ltype and is_source else ltype.source_label if ltype else None
            )
            label = own or alt or rec.link_type
            linked: LinkedObjectOut
            if other.kind == "ticket":
                if not res.exists or res.queue_id is None:
                    continue
                if enforce:
                    if res.queue_id not in queue_ok:
                        queue_ok[res.queue_id] = await self._perms.check(
                            access.user_id, res.queue_id, "ro"
                        )
                    if not queue_ok[res.queue_id]:
                        continue
                linked = LinkedObjectOut(
                    kind="ticket",
                    key=other.key,
                    label=res.label,
                    hidden=False,
                    url=_url("ticket", other.key),
                )
            elif other.kind == "ci":
                if not res.exists:
                    continue
                if readable is not None and res.class_id not in readable:
                    linked = LinkedObjectOut(
                        kind="ci", key="", label=HIDDEN_LABEL, hidden=True, url=None
                    )
                else:
                    linked = LinkedObjectOut(
                        kind="ci",
                        key=other.key,
                        label=res.label,
                        hidden=False,
                        url=_url("ci", other.key),
                    )
            else:
                linked = LinkedObjectOut(
                    kind=other.kind,
                    key=other.key,
                    label=res.label,
                    hidden=False,
                    url=_url(other.kind, other.key),
                )
            out.append(
                LinkOut(
                    id=rec.id,
                    link_type=rec.link_type,
                    link_type_label=label,
                    direction=direction,
                    other=linked,
                    created_by=users.get(rec.created_by),
                    created_at=rec.created_at,
                )
            )
        return out

    async def _user_names(self, ids: set[int]) -> dict[int, str]:
        if not ids:
            return {}
        rows = (
            await self._s.execute(
                text("SELECT id, login FROM users WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": sorted(ids)},
            )
        ).all()
        return {int(i): str(login) for i, login in rows}

    async def ci_neighbours(
        self, ci_ids: Iterable[int], *, propagating_only: bool
    ) -> list[tuple[int, int, str]]:
        """CI to CI links touching ``ci_ids``: ``(source CI, target CI, link type name)``.

        ``propagating_only`` keeps the types that propagate (valid, propagation not ``none``).
        Keys are strings in ``tiqora_itsm_link``, so every IN list holds strings.
        """
        names: list[str] | None = None
        if propagating_only:
            names = sorted(await LinkTypeService(self._s).propagating())
            if not names:
                return []
        seen: set[tuple[int, int, str]] = set()
        for chunk in _chunks(sorted({int(i) for i in ci_ids})):
            keys = [str(i) for i in chunk]
            stmt = select(
                TiqoraItsmLink.source_key, TiqoraItsmLink.target_key, TiqoraItsmLink.link_type
            ).where(
                TiqoraItsmLink.source_kind == "ci",
                TiqoraItsmLink.target_kind == "ci",
                or_(TiqoraItsmLink.source_key.in_(keys), TiqoraItsmLink.target_key.in_(keys)),
            )
            if names is not None:
                stmt = stmt.where(TiqoraItsmLink.link_type.in_(names))
            for sk, tk, name in (await self._s.execute(stmt)).all():
                if str(sk).isdigit() and str(tk).isdigit():
                    seen.add((int(sk), int(tk), str(name)))
        return sorted(seen)


def _num(key: str) -> int:
    return int(key) if key.isdigit() else 0


__all__ = [
    "HIDDEN_LABEL",
    "LINK_KINDS",
    "LinkEnd",
    "LinkInvalid",
    "LinkKind",
    "LinkRecord",
    "LinkService",
    "LinkTypeService",
    "UnknownLinkType",
    "signal_ticket_link",
]
