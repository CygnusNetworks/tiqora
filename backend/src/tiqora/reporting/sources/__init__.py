"""Core report sources."""

from __future__ import annotations

from tiqora.reporting.semantic import ReportSource


def core_report_sources() -> list[ReportSource]:
    from tiqora.reporting.sources.accounted_time import accounted_time_source
    from tiqora.reporting.sources.daily_snapshot import daily_snapshot_source
    from tiqora.reporting.sources.open_tickets import open_tickets_source
    from tiqora.reporting.sources.ticket_events import ticket_events_source
    from tiqora.reporting.sources.tickets import tickets_source

    return [
        tickets_source(),
        ticket_events_source(),
        daily_snapshot_source(),
        accounted_time_source(),
        open_tickets_source(),
    ]
