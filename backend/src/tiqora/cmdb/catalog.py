"""General catalog (Znuny ``general_catalog``): states, yes/no and attribute lists."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.errors import CmdbError, CmdbNotFound
from tiqora.cmdb.seed import INCIDENT_STATE
from tiqora.db.tiqora.models import TiqoraCmdbCatalog
from tiqora.jobs.service import utc_now

_UNSET: Final[Any] = object()


class CatalogMisconfigured(CmdbError):
    """A required catalog item (e.g. the incident state with functionality ``warning``) is gone."""


class LastWarningState(CatalogMisconfigured):
    """The change would leave no valid incident state with functionality ``warning``."""


class CatalogItemExists(CmdbError):
    """``(catalog_class, item_key)`` is taken."""


@dataclass(frozen=True, slots=True)
class CatalogItem:
    id: int
    catalog_class: str
    item_key: str
    name: str
    functionality: str | None
    position: int
    valid: bool
    comment: str | None


def _item(row: TiqoraCmdbCatalog) -> CatalogItem:
    return CatalogItem(
        id=int(row.id),
        catalog_class=row.catalog_class,
        item_key=row.item_key,
        name=row.name,
        functionality=row.functionality,
        position=int(row.position),
        valid=bool(row.valid),
        comment=row.comment,
    )


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_classes(self) -> list[str]:
        rows = await self._s.execute(
            select(TiqoraCmdbCatalog.catalog_class)
            .distinct()
            .order_by(TiqoraCmdbCatalog.catalog_class)
        )
        return [str(c) for c in rows.scalars().all()]

    async def items(
        self, catalog_class: str, *, include_invalid: bool = False
    ) -> list[CatalogItem]:
        stmt = select(TiqoraCmdbCatalog).where(TiqoraCmdbCatalog.catalog_class == catalog_class)
        if not include_invalid:
            stmt = stmt.where(TiqoraCmdbCatalog.valid.is_(True))
        stmt = stmt.order_by(
            TiqoraCmdbCatalog.position, TiqoraCmdbCatalog.name, TiqoraCmdbCatalog.id
        )
        return [_item(r) for r in (await self._s.execute(stmt)).scalars().all()]

    async def get(self, item_id: int) -> CatalogItem:
        row = await self._s.get(TiqoraCmdbCatalog, item_id)
        if row is None:
            raise CmdbNotFound(f"catalog item {item_id}")
        return _item(row)

    async def create(
        self,
        catalog_class: str,
        item_key: str,
        name: str,
        *,
        functionality: str | None = None,
        position: int = 0,
        valid: bool = True,
        comment: str | None = None,
    ) -> CatalogItem:
        taken = (
            await self._s.execute(
                select(TiqoraCmdbCatalog.id).where(
                    TiqoraCmdbCatalog.catalog_class == catalog_class,
                    TiqoraCmdbCatalog.item_key == item_key,
                )
            )
        ).first()
        if taken is not None:
            raise CatalogItemExists(f"{catalog_class}/{item_key}")
        now = utc_now()
        row = TiqoraCmdbCatalog(
            catalog_class=catalog_class,
            item_key=item_key,
            name=name,
            functionality=functionality,
            position=position,
            valid=valid,
            comment=comment,
            created_at=now,
            changed_at=now,
        )
        self._s.add(row)
        await self._s.flush()
        return _item(row)

    async def update(
        self,
        item_id: int,
        *,
        name: str | None = None,
        functionality: Any = _UNSET,
        position: int | None = None,
        valid: bool | None = None,
        comment: Any = _UNSET,
    ) -> CatalogItem:
        """Change an item; ``catalog_class`` and ``item_key`` are immutable."""
        row = await self._s.get(TiqoraCmdbCatalog, item_id)
        if row is None:
            raise CmdbNotFound(f"catalog item {item_id}")
        if name is not None:
            row.name = name
        if functionality is not _UNSET:
            row.functionality = functionality
        if position is not None:
            row.position = position
        if valid is not None:
            row.valid = valid
        if comment is not _UNSET:
            row.comment = comment
        row.changed_at = utc_now()
        await self._s.flush()
        return _item(row)

    async def check_keeps_warning_state(
        self, item_id: int, *, functionality: str | None, valid: bool
    ) -> None:
        """Raise ``LastWarningState`` when giving item ``item_id`` this functionality and
        validity would leave no valid ``warning`` incident state (every recompute and CI save
        would then fail). Used by the admin editor; the Znuny importer may pass through such a
        state on its way to the imported catalog."""
        row = await self._s.get(TiqoraCmdbCatalog, item_id)
        if row is None:
            raise CmdbNotFound(f"catalog item {item_id}")
        is_warning = row.valid and row.functionality == "warning"
        if row.catalog_class != INCIDENT_STATE or not is_warning:
            return
        if valid and functionality == "warning":
            return
        other = (
            await self._s.execute(
                select(TiqoraCmdbCatalog.id)
                .where(
                    TiqoraCmdbCatalog.catalog_class == INCIDENT_STATE,
                    TiqoraCmdbCatalog.functionality == "warning",
                    TiqoraCmdbCatalog.valid.is_(True),
                    TiqoraCmdbCatalog.id != item_id,
                )
                .limit(1)
            )
        ).first()
        if other is None:
            raise LastWarningState(f"catalog item {item_id} is the last valid warning state")

    async def state_ids_by_functionality(self, catalog_class: str) -> dict[str, list[int]]:
        """Ids per functionality, invalid items included (CIs may still carry them)."""
        rows = await self._s.execute(
            select(TiqoraCmdbCatalog.functionality, TiqoraCmdbCatalog.id)
            .where(
                TiqoraCmdbCatalog.catalog_class == catalog_class,
                TiqoraCmdbCatalog.functionality.is_not(None),
            )
            .order_by(TiqoraCmdbCatalog.position, TiqoraCmdbCatalog.id)
        )
        out: dict[str, list[int]] = {}
        for func, item_id in rows.all():
            out.setdefault(str(func), []).append(int(item_id))
        return out

    async def warning_state_id(self) -> int:
        """The valid incident state with functionality ``warning`` and the lowest position."""
        row = (
            await self._s.execute(
                select(TiqoraCmdbCatalog.id)
                .where(
                    TiqoraCmdbCatalog.catalog_class == INCIDENT_STATE,
                    TiqoraCmdbCatalog.functionality == "warning",
                    TiqoraCmdbCatalog.valid.is_(True),
                )
                .order_by(TiqoraCmdbCatalog.position, TiqoraCmdbCatalog.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise CatalogMisconfigured("no valid incident state with functionality 'warning'")
        return int(row)

    async def functionality_of(self, ids: list[int]) -> dict[int, str | None]:
        out: dict[int, str | None] = {}
        unique = sorted(set(ids))
        for i in range(0, len(unique), 1000):
            rows = await self._s.execute(
                select(TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.functionality).where(
                    TiqoraCmdbCatalog.id.in_(unique[i : i + 1000])
                )
            )
            out.update({int(k): v for k, v in rows.all()})
        return out

    async def resolve_name(self, catalog_class: str, text: str) -> int | None:
        """Valid item id by display name or key, case-insensitive; ``None`` if unknown."""
        needle = text.strip().casefold()
        if not needle:
            return None
        for item in await self.items(catalog_class):
            if item.name.casefold() == needle or item.item_key.casefold() == needle:
                return item.id
        return None


__all__ = ["CatalogItem", "CatalogItemExists", "CatalogMisconfigured", "CatalogService"]
