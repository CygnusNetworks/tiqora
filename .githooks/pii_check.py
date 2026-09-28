#!/usr/bin/env python3
"""Refuse text that contains real personal data (see docs/development.md).

Two per-clone sources, both inside .git/info and never committed:

* ``pii-denylist``: literal strings, one per line, case-insensitive.
* ``pii-hashes.json``: HMAC-SHA256 digests of real customer and agent
  identifiers (mail, WP-Nummer, customer id, phone, full name), written by
  ``scripts/pii-hash-sync``. Only digests leave production, never the values.

Usage:
    <unified diff> | pii_check.py patch "<label>"   # checks added lines only
    <plain text>   | pii_check.py text  "<label>"
Exit 1 with a report on stderr when something matches.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import subprocess
import sys
from pathlib import Path

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# Digit runs with the separators people type inside identifiers and numbers.
DIGITS_RE = re.compile(r"\+?\d[\d \t./-]{5,22}\d")
# Customer ids as they prefix Znuny logins ("z12345", "stw-1234567890").
CUSTOMER_ID_RE = re.compile(r"\b[a-z]{1,3}-?\d{5,10}\b", re.IGNORECASE)
NAME_RE = re.compile(r"\b([A-ZÄÖÜ][a-zäöüß]{2,})[ \t]+([A-ZÄÖÜ][a-zäöüß]{2,}(?:-[A-ZÄÖÜ][a-zäöüß]{2,})?)\b")
NAME_REVERSED_RE = re.compile(r"\b([A-ZÄÖÜ][a-zäöüß]{2,}),[ \t]*([A-ZÄÖÜ][a-zäöüß]{2,})\b")


def info_dir() -> Path:
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return Path(common) / "info"


def normalize_phone(digits: str) -> str:
    if digits.startswith("0049"):
        return "0" + digits[4:]
    if digits.startswith("49") and len(digits) > 10:
        return "0" + digits[2:]
    return digits


def candidates(text: str) -> set[tuple[str, str]]:
    """(kind, normalized value) pairs worth hashing."""
    found: set[tuple[str, str]] = set()
    for m in EMAIL_RE.finditer(text):
        found.add(("email", m.group(0).lower()))
    for m in DIGITS_RE.finditer(text):
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 10:
            found.add(("wp", digits))
        if len(digits) >= 7:
            found.add(("phone", normalize_phone(digits)))
    for m in CUSTOMER_ID_RE.finditer(text):
        found.add(("cid", m.group(0).lower()))
    for m in NAME_RE.finditer(text):
        found.add(("name", f"{m.group(1)} {m.group(2)}".lower()))
    for m in NAME_REVERSED_RE.finditer(text):
        found.add(("name", f"{m.group(2)} {m.group(1)}".lower()))
    return found


def allowed() -> set[tuple[str, str]]:
    """Placeholder rows in production that match ordinary words ("Invalid
    User"); listed in .githooks/pii-allowlist as ``kind:value``."""
    path = Path(__file__).with_name("pii-allowlist")
    pairs: set[tuple[str, str]] = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                kind, value = line.split(":", 1)
                pairs.add((kind.strip(), value.strip().lower()))
    return pairs


def check(text: str) -> list[str]:
    problems: list[str] = []
    folder = info_dir()

    denylist = folder / "pii-denylist"
    if denylist.is_file():
        lowered = text.lower()
        for line in denylist.read_text(encoding="utf-8").splitlines():
            entry = line.strip()
            if entry and not entry.startswith("#") and entry.lower() in lowered:
                problems.append(f"denylist entry {entry[:2]}…")

    hashes = folder / "pii-hashes.json"
    if hashes.is_file():
        data = json.loads(hashes.read_text(encoding="utf-8"))
        key = bytes.fromhex(data["key"])
        known: dict[str, set[str]] = {k: set(v) for k, v in data["hashes"].items()}
        for kind, value in sorted(candidates(text) - allowed()):
            digest = hmac.new(key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()
            if digest in known.get(kind, ()):
                problems.append(f"real {kind} {value[:3]}…")
    return problems


def added_lines(patch: str) -> str:
    return "\n".join(
        line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")
    )


def main() -> int:
    mode, label = sys.argv[1], sys.argv[2]
    # Diffs may carry binary or non-UTF-8 content; decode what is text.
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    text = added_lines(raw) if mode == "patch" else raw
    problems = check(text)
    if not problems:
        return 0
    print(f"pii-check: {label} contains personal data:", file=sys.stderr)
    for problem in sorted(set(problems))[:10]:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "Replace it with made-up values (example.org, 123-45-67-89-0, Erika Beispiel).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
