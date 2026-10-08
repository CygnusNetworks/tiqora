"""Read-only listing of registered extension points, mounted at
``/api/v1/admin/extensions``."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from tiqora.api.v1.admin.deps import AdminUser
from tiqora.extensions import get_registry

router = APIRouter(prefix="/extensions", tags=["admin:extensions"])


class ExtensionItemOut(BaseModel):
    key: str
    owner: str


class ExtensionPointOut(BaseModel):
    key: str
    description: str
    sdk_stable: bool
    items: list[ExtensionItemOut]


@router.get("", response_model=list[ExtensionPointOut])
async def list_extensions(admin: AdminUser) -> list[ExtensionPointOut]:
    _ = admin
    registry = get_registry()
    return [
        ExtensionPointOut(
            key=point.key,
            description=point.description,
            sdk_stable=point.sdk_stable,
            items=[
                ExtensionItemOut(key=r.key, owner=r.owner) for r in registry.registrations(point)
            ],
        )
        for point in registry.points()
    ]
