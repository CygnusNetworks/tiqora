"""Perl-compat helpers used by the GenericInterface mappers (and perl_format)."""

from __future__ import annotations

import re
import time
from decimal import Decimal

import pytest

from tiqora.api.compat.mapping import MappingConfigError, MappingError
from tiqora.api.compat.mapping.perl import (
    compile_perl_regex,
    perl_fullmatch_x,
    perl_is_string,
    perl_replacement,
    perl_str,
    perl_truthy,
)
from tiqora.znuny.perl_format import perl_num, perl_num_str


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("0", False),
        (0, False),
        (0.0, False),
        ("0.0", True),
        ("a", True),
        (1, True),
        ({}, True),
        ([], True),
    ],
)
def test_perl_truthy(value: object, expected: bool) -> None:
    assert perl_truthy(value) is expected


def test_perl_is_string() -> None:
    assert perl_is_string("x") and perl_is_string(3) and perl_is_string(1.5)
    assert not perl_is_string(None) and not perl_is_string(True) and not perl_is_string({})


@pytest.mark.parametrize(("value", "expected"), [(2, "2"), (2.0, "2"), (1.5, "1.5"), ("x", "x")])
def test_perl_str(value: object, expected: str) -> None:
    assert perl_str(value) == expected


def test_fullmatch_x_ignores_whitespace_and_anchors() -> None:
    assert perl_fullmatch_x("Stat (e|us)", "Status")  # /x: the space is insignificant
    assert not perl_fullmatch_x("Stat(e|us)", "Statuses")  # \A…\z anchoring
    assert not perl_fullmatch_x("Stat(e|us)", "xState")


def test_fullmatch_x_dot_and_multiline_flags() -> None:
    assert perl_fullmatch_x("a.b", "a\nb")  # /s
    assert perl_fullmatch_x("^b$", "b")  # /m anchors still work inside


def test_perl_replacement() -> None:
    assert re.sub(r"^(\D*)ID$", perl_replacement("$1Number"), "TicketID") == "TicketNumber"
    assert re.sub(r"^(a)(b)$", perl_replacement("${2}x$1"), "ab") == "bxa"
    assert re.sub(r"^(a)$", perl_replacement(r"\$1"), "a") == "$1"
    assert re.sub(r"^(a)$", perl_replacement(r"x\y"), "a") == r"x\y"


def test_bad_regex_is_config_error() -> None:
    with pytest.raises(MappingConfigError, match="Invalid regular expression"):
        compile_perl_regex("(unclosed", verbose=True)


def test_compile_flags() -> None:
    assert compile_perl_regex("a b", verbose=True).fullmatch("ab")
    assert compile_perl_regex("a b", verbose=False).fullmatch("a b")


@pytest.mark.parametrize(
    ("value", "num", "text"),
    [
        (Decimal("3.00"), 3, "3"),
        ("1.50", 1.5, "1.5"),
        (None, 0, "0"),
        (2.0, 2, "2"),
        (7, 7, "7"),
        ("abc", 0, "0"),
        ("12abc", 12, "12"),
        (Decimal("0.25"), 0.25, "0.25"),
        (1e20, 1e20, "1e+20"),
    ],
)
def test_perl_num(value: object, num: float, text: str) -> None:
    assert perl_num(value) == num  # type: ignore[arg-type]
    assert perl_num_str(value) == text  # type: ignore[arg-type]


def test_perl_num_integral_is_int() -> None:
    assert isinstance(perl_num(Decimal("3.00")), int)
    assert isinstance(perl_num("1.50"), float)


def test_catastrophic_pattern_times_out_fast() -> None:
    started = time.monotonic()
    with pytest.raises(MappingError, match="timed out"):
        perl_fullmatch_x("(a|aa)+", "a" * 40 + "b")
    assert time.monotonic() - started < 1


def test_unanchored_alternation_matches_like_znuny() -> None:
    # m{ \A a|b \z }xms == (\A a) | (b \z): "ax" and "xb" both match, "xbx" does not
    assert perl_fullmatch_x("a|b", "ax")
    assert perl_fullmatch_x("a|b", "xb")
    assert not perl_fullmatch_x("a|b", "xbx")
