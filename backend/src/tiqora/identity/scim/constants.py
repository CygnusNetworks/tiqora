"""SCIM constants: token format and limits."""

from __future__ import annotations

from typing import Final

TOKEN_PREFIX: Final = "tqscim_"
MAX_ACTIVE_TOKENS: Final = 5
TOKEN_DEFAULT_DAYS: Final = 365
TOKEN_MAX_DAYS: Final = 730
LAST_USED_WRITE_INTERVAL: Final = 60  # seconds between last_used_at writes
