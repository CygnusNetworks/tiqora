"""Znuny-shaped ``TicketGet`` data for the GenericInterface compat layer.

Port of ``Kernel/GenericInterface/Operation/Ticket/TicketGet.pm`` ``Run`` plus the
Znuny 6.5 calls it makes. Key lists below were confirmed against the Perl source.

Ticket (``Kernel/System/Ticket.pm`` ``TicketGet``, called without ``UserID`` details)::

    Age ArchiveFlag("y"/"n") ChangeBy Changed CreateBy Created CustomerID CustomerUserID
    EscalationResponseTime EscalationSolutionTime EscalationTime EscalationUpdateTime GroupID
    Lock LockID Owner OwnerID Priority PriorityID Queue QueueID RealTillTimeNotUsed
    Responsible ResponsibleID Service* ServiceID SLA* SLAID State StateID StateType TicketID
    TicketNumber Title Type TypeID UnlockTimeout UntilTime
    + TimeUnit (``TicketAccountedTimeGet``) + DynamicField [{Name, Value}] (sorted by name)

    * ``Service``/``SLA`` exist only when the id is non-empty. ``ServiceID``/``SLAID`` are ``""``
      when unset, ``ResponsibleID``/``TypeID`` fall back to 1 (``|| 1``).
    ``Extended=1`` adds ``FirstResponse`` (first customer-visible agent article), ``Closed``
    (last NewTicket/StateUpdate history row into a closed state) and ``FirstLock``
    (``_TicketGetExtended``).

Known gaps (documented, not faked): the working-time based keys ``FirstResponseInMin``,
``FirstResponseDiffInMin``, ``SolutionInMin``, ``SolutionDiffInMin`` and the escalation detail
keys of ``TicketEscalationDateCalculation`` (``EscalationDestinationIn`` ...) need a calendar
working-time calculation that Tiqora does not have; ``EscalationTime`` keeps the stored column.

Article (``Article.pm`` ``_MetaArticleList`` + ``MIMEBase.pm`` ``ArticleGet``; the GenericInterface
never passes ``RealNames``, so there are no ``*Realname`` keys)::

    ArticleID ArticleNumber(1-based position in the whole ticket) Bcc Body Cc ChangeBy
    ChangeTime Charset CommunicationChannel CommunicationChannelID ContentCharset ContentType
    CreateBy CreateTime From IncomingTime InReplyTo IsVisibleForCustomer MessageID MimeType
    References ReplyTo SenderType SenderTypeID Subject TicketID To
    + TimeUnit (``ArticleAccountedTimeGet``) + DynamicField (Article-object fields only; every
    valid field is listed, ``Value`` is null when unset) when ``DynamicFields=1``
    + Attachment only with ``Attachments=1`` *and* a non-empty attachment index.

Attachment (``ArticleStorageDB.pm`` ``ArticleAttachment`` + ``TicketGet.pm``)::

    Content(base64, "" when GetAttachmentContents=0) ContentAlternative ContentID ContentType
    Disposition FileID Filename FilesizeRaw

``FileID`` is the 1-based position in ``ORDER BY filename, id``; the response lists files in
Perl's string sort of the ids. ``ExcludePlainText`` always, ``ExcludeHTMLBody`` unless
``HTMLBodyAsAttachment``, never ``ExcludeInline``.

All timestamps are ``YYYY-MM-DD HH:MM:SS`` (:func:`znuny_ts`).
"""

from __future__ import annotations

import base64
import calendar
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.domain.dynamic_field_values import DynamicFieldConfig, get_df_values_bulk
from tiqora.storage.backend import _as_bytes  # same driver normalisation as DbMimeStorage
from tiqora.znuny.perl_format import perl_num


def znuny_ts(value: datetime | None) -> str:
    """Znuny timestamp ``YYYY-MM-DD HH:MM:SS``; ``None`` -> ``""``."""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else ""


def znuny_num(value: Decimal | float | int | None) -> int | float:
    """Perl numification (``3.00`` -> ``3``, ``1.50`` -> ``1.5``)."""
    return perl_num(value)


