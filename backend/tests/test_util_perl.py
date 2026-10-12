"""``tiqora.util.perl``: Perl semantics shared by the Znuny-compat layers."""

from __future__ import annotations

from tiqora.util.perl import perl_comma_list


def test_perl_comma_list_splits_like_znuny() -> None:
    assert perl_comma_list(None) == []
    assert perl_comma_list("") == []
    assert perl_comma_list(" a , b,,c ") == ["a", "b", "c"]
    assert perl_comma_list(["x ", "", " y"]) == ["x", "y"]
    assert perl_comma_list(7) == ["7"]
