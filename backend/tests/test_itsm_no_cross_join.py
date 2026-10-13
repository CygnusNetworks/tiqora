"""Static guard: no statement in ``tiqora.itsm`` names a ``tiqora_*`` and a Znuny table.

Prod MariaDB mixes ``utf8mb4_general_ci`` and ``..._unicode_ci`` (error 1267 on a cross JOIN), and
Mode A is exactly where ``tiqora_*`` data meets Znuny ITSM tables. Statements are the full method
chain around every ``text(...)`` / ``select(...)`` call (so ``.join(Model)`` is covered); ORM
classes are resolved to their table names.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import tiqora.db.legacy  # noqa: F401  (registers the Znuny ORM classes)
import tiqora.itsm.models  # noqa: F401  (registers the itsm1 ORM classes)
from tests._itsm_db import ITSM_FIXTURES
from tiqora.bootstrap.schema_loader import schema_dir
from tiqora.db.legacy.base import LegacyBase
from tiqora.db.tiqora.base import TiqoraBase

ITSM_SRC = Path(__file__).resolve().parents[1] / "src" / "tiqora" / "itsm"
_CREATE = re.compile(r"CREATE TABLE\s+`?([a-z0-9_]+)`?", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def znuny_tables() -> frozenset[str]:
    """Core schema tables plus every table of every ITSM profile schema (F15)."""
    names = set(_CREATE.findall((schema_dir() / "schema.mysql.sql").read_text()))
    for schema in sorted(ITSM_FIXTURES.glob("*/schema.mysql.sql")):
        names.update(_CREATE.findall(schema.read_text()))
    names.update(t.name for t in LegacyBase.metadata.sorted_tables)
    return frozenset(n.lower() for n in names if not n.lower().startswith("tiqora_"))


def _class_tables() -> dict[str, str]:
    out: dict[str, str] = {}
    for base in (LegacyBase, TiqoraBase):
        for mapper in base.registry.mappers:
            table = getattr(mapper.class_, "__tablename__", None)
            if isinstance(table, str):
                out[mapper.class_.__name__] = table.lower()
    return out


def _statement_node(node: ast.Call, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    top: ast.AST = node
    while True:
        parent = parents.get(top)
        if (
            isinstance(parent, ast.Attribute)
            and parent.value is top
            or isinstance(parent, ast.Call)
            and parent.func is top
        ):
            top = parent
        else:
            return top


def _tables_named(statement: ast.AST, class_tables: dict[str, str]) -> set[str]:
    """Tables a statement names: ORM class references plus words of its string literals."""
    tables: set[str] = set()
    for sub in ast.walk(statement):
        if isinstance(sub, ast.Name) and sub.id in class_tables:
            tables.add(class_tables[sub.id])
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            tables.update(w.lower() for w in _WORD.findall(sub.value))
    return tables


def find_cross_joins(root: Path, znuny: frozenset[str]) -> list[str]:
    """``path:line`` of every statement under ``root`` that mixes both table families."""
    class_tables = _class_tables()
    findings: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"text", "select"}
            ):
                continue
            named = _tables_named(_statement_node(node, parents), class_tables)
            if any(t.startswith("tiqora_") for t in named) and named & znuny:
                findings.append(f"{path}:{node.lineno}")
    return findings


def test_no_cross_join_in_itsm_package() -> None:
    assert find_cross_joins(ITSM_SRC, znuny_tables()) == []


def test_znuny_table_list_covers_core_and_itsm_profiles() -> None:
    tables = znuny_tables()
    assert {"ticket", "article", "users", "configitem", "general_catalog"} <= tables
    assert not any(t.startswith("tiqora_") for t in tables)


def test_guard_detects_a_cross_join(tmp_path: Path) -> None:
    (tmp_path / "bad_text.py").write_text(
        "from sqlalchemy import text\n"
        'Q = text("SELECT 1 FROM tiqora_itsm_problem p JOIN configitem c ON c.id = p.ci_id")\n'
    )
    (tmp_path / "bad_orm.py").write_text(
        "from sqlalchemy import select\n"
        "from tiqora.itsm.models import TiqoraItsmProblem\n"
        "from tiqora.db.legacy.ticket import Ticket\n"
        "Q = select(TiqoraItsmProblem.id).join(Ticket, Ticket.id == TiqoraItsmProblem.ticket_id)\n"
    )
    (tmp_path / "good.py").write_text(
        "from sqlalchemy import select, text\n"
        "from tiqora.itsm.models import TiqoraItsmProblem\n"
        'A = text("SELECT 1 FROM tiqora_itsm_problem")\n'
        'B = text("SELECT 1 FROM configitem")\n'
        "C = select(TiqoraItsmProblem.id)\n"
    )
    found = find_cross_joins(tmp_path, znuny_tables())
    assert sorted(Path(f.rsplit(":", 1)[0]).name for f in found) == ["bad_orm.py", "bad_text.py"]


def test_guard_ignores_variables_named_like_znuny_tables(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text(
        "from sqlalchemy import select\n"
        "from tiqora.itsm.models import TiqoraItsmProblem\n"
        "ticket = 1\n"
        "Q = select(TiqoraItsmProblem.id).where(TiqoraItsmProblem.id == ticket)\n"
    )
    assert find_cross_joins(tmp_path, znuny_tables()) == []
