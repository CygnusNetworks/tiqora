"""Split the suite into CI shards by test file, and record what each file costs.

``TIQORA_TEST_SHARD=<i>/<n>`` (1-based) keeps only the files of shard *i* out of *n*.
Files, not tests, are the unit: state is restored between modules (see
``_restore_db_between_modules``), so a file must run whole and in order.

Files go to the shard with the least work so far, largest first. A file's work is
its seconds in ``shard_durations.json`` when it has an entry there, otherwise an
estimate from its tests: a database-parametrised test costs about a second, a unit
test next to nothing. Every shard computes the same split from the same collection,
so together they run each file exactly once.

``TIQORA_SHARD_DURATIONS_OUT=<path>`` writes this run's seconds per file (setup,
call and teardown of every test, module teardown included) as JSON. The CI shards
upload theirs; refresh the committed file from a full run with::

    jq -s 'add' durations-*.json > tests/shard_durations.json
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from functools import cache
from pathlib import Path

import pytest

DURATIONS = Path(__file__).parent / "shard_durations.json"

# Estimates for files without a recorded duration (seconds per test).
_DB_TEST_SECONDS = 1.0
_UNIT_TEST_SECONDS = 0.05


def _file_of(item: pytest.Item) -> str:
    return item.nodeid.partition("::")[0]


@cache
def _recorded() -> dict[str, float]:
    if not DURATIONS.exists():
        return {}
    data: dict[str, float] = json.loads(DURATIONS.read_text())
    return data


def _estimate(items: list[pytest.Item]) -> float:
    return sum(
        _DB_TEST_SECONDS if "_url]" in item.nodeid or "db" in item.keywords else _UNIT_TEST_SECONDS
        for item in items
    )


def parse_shard(value: str) -> tuple[int, int]:
    index, _, total = value.partition("/")
    try:
        i, n = int(index), int(total)
    except ValueError:
        raise pytest.UsageError(f"TIQORA_TEST_SHARD={value!r}: expected <i>/<n>") from None
    if not 1 <= i <= n:
        raise pytest.UsageError(f"TIQORA_TEST_SHARD={value!r}: need 1 <= i <= n")
    return i, n


def assign(work: dict[str, float], shards: int) -> list[list[str]]:
    """Largest file first, each to the shard with the least work so far (ties: lower index)."""
    loads = [0.0] * shards
    files: list[list[str]] = [[] for _ in range(shards)]
    for name in sorted(work, key=lambda f: (-work[f], f)):
        target = min(range(shards), key=lambda s: (loads[s], s))
        loads[target] += work[name]
        files[target].append(name)
    return files


def select_shard(config: pytest.Config, items: list[pytest.Item]) -> None:
    value = os.environ.get("TIQORA_TEST_SHARD")
    if not value:
        return
    index, total = parse_shard(value)
    by_file: dict[str, list[pytest.Item]] = defaultdict(list)
    for item in items:
        by_file[_file_of(item)].append(item)
    recorded = _recorded()
    work = {f: recorded.get(f, _estimate(its)) for f, its in by_file.items()}
    mine = set(assign(work, total)[index - 1])
    kept = [item for item in items if _file_of(item) in mine]
    dropped = [item for item in items if _file_of(item) not in mine]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept
    print(
        f"\nShard {index}/{total}: {len(mine)} files, {len(kept)} tests, "
        f"about {sum(work[f] for f in mine) / 60:.0f} min"
    )


class DurationRecorder:
    """Sums each file's setup, call and teardown time; writes them at the end."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seconds: dict[str, float] = defaultdict(float)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.seconds[report.nodeid.partition("::")[0]] += report.duration

    def pytest_sessionfinish(self) -> None:
        rounded = {f: round(s, 1) for f, s in sorted(self.seconds.items())}
        self.path.write_text(json.dumps(rounded, indent=1) + "\n")


def register_recorder(config: pytest.Config) -> None:
    out = os.environ.get("TIQORA_SHARD_DURATIONS_OUT")
    if out:
        config.pluginmanager.register(DurationRecorder(Path(out)), "tiqora-shard-durations")
