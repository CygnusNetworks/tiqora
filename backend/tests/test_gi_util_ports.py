"""Pure tests of the ``Kernel::System::Util`` ports and the DeepGet clean-up regex (G9)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tiqora.gi.deep_get import (
    DEEP_GET_CLEANUP_RE,
    base64_deep_encode,
    data_structure_remove_elements,
    perl_split,
)
from tiqora.gi.invoker_generic import _date_time_parts


def test_cleanup_regex() -> None:
    for key in ("UserPw", "Config::X", "AuthBackend", "OAuth2Token", "SecretKey", "UserConfigItem"):
        assert DEEP_GET_CLEANUP_RE.search(key), key
    for key in ("Title", "UserLogin", "QueueID", "DynamicField_GiTag", "Comment"):
        assert not DEEP_GET_CLEANUP_RE.search(key), key


def test_perl_split_drops_trailing_empty_fields() -> None:
    assert perl_split("->", "a->b->") == ["a", "b"]
    assert perl_split("->", "") == []
    assert perl_split("->", "->a") == ["", "a"]


def _sample() -> dict[str, Any]:
    return {
        "Title": "T",
        "Articles": [
            {"ArticleID": 1, "Body": "b1", "Subject": "s1"},
            {"ArticleID": 2, "Body": "b2", "Subject": "s2"},
        ],
        "QueueData": {"Name": "Raw", "Comment": "c"},
        "Article": {"Body": "b2", "Attachment": [{"Content": "x", "Filename": "f"}]},
    }


def test_remove_elements_nested_paths_and_lists() -> None:
    data = _sample()
    data_structure_remove_elements(
        data, ["Articles->Body", "QueueData->Comment", "Article->Attachment->Content", "Title"]
    )
    assert data["Articles"] == [
        {"ArticleID": 1, "Subject": "s1"},
        {"ArticleID": 2, "Subject": "s2"},
    ]
    assert data["QueueData"] == {"Name": "Raw"}
    assert data["Article"]["Attachment"] == [{"Filename": "f"}]
    assert "Title" not in data


def test_remove_elements_missing_paths_and_scalars_are_silent() -> None:
    data = _sample()
    before = copy.deepcopy(data)
    data_structure_remove_elements(data, ["Nope", "Title->Deeper", "QueueData->Name->Deeper"])
    assert data == before
    scalar_list = ["a", "b"]
    data_structure_remove_elements(scalar_list, ["a"])
    assert scalar_list == ["a", "b"]


def test_remove_elements_keeps_the_znuny_same_level_quirk() -> None:
    # After descending into Articles, Znuny keeps looping over the rest of the path on the
    # SAME level: a top-level "Body" is deleted by "Articles->Body" too.
    data = {"Body": "top", "Articles": [{"Body": "x", "Keep": 1}]}
    data_structure_remove_elements(data, ["Articles->Body"])
    assert data == {"Articles": [{"Keep": 1}]}


def test_base64_deep_encode_scalars_lists_and_paths() -> None:
    data = {
        "Title": "Hello",
        "Number": 5,
        "Articles": [{"Body": "abc", "Other": "o"}, {"Body": None}],
        "Tags": ["a", "b"],
        "Nested": {"Dict": "unchanged"},
    }
    base64_deep_encode(data, ["Title", "Number", "Articles->Body", "Tags", "Nested"])
    assert data["Title"] == "SGVsbG8=\n"
    assert data["Number"] == "NQ==\n"
    assert data["Articles"] == [{"Body": "YWJj\n", "Other": "o"}, {"Body": ""}]
    # the elements of a list under the last key are encoded one by one
    assert data["Tags"] == ["YQ==\n", "Yg==\n"]
    # a dict under the last key is left alone
    assert data["Nested"] == {"Dict": "unchanged"}


def test_base64_deep_encode_wraps_like_encode_base64() -> None:
    data = {"Body": "x" * 100}
    base64_deep_encode(data, ["Body"])
    lines = data["Body"].split("\n")
    assert [len(line) for line in lines] == [76, 60, 0]  # 76 + 60 characters, trailing newline
    assert data["Body"].endswith("\n")


def test_base64_deep_encode_missing_keys_untouched() -> None:
    data = {"Title": "T"}
    base64_deep_encode(data, ["Missing", "Title->Deeper"])
    # "Title->Deeper": Title exists, so it is descended into with the rest of the path; a
    # scalar is encoded whatever the rest says (Base64DeepEncode's first branch)
    assert data["Title"] == "VA==\n"


@pytest.mark.parametrize("keys", [[], [""]])
def test_empty_key_lists_are_noops(keys: list[str]) -> None:
    data = {"Title": "T", "L": [{"A": "a"}]}
    before = copy.deepcopy(data)
    data_structure_remove_elements(data, keys)
    base64_deep_encode(data, keys)
    assert data == before


def test_date_time_parts_like_kernel_system_datetime_get() -> None:
    now = datetime(2026, 1, 1, 23, 30, 0, tzinfo=UTC)
    assert _date_time_parts(3600, ZoneInfo("UTC"), now) == {
        "Year": 2026,
        "Month": 1,
        "MonthAbbr": "Jan",
        "Day": 2,
        "Hour": 0,
        "Minute": 30,
        "Second": 0,
        "DayOfWeek": 5,  # Friday, Monday = 1
        "DayAbbr": "Fri",
        "TimeZone": "UTC",
    }
    berlin = _date_time_parts(0, ZoneInfo("Europe/Berlin"), now)
    assert (berlin["Day"], berlin["Hour"], berlin["TimeZone"]) == (2, 0, "Europe/Berlin")
