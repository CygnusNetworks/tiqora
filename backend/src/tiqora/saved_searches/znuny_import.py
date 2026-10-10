"""Pure mapping of Znuny ticket-search profiles to Tiqora filter criteria (design D9).

No database access: the caller (``znuny_reader``) loads the profile values and the
lookups. Everything that cannot be taken over is reported in ``MappedProfile.unmapped``
(one :class:`UnmappedItem` per Znuny key) so callers can flag a profile as only partially
imported; nothing is silently dropped or invented. Keys that Znuny stores for every form
field but that carry no value are skipped silently, UI-only keys are ignored.

"Reported key" (``UnmappedItem.key``) rules for time searches:

* a future time point (``Next``/``After``), an invalid amount/direction/unit of a time
  point: the *amount* key (``TicketPendingTimePoint``; for dynamic fields the
  ``Search_DynamicField_<Name>TimePoint`` flag key);
* a time slot with an invalid or incomplete date: the ``…SearchType`` key (for dynamic
  fields the ``…TimeSlot`` flag key);
* a time field Tiqora has no counterpart for (close, last change, escalation, article
  time): the ``…SearchType`` key; its sub-keys are consumed and not reported again.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tiqora.filters.model import AllOf, AnyOf, Condition, ConditionOp, Filter, FilterNode
from tiqora.filters.tickets import _DF_TYPES  # the DF types the filter catalogue supports
from tiqora.util.perl import perl_truthy

FULLTEXT_REASON = (
    "Volltextsuche wird nicht in gespeicherte Suchen übernommen – bitte die Suche verwenden."
)
UNSUPPORTED_REASON = "Wird von Tiqora nicht unterstützt."
OPERATOR_REASON = "Suchoperatoren werden nicht übernommen."
FUTURE_REASON = "Zeitpunkte in der Zukunft werden nicht übernommen."
NO_TIME_FIELD_REASON = "Dieses Zeitfeld gibt es in der Tiqora-Suche nicht."
UNKNOWN_DF_REASON = "Dynamisches Feld unbekannt."
DF_TYPE_REASON = "Dieser Typ von dynamischem Feld wird von Tiqora nicht unterstützt."
INVALID_VALUE_REASON = "Kein gültiger Wert."
INVALID_DATE_REASON = "Kein gültiges Datum."
STATE_TYPE_REASON = "Statustyp unbekannt."
LOCK_REASON = "Sperrstatus unbekannt."
ARCHIVE_REASON = "Die Archivsuche ist nur für Administratoren möglich."

#: Keys of the search form that are not criteria; ignored silently.
IGNORED_KEYS = frozenset(
    {
        "ShownAttributes",
        "ResultForm",
        "Profile",
        "SaveProfile",
        "SelectTemplate",
        "EmptySearch",
        "TakeLastSearch",
        "SearchTemplate",
        "Subaction",
        "Action",
        "ProfileName",
    }
)

_FULLTEXT_KEYS = frozenset({"Fulltext", "From", "To", "Cc", "Subject", "Body", "AttachmentName"})
_ID_ROWS: tuple[tuple[str, str], ...] = (
    ("StateIDs", "state_id"),
    ("PriorityIDs", "priority_id"),
    ("TypeIDs", "type_id"),
    ("ServiceIDs", "service_id"),
    ("SLAIDs", "sla_id"),
    ("OwnerIDs", "owner_id"),
    ("ResponsibleIDs", "responsible_id"),
)
#: (condition field, search-type key, key prefix) of the time fields Tiqora can filter.
_TIME_ROWS: tuple[tuple[str, str, str], ...] = (
    ("created", "TimeSearchType", "TicketCreate"),
    ("changed", "ChangeTimeSearchType", "TicketChange"),
    ("pending_until", "PendingTimeSearchType", "TicketPending"),
)
#: (search-type key, key prefix) of the time fields without a Tiqora counterpart.
_NO_TIME_ROWS: tuple[tuple[str, str], ...] = (
    ("CloseTimeSearchType", "TicketClose"),
    ("LastChangeTimeSearchType", "TicketLastChange"),
    ("EscalationTimeSearchType", "TicketEscalation"),
    ("ArticleTimeSearchType", "ArticleCreate"),
)
_DF_KEY_PREFIX = "Search_DynamicField_"
_PARTS = ("Year", "Month", "Day", "Hour", "Minute", "Second")
_DIRECTIONS = {"last": "Last", "before": "Before", "next": "Next", "after": "After"}
_UNITS: dict[str, tuple[str, int]] = {
    "minute": ("minute", 1),
    "hour": ("hour", 1),
    "day": ("day", 1),
    "week": ("week", 1),
    "month": ("day", 30),  # AgentTicketSearch.pm: month = 30 days
    "year": ("day", 365),  # year = 365 days
}
_OPERATOR_TOKENS = ("||", "&&", "!", "(", ")", '"', " + ", " - ")
_SHOWN_LIMIT = 200
_NO_SEARCH = frozenset({"", "None"})
_DF_SUFFIX = re.compile(
    r"TimePoint|TimePointValue|TimePointFormat|TimePointStart|TimeSlot"
    r"|TimeSlot(?:Start|Stop)(?:Year|Month|Day|Hour|Minute|Second)"
)


@dataclass(frozen=True, slots=True)
class ZnunyLookups:
    state_type_names: Mapping[int, str]  # ticket_state_type.id -> name
    queue_names: Mapping[int, str]  # queue.id -> full name ("Parent::Child"), valid queues
    lock_names: Mapping[int, str]  # ticket_lock_type.id -> name (unlock | lock | tmp_lock)
    dynamic_fields: Mapping[str, str]  # valid ticket dynamic field name -> field_type
    time_zone: str  # the importing agent's zone (TimeSlot dates are local)


@dataclass(frozen=True, slots=True)
class UnmappedItem:
    key: str
    value: str  # shortened to 200 characters
    reason: str  # German


@dataclass(frozen=True, slots=True)
class MappedProfile:
    name: str
    criteria: Filter  # where is None when nothing mapped
    include_archived: bool
    unmapped: tuple[UnmappedItem, ...]


def _cond(field: str, op: ConditionOp, value: Any = None) -> Condition:
    return Condition(field=field, op=op, value=value)


def _is_uint(text: str) -> bool:
    return text.isascii() and text.isdigit()


def _has_operator(text: str) -> bool:
    return any(token in text for token in _OPERATOR_TOKENS)


def _pattern(field: str, value: str) -> Condition | None | str:
    """Pattern rule P. Returns a condition, ``None`` (match everything) or an error reason."""
    value = value.strip()
    if _has_operator(value):
        return OPERATOR_REASON
    if "*" not in value:
        return _cond(field, "eq", value)
    core = value.strip("*")
    if core == "":
        return None
    if "*" in core:
        return OPERATOR_REASON
    leading, trailing = value.startswith("*"), value.endswith("*")
    if trailing and not leading and value.count("*") == 1:
        return _cond(field, "starts_with", core)
    if leading and trailing and value.count("*") == 2:
        return _cond(field, "contains", core)
    return OPERATOR_REASON


def _time_point(field: str, amount: str, start: str, unit: str) -> Condition | str:
    """Rule T. Returns a condition or the reason it cannot be mapped."""
    direction = _DIRECTIONS.get(start.strip().lower())
    unit_spec = _UNITS.get(unit.strip().lower())
    amount = amount.strip()
    if direction is None or unit_spec is None or not _is_uint(amount) or int(amount) < 1:
        return INVALID_VALUE_REASON
    if direction in ("Next", "After"):
        return FUTURE_REASON
    unit_name, factor = unit_spec
    value = {"amount": int(amount) * factor, "unit": unit_name}
    return _cond(field, "within_last" if direction == "Last" else "older_than", value)


def _time_slot(
    field: str,
    start: Mapping[str, str],
    stop: Mapping[str, str],
    zone: ZoneInfo,
) -> Condition | None | str:
    """Local start/stop date parts -> ``between`` in naive UTC. ``None`` when both are blank."""
    ymd = ("Year", "Month", "Day")
    if not any(start.get(p) or stop.get(p) for p in ymd):
        return None
    bounds: list[str] = []
    for parts, defaults in ((start, (0, 0, 0)), (stop, (23, 59, 59))):
        if not all(_is_uint(parts.get(p, "")) for p in ymd):
            return INVALID_DATE_REASON
        hms = []
        for part, default in zip(_PARTS[3:], defaults, strict=True):
            raw = parts.get(part, "")
            if raw and not _is_uint(raw):
                return INVALID_DATE_REASON
            hms.append(int(raw) if raw else default)
        try:
            local = datetime(
                int(parts["Year"]),
                int(parts["Month"]),
                int(parts["Day"]),
                hms[0],
                hms[1],
                hms[2],
                tzinfo=zone,
            )
            bounds.append(local.astimezone(UTC).replace(tzinfo=None).isoformat())
        except (ValueError, OverflowError):
            return INVALID_DATE_REASON
    return _cond(field, "between", {"from": bounds[0], "to": bounds[1]})


def _combine(conds: list[FilterNode], *, any_of: bool = False) -> FilterNode | None:
    if not conds:
        return None
    if len(conds) == 1:
        return conds[0]
    return AnyOf(any=conds) if any_of else AllOf(all=conds)


def _show(raw: str | list[str] | None) -> str:
    text = ", ".join(raw) if isinstance(raw, list) else (raw or "")
    return text if len(text) <= _SHOWN_LIMIT else text[: _SHOWN_LIMIT - 1] + "…"


class _Mapper:
    def __init__(
        self,
        values: Mapping[str, str | list[str]],
        lookups: ZnunyLookups,
        is_admin: bool,
    ) -> None:
        self.values = values
        self.lookups = lookups
        self.is_admin = is_admin
        self.conds: list[FilterNode] = []
        self.unmapped: list[UnmappedItem] = []
        self.consumed: set[str] = set()
        self.include_archived = False
        try:
            self.zone = ZoneInfo(lookups.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            self.zone = ZoneInfo("UTC")

    # -- value access ---------------------------------------------------------

    def take(self, key: str) -> list[str]:
        """Consume ``key``; its non-empty stripped values (``[]`` when absent or blank)."""
        self.consumed.add(key)
        raw = self.values.get(key)
        if raw is None:
            return []
        items = raw if isinstance(raw, list) else [raw]
        return [s.strip() for s in items if isinstance(s, str) and s.strip()]

    def one(self, key: str) -> str:
        found = self.take(key)
        return found[0] if found else ""

    def reject(self, key: str, reason: str) -> None:
        self.unmapped.append(UnmappedItem(key, _show(self.values.get(key)), reason))

    def ints(self, key: str) -> list[int] | None:
        """Integer values of ``key``; rejects the whole key (``None``) on a non-integer."""
        found = self.take(key)
        if not all(_is_uint(v) for v in found):
            self.reject(key, INVALID_VALUE_REASON)
            return None
        return [int(v) for v in found]

    # -- rows -----------------------------------------------------------------

    def run(self) -> None:
        self.queues()
        for key, field in _ID_ROWS:
            ids = self.ints(key)
            if ids:
                self.conds.append(_cond(field, "in", ids))
        self.state_types()
        watchers = self.ints("WatchUserIDs")
        if watchers:
            self.conds.append(_cond("watched_by", "in", watchers))
        self.locks()
        self.ticket_number()
        self.title()
        self.customers()
        self.archive()
        for field, type_key, prefix in _TIME_ROWS:
            self.time_field(field, type_key, prefix)
        for type_key, prefix in _NO_TIME_ROWS:
            self.unsupported_time_field(type_key, prefix)
        self.dynamic_fields()
        self.leftovers()

    def queues(self) -> None:
        sub = perl_truthy(self.values.get("UseSubQueues"))
        self.consumed.add("UseSubQueues")
        ids = self.ints("QueueIDs")
        if not ids:
            return
        selected = set(ids)
        if sub:
            names = self.lookups.queue_names
            for qid in ids:
                parent = names.get(qid)
                if parent is not None:
                    selected.update(i for i, n in names.items() if n.startswith(f"{parent}::"))
        self.conds.append(_cond("queue_id", "in", sorted(selected)))

    def state_types(self) -> None:
        ids = self.ints("StateTypeIDs")
        if not ids:
            return
        names = [self.lookups.state_type_names.get(i) for i in ids]
        if any(n is None for n in names):
            self.reject("StateTypeIDs", STATE_TYPE_REASON)
            return
        self.conds.append(_cond("state_type", "in", list(dict.fromkeys(n for n in names if n))))

    def locks(self) -> None:
        ids = self.ints("LockIDs")
        if not ids:
            return
        kinds: set[str] = set()
        for lid in ids:
            name = self.lookups.lock_names.get(lid)
            if name == "unlock":
                kinds.add("unlocked")
            elif name in ("lock", "tmp_lock"):
                kinds.add("locked")
            else:
                self.reject("LockIDs", LOCK_REASON)
                return
        if kinds == {"locked"}:
            self.conds.append(_cond("locked", "is_true"))
        elif kinds == {"unlocked"}:
            self.conds.append(_cond("locked", "is_false"))

    def pattern_condition(self, key: str, field: str) -> None:
        """Pattern rule P for the values of ``key`` (several values: any of them)."""
        found = self.take(key)
        conds: list[FilterNode] = []
        for value in found:
            result = _pattern(field, value)
            if isinstance(result, str):
                self.reject(key, result)
                return
            if result is None:  # "*" matches everything
                return
            conds.append(result)
        combined = _combine(conds, any_of=True)
        if combined is not None:
            self.conds.append(combined)

    def ticket_number(self) -> None:
        self.pattern_condition("TicketNumber", "tn")

    def title(self) -> None:
        found = self.take("Title")
        if not found:
            return
        text = " ".join(found)
        words = text.split()
        if _has_operator(text) or any(w in ("+", "-") for w in words):
            self.reject("Title", OPERATOR_REASON)
            return
        conds: list[FilterNode] = []
        for word in words:
            core = word.strip("*")
            if "*" in core:
                self.reject("Title", OPERATOR_REASON)
                return
            if core:
                conds.append(_cond("title", "contains", core))
        combined = _combine(conds)
        if combined is not None:
            self.conds.append(combined)

    def customers(self) -> None:
        raw_rows = (("CustomerIDRaw", "customer_id"), ("CustomerUserLoginRaw", "customer_user_id"))
        have_raw: set[str] = set()
        for key, field in raw_rows:
            found = self.take(key)
            if found:
                have_raw.add(field)
                self.conds.append(
                    _cond(field, "eq", found[0]) if len(found) == 1 else _cond(field, "in", found)
                )
        for key, field in (
            ("CustomerID", "customer_id"),
            ("CustomerUserLogin", "customer_user_id"),
        ):
            if field in have_raw:
                self.take(key)  # the Raw key wins
            else:
                self.pattern_condition(key, field)

    def archive(self) -> None:
        mode = self.one("SearchInArchive")
        if mode in ("", "NotArchivedTickets"):
            return
        if mode not in ("AllTickets", "ArchivedTickets"):
            self.reject("SearchInArchive", UNSUPPORTED_REASON)
        elif not self.is_admin:
            self.reject("SearchInArchive", ARCHIVE_REASON)
        else:
            self.include_archived = True
            if mode == "ArchivedTickets":
                self.conds.append(_cond("archived", "is_true"))

    # -- ticket time fields ---------------------------------------------------

    def _sub_keys(self, prefix: str) -> list[str]:
        keys = [f"{prefix}TimePoint", f"{prefix}TimePointStart", f"{prefix}TimePointFormat"]
        for side in ("Start", "Stop"):
            keys += [f"{prefix}Time{side}{part}" for part in _PARTS]
        return keys

    def time_field(self, field: str, type_key: str, prefix: str) -> None:
        search_type = self.one(type_key)
        point_key = f"{prefix}TimePoint"
        amount = self.one(point_key)
        start = self.one(f"{prefix}TimePointStart")
        unit = self.one(f"{prefix}TimePointFormat")
        slots = {
            side: {p: self.one(f"{prefix}Time{side}{p}") for p in _PARTS[:3]}
            for side in ("Start", "Stop")
        }
        for key in self._sub_keys(prefix):
            self.consumed.add(key)
        if search_type in _NO_SEARCH:
            return
        if search_type == "TimePoint":
            if amount:
                self.add_or_reject(point_key, _time_point(field, amount, start, unit))
        elif search_type == "TimeSlot":
            self.add_or_reject(
                type_key, _time_slot(field, slots["Start"], slots["Stop"], self.zone)
            )
        else:
            self.reject(type_key, UNSUPPORTED_REASON)

    def add_or_reject(self, key: str, result: Condition | None | str) -> None:
        if isinstance(result, str):
            self.reject(key, result)
        elif result is not None:
            self.conds.append(result)

    def unsupported_time_field(self, type_key: str, prefix: str) -> None:
        search_type = self.one(type_key)
        self.consumed.update(self._sub_keys(prefix))
        if search_type not in _NO_SEARCH:
            self.reject(type_key, NO_TIME_FIELD_REASON)

    # -- dynamic fields -------------------------------------------------------

    def df_for_key(self, key: str) -> str | None:
        """The dynamic field a ``Search_DynamicField_…`` key belongs to (longest name wins).

        Date/DateTime fields (and types Tiqora does not support) also own their suffixed
        keys (``<Name>TimePoint`` …); all other fields only the exact name.
        """
        rest = key[len(_DF_KEY_PREFIX) :]
        best: str | None = None
        for name, ftype in self.lookups.dynamic_fields.items():
            if not rest.startswith(name):
                continue
            suffix = rest[len(name) :]
            suffixed = ftype not in _DF_TYPES or ftype in ("Date", "DateTime")
            if suffix and not (suffixed and _DF_SUFFIX.fullmatch(suffix)):
                continue
            if best is None or len(name) > len(best):
                best = name
        return best

    def dynamic_fields(self) -> None:
        by_field: dict[str, list[str]] = {}
        for key in self.values:
            if key.startswith(_DF_KEY_PREFIX) and key not in self.consumed:
                name = self.df_for_key(key)
                if name is not None:
                    by_field.setdefault(name, []).append(key)
        for name, keys in by_field.items():  # profile key order
            self.dynamic_field(name, keys)

    def dynamic_field(self, name: str, keys: list[str]) -> None:
        ftype = self.lookups.dynamic_fields[name]
        base = f"{_DF_KEY_PREFIX}{name}"
        field = f"df.{name}"
        if ftype not in _DF_TYPES:
            filled = [k for k in keys if self.take(k)]  # consumes all keys
            if filled:  # one item per field, not per sub-key
                self.reject(filled[0], DF_TYPE_REASON)
            return
        if ftype in ("Date", "DateTime"):
            self.dynamic_date(name, base, field)
            return
        found = self.take(base)
        if not found:
            return
        if ftype in ("Text", "TextArea"):
            self.pattern_condition(base, field)
        elif ftype == "Checkbox":
            flags = set(found)
            if not flags <= {"1", "-1"}:
                self.reject(base, INVALID_VALUE_REASON)
            elif flags == {"1"}:
                self.conds.append(_cond(field, "is_true"))
            elif flags == {"-1"}:
                self.conds.append(_cond(field, "is_false"))
        else:  # Dropdown, Multiselect
            self.conds.append(_cond(field, "in", found))

    def dynamic_date(self, name: str, base: str, field: str) -> None:
        # the plain key holds no value for date fields; consume it
        self.consumed.add(base)
        point_key = f"{base}TimePoint"
        flag = perl_truthy(self.one(point_key))
        amount = self.one(f"{base}TimePointValue")
        fmt = self.one(f"{base}TimePointFormat")
        start = self.one(f"{base}TimePointStart")
        slot_key = f"{base}TimeSlot"
        slot_flag = perl_truthy(self.one(slot_key))
        slots = {
            side: {p: self.one(f"{base}TimeSlot{side}{p}") for p in _PARTS}
            for side in ("Start", "Stop")
        }
        for suffix in ("TimePointValue", "TimePointFormat", "TimePointStart"):
            self.consumed.add(f"{base}{suffix}")
        if flag:
            # Znuny stores direction and unit swapped in some versions: the direction word decides
            if fmt.strip().lower() in _DIRECTIONS and start.strip().lower() not in _DIRECTIONS:
                fmt, start = start, fmt
            self.add_or_reject(point_key, _time_point(field, amount, start, fmt))
        if slot_flag:
            self.add_or_reject(
                slot_key, _time_slot(field, slots["Start"], slots["Stop"], self.zone)
            )

    # -- everything not consumed ---------------------------------------------

    def leftovers(self) -> None:
        for key, raw in self.values.items():
            if key in self.consumed or key in IGNORED_KEYS:
                continue
            if not self.take(key):
                continue
            if key.startswith(_DF_KEY_PREFIX):
                self.unmapped.append(UnmappedItem(key, _show(raw), UNKNOWN_DF_REASON))
            elif key in _FULLTEXT_KEYS or key.startswith(("Article", "MIMEBase_")):
                self.unmapped.append(UnmappedItem(key, _show(raw), FULLTEXT_REASON))
            else:
                self.unmapped.append(UnmappedItem(key, _show(raw), UNSUPPORTED_REASON))


def map_search_profile(
    name: str,
    values: Mapping[str, str | list[str]],
    lookups: ZnunyLookups,
    *,
    is_admin: bool = False,
) -> MappedProfile:
    """Map one Znuny search profile.

    ``is_admin``: ``SearchInArchive`` = ``ArchivedTickets``/``AllTickets`` is only mapped for
    an admin (Tiqora only lets admins search the archive); otherwise it is reported as
    unmapped instead of being silently dropped.
    """
    mapper = _Mapper(values, lookups, is_admin)
    mapper.run()
    return MappedProfile(
        name=name,
        criteria=Filter(where=_combine(mapper.conds)),
        include_archived=mapper.include_archived,
        unmapped=tuple(mapper.unmapped),
    )
