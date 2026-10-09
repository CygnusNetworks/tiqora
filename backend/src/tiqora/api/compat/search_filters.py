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

* The history families are one ``EXISTS`` subquery each (``TicketChangeTime*`` and
  ``TicketCloseTime*`` separately): every Newer/Older bound of a family applies to the SAME
  ``ticket_history`` row, exactly like Znuny's single alias per family (``th1`` / ``th2`` in
  ``_TicketHistoryReferenceForSearchArgument``). "Changed between 7 and 3 days ago" therefore
  needs one history row inside the window, and no history row is joined (no N^2 rows).
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
  like the GI operation, also when the ``Fulltext`` value itself is wildcard-only and
  skipped). ``_`` is a literal, not a LIKE wildcard (Znuny ``Quote($Value, 'Like')``).
  ``MIMEBase_AttachmentName`` exists only when SysConfig
  ``Ticket::Article::Backend::MIMEBase::IndexAttachmentNames`` is truthy.
* A non-integer element in any id filter makes the whole search empty (Znuny logs
  "The given param ... is invalid!" and returns nothing); an element of more than 16 digits
  is not an integer for Znuny (``Quote($Element, 'Integer')``) and empties it too.
* Pure SQL, no string JOINs between ``tiqora_*`` and Znuny tables.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.mapping.perl import perl_truthy
from tiqora.znuny.perl_format import perl_int, perl_num
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


def _scalar(data: Mapping[str, Any], key: str) -> str | None:
    """``IsStringWithData``: a non-empty scalar, whitespace stripped (as ``_GetParams``)."""
    value = data.get(key)
    if value is None or isinstance(value, (dict, list, tuple)):
        return None
    out = str(value).strip()
    return out or None


def _number(value: str) -> float:
    """Perl numification: a leading number, otherwise 0."""
    return float(perl_num(value))


def _int_or_float(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _two_digits(value: Any) -> str:
    """``sprintf("%02d", $v)`` for undef / numeric strings."""
    return f"{perl_int(str(value)) if value is not None else 0:02d}"


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
        if not perl_truthy(search_type):
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
                    perl_truthy(out.get(f"{prefix}{edge}Day"))
                    and perl_truthy(out.get(f"{prefix}{edge}Month"))
                    and perl_truthy(out.get(f"{prefix}{edge}Year"))
                ):
                    out[f"{prefix}{key}"] = (
                        f"{out[f'{prefix}{edge}Year']}-{out[f'{prefix}{edge}Month']}"
                        f"-{out[f'{prefix}{edge}Day']} {clock}"
                    )
        elif search_type == "TimePoint":
            point = out.get(f"{prefix}Point")
            start = out.get(f"{prefix}PointStart")
            fmt = out.get(f"{prefix}PointFormat")
            if perl_truthy(point) and perl_truthy(start) and perl_truthy(fmt):
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


_INT32_MIN, _INT32_MAX = -(2**31), 2**31 - 1


def _epoch(moment: datetime) -> int:
    # the epoch columns (escalation_*_time, until_time) are 32-bit INTEGER in Znuny's schema
    return max(_INT32_MIN, min(_INT32_MAX, int(moment.replace(tzinfo=UTC).timestamp())))


_FAR_PAST = datetime.min  # noqa: DTZ901 - naive UTC like every bound here
_FAR_FUTURE = datetime.max.replace(microsecond=0)  # noqa: DTZ901


# CPAN DateTime keeps the seconds of a duration in a Perl integer: past 2**63 they wrap, past
# 2**64 (unsigned) they collapse to -1
_IV_MAX = 2**63 - 1
_UV_LIMIT = 2**64


def minutes_before(now: datetime, minutes: Any) -> datetime | None:
    """``now`` minus ``minutes`` (Perl-numified), as Znuny's ``DateTime->Subtract`` computes it.

    Exact while the result is a date of the years 1-9999. Beyond that, CPAN DateTime (1.52)
    does not fail; it produces dates outside the ``YYYY-`` format:

    * a huge positive value: a far-past date (negative years, e.g. ``-292277022601-11-04``);
    * a negative value: a far-future date (5+ digit years) while ``-minutes * 60`` fits a
      signed 64-bit integer, a far-past one while it fits an unsigned one, and beyond that
      (``"-1e300"``) the seconds collapse to -1: the start of today minus one second, a
      normal date.

    Returns that "start of today minus one second" date, or ``None`` for the far-past /
    far-future results (see :func:`clamped_minutes_before`).
    """
    number = perl_int(minutes)
    try:
        return now - timedelta(minutes=number)
    except OverflowError:
        pass
    if number < 0 and -number * 60 >= _UV_LIMIT:
        return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
    return None


