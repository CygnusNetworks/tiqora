"""``Ticket::Generic`` invoker (G9, G10).

Port of ``Kernel/GenericInterface/Invoker/Ticket/Generic.pm`` ``PrepareRequest`` (response
handling follows in Task 10).
"""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.gi.config import InvokerConfig
from tiqora.gi.deep_get import (
    base64_deep_encode,
    data_structure_remove_elements,
    ticket_deep_get,
)
from tiqora.util.perl import perl_truthy
from tiqora.znuny.sysconfig import SysConfig

__all__ = ["prepare_request"]

_OMITTED: Final = "GenericInterface::Invoker::Ticket::Generic::PrepareRequest::OmittedFields"
_BASE64: Final = "GenericInterface::Invoker::Ticket::Generic::PrepareRequest::Base64EncodedFields"

_MONTH_ABBR: Final = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_DAY_ABBR: Final = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


async def _field_list(sysconfig: SysConfig, setting: str, invoker_name: str) -> list[str]:
    configured = await sysconfig.get(setting)
    if not isinstance(configured, dict):
        return []
    value = configured.get(invoker_name)
    if value is None or not str(value):
        return []
    # Perl: split /\s*;\s*/ (trailing empty fields are dropped)
    parts = re.split(r"\s*;\s*", str(value))
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _date_time_parts(seconds: int, zone: ZoneInfo, now: datetime) -> dict[str, Any]:
    """``Kernel::System::DateTime->Get()`` of *now* plus *seconds*."""
    moment = (now + timedelta(seconds=seconds)).astimezone(zone)
    return {
        "Year": moment.year,
        "Month": moment.month,
        "MonthAbbr": _MONTH_ABBR[moment.month - 1],
        "Day": moment.day,
        "Hour": moment.hour,
        "Minute": moment.minute,
        "Second": moment.second,
        "DayOfWeek": moment.isoweekday(),
        "DayAbbr": _DAY_ABBR[moment.weekday()],
        "TimeZone": zone.key,
    }


async def prepare_request(
    session: AsyncSession,
    *,
    invoker_name: str,
    invoker: InvokerConfig,
    data: dict[str, Any],
    sysconfig: SysConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``{"Ticket": <TicketDeepGet or {}>, "Event": <data>}`` after the omit/base64 lists.

    *data* is the event payload (``TicketID``, optional ``ArticleID``, ``GetAllArticleAttachments``
    and the ``Event`` name the requester added); it is copied, never changed. The SysConfig lists
    ``...PrepareRequest::OmittedFields`` and ``::Base64EncodedFields`` (hash: invoker name -> paths
    separated by ``;``) are applied to the ticket AND the event payload; other invoker names are
    untouched.
    """
    event = copy.deepcopy(data)
    get_all = (
        perl_truthy(event.get("GetAllArticleAttachments")) or invoker.get_all_article_attachments
    )

    ticket: dict[str, Any] = {}
    ticket_id = event.get("TicketID")
    if ticket_id is not None and perl_truthy(ticket_id):
        article_id = event.get("ArticleID")
        deep = await ticket_deep_get(
            session,
            int(ticket_id),
            article_id=int(article_id)
            if article_id is not None and perl_truthy(article_id)
            else None,
            all_attachments=get_all,
            sysconfig=sysconfig,
        )
        ticket = deep or {}
        until = ticket.get("UntilTime")
        if until:
            zone_name = await sysconfig.otrs_time_zone()
            try:
                zone = ZoneInfo(zone_name or "UTC")
            except (KeyError, ValueError, OSError):
                zone = ZoneInfo("UTC")
            ticket["UntilTimeDateTimeParts"] = _date_time_parts(
                int(until), zone, now or datetime.now(UTC)
            )

    omitted = await _field_list(sysconfig, _OMITTED, invoker_name)
    if omitted:
        data_structure_remove_elements(ticket, omitted)
        data_structure_remove_elements(event, omitted)

    encoded = await _field_list(sysconfig, _BASE64, invoker_name)
    if encoded:
        base64_deep_encode(ticket, encoded)
        base64_deep_encode(event, encoded)

    return {"Ticket": ticket, "Event": event}
