"""CLI entrypoint for running the Tiqora API process."""

import argparse

import uvicorn

from tiqora import __version__
from tiqora.config import get_settings


def main() -> None:
    """Parse CLI args and run uvicorn for the API factory."""
    parser = argparse.ArgumentParser(prog="tiqora", description="Tiqora ticket system")
    parser.add_argument("--version", action="version", version=f"tiqora {__version__}")
    parser.add_argument(
        "command",
        nargs="?",
        default="api",
        choices=["api", "worker", "mcp"],
        help="Process role (default: api)",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if args.command == "api":
        settings = get_settings()
        uvicorn.run(
            "tiqora.api.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload or settings.debug,
            log_level=settings.log_level.lower(),
        )
    elif args.command == "worker":
        from tiqora.worker.__main__ import run_worker

        run_worker()
    elif args.command == "mcp":
        from tiqora.mcp_server.__main__ import run_mcp

        run_mcp()


if __name__ == "__main__":
    main()
