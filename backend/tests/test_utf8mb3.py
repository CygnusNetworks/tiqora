"""Unit tests for utf8mb3 BMP sanitizer (no DB)."""

from __future__ import annotations

from tiqora.db.utf8mb3 import replace_non_bmp


def test_replace_non_bmp_none() -> None:
    assert replace_non_bmp(None) is None


def test_replace_non_bmp_ascii_and_bmp_unchanged() -> None:
    assert replace_non_bmp("hello") == "hello"
    assert replace_non_bmp("Straße — naïve") == "Straße — naïve"
    assert replace_non_bmp("日本語") == "日本語"


def test_replace_non_bmp_emoji_becomes_replacement() -> None:
    # 😅 is U+1F605, four-byte UTF-8, rejected by MariaDB utf8mb3 (error 1366).
    assert replace_non_bmp("Hallo 😅\r\n") == "Hallo \ufffd\r\n"


def test_replace_non_bmp_mixed() -> None:
    src = "ok 😅 and 🎉 done"
    assert replace_non_bmp(src) == "ok \ufffd and \ufffd done"
    assert replace_non_bmp(src) is not src
