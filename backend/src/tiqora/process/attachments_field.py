"""Znuny's dialog field ``Attachments``: select existing attachments of the ticket's articles.

Znuny sources (6.5.22): ``AgentTicketProcess.pm`` ``_GetAttachments`` (:6750-6810, the options),
``_RenderAttachment`` (:4447ff, the stored selection preselects), the save loop (:5513-5545,
comma-joined into the dynamic field ``Process::DynamicFieldProcessManagementAttachment``), and
``TransitionAction/Base.pm`` ``_GetAttachments`` (:145-243) plus ``ArticleSend.pm:163-200`` /
``TicketArticleCreate.pm:297-330`` (the actions that mail / store the selection).

An option key is ``ObjectType=Article;ObjectID=<ArticleID>;ID=<FileID>``. ``FileID`` is the
position in Znuny's attachment index (sorted by filename, then id), the same index
:mod:`tiqora.api.compat.ticket_get` uses.

Project rules: attachment bytes are read through :class:`tiqora.storage.backend.DbMimeStorage`
(the dialect rule); the actions mail file contents, so a key whose article does not belong to the
ticket is dropped (an agent with access to ticket A must not be able to mail the files of B).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.ticket_get import _load_attachment_index, attachment_index
from tiqora.crypto.attachment_kind import classify_attachment
from tiqora.db.legacy.queue import StandardAttachment
from tiqora.domain.dynamic_field_values import get_df_values, load_ticket_dynamic_field
from tiqora.domain.ticket_write_service import update_dynamic_field
from tiqora.process.schemas import ProcessDialogOptionOut
from tiqora.storage.backend import DbMimeStorage, _as_bytes
from tiqora.util.perl import perl_truthy
from tiqora.znuny.sysconfig import SysConfig

logger = logging.getLogger(__name__)

__all__ = [
    "ATTACHMENTS_FIELD",
    "attachment_selection_default",
    "collect_action_attachments",
    "collect_reused_attachments",
    "collect_std_attachments",
    "decode_attachment_selection",
    "encode_attachment_selection",
    "format_attachment_label",
    "list_ticket_attachment_options",
    "valid_attachment_keys",
]

ATTACHMENTS_FIELD = "Attachments"

_DF_SETTING = "Process::DynamicFieldProcessManagementAttachment"
_DEFAULT_DF = "ProcessManagementAttachment"
_DEFAULT_FORMAT = "%3$s#%1$d: %2$s"
_POSITIONAL = re.compile(r"%(\d+)\$([sd])|%%|%")
# German labels for sprintf's third and fourth argument (Znuny translates `Article`/`Attachment`).
_OBJECT_LABEL = "Artikel"
_ATTACHMENT_LABEL = "Anhang"
_SKIPPED_ENCRYPTED = frozenset(
    {"application/pkcs7-mime", "application/x-pkcs7-mime", "application/pgp-encrypted"}
)


async def _df_name(sysconfig: SysConfig) -> str:
    return (await sysconfig.get_str(_DF_SETTING, _DEFAULT_DF)) or _DEFAULT_DF


def format_attachment_label(fmt: str, args: Sequence[Any]) -> str:
    """Perl ``sprintf`` with positional arguments (``%N$s`` / ``%N$d``) for the option label.

    Anything else in *fmt* (``%s``, flags, widths, an argument that does not exist) is not
    ported: the default format is used instead of raising or showing ``None``."""

    def render(pattern: str) -> str | None:
        out: list[str] = []
        pos = 0
        for m in _POSITIONAL.finditer(pattern):
            out.append(pattern[pos : m.start()])
            pos = m.end()
            if m.group(0) == "%%":
                out.append("%")
                continue
            if m.group(1) is None:  # a bare `%`
                return None
            index = int(m.group(1)) - 1
            if not 0 <= index < len(args):
                return None
            try:
                out.append(str(int(args[index])) if m.group(2) == "d" else str(args[index]))
            except (TypeError, ValueError):
                return None
        out.append(pattern[pos:])
        return "".join(out)

    return render(fmt) or render(_DEFAULT_FORMAT) or ""


async def _ticket_article_ids(session: AsyncSession, ticket_id: int) -> list[int]:
    rows = await session.execute(
        text("SELECT id FROM article WHERE ticket_id = :t ORDER BY id"), {"t": int(ticket_id)}
    )
    return [int(r[0]) for r in rows]


def _option_key(article_id: int, file_id: int) -> str:
    return f"ObjectType=Article;ObjectID={article_id};ID={file_id}"


async def list_ticket_attachment_options(
    session: AsyncSession, ticket_id: int, sysconfig: SysConfig
) -> list[ProcessDialogOptionOut]:
    """The selectable attachments of the ticket (``_GetAttachments``, AgentTicketProcess.pm:6750).

    Per article (``ArticleNumber`` = position in the ticket) the attachment index without the
    plain-text body and the HTML body, every file with its Znuny ``FileID``."""
    article_ids = await _ticket_article_ids(session, ticket_id)
    if not article_ids:
        return []
    fmt = await sysconfig.get_str(f"{_DF_SETTING}::FormatString", _DEFAULT_FORMAT)
    by_article, _db_ids = await _load_attachment_index(session, article_ids)
    options: list[ProcessDialogOptionOut] = []
    for number, article_id in enumerate(article_ids, start=1):
        rows = by_article.get(article_id)
        if not rows:
            continue
        kept = attachment_index(
            rows,
            exclude_plain_text=True,
            exclude_html_body=True,
            exclude_inline=False,
            html_body=None,
        )
        for row in sorted(kept, key=lambda r: str(r.file_id)):  # Perl `sort keys`
            label = format_attachment_label(
                fmt, (number, row.filename, _OBJECT_LABEL, _ATTACHMENT_LABEL)
            )
            options.append(
                ProcessDialogOptionOut(value=_option_key(article_id, row.file_id), label=label)
            )
    return options


def encode_attachment_selection(keys: Sequence[str]) -> str:
    """The value stored in the dynamic field (``join ',', @Attachments``)."""
    return ",".join(keys)


def decode_attachment_selection(value: str | None) -> list[dict[str, str]]:
    """``Base.pm:176-221``: split at ``,``, each entry at ``;`` into ``key=value`` pairs.

    Entries without ``ObjectType`` are dropped (``next ATTACHMENT if !$Data{ObjectType}``)."""
    out: list[dict[str, str]] = []
    for entry in (value or "").split(","):
        data: dict[str, str] = {}
        for part in entry.split(";"):
            key, sep, val = part.partition("=")
            if sep and key:
                data[key] = val
        if data.get("ObjectType"):
            out.append(data)
    return out


async def valid_attachment_keys(
    session: AsyncSession, ticket_id: int, sysconfig: SysConfig, submitted: Any
) -> list[str]:
    """The submitted selection (a list of keys, or the comma-joined string) without the keys that
    are not in the ticket's current option list (forged or stale keys are dropped)."""
    if isinstance(submitted, str):
        wanted = [k for k in submitted.split(",") if k.strip()]
    elif isinstance(submitted, list | tuple):
        wanted = [str(k) for k in submitted if str(k).strip()]
    else:
        return []
    if not wanted:
        return []
    allowed = {o.value for o in await list_ticket_attachment_options(session, ticket_id, sysconfig)}
    kept = [k for k in wanted if k in allowed]
    if len(kept) != len(wanted):
        logger.warning(
            "ticket %s: dropped %d unknown attachment key(s) from the dialog submit",
            ticket_id,
            len(wanted) - len(kept),
        )
    return kept


