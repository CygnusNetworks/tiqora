"""Saved-search and own-filter sources of ticket lists and counters (Task 17).

Both are resolved for the **viewer**: the saved search is looked up with the viewer's
visibility and the compiled statement is scoped to the viewer's queues, never the owner's.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydantic import ValidationError
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.dashboard.providers import ProviderForbidden
from tiqora.dashboard.settings_models import FilterSource, SavedSearchSource
from tiqora.db.legacy.ticket import Ticket
from tiqora.filters import Filter, FilterError
from tiqora.filters.tickets import (
    TicketQueryScope,
    compile_ticket_filter,
    ticket_query_scope,
)
from tiqora.saved_searches.store import SavedSearchNotFound, get_visible

MSG_SEARCH_GONE = "Die Suche gibt es nicht mehr oder ist für Sie nicht freigegeben."
MSG_SEARCH_UNREADABLE = "Diese Suche kann nicht mehr gelesen werden."
MSG_FILTER_INVALID = "Der Filter ist nicht mehr gültig."


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    criteria: Filter
    scope: TicketQueryScope
    counter_key: str


def filter_counter_key(flt: dict[str, object]) -> str:
    digest = hashlib.sha1(  # noqa: S324 - a history key, not a security feature
        json.dumps(flt, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"filter:{digest[:16]}"


async def resolve_source(
    session: AsyncSession,
    viewer: int,
    source: SavedSearchSource | FilterSource,
    queue_ids: Sequence[int] | None = None,
) -> ResolvedSource:
    """``queue_ids`` (the dashboard filter) narrows the viewer's scope; empty = no narrowing.

    Narrowing the scope here makes counters, lists and drills all inherit it.
    """
    if isinstance(source, SavedSearchSource):
        try:
            search, _ = await get_visible(session, source.id, viewer, admin=False)
        except SavedSearchNotFound:
            raise ProviderForbidden(MSG_SEARCH_GONE) from None
        if search.criteria is None:
            raise ProviderForbidden(MSG_SEARCH_UNREADABLE)
        criteria = search.criteria
        key = f"search:{source.id}"
    else:
        try:
            criteria = Filter.model_validate(source.filter)
        except ValidationError:
            raise ProviderForbidden(MSG_FILTER_INVALID) from None
        key = filter_counter_key(source.filter)
    scope = await ticket_query_scope(session, viewer, include_archived=False)
    if queue_ids:
        scope = replace(scope, queue_ids=scope.queue_ids & frozenset(queue_ids))
    return ResolvedSource(criteria, scope, key)


async def compile_for_viewer(
    session: AsyncSession, resolved: ResolvedSource
) -> Select[tuple[Ticket]] | None:
    try:
        return await compile_ticket_filter(session, resolved.criteria, resolved.scope)
    except FilterError:  # e.g. a dynamic field was deleted after the filter was saved
        raise ProviderForbidden(MSG_FILTER_INVALID) from None
