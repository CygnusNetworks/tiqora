"""DDL and constraints of the saved-report / saved-search tables (both dialects).

Band 48000-48019: owner user ids only (no Znuny rows are seeded; owner_user_id has no FK).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db
LO, HI = 48000, 48019
T0 = datetime(2026, 10, 10, 8, 0)
_URLS: set[str] = set()
_CLEANUP = (
    "DELETE FROM tiqora_saved_search WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi",
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
def test_tables_columns_and_indexes(url_fixture: str, request: pytest.FixtureRequest) -> None:
    engine = _engine(request.getfixturevalue(url_fixture))
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("tiqora_report")}
    assert cols == {
        "id",
        "name",
        "description",
        "owner_user_id",
        "visibility",
        "definition",
        "schema_version",
        "version",
        "valid",
        "copied_from",
        "created_at",
        "created_by",
        "changed_at",
        "changed_by",
    }
    assert {c["name"] for c in insp.get_columns("tiqora_saved_search")} == {
        "id",
        "owner_user_id",
        "name",
        "criteria",
        "include_archived",
        "sort_by",
        "sort_order",
        "source",
        "source_name",
        "version",
        "created_at",
        "changed_at",
    }
    assert {c["name"] for c in insp.get_columns("tiqora_saved_search_pref")} == {
        "saved_search_id",
        "user_id",
        "show_in_sidebar",
        "show_count",
        "position",
    }
    assert insp.get_pk_constraint("tiqora_report_group")["constrained_columns"] == [
        "report_id",
        "group_id",
    ]
    assert {i["name"] for i in insp.get_indexes("tiqora_report")} >= {
        "ix_tiqora_report_owner",
        "ix_tiqora_report_visibility",
    }
    uniques = {u["name"] for u in insp.get_unique_constraints("tiqora_saved_search")} | {
        i["name"] for i in insp.get_indexes("tiqora_saved_search") if i.get("unique")
    }
    assert "uq_tiqora_saved_search_source" in uniques
    engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
def test_defaults_cascade_and_source_unique(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    engine = _engine(request.getfixturevalue(url_fixture))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tiqora_report (name, owner_user_id, definition, schema_version,"
                " created_at, created_by, changed_at, changed_by)"
                " VALUES ('rep48 r', 48001, '{}', 1, :t, 48001, :t, 48001)"
            ),
            {"t": T0},
        )
        rid = conn.execute(
            text("SELECT id FROM tiqora_report WHERE owner_user_id = 48001")
        ).scalar_one()
        row = conn.execute(
            text("SELECT visibility, version, valid FROM tiqora_report WHERE id = :i"), {"i": rid}
        ).one()
        assert (row.visibility, row.version, bool(row.valid)) == ("private", 1, True)
        conn.execute(
            text("INSERT INTO tiqora_report_group (report_id, group_id) VALUES (:r, 7)"), {"r": rid}
        )
        conn.execute(text("DELETE FROM tiqora_report WHERE id = :r"), {"r": rid})
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM tiqora_report_group WHERE report_id = :r"), {"r": rid}
            ).scalar_one()
            == 0
        )
        for _ in range(2):  # NULL source_name never collides
            conn.execute(
                text(
                    "INSERT INTO tiqora_saved_search (owner_user_id, name, criteria,"
                    " created_at, changed_at)"
                    " VALUES (48002, 'rep48 s', '{}', :t, :t)"
                ),
                {"t": T0},
            )
        s = conn.execute(
            text(
                "SELECT sort_by, sort_order, source, include_archived FROM tiqora_saved_search"
                " WHERE owner_user_id = 48002"
            )
        ).first()
        assert (s.sort_by, s.sort_order, s.source, bool(s.include_archived)) == (
            "age",
            "desc",
            "tiqora",
            False,
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_saved_search (owner_user_id, name, criteria, source,"
                " source_name,"
                " created_at, changed_at) VALUES (48003, 'rep48 z', '{}', 'znuny', 'P1', :t, :t)"
            ),
            {"t": T0},
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tiqora_saved_search (owner_user_id, name, criteria, source,"
                " source_name,"
                " created_at, changed_at) VALUES (48003, 'rep48 z2', '{}', 'znuny', 'P1', :t, :t)"
            ),
            {"t": T0},
        )
    engine.dispose()
