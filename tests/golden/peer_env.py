#!/usr/bin/env python3
"""Resolve GOLDEN_PEER from peers.yaml and print shell exports / JSON.

Examples::

    eval "$(python3 tests/golden/peer_env.py znuny-6.5)"
    python3 tests/golden/peer_env.py --list
    python3 tests/golden/peer_env.py --json znuny-7.3
    python3 tests/golden/peer_env.py --check znuny-6.5   # exit 1 if source missing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover — pyyaml is a backend dep
    yaml = None  # type: ignore[assignment]

GOLDEN_DIR = Path(__file__).resolve().parent
REPO_ROOT = GOLDEN_DIR.parents[1]
PEERS_FILE = GOLDEN_DIR / "peers.yaml"


def _load() -> dict:
    if yaml is None:
        # Minimal fallback parser for our simple YAML (no nested lists).
        return _load_simple(PEERS_FILE.read_text(encoding="utf-8"))
    data = yaml.safe_load(PEERS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"invalid {PEERS_FILE}")
    return data


def _load_simple(text: str) -> dict:
    """Tiny YAML subset reader if PyYAML is unavailable outside backend venv."""
    default = "znuny-6.5"
    peers: dict[str, dict[str, str]] = {}
    current: str | None = None
    in_peers = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("default:"):
            default = line.split(":", 1)[1].strip()
            continue
        if line.startswith("peers:"):
            in_peers = True
            continue
        if not in_peers:
            continue
        if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
            current = line.strip().rstrip(":")
            peers[current] = {}
            continue
        if current and line.startswith("    ") and ":" in line:
            k, v = line.strip().split(":", 1)
            peers[current][k.strip()] = v.strip().strip("\"'")
    return {"default": default, "peers": peers}


def peer_ids(data: dict) -> list[str]:
    peers = data.get("peers") or {}
    return list(peers.keys())


def resolve(peer_id: str | None = None) -> dict:
    data = _load()
    pid = (peer_id or os.environ.get("GOLDEN_PEER") or data.get("default") or "znuny-6.5").strip()
    peers = data.get("peers") or {}
    if pid not in peers:
        known = ", ".join(peer_ids(data))
        raise SystemExit(f"Unknown GOLDEN_PEER={pid!r}. Known: {known}")
    meta = dict(peers[pid])
    source_dir = meta.get("source_dir") or pid
    source_path = REPO_ROOT / source_dir
    return {
        "GOLDEN_PEER": pid,
        "GOLDEN_SOURCE_DIR": source_dir,
        "GOLDEN_SOURCE_PATH": str(source_path),
        "GOLDEN_SOURCE_OK": "1" if _source_ok(source_path) else "0",
        "GOLDEN_SCHEMA_PROFILE": meta.get("schema_profile") or pid,
        "GOLDEN_INSTALL_HOME": meta.get("install_home") or "/opt/otrs",
        "GOLDEN_PRODUCT": meta.get("product") or "",
        "GOLDEN_VERSION": meta.get("version") or "",
        "GOLDEN_FRAMEWORK": meta.get("framework") or "",
        # Compose project names: lowercase alnum / hyphen / underscore only (no dots).
        "GOLDEN_COMPOSE_PROJECT": _compose_project(pid),
        "GOLDEN_DB_URL": os.environ.get(
            "GOLDEN_DB_URL", "mysql+pymysql://znuny:znuny@127.0.0.1:3307/znuny"
        ),
        "GOLDEN_DB_ASYNC_URL": os.environ.get(
            "GOLDEN_DB_ASYNC_URL", "mysql+aiomysql://znuny:znuny@127.0.0.1:3307/znuny"
        ),
        "GOLDEN_NOTES": meta.get("notes") or "",
    }


def _compose_project(peer_id: str) -> str:
    """Docker Compose project name safe for peer ids like znuny-6.5."""
    safe = peer_id.lower().replace(".", "-")
    return f"tiqora-golden-{safe}"


def _source_ok(path: Path) -> bool:
    if not path.is_dir():
        return False
    db = path / "scripts" / "database"
    if not db.is_dir():
        return False
    # 6.5+ names or pre-6.4 otrs- prefix
    return any(
        (db / name).is_file()
        for name in (
            "schema.mysql.sql",
            "otrs-schema.mysql.sql",
        )
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("peer", nargs="?", default=None, help="Peer id (default: env or yaml default)")
    p.add_argument("--list", action="store_true", help="List peer ids")
    p.add_argument("--json", action="store_true", help="Print JSON instead of shell exports")
    p.add_argument(
        "--check",
        action="store_true",
        help="Exit 0 only if peer source_dir exists with schema SQL",
    )
    p.add_argument(
        "--list-ready",
        action="store_true",
        help="List peers whose source_dir is present on disk",
    )
    args = p.parse_args(argv)

    data = _load()
    if args.list:
        for pid in peer_ids(data):
            print(pid)
        return 0
    if args.list_ready:
        for pid in peer_ids(data):
            env = resolve(pid)
            if env["GOLDEN_SOURCE_OK"] == "1":
                print(pid)
        return 0

    env = resolve(args.peer)
    if args.check:
        if env["GOLDEN_SOURCE_OK"] != "1":
            print(
                f"ERROR: peer {env['GOLDEN_PEER']}: source missing or incomplete at "
                f"{env['GOLDEN_SOURCE_PATH']}\n"
                f"Copy/extract the release tree there (see tests/golden/README-multi-peer.md).",
                file=sys.stderr,
            )
            return 1
        print(f"OK {env['GOLDEN_PEER']} source at {env['GOLDEN_SOURCE_PATH']}")
        return 0

    if args.json:
        print(json.dumps(env, indent=2, sort_keys=True))
        return 0

    for key, val in sorted(env.items()):
        # Shell-safe single quotes
        escaped = str(val).replace("'", "'\"'\"'")
        print(f"export {key}='{escaped}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
