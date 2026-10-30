"""The read-only archive of Znuny's changes in Tiqora (design I16), for Mode B.

:func:`archive_changes` pages through Znuny's change ids, reads each change with the legacy
reader (the only module with ``change_*`` SQL) and stores it in ``tiqora_itsm_legacy_change``:
the change as JSON (``data``, the :class:`~tiqora.itsm.change.legacy.model.LegacyChange` fields)
plus the columns the list and search need, and its lookup rows in
``tiqora_itsm_legacy_change_ref`` (linked tickets, CIs, services and CAB customer users), rebuilt
with every write. Only ``tiqora_*`` tables are written.

* CI links are stored as ``tiqora:<id>`` when the importer took the CI over (map kind ``ci``),
  else as ``znuny:<id>`` (shown by its id only). The fingerprint is taken of this stored form, so
  a CI imported later turns the refs of an unchanged change into ``tiqora:`` refs on the next run.
* Idempotent: map kind ``legacy_change`` (Znuny change id -> archive row id, fingerprint) and the
  row's own ``fingerprint``; a change whose stored form did not change is ``unchanged``. Rows of
  changes deleted in Znuny stay.
* Times are what the reader gives: Znuny's ``9999-01-01`` "not set" is ``NULL`` in the columns
  and ``null`` in ``data``; raw history values keep Znuny's text as it is.
* Change templates, conditions and notification rules are only counted (``not_archived``); the
  template text is never read (ruling S4).
* A dry run reads and counts like a real run and writes nothing.

Notification history rows hold recipient addresses (ruling S5): nothing here logs ``data`` or
history values. :func:`erase_legacy_change_customers` removes an erased customer from the archive
and leaves a tombstone (``tiqora_itsm_legacy_erased``, a keyed hash only); every write of the
archive redacts against all tombstones (:mod:`tiqora.itsm.change.legacy.redact`), so the person
never re-enters it, also through a change archived for the first time later. The tombstones are
keyed (:func:`~tiqora.itsm.change.legacy.redact.erased_key`); each key that wrote tombstones leaves
a check row (``kind="check"``). While tombstones exist and any check row is not the one of the
current key (``TIQORA_SECRET_KEY`` rotated without ``TIQORA_ITSM_LEGACY_ERASED_KEY``), archive runs
refuse instead of storing erased customers again (:class:`ErasedKeyChanged`). The check runs at the
start and again before each batch: a run under way stops when an erasure under another key commits;
the page rolls back, earlier pages stay.
An erasure never waits for the key: it works under any key, adds that key's check row and so keeps
archive runs refused (mixed keys cannot be matched by any one key). The fingerprint is
taken of the unredacted form; a row is written again when its fingerprint or the set of
tombstones it was redacted for (``data["redacted"]``) differs.

The standalone job (:data:`ITSM_LEGACY_CHANGE_ARCHIVE_JOB`) runs only in Mode B; the import
pipeline archives in every run (step ``changes``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel
from sqlalchemy import delete, func, insert, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.itsm.change.legacy import reader
from tiqora.itsm.change.legacy.model import (
    CabKind,
    LegacyChange,
    LegacyHistoryEntry,
    LegacyLink,
    LegacyWorkOrder,
)
from tiqora.itsm.change.legacy.redact import (
    REDACTED,
    REDACTED_KEY,
    MappingRedactor,
    TombstoneRedactor,
    erased_hash,
    erased_key,
    key_check,
    redact_data,
    redacted_hashes,
)
from tiqora.itsm.change.models import (
    TiqoraItsmLegacyChange,
    TiqoraItsmLegacyChangeRef,
    TiqoraItsmLegacyErased,
)
from tiqora.itsm.ci_provider import CiRef
from tiqora.itsm.importer.mapstore import MapStore, fingerprint
from tiqora.itsm.importer.steps.cis import KIND_CI
from tiqora.itsm.mode import get_itsm_mode
from tiqora.itsm.znuny_adapter.catalog import chunks
from tiqora.itsm.znuny_adapter.reader import like_contains
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import find_active_job, utc_now

logger = logging.getLogger(__name__)

#: import map kind of an archived change (source id = Znuny change id, target = archive row id)
KIND_LEGACY_CHANGE: Final = "legacy_change"
#: format of ``tiqora_itsm_legacy_change.data``
DATA_VERSION: Final = 1
#: map ``run_id`` of the standalone job (it has no import run)
STANDALONE_RUN_ID: Final = 0
PROGRESS_LABEL: Final = "changes"

ARCHIVE_JOB_KIND: Final = "core.itsm_legacy_change_archive"
ARCHIVE_DEDUPE_KEY: Final = "itsm_legacy_change_archive"
#: the importer's lane (one slot): the archive never runs next to an import or a switch
ARCHIVE_LANE: Final = "itsm_import"

#: ``tiqora_itsm_legacy_change_ref.ref_kind``
REF_TICKET: Final = "ticket"
REF_CI: Final = "ci"
REF_SERVICE: Final = "service"
REF_CUSTOMER: Final = "customer_user"

#: tables a GDPR erasure changes here (``rollback_job`` restores them)
GDPR_TABLES: Final = ("tiqora_itsm_legacy_change", "tiqora_itsm_legacy_change_ref")
#: backup table name of a tombstone the erasure created (``rollback_job`` deletes it again)
TOMBSTONE_CREATED: Final = "tiqora_itsm_legacy_erased:created"
#: ``tiqora_itsm_legacy_erased.kind`` of the key check row (:func:`redact.key_check`); it is not
#: a tombstone, has no job and is never backed up, so a rollback keeps it
KEY_CHECK_KIND: Final = "check"
KEY_CHANGED_MESSAGE: Final = (
    "Der Schlüssel für gelöschte Kunden im Change-Archiv hat sich geändert (wurde "
    "TIQORA_SECRET_KEY gewechselt?). Mit dem neuen Schlüssel kämen gelöschte Kunden "
    "ungeschwärzt ins Archiv zurück, deshalb wird nichts mehr geschrieben; das Archiv bleibt "
    "lesbar und geschwärzt. Haben Sie den bisherigen Schlüssel noch und lief seit dem Wechsel "
    "keine Löschung: Setzen Sie TIQORA_ITSM_LEGACY_ERASED_KEY auf den bisherigen "
    "TIQORA_SECRET_KEY und starten Sie den Vorgang erneut. Ist er verloren oder liefen "
    "inzwischen Löschungen: Weitere Archiv-Läufe sind nicht möglich, Löschungen funktionieren "
    "weiter."
)
#: output of an erasure that ran under a changed key (CLI); the job has ``KEY_CHANGED_COUNT``
KEY_CHANGED_WARNING: Final = (
    "WARNING: the tombstone key of the change archive has changed; this erasure is complete, "
    "but archive runs stay refused (see TIQORA_ITSM_LEGACY_ERASED_KEY in docs/deployment.md)"
)
#: key in the counts of an erasure job that ran under a changed key (value 1)
KEY_CHANGED_COUNT: Final = "legacy_archive_key_changed"


class ErasedKeyChanged(Exception):
    """Tombstones exist under another key than the current one (see the module docstring)."""

    def __init__(self) -> None:
        super().__init__(KEY_CHANGED_MESSAGE)
        self.message = KEY_CHANGED_MESSAGE


_TITLE_MAX: Final = 250
_NAME_MAX: Final = 200
_NUMBER_MAX: Final = 100
_REF_KEY_MAX: Final = 200


# --- what is stored -------------------------------------------------------------------------


@dataclass
class ArchiveReport:
    """Counts of one archive run (per change); ``not_archived`` per kind with rows in Znuny."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    #: read as an id, gone when its page was read (deleted in Znuny meanwhile)
    skipped: int = 0
    not_archived: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def change_to_data(change: LegacyChange) -> dict[str, Any]:
    """The JSON form of ``data`` (datetimes as ISO text)."""
    return {"v": DATA_VERSION, **_plain(change)}


