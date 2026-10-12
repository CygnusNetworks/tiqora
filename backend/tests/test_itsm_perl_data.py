"""The shared Perl data parser as the ITSM definition reader uses it (pure).

The parser itself lives in ``tiqora.znuny.perl_data`` (step 0, ruling R8); its exhaustive tests are
``test_znuny_perl_data.py``. These cases pin what the ITSM adapter relies on for old OTRS-stock
class definitions.
"""

from __future__ import annotations

import pytest

from tiqora.znuny.perl_data import PerlDataError, parse_perl_data


def test_parses_nested_structures_and_quotes() -> None:
    src = """[
      { Key => 'Vendor', Name => "Vendor \\"Inc\\"", Input => { Type => 'Text', Size => 50 } },
      { Key => 'it\\'s', Sub => [ { Key => 'X' } ] },
    ]"""
    got = parse_perl_data(src)
    assert got[0] == {
        "Key": "Vendor",
        "Name": 'Vendor "Inc"',
        "Input": {"Type": "Text", "Size": 50},
    }
    assert got[1] == {"Key": "it's", "Sub": [{"Key": "X"}]}


def test_bareword_keys_and_fat_comma() -> None:
    assert parse_perl_data("{ Searchable => 1, 'Count Max' => 2, 3 => 'x' }") == {
        "Searchable": 1,
        "Count Max": 2,
        "3": "x",
    }


def test_undef_numbers_comments() -> None:
    src = "[ # first\n undef, -3, 4.5, 0, # tail\n 'a' ]"
    assert parse_perl_data(src) == [None, -3, 4.5, 0, "a"]


@pytest.mark.parametrize(
    "snippet",
    ["sub { 1 }", "qw(a b)", "do { }", "eval '1'", "$x", "@a", "%h", "\\'x'", "`ls`"],
)
def test_code_is_rejected(snippet: str) -> None:
    with pytest.raises(PerlDataError):
        parse_perl_data(snippet)
    with pytest.raises(PerlDataError):
        parse_perl_data(f"[ {snippet} ]")


def test_deeply_nested_input_bounded() -> None:
    with pytest.raises(PerlDataError, match="depth"):
        parse_perl_data("[" * 65 + "]" * 65)
    assert parse_perl_data("[" * 64 + "]" * 64) is not None
    with pytest.raises(PerlDataError):
        parse_perl_data("[" + "'" + "x" * (1024 * 1024) + "'" + "]")
