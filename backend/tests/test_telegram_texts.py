"""Telegram bot texts: built-in English/German defaults by customer language
(tiqora.channels.telegram.texts). Pure functions, no database."""

from __future__ import annotations

from tiqora.channels.telegram.texts import (
    DEFAULT_TEXTS,
    customer_language,
    default_text,
    normalize_language,
)


def test_normalize_language_keeps_the_primary_subtag() -> None:
    assert normalize_language("de-AT") == "de"
    assert normalize_language("en_US") == "en"
    assert normalize_language("FR") == "fr"
    assert normalize_language("") is None
    assert normalize_language(None) is None


def test_customer_language_prefers_what_the_customer_wrote() -> None:
    # Prod ticket 43132: an English message from a German-configured setup.
    english = "i have activated my internet access via my router but am unable to connect"
    assert customer_language(english, "de") == "en"
    assert customer_language("Ich habe kein Internet mehr, bitte helft mir", "en") == "de"


def test_customer_language_falls_back_to_the_client_language() -> None:
    assert customer_language("z90002", "de-DE") == "de"
    assert customer_language(None, "en") == "en"
    assert customer_language("ok", None) is None


def test_default_text_is_german_only_for_german_and_english_otherwise() -> None:
    for key, variants in DEFAULT_TEXTS.items():
        assert default_text(key, "de") == variants["de"]
        assert default_text(key, "en") == variants["en"]
        assert default_text(key, None) == variants["en"]
        assert default_text(key, "fr") == variants["en"]


def test_identity_no_match_texts_keep_the_fields_placeholder() -> None:
    for language in ("de", "en"):
        assert "{fields}" in default_text("identity_no_match_text", language)
