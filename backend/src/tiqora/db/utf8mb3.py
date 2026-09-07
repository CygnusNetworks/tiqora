"""Make strings safe for MariaDB ``utf8mb3`` (3-byte UTF-8) columns.

Znuny 6.x tables such as ``article_data_mime``, ``ticket`` and
``ticket_history`` are typically ``utf8mb3_unicode_ci`` even when the
SQLAlchemy connection charset is ``utf8mb4``. Four-byte UTF-8 (emoji and
other supplementary-plane characters) then raises ``pymysql.err.DataError``
1366 ``Incorrect string value``.

U+FFFD is three-byte UTF-8 and fits. Convert the column to ``utf8mb4``
only after schema ownership; until then writers must stay inside the BMP.
"""

from __future__ import annotations

_REPLACEMENT = "\ufffd"
_BMP_MAX = 0xFFFF


def replace_non_bmp(value: str | None) -> str | None:
    """Replace code points above U+FFFF with U+FFFD. ``None`` stays ``None``."""
    if value is None:
        return None
    if not any(ord(ch) > _BMP_MAX for ch in value):
        return value
    return "".join(ch if ord(ch) <= _BMP_MAX else _REPLACEMENT for ch in value)


__all__ = ["replace_non_bmp"]
