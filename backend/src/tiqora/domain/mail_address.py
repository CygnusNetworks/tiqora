"""Plain-text helpers for outgoing mail: one header line, one address, a usable sender.

Domain layer: the reporting schedules, the notification worker and the approval mails share
these, so none of them depends on another feature's module for it.
"""

from __future__ import annotations

import re
from email.utils import parseaddr
from typing import Final

_CONTROL: Final = re.compile(r"[\r\n\t]+")
_FORBIDDEN_IN_ADDRESS: Final = re.compile(r"[\s,;<>\"()\[\]\\]")
_UNRESOLVED_TAG: Final = re.compile(r"<(?:OTRS|TIQORA)_", re.IGNORECASE)


def clean_line(value: str) -> str:
    """One-line free text: control characters become a space (they would break mail headers)."""
    return _CONTROL.sub(" ", value).strip()


def normalise_address(value: str) -> str | None:
    """Lower-cased single addr-spec, or None when ``value`` is anything else."""
    candidate = value.strip().lower()
    if not candidate or _FORBIDDEN_IN_ADDRESS.search(candidate):
        return None
    if candidate.count("@") != 1 or parseaddr(candidate) != ("", candidate):
        return None
    local, domain = candidate.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    if ".." in domain or not candidate.isascii():
        return None
    return candidate


def usable_sender_email(raw: str | None) -> str | None:
    """``raw`` when it is a configured address, ``None`` for empty or unresolved values.

    Znuny ships ``NotificationSenderEmail`` as ``znuny@<OTRS_CONFIG_FQDN>``; the tag is
    never replaced here, so such a value is not an address anybody can send as.
    """
    value = (raw or "").strip()
    return value if value and not _UNRESOLVED_TAG.search(value) else None
