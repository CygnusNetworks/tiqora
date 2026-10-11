"""PDF writer: HTML escaping, truncation, extraction, no network (pure, no database)."""

from __future__ import annotations

import io
import os
import time
from datetime import datetime
from typing import Any

import pytest
from pypdf import PdfReader

import tiqora.reporting.pdf as pdf_module
from tests.test_report_texts import col, make_result
from tiqora.reporting.definition import Visualization
from tiqora.reporting.pdf import (
    MAX_TOTAL_ROWS,
    PdfMeta,
    ReportSection,
    render_report_html,
    write_report_pdf,
)
from tiqora.reporting.texts import load_texts, server_export_labels

pdf_required = os.environ.get("TIQORA_REQUIRE_PDF") == "1"
try:
    import weasyprint  # noqa: F401

    HAVE_PDF = True
except (ImportError, OSError):
    HAVE_PDF = False
needs_pdf = pytest.mark.skipif(
    not HAVE_PDF and not pdf_required, reason="WeasyPrint native libs missing"
)


def _section(
    rows: list[list[Any]] | None = None,
    *,
    title: str = "rep52 Volumen",
    truncated: bool = False,
    viz: str = "bar",
    row_limit: int = 1000,
) -> ReportSection:
    result = make_result(
        columns=[col("queue", "dimension", "id"), col("tickets", "measure", "count")],
        rows=rows if rows is not None else [[4, 12], [5, 3]],
        labels={"queue": {"4": "rep52 Queue A", "5": "rep52 Queue B"}},
        truncated=truncated,
    )
    texts = load_texts("de")
    return ReportSection(
        title=title,
        result=result,
        visualization=Visualization(type=viz),  # type: ignore[arg-type]
        labels=server_export_labels(result, texts),
        info_lines=("Zeitraum: 01.09.2026 – 30.09.2026",),
        row_limit=row_limit,
    )


META = PdfMeta(
    title="rep52 Zeitplan", generated_at_local=datetime(2026, 10, 11, 7, 0), owner_line=None
)


def _text(data: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def test_html_escapes_everything() -> None:
    evil = '<img src="http://evil.test/x"><link href="file:///etc/passwd">{{owner}}'
    html_out = render_report_html([_section(title=evil)], META, load_texts("de"))
    assert "<img" not in html_out and '<link href="file' not in html_out
    assert "&lt;img" in html_out
    assert "{{owner}}" in html_out  # literal, never substituted


def test_html_escapes_labels_and_meta() -> None:
    evil = "<script>alert(1)</script>"
    meta = PdfMeta(title=evil, generated_at_local=META.generated_at_local, owner_line=evil)
    section = _section()
    result = make_result(
        columns=[col("queue", "dimension", "id"), col("tickets", "measure", "count")],
        rows=[[4, 1]],
        labels={"queue": {"4": evil}},
    )
    section = ReportSection(
        title=evil,
        result=result,
        visualization=Visualization(type="bar"),
        labels=server_export_labels(result, load_texts("de")),
        info_lines=(evil,),
        row_limit=10,
    )
    html_out = render_report_html([section], meta, load_texts("de"))
    assert "<script" not in html_out and "&lt;script&gt;" in html_out


def test_html_truncation_and_empty_notes() -> None:
    texts = load_texts("de")
    many = _section(rows=[[i, 1] for i in range(1500)])
    out = render_report_html([many], META, texts)
    assert "nur die ersten 1.000 Zeilen" in out
    assert out.count("<tr>") == 1000 + 1  # header row + 1000 data rows
    assert texts.fixed("pdf.empty") in render_report_html([_section(rows=[])], META, texts)


def test_html_truncated_flag_shows_note_without_extra_rows() -> None:
    out = render_report_html([_section(truncated=True)], META, load_texts("de"))
    assert "nur die ersten 2 Zeilen" in out


def test_total_row_budget_across_sections() -> None:
    sections = [_section(rows=[[i, 1] for i in range(3000)], row_limit=5000) for _ in range(2)]
    out = render_report_html(sections, META, load_texts("de"))
    assert out.count("<tr>") == MAX_TOTAL_ROWS + 2  # two header rows
    assert "nur die ersten 2.000 Zeilen" in out  # the second section got what was left


def test_html_has_footer_and_german_formats() -> None:
    out = render_report_html([_section()], META, load_texts("de"))
    assert "counter(page)" in out and "counter(pages)" in out and "Seite " in out
    assert "Erstellt am 11.10.2026 um 07:00" in out
    en = render_report_html([_section()], META, load_texts("en"))
    assert "Generated on 10/11/2026 at 07:00" in en and 'lang="en"' in en


@needs_pdf
def test_pdf_text_extraction() -> None:
    data = write_report_pdf(
        [_section(), _section(title="rep52 Zweiter Bericht", viz="table")], META, load_texts("de")
    )
    assert data.startswith(b"%PDF")
    text = _text(data)
    assert "rep52 Volumen" in text and "rep52 Zweiter Bericht" in text
    assert "rep52 Queue A" in text
    assert "Seite 1 von" in text


@needs_pdf
def test_pdf_escapes_and_fetches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = pdf_module._refuse_fetch

    def spy(url: str, *a: object, **k: object) -> Any:
        calls.append(url)
        return real(url, *a, **k)

    monkeypatch.setattr(pdf_module, "_refuse_fetch", spy)
    evil = (
        '<img src="http://evil.test/x"><link rel="stylesheet" href="file:///etc/passwd">'
        + "W" * 5000
    )
    data = write_report_pdf([_section(title=evil)], META, load_texts("de"))
    assert "<img" in _text(data)  # shown as literal text
    assert calls == []  # escaped markup never asked the fetcher for anything


@needs_pdf
def test_fetcher_is_really_wired_into_weasyprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw (unescaped) markup does reach the fetcher, and the refusal is not fatal."""
    calls: list[str] = []
    real = pdf_module._refuse_fetch

    def spy(url: str, *a: object, **k: object) -> Any:
        calls.append(url)
        return real(url, *a, **k)

    monkeypatch.setattr(pdf_module, "_refuse_fetch", spy)
    data = pdf_module._render_pdf(
        '<html><body><p>rep52 ok</p><img src="http://evil.test/x">'
        '<img src="file:///etc/hostname"></body></html>'
    )
    assert calls == ["http://evil.test/x", "file:///etc/hostname"]
    assert "rep52 ok" in _text(data)


def test_refuse_fetch_blocks_all() -> None:
    for url in ("http://evil.test/", "https://evil.test/", "file:///etc/passwd", "data:text/css,x"):
        with pytest.raises(ValueError):
            pdf_module._refuse_fetch(url)


def test_pdf_unavailable_when_weasyprint_cannot_load(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fail(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "weasyprint":
            raise OSError("cannot load library 'libgobject-2.0-0'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail)
    with pytest.raises(pdf_module.PdfUnavailable):
        write_report_pdf([_section()], META, load_texts("de"))


@needs_pdf
def test_render_cost_of_ten_sections_with_1000_rows() -> None:
    """Measured for the commit body (ruling); loose bound so a slow runner does not flake."""
    import resource

    sections = [
        _section(rows=[[i, i % 7] for i in range(1000)], title=f"rep52 Bericht {n}", viz="table")
        for n in range(5)
    ] * 2
    start = time.perf_counter()
    data = write_report_pdf(sections, META, load_texts("de"))
    seconds = time.perf_counter() - start
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"PDF 10x1000 rows: {seconds:.1f}s, {len(data) // 1024} KiB, peak rss {peak}")
    assert data.startswith(b"%PDF") and seconds < 120
