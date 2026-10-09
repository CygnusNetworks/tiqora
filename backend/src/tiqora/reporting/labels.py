"""Display labels for dimension values (queue names, agent names, ticket numbers, ...).

Keys are loaded as IN-lists from ONE table each (no JOIN between Tiqora and
Znuny tables, no string JOIN across collations). Result keys are ``str(key)``;
a key that is not found maps to ``str(key)``. The queue value
:data:`~tiqora.reporting.compiler.HIDDEN_QUEUE_ID` (a queue outside the
viewer's scope) is never looked up and always reads "Andere Queue".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import Ticket, TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import Users
from tiqora.reporting.compiler import HIDDEN_QUEUE_ID
from tiqora.reporting.semantic import LabelSource

HIDDEN_QUEUE_LABEL: Final = "Andere Queue"
_IN_CHUNK: Final = 500

#: label source -> (id column, name column) for the plain "id -> name" tables
_NAME_TABLES: Final[dict[str, tuple[Any, Any]]] = {
    "queue": (Queue.id, Queue.name),
    "state": (TicketState.id, TicketState.name),
    "priority": (TicketPriority.id, TicketPriority.name),
    "type": (TicketType.id, TicketType.name),
    "service": (Service.id, Service.name),
    "sla": (Sla.id, Sla.name),
}


def _int_keys(keys: Iterable[str | int]) -> list[int]:
    out: set[int] = set()
    for key in keys:
        if isinstance(key, bool):
            continue
        try:
            out.add(int(key))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _by_folded(keys: Iterable[str | int]) -> dict[str, list[str]]:
    """Case-folded key -> the requested spellings (MariaDB compares strings case-insensitively)."""
    out: dict[str, list[str]] = {}
    for key in keys:
        out.setdefault(str(key).casefold(), []).append(str(key))
    return out


def _spellings(folded: dict[str, list[str]]) -> list[str]:
    return sorted({s for spellings in folded.values() for s in spellings})


def _chunks[T](items: Sequence[T]) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), _IN_CHUNK):
        yield items[i : i + _IN_CHUNK]


def _person(first: str | None, last: str | None, fallback: str) -> str:
    name = f"{first or ''} {last or ''}".strip()
    return name or fallback


async def _lookup(
    session: AsyncSession, key_col: Any, columns: Sequence[Any], keys: Sequence[Any]
) -> list[Any]:
    rows: list[Any] = []
    for chunk in _chunks(keys):
        stmt = select(key_col, *columns).where(key_col.in_(chunk))
        rows.extend((await session.execute(stmt)).all())
    return rows


async def resolve_labels(
    session: AsyncSession, source: LabelSource, keys: Iterable[str | int]
) -> dict[str, str]:
    """Labels for ``keys`` of one label source; see the module docstring."""
    if source == "none":
        return {}
    wanted = [k for k in dict.fromkeys(keys) if k is not None]
    out = {str(k): str(k) for k in wanted}
    if not wanted:
        return out

    if source in _NAME_TABLES:
        ids = _int_keys(wanted)
        if source == "queue" and HIDDEN_QUEUE_ID in ids:
            out[str(HIDDEN_QUEUE_ID)] = HIDDEN_QUEUE_LABEL
            ids.remove(HIDDEN_QUEUE_ID)
        key_col, name_col = _NAME_TABLES[source]
        for key, name in await _lookup(session, key_col, [name_col], ids):
            out[str(key)] = name
    elif source == "user":
        for key, login, first, last in await _lookup(
            session, Users.id, [Users.login, Users.first_name, Users.last_name], _int_keys(wanted)
        ):
            out[str(key)] = _person(first, last, login)
    elif source == "ticket":
        for key, tn, title in await _lookup(
            session, Ticket.id, [Ticket.tn, Ticket.title], _int_keys(wanted)
        ):
            out[str(key)] = f"{tn} {title or ''}".strip()
    elif source == "customer_company":
        by_name = _by_folded(wanted)
        for key, name in await _lookup(
            session, CustomerCompany.customer_id, [CustomerCompany.name], _spellings(by_name)
        ):
            for original in by_name.get(str(key).casefold(), ()):
                out[original] = name
    elif source == "customer_user":
        by_login = _by_folded(wanted)
        for key, first, last in await _lookup(
            session,
            CustomerUser.login,
            [CustomerUser.first_name, CustomerUser.last_name],
            _spellings(by_login),
        ):
            for original in by_login.get(str(key).casefold(), ()):
                out[original] = _person(first, last, original)
    return out
