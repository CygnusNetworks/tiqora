"""Saved import mapping templates ("Zuordnungsvorlagen"): private by default, optionally shared.

Only the owner changes or deletes one. A stale ``lock_version`` raises
:class:`TemplateConflict` (answered as 409). Never commits.
"""

from __future__ import annotations

import json
import logging
from typing import Literal, cast

from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.errors import CmdbError, CmdbForbidden, CmdbNotFound
from tiqora.cmdb.schemas import ImportTemplateIn, ImportTemplateOut, MappingRowIn
from tiqora.cmdb.transfer.mapping import ImportOptions
from tiqora.db.tiqora.models import TiqoraCmdbImportTemplate
from tiqora.jobs.service import utc_now

logger = logging.getLogger(__name__)


class TemplateConflict(CmdbError):
    """The template was changed since the caller read it (``lock_version`` is stale)."""


def _options(raw: str) -> ImportOptions:
    try:
        return ImportOptions.model_validate_json(raw)
    except (ValidationError, ValueError):
        logger.warning("cmdb: stored import options are not valid, reading the defaults")
        return ImportOptions.model_validate({})


def _mapping(raw: str) -> list[MappingRowIn]:
    try:
        data = json.loads(raw)
        return [MappingRowIn.model_validate(r) for r in data]
    except (ValidationError, ValueError, TypeError):
        logger.warning("cmdb: stored import mapping is not valid, reading it as empty")
        return []


def template_out(row: TiqoraCmdbImportTemplate, user_id: int) -> ImportTemplateOut:
    return ImportTemplateOut(
        id=int(row.id),
        name=row.name,
        class_id=int(row.class_id),
        file_format=cast(Literal["csv", "xlsx"], row.file_format),
        options=_options(row.options),
        mapping=_mapping(row.mapping),
        shared=bool(row.shared),
        owned=int(row.owner_user_id) == user_id,
        owner_user_id=int(row.owner_user_id),
        lock_version=int(row.lock_version),
        created_at=row.created_at,
        changed_at=row.changed_at,
    )


def _dump_options(options: ImportOptions) -> str:
    return options.model_dump_json()


def _dump_mapping(mapping: list[MappingRowIn]) -> str:
    return json.dumps([m.model_dump(mode="json") for m in mapping], ensure_ascii=False)


async def list_templates(
    session: AsyncSession, user_id: int, class_id: int | None = None
) -> list[TiqoraCmdbImportTemplate]:
    """The user's own templates and everybody's shared ones, own first, then by name."""
    stmt = select(TiqoraCmdbImportTemplate).where(
        or_(
            TiqoraCmdbImportTemplate.owner_user_id == user_id,
            TiqoraCmdbImportTemplate.shared.is_(True),
        )
    )
    if class_id is not None:
        stmt = stmt.where(TiqoraCmdbImportTemplate.class_id == class_id)
    rows = (await session.execute(stmt)).scalars().all()
    return sorted(
        rows, key=lambda r: (int(r.owner_user_id) != user_id, r.name.casefold(), int(r.id))
    )


async def get_template(
    session: AsyncSession, user_id: int, template_id: int
) -> TiqoraCmdbImportTemplate:
    """Somebody else's private template is "not found"."""
    row = await session.get(TiqoraCmdbImportTemplate, template_id)
    if row is None or (int(row.owner_user_id) != user_id and not row.shared):
        raise CmdbNotFound(f"import template {template_id}")
    return row


async def create_template(
    session: AsyncSession, user_id: int, data: ImportTemplateIn
) -> TiqoraCmdbImportTemplate:
    now = utc_now()
    row = TiqoraCmdbImportTemplate(
        name=data.name.strip(),
        class_id=data.class_id,
        file_format=data.file_format,
        options=_dump_options(data.options),
        mapping=_dump_mapping(data.mapping),
        owner_user_id=user_id,
        shared=data.shared,
        lock_version=1,
        created_at=now,
        changed_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def _owned(session: AsyncSession, user_id: int, template_id: int) -> TiqoraCmdbImportTemplate:
    """Somebody else's private template is "not found"; a shared one is visible but not
    changeable (``CmdbForbidden``)."""
    row = await get_template(session, user_id, template_id)
    if int(row.owner_user_id) != user_id:
        raise CmdbForbidden(f"import template {template_id}")
    return row


async def update_template(
    session: AsyncSession, user_id: int, template_id: int, data: ImportTemplateIn
) -> TiqoraCmdbImportTemplate:
    row = await _owned(session, user_id, template_id)
    if data.lock_version is None or data.lock_version != int(row.lock_version):
        raise TemplateConflict(f"import template {template_id}")
    result = await session.execute(
        update(TiqoraCmdbImportTemplate)
        .where(
            TiqoraCmdbImportTemplate.id == template_id,
            TiqoraCmdbImportTemplate.lock_version == data.lock_version,
        )
        .values(
            name=data.name.strip(),
            class_id=data.class_id,
            file_format=data.file_format,
            options=_dump_options(data.options),
            mapping=_dump_mapping(data.mapping),
            shared=data.shared,
            lock_version=TiqoraCmdbImportTemplate.lock_version + 1,
            changed_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise TemplateConflict(f"import template {template_id}")
    await session.refresh(row)
    return row


async def delete_template(session: AsyncSession, user_id: int, template_id: int) -> None:
    row = await _owned(session, user_id, template_id)
    await session.delete(row)
    await session.flush()


__all__ = [
    "TemplateConflict",
    "create_template",
    "delete_template",
    "get_template",
    "list_templates",
    "template_out",
    "update_template",
]
