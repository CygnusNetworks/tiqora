"""Perl compatibility helpers for GenericInterface mappers.

Ports the pieces of ``Kernel::System::VariableCheck`` (``IsString``) and Perl regex/truthiness
semantics that ``Kernel/GenericInterface/Mapping/Simple.pm`` and ``XSLT.pm`` rely on.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from tiqora.api.compat.mapping import MappingConfigError
from tiqora.znuny.perl_format import perl_num_str


def perl_truthy(value: Any) -> bool:
    """Perl boolean context: undef, "", "0", 0, 0.0 are false; references (dict/list) are true."""
    if value is None:
        return False
    if isinstance(value, str):
        return value not in ("", "0")
    if isinstance(value, (int, float)):
        return value != 0
    return True


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


@lru_cache(maxsize=512)
def compile_perl_regex(pattern: str, *, verbose: bool) -> re.Pattern[str]:
    """Compile a Perl regex from a webservice config; ``verbose`` = Perl ``/xms``."""
    flags = re.VERBOSE | re.MULTILINE | re.DOTALL if verbose else 0
    try:
        return re.compile(pattern, flags)
    except (re.error, RecursionError, OverflowError) as exc:
        raise MappingConfigError(f"Ungültiger regulärer Ausdruck {pattern!r}: {exc}") from exc


def perl_fullmatch_x(pattern: str, value: str) -> bool:
    """``$value =~ m{ \\A $pattern \\z }xms`` (pattern interpolated unparenthesised, like Perl)."""
    regex = compile_perl_regex(rf"\A{pattern} \Z", verbose=True)
    return regex.search(value) is not None


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