def _when(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


def _at(value: Any) -> datetime:
    when = _when(value)
    if when is None:
        raise ValueError("archived change without a required time")
    return when


def _fields(raw: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): tuple(str(v) for v in values) for k, values in raw.items()}


def _opt(value: Any) -> str | None:
    return None if value is None else str(value)


def _cab_kind(kind: object) -> CabKind:
    return "customer_user" if kind == "customer_user" else "user"


def change_from_data(data: Mapping[str, Any]) -> LegacyChange:
    """``data`` back into the change the reader returned."""
    workorders = tuple(
        LegacyWorkOrder(
            number=int(w["number"]),
            title=_opt(w.get("title")),
            work_order_type=_opt(w.get("work_order_type")),
            state=_opt(w.get("state")),
            agent_login=_opt(w.get("agent_login")),
            planned_start=_when(w.get("planned_start")),
            planned_end=_when(w.get("planned_end")),
            actual_start=_when(w.get("actual_start")),
            actual_end=_when(w.get("actual_end")),
            planned_effort=float(w.get("planned_effort") or 0),
            accounted_time=float(w.get("accounted_time") or 0),
            instruction=_opt(w.get("instruction")),
            report=_opt(w.get("report")),
            dynamic_fields=_fields(w.get("dynamic_fields")),
        )
        for w in data.get("workorders") or ()
    )
    history = tuple(
        LegacyHistoryEntry(
            created_at=_at(h["created_at"]),
            user_login=_opt(h.get("user_login")),
            history_type=str(h.get("history_type") or ""),
            field=_opt(h.get("field")),
            old=_opt(h.get("old")),
            new=_opt(h.get("new")),
            workorder_number=int(h.get("workorder_number") or 0),
            old_display=_opt(h.get("old_display")),
            new_display=_opt(h.get("new_display")),
        )
        for h in data.get("history") or ()
    )
    links = tuple(
        LegacyLink(
            object=str(li["object"]),
            key=str(li["key"]),
            link_type=str(li["link_type"]),
            direction="target" if li.get("direction") == "target" else "source",
            workorder_number=int(li.get("workorder_number") or 0),
        )
        for li in data.get("links") or ()
    )
    cab = tuple((_cab_kind(kind), str(login)) for kind, login in data.get("cab") or ())
    return LegacyChange(
        znuny_change_id=int(data["znuny_change_id"]),
        number=str(data["number"]),
        title=_opt(data.get("title")),
        state=_opt(data.get("state")),
        category=_opt(data.get("category")),
        impact=_opt(data.get("impact")),
        priority=_opt(data.get("priority")),
        manager_login=_opt(data.get("manager_login")),
        builder_login=_opt(data.get("builder_login")),
        requested_time=_when(data.get("requested_time")),
        planned_start=_when(data.get("planned_start")),
        planned_end=_when(data.get("planned_end")),
        actual_start=_when(data.get("actual_start")),
        actual_end=_when(data.get("actual_end")),
        created_at=_at(data["created_at"]),
        changed_at=_at(data["changed_at"]),
        description=_opt(data.get("description")),
        justification=_opt(data.get("justification")),
        cab=cab,
        workorders=workorders,
        history=history,
        dynamic_fields=_fields(data.get("dynamic_fields")),
        links=links,
    )


