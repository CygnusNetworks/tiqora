"""MCP server process entrypoint (FastMCP placeholder)."""

import structlog

from tiqora.config import get_settings
from tiqora.logging_setup import configure_logging

logger = structlog.get_logger(__name__)


def run_mcp() -> None:
    """Start the MCP server.

    FastMCP tool registration lands in Phase 2. Scaffold provides a stable
    process entrypoint for Docker and compose.
    """
    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "tiqora_mcp_placeholder",
        message="FastMCP server not yet implemented",
    )
    import time

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    run_mcp()
