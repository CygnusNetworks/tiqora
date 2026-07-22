"""Unit tests for the tolerant queue-policy list-column parsing.

Prod incident 2026-07-22: the admin frontend saved ``mcp_client_ids`` as CSV
while the runtime parsed it with ``json.loads`` — ``json.loads("2") == 2``
slipped into ``.in_(2)`` and 500'd every manual-assist request.
"""

from __future__ import annotations

from tiqora.ai.listfields import parse_int_list, parse_str_list


def test_parse_int_list_json_array() -> None:
    assert parse_int_list("[21, 22]") == [21, 22]


def test_parse_int_list_csv() -> None:
    assert parse_int_list("21,22") == [21, 22]
    assert parse_int_list(" 21 , 22 ") == [21, 22]


def test_parse_int_list_scalar_json() -> None:
    # json.loads("2") yields a bare int — the exact prod-crash shape.
    assert parse_int_list("2") == [2]


def test_parse_int_list_empty_and_garbage() -> None:
    assert parse_int_list(None) == []
    assert parse_int_list("") == []
    assert parse_int_list("  ") == []
    assert parse_int_list("[]") == []
    assert parse_int_list("a,b") == []
    # Bogus "0" from the old empty-selection frontend bug is dropped.
    assert parse_int_list("0") == []
    assert parse_int_list('["0"]') == []


def test_parse_str_list_json_array() -> None:
    assert parse_str_list('["studnet", "netz"]') == ["studnet", "netz"]


def test_parse_str_list_csv() -> None:
    assert parse_str_list("studnet, netz") == ["studnet", "netz"]


def test_parse_str_list_single_word() -> None:
    # A bare word is not valid JSON — must fall back to CSV, not crash.
    assert parse_str_list("studnet") == ["studnet"]


def test_parse_str_list_empty() -> None:
    assert parse_str_list(None) == []
    assert parse_str_list("") == []
    assert parse_str_list("[]") == []
    assert parse_str_list(",") == []
