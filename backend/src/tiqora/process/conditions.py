"""Transition condition data and evaluation: a port of Znuny's ``Transition.pm`` and the
``TransitionValidation`` modules, with smart-tag substitution for the ``Base`` types.

Sources (``znuny-6.5.22/Kernel/System/ProcessManagement``):

- ``Transition.pm:329-1000`` (``TransitionCheck``): linking / block / field combination.
- ``TransitionValidation/String.pm:99-170``, ``Regexp.pm:99-175``: raw comparisons.
- ``TransitionValidation/Base.pm:154-285`` (``CheckValueGet``, ``MatchValueGet``,
  ``ValueValidate``) and ``Base.pm:346-610`` (``Equal`` ... ``NotContains``).

``get_condition_data`` builds the equivalent of ``TicketGet(DynamicFields => 1)``, which is
the ``Data`` Znuny hands to ``TransitionCheck`` (``Process.pm:420-445``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.domain.dynamic_field_values import (
    DynamicFieldConfig,
    get_df_values_bulk,
)
from tiqora.domain.ticket_write_service import TicketNotFound
from tiqora.process.condition_modules import resolve_condition_module
from tiqora.process.config import TransitionConditionField, TransitionConfig
from tiqora.util.perl_regex import REGEX_TIMEOUT_SECONDS, PerlRegexError, compile_perl_regex

logger = logging.getLogger(__name__)

ConditionValue = str | list[str] | None
_WARNED_MODULES: set[str] = set()
ConditionData = dict[str, ConditionValue]

# Condition types derived from TransitionValidation::Base: only these substitute smart tags
# (CheckValueGet / MatchValueGet). String and Regexp compare raw (String.pm, Regexp.pm).
BASE_CONDITION_TYPES = frozenset(
    {
        "Equal",
        "NotEqual",
        "Contains",
        "NotContains",
        "GreaterThan",
        "GreaterThanOrEqual",
        "LessThan",
        "LessThanOrEqual",
    }
)

__all__ = [
    "BASE_CONDITION_TYPES",
    "ConditionConfigError",
    "ConditionData",
    "ConditionValue",
    "check_transition",
    "as_integer",
    "evaluate_condition_field",
    "evaluate_transition",
    "get_condition_data",
    "resolve_transition_tags",
    "value_validate",
]

_TAG_MARKER = "<otrs_"

# ---------------------------------------------------------------------------
# Condition data (Znuny TicketGet with DynamicFields)
# ---------------------------------------------------------------------------


async def get_condition_data(session: AsyncSession, ticket_id: int) -> ConditionData:
    """``TicketGet(DynamicFields => 1)`` as Znuny's ``Process.pm`` feeds it to ``TransitionCheck``.

    Ticket.pm:1240-1290 (columns), 1320-1360 (dynamic fields), 1362-1420 (names). IDs are
    strings; unset optional attributes are ``None``, except ``ServiceID``/``SLAID`` (``''``,
    Ticket.pm:1271-1272); ``TypeID``/``ResponsibleID`` fall back to ``1``. Every valid ticket
    dynamic field is ``DynamicField_<Name>``: ``None`` when unset, a list for Multiselect,
    else a string.
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT t.id, t.tn, t.title, t.queue_id, t.ticket_state_id,"
                    " t.ticket_priority_id, t.ticket_lock_id, t.type_id, t.service_id, t.sla_id,"
                    " t.user_id, t.responsible_user_id, t.customer_id, t.customer_user_id,"
                    " t.archive_flag, t.create_time, t.change_time FROM ticket t"
                    " WHERE t.id = :tid LIMIT 1"
                ),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise TicketNotFound(ticket_id)

    async def _name(sql: str, key: object | None) -> str | None:
        if key is None or key == 0:
            return None
        found = (await session.execute(text(sql), {"k": key})).first()
        return None if found is None else str(found[0])

    type_id = int(row["type_id"] or 1)  # Ticket.pm:1263: TypeID || 1
    responsible_id = int(row["responsible_user_id"] or 1)  # Ticket.pm:1262
    state = (
        await session.execute(
            text(
                "SELECT s.name, st.name FROM ticket_state s"
                " JOIN ticket_state_type st ON st.id = s.type_id WHERE s.id = :k"
            ),
            {"k": row["ticket_state_id"]},
        )
    ).first()
    data: ConditionData = {
        "TicketID": str(row["id"]),
        "TicketNumber": str(row["tn"]),
        "Title": row["title"],
        "Queue": await _name("SELECT name FROM queue WHERE id = :k", row["queue_id"]),
        "QueueID": str(row["queue_id"]),
        "State": None if state is None else str(state[0]),
        "StateID": str(row["ticket_state_id"]),
        "StateType": None if state is None else str(state[1]),
        "Priority": await _name(
            "SELECT name FROM ticket_priority WHERE id = :k", row["ticket_priority_id"]
        ),
        "PriorityID": str(row["ticket_priority_id"]),
        "Lock": await _name(
            "SELECT name FROM ticket_lock_type WHERE id = :k", row["ticket_lock_id"]
        ),
        "LockID": str(row["ticket_lock_id"]),
        "Type": await _name("SELECT name FROM ticket_type WHERE id = :k", type_id),
        "TypeID": str(type_id),
        "Service": await _name("SELECT name FROM service WHERE id = :k", row["service_id"]),
        "ServiceID": str(row["service_id"]) if row["service_id"] else "",  # Ticket.pm:1271
        "SLA": await _name("SELECT name FROM sla WHERE id = :k", row["sla_id"]),
        "SLAID": str(row["sla_id"]) if row["sla_id"] else "",  # Ticket.pm:1272
        "Owner": await _name("SELECT login FROM users WHERE id = :k", row["user_id"]),
        "OwnerID": str(row["user_id"]),
        "Responsible": await _name("SELECT login FROM users WHERE id = :k", responsible_id),
        "ResponsibleID": str(responsible_id),
        "CustomerID": row["customer_id"],
        "CustomerUserID": row["customer_user_id"],
        "ArchiveFlag": "y" if row["archive_flag"] else "n",  # Ticket.pm:1277
        "Created": _fmt_time(row["create_time"]),
        "Changed": _fmt_time(row["change_time"]),
    }

    fields = [
        DynamicFieldConfig(id=int(r[0]), name=str(r[1]), field_type=str(r[2]))
        for r in (
            await session.execute(
                text(
                    "SELECT id, name, field_type FROM dynamic_field"
                    " WHERE object_type = 'Ticket' AND valid_id = 1 ORDER BY id"
                )
            )
        ).fetchall()
    ]
    values = await get_df_values_bulk(session, fields, [ticket_id])
    for field in fields:
        stored = values.get((field.id, ticket_id))
        key = f"DynamicField_{field.name}"
        if not stored:
            data[key] = None  # Znuny leaves the key out; ``//`` in CheckValueGet treats both alike
        elif field.field_type == "Multiselect":
            data[key] = list(stored)
        else:
            data[key] = stored[0]
    return data