def _ci_key(key: str, targets: Mapping[str, int]) -> str:
    target = targets.get(key)
    if target is not None and target > 0:
        return str(CiRef("tiqora", target))
    return str(CiRef("znuny", int(key))) if key.isdigit() else key


def with_ci_refs(change: LegacyChange, targets: Mapping[str, int]) -> LegacyChange:
    """The change with its CI link keys as ``tiqora:<id>`` (``targets``: Znuny CI id -> Tiqora
    CI id of the import map) or ``znuny:<id>``."""
    links = tuple(
        replace(li, key=_ci_key(li.key, targets)) if li.object == reader.CI_OBJECT else li
        for li in change.links
    )
    return replace(change, links=links)


def change_refs(change: LegacyChange) -> set[tuple[str, str, int]]:
    """``(ref_kind, ref_key, workorder_number)`` lookup rows of a stored change."""
    out: set[tuple[str, str, int]] = set()
    for li in change.links:
        if li.object == reader.TICKET_OBJECT and li.key.isdigit():
            out.add((REF_TICKET, li.key, li.workorder_number))
        elif li.object == reader.CI_OBJECT:
            out.add((REF_CI, li.key[:_REF_KEY_MAX], li.workorder_number))
        elif li.object == reader.SERVICE_OBJECT and li.key.isdigit():
            out.add((REF_SERVICE, li.key, li.workorder_number))
    for kind, login in change.cab:
        if kind == "customer_user" and login != REDACTED:
            out.add((REF_CUSTOMER, login[:_REF_KEY_MAX], 0))
    return out


def _cut(value: str | None, size: int) -> str | None:
    return None if value is None else value[:size]


def _columns(change: LegacyChange) -> dict[str, Any]:
    return {
        "change_number": change.number[:_NUMBER_MAX],
        "title": _cut(change.title, _TITLE_MAX),
        "state": _cut(change.state, _NAME_MAX),
        "category": _cut(change.category, _NAME_MAX),
        "impact": _cut(change.impact, _NAME_MAX),
        "priority": _cut(change.priority, _NAME_MAX),
        "manager_login": _cut(change.manager_login, _NAME_MAX),
        "builder_login": _cut(change.builder_login, _NAME_MAX),
        "requested_time": change.requested_time,
        "planned_start": change.planned_start,
        "planned_end": change.planned_end,
        "actual_start": change.actual_start,
        "actual_end": change.actual_end,
        "znuny_created_at": change.created_at,
        "znuny_changed_at": change.changed_at,
        "workorder_count": len(change.workorders),
    }


