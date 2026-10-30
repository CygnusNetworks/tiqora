"""Core report sources."""

from __future__ import annotations

from tiqora.reporting.semantic import ReportSource


def core_report_sources() -> list[ReportSource]:
    from tiqora.reporting.sources.accounted_time import accounted_time_source
    from tiqora.reporting.sources.change_events import change_events_source
    from tiqora.reporting.sources.changes import changes_source
    from tiqora.reporting.sources.ci_tickets import ci_tickets_source
    from tiqora.reporting.sources.config_items import config_items_source
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
        config_items_source(),
        changes_source(),
        change_events_source(),
        ci_tickets_source(),
    ]