def clamped_minutes_before(now: datetime, minutes: Any) -> datetime:
    """:func:`minutes_before` for direct comparisons: Perl's far-past result becomes the
    earliest date, its far-future result the latest (``create_time <= far past`` matches
    nothing, ``>= far past`` everything). Znuny puts its out-of-range date string into the SQL
    as it is, so what it selects there depends on the database."""
    moment = minutes_before(now, minutes)
    if moment is not None:
        return moment
    number = perl_int(minutes)
    if number > 0 or -number * 60 > _IV_MAX:
        return _FAR_PAST
    return _FAR_FUTURE


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

    ``now`` is a naive UTC datetime (the DB stores naive UTC). ``joins`` is kept for the
    interface: the history families are ``EXISTS`` subqueries now, so nothing is joined.
    """
    del joins
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
            moment = clamped_minutes_before(now, raw)
            conditions.append(f"{column} {op} {bind(target(moment))}")

        older_date: datetime | None = None
        raw = _scalar(data, f"{prefix}OlderDate")
        if perl_truthy(raw):
            assert raw is not None
            older_date = _parse_date(raw)
            if older_date is None:
                return False
            if escalation:
                conditions.append(f"{column} != 0")
            conditions.append(f"{column} <= {bind(target(older_date))}")
        raw = _scalar(data, f"{prefix}NewerDate")
        if perl_truthy(raw):
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
                bound_moment = minutes_before(now, minutes)
                if bound_moment is None:
                    # Znuny formats the out-of-range date ("-292277022601-..." or a 5-digit
                    # year) and refuses it: "Invalid time format" -> TicketSearch returns nothing
                    return False
                # zero-padded year like Perl's ymd (strftime's %Y does not pad on every libc)
                dates[bound] = bound_moment.isoformat(sep=" ", timespec="seconds")
            else:
                dates[bound] = _scalar(data, f"{prefix}{bound}Date")

        if kind == "pending" and (
            _scalar(data, f"{prefix}OlderMinutes") is not None
            or _scalar(data, f"{prefix}NewerMinutes") is not None
            or perl_truthy(dates["Older"])
            or perl_truthy(dates["Newer"])
        ):
            conditions.append(_PENDING_STATES)

        # Change / Close: every bound constrains the SAME history row (Znuny maps all
        # TicketChangeTime* to alias th1 and all TicketCloseTime* to th2), collected here and
        # emitted as one EXISTS per family below.
        history_bounds: list[tuple[datetime, str]] = []

        def emit(
            moment: datetime,
            op: str,
            kind: str = kind,
            history_bounds: list[tuple[datetime, str]] = history_bounds,
        ) -> None:
            if kind == "last_change":
                conditions.append(f"t.change_time {op} {bind(moment)}")
            elif kind == "pending":
                conditions.append(f"t.until_time {op} {bind(_epoch(moment))}")
            else:
                history_bounds.append((moment, op))

        older: datetime | None = None
        if perl_truthy(dates["Older"]):
            assert dates["Older"] is not None
            older = _parse_date(dates["Older"])
            if older is None:
                return False
            emit(older, "<=")
        if perl_truthy(dates["Newer"]):
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

        if history_bounds:
            history_alias += 1
            alias = f"thw{history_alias}"
            parts = [f"{alias}.ticket_id = t.id"]
            if kind == "close":
                parts.append(_CLOSE_HISTORY.format(a=alias))
            parts.extend(
                f"{alias}.create_time {op} {bind(moment)}" for moment, op in history_bounds
            )
            conditions.append(
                f"EXISTS (SELECT 1 FROM ticket_history {alias} WHERE {' AND '.join(parts)})"
            )
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


# Znuny ``Quote($Element, 'Integer')``: ``\A [+-]? \d{1,16} \z`` - a longer id is invalid
_INTEGER_RE = re.compile(r"[+-]?\d{1,16}")


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
    pattern = ("%" + text_value + "%").replace("%%", "%").lower()
    # Quote($Value, 'Like'): '_' is a literal (backslash is the LIKE escape on both dialects)
    return pattern.replace("\\", "\\\\").replace("_", "\\_")


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
    index_names = perl_truthy(await sysconfig.get(_INDEX_ATTACHMENT_NAMES, 0))
    if index_names and (pat := content_like_pattern(data.get("MIMEBase_AttachmentName"))):
        wanted.append(("__attachment__", pat))
    fulltext = data.get("Fulltext")
    # GI TicketSearch.pm: `if ($GetParam{Fulltext}) { ContentSearch = 'OR' }` - also when the
    # value itself is wildcard-only and skipped
    fulltext_or = perl_truthy(fulltext)
    fulltext_pat = content_like_pattern(fulltext) if fulltext_or else None
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
        if fulltext_or or str(data.get("ContentSearch", "")).strip().upper() == "OR"
        else " AND "
    )
    conditions.append("(" + joiner.join(parts) + ")")