# ---------------------------------------------------------------------------
# Attachment index (MIMEBase/Base.pm ArticleAttachmentIndex)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttachmentRow:
    file_id: int
    filename: str
    content_type: str
    content_id: str
    content_alternative: str
    disposition: str
    content_size: int
    content: bytes | None


_TEXT_PLAIN = re.compile(r"text/plain", re.IGNORECASE)
_TEXT_HTML = re.compile(r"text/html", re.IGNORECASE)
_FILE_12 = re.compile(r"file-[12]\Z")
_IMAGE = re.compile(r"image", re.IGNORECASE)
_CID_BRACKETS = re.compile(r"^<(.*)>$", re.MULTILINE | re.DOTALL)


def attachment_index(
    rows: Sequence[AttachmentRow],
    *,
    exclude_plain_text: bool,
    exclude_html_body: bool,
    exclude_inline: bool,
    html_body: str | None,
) -> list[AttachmentRow]:
    """Exact port of ``ArticleAttachmentIndex`` (``MIMEBase/Base.pm``).

    ``rows`` is the raw index in ``file_id`` order; ``html_body`` is the content of the html
    body part (used only to find inline images referenced by it). The result keeps the input
    order.
    """
    if not (exclude_plain_text or exclude_html_body or exclude_inline):
        return list(rows)

    # Perl iterates ``sort keys`` -- a string sort ("10" before "2").
    ordered = sorted(rows, key=lambda r: str(r.file_id))
    plain_id: int | None = None
    html_id: int | None = None

    for r in ordered:
        if (
            plain_id is None
            and r.filename == "file-1"
            and _TEXT_PLAIN.search(r.content_type)
            and r.disposition == "inline"
        ):
            plain_id = r.file_id
            continue
        if (
            html_id is None
            and (_FILE_12.match(r.filename) or r.filename == "file-1.html")
            and _TEXT_HTML.search(r.content_type)
            and r.disposition == "inline"
        ):
            html_id = r.file_id

    if plain_id is None and html_id is None:
        for r in ordered:
            if r.filename == "file-1" and _TEXT_PLAIN.search(r.content_type):
                plain_id = r.file_id
                break

    inline_ids: set[int] = set()
    if html_id is not None and html_body:
        for r in ordered:
            if not _IMAGE.search(r.content_type) or not r.content_id:
                continue
            match = _CID_BRACKETS.match(r.content_id)
            image_id = match.group(1) if match else ""
            pattern = rf"<img.+src=['|\"]cid:{re.escape(image_id)}['|\"].*>"
            if re.search(pattern, html_body, re.IGNORECASE | re.DOTALL):
                inline_ids.add(r.file_id)

    drop: set[int] = set()
    if plain_id is not None and exclude_plain_text:
        drop.add(plain_id)
    if html_id is not None and exclude_html_body:
        drop.add(html_id)
    if exclude_inline:
        drop |= inline_ids
    return [r for r in rows if r.file_id not in drop]


# ---------------------------------------------------------------------------
# Dynamic fields
# ---------------------------------------------------------------------------


async def _load_df_configs(session: AsyncSession, object_type: str) -> list[DynamicFieldConfig]:
    rows = (
        await session.execute(
            text(
                "SELECT id, name, field_type FROM dynamic_field"
                " WHERE object_type = :ot AND valid_id = 1"
            ),
            {"ot": object_type},
        )
    ).fetchall()
    return sorted(
        (DynamicFieldConfig(id=int(r[0]), name=str(r[1]), field_type=str(r[2])) for r in rows),
        key=lambda f: f.name,
    )


def _df_value(field: DynamicFieldConfig, values: Sequence[str]) -> Any:
    """``ValueGet`` shape: Multiselect is a list, every other driver a scalar (or null)."""
    if field.field_type == "Multiselect":
        return list(values)
    return values[0] if values else None


# ---------------------------------------------------------------------------
# Ticket
# ---------------------------------------------------------------------------

