"""The CMDB sources compile cleanly under ``-W error`` (no invalid escape sequences)."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from tiqora.api.v1.cmdb.attachments import content_disposition

SRC = Path(__file__).resolve().parents[1] / "src" / "tiqora"
FILES = sorted(
    [*(SRC / "cmdb").rglob("*.py"), *(SRC / "api" / "v1" / "cmdb").rglob("*.py")],
    key=str,
)


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_compiles_without_warnings(path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(path.read_text("utf-8"), str(path), "exec")


def test_content_disposition_drops_quote_backslash_and_semicolon() -> None:
    header = content_disposition('a"b\\c;d.txt')
    assert header.startswith('attachment; filename="abcd.txt";')


def test_transfer_modules_share_the_report_formula_guard() -> None:
    from tiqora.cmdb.transfer import bulk_job, export_job, import_job, readers
    from tiqora.reporting.export import FORMULA_PREFIXES

    for mod in (bulk_job, export_job, import_job, readers):
        assert mod.FORMULA_PREFIXES is FORMULA_PREFIXES
        assert not hasattr(mod, "_FORMULA_PREFIXES")
