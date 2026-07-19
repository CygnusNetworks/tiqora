"""Follow-up detection — port of ``Kernel::System::PostMaster::FollowUpCheck::{Subject,References}``
and ``Kernel::System::Ticket::Number::*::GetTNByString`` / ``Ticket.pm::TicketCheckNumber``.

Detection order (Znuny default active ``PostMaster::CheckFollowUpModule`` chain,
``Kernel/Config/Files/XML/Ticket.xml``): ``0100-Subject`` then
``0200-References`` (Body/Attachments/RawEmail/ExternalTicketNumberRecognition
are registered but *not valid* by default — out of scope for Phase 4a).
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.sysconfig import SysConfig
from tiqora.znuny.ticket_number import generator_short_name

_MERGE_NAME_RE = re.compile(r"^.*%%\d+?%%(\d+?)$")


def get_tn_by_string(
    subject: str,
    *,
    hook: str,
    hook_divider: str,
    generator: str,
    system_id: str,
    check_system_id: bool = False,
    min_counter_size: int = 5,
) -> str | None:
    """Extract a ticket number from *subject* (or any free string).

    Mirrors ``AutoIncrement.pm`` / ``Date.pm`` / ``DateChecksum.pm``
    ``GetTNByString``: two accepted shapes —
    ``<Hook><HookDivider><digits>`` and ``<Hook>: <digits>`` (0-2 spaces).
    """
    if not subject:
        return None

    sid = system_id if check_system_id else ""
    short = generator_short_name(generator)

    if short == "DateChecksum" or short == "Date":
        digit_pattern = rf"\d{{8}}{re.escape(sid)}\d{{4,40}}"
    else:
        # AutoIncrement (and unknown/Random fallback — Random has no TN regex
        # in Znuny either, so this best-effort pattern still applies).
        max_size = min_counter_size + 5
        digit_pattern = rf"{re.escape(sid)}\d{{{min_counter_size},{max_size}}}"

    hooked = re.escape(hook) + re.escape(hook_divider)
    match = re.search(hooked + rf"({digit_pattern})", subject, re.IGNORECASE)
    if match:
        return match.group(1)

    match = re.search(re.escape(hook) + rf":\s{{0,2}}({digit_pattern})", subject, re.IGNORECASE)
    if match:
        return match.group(1)

    return None


async def ticket_check_number(session: AsyncSession, tn: str) -> int | None:
    """Return the ticket id for *tn*, resolving through up to 10 merge hops."""
    row = (await session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})).first()
    if row is None:
        return None
    ticket_id = int(row[0])

    for _ in range(10):
        state_row = (
            await session.execute(
                text(
                    "SELECT tst.name FROM ticket t"
                    " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                    " WHERE t.id = :tid"
                ),
                {"tid": ticket_id},
            )
        ).first()
        if state_row is None or str(state_row[0]).lower() != "merged":
            return ticket_id

        hist_rows = (
            await session.execute(
                text(
                    "SELECT th.name FROM ticket_history th"
                    " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
                    " WHERE th.ticket_id = :tid AND tht.name = 'Merged'"
                    " ORDER BY th.id DESC"
                ),
                {"tid": ticket_id},
            )
        ).all()
        merged_into: int | None = None
        for (name,) in hist_rows:
            m = _MERGE_NAME_RE.match(str(name))
            if m:
                merged_into = int(m.group(1))
                break
        if merged_into is None:
            return ticket_id
        ticket_id = merged_into

    return None


async def find_ticket_by_references(session: AsyncSession, references: list[str]) -> int | None:
    """Port of ``FollowUpCheck::References`` — walk References (incl. In-Reply-To)
    looking up each Message-ID against ``article_data_mime.a_message_id``."""
    for reference in references:
        row = (
            await session.execute(
                text(
                    "SELECT a.ticket_id FROM article_data_mime adm"
                    " JOIN article a ON a.id = adm.article_id"
                    " WHERE adm.a_message_id = :mid LIMIT 1"
                ),
                {"mid": f"<{reference}>"},
            )
        ).first()
        if row is not None:
            return int(row[0])
    return None


async def detect_followup(
    session: AsyncSession,
    sysconfig: SysConfig,
    *,
    subject: str,
    references: list[str],
) -> tuple[str, int] | None:
    """Return ``(tn, ticket_id)`` for the first matching follow-up check, or None."""
    hook = await sysconfig.ticket_hook()
    hook_divider = await sysconfig.ticket_hook_divider()
    generator = await sysconfig.ticket_number_generator()
    system_id = await sysconfig.system_id()
    check_system_id = bool(await sysconfig.get("Ticket::NumberGenerator::CheckSystemID", False))
    min_counter_size = int(
        await sysconfig.get("Ticket::NumberGenerator::AutoIncrement::MinCounterSize", 5) or 5
    )

    tn = get_tn_by_string(
        subject,
        hook=hook,
        hook_divider=hook_divider,
        generator=generator,
        system_id=system_id,
        check_system_id=check_system_id,
        min_counter_size=min_counter_size,
    )
    if tn:
        ticket_id = await ticket_check_number(session, tn)
        if ticket_id is not None:
            return tn, ticket_id

    if references:
        ticket_id = await find_ticket_by_references(session, references)
        if ticket_id is not None:
            row = (
                await session.execute(
                    text("SELECT tn FROM ticket WHERE id = :tid"), {"tid": ticket_id}
                )
            ).first()
            if row is not None:
                return str(row[0]), ticket_id

    return None