_TICKET_SQL = text(
    "SELECT t.id, t.queue_id, t.ticket_state_id, t.ticket_lock_id, t.ticket_priority_id,"
    " t.create_time, t.tn, t.customer_id, t.customer_user_id, t.user_id, t.responsible_user_id,"
    " t.until_time, t.change_time, t.title, t.escalation_update_time, t.timeout, t.type_id,"
    " t.service_id, t.sla_id, t.escalation_response_time, t.escalation_solution_time,"
    " t.escalation_time, t.archive_flag, t.create_by, t.change_by,"
    " q.name AS queue_name, q.group_id AS group_id, ts.name AS state_name,"
    " tst.name AS state_type, tp.name AS priority_name, lt.name AS lock_name,"
    " ou.login AS owner_login, ru.login AS responsible_login, tt.name AS type_name,"
    " svc.name AS service_name, sla.name AS sla_name"
    " FROM ticket t"
    " LEFT JOIN queue q ON q.id = t.queue_id"
    " LEFT JOIN ticket_state ts ON ts.id = t.ticket_state_id"
    " LEFT JOIN ticket_state_type tst ON tst.id = ts.type_id"
    " LEFT JOIN ticket_priority tp ON tp.id = t.ticket_priority_id"
    " LEFT JOIN ticket_lock_type lt ON lt.id = t.ticket_lock_id"
    " LEFT JOIN users ou ON ou.id = t.user_id"
    " LEFT JOIN users ru ON ru.id = COALESCE(NULLIF(t.responsible_user_id, 0), 1)"
    " LEFT JOIN ticket_type tt ON tt.id = COALESCE(NULLIF(t.type_id, 0), 1)"
    " LEFT JOIN service svc ON svc.id = t.service_id"
    " LEFT JOIN sla ON sla.id = t.sla_id"
    " WHERE t.id = :tid"
)


async def _scalar_ts(session: AsyncSession, sql: str, params: dict[str, Any]) -> str | None:
    value = (await session.execute(text(sql), params)).scalar_one_or_none()
    return znuny_ts(value) if isinstance(value, datetime) else None


async def _extended(session: AsyncSession, ticket_id: int) -> dict[str, Any]:
    """``_TicketGetExtended``: FirstResponse, Closed, FirstLock (no working-time keys)."""
    out: dict[str, Any] = {}
    first_response = await _scalar_ts(
        session,
        "SELECT a.create_time FROM article a"
        " JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
        " WHERE a.ticket_id = :tid AND ast.name = 'agent' AND a.is_visible_for_customer = 1"
        " ORDER BY a.create_time, a.id LIMIT 1",
        {"tid": ticket_id},
    )
    if first_response:
        out["FirstResponse"] = first_response
    closed = await _scalar_ts(
        session,
        "SELECT MAX(th.create_time) FROM ticket_history th"
        " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
        " WHERE th.ticket_id = :tid AND tht.name IN ('StateUpdate', 'NewTicket')"
        " AND th.state_id IN (SELECT ts.id FROM ticket_state ts"
        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
        " WHERE tst.name = 'closed' AND ts.valid_id = 1)",
        {"tid": ticket_id},
    )
    if closed:
        out["Closed"] = closed
    first_lock = await _scalar_ts(
        session,
        "SELECT th.create_time FROM ticket_history th"
        " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
        " WHERE th.ticket_id = :tid AND tht.name = 'Lock'"
        " ORDER BY th.create_time ASC, th.id ASC LIMIT 1",
        {"tid": ticket_id},
    )
    if first_lock:
        out["FirstLock"] = first_lock
    return out


