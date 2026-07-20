"""CLI entrypoint for running the Tiqora API, worker, MCP, and index tools."""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn

from tiqora import __version__
from tiqora.cli.dev import add_dev_subparser
from tiqora.cli.migrate import add_migrate_subparser, run_migrate
from tiqora.cli.ownership import add_ownership_subparser
from tiqora.config import get_settings


def main(argv: list[str] | None = None) -> None:
    """Parse CLI args and dispatch to the requested process role."""
    parser = argparse.ArgumentParser(prog="tiqora", description="Tiqora ticket system")
    parser.add_argument("--version", action="version", version=f"tiqora {__version__}")
    sub = parser.add_subparsers(dest="command")

    # Default-friendly: `tiqora` / `tiqora api` / `tiqora worker` / `tiqora mcp`
    sub.add_parser("api", help="Run the FastAPI HTTP server")
    sub.add_parser("worker", help="Run the background worker (poller)")
    sub.add_parser("mcp", help="Run the MCP server")
    add_ownership_subparser(sub)
    add_migrate_subparser(sub)
    add_dev_subparser(sub)

    index_p = sub.add_parser("index", help="Search index maintenance")
    index_sub = index_p.add_subparsers(dest="index_command")
    rebuild_p = index_sub.add_parser("rebuild", help="Bulk re-index all tickets")
    rebuild_p.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore watermark and rebuild from ticket id 0",
    )
    rebuild_p.add_argument("--batch-size", type=int, default=None)

    # Also accept legacy positional: tiqora api --host ...
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")

    args = parser.parse_args(argv)
    command = args.command or "api"

    if command == "api":
        settings = get_settings()
        uvicorn.run(
            "tiqora.api.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload or settings.debug,
            log_level=settings.log_level.lower(),
        )
    elif command == "worker":
        from tiqora.worker.__main__ import run_worker

        run_worker()
    elif command == "mcp":
        from tiqora.mcp_server.__main__ import run_mcp

        run_mcp()
    elif command == "index":
        if args.index_command == "rebuild":
            from tiqora.worker.indexer import rebuild_index

            result = asyncio.run(
                rebuild_index(
                    resume=not args.no_resume,
                    batch_size=args.batch_size,
                )
            )
            print(result)  # noqa: T201 — CLI output
        else:
            index_p.print_help()
            sys.exit(2)
    elif command == "ownership":
        func = getattr(args, "func", None)
        if func is None:
            sub.choices["ownership"].print_help()
            sys.exit(2)
        exit_code = asyncio.run(func(args))
        sys.exit(exit_code)
    elif command == "migrate":
        sys.exit(run_migrate(args))
    elif command == "dev":
        func = getattr(args, "func", None)
        if func is None:
            sub.choices["dev"].print_help()
            sys.exit(2)
        exit_code = asyncio.run(func(args))
        sys.exit(exit_code)
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
