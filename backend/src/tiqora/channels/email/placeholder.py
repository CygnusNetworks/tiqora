"""``<OTRS_...>`` placeholder expansion for auto-response templates.

Pragmatic subset of ``Kernel/System/TemplateGenerator.pm``'s ``_Replace``: the
tags Tiqora's auto-response path needs (``TICKET_*``, ``QUEUE``,
``CUSTOMER_SUBJECT``, ``CUSTOMER_EMAIL[n]``, ``CONFIG_*``). Unsupported tags
(``OTRS_AGENT_*``, ``OTRS_CUSTOMER_BODY``, ``OTRS_CUSTOMER_DATA_*``,
notification-only tags, DynamicField tags) are left verbatim in the text and
are documented in ``docs/parallel-operation.md`` → Uncertainties.
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.sysconfig import SysConfig

_TAG_RE = re.compile(r"<OTRS_([A-Z0-9_]+)(?:\[(\d+)\])?>")


async def expand_placeholders(
    session: AsyncSession,
    sysconfig: SysConfig,
    text: str,
    *,
    ticket: dict[str, str],
    queue_name: str,
    customer_subject: str,
    customer_email_lines: list[str],
) -> str:
    """Expand the supported ``<OTRS_...>`` tag subset in *text*."""

    async def _resolve(match: re.Match[str]) -> str:
        tag = match.group(1)
        n = match.group(2)

        if tag.startswith("TICKET_"):
            key = tag[len("TICKET_") :]
            return ticket.get(key, "")
        if tag == "QUEUE":
            return queue_name
        if tag == "CUSTOMER_SUBJECT":
            return customer_subject
        if tag.startswith("CUSTOMER_EMAIL"):
            count = int(n) if n else len(customer_email_lines)
            return "\n".join(customer_email_lines[:count])
        if tag.startswith("CONFIG_"):
            setting_name = tag[len("CONFIG_") :].replace("_", "::")
            value = await sysconfig.get(setting_name)
            return str(value) if value is not None else ""
        return match.group(0)

    # re.sub has no async support; resolve sequentially.
    result = text
    offset = 0
    for match in list(_TAG_RE.finditer(text)):
        replacement = await _resolve(match)
        start, end = match.span()
        result = result[: start + offset] + replacement + result[end + offset :]
        offset += len(replacement) - (end - start)
    return result