def _dump(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _stored_hashes(raw: str) -> set[str]:
    try:
        return redacted_hashes(json.loads(raw))
    except ValueError:
        return set()


async def load_tombstones(session: AsyncSession) -> set[str]:
    """Every erased identifier's hash (distinct; one row per erasure job and identifier)."""
    table = TiqoraItsmLegacyErased
    rows = await session.execute(
        select(table.identifier_hash).where(table.kind != KEY_CHECK_KIND).distinct()
    )
    return {str(r[0]).strip() for r in rows}


async def _has_tombstones(session: AsyncSession) -> bool:
    table = TiqoraItsmLegacyErased
    found = await session.execute(select(table.id).where(table.kind != KEY_CHECK_KIND).limit(1))
    return found.first() is not None


async def _stored_checks(session: AsyncSession) -> set[str]:
    table = TiqoraItsmLegacyErased
    rows = await session.execute(select(table.identifier_hash).where(table.kind == KEY_CHECK_KIND))
    return {str(r[0]).strip() for r in rows}


async def erased_key_changed(session: AsyncSession, key: bytes) -> bool:
    """Tombstones exist and a stored key check is not the one of ``key``, so that some of them
    cannot be matched under it (reads only). Without a check row there is nothing to compare."""
    check = key_check(key)
    return any(c != check for c in await _stored_checks(session)) and await _has_tombstones(session)


async def require_erased_key(session: AsyncSession, key: bytes) -> None:
    """Raise :class:`ErasedKeyChanged` when :func:`erased_key_changed`."""
    if await erased_key_changed(session, key):
        raise ErasedKeyChanged


async def _ensure_key_check(session: AsyncSession, key: bytes) -> None:
    """Before a tombstone is written: make sure the check row of ``key`` exists. Never refuses:
    a legal erasure does not wait for the key. Stale rows go only when no tombstone is left;
    with tombstones they stay, so archive runs stay refused."""
    check = key_check(key)
    checks = await _stored_checks(session)
    table = TiqoraItsmLegacyErased
    if checks - {check} and not await _has_tombstones(session):
        await session.execute(
            delete(table).where(table.kind == KEY_CHECK_KIND, table.identifier_hash != check)
        )
    if check in checks:
        return
    session.add(table(identifier_hash=check, kind=KEY_CHECK_KIND, job_id=None, created=utc_now()))
    await session.flush()


def redact_for_storage(
    change: LegacyChange, key: bytes, tombstones: Collection[str]
) -> tuple[dict[str, Any], set[str]]:
    """``data`` of a change to store, redacted against the tombstones, and the hashes it hit."""
    data = change_to_data(change)
    if not tombstones:
        return data, set()
    redactor = TombstoneRedactor(key, tombstones)
    out = redact_data(data, redactor)
    if redactor.hits:
        out[REDACTED_KEY] = sorted(redactor.hits)
    return out, redactor.hits


# --- the archive run ------------------------------------------------------------------------


async def _replace_refs(
    session: AsyncSession, legacy_change_id: int, refs: set[tuple[str, str, int]]
) -> None:
    await session.execute(
        delete(TiqoraItsmLegacyChangeRef).where(
            TiqoraItsmLegacyChangeRef.legacy_change_id == legacy_change_id
        )
    )
    if refs:
        await session.execute(
            insert(TiqoraItsmLegacyChangeRef),
            [
                {
                    "legacy_change_id": legacy_change_id,
                    "ref_kind": kind,
                    "ref_key": key,
                    "workorder_number": wo,
                }
                for kind, key, wo in sorted(refs)
            ],
        )


async def _archive_batch(
    session: AsyncSession,
    store: MapStore,
    ids: Sequence[int],
    *,
    run_id: int | None,
    dry_run: bool,
    report: ArchiveReport,
    key: bytes,
    tombstones: Callable[[], Awaitable[Collection[str]]],
) -> None:
    """At most :data:`reader.MAX_READ` changes (flush only). ``tombstones`` is called after the
    Znuny read, right before the writes: an erasure committed while the page was read counts."""
    changes = await reader.read_changes(session, ids)
    report.skipped += len(ids) - len(changes)
    erased = await tombstones()
    ci_keys = sorted({li.key for c in changes for li in c.links if li.object == reader.CI_OBJECT})
    targets = {k: e.target_id for k, e in (await store.get_many(KIND_CI, ci_keys)).items()}
    known = await store.get_many(KIND_LEGACY_CHANGE, [str(c.znuny_change_id) for c in changes])
    rows = {
        int(r.znuny_change_id): r
        for r in (
            await session.execute(
                select(TiqoraItsmLegacyChange).where(
                    TiqoraItsmLegacyChange.znuny_change_id.in_([c.znuny_change_id for c in changes])
                )
            )
        ).scalars()
    }
    now = utc_now()
    for change in changes:
        source_id = str(change.znuny_change_id)
        stored = with_ci_refs(change, targets)
        fp = fingerprint(stored)  # of the unredacted form: Znuny unchanged -> unchanged
        data, hits = redact_for_storage(stored, key, erased)
        row = rows.get(change.znuny_change_id)
        if row is not None and row.fingerprint == fp and _stored_hashes(row.data) == hits:
            report.unchanged += 1
            hit = known.get(source_id)
            if hit is None or hit.fingerprint != fp or hit.target_id != row.id:
                await store.put(KIND_LEGACY_CHANGE, source_id, int(row.id), fp)
            continue
        if row is None:
            report.created += 1
        else:
            report.updated += 1
        if dry_run:
            continue
        shown = change_from_data(data) if hits else stored
        values = {
            **_columns(shown),
            "data": _dump(data),
            "fingerprint": fp,
            "imported_at": now,
            "import_run_id": run_id,
        }
        if row is None:
            row = TiqoraItsmLegacyChange(znuny_change_id=change.znuny_change_id, **values)
            session.add(row)
        else:
            for column, value in values.items():
                setattr(row, column, value)
        await session.flush()
        await _replace_refs(session, int(row.id), change_refs(shown))
        await store.put(KIND_LEGACY_CHANGE, source_id, int(row.id), fp)


async def archive_changes(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: int | None,
    dry_run: bool,
    page_size: int,
    progress: Callable[[int, int | None, str], Awaitable[None]],
    check_cancelled: Callable[[], Awaitable[None]],
    report: ArchiveReport,
    settings: Settings,
) -> ArchiveReport:
    """Archive every Znuny change into ``report`` (one session and commit per page of
    ``page_size`` ids; cancellation is checked before each page). Without the change tables
    nothing happens. ``settings`` give the tombstone key (:func:`redact.erased_key`); under a
    changed key the run refuses before it writes anything (:class:`ErasedKeyChanged`), and a key
    that changes during the run stops it before the next batch: that page rolls back, the pages
    already committed are valid."""
    key = erased_key(settings)
    async with session_factory() as session:
        if not await reader.change_tables_present(session):
            return report
        await require_erased_key(session, key)
        total = await reader.count_changes(session)
        report.not_archived = {
            kind: n for kind, n in (await reader.not_archived_counts(session)).items() if n > 0
        }

    async def fresh_tombstones() -> set[str]:
        # a session of its own: a fresh snapshot on both dialects (the page's transaction keeps
        # its first one on MariaDB, REPEATABLE READ)
        async with session_factory() as fresh:
            # an erasure under another key since the start: its hashes cannot match under this
            # key, and its check row would refuse every run that could repair the page
            await require_erased_key(fresh, key)
            return await load_tombstones(fresh)

    after = 0
    done = 0
    await progress(0, total, PROGRESS_LABEL)
    while True:
        await check_cancelled()
        async with session_factory() as session:
            ids = await reader.iter_change_ids(session, after_id=after, limit=max(1, page_size))
            if not ids:
                break
            store = MapStore(
                session,
                run_id=STANDALONE_RUN_ID if run_id is None else run_id,
                read_only=dry_run,
            )
            for part in chunks(ids, reader.MAX_READ):
                await _archive_batch(
                    session,
                    store,
                    part,
                    run_id=run_id,
                    dry_run=dry_run,
                    report=report,
                    key=key,
                    tombstones=fresh_tombstones,
                )
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
        after = ids[-1]
        done += len(ids)
        await progress(done, max(total, done), PROGRESS_LABEL)
    logger.info(
        "itsm_legacy_change_archive created=%s updated=%s unchanged=%s skipped=%s dry_run=%s",
        report.created,
        report.updated,
        report.unchanged,
        report.skipped,
        dry_run,
    )
    return report


# --- the standalone job (Mode B) ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArchiveBlocked:
    """Why the standalone archive cannot run: ``code`` for the API, ``message`` for people."""

    code: str
    message: str


MODE_B_ONLY: Final = ArchiveBlocked(
    "legacy_archive_mode_b_only",
    "Das Change-Archiv wird nur im ITSM-Modus Tiqora eigenständig übernommen; vorher übernimmt "
    "der ITSM-Import die Changes.",
)
NO_CHANGE_TABLES: Final = ArchiveBlocked(
    "legacy_archive_no_tables",
    "In der Znuny-Datenbank ist kein Change-Management (ITSMChangeManagement) installiert.",
)


async def archive_blocker(session: AsyncSession) -> ArchiveBlocked | None:
    """Why the standalone archive cannot run now, or ``None``."""
    if (await get_itsm_mode(session)).mode != "tiqora":
        return MODE_B_ONLY
    if not await reader.change_tables_present(session):
        return NO_CHANGE_TABLES
    return None


class LegacyArchivePayload(BaseModel):
    user_id: int
    dry_run: bool = False
    page_size: int = 500


async def run_legacy_archive_job(ctx: JobContext, payload: Any) -> dict[str, Any]:
    async with ctx.session_factory() as session:
        blocked = await archive_blocker(session)
    if blocked is not None:
        raise JobFailed(blocked.message)
    try:
        report = await archive_changes(
            ctx.session_factory,
            run_id=None,
            dry_run=payload.dry_run,
            page_size=payload.page_size,
            progress=ctx.progress,
            check_cancelled=ctx.check_cancelled,
            report=ArchiveReport(),
            settings=ctx.settings,
        )
    except ErasedKeyChanged as exc:
        raise JobFailed(exc.message) from exc
    return {"dry_run": payload.dry_run, **report.as_dict()}


def archive_dedupe_key(dry_run: bool) -> str:
    """A dry run and a real run are separate jobs (one active of each)."""
    return f"{ARCHIVE_DEDUPE_KEY}:dry_run" if dry_run else ARCHIVE_DEDUPE_KEY


ITSM_LEGACY_CHANGE_ARCHIVE_JOB = JobHandler(
    kind=ARCHIVE_JOB_KIND,
    payload_model=LegacyArchivePayload,
    run=run_legacy_archive_job,
    title_key="jobs.kinds.core.itsm_legacy_change_archive",
    timeout_seconds=7200,
    lane=ARCHIVE_LANE,
    max_attempts=2,
    requeue_if_interrupted=True,
)


@dataclass(frozen=True, slots=True)
class ArchiveStatus:
    #: ``None`` when the standalone archive can run now
    blocked: ArchiveBlocked | None
    archived: int
    last_archived_at: datetime | None
    active_job_id: int | None


async def archive_status(session: AsyncSession) -> ArchiveStatus:
    count, last = (
        await session.execute(
            select(func.count(), func.max(TiqoraItsmLegacyChange.imported_at)).select_from(
                TiqoraItsmLegacyChange
            )
        )
    ).one()
    job = await find_active_job(
        session, kind=ARCHIVE_JOB_KIND, dedupe_key=archive_dedupe_key(False)
    ) or await find_active_job(session, kind=ARCHIVE_JOB_KIND, dedupe_key=archive_dedupe_key(True))
    return ArchiveStatus(
        blocked=await archive_blocker(session),
        archived=int(count or 0),
        last_archived_at=last,
        active_job_id=None if job is None else int(job.id),
    )


# --- GDPR -----------------------------------------------------------------------------------

#: ``add_backup(table, pk, original_row)``
BackupFn = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[None]]
_LIKE_CHUNK: Final = 20


