"""Ticket CSV export as a background job."""

from __future__ import annotations

import csv
import io
from typing import Any

from pydantic import BaseModel

from tiqora.domain.ticket_csv import CSV_HEADER, EchoWriter, ticket_csv_row
from tiqora.domain.ticket_service import TicketService
from tiqora.filters import Filter, FilterError
from tiqora.filters.tickets import compile_ticket_filter, count_tickets, ticket_query_scope
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.registry import JobHandler

PROGRESS_EVERY_ROWS = 500


class TicketExportPayload(BaseModel):
    user_id: int
    filter: Filter
    include_archived: bool = False
    sort: str = "age"
    order: str = "desc"


async def run_ticket_export(ctx: JobContext, payload: TicketExportPayload) -> dict[str, Any]:
    async with ctx.session_factory() as session:
        # Permission is evaluated now, not when the job was queued.
        scope = await ticket_query_scope(
            session, payload.user_id, include_archived=payload.include_archived
        )
        try:
            total = await count_tickets(session, payload.filter, scope)
            stmt = await compile_ticket_filter(session, payload.filter, scope)
        except FilterError as exc:
            raise JobFailed(f"Der Filter ist nicht mehr gültig: {exc.message}") from exc
        await ctx.progress(0, total, "Tickets werden gelesen …")
        out = io.BytesIO()
        # UTF-8 BOM first, so Excel opens the file as UTF-8.
        out.write(b"\xef\xbb\xbf")
        writer = csv.writer(EchoWriter(), delimiter=";")
        out.write(writer.writerow(CSV_HEADER).encode("utf-8"))
        rows = 0
        if stmt is not None:
            async for item in TicketService(session).iter_ticket_items(
                stmt, sort=payload.sort, order=payload.order
            ):
                out.write(writer.writerow(ticket_csv_row(item)).encode("utf-8"))
                rows += 1
                if rows % PROGRESS_EVERY_ROWS == 0:
                    await ctx.progress(rows, total, "Tickets werden gelesen …")
    await ctx.progress(rows, rows, "Datei wird gespeichert …")
    await ctx.add_file("tickets.csv", "text/csv; charset=utf-8", out.getvalue())
    return {"rows": rows}


TICKET_EXPORT_JOB = JobHandler(
    kind="core.ticket_export",
    payload_model=TicketExportPayload,
    run=run_ticket_export,
    title_key="jobs.kinds.core.ticket_export",
)
