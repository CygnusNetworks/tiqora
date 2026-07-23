"""Sender blocklist matcher (plan block 2) — generic pattern matching only.

Which addresses are blocked is pure queue configuration
(``tiqora_ai_queue_policy.ignored_senders``); nothing here knows about any
concrete mailbox. Two pattern shapes are supported: an exact address
(``noreply@example.com``) and a domain wildcard (``*@example.com``), both
matched case-insensitively against the address extracted from a raw
``From:`` header (which may be ``"Display Name <mail@x>"``).
"""

from __future__ import annotations

from email.utils import parseaddr


def _extract_address(from_header: str | None) -> str | None:
    if not from_header:
        return None
    address = parseaddr(from_header)[1]
    return address.strip().lower() or None


def matches_ignored(from_address: str | None, patterns: list[str]) -> bool:
    """``True`` iff ``from_address`` matches one of ``patterns``.

    ``from_address`` may be a raw ``From:`` header value or a bare address;
    both are normalized before comparison. A pattern starting with ``*@`` is
    a domain wildcard (``*@example.com`` matches any local part at that
    domain); everything else is compared as an exact address.
    """
    if not patterns:
        return False
    address = _extract_address(from_address)
    if not address:
        return False
    for raw_pattern in patterns:
        pattern = raw_pattern.strip().lower()
        if not pattern:
            continue
        if pattern.startswith("*@"):
            if address.endswith(pattern[1:]):
                return True
        elif address == pattern:
            return True
    return False


__all__ = ["matches_ignored"]
