"""Bulk edit of up to 5000 CIs as a background job (D9): the same states and/or the same link.

Per CI, in order: a CI that no longer exists or whose class the user cannot write is *skipped*
(reported, never an error of the job); otherwise the states are set through
:meth:`tiqora.cmdb.service.CiService.update` with ``expected_lock_version=None`` (a bulk edit has no
stale-conflict) and the link is added through :class:`tiqora.cmdb.links.LinkService` with the
permission rules of ``POST /links`` (``rw`` on a ticket end, ``ro`` on the other). Each CI runs
in its own savepoint, so a CI that fails (invalid link, concurrent delete) is reported and does
not undo the others; blocks of 100 CIs are committed together. Incident states are recomputed
once at the end for all updated CIs (``recompute_from`` may defer to the propagation job).

The result is ``{"updated", "skipped"}``; a CI that failed counts as skipped and has the reason in
``report.csv`` (``id;number;status;message``, status ``updated|skipped|error``).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.access import load_access, writable_class_ids
from tiqora.cmdb.errors import CmdbError
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.propagation import recompute_from
from tiqora.cmdb.schemas import BulkLinkIn
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.cmdb.service import CiService, InvalidValues
from tiqora.cmdb.settings import load_cmdb_settings
from tiqora.db.tiqora.models import TiqoraCmdbCatalog, TiqoraCmdbCi
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.registry import JobHandler

REPORT_NAME: Final = "report.csv"
COMMIT_EVERY: Final = 100
MAX_CIS: Final = 5000
DEFAULT_NOTE: Final = "Sammelbearbeitung"
_CHUNK: Final = 500
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")


class CmdbBulkPayload(BaseModel):
    user_id: int
    ci_ids: list[int] = Field(min_length=1, max_length=MAX_CIS)
    deployment_state_id: int | None = None
    incident_state_id: int | None = None
    link: BulkLinkIn | None = None
    change_note: str | None = None


@dataclass(slots=True)
class _Outcome:
    ci_id: int
    number: str
    status: str
    message: str = ""


async def invalid_state_ids(
    session: AsyncSession, deployment_state_id: int | None, incident_state_id: int | None
) -> str | None:
    """German message when a given state is unknown, of the wrong list or no longer valid."""
    wanted = {
        deployment_state_id: DEPLOYMENT_STATE,
        incident_state_id: INCIDENT_STATE,
    }
    wanted.pop(None, None)
    if not wanted:
        return None
    rows = (
        await session.execute(
            select(
                TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.catalog_class, TiqoraCmdbCatalog.valid
            ).where(TiqoraCmdbCatalog.id.in_(sorted(i for i in wanted if i is not None)))
        )
    ).all()
    found = {int(i): (str(c), bool(v)) for i, c, v in rows}
    for item_id, catalog_class in wanted.items():
        hit = found.get(int(item_id)) if item_id is not None else None
        if hit is None or hit[0] != catalog_class or not hit[1]:
            return "Ungültiger Status."
    return None


def _guard(text: str) -> str:
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _report(outcomes: Sequence[_Outcome]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(["id", "number", "status", "message"])
    for o in outcomes:
        writer.writerow([o.ci_id, _guard(o.number), o.status, _guard(o.message)])
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def _error_text(exc: Exception) -> str:
    if isinstance(exc, InvalidValues):
        return "; ".join(f"{i.path}: {i.message}" for i in exc.issues)
    return str(exc) or "Nicht gespeichert"


async def run_cmdb_bulk(ctx: JobContext, payload: CmdbBulkPayload) -> dict[str, Any]:
    ids = list(dict.fromkeys(payload.ci_ids))
    note = (payload.change_note or "").strip() or DEFAULT_NOTE
    states_given = payload.deployment_state_id is not None or payload.incident_state_id is not None
    outcomes: list[_Outcome] = []
    updated: list[int] = []
    async with ctx.session_factory() as session:
        access = await load_access(session, payload.user_id)
        problem = await invalid_state_ids(
            session, payload.deployment_state_id, payload.incident_state_id
        )
        if problem:
            raise JobFailed(problem)
        writable = set(await writable_class_ids(session, access))
        rows: dict[int, tuple[str, int]] = {}
        for i in range(0, len(ids), _CHUNK):
            part = ids[i : i + _CHUNK]
            for ci_id, number, class_id in (
                await session.execute(
                    select(TiqoraCmdbCi.id, TiqoraCmdbCi.number, TiqoraCmdbCi.class_id).where(
                        TiqoraCmdbCi.id.in_(part)
                    )
                )
            ).all():
                rows[int(ci_id)] = (str(number), int(class_id))

        svc = CiService(session)
        links = LinkService(session)
        await ctx.progress(0, len(ids), "CIs werden bearbeitet …")
        for n, ci_id in enumerate(ids, start=1):
            found = rows.get(ci_id)
            if found is None:
                outcomes.append(_Outcome(ci_id, "", "skipped", "CI nicht gefunden"))
            elif found[1] not in writable:
                outcomes.append(
                    _Outcome(ci_id, found[0], "skipped", "Keine Schreibberechtigung für die Klasse")
                )
            else:
                try:
                    async with session.begin_nested():
                        if states_given:
                            await svc.update(
                                access,
                                ci_id,
                                expected_lock_version=None,
                                deployment_state_id=payload.deployment_state_id,
                                incident_state_id=payload.incident_state_id,
                                change_note=note,
                                recompute=False,
                                enforce_permissions=False,
                            )
                        if payload.link is not None:
                            ci_end = LinkEnd("ci", str(ci_id))
                            other = LinkEnd(payload.link.target_kind, payload.link.target_key)
                            source, target = (
                                (ci_end, other)
                                if payload.link.ci_side == "source"
                                else (other, ci_end)
                            )
                            await links.add(
                                access,
                                source=source,
                                target=target,
                                link_type_key=payload.link.link_type_key,
                            )
                except CmdbError as exc:
                    outcomes.append(_Outcome(ci_id, found[0], "error", _error_text(exc)))
                else:
                    outcomes.append(_Outcome(ci_id, found[0], "updated"))
                    updated.append(ci_id)
            if n % COMMIT_EVERY == 0:
                await session.commit()
                await ctx.progress(n, len(ids), "CIs werden bearbeitet …")
        await session.commit()

    if states_given and updated:
        async with ctx.session_factory() as session:
            await recompute_from(session, updated, settings=await load_cmdb_settings(session))
            await session.commit()
    await ctx.add_file(REPORT_NAME, "text/csv; charset=utf-8", _report(outcomes))
    return {"updated": len(updated), "skipped": len(outcomes) - len(updated)}


CMDB_BULK_JOB = JobHandler(
    kind="core.cmdb_bulk",
    payload_model=CmdbBulkPayload,
    run=run_cmdb_bulk,
    title_key="jobs.kinds.core.cmdb_bulk",
    timeout_seconds=3600,
    result_private=True,
)

__all__ = [
    "CMDB_BULK_JOB",
    "CmdbBulkPayload",
    "invalid_state_ids",
    "run_cmdb_bulk",
]
