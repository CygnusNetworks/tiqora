"""Run the GenericInterface mail jobs (kind ``gi_mail``) the way the worker does.

TicketCreate / TicketUpdate only queue their mails (``tiqora_job``); tests that look at the
sent mails drain the queue afterwards: every queued ``gi_mail`` job that is due, in id order,
through the real :class:`~tiqora.jobs.runner.JobRunner` claim / execute / finalize path. A job
waiting for a retry (``due_at`` ahead) stays queued; :func:`make_due` brings it forward.

Cleanups are scoped to a module's own jobs: :func:`delete_gi_mail_jobs` takes the module's
ticket ids and/or an id watermark (:func:`max_job_id`, taken before the module wrote).
"""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any

from sqlalchemy import Connection, bindparam, create_engine, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import get_settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.extensions import get_registry
from tiqora.jobs.runner import JobRunner
from tiqora.jobs.service import utc_now

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
                    .where(
                        TiqoraJob.status == "queued",
                        TiqoraJob.kind == GI_MAIL_KIND,
                        or_(TiqoraJob.due_at.is_(None), TiqoraJob.due_at <= utc_now()),
                    )
                    .order_by(TiqoraJob.id)
                    .limit(1)
                )
            ).first()
        if row is None:
            break
        job_id, kind, payload, created_by = row
        attempt = await runner._claim(job_id)
        assert attempt
        await runner._execute(job_id, kind, payload, created_by, attempt=attempt)
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


def gi_mail_jobs(sync_url: str, *, above_id: int = 0) -> list[dict[str, Any]]:
    """Every ``gi_mail`` job row above ``above_id`` (id order) with its decoded payload."""
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, status, payload, created_by, attempts, due_at, checkpoint, error"
                    " FROM tiqora_job WHERE kind = :k AND id > :m ORDER BY id"
                ),
                {"k": GI_MAIL_KIND, "m": above_id},
            ).all()
    finally:
        engine.dispose()
    return [
        {
            "id": int(r[0]),
            "status": str(r[1]),
            "payload": json.loads(r[2]),
            "created_by": r[3],
            "attempts": int(r[4]),
            "due_at": r[5],
            "checkpoint": r[6],
            "error": r[7],
        }
        for r in rows
    ]


def make_due(sync_url: str, *, above_id: int = 0) -> None:
    """Let every queued ``gi_mail`` job above ``above_id`` be claimed now (skip the backoff)."""
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                update(TiqoraJob)
                .where(
                    TiqoraJob.kind == GI_MAIL_KIND,
                    TiqoraJob.status == "queued",
                    TiqoraJob.id > above_id,
                )
                .values(due_at=None)
            )
    finally:
        engine.dispose()


def max_job_id(sync_url: str) -> int:
    """The highest ``tiqora_job`` id now (0 when the table does not exist yet)."""
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            return int(conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM tiqora_job")).scalar())
    except Exception:  # noqa: BLE001 — table not created yet
        return 0
    finally:
        engine.dispose()


def delete_gi_mail_jobs(
    conn: Connection, *, ticket_ids: Collection[int] = (), above_id: int | None = None
) -> None:
    """Delete this module's ``gi_mail`` jobs: those for ``ticket_ids`` (payload) and, with
    ``above_id``, every ``gi_mail`` job created after that watermark."""
    tickets = {int(t) for t in ticket_ids}
    ids: list[int] = []
    for job_id, payload in conn.execute(
        text("SELECT id, payload FROM tiqora_job WHERE kind = :k"), {"k": GI_MAIL_KIND}
    ).all():
        if above_id is not None and int(job_id) > above_id:
            ids.append(int(job_id))
            continue
        try:
            ticket = int(json.loads(payload).get("ticket_id"))
        except (TypeError, ValueError):
            continue
        if ticket in tickets:
            ids.append(int(job_id))
    if ids:
        conn.execute(
            text("DELETE FROM tiqora_job WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": ids},
        )