async def build_ticket_entry(
    session: AsyncSession,
    ticket_id: int,
    *,
    dynamic_fields: bool,
    extended: bool,
    now: datetime,
) -> dict[str, Any] | None:
    """The ``Ticket[]`` entry for one ticket (without ``Article``); ``None`` when unknown.

    ``now`` is naive UTC (Znuny stores UTC) and drives ``Age`` and ``UntilTime``.
    """
    row = (await session.execute(_TICKET_SQL, {"tid": ticket_id})).mappings().first()
    if row is None:
        return None

    created: datetime = row["create_time"]
    state_type = str(row["state_type"] or "")
    real_till = int(row["until_time"] or 0)
    # TicketGet: `lc $StateData{TypeName} eq 'pending'` is a literal compare and the real
    # state type names are "pending reminder"/"pending auto", so it practically never holds.
    if not real_till or state_type.lower() == "pending":
        until_time = 0
    else:
        until_time = real_till - calendar.timegm(now.timetuple())

    responsible_id = int(row["responsible_user_id"] or 0) or 1
    type_id = int(row["type_id"] or 0) or 1
    service_id = int(row["service_id"] or 0)
    sla_id = int(row["sla_id"] or 0)

    entry: dict[str, Any] = {
        "TicketID": int(row["id"]),
        "QueueID": row["queue_id"],
        "StateID": row["ticket_state_id"],
        "LockID": row["ticket_lock_id"],
        "PriorityID": row["ticket_priority_id"],
        "Created": znuny_ts(created),
        "TicketNumber": row["tn"],
        "CustomerID": row["customer_id"],
        "CustomerUserID": row["customer_user_id"],
        "OwnerID": row["user_id"],
        "ResponsibleID": responsible_id,
        "RealTillTimeNotUsed": real_till,
        "Changed": znuny_ts(row["change_time"]),
        "Title": row["title"],
        "EscalationUpdateTime": row["escalation_update_time"],
        "UnlockTimeout": row["timeout"],
        "TypeID": type_id,
        "ServiceID": service_id or "",
        "SLAID": sla_id or "",
        "EscalationResponseTime": row["escalation_response_time"],
        "EscalationSolutionTime": row["escalation_solution_time"],
        "EscalationTime": row["escalation_time"],
        "ArchiveFlag": "y" if row["archive_flag"] else "n",
        "CreateBy": row["create_by"],
        "ChangeBy": row["change_by"],
        "Queue": row["queue_name"],
        "GroupID": row["group_id"],
        "Age": abs(int((now - created).total_seconds())),
        "Priority": row["priority_name"],
        "Owner": row["owner_login"],
        "Responsible": row["responsible_login"],
        "Lock": row["lock_name"],
        "Type": row["type_name"],
        "StateType": row["state_type"],
        "State": row["state_name"],
        "UntilTime": until_time,
    }
    if service_id:
        entry["Service"] = row["service_name"]
    if sla_id:
        entry["SLA"] = row["sla_name"]

    if extended:
        entry.update(await _extended(session, ticket_id))

    units = (
        await session.execute(
            text("SELECT time_unit FROM time_accounting WHERE ticket_id = :tid"),
            {"tid": ticket_id},
        )
    ).fetchall()
    entry["TimeUnit"] = znuny_num(sum((Decimal(str(u[0])) for u in units), Decimal(0)))

    if dynamic_fields:
        fields = await _load_df_configs(session, "Ticket")
        values = await get_df_values_bulk(session, fields, [ticket_id])
        df_entries = [
            {"Name": f.name, "Value": _df_value(f, values[(f.id, ticket_id)])}
            for f in fields
            if (f.id, ticket_id) in values
        ]
        if df_entries:
            entry["DynamicField"] = df_entries
    return entry


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------

_MIME_TYPE = re.compile(r"^(\w+/\w+)", re.IGNORECASE)


def _charset(content_type: str | None) -> str:
    if not content_type or "charset=" not in content_type.lower():
        return ""
    charset = re.sub(r".+?charset=(\"|'|)(\w+)", r"\2", content_type, flags=re.IGNORECASE)
    charset = charset.replace('"', "").replace("'", "")
    return re.sub(r"(.+?);.*", r"\1", charset)


def _mime_type(content_type: str | None) -> str:
    if content_type:
        match = _MIME_TYPE.match(content_type)
        if match:
            return match.group(1).replace('"', "").replace("'", "")
    return ""


def _strip_newlines(value: Any) -> Any:
    if value:
        return str(value).replace("\n", "").replace("\r", "")
    return value