def _needle(value: str) -> str:
    """How ``value`` appears inside the stored JSON text."""
    return json.dumps(value, ensure_ascii=False)[1:-1]


async def _candidate_ids(
    session: AsyncSession, logins: Sequence[str], emails: Sequence[str]
) -> set[int]:
    ids: set[int] = set()
    ref = TiqoraItsmLegacyChangeRef
    for part in chunks(sorted(set(logins))):
        rows = await session.execute(
            select(ref.legacy_change_id).where(
                ref.ref_kind == REF_CUSTOMER, ref.ref_key.in_(list(part))
            )
        )
        ids.update(int(r[0]) for r in rows)
    needles = sorted({_needle(v).lower() for v in (*logins, *emails) if v})
    table = TiqoraItsmLegacyChange
    for part in chunks(needles, _LIKE_CHUNK):
        conditions = [
            or_(
                func.lower(table.data).like(like_contains(n), escape="!"),
                func.lower(table.title).like(like_contains(n), escape="!"),
            )
            for n in part
        ]
        rows = await session.execute(select(table.id).where(or_(*conditions)))
        ids.update(int(r[0]) for r in rows)
    return ids


async def _add_tombstones(
    session: AsyncSession,
    key: bytes,
    identifiers: Sequence[tuple[str, str]],
    add_backup: BackupFn,
    job_id: int | None,
) -> int:
    """One tombstone of this job per ``(kind, identifier)``; each new one is backed up as
    created, so a rollback of the job deletes its own rows only and an identifier another
    erasure also holds stays erased. Rows without a job (the CLI anonymisation) are matched
    with ``job_id IS NULL`` before the insert: the unique key treats NULLs as distinct."""
    wanted: dict[str, str] = {}
    for kind, identifier in identifiers:
        if identifier and identifier.strip():
            wanted.setdefault(erased_hash(key, identifier), kind)
    if not wanted:
        return 0
    await _ensure_key_check(session, key)
    table = TiqoraItsmLegacyErased
    mine = table.job_id.is_(None) if job_id is None else table.job_id == job_id
    known: set[str] = set()
    for part in chunks(sorted(wanted)):
        rows = await session.execute(
            select(table.identifier_hash).where(table.identifier_hash.in_(list(part)), mine)
        )
        known.update(str(r[0]).strip() for r in rows)
    created = 0
    now = utc_now()
    for digest, kind in sorted(wanted.items()):
        if digest in known:
            continue
        row = TiqoraItsmLegacyErased(identifier_hash=digest, kind=kind, job_id=job_id, created=now)
        session.add(row)
        await session.flush()
        await add_backup(TOMBSTONE_CREATED, {"id": int(row.id)}, {})
        created += 1
    return created


