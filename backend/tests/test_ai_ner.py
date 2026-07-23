"""Unit tests for tiqora.ai.ner (spaCy NER person-name extraction, plan §3.7
gap). No DB, no network — loads the two bundled small spaCy models
in-process (they ship as direct dependencies, see pyproject.toml).
"""

from __future__ import annotations

import time

from tiqora.ai.ner import extract_person_names


def test_extract_person_names_german_and_english_in_one_call() -> None:
    text = (
        "Marie Knoblich und Rasmus Müller haben sich getroffen. "
        "John Smith wrote to us yesterday about the invoice."
    )
    names = extract_person_names(text)
    assert "Marie Knoblich" in names
    assert "Rasmus Müller" in names
    assert "John Smith" in names


def test_extract_person_names_dedupes_case_insensitively() -> None:
    text = "Anna Schmidt called. Later, anna schmidt called again. ANNA SCHMIDT confirmed."
    names = extract_person_names(text)
    lowered = [n.lower() for n in names]
    assert lowered.count("anna schmidt") == 1


def test_extract_person_names_drops_short_and_digit_containing_entries() -> None:
    # A synthetic entity list is awkward to force through spaCy's NER
    # directly, so this exercises the actual filtering by picking inputs
    # that would otherwise reasonably be tagged as short/numeric "names".
    text = "Ticket 12345 was opened. Case ID A1 was opened. Xi met with Bo."
    names = extract_person_names(text)
    for name in names:
        assert len(name) >= 3
        assert not any(c.isdigit() for c in name)


def test_extract_person_names_empty_text_returns_empty_list() -> None:
    assert extract_person_names("") == []
    assert extract_person_names(None) == []  # type: ignore[arg-type]


def test_extract_person_names_latency_on_large_text(capsys: object) -> None:
    """Not a hard perf assertion (CI hardware varies) — measures and prints
    wall-clock for a ~50k-char text so the report captures a real number."""
    paragraph = (
        "Marie Knoblich schrieb an John Smith bezueglich der offenen Rechnung. "
        "Rasmus Mueller bestaetigte den Empfang der Unterlagen und informierte "
        "das Team ueber den aktuellen Stand der Bearbeitung. "
    )
    text = paragraph * (50_000 // len(paragraph) + 1)
    assert len(text) >= 50_000

    start = time.perf_counter()
    names = extract_person_names(text)
    elapsed = time.perf_counter() - start
    print(f"extract_person_names on {len(text)} chars took {elapsed:.3f}s")
    assert names  # sanity: still finds names in the large text
