"""The shared Perl-regex helper used by GI mapping and process conditions."""

from __future__ import annotations

import pytest
import regex

from tiqora.api.compat.mapping import MappingConfigError
from tiqora.api.compat.mapping import perl as compat_perl
from tiqora.util import perl_regex


def test_one_cached_compiler_backs_both_callers() -> None:
    pattern = perl_regex.compile_perl_regex("a b", verbose=True)
    assert isinstance(pattern, regex.Pattern)
    assert pattern.fullmatch("ab")
    # GI mapping goes through the same cache: same compiled object, same timeout constant
    assert compat_perl.compile_perl_regex("a b", verbose=True) is pattern
    assert compat_perl.REGEX_TIMEOUT_SECONDS is perl_regex.REGEX_TIMEOUT_SECONDS == 0.2


def test_broken_pattern_error_types() -> None:
    with pytest.raises(perl_regex.PerlRegexError):
        perl_regex.compile_perl_regex("(unclosed", verbose=False)
    with pytest.raises(MappingConfigError):
        compat_perl.compile_perl_regex("(unclosed", verbose=False)


def test_timeout_is_enforced() -> None:
    compiled = perl_regex.compile_perl_regex("(a|aa)+$", verbose=False)
    with pytest.raises(TimeoutError):
        compiled.search("a" * 40 + "!", timeout=perl_regex.REGEX_TIMEOUT_SECONDS)