def _fmt_time(value: Any) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else None


# ---------------------------------------------------------------------------
# Value helpers (Base.pm ValueValidate, VariableCheck)
# ---------------------------------------------------------------------------

_DATETIME_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
# Znuny VariableCheck::IsInteger: optional leading '-', no leading zeros.
_INTEGER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)")


def value_validate(raw: str) -> str | int:
    """``Base.pm:ValueValidate`` -- DateTime/Date strings become epoch seconds (UTC)."""
    m = _DATETIME_RE.match(raw)
    if m:
        y, mo, d, h, mi, sec = m.groups()
        return int(
            datetime(
                int(y), int(mo), int(d), int(h), int(mi), int(sec or 0), tzinfo=UTC
            ).timestamp()
        )
    m = _DATE_RE.match(raw)
    if m:
        y, mo, d = m.groups()
        return int(datetime(int(y), int(mo), int(d), 0, 0, 0, tzinfo=UTC).timestamp())
    return raw


def as_integer(value: Any) -> int | None:
    """Znuny ``IsInteger`` after ValueValidate (ints or integer strings)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    s = str(value)
    if _INTEGER_RE.fullmatch(s):  # IsInteger: anchored, no surrounding whitespace
        return int(s)
    return None


def _sub(value: str, tags: Mapping[str, str] | None) -> str:
    """``TemplateGenerator::_Replace`` stand-in: the caller pre-rendered every tagged string."""
    return value if not tags else tags.get(value, value)


def _scalar(value: Any) -> str | None:
    """A Perl scalar: refs (list/dict) and undef are not scalars."""
    if value is None or isinstance(value, list | tuple | dict):
        return None
    return str(value)


def _perl_true(value: str | None) -> bool:
    return value not in (None, "", "0")


# ---------------------------------------------------------------------------
# Base.pm comparisons (346-610)
# ---------------------------------------------------------------------------


def _split_commas(text_: str) -> list[str]:
    parts = re.split(r"\s*,\s*", text_)
    while parts and parts[-1] == "":  # Perl split drops trailing empty fields
        parts.pop()
    return parts


def _equal(check: str | list[str], match: str | list[str]) -> bool:
    """``Base.pm:Equal``. Empty strings and empty lists never match (IsStringWithData)."""
    if isinstance(match, str):
        match = match.lower()
    if isinstance(check, list) and isinstance(match, list):
        return bool(check and match) and check == match
    if isinstance(check, list) and isinstance(match, str):
        if not check or not match:
            return False
        if "," in match:
            return [c.lower() for c in check] == _split_commas(match)
        return len(check) == 1 and check[0].lower() == match
    if isinstance(check, str) and isinstance(match, list):
        if not check or not match:
            return False
        if "," in check:
            return _split_commas(check) == [m.lower() for m in match]
        return len(match) == 1 and match[0].lower() == check.lower()
    if isinstance(check, str) and isinstance(match, str):
        return bool(check) and bool(match) and check.lower() == match
    return False


def _not_equal(check: str | list[str], match: str | list[str]) -> bool:
    """``Base.pm:NotEqual`` -- match is NOT lower-cased on the list path (Base.pm:454-458)."""
    if isinstance(check, list) and isinstance(match, list) and check and match:
        return check != match
    if isinstance(check, list) and check:
        return not any(match == c.lower() for c in check)
    if isinstance(check, list):  # empty list stringifies to a ref, never equal
        return True
    return str(check).lower() != str(match).lower()


def _ordered(check: Any, match: Any, op: str) -> bool:
    """GreaterThan & co (Base.pm:480-540): both sides must be integers (``IsInteger``)."""
    if isinstance(check, list) or isinstance(match, list):
        return False
    check_i = as_integer(check)
    match_i = as_integer(match)
    if check_i is None or match_i is None:
        return False
    return {
        "gt": check_i > match_i,
        "gte": check_i >= match_i,
        "lt": check_i < match_i,
        "lte": check_i <= match_i,
    }[op]


def _regex(pattern: str, value: str) -> bool | None:
    """Perl-flavoured search with a time budget; ``None`` = invalid pattern or timeout.

    Customer text reaches patterns through smart tags, so a catastrophic pattern must not
    stall the worker: a timeout is logged and counts as "no match", it never aborts.
    """
    try:
        return (
            compile_perl_regex(pattern, verbose=False).search(value, timeout=REGEX_TIMEOUT_SECONDS)
            is not None
        )
    except PerlRegexError:
        logger.warning("invalid condition regular expression %r", pattern)
    except TimeoutError:
        logger.error("condition regular expression %r timed out", pattern)
    return None


def _contains(check: str | list[str], match: Any) -> bool:
    """``Base.pm:Contains`` -- the lower-cased match is used as a regular expression.

    A list Match stringifies to ``ARRAY(0x...)`` in Perl and never matches.
    """
    if isinstance(match, list):
        return False
    pattern = str(match).lower()
    if isinstance(check, list):
        return any(_regex(pattern, c.lower()) is True for c in check)
    return _regex(pattern, check.lower()) is True


def _not_contains(check: str | list[str], match: Any) -> bool:
    """``Base.pm:NotContains`` -- list: exact element compare; string: regex non-match.

    A list Match never matches (see :func:`_contains`); a timeout or invalid pattern is no match.
    """
    if isinstance(match, list):
        return False
    pattern = str(match).lower()
    if isinstance(check, list):
        return not any(pattern == c.lower() for c in check)
    return _regex(pattern, check.lower()) is False


# ---------------------------------------------------------------------------
# Field evaluation
# ---------------------------------------------------------------------------

_MATCH = "match"
_NO_MATCH = "nomatch"
# Hash data that is not a hash: FieldFail++ but no short circuit (Transition.pm:627-631).
_NO_MATCH_QUIET = "nomatch_quiet"
# Config error: Znuny logs and ``return``s from TransitionCheck altogether.
_ERROR = "error"


def _check_value_get(
    field_name: str, data: Mapping[str, Any], tags: Mapping[str, str] | None
) -> str | list[str]:
    """``Base.pm:CheckValueGet`` (154-215)."""
    field_value = _sub(field_name, tags)
    if not field_value or field_value == "-":
        field_value = field_name
    check = data.get(field_value)
    if check is None:  # Perl ``//``
        check = field_value
    if isinstance(check, list):
        return [str(c) for c in check]
    check = str(check)
    if not _perl_true(check):
        return check
    return str(value_validate(_sub(check, tags)))


def _match_value_get(match: Any, tags: Mapping[str, str] | None) -> str | list[str] | None:
    """``Base.pm:MatchValueGet`` (221-262)."""
    if match is None:
        return None
    if isinstance(match, list | tuple):
        return [str(m) for m in match]
    if isinstance(match, dict):
        return None
    return str(value_validate(_sub(str(match), tags)))


def _evaluate_field(
    field_name: str,
    cond: TransitionConditionField,
    data: Mapping[str, Any],
    tags: Mapping[str, str] | None,
) -> str:
    type_ = cond.type_
    value = data.get(field_name)

    if type_ == "String":  # String.pm:99-170
        match = _scalar(cond.match)
        if match is None or match == "":  # ``!Match && Match ne '0'`` -> error, no match
            return _NO_MATCH
        if value is None:
            return _NO_MATCH
        if isinstance(value, list):
            return _MATCH if any(match == str(i) for i in value) else _NO_MATCH
        return _MATCH if match == str(value) else _NO_MATCH

    if type_ == "Regexp":  # Regexp.pm:99-175
        pattern = _scalar(cond.match)
        if not _perl_true(pattern):
            return _NO_MATCH
        assert pattern is not None
        if isinstance(value, list):
            return _MATCH if any(_regex(pattern, str(i)) is True for i in value) else _NO_MATCH
        if _perl_true(None if value is None else str(value)):
            return _MATCH if _regex(pattern, str(value)) is True else _NO_MATCH
        return _NO_MATCH

    if type_ == "Array":  # Transition.pm:543-607
        if not isinstance(cond.match, list | tuple):
            return _ERROR
        if isinstance(value, list):
            have = [str(v) for v in value]
        elif value is None:
            have = []
        else:  # Perl would die dereferencing a string; a single-element list is the sane reading
            have = [str(value)]
        found = sum(have.count(str(m)) for m in cond.match)
        return _MATCH if found == len(cond.match) else _NO_MATCH

    if type_ == "Hash":  # Transition.pm:608-690
        if not isinstance(cond.match, dict):
            return _ERROR
        if not value or not isinstance(value, Mapping):
            return _NO_MATCH_QUIET
        equal = sum(1 for k, v in cond.match.items() if str(value.get(k, "")) == str(v))
        return _MATCH if equal == len(cond.match) else _NO_MATCH

    if type_ == "Module":
        module = resolve_condition_module(cond.match)
        if module is None:
            _warn_module_unavailable(cond.match)
            return _NO_MATCH
        return _MATCH if module.validate(data, field_name) else _NO_MATCH

    if type_ in BASE_CONDITION_TYPES:
        check = _check_value_get(field_name, data, tags)
        match_value = _match_value_get(cond.match, tags)
        if match_value is None:  # Equal.pm: ``return if !defined $MatchValue``
            return _NO_MATCH
        return _MATCH if _base_compare(type_, check, match_value) else _NO_MATCH

    logger.warning("unsupported transition condition Type: %s", cond.type_)
    return _ERROR


def _warn_module_unavailable(match: Any) -> None:
    """Log an unavailable condition module once per process run."""
    key = str(match)
    if key not in _WARNED_MODULES:
        _WARNED_MODULES.add(key)
        logger.warning("transition condition module not available: %s", key)


_ORDERED_OPS = {
    "GreaterThan": "gt",
    "GreaterThanOrEqual": "gte",
    "LessThan": "lt",
    "LessThanOrEqual": "lte",
}


def _base_compare(type_: str, check: str | list[str], match: str | list[str]) -> bool:
    if type_ == "Equal":
        return _equal(check, match)
    if type_ == "NotEqual":
        return _not_equal(check, match)
    if type_ == "Contains":
        return _contains(check, match)
    if type_ == "NotContains":
        return _not_contains(check, match)
    return _ordered(check, match, _ORDERED_OPS[type_])


def evaluate_condition_field(
    field_name: str,
    cond: TransitionConditionField,
    data: Mapping[str, Any],
    *,
    tags: Mapping[str, str] | None = None,
) -> bool:
    """Does one ``Fields`` entry match *data*? ``tags`` maps raw text to substituted text."""
    return _evaluate_field(field_name, cond, data, tags) == _MATCH


# ---------------------------------------------------------------------------
# Transition evaluation (Transition.pm:329-1000)
# ---------------------------------------------------------------------------

_LINKINGS = ("and", "or", "xor")


class ConditionConfigError(Exception):
    """A transition configuration Znuny rejects with ``return;`` inside ``TransitionCheck``.

    That bare ``return`` leaves the whole check, so no transition of the activity fires --
    not only the broken one (Transition.pm:322-327, 345-355, 359-366, 372-381, 460-466).
    """


def evaluate_transition(
    transition_config: TransitionConfig,
    ticket_attrs: Mapping[str, Any],
    *,
    tags: Mapping[str, str] | None = None,
) -> bool:
    """:func:`check_transition` with a configuration error reported as "no match"."""
    try:
        return check_transition(transition_config, ticket_attrs, tags=tags)
    except ConditionConfigError as exc:
        logger.warning("transition condition: %s", exc)
        return False


def check_transition(
    transition_config: TransitionConfig,
    ticket_attrs: Mapping[str, Any],
    *,
    tags: Mapping[str, str] | None = None,
) -> bool:
    """``TransitionCheck`` for one transition, including its counting quirks.

    Linking and block types ``and``/``or``/``xor``. A transition without a condition, a block
    without fields, an unknown linking / block / field type or a malformed ``Match`` is a
    configuration error and raises :class:`ConditionConfigError`.
    """
    if not transition_config.has_condition:  # Transition.pm:321
        raise ConditionConfigError("no conditions")
    linking = transition_config.condition_linking
    if linking not in _LINKINGS:
        raise ConditionConfigError(
            f"invalid ConditionLinking {transition_config.condition_linking!r}"
        )

    cond_success = cond_fail = 0
    for block in transition_config.conditions:
        if not block.fields:
            raise ConditionConfigError("condition block without Fields")
        cond_type = block.type_ or "and"
        if cond_type not in _LINKINGS:
            raise ConditionConfigError(f"invalid condition block Type {block.type_!r}")

        field_success = field_fail = 0
        skip_block = False
        for name in sorted(block.fields):
            result = _evaluate_field(name, block.fields[name], ticket_attrs, tags)
            if result == _ERROR:
                raise ConditionConfigError(f"invalid condition field {name!r}")
            if result == _MATCH:
                field_success += 1
                if linking == "or" and cond_type == "or":
                    return True  # Transition.pm:502-513
                if linking != "or" and cond_type == "or":
                    skip_block = True  # next CONDITIONNAME, nothing counted (:515)
                    break
            else:
                field_fail += 1
                if result == _NO_MATCH_QUIET:
                    continue
                if linking == "and" and cond_type == "and":
                    return False  # next TRANSITIONENTITYID (:536)
                if cond_type == "and":
                    skip_block = True  # next CONDITIONNAME (:539)
                    break
        if skip_block:
            continue

        # Transition.pm:868-948
        if cond_type == "and":
            ok = field_fail == 0
        elif cond_type == "or":
            ok = field_success > 0
        else:
            ok = field_success == 1
        if ok:
            if linking == "or":
                return True
            cond_success += 1
        else:
            cond_fail += 1
            if linking == "and" and cond_type != "xor":
                return False

    # Transition.pm:952-990
    if linking == "and":
        return cond_fail == 0
    if linking == "or":
        return cond_success > 0
    return cond_success == 1


# ---------------------------------------------------------------------------
# Smart tags
# ---------------------------------------------------------------------------


def _has_tag(value: Any) -> bool:
    return isinstance(value, str) and _TAG_MARKER in value.lower()


async def resolve_transition_tags(
    transition_config: TransitionConfig,
    data: Mapping[str, Any],
    *,
    render: Callable[[str], Awaitable[str]],
) -> dict[str, str]:
    """Render every tagged string a Base-type condition will look at (Base.pm:154-262).

    Field names and ``Match`` values, plus the data value found under the substituted field
    name (two rounds: ``CheckValueGet`` replaces the name, then the value). Each text is
    rendered once.
    """
    out: dict[str, str] = {}

    async def _render(value: str) -> str:
        if value not in out:
            out[value] = await render(value)
        return out[value]

    for block in transition_config.conditions:
        for name, cond in block.fields.items():
            if cond.type_ not in BASE_CONDITION_TYPES:
                continue
            field_name = name
            if _has_tag(name):
                field_name = await _render(name)
                if not field_name or field_name == "-":
                    field_name = name
            if _has_tag(cond.match):
                await _render(cond.match)
            found = data.get(field_name)
            if _has_tag(found):
                assert isinstance(found, str)
                await _render(found)
    return out
