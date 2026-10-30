"""Every core report source stays inside one table family (no Tiqora/Znuny JOIN, error 1267)."""

from __future__ import annotations

import pytest

from tiqora.plugins.report_points import source_tables
from tiqora.reporting.semantic import ReportSource
from tiqora.reporting.sources import core_report_sources


@pytest.mark.parametrize("source", core_report_sources(), ids=lambda s: s.key)
def test_core_sources_read_one_table_family(source: ReportSource) -> None:
    tables = source_tables(source)
    assert tables
    tiqora = {t for t in tables if t.startswith("tiqora_")}
    assert tiqora in (set(), tables), f"{source.key} mixes table families: {sorted(tables)}"
