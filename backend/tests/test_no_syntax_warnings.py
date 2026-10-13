"""Every module under src/ compiles without SyntaxWarning (prod prints them at startup)."""

from __future__ import annotations

import warnings
from pathlib import Path

import tiqora


def test_sources_compile_without_syntax_warnings() -> None:
    root = Path(tiqora.__file__).parent
    bad: list[str] = []
    for path in sorted(root.rglob("*.py")):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        bad += [f"{path}: {w.message}" for w in caught if issubclass(w.category, SyntaxWarning)]
    assert not bad, bad
