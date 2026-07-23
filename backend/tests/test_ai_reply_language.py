"""Unit tests for tiqora.ai.reply_language (plan block 3). Pure functions,
no DB/network."""

from __future__ import annotations

from tiqora.ai.reply_language import (
    LANGUAGE_PROFILES,
    detect_reply_language,
    detect_reply_language_detailed,
)


def test_english_title_and_id_body_detected_as_english() -> None:
    lang = detect_reply_language(
        "Connection issue on my line",
        "Reference id z75363, still not working, please help",
        candidates=list(LANGUAGE_PROFILES),
        default="de",
    )
    assert lang == "en"


def test_german_prose_detected_as_german() -> None:
    lang = detect_reply_language(
        "Verbindungsproblem",
        "Hallo, meine Verbindung funktioniert seit heute nicht mehr, bitte um Hilfe.",
        candidates=list(LANGUAGE_PROFILES),
        default="en",
    )
    assert lang == "de"


def test_empty_text_falls_back_to_default() -> None:
    lang = detect_reply_language(None, None, candidates=list(LANGUAGE_PROFILES), default="en")
    assert lang == "en"


def test_short_ambiguous_text_falls_back_to_default() -> None:
    lang = detect_reply_language(
        "z75363", "z75363", candidates=list(LANGUAGE_PROFILES), default="de"
    )
    assert lang == "de"


def test_noise_is_stripped_before_scoring() -> None:
    # Email/URL/id noise alone must not accidentally match a stopword profile
    # (no genuine function words are present in either language here).
    lang = detect_reply_language(
        "Bestellung#123",
        "siehe foo.bar@example.com https://example.com/track/abc123 z75363",
        candidates=list(LANGUAGE_PROFILES),
        default="de",
    )
    assert lang == "de"


def test_unknown_candidate_language_is_ignored() -> None:
    lang = detect_reply_language(
        "Bonjour, ceci est un test",
        "Bonjour",
        candidates=["fr", "de"],
        default="de",
    )
    assert lang == "de"


def test_detailed_english_text_without_default_reaches_min_score() -> None:
    # No configured default (mirrors runtime's "auto without a
    # reply_language_default" case): a clearly English text still yields a
    # trustworthy (non-fallback) "en" detection.
    detection = detect_reply_language_detailed(
        "Connection issue on my line",
        "Hello, my connection has not been working since this morning, please help.",
        candidates=list(LANGUAGE_PROFILES),
        default="",
    )
    assert detection.language == "en"
    assert detection.used_fallback is False


def test_detailed_gibberish_below_min_score_falls_back() -> None:
    detection = detect_reply_language_detailed(
        "z75363", "z75363", candidates=list(LANGUAGE_PROFILES), default=""
    )
    assert detection.used_fallback is True
    assert detection.language == ""


def test_detailed_empty_text_reports_fallback() -> None:
    detection = detect_reply_language_detailed(
        None, None, candidates=list(LANGUAGE_PROFILES), default="en"
    )
    assert detection.used_fallback is True
    assert detection.language == "en"
    assert detection.score == 0
