"""Built-in class templates (computer, hardware, location, network, software).

The JSON files in ``class_templates/`` hold a class name, an icon, the attribute tree and the
general-catalog items its catalog attributes use (Znuny's default names). They are loaded once at
import time; ``tests/test_cmdb_templates.py`` validates every template against the definition rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tiqora.cmdb.definition import AttributeDef, parse_attrs
from tiqora.cmdb.seed import CatalogSeed

_DIR: Final = Path(__file__).parent / "class_templates"


@dataclass(frozen=True)
class ClassTemplate:
    key: str
    name: str
    icon: str
    attributes: list[AttributeDef]
    catalog_items: list[CatalogSeed]


def _load(path: Path) -> ClassTemplate:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ClassTemplate(
        key=raw["key"],
        name=raw["name"],
        icon=raw["icon"],
        attributes=parse_attrs(raw["attributes"]),
        catalog_items=[
            CatalogSeed(
                c["catalog_class"], c["item_key"], c["name"], c["functionality"], c["position"]
            )
            for c in raw["catalog_items"]
        ],
    )


CLASS_TEMPLATES: Final[dict[str, ClassTemplate]] = {
    t.key: t for t in (_load(p) for p in sorted(_DIR.glob("*.json")))
}

__all__ = ["CLASS_TEMPLATES", "ClassTemplate"]
