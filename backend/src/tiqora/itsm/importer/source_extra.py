"""Znuny reads the itsm1 adapter does not offer (C3 fallback): links by CI id and change times.

Plain ``text()`` statements with bound parameters on Znuny tables only (no ``tiqora_*`` join).
Attachments (``virtual_fs``) and change counts follow with their tasks (11, 12).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.importer.ports import EPOCH, ZnunyLink
from tiqora.itsm.znuny_adapter.catalog import chunks

_LINKS_BY_KEY = text(
    "SELECT so.name, r.source_key, tobj.name, r.target_key, lt.name, ls.name,"
    " r.create_time, r.create_by FROM link_relation r"
    " JOIN link_object so ON so.id = r.source_object_id"
    " JOIN link_object tobj ON tobj.id = r.target_object_id"
    " JOIN link_type lt ON lt.id = r.type_id"
    " JOIN link_state ls ON ls.id = r.state_id"
    " WHERE (so.name = :obj AND r.source_key IN :keys)"
    " OR (tobj.name = :obj AND r.target_key IN :keys)"
    " ORDER BY r.source_object_id, r.source_key, r.target_object_id, r.target_key, r.type_id"
).bindparams(bindparam("keys", expanding=True))


async def links_for_cis(
    session: AsyncSession, ci_ids: Sequence[int], *, ci_object: str
) -> list[ZnunyLink]:
    """Every ``link_relation`` row with one of the CIs at either end (each row once)."""
    seen: set[str] = set()
    out: list[ZnunyLink] = []
    for part in chunks(sorted({int(i) for i in ci_ids})):
        params = {"obj": ci_object, "keys": [str(i) for i in part]}
        for r in (await session.execute(_LINKS_BY_KEY, params)).all():
            link = ZnunyLink(
                source_object=str(r[0]),
                source_key=str(r[1]),
                target_object=str(r[2]),
                target_key=str(r[3]),
                type_name=str(r[4]),
                state_name=str(r[5]),
                created_at=r[6] if r[6] is not None else EPOCH,
                created_by=int(r[7]) if r[7] is not None else 1,
            )
            if link.map_key not in seen:
                seen.add(link.map_key)
                out.append(link)
    return out


async def ci_count(session: AsyncSession) -> int:
    return int((await session.execute(text("SELECT COUNT(*) FROM configitem"))).scalar_one())


async def max_ci_change_time(session: AsyncSession) -> datetime | None:
    value = (await session.execute(text("SELECT MAX(change_time) FROM configitem"))).scalar_one()
    return value if isinstance(value, datetime) else None


async def count_cis_changed_since(session: AsyncSession, since: datetime) -> int:
    return int(
        (
            await session.execute(
                text("SELECT COUNT(*) FROM configitem WHERE change_time > :since"),
                {"since": since},
            )
        ).scalar_one()
    )
