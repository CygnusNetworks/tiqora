"""Znuny reads the itsm1 adapter does not offer (C3 fallback): links by CI id and change times.

Plain ``text()`` statements with bound parameters on Znuny tables only (no ``tiqora_*`` join).
CI attachments (``virtual_fs``, Task 11); change and work order counts (Task 12).
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import bindparam, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from tiqora.itsm.importer.ports import EPOCH, ZnunyAttachment, ZnunyLink
from tiqora.itsm.znuny_adapter.catalog import chunks
from tiqora.znuny.sysconfig import SysConfig

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


async def class_count(session: AsyncSession) -> int:
    """Classes that have at least one definition (what the import can bring over)."""
    query = text("SELECT COUNT(DISTINCT class_id) FROM configitem_definition")
    return int((await session.execute(query)).scalar_one())


_LINK_OBJECT_ID = text("SELECT id FROM link_object WHERE name = :obj")
_LINKS_CREATED_SINCE = text(
    "SELECT COUNT(*) FROM link_relation"
    " WHERE (source_object_id = :oid OR target_object_id = :oid) AND create_time > :since"
)


async def count_links_changed_since(
    session: AsyncSession, since: datetime, *, ci_object: str
) -> int:
    """``link_relation`` rows with a CI at one end that were created after ``since``.

    Links do not touch ``configitem.change_time``, so the drift count needs them separately
    (Assumption 9 ruling). A deleted link leaves no row behind and cannot be counted.
    """
    object_id = (await session.execute(_LINK_OBJECT_ID, {"obj": ci_object})).scalar_one_or_none()
    if object_id is None:
        return 0
    return int(
        (
            await session.execute(_LINKS_CREATED_SINCE, {"oid": int(object_id), "since": since})
        ).scalar_one()
    )


#: Znuny ITSMChangeManagement tables counted by :func:`count_changes`: report key -> table
_CHANGE_TABLES = {"change": "change_item", "workorder": "change_workorder"}


async def count_changes(session: AsyncSession) -> dict[str, int]:
    """Changes and work orders of ITSMChangeManagement (0 when the package is not installed).

    Only counted: this wave imports none of them (the report says so).
    """

    def table_names(sync_session: Session) -> set[str]:
        return set(inspect(sync_session.connection()).get_table_names())

    names = await session.run_sync(table_names)
    out: dict[str, int] = {}
    for key, table in _CHANGE_TABLES.items():
        if table in names:
            out[key] = int(
                (await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
            )
        else:
            out[key] = 0
    return out


# --- CI attachments (Znuny ITSMConfigItem::ConfigItemAttachmentAdd/List/Get) --------------------
#
# ``virtual_fs.filename = 'ConfigItem/<ConfigItemID>/<FileName>'``; ``virtual_fs.backend`` names
# the storage class: ``Kernel::System::VirtualFS::DB`` keeps the bytes in ``virtual_fs_db`` (row id
# = ``FileID`` of ``virtual_fs.backend_key`` (``Compress=0;Crypt=0;FileID=3;Mode=binary;``, or a
# bare number); MySQL as BLOB, PostgreSQL as MIME::Base64 text), any other backend
# (``...::FS``) keeps files on Znuny's disk, which Tiqora cannot reach (``data`` stays None).
# ``virtual_fs_preferences`` carries ``ContentType`` and ``FilesizeRaw``.

#: largest attachment whose bytes the reader loads (the step reports larger ones)
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
_FS_PREFIX = "ConfigItem/"
_DB_BACKEND_SUFFIX = "::DB"
_PREFIX_CHUNK = 50  # one LIKE per CI: keep the statement small
_FILENAME = re.compile(r"ConfigItem/(\d+)/(.+)", re.DOTALL)


def _prefix_query(n: int) -> str:
    likes = " OR ".join(f"filename LIKE :p{i}" for i in range(n))
    return (
        "SELECT id, filename, backend, backend_key, create_time FROM virtual_fs"
        f" WHERE {likes} ORDER BY id"
    )


_PREFERENCES = text(
    "SELECT virtual_fs_id, preferences_key, preferences_value FROM virtual_fs_preferences"
    " WHERE virtual_fs_id IN :ids"
    " AND preferences_key IN ('ContentType', 'FilesizeRaw', 'UserID', 'ContentID')"
).bindparams(bindparam("ids", expanding=True))
_CONTENT = text("SELECT id, content FROM virtual_fs_db WHERE id IN :ids").bindparams(
    bindparam("ids", expanding=True)
)


def _decode_content(value: object) -> bytes:
    """``virtual_fs_db.content`` as bytes (BLOB, or PostgreSQL's base64 text)."""
    if value is None:
        return b""
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    raw = str(value)
    try:
        return base64.b64decode("".join(raw.split()), validate=True)
    except (binascii.Error, ValueError):
        return raw.encode("utf-8")


async def read_ci_attachments(
    session: AsyncSession,
    ci_ids: Sequence[int],
    *,
    with_data: bool,
    sysconfig: SysConfig | None = None,
) -> list[ZnunyAttachment]:
    """Attachments of the CIs, ordered by CI id and ``virtual_fs.id``.

    ``with_data=False`` never reads ``virtual_fs_db.content``. With data, only the DB backend is
    read, and only files up to :data:`MAX_ATTACHMENT_BYTES` (larger ones keep ``data=None`` and
    their real ``size``, so the caller can report them). ``sysconfig`` is accepted for the
    adapter's signature; the FS backend is never read, so no path setting is needed.
    """
    wanted = sorted({int(i) for i in ci_ids})
    files: list[tuple[int, int, str, str, str, datetime]] = []  # vfs id, ci, name, backend, key, t
    for part in chunks(wanted, _PREFIX_CHUNK):
        params = {f"p{i}": f"{_FS_PREFIX}{ci}/%" for i, ci in enumerate(part)}
        for r in (await session.execute(text(_prefix_query(len(part))), params)).all():
            match = _FILENAME.fullmatch(str(r[1]))
            if match is None or int(match.group(1)) not in wanted:
                continue  # LIKE is case-insensitive on MariaDB; the id must match exactly
            files.append(
                (
                    int(r[0]),
                    int(match.group(1)),
                    match.group(2),
                    str(r[2]),
                    str(r[3]),
                    r[4] if r[4] is not None else EPOCH,
                )
            )
    if not files:
        return []
    prefs: dict[int, dict[str, str]] = {}
    for part in chunks([f[0] for f in files]):
        for r in (await session.execute(_PREFERENCES, {"ids": list(part)})).all():
            prefs.setdefault(int(r[0]), {})[str(r[1])] = "" if r[2] is None else str(r[2])
    sizes = {f[0]: _size(prefs.get(f[0], {})) for f in files}
    content: dict[int, bytes] = {}
    if with_data:
        db_keys = {
            f[0]: file_id
            for f in files
            if f[3].endswith(_DB_BACKEND_SUFFIX)
            and (file_id := _db_file_id(f[4])) is not None
            and (sizes[f[0]] or 0) <= MAX_ATTACHMENT_BYTES
        }
        by_key = {key: vfs for vfs, key in db_keys.items()}
        for part in chunks(sorted(by_key)):
            for r in (await session.execute(_CONTENT, {"ids": list(part)})).all():
                content[by_key[int(r[0])]] = _decode_content(r[1])
    out: list[ZnunyAttachment] = []
    for vfs_id, ci, name, _backend, _key, created in sorted(files, key=lambda f: (f[1], f[0])):
        data = content.get(vfs_id)
        size = sizes[vfs_id]
        out.append(
            ZnunyAttachment(
                ci_id=ci,
                filename=name,
                content_type=prefs.get(vfs_id, {}).get("ContentType") or "",
                size=len(data) if size is None and data is not None else (size or 0),
                data=data,
                created_at=created,
                user_id=_user_id(prefs.get(vfs_id, {})),
                content_id=prefs.get(vfs_id, {}).get("ContentID", ""),
            )
        )
    return out


def _db_file_id(backend_key: str) -> int | None:
    """``virtual_fs_db.id`` named by a ``virtual_fs.backend_key``.

    Znuny's ``VirtualFS::DB`` writes ``Compress=0;Crypt=0;FileID=3;Mode=binary;`` (seen on the
    Znuny 6.5 and 7.3 golden peers); a bare number is accepted as well.
    """
    key = backend_key.strip()
    if key.isdigit():
        return int(key)
    for pair in key.split(";"):
        name, _, value = pair.partition("=")
        if name.strip() == "FileID" and value.strip().isdigit():
            return int(value.strip())
    return None


def _user_id(prefs: dict[str, str]) -> int | None:
    raw = prefs.get("UserID", "").strip()
    return int(raw) if raw.isdigit() else None


def _size(prefs: dict[str, str]) -> int | None:
    raw = prefs.get("FilesizeRaw", "").strip()
    return int(raw) if raw.isdigit() else None