def _default_disposition(filename: str, content_type: str, content_id: str) -> str:
    if content_id and re.search("image", content_type, re.IGNORECASE):
        return "inline"
    if re.search("file-[12]", filename):
        return "inline"
    return "attachment"


async def _load_attachments(
    session: AsyncSession, article_ids: Sequence[int]
) -> dict[int, list[AttachmentRow]]:
    stmt = text(
        "SELECT article_id, filename, content_type, content_size, content_id,"
        " content_alternative, disposition, content FROM article_data_mime_attachment"
        " WHERE article_id IN :ids ORDER BY article_id, filename, id"
    ).bindparams(bindparam("ids", expanding=True))
    rows = (await session.execute(stmt, {"ids": list(article_ids)})).fetchall()
    out: dict[int, list[AttachmentRow]] = {}
    for article_id, filename, ctype, size, cid, alt, disposition, content in rows:
        items = out.setdefault(int(article_id), [])
        fname = str(filename or "")
        ctype_s = str(ctype or "")
        cid_s = str(cid or "")
        items.append(
            AttachmentRow(
                file_id=len(items) + 1,
                filename=fname,
                content_type=ctype_s,
                content_id=cid_s,
                content_alternative=str(alt or ""),
                disposition=str(disposition or _default_disposition(fname, ctype_s, cid_s)),
                content_size=int(perl_num(size)),
                content=_as_bytes(content) if content is not None else None,
            )
        )
    return out


def _attachment_entries(
    rows: Sequence[AttachmentRow], *, with_contents: bool, html_body_as_attachment: bool
) -> list[dict[str, Any]]:
    kept = attachment_index(
        rows,
        exclude_plain_text=True,
        exclude_html_body=not html_body_as_attachment,
        exclude_inline=False,
        html_body=None,
    )
    out: list[dict[str, Any]] = []
    for r in sorted(kept, key=lambda r: str(r.file_id)):
        out.append(
            {
                "Content": base64.b64encode(r.content or b"").decode("ascii")
                if with_contents
                else "",
                "ContentAlternative": r.content_alternative if with_contents else "",
                "ContentID": r.content_id,
                "ContentType": r.content_type,
                "Disposition": r.disposition,
                "FileID": r.file_id,
                "Filename": r.filename,
                "FilesizeRaw": r.content_size,
            }
        )
    return out