async def attachment_selection_default(
    session: AsyncSession,
    ticket_id: int,
    sysconfig: SysConfig,
    options: Sequence[ProcessDialogOptionOut],
) -> list[str]:
    """The stored selection (``_RenderAttachment`` preselects ``SelectedID``), limited to the
    keys still on offer."""
    config = await load_ticket_dynamic_field(session, await _df_name(sysconfig))
    if config is None:
        return []
    stored = await get_df_values(session, config, ticket_id)
    offered = {o.value for o in options}
    keys = [k for v in stored for k in v.split(",") if k in offered]
    return keys


def _encrypted(filename: str | None, content_type: str | None) -> bool:
    base = (content_type or "").split(";", 1)[0].strip().lower()
    return base in _SKIPPED_ENCRYPTED or classify_attachment(filename, content_type) == (
        "pgp_message"
    )


async def collect_reused_attachments(
    session: AsyncSession, *, ticket_id: int, sysconfig: SysConfig, user_id: int = 1
) -> list[tuple[str, str, bytes]]:
    """``Base.pm _GetAttachments`` (:145-243): the files named by the ticket's selection field.

    Returns ``(filename, content_type, bytes)``. A key whose article is not an article of
    *ticket_id* is skipped with a warning, as is an encrypted blob (no decryption context here)
    and a file that does not exist. The field is cleared afterwards unless
    ``Process::DynamicFieldProcessManagementAttachment::Keep`` is truthy. Znuny clears with
    ``UserID => 1``; *user_id* is accepted for call compatibility and not used."""
    config = await load_ticket_dynamic_field(session, await _df_name(sysconfig))
    if config is None:  # `return ... if !IsHashRefWithData($DynamicFieldConfig)`
        return []
    stored = await get_df_values(session, config, ticket_id)
    selected = decode_attachment_selection(stored[0] if stored else None)
    if not selected:
        return []

    article_ids = await _ticket_article_ids(session, ticket_id)
    own = set(article_ids)
    _by_article, db_ids = await _load_attachment_index(session, article_ids) if own else ({}, {})
    storage = DbMimeStorage(session)
    files: list[tuple[str, str, bytes]] = []
    for data in selected:
        if data["ObjectType"] != "Article":
            continue
        oid, fid = data.get("ObjectID", ""), data.get("ID", "")
        if not (oid.isdecimal() and fid.isdecimal()):
            continue
        if int(oid) not in own:
            logger.warning(
                "ticket %s: attachment selection names article %s of another ticket, skipped",
                ticket_id,
                oid,
            )
            continue
        row_id = db_ids.get((int(oid), int(fid)))
        found = await storage.get_attachment(row_id) if row_id is not None else None
        if found is None:
            continue
        meta = found.meta
        if _encrypted(meta.filename, meta.content_type):
            logger.warning(
                "ticket %s: attachment %s of article %s is encrypted, not reused",
                ticket_id,
                meta.filename,
                oid,
            )
            continue
        files.append(
            (
                meta.filename or "attachment",
                meta.content_type or "application/octet-stream",
                found.content,
            )
        )

    if not await sysconfig.get_bool(f"{_DF_SETTING}::Keep"):
        await update_dynamic_field(
            session, ticket_id=ticket_id, field_name=config.name, values=[""], user_id=1
        )
    return files


