"""Conservative "is this ticket title free of personal data?" check.

Used by the customer-ticket integration endpoint
(``GET /api/v1/integrations/customer-tickets``) to tell an external consumer
(netadmin) whether a ticket title may be shown verbatim in a less protected
context. The answer is deliberately one-sided: ``True`` only when *none* of
the checks below finds anything, so a false ``False`` (a clean title hidden)
is the accepted failure mode, a false ``True`` is not.

Pure: no I/O. Optional NER is injected as a callable so callers decide
whether it runs (see :func:`tiqora.ai.ner.extract_person_names`).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

from tiqora.ai.pii import PiiMapper

# Five or more digits, optionally grouped by single "-", "/", ".", or spaces:
# PKZ ("900002"), Wohnplatznummer ("999-05-06-07-8"), phone fragments
# ("0228 123 456"). PiiMapper deliberately leaves PKZ/WP-Nummer readable for
# the AI agent, so this is checked separately.
_DIGIT_RUN_RE = re.compile(r"\d(?:[ ./-]?\d){4,}")
# Dates ("30.09.2026", "2026-09-30") are not PII; they are blanked out before
# the digit-run check so "30.09.2026 10:30" does not count as one long run.
_DATE_RE = re.compile(r"(?<!\d)(?:\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})(?!\d)")
_IPV4_RE = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")
_IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")
# A clock time ("10:30", "10:30:00") also fits the loose IPv6 shape.
_TIME_RE = re.compile(r":?\d{1,2}(?::\d{2}){1,2}")
_MAC_RE = re.compile(
    r"(?<![0-9A-Fa-f])(?:"
    r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}"  # 00:11:22:aa:bb:cc / 00-11-…
    r"|(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}"  # 0011.22aa.bbcc
    r"|(?=[0-9A-Fa-f]*\d)[0-9A-Fa-f]{12}"  # 001122aabbcc
    r")(?![0-9A-Fa-f])"
)


def _has_digit_run(title: str) -> bool:
    return _DIGIT_RUN_RE.search(_DATE_RE.sub(" ", title)) is not None


def _has_ipv6(title: str) -> bool:
    for match in _IPV6_RE.finditer(title):
        value = match.group(0)
        if value.count(":") >= 2 and not _TIME_RE.fullmatch(value):
            return True
    return False


def is_title_pii_free(
    title: str | None,
    *,
    known_names: Iterable[str] = (),
    identifiers: Iterable[str] = (),
    person_names: Callable[[str], Sequence[str]] | None = None,
) -> bool:
    """``True`` only when ``title`` shows no sign of personal data.

    ``known_names``: the customer's first/last names and the display names
    from the ticket's From headers — anything :class:`PiiMapper` masks with
    them (or its structured patterns: e-mail, phone, MAC, IP) fails the title.
    ``identifiers``: the customer login and every matched
    ``ticket.customer_user_id``; a case-insensitive substring hit fails it.
    ``person_names``: optional NER (``tiqora.ai.ner.extract_person_names``);
    any name found fails it. ``None`` skips NER.

    An empty title is ``False``: there is nothing to vouch for.
    """
    if not title or not title.strip():
        return False
    if "@" in title:
        return False
    if _has_digit_run(title) or _IPV4_RE.search(title) or _MAC_RE.search(title):
        return False
    if _has_ipv6(title):
        return False
    lowered = title.lower()
    if any(ident and ident.lower() in lowered for ident in identifiers):
        return False
    if PiiMapper(known_names=list(known_names)).mask(title) != title:
        return False
    return not (person_names is not None and person_names(title))


__all__ = ["is_title_pii_free"]
