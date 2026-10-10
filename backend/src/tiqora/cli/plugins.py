"""``tiqora plugins ...`` CLI: inspect, verify and bake plugins (no DB access)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tiqora.plugins.bake import BakeError, bake, core_constraints, verify_installed_plugins
from tiqora.plugins.discovery import discover_plugins

DEFAULT_IMPORT_TIMEOUT = 20.0


def add_plugins_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("plugins", help="Plugins: list, verify and bake into an image")
    plugins_sub = p.add_subparsers(dest="plugins_command")

    list_p = plugins_sub.add_parser("list", help="List installed plugins (no import, no DB)")
    list_p.add_argument("--json", action="store_true", dest="as_json")
    list_p.set_defaults(func=_cmd_list)

    verify_p = plugins_sub.add_parser("verify", help="Check installed plugins; exit 1 on problems")
    verify_p.add_argument(
        "--import", action="store_true", dest="import_check", help="also load each entry point"
    )
    verify_p.add_argument(
        "--strict", action="store_true", help="fail when a plugin requires tiqora/tiqora-sdk"
    )
    verify_p.add_argument("--timeout", type=float, default=DEFAULT_IMPORT_TIMEOUT)
    verify_p.set_defaults(func=_cmd_verify)

    constraints_p = plugins_sub.add_parser(
        "constraints", help="Print Name==version of every installed distribution"
    )
    constraints_p.set_defaults(func=_cmd_constraints)

    bake_p = plugins_sub.add_parser(
        "bake",
        help="Generate Dockerfile + plugins.txt (+ wheels/) for an image with plugins baked in",
        description=(
            "plugins.txt: pinned names, 'name @ https://…whl', local .whl paths, optional "
            "--hash=sha256:… per line (recommended: pip then requires hashes) and at most one "
            "--index-url. Git sources and --extra-index-url are rejected."
        ),
    )
    bake_p.add_argument("plugins_txt", type=Path)
    bake_p.add_argument("--output", "-o", type=Path, required=True)
    bake_p.add_argument("--base-image", default=None)
    bake_p.set_defaults(func=_cmd_bake)


async def _cmd_list(args: argparse.Namespace) -> int:
    from tiqora.plugins.compat import check_compat, release_core_version

    core = release_core_version()
    rows = []
    for d in discover_plugins():
        problem = d.problem
        if problem is None and d.manifest is not None:
            problem = check_compat(d.manifest, core=core)
        rows.append(
            {
                "id": d.plugin_id,
                "name": d.manifest.name if d.manifest else None,
                "version": d.dist_version,
                "distribution": d.dist_name,
                "problem": problem,
            }
        )
    if args.as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))  # noqa: T201 — CLI output
    else:
        for r in rows:
            status = r["problem"] or "ok"
            print(f"{r['id']}  {r['version']}  {status}")  # noqa: T201 — CLI output
    return 0


async def _cmd_verify(args: argparse.Namespace) -> int:
    problems = await verify_installed_plugins(
        import_check=args.import_check, strict=args.strict, timeout=args.timeout
    )
    for line in problems:
        print(line, file=sys.stderr)  # noqa: T201 — CLI output
    return 1 if problems else 0


async def _cmd_constraints(args: argparse.Namespace) -> int:
    for line in core_constraints():
        print(line)  # noqa: T201 — CLI output
    return 0


async def _cmd_bake(args: argparse.Namespace) -> int:
    try:
        bake(args.plugins_txt, args.output, base_image=args.base_image)
    except (BakeError, OSError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)  # noqa: T201 — CLI output
        return 2
    print(f"Geschrieben nach {args.output}")  # noqa: T201 — CLI output
    return 0