def _split_list(value: Any) -> list[str]:
    """``split /\\s*,\\s*/`` of a Config value (a list value is taken as is)."""
    if isinstance(value, list | tuple):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p for p in re.split(r"\s*,\s*", str(value or "").strip()) if p]


async def collect_std_attachments(
    session: AsyncSession, config: Mapping[str, Any]
) -> list[tuple[str, str, bytes]]:
    """``ArticleSend.pm:168-200``: ``AttachmentIDs`` first, then ``Attachments`` (names).

    A name or id that does not exist is skipped; an invalid standard attachment is logged as an
    error and skipped (``ValidID != 1``)."""
    ids: list[int] = [int(i) for i in _split_list(config.get("AttachmentIDs")) if i.isdecimal()]
    names = _split_list(config.get("Attachments"))
    if names:
        rows = (
            await session.execute(
                select(StandardAttachment.id, StandardAttachment.name).where(
                    StandardAttachment.name.in_(names)
                )
            )
        ).all()
        by_name = {str(r[1]): int(r[0]) for r in rows}  # exact case (the IN is collation-wide)
        ids.extend(by_name[n] for n in names if n in by_name)
    if not ids:
        return []
    found = {
        int(r.id): r
        for r in (
            await session.execute(
                select(StandardAttachment).where(StandardAttachment.id.in_(sorted(set(ids))))
            )
        ).scalars()
    }
    files: list[tuple[str, str, bytes]] = []
    for sid in ids:
        row = found.get(sid)
        if row is None:
            continue
        if row.valid_id != 1:
            logger.error(
                "ArticleSend: Attachment (ID: %s, Name: %s) is invalid. Skip Attachment!",
                sid,
                row.name,
            )
            continue
        files.append((row.filename, row.content_type, _as_bytes(row.content)))
    return files


async def collect_action_attachments(
    session: AsyncSession,
    config: Mapping[str, Any],
    *,
    ticket_id: int,
    sysconfig: SysConfig,
    user_id: int,
) -> list[tuple[str, str, bytes]]:
    """What ``ArticleSend`` / ``TicketArticleCreate`` attach: the reused selection when
    ``AttachmentsReuse`` is truthy (``ArticleSend.pm:163``), then the standard attachments."""
    files: list[tuple[str, str, bytes]] = []
    if perl_truthy(config.get("AttachmentsReuse")):
        files.extend(
            await collect_reused_attachments(
                session, ticket_id=ticket_id, sysconfig=sysconfig, user_id=user_id
            )
        )
    if config.get("Attachments") or config.get("AttachmentIDs"):
        files.extend(await collect_std_attachments(session, config))
    return files
