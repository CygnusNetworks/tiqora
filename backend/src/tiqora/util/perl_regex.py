"""Perl-style regular expressions with a per-call time budget, shared by GI mapping and processes.

Patterns come from admin-controlled configs (webservices, process transitions) but are matched
against untrusted text (customer data via smart tags), so a catastrophic pattern such as ``(a+)+``
must not block the event loop. mrab-regex enforces a per-call ``timeout``.
"""

from __future__ import annotations

from functools import lru_cache

import regex

REGEX_TIMEOUT_SECONDS = 0.2


class PerlRegexError(ValueError):
    """The pattern does not compile."""


@lru_cache(maxsize=512)
def compile_perl_regex(pattern: str, *, verbose: bool) -> regex.Pattern[str]:
    """Compile a Perl regex; ``verbose`` = Perl ``/xms``."""
    flags = regex.VERBOSE | regex.MULTILINE | regex.DOTALL if verbose else 0
    try:
        return regex.compile(pattern, flags)
    except (regex.error, RecursionError, OverflowError) as exc:
        raise PerlRegexError(f"Invalid regular expression {pattern!r}: {exc}") from exc
