"""Install-time checks for plugin packages (D4): dependencies, module names, raw SQL.

Everything here is pure and static: package code is only ever parsed with :mod:`ast`,
never imported or executed. Problem texts are German and never echo package file
contents (only names).
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from importlib import metadata
from typing import Final

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

#: distributions a plugin must never depend on (Tiqora provides them itself)
_CORE_DISTS: Final = frozenset({"tiqora", "tiqora-sdk"})
#: import names a plugin must never ship, even if the metadata lookup misses them
_CORE_MODULES: Final = ("tiqora", "tiqora_sdk")
#: total source bytes the static scan reads; the rest is skipped with one warning
MAX_SCAN_BYTES: Final = 2 * 1024 * 1024

_SQL_RE: Final = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE)\s+"
    r"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?[`\"\[]?(\w+)",
    re.IGNORECASE,
)


def installed_versions() -> dict[str, str]:
    """Canonical distribution name -> version of this process."""
    out: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name:
            out.setdefault(canonicalize_name(name), dist.version)
    return out


def requirement_problems(
    requires_dist: Sequence[str], installed: Mapping[str, str] | None = None
) -> list[str]:
    """Problems for ``Requires-Dist`` lines that this Tiqora cannot satisfy."""
    have = {canonicalize_name(k): v for k, v in (installed or installed_versions()).items()}
    problems: list[str] = []
    for line in requires_dist:
        try:
            req = Requirement(line)
        except InvalidRequirement:
            problems.append("enthält eine ungültige Abhängigkeitsangabe")
            continue
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        name = canonicalize_name(req.name)
        if name in _CORE_DISTS:
            problems.append(f"verlangt „{req.name}“ als Abhängigkeit; Tiqora bringt das selbst mit")
            continue
        version = have.get(name)
        if version is None:
            problems.append(
                f"benötigt das Python-Paket „{req.name}“, das in diesem Tiqora nicht enthalten ist"
            )
            continue
        try:
            ok = req.specifier.contains(Version(version), prereleases=True)
        except InvalidVersion:
            ok = True
        if not ok:
            problems.append(
                f"benötigt „{req.name}“ in Version {req.specifier}, enthalten ist {version}"
            )
    return problems


def collision_problems(
    top_level: Sequence[str],
    taken: Mapping[str, Sequence[str]] | None = None,
    *,
    own_dist: str | None = None,
) -> list[str]:
    """Top-level module names that already exist in this Tiqora (or the stdlib).

    Names provided only by ``own_dist`` (the plugin's own installed copy, on update) are fine.
    """
    owners = taken if taken is not None else metadata.packages_distributions()
    own = canonicalize_name(own_dist) if own_dist else None
    problems: list[str] = []
    for top in top_level:
        if top == "tqp":
            continue
        dists = [canonicalize_name(d) for d in owners.get(top, ())]
        if own is not None and dists and all(d == own for d in dists):
            continue
        if top in sys.stdlib_module_names:
            problems.append(f"Python-Modul „{top}“ gibt es in Tiqora schon (Standardbibliothek)")
        elif dists:
            problems.append(f"Python-Modul „{top}“ gibt es in Tiqora schon (Paket {dists[0]})")
        elif top in _CORE_MODULES:
            problems.append(
                f"Python-Modul „{top}“ gibt es in Tiqora schon (Paket {top.replace('_', '-')})"
            )
    return problems


def _parse(source: bytes) -> ast.AST | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def _is_migration(path: str) -> bool:
    parts = path.split("/")
    return path.endswith(".py") and "migrations" in parts[:-1]


def _create_table_names(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        first = node.args[0]
        if (
            name == "create_table"
            and isinstance(first, ast.Constant)
            and isinstance(first.value, str)
        ):
            yield first.value


def migration_tables(files: Mapping[str, bytes]) -> list[str]:
    """Sorted unique table names created by ``op.create_table`` in ``migrations/*.py``."""
    names: set[str] = set()
    for path, source in files.items():
        if not _is_migration(path):
            continue
        tree = _parse(source)
        if tree is not None:
            names.update(_create_table_names(tree))
    return sorted(names)


def prefix_problems(files: Mapping[str, bytes], short_id: str) -> list[str]:
    """Blocking: migration tables without the ``tqp_<short_id>_`` prefix."""
    prefix = f"tqp_{short_id}_"
    return [
        f"Tabelle „{name}“ hat nicht das Präfix {prefix}"
        for name in migration_tables(files)
        if not name.startswith(prefix)
    ]


def znuny_table_names() -> frozenset[str]:
    """Table names of the Znuny read-path models (``LegacyBase.metadata``)."""
    import tiqora.db.legacy  # noqa: F401  (registers every legacy model)
    from tiqora.db.legacy.base import LegacyBase

    return frozenset(LegacyBase.metadata.tables)


def _dotted(node: ast.AST, aliases: Mapping[str, str]) -> str | None:
    """Resolve ``a.b.c`` through the import alias map; None when not rooted in a name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    root = aliases.get(node.id, node.id)
    return ".".join([root, *reversed(parts)])


def _is_tiqora(dotted: str | None) -> bool:
    return dotted is not None and dotted.split(".", 1)[0] in _CORE_MODULES


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    root = a.name.split(".", 1)[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _patched_modules(tree: ast.AST) -> Iterable[str]:
    aliases = _import_aliases(tree)
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute):
                owner = _dotted(target.value, aliases)
                if _is_tiqora(owner):
                    assert owner is not None
                    yield owner
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and node.args
        ):
            owner = _dotted(node.args[0], aliases)
            if _is_tiqora(owner):
                assert owner is not None
                yield owner


def static_warnings(files: Mapping[str, bytes], *, short_id: str) -> list[str]:
    """Non-blocking findings: raw SQL on Znuny tables, runtime patching of Tiqora code."""
    znuny = znuny_table_names()
    own_prefix = f"tqp_{short_id}_"
    sql_tables: set[str] = set()
    patched: set[str] = set()
    unreadable: list[str] = []
    scanned = 0
    truncated = False
    for path in sorted(files):
        if not path.endswith(".py"):
            continue
        source = files[path]
        if scanned + len(source) > MAX_SCAN_BYTES:
            truncated = True
            continue
        scanned += len(source)
        tree = _parse(source)
        if tree is None:
            unreadable.append(path)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for m in _SQL_RE.finditer(node.value):
                    table = m.group(1).lower()
                    if table in znuny and not table.startswith(own_prefix):
                        sql_tables.add(table)
        patched.update(_patched_modules(tree))
    out = [f"Greift direkt per SQL auf die Znuny-Tabelle „{t}“ zu" for t in sorted(sql_tables)]
    out += [f"Verändert Tiqora-Code zur Laufzeit ({m})" for m in sorted(patched)]
    out += [
        f"Datei „{p}“ ist nicht lesbar (Syntaxfehler); statische Prüfung übersprungen"
        for p in unreadable
    ]
    if truncated:
        out.append("Das Paket enthält sehr viel Quelltext; die statische Prüfung wurde gekürzt")
    return out
