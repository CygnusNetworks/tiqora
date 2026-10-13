"""CI numbers (D5): ``<class prefix><6-digit counter>``, unique across all classes."""

from __future__ import annotations

from typing import Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.errors import CmdbError, CmdbNotFound
from tiqora.db.tiqora.models import TiqoraCmdbCi, TiqoraCmdbClass

#: how many candidate numbers are tried before giving up (imported numbers can be dense)
MAX_STEPS: Final = 1000


class NumberExhausted(CmdbError):
    """Every number in the next ``MAX_STEPS`` steps is taken."""


async def next_ci_number(session: AsyncSession, class_id: int) -> str:
    """Reserve and return the next free number of the class.

    The class row is locked (``FOR UPDATE``) for the read-modify-write of the counter; numbers
    that already exist (imported ones) are skipped. The caller commits.
    """
    row = (
        await session.execute(
            select(TiqoraCmdbClass.number_prefix, TiqoraCmdbClass.number_counter)
            .where(TiqoraCmdbClass.id == class_id)
            .with_for_update()
        )
    ).first()
    if row is None:
        raise CmdbNotFound(f"class {class_id}")
    prefix, counter = str(row[0]), int(row[1])
    candidates = {counter + i: f"{prefix}{counter + i:06d}" for i in range(1, MAX_STEPS + 1)}
    taken = set(
        (
            await session.execute(
                select(TiqoraCmdbCi.number).where(TiqoraCmdbCi.number.in_(candidates.values()))
            )
        )
        .scalars()
        .all()
    )
    for n, number in candidates.items():
        if number not in taken:
            await session.execute(
                update(TiqoraCmdbClass)
                .where(TiqoraCmdbClass.id == class_id)
                .values(number_counter=n)
                .execution_options(synchronize_session=False)
            )
            return number
    raise NumberExhausted(f"class {class_id}")


async def bump_counter_for_number(session: AsyncSession, class_id: int, number: str) -> None:
    """Raise the class counter to the numeric suffix of an imported ``number`` (same prefix)."""
    prefix = (
        await session.execute(
            select(TiqoraCmdbClass.number_prefix).where(TiqoraCmdbClass.id == class_id)
        )
    ).scalar_one_or_none()
    if prefix is None or not number.startswith(str(prefix)):
        return
    suffix = number[len(str(prefix)) :]
    if not suffix.isascii() or not suffix.isdigit() or len(suffix) > 15:
        return
    await session.execute(
        update(TiqoraCmdbClass)
        .where(TiqoraCmdbClass.id == class_id, TiqoraCmdbClass.number_counter < int(suffix))
        .values(number_counter=int(suffix))
        .execution_options(synchronize_session=False)
    )


__all__ = ["MAX_STEPS", "NumberExhausted", "bump_counter_for_number", "next_ci_number"]