async def erase_legacy_change_customers(
    session: AsyncSession,
    *,
    replacements: Mapping[str, str],
    emails: Collection[str],
    add_backup: BackupFn,
    settings: Settings,
    job_id: int | None,
) -> dict[str, int]:
    """Remove erased customer users from the archive for good (flush only; no commit).

    ``replacements`` maps each erased login to the token its tickets got; ``emails`` are the
    customers' addresses; ``job_id`` is the erasure job (``None`` for the CLI anonymisation,
    which has no rollback). A tombstone of the job is left per login and address (the archive
    redacts against all of them on every later write). In the archived rows the person-bearing
    values (:func:`~tiqora.itsm.change.legacy.redact.redact_data`) are pseudonymised: each login
    becomes its token, each address :data:`REDACTED`; their ``customer_user`` lookup rows are
    deleted. Every changed, deleted or created row is backed up first, so ``rollback_job``
    restores the rows (incl. the fingerprint) and deletes the job's own tombstones.

    The redaction of the stored rows goes by the identifiers themselves, so it holds under any
    key; only the hashes it leaves behind depend on it (see :func:`erased_key_changed`).
    """
    logins = [lg for lg in replacements if lg and lg.strip()]
    addresses = [e for e in emails if e and e.strip()]
    counts = {
        "tiqora_itsm_legacy_change": 0,
        "tiqora_itsm_legacy_change_ref": 0,
        "tiqora_itsm_legacy_erased": 0,
    }
    if not logins and not addresses:
        return counts
    key = erased_key(settings)
    counts["tiqora_itsm_legacy_erased"] = await _add_tombstones(
        session,
        key,
        [*(("login", lg) for lg in logins), *(("email", e) for e in addresses)],
        add_backup,
        job_id,
    )
    table = TiqoraItsmLegacyChange
    for part in chunks(sorted(await _candidate_ids(session, logins, addresses))):
        rows = (
            await session.execute(
                select(table.id, table.data, table.title, table.fingerprint).where(
                    table.id.in_(list(part))
                )
            )
        ).all()
        for row_id, raw, title, fp in rows:
            redactor = MappingRedactor(key, replacements, addresses)
            old = json.loads(raw)
            data = redact_data(old, redactor)
            if not redactor.hits:
                continue
            data[REDACTED_KEY] = sorted(redacted_hashes(old) | redactor.hits)
            new_title = data.get("title")
            new_title = None if new_title is None else str(new_title)[:_TITLE_MAX]
            await add_backup(
                GDPR_TABLES[0],
                {"id": int(row_id)},
                {"data": raw, "title": title, "fingerprint": fp},
            )
            await session.execute(
                text("UPDATE tiqora_itsm_legacy_change SET data = :d, title = :t WHERE id = :i"),
                {"d": _dump(data), "t": new_title, "i": int(row_id)},
            )
            counts["tiqora_itsm_legacy_change"] += 1
    ref = TiqoraItsmLegacyChangeRef
    for keys in chunks(sorted(set(logins))):
        found = (
            await session.execute(
                select(ref.legacy_change_id, ref.ref_kind, ref.ref_key, ref.workorder_number).where(
                    ref.ref_kind == REF_CUSTOMER, ref.ref_key.in_(list(keys))
                )
            )
        ).all()
        for change_id, kind, key, wo in found:
            pk = {
                "legacy_change_id": int(change_id),
                "ref_kind": str(kind),
                "ref_key": str(key),
                "workorder_number": int(wo),
            }
            await add_backup(GDPR_TABLES[1], pk, dict(pk))
            await session.execute(
                delete(ref).where(
                    ref.legacy_change_id == pk["legacy_change_id"],
                    ref.ref_kind == pk["ref_kind"],
                    ref.ref_key == pk["ref_key"],
                    ref.workorder_number == pk["workorder_number"],
                )
            )
            counts["tiqora_itsm_legacy_change_ref"] += 1
    await session.flush()
    return counts


