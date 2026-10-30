"""Every core report source stays inside one table family (no Tiqora/Znuny JOIN, error 1267)."""

from __future__ import annotations

import pytest
from sqlalchemy import Integer, column, table

from tiqora.plugins.registrar import RegistrationRejected
from tiqora.plugins.report_points import check_plugin_source, source_tables
from tiqora.reporting.itsm_roles import itsm_role_dim
from tiqora.reporting.semantic import ReportSource
from tiqora.reporting.sources import core_report_sources
from tiqora.reporting.sources._common import col, measure


@pytest.mark.parametrize("source", core_report_sources(), ids=lambda s: s.key)
def test_core_sources_read_one_table_family(source: ReportSource) -> None:
    tables = source_tables(source)
    assert tables
    tiqora = {t for t in tables if t.startswith("tiqora_")}
    assert tiqora in (set(), tables), f"{source.key} mixes table families: {sorted(tables)}"


def test_probe_sees_the_type_column_of_a_role_dimension() -> None:
    """The role CASE only names its column with roles configured; the probe must compile that."""
    znuny = table("ist972_znuny", column("queue_id", Integer))
    tiqora = table("tiqora_ist972", column("type_id", Integer))
    source = ReportSource(
        key="plugin.points.ist972",
        label_key="reports.sources.ist972",
        from_clause=lambda ctx: znuny,
        scope_column=col(znuny, "queue_id"),
        dimensions=(itsm_role_dim("itsm_role", col(tiqora, "type_id")),),
        measures=(measure("rows", "count"),),
        requires_facts=False,
    )
    assert source_tables(source) == {"ist972_znuny", "tiqora_ist972"}
    with pytest.raises(RegistrationRejected):
        check_plugin_source(source, "points")
