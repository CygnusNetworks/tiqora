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

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final

from tiqora.domain.history_render import split_history_name
from tiqora.gi.conditions import ConditionWarning, condition_check, serialize_config
from tiqora.gi.config import InvokerConfig
from tiqora.gi.jobs import GiInvokePayload
from tiqora.gi.loader import GiWebservice
from tiqora.plugins.events import TicketEvent

__all__ = ["DeepGetLoader", "event_data", "invoker_can_fire", "match_invokers"]

#: ``(ticket_id, article_id) -> TicketDeepGet hash`` (``None`` when the ticket is gone)
DeepGetLoader = Callable[[int, int | None], Awaitable[dict[str, Any] | None]]

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


def invoker_can_fire(invoker: InvokerConfig) -> bool:
    """Supported type and a usable mapping: anything else is skipped (the debugger shows why)."""
    return invoker.supported and invoker.mapping_error is None


def _has_condition(condition: dict[str, Any] | None) -> bool:
    """``IsHashRefWithData(Condition) && IsHashRefWithData(Condition->{Condition})``."""
    if not condition:
        return False
    inner = condition.get("Condition")
    return isinstance(inner, dict) and bool(inner)


async def match_invokers(
    ws_list: Sequence[GiWebservice],
    event: TicketEvent,
    deep_get_loader: DeepGetLoader,
    *,
    warnings: list[ConditionWarning] | None = None,
) -> list[GiInvokePayload]:
    """Payloads for every invoker event entry that listens to *event* (``Handler.pm`` ``Run``).

    Webservices in the given order, invokers sorted by name, entries in config order (an
    invoker listing the same event twice yields two payloads, but both share one dedupe key
    so the second is dropped by ``enqueue_job``; Znuny would fire twice).
    ``deep_get_loader`` is only called for entries with a condition. Condition problems are
    appended to *warnings*.
    """
    payloads: list[GiInvokePayload] = []
    data = event_data(event)
    for ws in ws_list:
        for name in sorted(ws.requester.invokers):
            invoker = ws.requester.invokers[name]
            if not invoker_can_fire(invoker):
                continue
            for entry in invoker.events:
                if entry.event != event.event:
                    continue
                if _has_condition(entry.condition):
                    assert entry.condition is not None
                    article_id = data.get("ArticleID")
                    # Article events need the article for DataGet; without one Znuny logs an
                    # error, gets no data and skips the check.
                    wants_article = event.event in ("ArticleCreate", "TicketAccountTime")
                    deep = (
                        None
                        if wants_article and not article_id
                        else await deep_get_loader(int(data["TicketID"]), article_id)
                    )
                    if deep:
                        matched = condition_check(
                            entry.condition,
                            serialize_config(deep),
                            invoker=name,
                            event=event.event,
                            warnings=warnings,
                        )
                        if not matched:
                            continue
                payloads.append(
                    GiInvokePayload(
                        webservice_id=ws.id,
                        invoker=name,
                        data={**data, "Event": event.event},
                        asynchronous=entry.asynchronous,
                        origin="event",
                        history_id=event.id,
                    )
                )
    return payloads
