"""MCP server process entrypoint — runs the FastMCP streamable-HTTP server."""

from tiqora.config import get_settings
from tiqora.logging_setup import configure_logging
from tiqora.mcp_server.server import run_mcp_server


def run_mcp() -> None:
    """Start the Tiqora MCP server (standalone process)."""
    settings = get_settings()
    configure_logging(settings)
    run_mcp_server(host="0.0.0.0", port=8001)


if __name__ == "__main__":
    run_mcp()
