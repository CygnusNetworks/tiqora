import ast
from pathlib import Path

import tiqora.approvals as approvals_pkg

FORBIDDEN = ("tiqora.process", "tiqora.itsm", "tiqora.cmdb", "tiqora.api")
PKG_DIR = Path(approvals_pkg.__file__).parent
PURE_ALLOWED_TOP = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "types",
    "typing",
    "pydantic",
}
PURE_ALLOWED_TIQORA = {"tiqora.extensions.registry", "tiqora.approvals.contract"}


def _imports(path: Path) -> list[tuple[str, bool]]:
    """(module, under_type_checking) for every import in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, bool]] = []

    def visit(node: ast.AST, tc: bool) -> None:
        for child in ast.iter_child_nodes(node):
            child_tc = tc
            if isinstance(child, ast.If):
                test = child.test
                if (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                    isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                ):
                    for stmt in child.body:
                        visit_stmt(stmt, True)
                    for stmt in child.orelse:
                        visit_stmt(stmt, tc)
                    continue
            visit_stmt(child, child_tc)

    def visit_stmt(node: ast.AST, tc: bool) -> None:
        if isinstance(node, ast.Import):
            found.extend((a.name, tc) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relative import in {path.name}"
            found.append((node.module or "", tc))

        else:
            visit(node, tc)

    visit(tree, False)
    return found


def _forbidden_imports(path: Path) -> list[str]:
    """Forbidden modules imported by the file, also via ``from tiqora import itsm``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{a.name}" for a in node.names)
    return [n for n in names if _is_forbidden(n)]


def _is_forbidden(module: str) -> bool:
    return any(module == f or module.startswith(f + ".") for f in FORBIDDEN)


def test_approvals_package_imports_no_consumer() -> None:
    files = sorted(PKG_DIR.rglob("*.py"))
    assert files, "approvals package is empty"
    for path in files:
        assert not _forbidden_imports(path), f"{path.name} imports {_forbidden_imports(path)}"


def test_contract_and_rules_are_pure() -> None:
    for name in ("contract.py", "rules.py"):
        for module, under_tc in _imports(PKG_DIR / name):
            top = module.split(".")[0]
            if module == "sqlalchemy.ext.asyncio":
                assert under_tc, f"{name}: {module} must be imported under TYPE_CHECKING"
            elif top == "tiqora":
                assert module in PURE_ALLOWED_TIQORA, f"{name} imports {module}"
            else:
                assert top in PURE_ALLOWED_TOP, f"{name} imports {module}"
    # contract may not depend on rules (rules depends on contract)
    assert all(m != "tiqora.approvals.rules" for m, _ in _imports(PKG_DIR / "contract.py"))


def test_import_scanner_detects_forbidden(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from tiqora.itsm.models import X\nimport tiqora.api.deps\n", encoding="utf-8")
    assert {"tiqora.itsm.models", "tiqora.api.deps"} <= set(_forbidden_imports(bad))
    sub = tmp_path / "sub.py"
    sub.write_text("from tiqora import itsm\n", encoding="utf-8")
    assert _forbidden_imports(sub) == ["tiqora.itsm"]
    ok = tmp_path / "ok.py"
    ok.write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import sqlalchemy.ext.asyncio\n",
        encoding="utf-8",
    )
    assert ("sqlalchemy.ext.asyncio", True) in _imports(ok)
