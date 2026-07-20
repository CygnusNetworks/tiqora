"""``tiqora openapi``: dump the FastAPI app's OpenAPI schema as JSON.

Used to keep ``docs/api/openapi.json`` in sync with the actual REST surface
(``/api/v1``, ``/api/portal``, ``/znuny-compat`` static routes). Does not
require a live database or Redis connection — it only needs the app's route
table, which is built at import time.
"""

from __future__ import annotations

import argparse
import json
import sys


def add_openapi_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("openapi", help="Dump the FastAPI OpenAPI schema as JSON")
    p.add_argument(
        "-o",
        "--output",
        default=None,
        help="Write to this file instead of stdout",
    )
    p.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation (default: 2)",
    )
    p.set_defaults(func=_cmd_openapi)


def _cmd_openapi(args: argparse.Namespace) -> int:
    from tiqora.api.app import create_app

    app = create_app()
    schema = app.openapi()
    text = json.dumps(schema, indent=args.indent, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0
