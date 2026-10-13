"""Report sources and built-in reports from plugins (D15).

A plugin source reads either Znuny tables or its own ``tqp_<short_id>_*`` tables, never
``tiqora_*`` tables and never a mix of both (prod MariaDB mixes ``general_ci``/``unicode_ci``,
so a JOIN across the two worlds ends in error 1267). Registration checks this by compiling a
probe statement that contains EVERY expression the source can put into a report query (scope
column, dimensions, measures, filter-only fields, base filter, FROM clause) and walking it for
tables. The compiler's queue scope (``scope_column IN viewer queues``) is unchanged and applies
to plugin sources like to core ones.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.sql.expression import Alias, ColumnClause, TableClause
from sqlalchemy.sql.visitors import iterate

from tiqora.jobs.service import utc_now
from tiqora.reporting.semantic import CompileContext, ReportSource

__all__ = ["MIXED_TABLES_MESSAGE", "check_plugin_source", "source_tables"]

MIXED_TABLES_MESSAGE: Final = (
    "Berichtsquelle darf nur Znuny-Tabellen oder eigene Tabellen (tqp_<kurzname>_) lesen, "
    "nicht gemischt"
)


def _probe(source: ReportSource) -> Any:
    ctx = CompileContext(now=utc_now(), time_zone="UTC", tz=(), near_miss_percent=0, window=None)
    exprs: list[Any] = [source.scope_column(ctx)]
    exprs.extend(d.expr(ctx) for d in source.dimensions)
    for measure in source.measures:
        exprs.extend(fn(ctx) for fn in (measure.expr, measure.condition, measure.denominator) if fn)
    exprs.extend(f.expr(ctx) for f in source.filter_fields)
    if source.archive_exclusion is not None:
        exprs.append(source.archive_exclusion(ctx))
    return select(*exprs).select_from(source.from_clause(ctx)).where(*source.base_where(ctx))


def source_tables(source: ReportSource) -> set[str]:
    """Names of all tables (aliases resolved to the real table) a source's queries touch."""
    names: set[str] = set()
    for element in iterate(_probe(source)):
        # a column does not traverse into its table: a table named only in an ON clause or in
        # an expression (never in FROM) is found through the column
        owner = element.table if isinstance(element, ColumnClause) else element
        while isinstance(owner, Alias):
            owner = owner.element
        if isinstance(owner, TableClause):
            names.add(owner.name)
    return names


def check_plugin_source(source: ReportSource, short_id: str) -> None:
    """Raise ``RegistrationRejected`` unless the tables are all Znuny's or all the plugin's own."""
    from tiqora.plugins.registrar import RegistrationRejected  # noqa: PLC0415 - import cycle

    try:
        tables = source_tables(source)
    except Exception as exc:
        raise RegistrationRejected(
            f"Berichtsquelle „{source.key}“ konnte nicht geprüft werden: {type(exc).__name__}"
        ) from exc
    own_prefix = f"tqp_{short_id}_"
    own = {t for t in tables if t.startswith(own_prefix)}
    foreign = {t for t in tables if t.startswith(("tiqora_", "tqp_")) and t not in own}
    znuny = tables - own - foreign
    if foreign or (own and znuny):
        raise RegistrationRejected(MIXED_TABLES_MESSAGE)