async def build_article_entries(
    session: AsyncSession,
    ticket_id: int,
    *,
    customer_only: bool,
    sender_types: list[str] | None,
    order: str,
    limit: int,
    dynamic_fields: bool,
    attachments: bool,
    get_attachment_contents: bool,
    html_body_as_attachment: bool,
) -> list[dict[str, Any]]:
    """The ``Article[]`` entries of one ticket exactly as ``TicketGet.pm`` assembles them."""
    metas = (
        (
            await session.execute(
                text(
                    "SELECT id, communication_channel_id, article_sender_type_id,"
                    " is_visible_for_customer, create_by, create_time, change_by, change_time"
                    " FROM article WHERE ticket_id = :tid ORDER BY id ASC"
                ),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .all()
    )
    # ArticleNumber is the position in the whole ticket, before any filter.
    numbered: list[tuple[int, Any]] = list(enumerate(metas, start=1))
    if customer_only:
        numbered = [(n, m) for n, m in numbered if int(m["is_visible_for_customer"] or 0) == 1]

    sender_names = {
        int(r[0]): str(r[1])
        for r in (
            await session.execute(text("SELECT id, name FROM article_sender_type"))
        ).fetchall()
    }
    channel_names = {
        int(r[0]): str(r[1])
        for r in (
            await session.execute(text("SELECT id, name FROM communication_channel"))
        ).fetchall()
    }

    selected: list[tuple[int, Any]]
    if sender_types:
        selected = []
        for sender_type in sender_types:
            selected.extend(
                (n, m)
                for n, m in numbered
                if sender_names.get(int(m["article_sender_type_id"])) == sender_type
            )
    else:
        selected = numbered

    # Znuny quirk: DESC only reverses when a limit is in effect.
    if selected and limit > 0:
        limit = min(limit, len(selected))
        if order == "DESC":
            selected.reverse()
        selected = selected[:limit]
    if not selected:
        return []

    article_ids = sorted({int(m["id"]) for _, m in selected})
    mime_stmt = text(
        "SELECT article_id, a_from, a_reply_to, a_to, a_cc, a_bcc, a_subject, a_message_id,"
        " a_in_reply_to, a_references, a_content_type, a_body, incoming_time"
        " FROM article_data_mime WHERE article_id IN :ids ORDER BY id"
    ).bindparams(bindparam("ids", expanding=True))
    mime_by_article: dict[int, Any] = {}
    for mime_row in (await session.execute(mime_stmt, {"ids": article_ids})).mappings().all():
        mime_by_article.setdefault(int(mime_row["article_id"]), mime_row)

    time_units: dict[int, Decimal] = {}
    unit_stmt = text(
        "SELECT article_id, time_unit FROM time_accounting WHERE article_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    for aid, unit in (await session.execute(unit_stmt, {"ids": article_ids})).fetchall():
        time_units[int(aid)] = time_units.get(int(aid), Decimal(0)) + Decimal(str(unit))

    df_fields: list[DynamicFieldConfig] = []
    df_values: dict[tuple[int, int], list[str]] = {}
    if dynamic_fields:
        df_fields = await _load_df_configs(session, "Article")
        df_values = await get_df_values_bulk(session, df_fields, article_ids)

    atts_by_article: dict[int, list[AttachmentRow]] = {}
    if attachments:
        atts_by_article = await _load_attachments(session, article_ids)

    out: list[dict[str, Any]] = []
    for number, meta in selected:
        aid = int(meta["id"])
        mime = mime_by_article.get(aid)
        if mime is None:
            # ArticleGet returns nothing for an article without article_data_mime.
            continue
        content_type = mime["a_content_type"]
        charset = _charset(content_type)
        entry: dict[str, Any] = {
            "ArticleID": aid,
            "TicketID": ticket_id,
            "ArticleNumber": number,
            "CommunicationChannelID": meta["communication_channel_id"],
            "SenderTypeID": meta["article_sender_type_id"],
            "IsVisibleForCustomer": meta["is_visible_for_customer"],
            "CreateBy": meta["create_by"],
            "CreateTime": znuny_ts(meta["create_time"]),
            "ChangeBy": meta["change_by"],
            "ChangeTime": znuny_ts(meta["change_time"]),
            "From": _strip_newlines(mime["a_from"]),
            "ReplyTo": mime["a_reply_to"],
            "To": _strip_newlines(mime["a_to"]),
            "Cc": _strip_newlines(mime["a_cc"]),
            "Bcc": _strip_newlines(mime["a_bcc"]),
            "Subject": _strip_newlines(mime["a_subject"]),
            "MessageID": mime["a_message_id"],
            "InReplyTo": mime["a_in_reply_to"],
            "References": mime["a_references"],
            "ContentType": content_type,
            "Body": mime["a_body"],
            "IncomingTime": mime["incoming_time"],
            "SenderType": sender_names.get(int(meta["article_sender_type_id"])),
            "CommunicationChannel": channel_names.get(int(meta["communication_channel_id"])),
            "Charset": charset,
            "ContentCharset": charset,
            "MimeType": _mime_type(content_type),
            "TimeUnit": znuny_num(time_units.get(aid, Decimal(0))),
        }
        if df_fields:
            entry["DynamicField"] = [
                {
                    "Name": f.name,
                    "Value": _df_value(f, df_values[(f.id, aid)])
                    if (f.id, aid) in df_values
                    else None,
                }
                for f in df_fields
            ]
        if attachments:
            files = _attachment_entries(
                atts_by_article.get(aid, []),
                with_contents=get_attachment_contents,
                html_body_as_attachment=html_body_as_attachment,
            )
            if files:
                entry["Attachment"] = files
        out.append(entry)
    return out
