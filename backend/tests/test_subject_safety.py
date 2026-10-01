"""Unit tests for the conservative ticket-title PII check (no DB)."""

from __future__ import annotations

import pytest

from tiqora.domain.integrations.subject_safety import is_title_pii_free

_NAMES = ["Anna", "Meyer", "Anna Meyer"]
_IDENTS = ["z50test", "z50test#3"]


def _free(title: str | None, **kwargs: object) -> bool:
    kwargs.setdefault("known_names", _NAMES)
    kwargs.setdefault("identifiers", _IDENTS)
    return is_title_pii_free(title, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "title",
    [
        "Störungsmeldung Internet",
        "WLAN langsam seit gestern",
        "Re: AW: Kein Internet",
        "Ausfall am 30.09.2026 ab 10:30 Uhr",
        "Ausfall seit 2026-09-30, ca. 10:30:00 Uhr",
        "Zimmer 12, Haus 3",
    ],
)
def test_clean_titles_are_pii_free(title: str) -> None:
    assert _free(title) is True


@pytest.mark.parametrize("title", [None, "", "   "])
def test_empty_title_is_not_pii_free(title: str | None) -> None:
    assert _free(title) is False


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("Internet geht nicht - Anna Meyer", "known full name"),
        ("Frage von meyer", "known last name, case-insensitive"),
        ("Kontakt anna.meyer@example.com", "e-mail"),
        ("Rückruf an x@y", "bare @"),
        ("PKZ 900002 kein Zugang", "6-digit PKZ"),
        ("WP 999-05-06-07-8", "grouped Wohnplatznummer"),
        ("Bitte zurückrufen 0228 123 456", "phone with spaces"),
        ("Tel +49 228 1234567", "international phone"),
        ("12345", "bare 5 digits"),
        ("Gerät 192.168.1.10 offline", "IPv4"),
        ("Prefix 2001:db8::1 down", "IPv6"),
        ("Router 00:11:22:aa:bb:cc", "MAC colon"),
        ("Router 00-11-22-AA-BB-CC", "MAC dash"),
        ("Router 0011.22aa.bbcc", "MAC dotted"),
        ("Router 001122aabbcc", "MAC bare"),
        ("Konto Z50TEST gesperrt", "login, case-insensitive"),
        ("Vertrag z50test#3 kündigen", "customer_user_id"),
    ],
)
def test_pii_titles_are_flagged(title: str, reason: str) -> None:
    assert _free(title) is False, reason


def test_without_known_names_a_name_passes_unless_ner_finds_it() -> None:
    title = "Frau Kellbach meldet Ausfall"
    assert is_title_pii_free(title) is True
    assert is_title_pii_free(title, person_names=lambda _t: ["Kellbach"]) is False
    assert is_title_pii_free(title, person_names=lambda _t: []) is True


def test_ner_not_called_when_disabled() -> None:
    def _boom(_t: str) -> list[str]:
        raise AssertionError("NER must not run")

    assert is_title_pii_free("Störungsmeldung Internet") is True
    # A title failing an earlier check never reaches NER either.
    assert is_title_pii_free("PKZ 900002", person_names=_boom) is False


def test_short_known_names_are_ignored_like_pii_mapper() -> None:
    # PiiMapper drops name candidates shorter than 3 characters ("Li").
    assert is_title_pii_free("Linux Frage", known_names=["Li"]) is True
