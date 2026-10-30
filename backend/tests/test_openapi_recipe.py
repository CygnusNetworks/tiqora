"""The generated OpenAPI files stay pure, stable JSON (the justfile recipe and the CLI)."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from tiqora.cli.openapi import _cmd_openapi

REPO = Path(__file__).resolve().parents[2]


def test_cli_writes_pure_sorted_json(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert _cmd_openapi(argparse.Namespace(output=str(out), indent=2)) == 0
    assert out.read_text().startswith('{\n  "components"')


def test_recipe_uses_the_cli_and_captures_no_stdout() -> None:
    # Redirecting stdout into the spec also captures the app's startup log line.
    justfile = (REPO / "justfile").read_text()
    recipe = re.search(r"^api-client-gen:\n((?:    .*\n)+)", justfile, re.MULTILINE)
    assert recipe is not None
    assert "uv run tiqora openapi -o ../packages/api-client/openapi.json" in recipe[1]
    assert ">" not in recipe[1]
