"""Names for report sources and built-in reports from plugins (``registry.report_source``).

A source (``plugin.<short_id>.<name>``) describes a FROM clause with its dimensions and measures
as SQLAlchemy expressions. It reads either Znuny tables or the plugin's own ``tqp_<short_id>_*``
tables, never ``tiqora_*`` tables and never a mix of both. Every label key starts with
``plugin.<plugin id>:``. ``scope_column`` is the queue id column; the report engine restricts
every run to the queues the viewer may read. Built-in reports (``registry.builtin_report``) show
up in the report catalogue under ``plugin.<plugin id>.<name>``; their title and description are
``plugin.<plugin id>:reports.<name>.title`` / ``.description`` in the plugin's i18n.
"""

from __future__ import annotations

from tiqora.reporting.catalogue import ReportCategory
from tiqora.reporting.definition import ReportDefinition
from tiqora.reporting.semantic import (
    CompileContext,
    Dimension,
    FilterOnlyField,
    Measure,
    ReportSource,
)

__all__ = [
    "CompileContext",
    "Dimension",
    "FilterOnlyField",
    "Measure",
    "ReportCategory",
    "ReportDefinition",
    "ReportSource",
]
