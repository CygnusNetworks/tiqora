"""Display form of CI attribute values (D4): reference names, links, portal filtering.

``render_values`` turns a stored value document into a tree of :class:`RenderedAttr` with one
IN-list lookup per reference type (Znuny and Tiqora tables are never joined). A reference the
reader may not see is shown as "Kein Zugriff" without a link, so rendering never leaks the name
of a CI in a class the reader has no access to.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.access import CmdbAccess
from tiqora.cmdb.definition import AttributeDef, AttributeType
from tiqora.cmdb.values import REF_TYPES, collect_references
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import (
    TiqoraCmdbAttachment,
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbClass,
)

NO_ACCESS: Final = "Kein Zugriff"

RefKind = Literal[
    "ci", "service", "customer_user", "customer_company", "attachment", "queue", "agent", "url"
]

_CHUNK: Final = 500
_INT32: Final = 2**31 - 1
#: reference types a portal reader never sees (internal data)
_PORTAL_HIDDEN: Final[frozenset[str]] = frozenset({"agent", "queue"})
#: reference type -> link kind (types without an entry show the name only)
_REF_KIND: Final[dict[str, RefKind]] = {
    "ci_ref": "ci",
    "service": "service",
    "queue": "queue",
    "agent": "agent",
    "customer_user": "customer_user",
    "customer_company": "customer_company",
    "attachment": "attachment",
}
_NAME_COLUMNS: Final[dict[str, tuple[Any, Any]]] = {
    "service": (Service.id, Service.name),
    "sla": (Sla.id, Sla.name),
    "queue": (Queue.id, Queue.name),
    "priority": (TicketPriority.id, TicketPriority.name),
    "ticket_type": (TicketType.id, TicketType.name),
    "state": (TicketState.id, TicketState.name),
}


@dataclass(frozen=True, slots=True)
class RefLink:
    kind: RefKind
    key: str


@dataclass(frozen=True, slots=True)
class RenderedAttr:
    #: positional path, e.g. ``NIC[1].IPAddress[2]``
    path: str
    key: str
    label: str
    type: AttributeType
    display: str | None
    ref: RefLink | None
    children: list[RenderedAttr] = field(default_factory=list)
    portal: bool = False


@dataclass(slots=True)
class _Lookups:
    names: dict[tuple[str, int | str], str] = field(default_factory=dict)
    #: customer user / company: casefolded key -> real key
    real_keys: dict[tuple[str, str], str] = field(default_factory=dict)
    #: ci id -> (number, name, class id)
    cis: dict[int, tuple[str, str, int]] = field(default_factory=dict)
    #: class id -> (group id, portal visible)
    classes: dict[int, tuple[int, bool]] = field(default_factory=dict)


def _chunks(items: list[Any]) -> list[list[Any]]:
    return [items[i : i + _CHUNK] for i in range(0, len(items), _CHUNK)]


def _full_name(first: str | None, last: str | None) -> str:
    return f"{first or ''} {last or ''}".strip()


async def _load(
    session: AsyncSession, refs: Mapping[str, set[int | str]], need_classes: bool
) -> _Lookups:
    out = _Lookups()
    for t, values in refs.items():
        ints = sorted(v for v in values if isinstance(v, int) and not isinstance(v, bool))
        strs = sorted(v for v in values if isinstance(v, str))
        if t in _NAME_COLUMNS:
            id_col, name_col = _NAME_COLUMNS[t]
            for part in _chunks([i for i in ints if 0 <= i <= _INT32]):
                for i, name in (
                    await session.execute(select(id_col, name_col).where(id_col.in_(part)))
                ).all():
                    out.names[(t, int(i))] = str(name)
        elif t == "agent":
            for part in _chunks([i for i in ints if 0 <= i <= _INT32]):
                for i, first, last in (
                    await session.execute(
                        select(Users.id, Users.first_name, Users.last_name).where(
                            Users.id.in_(part)
                        )
                    )
                ).all():
                    out.names[(t, int(i))] = _full_name(first, last) or f"#{int(i)}"
        elif t == "catalog":
            for part in _chunks([i for i in ints if 0 <= i <= _INT32]):
                for i, name in (
                    await session.execute(
                        select(TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.name).where(
                            TiqoraCmdbCatalog.id.in_(part)
                        )
                    )
                ).all():
                    out.names[(t, int(i))] = str(name)
        elif t == "ci_ref":
            for part in _chunks([i for i in ints if 0 <= i <= 2**63 - 1]):
                for i, number, name, class_id in (
                    await session.execute(
                        select(
                            TiqoraCmdbCi.id,
                            TiqoraCmdbCi.number,
                            TiqoraCmdbCi.name,
                            TiqoraCmdbCi.class_id,
                        ).where(TiqoraCmdbCi.id.in_(part))
                    )
                ).all():
                    out.cis[int(i)] = (str(number), str(name), int(class_id))
        elif t == "attachment":
            for part in _chunks([i for i in ints if 0 <= i <= 2**63 - 1]):
                for i, filename in (
                    await session.execute(
                        select(TiqoraCmdbAttachment.id, TiqoraCmdbAttachment.filename).where(
                            TiqoraCmdbAttachment.id.in_(part)
                        )
                    )
                ).all():
                    out.names[(t, int(i))] = str(filename)
        elif t == "customer_user":
            for part in _chunks(strs):
                for login, email, first, last in (
                    await session.execute(
                        select(
                            CustomerUser.login,
                            CustomerUser.email,
                            CustomerUser.first_name,
                            CustomerUser.last_name,
                        ).where(CustomerUser.login.in_(part))
                    )
                ).all():
                    name = _full_name(first, last)
                    shown = f"{name} <{email}>" if name else f"<{email}>"
                    out.names[(t, str(login).casefold())] = shown
                    out.real_keys[(t, str(login).casefold())] = str(login)
        elif t == "customer_company":
            for part in _chunks(strs):
                for cid, name in (
                    await session.execute(
                        select(CustomerCompany.customer_id, CustomerCompany.name).where(
                            CustomerCompany.customer_id.in_(part)
                        )
                    )
                ).all():
                    out.names[(t, str(cid).casefold())] = str(name or cid)
                    out.real_keys[(t, str(cid).casefold())] = str(cid)
    class_ids = sorted({c for _, _, c in out.cis.values()}) if need_classes else []
    for part in _chunks(class_ids):
        for cid, group_id, portal_visible in (
            await session.execute(
                select(
                    TiqoraCmdbClass.id, TiqoraCmdbClass.group_id, TiqoraCmdbClass.portal_visible
                ).where(TiqoraCmdbClass.id.in_(part))
            )
        ).all():
            out.classes[int(cid)] = (int(group_id), bool(portal_visible))
    return out


def _safe_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


class _Renderer:
    def __init__(self, lookups: _Lookups, access: CmdbAccess | None, portal_only: bool) -> None:
        self._l = lookups
        self._access = access
        self._portal_only = portal_only

    def _ci(self, value: Any) -> tuple[str, RefLink | None] | None:
        """``None`` = leave the entry out (portal reader, CI not portal visible)."""
        if not isinstance(value, int) or isinstance(value, bool):
            return str(value), None
        found = self._l.cis.get(value)
        if found is None:
            return f"#{value}", None
        number, name, class_id = found
        group_id, portal_visible = self._l.classes.get(class_id, (0, False))
        if self._portal_only and not portal_visible:
            return None
        if self._access is not None and not self._access.can_read_group(group_id):
            return NO_ACCESS, None
        return f"{number} – {name}", RefLink("ci", str(value))

    def _simple(self, a: AttributeDef, value: Any) -> tuple[str, RefLink | None]:
        t = a.type
        if t == "url":
            text = str(value)
            return text, RefLink("url", text) if _safe_url(text) else None
        if t == "money":
            cur = a.options.currency
            return (f"{value} {cur}" if cur else str(value)), None
        if t == "datetime":
            text = str(value)
            return (text if text.endswith("Z") or "+" in text[10:] else f"{text}Z"), None
        if t in REF_TYPES and t not in ("ci_ref", "integer"):
            key: int | str = value.casefold() if isinstance(value, str) else value
            name = self._l.names.get((t, key))
            if name is None:
                return (f"#{value}" if isinstance(value, int) else str(value)), None
            kind = _REF_KIND.get(t)
            if kind is None:
                return name, None
            real = self._l.real_keys.get((t, str(key)), str(value))
            return name, RefLink(kind, real)
        return str(value), None

    def build(
        self, attrs: list[AttributeDef], values: Mapping[str, Any], prefix: str
    ) -> list[RenderedAttr]:
        out: list[RenderedAttr] = []
        for a in attrs:
            if self._portal_only and (not a.portal or a.type in _PORTAL_HIDDEN):
                continue
            entries = values.get(a.key)
            if not isinstance(entries, list):
                continue
            for idx, entry in enumerate(entries, start=1):
                if not isinstance(entry, Mapping):
                    continue
                path = f"{prefix}{a.key}[{idx}]"
                v = entry.get("v")
                sub = entry.get("s")
                children = (
                    self.build(a.sub, sub, f"{path}.") if a.sub and isinstance(sub, Mapping) else []
                )
                display: str | None = None
                ref: RefLink | None = None
                if a.type != "group" and v is not None:
                    if a.type == "ci_ref":
                        shown = self._ci(v)
                        if shown is None:
                            continue
                        display, ref = shown
                    else:
                        display, ref = self._simple(a, v)
                if display is None and not children:
                    continue
                out.append(
                    RenderedAttr(
                        path=path,
                        key=a.key,
                        label=a.name,
                        type=a.type,
                        display=display,
                        ref=ref,
                        children=children,
                        portal=a.portal,
                    )
                )
        return out


async def render_values(
    session: AsyncSession,
    attrs: list[AttributeDef],
    values: Mapping[str, Any],
    *,
    access: CmdbAccess | None,
    portal_only: bool = False,
) -> list[RenderedAttr]:
    """Display tree of ``values`` in the order of ``attrs`` (the definition of that version).

    ``access=None`` skips the class-group check of referenced CIs (system callers, portal).
    ``portal_only`` keeps only ``portal`` attributes and drops ``agent``/``queue`` values and
    CI references into classes that are not portal visible. Only ``http``/``https`` URLs get a
    ``url`` link (F14).
    """
    refs = collect_references(attrs, values)
    lookups = await _load(session, refs, need_classes=bool(refs.get("ci_ref")))
    return _Renderer(lookups, access, portal_only).build(attrs, values, "")


def flatten(nodes: list[RenderedAttr]) -> list[RenderedAttr]:
    """Depth-first nodes that carry a display value (groups contribute their children only)."""
    out: list[RenderedAttr] = []
    for n in nodes:
        if n.display is not None:
            out.append(n)
        out.extend(flatten(n.children))
    return out


__all__ = ["NO_ACCESS", "RefKind", "RefLink", "RenderedAttr", "flatten", "render_values"]
