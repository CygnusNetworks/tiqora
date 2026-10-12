"""Event data of the Znuny ticket events as the GenericInterface hands it to an invoker (G9).

Znuny's ``EventHandler`` passes the ``Data`` hash of the raising call to
``Kernel::GenericInterface::Event::Handler`` (``Handler.pm``), which adds ``Event`` and queues it.
Tiqora derives the same hash from a ``ticket_history`` row (:class:`TicketEvent`):

* every ticket event: ``TicketID``
* ``ArticleCreate`` / ``TicketAccountTime``: ``ArticleID``
* ``TicketDynamicFieldUpdate_<Name>``: ``FieldName``, ``Value``, ``OldValue`` (parsed from the
  history name ``%%FieldName%%X%%Value%%Y%%OldValue%%Z``; both present as soon as the label is
  there, ``""`` when empty; lossy: the history name holds the rendered value truncated to about
  55 characters with ``[...]``, Znuny passes the raw value) and
  ``UserID`` (the only ticket event whose data carries it)
* ``TicketMerge``: ``TicketID`` is the merged ticket and ``MainTicketID`` the main ticket, both
  from the history name ``%%MergeTN%%MergeID%%MainTN%%MainID`` (the row exists on both tickets)

``OldTicketData`` (the ticket before the change, Znuny adds it for state, queue, SLA and article
events) cannot be rebuilt from ``ticket_history`` and is never present. ``match_invokers`` follows
in Task 13.
"""

from __future__ import annotations

from typing import Any, Final

from tiqora.domain.history_render import split_history_name
from tiqora.plugins.events import TicketEvent

__all__ = ["event_data"]

_DF_PREFIX: Final = "TicketDynamicFieldUpdate_"


def _labelled(values: list[str], label: str) -> str | None:
    """Value after *label* in a ``%%Label%%Value`` pair list (``None`` when the label is absent)."""
    for index in range(0, len(values) - 1, 2):
        if values[index] == label:
            return values[index + 1]
    return None


def _as_id(value: str) -> int | None:
    value = value.strip()
    return int(value) if value.isdigit() else None


def event_data(event: TicketEvent) -> dict[str, Any]:
    """The ``Data`` hash Znuny raises *event* with (see the module docstring)."""
    data: dict[str, Any] = {"TicketID": event.ticket_id}
    name = event.event
    if name in ("ArticleCreate", "TicketAccountTime"):
        if event.article_id:
            data["ArticleID"] = event.article_id
        elif name == "TicketAccountTime":
            data["ArticleID"] = None  # Znuny passes ArticleID => undef (key present)
    elif name.startswith(_DF_PREFIX):
        data["FieldName"] = name[len(_DF_PREFIX) :]
        values = split_history_name(event.name)
        for key in ("Value", "OldValue"):
            found = _labelled(values, key)
            if found is not None:
                data[key] = found
        data["UserID"] = event.create_by
    elif name == "TicketMerge":
        values = split_history_name(event.name)
        merged = _as_id(values[1]) if len(values) > 1 else None
        main = _as_id(values[3]) if len(values) > 3 else None
        if merged is not None:
            data["TicketID"] = merged
        if main is not None:
            data["MainTicketID"] = main
    return data
