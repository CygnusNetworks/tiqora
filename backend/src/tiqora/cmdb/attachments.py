"""CI attachments (D5): staged uploads, binding to a CI, download access and deletion.

A file uploaded without a CI is *staged* (``ci_id`` NULL) and visible to its uploader only; it is
bound by ``CiService.create``/``update`` once an ``attachment`` attribute (or ``attachment_ids``)
references it. Stale staged files are removed by :func:`purge_stale_staged` (Task 16 daily service).
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.access import CmdbAccess, require_class_access
from tiqora.cmdb.definition import AttributeDef, iter_attribute_paths
from tiqora.cmdb.errors import CmdbError, CmdbNotFound
from tiqora.cmdb.values import ValueIssue, collect_reference_paths
from tiqora.db.tiqora.models import TiqoraCmdbAttachment, TiqoraCmdbCi, TiqoraCmdbCiHistory
from tiqora.jobs.service import utc_now

FILENAME_MAX: Final = 255
DEFAULT_FILENAME: Final = "datei"
DEFAULT_CONTENT_TYPE: Final = "application/octet-stream"
_CONTENT_TYPE_RE: Final = re.compile(r"^[A-Za-z0-9][\w!#$&^.+-]*/[A-Za-z0-9][\w!#$&^.+-]*$")
_CHUNK: Final = 500
#: clients (httpx, some browsers) percent-encode control characters and separators in the
#: multipart filename; decode just those so they are stripped like their literal forms
_PCT_DANGEROUS: Final = re.compile(r"%(?:[01][0-9a-fA-F]|7[fF]|2[fF]|5[cC])")
EVENT_ADD: Final = "AttachmentAddPost"
EVENT_DELETE: Final = "AttachmentDeletePost"


class AttachmentInUse(CmdbError):
    """The current version of the CI still references the file in an ``attachment`` attribute."""


@dataclass(frozen=True, slots=True)
class AttachmentRecord:
    id: int
    ci_id: int | None
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_by: int
    created_at: datetime


def sanitize_filename(raw: str | None) -> str:
    """Last path component, control characters removed, at most 255 characters (extension kept)."""
    text = _PCT_DANGEROUS.sub(lambda m: chr(int(m.group(0)[1:], 16)), raw or "")
    name = text.replace("\\", "/").rsplit("/", 1)[-1]
    name = os.path.basename(name)
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf", "Cs")).strip()
    if not name.strip("."):
        return DEFAULT_FILENAME
    if len(name) > FILENAME_MAX:
        stem, ext = os.path.splitext(name)
        ext = ext[:20]
        name = stem[: FILENAME_MAX - len(ext)] + ext
    return name


def sanitize_content_type(raw: str | None) -> str:
    base = (raw or "").split(";", 1)[0].strip().lower()
    if len(base) <= FILENAME_MAX and _CONTENT_TYPE_RE.match(base):
        return base
    return DEFAULT_CONTENT_TYPE


def attachment_ids_in(attrs: list[AttributeDef], values: Mapping[str, Any]) -> set[int]:
    """Ids referenced by ``attachment`` attributes of ``values`` (any nesting depth)."""
    by_path = dict(iter_attribute_paths(attrs))
    found: set[int] = set()
    for path, refs in collect_reference_paths(attrs, values).items():
        a = by_path.get(path)
        if a is not None and a.type == "attachment":
            found.update(v for v in refs if isinstance(v, int))
    return found


def _chunks(ids: list[int]) -> list[list[int]]:
    return [ids[i : i + _CHUNK] for i in range(0, len(ids), _CHUNK)]


def _record(row: TiqoraCmdbAttachment) -> AttachmentRecord:
    return AttachmentRecord(
        id=int(row.id),
        ci_id=None if row.ci_id is None else int(row.ci_id),
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=int(row.size_bytes),
        sha256=row.sha256,
        created_by=int(row.created_by),
        created_at=row.created_at,
    )


def _history(ci_id: int, event: str, filename: str, user_id: int, at: datetime) -> Any:
    return TiqoraCmdbCiHistory(
        ci_id=ci_id,
        event=event,
        detail=filename[:1000],
        version_id=None,
        created_by=user_id,
        created_at=at,
    )


async def check_staged(
    session: AsyncSession, user_id: int, ids: Iterable[int], *, path: str = "attachment_ids"
) -> list[ValueIssue]:
    """Every id must be a staged upload of ``user_id``; others are ``unknown_reference``."""
    wanted = sorted(set(ids))
    ok: set[int] = set()
    for part in _chunks([i for i in wanted if 0 < i <= 2**63 - 1]):
        rows = await session.execute(
            select(TiqoraCmdbAttachment.id).where(
                TiqoraCmdbAttachment.id.in_(part),
                TiqoraCmdbAttachment.ci_id.is_(None),
                TiqoraCmdbAttachment.created_by == user_id,
            )
        )
        ok.update(int(i) for (i,) in rows.all())
    return [
        ValueIssue(f"{path}[{n}]", "unknown_reference", "Der Anhang existiert nicht.")
        for n, i in enumerate(wanted, start=1)
        if i not in ok
    ]


async def bind_staged(session: AsyncSession, user_id: int, ci_id: int, ids: Iterable[int]) -> int:
    """Attach the user's staged uploads among ``ids`` to the CI (history ``AttachmentAddPost``).

    Files that are not staged by ``user_id`` (already bound, foreign, unknown) are skipped.
    """
    wanted = sorted({i for i in ids if 0 < i <= 2**63 - 1})
    bound = 0
    now = utc_now()
    for part in _chunks(wanted):
        rows = (
            await session.execute(
                select(TiqoraCmdbAttachment.id, TiqoraCmdbAttachment.filename).where(
                    TiqoraCmdbAttachment.id.in_(part),
                    TiqoraCmdbAttachment.ci_id.is_(None),
                    TiqoraCmdbAttachment.created_by == user_id,
                )
            )
        ).all()
        if not rows:
            continue
        await session.execute(
            update(TiqoraCmdbAttachment)
            .where(TiqoraCmdbAttachment.id.in_([int(i) for i, _ in rows]))
            .values(ci_id=ci_id)
            .execution_options(synchronize_session=False)
        )
        for _, filename in rows:
            session.add(_history(ci_id, EVENT_ADD, filename, user_id, now))
        bound += len(rows)
    if bound:
        await session.flush()
    return bound


async def purge_stale_staged(session: AsyncSession, older_than: datetime) -> int:
    """Delete staged (unbound) uploads created before ``older_than``; returns the count."""
    res = await session.execute(
        delete(TiqoraCmdbAttachment)
        .where(TiqoraCmdbAttachment.ci_id.is_(None), TiqoraCmdbAttachment.created_at < older_than)
        .execution_options(synchronize_session=False)
    )
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


class AttachmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _row(self, attachment_id: int) -> TiqoraCmdbAttachment:
        if not 0 < attachment_id <= 2**63 - 1:
            raise CmdbNotFound(f"attachment {attachment_id}")
        row = (
            await self._s.execute(
                select(TiqoraCmdbAttachment)
                .where(TiqoraCmdbAttachment.id == attachment_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            raise CmdbNotFound(f"attachment {attachment_id}")
        return row

    async def _ci_class(self, ci_id: int) -> int:
        class_id = (
            await self._s.execute(select(TiqoraCmdbCi.class_id).where(TiqoraCmdbCi.id == ci_id))
        ).scalar_one_or_none()
        if class_id is None:
            raise CmdbNotFound(f"ci {ci_id}")
        return int(class_id)

    async def _authorize(self, access: CmdbAccess, row: TiqoraCmdbAttachment, perm: str) -> None:
        if row.ci_id is None:  # staged: its uploader only
            if int(row.created_by) != access.user_id:
                raise CmdbNotFound(f"attachment {row.id}")
            return
        await require_class_access(
            self._s,
            access,
            await self._ci_class(int(row.ci_id)),
            "rw" if perm == "rw" else "ro",
        )

    async def upload(
        self,
        access: CmdbAccess,
        *,
        ci_id: int | None,
        filename: str | None,
        content_type: str | None,
        data: bytes,
    ) -> AttachmentRecord:
        now = utc_now()
        name = sanitize_filename(filename)
        if ci_id is not None:
            await require_class_access(self._s, access, await self._ci_class(ci_id), "rw")
        row = TiqoraCmdbAttachment(
            ci_id=ci_id,
            filename=name,
            content_type=sanitize_content_type(content_type),
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content=data,
            created_by=access.user_id,
            created_at=now,
        )
        self._s.add(row)
        await self._s.flush()
        if ci_id is not None:
            self._s.add(_history(ci_id, EVENT_ADD, name, access.user_id, now))
            await self._s.flush()
        return _record(row)

    async def get(self, access: CmdbAccess, attachment_id: int) -> AttachmentRecord:
        row = await self._row(attachment_id)
        await self._authorize(access, row, "ro")
        return _record(row)

    async def list_for_ci(self, access: CmdbAccess, ci_id: int) -> list[AttachmentRecord]:
        await require_class_access(self._s, access, await self._ci_class(ci_id), "ro")
        rows = (
            (
                await self._s.execute(
                    select(TiqoraCmdbAttachment)
                    .where(TiqoraCmdbAttachment.ci_id == ci_id)
                    .order_by(TiqoraCmdbAttachment.id)
                )
            )
            .scalars()
            .all()
        )
        return [_record(r) for r in rows]

    async def content(self, access: CmdbAccess, attachment_id: int) -> bytes:
        row = await self._row(attachment_id)
        await self._authorize(access, row, "ro")
        raw = row.content
        return bytes(raw) if not isinstance(raw, bytes) else raw

    async def delete(self, access: CmdbAccess, attachment_id: int) -> None:
        # local import: ``service`` imports this module for binding on create/update
        from tiqora.cmdb.service import CiService

        row = await self._row(attachment_id)
        await self._authorize(access, row, "rw")
        if row.ci_id is not None:
            ci_id = int(row.ci_id)
            ci = (
                await self._s.execute(
                    select(TiqoraCmdbCi.current_version_id).where(TiqoraCmdbCi.id == ci_id)
                )
            ).scalar_one_or_none()
            if ci is not None:
                version = await CiService(self._s).get_version(
                    access, ci_id, int(ci), enforce_permissions=False
                )
                if int(row.id) in attachment_ids_in(version.definition_attrs, version.attributes):
                    raise AttachmentInUse(f"attachment {attachment_id}")
            self._s.add(_history(ci_id, EVENT_DELETE, row.filename, access.user_id, utc_now()))
        await self._s.execute(
            delete(TiqoraCmdbAttachment)
            .where(TiqoraCmdbAttachment.id == attachment_id)
            .execution_options(synchronize_session=False)
        )
        self._s.expunge(row)
        await self._s.flush()


__all__ = [
    "AttachmentInUse",
    "AttachmentRecord",
    "AttachmentService",
    "attachment_ids_in",
    "bind_staged",
    "check_staged",
    "purge_stale_staged",
    "sanitize_content_type",
    "sanitize_filename",
]
