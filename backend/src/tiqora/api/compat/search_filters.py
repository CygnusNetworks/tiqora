"""TicketSearch filters for the GenericInterface compat layer (Created*, time families).

Ports ``Kernel::GenericInterface::Operation::Ticket::TicketSearch`` (``_GetParams``,
``_CreateTimeSettings``) and the matching blocks of ``Kernel::System::Ticket::TicketSearch``.

Column each family filters on (Znuny 6.5):

=============================================  ==========================================
Family                                         Column
=============================================  ==========================================
Created{Types,States,Queues,Priorities,        one ``ticket_history`` row of type
UserIDs} (+ ``*IDs``)                          ``NewTicket`` (type_id / state_id / queue_id /
                                               priority_id / create_by) - Znuny's ``th0``
TicketCreateTime*                              ``ticket.create_time`` (datetime)
TicketEscalation{,Update,Response,Solution}    ``ticket.escalation*_time`` (epoch, ``0``
Time*                                          means "no escalation" and is excluded)
TicketChangeTime*                              ``ticket_history.create_time`` of ANY history
                                               row of the ticket (Znuny ``th1``)
TicketLastChangeTime*                          ``ticket.change_time``
TicketCloseTime*                               ``ticket_history.create_time`` of a
                                               NewTicket/StateUpdate row whose ``state_id``
                                               is a valid ``closed``-type state (``th2``)
TicketPendingTime*                             ``ticket.until_time`` (epoch), restricted to
                                               tickets in ``pending reminder`` /
                                               ``pending auto`` states
=============================================  ==========================================

Deviations / decisions:

* Every Newer/Older bound of the history families gets its OWN history join (controller
  ruling); Znuny shares one alias per family, so both bounds must hit the same row there.
* ``TimePoint`` / ``TimeSlot`` forms exist in Znuny's GI operation only for
  ``ChangeTimeSearchType``, ``LastChangeTimeSearchType`` and ``CloseTimeSearchType``;
  there is no create-time form (``TimeSearchType`` is read and ignored), so none is added.
* Znuny aborts a search with an invalid date string or an impossible window (Newer after
  now / after Older) and the operation answers with an empty ``Data`` -> ``time_filters``
  returns ``False`` and the caller answers ``{}``.
* ``TicketLastCloseTime*`` is not on the GI whitelist (``_GetParams``) and is not supported.
* Every id filter (TicketID, QueueIDs, StateIDs, ... Created*IDs) is range-checked against
  its column width (``sanitize_id_filters``): an id that does not fit can never match, so it
  is dropped and never bound (asyncpg raises on out-of-range parameters instead of matching
  nothing). A list without any usable id is "no match" -> ``{}``.
* Article text filters (``article_text_filters``): Znuny answers ``MIMEBase_*`` / ``Fulltext``
  from ``article_search_index`` (tokenised, stop words, one row per word); Tiqora keeps
  ``LOWER(article_data_mime.<col>) LIKE`` instead - a known approximation (no stop words,
  no tokenisation, substring on the raw text). ``Fulltext`` also looks at the ticket title
  like Znuny's ``ArticleSearchIndex::DB``. The GI sets ``ContentSearchPrefix/Suffix`` to
  ``*``, so every value is wrapped (``Rech*`` is also a substring match), ``*`` -> ``%``,
  ``%%`` collapses to ``%``; a value of only wildcards (any length) is skipped.
  ``ContentSearch`` ``OR`` joins the conditions with OR (default AND; ``Fulltext`` forces OR
  like the GI operation). ``MIMEBase_AttachmentName`` exists only when SysConfig
  ``Ticket::Article::Backend::MIMEBase::IndexAttachmentNames`` is truthy.
* A non-integer element in any id filter makes the whole search empty (Znuny logs
  "The given param ... is invalid!" and returns nothing).
* Pure SQL, no string JOINs between ``tiqora_*`` and Znuny tables.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.sysconfig import SysConfig

_DATE_RE = re.compile(r"\A(\d{4})-(\d{1,2})-(\d{1,2}) (\d{1,2}):(\d{1,2}):(\d{1,2})\Z")

_POINT_MINUTES = {
    "minute": 1,
    "hour": 60,
    "day": 60 * 24,
    "week": 60 * 24 * 7,
    "month": 60 * 24 * 30,
    "year": 60 * 24 * 365,
}


def _perl_true(value: Any) -> bool:
    """Perl truthiness of a scalar (``""``, ``"0"``, ``0`` and undef are false)."""
    if value is None:
        return False
    if isinstance(value, (dict, list, tuple)):
        return bool(value)
    return str(value) not in ("", "0")


def _scalar(data: Mapping[str, Any], key: str) -> str | None:
    """``IsStringWithData``: a non-empty scalar, whitespace stripped (as ``_GetParams``)."""
    value = data.get(key)
    if value is None or isinstance(value, (dict, list, tuple)):
        return None
    out = str(value).strip()
    return out or None


def _number(value: str) -> float:
    """Perl numification: a leading number, otherwise 0."""
    match = re.match(r"\s*[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?", value)
    return float(match.group(0)) if match else 0.0


def _int_or_float(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _two_digits(value: Any) -> str:
    """``sprintf("%02d", $v)`` for undef / numeric strings."""
    return f"{int(_number(str(value))) if value is not None else 0:02d}"


def create_time_settings(data: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    """Port of ``_CreateTimeSettings``: TimeSlot/TimePoint forms -> Newer/Older keys.

    ``now`` is accepted for interface symmetry; Znuny does not use it here.
    Returns a copy of ``data`` with ``Ticket{Change,LastChange,Close}Time{Newer,Older}
    {Date,Minutes}`` added where the form asks for them.
    """
    del now
    out = dict(data)
    for search_type_key, prefix in (
        ("ChangeTimeSearchType", "TicketChangeTime"),
        ("LastChangeTimeSearchType", "TicketLastChangeTime"),
        ("CloseTimeSearchType", "TicketCloseTime"),
    ):
        search_type = out.get(search_type_key)
        if not _perl_true(search_type):
            continue
        if search_type == "TimeSlot":
            for edge in ("Start", "Stop"):
                for part in ("Month", "Day"):
                    out[f"{prefix}{edge}{part}"] = _two_digits(out.get(f"{prefix}{edge}{part}"))
            for edge, key, clock in (
                ("Start", "NewerDate", "00:00:00"),
                ("Stop", "OlderDate", "23:59:59"),
            ):
                if (
                    _perl_true(out.get(f"{prefix}{edge}Day"))
                    and _perl_true(out.get(f"{prefix}{edge}Month"))
                    and _perl_true(out.get(f"{prefix}{edge}Year"))
                ):
                    out[f"{prefix}{key}"] = (
                        f"{out[f'{prefix}{edge}Year']}-{out[f'{prefix}{edge}Month']}"
                        f"-{out[f'{prefix}{edge}Day']} {clock}"
                    )
        elif search_type == "TimePoint":
            point = out.get(f"{prefix}Point")
            start = out.get(f"{prefix}PointStart")
            fmt = out.get(f"{prefix}PointFormat")
            if _perl_true(point) and _perl_true(start) and _perl_true(fmt):
                minutes = _int_or_float(_number(str(point)) * _POINT_MINUTES.get(str(fmt), 0))
                key = "OlderMinutes" if start == "Before" else "NewerMinutes"
                out[f"{prefix}{key}"] = minutes
    return out


def _parse_date(value: str) -> datetime | None:
    match = _DATE_RE.match(value)
    if not match:
        return None
    try:
        year, month, day, hour, minute, second = (int(g) for g in match.groups())
        return datetime(year, month, day, hour, minute, second)  # noqa: DTZ001 - naive UTC
    except ValueError:
        return None


def _epoch(moment: datetime) -> int:
    return int(moment.replace(tzinfo=UTC).timestamp())


_CLOSE_HISTORY = (
    "{a}.history_type_id IN (SELECT id FROM ticket_history_type"
    " WHERE name IN ('NewTicket', 'StateUpdate'))"
    " AND {a}.state_id IN (SELECT ts.id FROM ticket_state ts"
    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
    " WHERE tst.name = 'closed' AND ts.valid_id = 1)"
)

_PENDING_STATES = (
    "t.ticket_state_id IN (SELECT ts.id FROM ticket_state ts"
    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
    " WHERE tst.name IN ('pending reminder', 'pending auto') AND ts.valid_id = 1)"
)

# families compared directly against a ticket column
_DIRECT_FAMILIES: tuple[tuple[str, str, bool, bool], ...] = (
    # prefix, column, is_epoch, escalation (exclude 0 = no escalation)
    ("TicketCreateTime", "t.create_time", False, False),
    ("TicketEscalationTime", "t.escalation_time", True, True),
    ("TicketEscalationUpdateTime", "t.escalation_update_time", True, True),
    ("TicketEscalationResponseTime", "t.escalation_response_time", True, True),
    ("TicketEscalationSolutionTime", "t.escalation_solution_time", True, True),
)


def time_filters(
    data: Mapping[str, Any],
    conditions: list[str],
    params: dict[str, Any],
    joins: list[str],
    *,
    now: datetime,
) -> bool:
    """Add the ticket time-window filters; ``False`` means "answer with an empty result".

    ``now`` is a naive UTC datetime (the DB stores naive UTC).
    """
    counter = 0

    def bind(value: Any) -> str:
        nonlocal counter
        counter += 1
        name = f"tw{counter}"
        params[name] = value
        return f":{name}"

    # --- create time + escalation times: compared in place -----------------------
    for prefix, column, is_epoch, escalation in _DIRECT_FAMILIES:

        def target(moment: datetime, is_epoch: bool = is_epoch) -> Any:
            return _epoch(moment) if is_epoch else moment

        for key, op in (("OlderMinutes", "<="), ("NewerMinutes", ">=")):
            raw = _scalar(data, f"{prefix}{key}")
            if raw is None:
                continue
            if escalation:
                conditions.append(f"{column} != 0")
            moment = now - timedelta(minutes=int(_number(raw)))
            conditions.append(f"{column} {op} {bind(target(moment))}")

        older_date: datetime | None = None
        raw = _scalar(data, f"{prefix}OlderDate")
        if _perl_true(raw):
            assert raw is not None
            older_date = _parse_date(raw)
            if older_date is None:
                return False
            if escalation:
                conditions.append(f"{column} != 0")
            conditions.append(f"{column} <= {bind(target(older_date))}")
        raw = _scalar(data, f"{prefix}NewerDate")
        if _perl_true(raw):
            assert raw is not None
            newer_date = _parse_date(raw)
            if newer_date is None or newer_date > now:
                return False
            if older_date is not None and newer_date > older_date:
                return False
            if escalation:
                conditions.append(f"{column} != 0")
            conditions.append(f"{column} >= {bind(target(newer_date))}")

    # --- change / last change / close / pending: minutes become dates first -------
    history_alias = 0

    def history_join() -> str:
        nonlocal history_alias
        history_alias += 1
        alias = f"thw{history_alias}"
        joins.append(f"JOIN ticket_history {alias} ON {alias}.ticket_id = t.id")
        return alias

    for prefix, kind in (
        ("TicketChangeTime", "change"),
        ("TicketLastChangeTime", "last_change"),
        ("TicketCloseTime", "close"),
        ("TicketPendingTime", "pending"),
    ):
        dates: dict[str, str | None] = {}
        for bound in ("Older", "Newer"):
            minutes = _scalar(data, f"{prefix}{bound}Minutes")
            if minutes is not None:
                dates[bound] = (now - timedelta(minutes=int(_number(minutes)))).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            else:
                dates[bound] = _scalar(data, f"{prefix}{bound}Date")

        if kind == "pending" and (
            _scalar(data, f"{prefix}OlderMinutes") is not None
            or _scalar(data, f"{prefix}NewerMinutes") is not None
            or _perl_true(dates["Older"])
            or _perl_true(dates["Newer"])
        ):
            conditions.append(_PENDING_STATES)

        def emit(moment: datetime, op: str, kind: str = kind) -> None:
            if kind == "last_change":
                conditions.append(f"t.change_time {op} {bind(moment)}")
            elif kind == "pending":
                conditions.append(f"t.until_time {op} {bind(_epoch(moment))}")
            else:
                alias = history_join()
                if kind == "close":
                    conditions.append(_CLOSE_HISTORY.format(a=alias))
                conditions.append(f"{alias}.create_time {op} {bind(moment)}")

        older: datetime | None = None
        if _perl_true(dates["Older"]):
            assert dates["Older"] is not None
            older = _parse_date(dates["Older"])
            if older is None:
                return False
            emit(older, "<=")
        if _perl_true(dates["Newer"]):
            assert dates["Newer"] is not None
            newer = _parse_date(dates["Newer"])
            if newer is None:
                return False
            # Znuny checks "newer after now" for every family except pending
            if kind != "pending" and newer > now:
                return False
            if older is not None and newer > older:
                return False
            emit(newer, ">=")
    return True


async def created_filters(
    session: AsyncSession,
    data: Mapping[str, Any],
    conditions: list[str],
    params: dict[str, Any],
) -> None:
    """Created* filters: values of the ticket's ``NewTicket`` history row.

    All Created* arguments constrain the SAME history row (Znuny ``th0``), so they are
    combined in one ``EXISTS``. A name that does not exist makes Znuny abort the search
    (empty result) - a never-true condition is added in that case.
    """
    specs: tuple[tuple[str, str, str, str], ...] = (
        # names key, ids key, history column, lookup table
        ("CreatedTypes", "CreatedTypeIDs", "type_id", "ticket_type"),
        ("CreatedStates", "CreatedStateIDs", "state_id", "ticket_state"),
        ("CreatedQueues", "CreatedQueueIDs", "queue_id", "queue"),
        ("CreatedPriorities", "CreatedPriorityIDs", "priority_id", "ticket_priority"),
        ("CreatedUserIDs", "", "create_by", ""),
    )
    parts: list[str] = []
    for names_key, ids_key, column, table in specs:
        ids: list[int] = []
        names = _as_list(data.get(names_key)) if table else []
        raw_ids = _as_list(data.get(ids_key if table else names_key))
        for name in names:
            row = (
                await session.execute(
                    text(f"SELECT id FROM {table} WHERE name = :n"),  # noqa: S608 - fixed table
                    {"n": str(name)},
                )
            ).first()
            if row is None:
                conditions.append("1 = 0")
                return
            ids.append(int(row[0]))
        for raw in raw_ids:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                conditions.append("1 = 0")
                return
        if not ids:
            continue
        holders = []
        for value in ids:
            name = f"cr{len(params)}"
            params[name] = value
            holders.append(f":{name}")
        parts.append(f"th0.{column} IN ({', '.join(holders)})")
    if not parts:
        return
    conditions.append(
        "EXISTS (SELECT 1 FROM ticket_history th0 WHERE th0.ticket_id = t.id"
        " AND th0.history_type_id = (SELECT id FROM ticket_history_type WHERE name = 'NewTicket')"
        " AND " + " AND ".join(parts) + ")"
    )


SMALLINT_MAX = 2**15 - 1
INT_MAX = 2**31 - 1
BIGINT_MAX = 2**63 - 1

# id filter -> (column width (signed max), alternative name filter or None)
# widths follow the Znuny schema (ticket / ticket_history / ticket_watcher)
_ID_FILTERS: dict[str, tuple[int, str | None]] = {
    "TicketID": (BIGINT_MAX, None),
    "QueueIDs": (INT_MAX, "Queues"),
    "StateIDs": (SMALLINT_MAX, "States"),
    "StateTypeIDs": (SMALLINT_MAX, None),
    "PriorityIDs": (SMALLINT_MAX, "Priorities"),
    "TypeIDs": (SMALLINT_MAX, "Types"),
    "ServiceIDs": (INT_MAX, "Services"),
    "SLAIDs": (INT_MAX, "SLAs"),
    "LockIDs": (SMALLINT_MAX, "Locks"),
    "OwnerIDs": (INT_MAX, None),
    "ResponsibleIDs": (INT_MAX, None),
    "WatchUserIDs": (INT_MAX, None),
    "CreatedUserIDs": (INT_MAX, None),
    "CreatedQueueIDs": (INT_MAX, "CreatedQueues"),
    "CreatedStateIDs": (SMALLINT_MAX, "CreatedStates"),
    "CreatedPriorityIDs": (SMALLINT_MAX, "CreatedPriorities"),
    "CreatedTypeIDs": (SMALLINT_MAX, "CreatedTypes"),
}


_INTEGER_RE = re.compile(r"[+-]?\d+")


def bounded_ids(raw: Any, max_value: int) -> list[int]:
    """Ids of ``raw`` (scalar or list) that fit a signed column of ``max_value``.

    Non-numeric and out-of-range values can never match a row, so they are dropped and
    never bound (asyncpg raises on out-of-range parameters instead of matching nothing).
    """
    out: list[int] = []
    for item in _as_list(raw):
        try:
            number = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if -max_value - 1 <= number <= max_value:
            out.append(number)
    return out


def sanitize_id_filters(data: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Range-check every id filter; returns ``(clean data, no_match)``.

    An id list that was given but has no usable id is "no match" (``True``) unless the
    same filter also has a name alternative (``Queues`` next to ``QueueIDs``): Znuny merges
    both, so the names can still match.
    """
    out = dict(data)
    for key, (limit, alternative) in _ID_FILTERS.items():
        raw = data.get(key)
        if not _as_list(raw):
            continue
        if any(not _INTEGER_RE.fullmatch(str(item)) for item in _as_list(raw)):
            return out, True
        ids = bounded_ids(raw, limit)
        out[key] = ids
        if not ids and not (alternative and _as_list(data.get(alternative))):
            return out, True
    return out, False


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v is not None and v != ""]
    return [value]


