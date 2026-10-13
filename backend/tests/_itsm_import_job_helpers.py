"""Shared setup of the importer job and CLI tests (band 56300-56329 for users and runs)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._itsm_import_fixtures import cleanup_cmdb_rows
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.itsm.importer.jobs import (
    IMPORT_DEDUPE_KEY,
    IMPORT_JOB_KIND,
    ItsmImportPayload,
    start_run,
)
from tiqora.jobs.context import JobContext
from tiqora.jobs.service import enqueue_job

USER_LO, USER_HI = 56300, 56329
#: the last id of the shared importer band; map rows of this module are moved there so that
#: ``cleanup_cmdb_rows`` finds (and deletes) what the runs created
MAP_BAND_END = 56299


def wipe(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            run_ids = [
                int(r[0])
                for r in conn.execute(
                    text(
                        "SELECT id FROM tiqora_itsm_import_run"
                        " WHERE started_by BETWEEN :a AND :b OR started_by = 1"
                    ),
                    {"a": USER_LO, "b": USER_HI},
                ).all()
            ]
            for run_id in run_ids:
                conn.execute(
                    text("UPDATE tiqora_itsm_import_map SET run_id = :e WHERE run_id = :r"),
                    {"e": MAP_BAND_END, "r": run_id},
                )
            conn.execute(
                text(
                    "UPDATE tiqora_cmdb_class SET current_definition_id = NULL"
                    " WHERE id IN (SELECT target_id FROM tiqora_itsm_import_map"
                    " WHERE source_kind = 'class' AND run_id BETWEEN 56070 AND 56299)"
                )
            )
        cleanup_cmdb_rows(sync_url)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tiqora_cmdb_catalog"))
            conn.execute(text("DELETE FROM tiqora_itsm_import_map WHERE run_id >= 56070"))
            conn.execute(text("DELETE FROM tiqora_itsm_link_type"))
            conn.execute(
                text(
                    "DELETE FROM tiqora_itsm_import_run"
                    " WHERE started_by BETWEEN :a AND :b OR started_by = 1"
                ),
                {"a": USER_LO, "b": USER_HI},
            )
            conn.execute(
                text(
                    "DELETE FROM tiqora_job_file WHERE job_id IN"
                    " (SELECT id FROM tiqora_job WHERE created_by BETWEEN :a AND :b"
                    " OR kind IN ('core.itsm_import', 'core.itsm_switch'))"
                ),
                {"a": USER_LO, "b": USER_HI},
            )
            conn.execute(
                text(
                    "DELETE FROM tiqora_job WHERE created_by BETWEEN :a AND :b"
                    " OR kind IN ('core.itsm_import', 'core.itsm_switch')"
                ),
                {"a": USER_LO, "b": USER_HI},
            )
            conn.execute(
                text(
                    "DELETE FROM tiqora_settings WHERE `key` IN"
                    " ('itsm.switch', 'itsm.znuny_readonly')"
                    if conn.dialect.name == "mysql"
                    else "DELETE FROM tiqora_settings WHERE key IN"
                    " ('itsm.switch', 'itsm.znuny_readonly')"
                )
            )
    finally:
        engine.dispose()


async def enqueue_import(
    factory: async_sessionmaker[AsyncSession],
    *,
    dry_run: bool,
    user_id: int,
    page_size: int = 3,
) -> tuple[int, int]:
    """Create the run row and the job the way the API does; returns ``(run_id, job_id)``."""
    async with factory() as session:
        run = await start_run(session, kind="dry_run" if dry_run else "import", user_id=user_id)
        job = await enqueue_job(
            session,
            kind=IMPORT_JOB_KIND,
            payload=ItsmImportPayload(
                user_id=user_id, dry_run=dry_run, run_id=run.id, page_size=page_size
            ),
            created_by=user_id,
            dedupe_key=IMPORT_DEDUPE_KEY,
        )
        run.job_id = job.id
        run_id, job_id = run.id, job.id
        await session.commit()
    return run_id, job_id


def make_ctx(
    factory: async_sessionmaker[AsyncSession], job_id: int, kind: str, user_id: int
) -> JobContext:
    return JobContext(
        job_id=job_id,
        kind=kind,
        created_by=user_id,
        settings=get_settings(),
        session_factory=factory,
    )


async def call_handler(
    factory: async_sessionmaker[AsyncSession],
    job_id: int,
    handler: Callable[[JobContext, Any], Awaitable[Any]],
    payload: Any,
    kind: str,
) -> Any:
    """Run the handler function directly (a claim that is not driven by the runner)."""
    return await handler(make_ctx(factory, job_id, kind, payload.user_id), payload)
