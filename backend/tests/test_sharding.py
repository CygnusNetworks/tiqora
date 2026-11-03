"""The CI shard split: every file in exactly one shard, balanced, the same on every run."""

from __future__ import annotations

import json

import pytest

from tests import _sharding
from tests._sharding import assign, parse_shard


def test_every_file_lands_in_exactly_one_shard() -> None:
    work = {f"tests/test_{i:03}.py": float(i % 17 + 1) for i in range(200)}
    shards = assign(work, 10)
    files = [f for shard in shards for f in shard]
    assert sorted(files) == sorted(work)


def test_largest_files_are_spread_and_loads_stay_close() -> None:
    work = {"big_a": 100.0, "big_b": 90.0, **{f"small_{i}": 1.0 for i in range(190)}}
    shards = assign(work, 2)
    assert all(not {"big_a", "big_b"} <= set(s) for s in shards)
    loads = [sum(work[f] for f in s) for s in shards]
    assert max(loads) - min(loads) <= 1.0


def test_the_split_does_not_depend_on_dict_order() -> None:
    work = {f"f{i}": float(i % 5) for i in range(50)}
    reordered = dict(reversed(list(work.items())))
    assert assign(work, 7) == assign(reordered, 7)


@pytest.mark.parametrize("value", ["", "3", "0/10", "11/10", "a/b"])
def test_bad_shard_values_are_a_usage_error(value: str) -> None:
    with pytest.raises(pytest.UsageError):
        parse_shard(value)


def test_shard_value_is_one_based() -> None:
    assert parse_shard("1/10") == (1, 10)
    assert parse_shard("10/10") == (10, 10)


def test_recorded_durations_parse_as_seconds_per_file() -> None:
    _sharding._recorded.cache_clear()
    recorded = _sharding._recorded()
    if not _sharding.DURATIONS.exists():
        assert recorded == {}
        return
    assert recorded == json.loads(_sharding.DURATIONS.read_text())
    assert all(f.startswith("tests/") and f.endswith(".py") for f in recorded)
    assert all(isinstance(s, int | float) and s >= 0 for s in recorded.values())
