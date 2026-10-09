"""Unit tests for tiqora.ai.quoted_text and the emoji output guard."""

from __future__ import annotations

from tiqora.ai.output_guards import strip_astral_symbols
from tiqora.ai.quoted_text import strip_trailing_quote


def test_english_attribution_wrapped_over_two_lines() -> None:
    body = "Still broken.\n\nOn Fri, 9 Oct 2026 at 09:26 X <x@y.de>\nwrote:\n\n> old\n> text\n"
    assert strip_trailing_quote(body) == "Still broken."


def test_german_attribution_and_crlf() -> None:
    body = (
        "Geht nicht.\r\n\r\nAm 09.10.2026 um 09:26 schrieb NetAdmin <n@a.de>:\r\n"
        "> alt\r\n> text\r\n"
    )
    assert strip_trailing_quote(body) == "Geht nicht."


def test_original_message_separator() -> None:
    body = "Hi\n\n-----Original Message-----\nFrom: a\nSent: b\n\nold stuff"
    assert strip_trailing_quote(body) == "Hi"


def test_bare_trailing_quote_block_without_attribution() -> None:
    body = "Reply\n\n> one\n> two\n> three\n"
    assert strip_trailing_quote(body) == "Reply"


def test_interleaved_answers_are_kept() -> None:
    body = "> Is the router on?\nYes it is.\n> Is the cable in WAN?\nYes.\n"
    assert strip_trailing_quote(body) == body


def test_pure_forward_keeps_original() -> None:
    body = "On Fri, X wrote:\n> only quoted\n> content\n> here"
    assert strip_trailing_quote(body) == body


def test_plain_text_untouched() -> None:
    assert strip_trailing_quote("No quote here.") == "No quote here."
    assert strip_trailing_quote(None) == ""


def test_strip_astral_symbols_drops_emoji_and_double_space() -> None:
    assert strip_astral_symbols('Symbol wie 🌐 oder "Internet"') == 'Symbol wie oder "Internet"'
    assert strip_astral_symbols("Ungültig ä ü – ok") == "Ungültig ä ü – ok"
