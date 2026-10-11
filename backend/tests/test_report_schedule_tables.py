"""DDL, defaults and cascades of the report schedule/run tables (both dialects).

Band 52000-52019: owner user ids only (owner_user_id has no FK; no Znuny rows seeded).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.reporting.settings import ReportingSettings

pytestmark = pytest.mark.db
LO, HI = 52000, 52019
T0 = datetime(2026, 10, 11, 7, 0)
_URLS: set[str] = set()
_CLEANUP = (
    "DELETE FROM tiqora_report_run WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi",
)


def _engine(url: str):
    engine = create_engine(url)
    TiqoraBase.metadata.create_all(engine)
    _URLS.add(url)
    return engine


@pytest.fixture(scope="module", autouse=True)
def _cleanup() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        engine = create_engine(url)
        with engine.begin() as conn:
            for stmt in _CLEANUP:
                conn.execute(text(stmt), {"lo": LO, "hi": HI})
        engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
def test_columns_and_indexes(url_fixture: str, request: pytest.FixtureRequest) -> None:
    engine = _engine(request.getfixturevalue(url_fixture))
    insp = inspect(engine)
    assert {c["name"] for c in insp.get_columns("tiqora_report_schedule")} == {
        "id",
        "owner_user_id",
        "name",
        "format",
        "recurrence",
        "time_zone",
        "language",
        "subject",
        "body",
        "only_if_nonempty",
        "active",
        "paused_reason",
        "next_run_at",
        "last_run_at",
        "last_status",
        "failure_count",
        "version",
        "created_at",
        "changed_at",
    }
    assert insp.get_pk_constraint("tiqora_report_schedule_item")["constrained_columns"] == [
        "schedule_id",
        "position",
    ]
    assert {c["name"] for c in insp.get_columns("tiqora_report_run")} >= {
        "schedule_id",
        "schedule_name",
        "run_kind",
        "owner_only",
        "due_at",
        "job_id",
        "status",
        "report_keys",
        "row_count",
        "recipient_count",
        "detail",
        "error",
    }
    assert {i["name"] for i in insp.get_indexes("tiqora_report_schedule")} >= {
        "ix_tiqora_report_schedule_due",
        "ix_tiqora_report_schedule_owner",
    }
    engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
def test_defaults_and_cascade(url_fixture: str, request: pytest.FixtureRequest) -> None:
    engine = _engine(request.getfixturevalue(url_fixture))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tiqora_report_schedule (owner_user_id, name, recurrence, time_zone,"
                " subject, body,"
                " created_at, changed_at)"
                " VALUES (52001, 'rep52 s', '{}', 'Europe/Berlin', '', '', :t, :t)"
            ),
            {"t": T0},
        )
        sid = conn.execute(
            text("SELECT id FROM tiqora_report_schedule WHERE owner_user_id = 52001")
        ).scalar_one()
        row = conn.execute(
            text(
                "SELECT format, language, active, only_if_nonempty, failure_count, version"
                " FROM tiqora_report_schedule WHERE id = :i"
            ),
            {"i": sid},
        ).one()
        assert (
            row.format,
            row.language,
            bool(row.active),
            bool(row.only_if_nonempty),
            row.failure_count,
            row.version,
        ) == ("pdf", "de", True, False, 0, 1)
        conn.execute(
            text(
                "INSERT INTO tiqora_report_schedule_item (schedule_id, position, report_key)"
                " VALUES (:s, 0, 'builtin.volume')"
            ),
            {"s": sid},
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_report_schedule_recipient (schedule_id, kind, ref_id)"
                " VALUES (:s, 'user', 52001)"
            ),
            {"s": sid},
        )
        conn.execute(text("DELETE FROM tiqora_report_schedule WHERE id = :s"), {"s": sid})
        for table in ("tiqora_report_schedule_item", "tiqora_report_schedule_recipient"):
            assert (
                conn.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE schedule_id = :s"), {"s": sid}
                ).scalar_one()
                == 0
            )
        conn.execute(
            text(
                "INSERT INTO tiqora_report_run (schedule_name, owner_user_id, run_kind, status,"
                " report_keys,"
                " created_at) VALUES ('rep52 s', 52002, 'manual', 'queued', '[]', :t)"
            ),
            {"t": T0},
        )
        rid = conn.execute(
            text("SELECT id FROM tiqora_report_run WHERE owner_user_id = 52002")
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO tiqora_report_run_file"
                " (run_id, name, content_type, size_bytes, data, created_at)"
                " VALUES (:r, 'a.pdf', 'application/pdf', 3, :d, :t)"
            ),
            {"r": rid, "d": b"%PD", "t": T0},
        )
        conn.execute(text("DELETE FROM tiqora_report_run WHERE id = :r"), {"r": rid})
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM tiqora_report_run_file WHERE run_id = :r"), {"r": rid}
            ).scalar_one()
            == 0
        )
    engine.dispose()


def test_reporting_settings_defaults() -> None:
    s = ReportingSettings()
    assert (
        s.schedule_external_recipients,
        s.schedule_max_recipients,
        s.run_retention_days,
        s.pdf_row_limit,
        s.delivery_max_attachment_mb,
    ) == (False, 50, 90, 1000, 15)


def test_run_retention_days_capped_at_365() -> None:
    with pytest.raises(ValueError):
        ReportingSettings(run_retention_days=366)
    assert ReportingSettings(run_retention_days=365).run_retention_days == 365
