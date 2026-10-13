"""Pure install checks: dependencies, module name collisions, migration tables, static SQL scan."""

from __future__ import annotations

import pytest

from tiqora.plugins.install_checks import (
    MAX_SCAN_BYTES,
    collision_problems,
    migration_tables,
    prefix_problems,
    requirement_problems,
    static_warnings,
)

INSTALLED = {"httpx": "0.28.1", "pydantic": "2.9.2", "packaging": "24.2"}


def test_requirements_satisfied() -> None:
    assert (
        requirement_problems(["httpx>=0.27", "pydantic<3; python_version >= '3.10'"], INSTALLED)
        == []
    )


def test_requirement_missing_or_wrong_version() -> None:
    p = requirement_problems(["requests>=2", "httpx<0.20"], INSTALLED)
    assert any("„requests“" in x and "nicht enthalten" in x for x in p)
    assert any("„httpx“" in x and "0.28.1" in x for x in p)
    assert len(p) == 2


def test_requirement_extra_only_lines_skipped() -> None:
    assert requirement_problems(['pytest; extra == "test"'], INSTALLED) == []


def test_requirement_marker_not_applicable_skipped() -> None:
    assert requirement_problems(["requests; python_version < '3.0'"], INSTALLED) == []


def test_requirement_invalid_line_is_a_problem() -> None:
    assert requirement_problems(["not a requirement!!"], INSTALLED) != []


@pytest.mark.parametrize("name", ["tiqora", "tiqora-sdk", "Tiqora_SDK"])
def test_core_requirement_refused(name: str) -> None:
    assert "Tiqora bringt das selbst mit" in requirement_problems([name], INSTALLED)[0]


@pytest.mark.parametrize("top", ["tiqora", "tiqora_sdk", "fastapi", "json", "asyncio"])
def test_top_level_collision_refused(top: str) -> None:
    problems = collision_problems([top])
    assert problems and f"„{top}“" in problems[0]


def test_free_name_and_tqp_are_fine() -> None:
    assert collision_problems(["tq_hello", "tqp"], {}) == []


def test_own_previous_copy_not_a_collision() -> None:
    assert collision_problems(["tq_x"], {"tq_x": ["tq-x"]}, own_dist="tq-x") == []
    assert collision_problems(["tq_x"], {"tq_x": ["tq-x"]}) != []
    assert collision_problems(["tq_x"], {"tq_x": ["tq-x", "other"]}, own_dist="tq-x") != []


def test_migration_tables_found() -> None:
    src = (
        b"from alembic import op\nimport sqlalchemy as sa\n"
        b'def upgrade():\n    op.create_table("tqp_hello_note", sa.Column("id", sa.Integer))\n'
    )
    assert migration_tables({"tq_x/migrations/0001_init.py": src, "tq_x/other.py": src}) == [
        "tqp_hello_note"
    ]


def test_migration_tables_sorted_unique_and_syntax_error_ignored() -> None:
    a = b'from alembic import op\nop.create_table("tqp_h_b")\nop.create_table("tqp_h_a")\n'
    b = b'from alembic import op\nop.create_table("tqp_h_a")\n'
    files = {"p/migrations/1.py": a, "p/migrations/2.py": b, "p/migrations/3.py": b"def (:"}
    assert migration_tables(files) == ["tqp_h_a", "tqp_h_b"]


def test_prefix_problem_blocks() -> None:
    src = b'from alembic import op\ndef upgrade():\n    op.create_table("ticket_x")\n'
    problems = prefix_problems({"tq_x/migrations/0001.py": src}, "hello")
    assert len(problems) == 1 and "tqp_hello_" in problems[0] and "„ticket_x“" in problems[0]


def test_prefix_ok_for_own_tables() -> None:
    src = b'from alembic import op\nop.create_table("tqp_hello_note")\n'
    assert prefix_problems({"tq_x/migrations/0001.py": src}, "hello") == []


def test_static_warnings_raw_sql_on_znuny_table() -> None:
    src = b'Q = "UPDATE ticket SET title = :t WHERE id = :i"\n'
    out = static_warnings({"tq_x/a.py": src}, short_id="hello")
    assert len(out) == 1 and "„ticket“" in out[0]


def test_static_warnings_monkeypatch() -> None:
    src = b"import tiqora.domain.ticket_write_service as w\nw.add_article = None\n"
    out = static_warnings({"tq_x/a.py": src}, short_id="hello")
    assert len(out) == 1 and "tiqora.domain.ticket_write_service" in out[0]


def test_static_warnings_setattr_and_from_import() -> None:
    src = b"from tiqora.domain import ticket_write_service as w\nsetattr(w, 'x', 1)\n"
    out = static_warnings({"tq_x/a.py": src}, short_id="hello")
    assert out == ["Verändert Tiqora-Code zur Laufzeit (tiqora.domain.ticket_write_service)"]


def test_static_warnings_own_attribute_assignment_is_fine() -> None:
    src = b"class A:\n    def f(self):\n        self.x = 1\nimport tiqora_sdk\nv = tiqora_sdk.V\n"
    assert static_warnings({"tq_x/a.py": src}, short_id="hello") == []


def test_static_warnings_ignore_own_tables_and_syntax_errors() -> None:
    own = b'Q = "INSERT INTO tqp_hello_note (a) VALUES (1)"\n'
    assert static_warnings({"tq_x/a.py": own}, short_id="hello") == []
    out = static_warnings({"tq_x/bad.py": b"def (:\n"}, short_id="hello")
    assert len(out) == 1 and "nicht lesbar" in out[0] and "bad.py" in out[0]


def test_static_warnings_prose_without_znuny_table_is_quiet() -> None:
    src = b'MSG = "update the settings and delete from the list"\n'
    assert static_warnings({"tq_x/a.py": src}, short_id="hello") == []


def test_static_warnings_scan_budget() -> None:
    big = b"x = 1\n" + b"#" * MAX_SCAN_BYTES
    out = static_warnings({"tq_x/big.py": big}, short_id="hello")
    assert len(out) == 1 and "gekürzt" in out[0]