#: ``add_backup_for(job_id, table, pk, original_row)``: a backup row for that erasure job
JobBackupFn = Callable[[int, str, dict[str, Any], dict[str, Any]], Awaitable[None]]


async def _tombstone_holders(session: AsyncSession) -> dict[str, set[int]]:
    """Each erased hash -> the erasure jobs holding it (empty: only the CLI anonymisation)."""
    holders: dict[str, set[int]] = {}
    table = TiqoraItsmLegacyErased
    rows = await session.execute(
        select(table.identifier_hash, table.job_id).where(table.kind != KEY_CHECK_KIND)
    )
    for digest, job in rows:
        jobs = holders.setdefault(str(digest).strip(), set())
        if job is not None:
            jobs.add(int(job))
    return holders


async def redact_restored_changes(
    session: AsyncSession,
    change_ids: Collection[int],
    *,
    settings: Settings,
    add_backup_for: JobBackupFn,
) -> int:
    """Redact archive rows a rollback restored against the tombstones that remain (flush only).

    A rollback restores the rows as they were before its job, which may name a customer
    another erasure (or the CLI anonymisation) still holds: those values become
    :data:`REDACTED` and their ``customer_user`` lookup rows go again, in the rollback's
    transaction. Each row changed here is backed up for every erasure job holding one of its
    hits, so rolling that job back later restores the row as this rollback left it.
    """
    if not change_ids:
        return 0
    holders = await _tombstone_holders(session)
    if not holders:
        return 0
    key = erased_key(settings)
    # under a changed key the remaining tombstones cannot all match: the restored rows could
    # name another erased customer, so this rollback refuses
    await require_erased_key(session, key)
    changed = 0
    table = TiqoraItsmLegacyChange
    ref = TiqoraItsmLegacyChangeRef
    for part in chunks(sorted(set(change_ids))):
        rows = (
            await session.execute(
                select(table.id, table.data, table.title, table.fingerprint).where(
                    table.id.in_(list(part))
                )
            )
        ).all()
        for row_id, raw, title, fp in rows:
            redactor = TombstoneRedactor(key, holders)
            old = json.loads(raw)
            data = redact_data(old, redactor)
            if not redactor.hits:
                continue
            data[REDACTED_KEY] = sorted(redacted_hashes(old) | redactor.hits)
            new_title = data.get("title")
            new_title = None if new_title is None else str(new_title)[:_TITLE_MAX]
            original = {"data": raw, "title": title, "fingerprint": fp}
            for job in sorted(set().union(*(holders[h] for h in redactor.hits))):
                await add_backup_for(job, GDPR_TABLES[0], {"id": int(row_id)}, original)
            await session.execute(
                text("UPDATE tiqora_itsm_legacy_change SET data = :d, title = :t WHERE id = :i"),
                {"d": _dump(data), "t": new_title, "i": int(row_id)},
            )
            changed += 1
        found = (
            await session.execute(
                select(ref.legacy_change_id, ref.ref_kind, ref.ref_key, ref.workorder_number).where(
                    ref.legacy_change_id.in_(list(part)), ref.ref_kind == REF_CUSTOMER
                )
            )
        ).all()
        for change_id, kind, login, wo in found:
            jobs = holders.get(erased_hash(key, str(login)))
            if jobs is None:
                continue
            pk = {
                "legacy_change_id": int(change_id),
                "ref_kind": str(kind),
                "ref_key": str(login),
                "workorder_number": int(wo),
            }
            for job in sorted(jobs):
                await add_backup_for(job, GDPR_TABLES[1], pk, dict(pk))
            await session.execute(
                delete(ref).where(
                    ref.legacy_change_id == pk["legacy_change_id"],
                    ref.ref_kind == pk["ref_kind"],
                    ref.ref_key == pk["ref_key"],
                    ref.workorder_number == pk["workorder_number"],
                )
            )
    await session.flush()
    return changed
