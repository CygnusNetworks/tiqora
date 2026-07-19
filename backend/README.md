# Tiqora backend

Python package `tiqora` — FastAPI application, workers, and MCP server.

See the repository root [README](../README.md) and [docs/development.md](../docs/development.md).

```bash
uv sync
uv run uvicorn tiqora.api.app:create_app --factory --reload
```
