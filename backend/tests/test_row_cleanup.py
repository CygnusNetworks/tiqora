"""The cleanup helper must also cover a module that runs first in a fresh container."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError, OperationalError, ProgrammingError

from . import _row_cleanup
from ._row_cleanup import delete_rows_above, snapshot_marks, snapshot_max_ids


def test_table_created_after_the_snapshot_is_still_cleaned(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'cleanup.db'}"
    snapshot = snapshot_max_ids(url, ("tiqora_event_outbox",))
    assert snapshot == {"tiqora_event_outbox": 0}

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tiqora_event_outbox (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO tiqora_event_outbox (id) VALUES (1), (2)"))

    delete_rows_above(url, snapshot)

    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM tiqora_event_outbox")).scalar_one() == 0
    engine.dispose()


class _PgUndefinedTable(Exception):
    pgcode = "42P01"


class _PgOtherError(Exception):
    pgcode = "53300"  # too_many_connections


@pytest.mark.parametrize(
    ("error", "marks"),
    [
        (ProgrammingError("q", {}, Exception(1146, "Table 'x.queue' doesn't exist")), {"queue": 0}),
        (
            ProgrammingError("q", {}, _PgUndefinedTable('relation "queue" does not exist')),
            {"queue": 0},
        ),
        (OperationalError("q", {}, Exception(1040, "Too many connections")), {}),
        (OperationalError("q", {}, Exception(2013, "Lost connection")), {}),
        (OperationalError("q", {}, _PgOtherError("too many clients")), {}),
    ],
)
def test_only_a_missing_table_gets_mark_zero(
    monkeypatch: pytest.MonkeyPatch, error: DatabaseError, marks: dict[str, int]
) -> None:
    @contextmanager
    def failing_begin() -> Iterator[None]:
        raise error
        yield

    engine = MagicMock()
    engine.begin = failing_begin
    monkeypatch.setattr(_row_cleanup, "create_engine", lambda _url: engine)

    assert snapshot_marks("ignored://", ("queue",)) == marks
