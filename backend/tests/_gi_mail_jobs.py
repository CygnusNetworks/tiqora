"""Run the GenericInterface mail jobs (kind ``gi_mail``) the way the worker does.

TicketCreate / TicketUpdate only queue their mails (``tiqora_job``); tests that look at the
sent mails drain the queue afterwards: every queued ``gi_mail`` job, in id order, through the
real :class:`~tiqora.jobs.runner.JobRunner` claim / execute / finalize path.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import get_settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.extensions import get_registry
from tiqora.jobs.runner import JobRunner

GI_MAIL_KIND = "gi_mail"


def async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


async def drain_with_factory(factory: async_sessionmaker[AsyncSession]) -> list[dict[str, Any]]:
    """Run the queued ``gi_mail`` jobs one by one; returns ``{id, status, error}`` per job."""
    runner = JobRunner(session_factory=factory, settings=get_settings(), registry=get_registry())
    done: list[dict[str, Any]] = []
    while True:
        async with factory() as session:
            row = (
                await session.execute(
                    select(TiqoraJob.id, TiqoraJob.kind, TiqoraJob.payload, TiqoraJob.created_by)
                    .where(TiqoraJob.status == "queued", TiqoraJob.kind == GI_MAIL_KIND)
                    .order_by(TiqoraJob.id)
                    .limit(1)
                )
            ).first()
        if row is None:
            break
        job_id, kind, payload, created_by = row
        assert await runner._claim(job_id)
        await runner._execute(job_id, kind, payload, created_by)
        async with factory() as session:
            status, error = (
                await session.execute(
                    select(TiqoraJob.status, TiqoraJob.error).where(TiqoraJob.id == job_id)
                )
            ).one()
        done.append({"id": job_id, "status": status, "error": error})
    return done


async def drain_gi_mail_jobs(sync_url: str) -> list[dict[str, Any]]:
    engine = create_async_engine(async_url(sync_url))
    try:
        return await drain_with_factory(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def gi_mail_jobs(sync_url: str) -> list[dict[str, Any]]:
    """Every ``gi_mail`` job row (id order) with its decoded payload."""
    import json

    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, status, payload, created_by FROM tiqora_job"
                    " WHERE kind = :k ORDER BY id"
                ),
                {"k": GI_MAIL_KIND},
            ).all()
    finally:
        engine.dispose()
    return [
        {"id": int(r[0]), "status": str(r[1]), "payload": json.loads(r[2]), "created_by": r[3]}
        for r in rows
    ]
