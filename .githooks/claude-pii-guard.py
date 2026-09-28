#!/usr/bin/env python3
"""Claude Code PreToolUse hook: refuse Write/Edit/MultiEdit/NotebookEdit calls
whose new text contains real personal data (same checks as the git hooks,
see pii_check.py). Exit 2 blocks the call and tells the model why."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pii_check  # noqa: E402


def new_text(tool_input: dict) -> str:
    parts = [
        tool_input.get("content"),
        tool_input.get("new_string"),
        tool_input.get("new_source"),
    ]
    for edit in tool_input.get("edits") or []:
        parts.append(edit.get("new_string"))
    return "\n".join(p for p in parts if isinstance(p, str))


def main() -> int:
    payload = json.load(sys.stdin)
    text = new_text(payload.get("tool_input") or {})
    if not text:
        return 0
    problems = pii_check.check(text)
    if not problems:
        return 0
    print(
        "Blocked: the new text contains personal data ("
        + ", ".join(sorted(set(problems))[:5])
        + "). Use made-up values (example.org, 123-45-67-89-0, Erika Beispiel).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
