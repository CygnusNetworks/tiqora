"""Every OpenAPI component schema keeps its plain model name.

Two Pydantic response/request models with the same class name in different modules make
FastAPI qualify BOTH component names (``tiqora__plugins__repositories__CatalogOut``), which
silently renames the generated ``Schemas["CatalogOut"]`` the frontend imports. Found when the
plugin catalog's ``CatalogOut`` met the ITSM catalog's ``CatalogOut`` at the wave-4 merge.
"""

from __future__ import annotations

from tiqora.api.app import create_app
from tiqora.config import Settings


def test_no_qualified_component_schema_names() -> None:
    schemas = create_app(Settings(environment="test")).openapi()["components"]["schemas"]
    assert [name for name in schemas if name.startswith("tiqora__")] == []
    assert "PluginCatalogOut" in schemas and "CatalogOut" in schemas
