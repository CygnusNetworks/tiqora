"""Perl compatibility helpers for GenericInterface mappers.

Ports the pieces of ``Kernel::System::VariableCheck`` (``IsString``) and Perl regex/truthiness
semantics that ``Kernel/GenericInterface/Mapping/Simple.pm`` and ``XSLT.pm`` rely on.
"""

from __future__ import annotations

import re
from typing import Any

import regex

from tiqora.api.compat.mapping import MappingConfigError, MappingError
from tiqora.util.perl import perl_truthy as perl_truthy  # re-export (single implementation)
from tiqora.util.perl_regex import REGEX_TIMEOUT_SECONDS, PerlRegexError
from tiqora.util.perl_regex import compile_perl_regex as _compile_perl_regex
from tiqora.znuny.perl_format import perl_num_str


def perl_is_string(value: Any) -> bool:
    """``IsString``: a defined plain scalar (str or number). JSON booleans are not strings."""
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def perl_str(value: Any) -> str:
    """Perl stringification of a scalar: numbers via ``%.15g`` rules, strings unchanged."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (bool, int, float)):
        return perl_num_str(value)
    return str(value)


def compile_perl_regex(pattern: str, *, verbose: bool) -> regex.Pattern[str]:
    """Shared compiler (``tiqora.util.perl_regex``); a broken pattern is a MappingConfigError."""
    try:
        return _compile_perl_regex(pattern, verbose=verbose)
    except PerlRegexError as exc:
        raise MappingConfigError(str(exc)) from exc


def _fullmatch_x_regex(pattern: str) -> regex.Pattern[str]:
    # Perl interpolates the pattern *unparenthesised* into ``m{ \A $pattern \z }xms``, so a
    # top-level alternation ``a|b`` is only anchored on its first/last branch - same as Znuny.
    # ``\z`` (Perl: absolute end) is spelled ``\Z`` here: in Python/regex ``\Z`` is also absolute
    # end of string (unlike Perl's ``\Z``, which also allows a trailing newline), and ``\z`` is
    # not available in ``re`` before 3.14.
    return compile_perl_regex(rf"\A{pattern} \Z", verbose=True)


def check_perl_fullmatch_x(pattern: str) -> None:
    """Compile eagerly so a broken pattern fails when the mapper is built (MappingConfigError)."""
    _fullmatch_x_regex(pattern)


def perl_fullmatch_x(pattern: str, value: str) -> bool:
    """``$value =~ m{ \A $pattern \z }xms``; MappingError if the match exceeds the time budget."""
    try:
        return _fullmatch_x_regex(pattern).search(value, timeout=REGEX_TIMEOUT_SECONDS) is not None
    except TimeoutError as exc:
        raise MappingError(
            f"regex timed out after {REGEX_TIMEOUT_SECONDS}s (pattern {pattern!r})"
        ) from exc


_REPL_TOKEN = re.compile(r"\$\{(\d+)\}|\$(\d+)|\\\$|\\(?!\d)")


def perl_replacement(replace: str) -> str:
    """Perl double-quoted replacement -> ``re.sub`` template.

    ``$1`` / ``${1}`` become ``\\g<1>``, ``\\$`` a literal ``$``; backslashes not followed by a
    digit are doubled so ``re.sub`` keeps them literally.
    """

    def repl(match: re.Match[str]) -> str:
        text = match.group(0)
        group = match.group(1) or match.group(2)
        if group is not None:
            return rf"\g<{group}>"
        if text == r"\$":
            return "$"
        return "\\\\"  # lone backslash (not before a digit): keep it literal

    return _REPL_TOKEN.sub(repl, replace)
