"""Default general-catalog rows and the idempotent seeder.

The migration inserts the same rows as literals; ``seed_defaults`` is the safety net behind
``POST /admin/cmdb/seed-defaults`` and the test world. Link types are owned by the ITSM core:
the seeder delegates to :func:`tiqora.itsm.link_type_defaults.ensure_default_link_types`.

Names and functionalities are Znuny's (ITSMConfigurationManagement general catalog classes
``ITSM::ConfigItem::DeploymentState``, ``ITSM::Core::IncidentState``, ``ITSM::ConfigItem::YesNo``).
"""

from __future__ import annotations

from typing import Final, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraCmdbCatalog
from tiqora.itsm.link_type_defaults import ensure_default_link_types
from tiqora.jobs.service import utc_now

DEPLOYMENT_STATE: Final = "ITSM::ConfigItem::DeploymentState"
INCIDENT_STATE: Final = "ITSM::Core::IncidentState"
YES_NO: Final = "ITSM::ConfigItem::YesNo"

#: ``created_by`` of rows the seeder writes (Znuny's root agent)
SYSTEM_USER_ID: Final = 1


class CatalogSeed(NamedTuple):
    catalog_class: str
    item_key: str
    name: str
    functionality: str | None
    position: int


DEFAULT_CATALOG_ROWS: Final[tuple[CatalogSeed, ...]] = (
    CatalogSeed(DEPLOYMENT_STATE, "expired", "Expired", "postproductive", 10),
    CatalogSeed(DEPLOYMENT_STATE, "inactive", "Inactive", "postproductive", 20),
    CatalogSeed(DEPLOYMENT_STATE, "maintenance", "Maintenance", "productive", 30),
    CatalogSeed(DEPLOYMENT_STATE, "pilot", "Pilot", "preproductive", 40),
    CatalogSeed(DEPLOYMENT_STATE, "planned", "Planned", "preproductive", 50),
    CatalogSeed(DEPLOYMENT_STATE, "production", "Production", "productive", 60),
    CatalogSeed(DEPLOYMENT_STATE, "repair", "Repair", "productive", 70),
    CatalogSeed(DEPLOYMENT_STATE, "retired", "Retired", "postproductive", 80),
    CatalogSeed(DEPLOYMENT_STATE, "review", "Review", "productive", 90),
    CatalogSeed(DEPLOYMENT_STATE, "test_qa", "Test/QA", "preproductive", 100),
    CatalogSeed(INCIDENT_STATE, "operational", "Operational", "operational", 10),
    CatalogSeed(INCIDENT_STATE, "warning", "Warning", "warning", 20),
    CatalogSeed(INCIDENT_STATE, "incident", "Incident", "incident", 30),
    CatalogSeed(YES_NO, "yes", "Yes", None, 10),
    CatalogSeed(YES_NO, "no", "No", None, 20),
)


async def seed_defaults(session: AsyncSession) -> None:
    """Insert every missing default catalog row and link type. Flushes; the caller commits."""
    existing = {
        (cls, key)
        for cls, key in (
            await session.execute(
                select(TiqoraCmdbCatalog.catalog_class, TiqoraCmdbCatalog.item_key)
            )
        ).all()
    }
    now = utc_now()
    for row in DEFAULT_CATALOG_ROWS:
        if (row.catalog_class, row.item_key) in existing:
            continue
        session.add(
            TiqoraCmdbCatalog(
                catalog_class=row.catalog_class,
                item_key=row.item_key,
                name=row.name,
                functionality=row.functionality,
                position=row.position,
                valid=True,
                created_at=now,
                changed_at=now,
            )
        )
    await session.flush()
    await ensure_default_link_types(session, user_id=SYSTEM_USER_ID)


__all__ = [
    "DEFAULT_CATALOG_ROWS",
    "DEPLOYMENT_STATE",
    "INCIDENT_STATE",
    "SYSTEM_USER_ID",
    "YES_NO",
    "CatalogSeed",
    "seed_defaults",
]
