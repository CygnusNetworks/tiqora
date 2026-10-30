"""The cleanup helper must also cover a module that runs first in a fresh container."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

from ._row_cleanup import delete_rows_above, snapshot_max_ids


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