_MIME_COLUMNS = (
    ("MIMEBase_From", "a_from"),
    ("MIMEBase_To", "a_to"),
    ("MIMEBase_Cc", "a_cc"),
    ("MIMEBase_Bcc", "a_bcc"),
    ("MIMEBase_Subject", "a_subject"),
    ("MIMEBase_Body", "a_body"),
)
_INDEX_ATTACHMENT_NAMES = "Ticket::Article::Backend::MIMEBase::IndexAttachmentNames"


def content_like_pattern(value: Any) -> str | None:
    """LIKE pattern Znuny builds for an article text value, ``None`` = skip the filter.

    Port of ``ArticleSearchIndex::DB::ArticleSearchIndexWhereCondition`` with the GI's
    ``ContentSearchPrefix/Suffix`` ``*``: ``*`` -> ``%``, wildcard-only values are skipped,
    the value is wrapped in ``%``, ``%%`` collapses to ``%`` and the match is lower-cased.
    """
    if value is None or isinstance(value, (dict, list, tuple)):
        return None
    text_value = str(value).strip().replace("*", "%")
    if not text_value or re.fullmatch(r"%+", text_value):
        return None
    return ("%" + text_value + "%").replace("%%", "%").lower()


async def article_text_filters(
    session: AsyncSession,
    sysconfig: SysConfig,
    data: Mapping[str, Any],
    conditions: list[str],
    params: dict[str, Any],
    add_join: Callable[[str, str], None],
) -> None:
    """Append the ``MIMEBase_*`` / ``Fulltext`` / ``ContentSearch`` filters.

    ``session`` is unused (the SysConfig reader carries its own); kept for the interface.
    Adds one parenthesised condition to ``conditions`` and its ``:mfN`` binds to
    ``params``; the article joins go through ``add_join(sql, key)``.
    """
    del session
    wanted: list[tuple[str, str]] = []  # (kind, pattern); kind = column or special
    for field, col in _MIME_COLUMNS:
        if (pat := content_like_pattern(data.get(field))) is not None:
            wanted.append((col, pat))
    index_names = _perl_true(await sysconfig.get(_INDEX_ATTACHMENT_NAMES, 0))
    if index_names and (pat := content_like_pattern(data.get("MIMEBase_AttachmentName"))):
        wanted.append(("__attachment__", pat))
    fulltext = data.get("Fulltext")
    fulltext_pat = content_like_pattern(fulltext) if _perl_true(fulltext) else None
    if fulltext_pat is not None:
        wanted.append(("__fulltext__", fulltext_pat))
    if not wanted:
        return

    add_join(
        "JOIN article a ON a.ticket_id = t.id JOIN article_data_mime adm ON adm.article_id = a.id",
        "mime",
    )
    attachment = (
        "EXISTS (SELECT 1 FROM article_data_mime_attachment ama"
        " WHERE ama.article_id = adm.article_id AND LOWER(ama.filename) LIKE :mf{i})"
    )
    parts: list[str] = []
    for i, (col, pat) in enumerate(wanted):
        if col == "__attachment__":
            parts.append(attachment.format(i=i))
        elif col == "__fulltext__":
            ors = [f"LOWER(adm.{c}) LIKE :mf{i}" for _, c in _MIME_COLUMNS]
            ors.append(f"LOWER(t.title) LIKE :mf{i}")
            if index_names:
                ors.append(attachment.format(i=i))
            parts.append("(" + " OR ".join(ors) + ")")
        else:
            parts.append(f"LOWER(adm.{col}) LIKE :mf{i}")
        params[f"mf{i}"] = pat
    joiner = (
        " OR "
        if fulltext_pat is not None or str(data.get("ContentSearch", "")).strip().upper() == "OR"
        else " AND "
    )
    conditions.append("(" + joiner.join(parts) + ")")
