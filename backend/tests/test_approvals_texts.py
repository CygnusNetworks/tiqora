"""Mail texts of the approval engine (A11, Task 11): pure, no database.

Seed band 971500-971599 belongs to Task 11; the text tests seed nothing.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from tiqora.approvals import texts

LOCALES = ("de", "en")
RICH = {
    "title": "Zugriff appr97",
    "requester": "Anna Appr",
    "rule": "Es genügt eine Freigabe",
    "deadline": "27.10.2026 14:30",
    "description": "Bitte prüfen.",
    "lines": ["Dienst: Mail", "Risiko: niedrig"],
    "link": "https://tiqora.example.org/approve#t=abc",
    "expires": "03.11.2026 14:30",
    "inbox_url": "https://tiqora.example.org/agent/approvals",
    "age": "2 Tagen",
    "name": "Berta Appr",
    "status": "approved",
    "substitute_for": "Berta Appr",
}


def test_texts_cover_every_kind_in_de_and_en() -> None:
    assert texts.KINDS == (
        "request",
        "reminder",
        "escalation",
        "overdue",
        "decided",
        "cancelled",
        "forwarded",
    )
    for kind in texts.KINDS:
        for locale in LOCALES:
            subject, body = texts.render(kind, locale, **RICH)
            assert subject.strip() and "\n" not in subject, (kind, locale)
            assert RICH["title"] in body, (kind, locale)
            assert not re.search(r"\{\w+\}", subject + body), (kind, locale)
    assert texts.render("request", "de", **RICH)[0] == "Freigabe erbeten: Zugriff appr97"
    assert texts.render("request", "en", **RICH)[0] == "Approval requested: Zugriff appr97"
    assert texts.render("decided", "de", **{**RICH, "status": "rejected"})[0] == (
        "Freigabe abgelehnt: Zugriff appr97"
    )
    assert texts.render("decided", "de", **RICH)[0] == "Freigabe erteilt: Zugriff appr97"
    assert texts.render("cancelled", "de", **RICH)[0] == "Freigabe zurückgezogen: Zugriff appr97"
    assert texts.render("overdue", "de", **RICH)[0] == "Frist überschritten: Zugriff appr97"
    assert texts.render("reminder", "de", **RICH)[0] == "Erinnerung: Freigabe wartet seit 2 Tagen"
    assert texts.render("forwarded", "de", **RICH)[0] == (
        "Freigabe an Sie weitergeleitet von Berta Appr"
    )
    assert texts.render("escalation", "de", **RICH)[0] == (
        "Eskalation: Sie können über diese Freigabe jetzt allein entscheiden"
    )


def test_render_leaves_out_missing_optional_parts() -> None:
    subject, body = texts.render("request", "de", title="Nur Titel", rule="Es genügt eine Freigabe")
    assert subject == "Freigabe erbeten: Nur Titel"
    for absent in ("Frist", "Freigeben oder ablehnen", "gültig", "None", "Vertretung"):
        assert absent not in body
    _, with_link = texts.render("request", "de", **RICH)
    assert "Freigeben oder ablehnen: https://tiqora.example.org/approve#t=abc" in with_link
    assert "Der Link ist bis 03.11.2026 14:30 gültig und funktioniert einmal." in with_link
    assert "Frist: 27.10.2026 14:30" in with_link
    assert "Dienst: Mail" in with_link and "Bitte prüfen." in with_link
    assert "in Vertretung für Berta Appr" in with_link


def test_german_texts_use_sie() -> None:
    forbidden = re.compile(r"(^|\W)(du|dein\w*|dich|dir)(\W|$)", re.IGNORECASE)
    seen = 0
    for kind in texts.KINDS:
        for status in ("approved", "rejected"):
            subject, body = texts.render(kind, "de", **{**RICH, "status": status})
            assert forbidden.search(subject + "\n" + body) is None, (kind, subject, body)
            seen += 1
    assert seen == 14
    assert "Sie" in texts.render("request", "de", **RICH)[1]


def test_rule_texts() -> None:
    assert texts.rule_text("any", None, "de") == "Es genügt eine Freigabe"
    assert texts.rule_text("all", None, "de") == "Alle Freigeber müssen zustimmen"
    assert texts.rule_text("quorum", 2, "de") == "Mindestens 2 Freigaben"
    assert texts.rule_text("majority", None, "de") == "Mehrheit der Freigeber"
    assert texts.rule_text("quorum", 3, "en") == "At least 3 approvals"
    assert texts.rule_text("any", None, "en") == "One approval is enough"
    with pytest.raises(KeyError):
        texts.rule_text("unknown", None, "de")


def test_locale_and_age_helpers() -> None:
    assert texts.text_locale("de") == "de"
    assert texts.text_locale("de_DE") == "de"
    assert texts.text_locale("en_GB") == "en"
    assert texts.text_locale("en") == "en"
    assert texts.text_locale("fr") == "de"  # unsupported: the bundled default
    assert texts.text_locale(None) == "de"
    assert texts.text_locale("") == "de"
    assert texts.age_text(timedelta(hours=3), "de") == "3 Stunden"
    assert texts.age_text(timedelta(hours=1), "de") == "1 Stunde"
    assert texts.age_text(timedelta(days=2, hours=5), "de") == "2 Tagen"
    assert texts.age_text(timedelta(days=1), "de") == "1 Tag"
    assert texts.age_text(timedelta(minutes=5), "de") == "weniger als einer Stunde"
    assert texts.age_text(timedelta(days=2), "en") == "2 days"


@pytest.mark.parametrize("locale", LOCALES)
def test_deadline_text_is_utc_and_locale_formatted(locale: str) -> None:
    from datetime import datetime

    value = datetime(2026, 10, 27, 14, 30)  # noqa: DTZ001 - naive UTC like the table
    expected = "27.10.2026 14:30 UTC" if locale == "de" else "2026-10-27 14:30 UTC"
    assert texts.moment_text(value, locale) == expected


def test_inbox_hint_can_be_left_out() -> None:
    _, with_hint = texts.render("request", "de", title="T")
    _, without = texts.render("request", "de", title="T", inbox_hint=False)
    assert "Meine Freigaben" in with_hint and "Meine Freigaben" not in without
