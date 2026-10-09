"""Znuny-identical dynamic-field writes for tickets.

Port of ``Kernel/System/DynamicField/Backend.pm`` (``ValueSet``,
``ValueIsDifferent``), the ``Driver/*`` ``ValueSet`` methods, the generic
``Kernel/System/DynamicFieldValue.pm::ValueSet`` and
``Kernel/System/DynamicField/ObjectType/Ticket.pm::PostValueSet``:

* Date / DateTime values live in ``value_date``, Checkbox in ``value_int``,
  everything else in ``value_text``.
* An unchanged value writes nothing (no history row, no ``change_time`` bump).
* A change bumps ``ticket.change_time`` and writes a ``TicketDynamicFieldUpdate``
  history row with Znuny's 166-character truncation rule.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.cache_invalidation import invalidate_ticket_cache
from tiqora.znuny.history import add_dynamic_field_update

DF_TEXT_TYPES = frozenset({"Text", "TextArea", "Dropdown", "Multiselect"})
DF_EDITABLE_TYPES = frozenset(
    {"Text", "TextArea", "Dropdown", "Multiselect", "Checkbox", "Date", "DateTime"}
)
HISTORY_MAX_CHARS = 166
DF_UPDATE_EVENT_PREFIX = "TicketDynamicFieldUpdate_"

_SINGLE_VALUE_TYPES = frozenset({"Text", "TextArea", "Dropdown", "Date", "DateTime", "Checkbox"})
_DATE_RE = re.compile(r"\A(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?\Z")


class DynamicFieldValueError(ValueError):
    """Invalid value for a dynamic field (plain German message)."""


@dataclass(frozen=True, slots=True)
class DynamicFieldConfig:
    id: int
    name: str
    field_type: str
    object_type: str = "Ticket"


def _parse_datetime(raw: str) -> datetime | None:
    m = _DATE_RE.match(raw.strip())
    if m is None:
        return None
    day, hh, mm, ss = m.groups()
    try:
        return datetime.fromisoformat(f"{day} {hh or '00'}:{mm or '00'}:{ss or '00'}")
    except ValueError:
        return None


def normalize_df_values(field_type: str, values: Sequence[str]) -> list[str]:
    """Normalise ``values`` for ``field_type`` (Znuny driver ``ValueSet`` rules)."""
    vals = [v for v in values if v != ""]
    if field_type in _SINGLE_VALUE_TYPES and len(vals) > 1:
        raise DynamicFieldValueError("Dieses Feld erlaubt nur einen Wert.")
    if field_type == "Date":
        out: list[str] = []
        for v in vals:
            dt = _parse_datetime(v)
            if dt is None or (dt.hour, dt.minute, dt.second) != (0, 0, 0):
                raise DynamicFieldValueError(
                    "Bitte ein gültiges Datum im Format JJJJ-MM-TT angeben (ohne Uhrzeit)."
                )
            out.append(dt.strftime("%Y-%m-%d %H:%M:%S"))
        return out
    if field_type == "DateTime":
        out = []
        for v in vals:
            dt = _parse_datetime(v)
            if dt is None or len(v.strip()) <= 10:
                raise DynamicFieldValueError(
                    "Bitte Datum und Uhrzeit im Format JJJJ-MM-TT HH:MM angeben."
                )
            out.append(dt.strftime("%Y-%m-%d %H:%M:%S"))
        return out
    if field_type == "Checkbox":
        # Checkbox.pm ValueSet: a defined-but-empty value is stored as 0;
        # only "no value at all" ([]) deletes the row.
        if values and not vals:
            return ["0"]
        for v in vals:
            if v not in ("0", "1"):
                raise DynamicFieldValueError("Ein Kontrollkästchen akzeptiert nur 0 oder 1.")
        return vals
    return vals


async def _load_field(
    session: AsyncSession, name: str, object_types: tuple[str, ...]
) -> DynamicFieldConfig | None:
    row = (
        await session.execute(
            text(
                "SELECT id, name, field_type, object_type FROM dynamic_field"
                " WHERE name = :n AND object_type IN :ot AND valid_id = 1 LIMIT 1"
            ).bindparams(bindparam("ot", expanding=True)),
            {"n": name, "ot": list(object_types)},
        )
    ).first()
    if row is None:
        return None
    return DynamicFieldConfig(
        id=int(row[0]), name=str(row[1]), field_type=str(row[2]), object_type=str(row[3])
    )


async def load_ticket_dynamic_field(session: AsyncSession, name: str) -> DynamicFieldConfig | None:
    return await _load_field(session, name, ("Ticket",))


async def load_dynamic_field(session: AsyncSession, name: str) -> DynamicFieldConfig | None:
    """The valid dynamic field *name* of object type ``Ticket`` or ``Article`` (or None).

    Znuny's GenericInterface ``DynamicFieldLookup`` covers exactly those two
    object types. The name match follows the database collation (case-insensitive
    on MariaDB); callers that need the exact spelling compare ``config.name``.
    """
    return await _load_field(session, name, ("Ticket", "Article"))


async def load_article_dynamic_field(session: AsyncSession, name: str) -> DynamicFieldConfig | None:
    """The valid dynamic field *name* of object type ``Article`` (or None)."""
    return await _load_field(session, name, ("Article",))


async def possible_values_of(session: AsyncSession, field: DynamicFieldConfig) -> dict[str, Any]:
    """``PossibleValues`` of a Dropdown / Multiselect, keys as strings (YAML reads ``1`` as int)."""
    from sqlalchemy import select

    from tiqora.db.legacy.dynamic_field import DynamicField
    from tiqora.domain.dynamic_field_config import config_from_yaml

    raw_config = (
        await session.execute(select(DynamicField.config).where(DynamicField.id == field.id))
    ).scalar_one_or_none()
    possible = config_from_yaml(raw_config).get("PossibleValues")
    return {str(k): v for k, v in possible.items()} if isinstance(possible, dict) else {}


async def values_outside_possible_values(
    session: AsyncSession, field: DynamicFieldConfig, values: Sequence[str]
) -> list[str]:
    """Non-empty ``values`` that are not keys of a Dropdown / Multiselect's PossibleValues.

    ``BaseSelect.pm::EditFieldValueValidate``: the keys compare as strings.
    Other field types never restrict.
    """
    if field.field_type not in ("Dropdown", "Multiselect"):
        return []
    allowed = await possible_values_of(session, field)
    return [v for v in values if v != "" and v not in allowed]


def _fmt_date(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else str(value)


def _render_df_rows(
    field_type: str, rows: Sequence[tuple[Any, Any, Any]]
) -> tuple[list[str], bool]:
    """Typed-column value logic shared by the single and the bulk reader.

    ``rows`` are ``(value_text, value_int, value_date)`` in ``id`` order. Returns
    ``(values, has_legacy)``; see :func:`_read_df_values`.
    """
    out: list[str] = []
    legacy = False
    for vt, vi, vd in rows:
        if field_type == "Checkbox":
            if vi is not None:
                out.append(str(int(vi)))
            elif vt is not None:
                legacy = True
                out.append(str(vt))
        elif field_type in ("Date", "DateTime"):
            if vd is not None:
                out.append(_fmt_date(vd))
            elif vt is not None:
                legacy = True
                parsed = _parse_datetime(str(vt))
                out.append(_fmt_date(parsed) if parsed is not None else str(vt))
        elif vt is not None:
            out.append(str(vt))
    return out, legacy


async def _read_df_values(
    session: AsyncSession, field: DynamicFieldConfig, object_id: int
) -> tuple[list[str], bool]:
    """Read stored values by field type (Znuny ``ValueGet``): Checkbox from
    ``value_int``, Date/DateTime from ``value_date``, everything else from
    ``value_text``.

    Returns ``(values, has_legacy)``. ``has_legacy`` is True when a Date/DateTime/
    Checkbox row has an empty typed column but a ``value_text`` (written by older
    Tiqora versions): it is still read, but the caller must rewrite it.
    """
    rows = (
        await session.execute(
            text(
                "SELECT value_text, value_int, value_date FROM dynamic_field_value"
                " WHERE field_id = :fid AND object_id = :oid ORDER BY id"
            ),
            {"fid": field.id, "oid": object_id},
        )
    ).fetchall()
    return _render_df_rows(field.field_type, [(r[0], r[1], r[2]) for r in rows])


async def get_df_values_bulk(
    session: AsyncSession, fields: Sequence[DynamicFieldConfig], object_ids: Sequence[int]
) -> dict[tuple[int, int], list[str]]:
    """Values of many fields for many objects in one query.

    Keyed by ``(field_id, object_id)``. A pair is present as soon as the object
    has at least one stored row for the field (its list may still be empty when
    every row is NULL) -- callers that mirror Znuny's "only fields with rows"
    behaviour rely on that. Rendering is identical to :func:`get_df_values`.
    """
    if not fields or not object_ids:
        return {}
    type_by_id = {f.id: f.field_type for f in fields}
    stmt = text(
        "SELECT field_id, object_id, value_text, value_int, value_date FROM dynamic_field_value"
        " WHERE field_id IN :fids AND object_id IN :oids ORDER BY id"
    ).bindparams(bindparam("fids", expanding=True), bindparam("oids", expanding=True))
    rows = (
        await session.execute(stmt, {"fids": list(type_by_id), "oids": list(set(object_ids))})
    ).fetchall()
    grouped: dict[tuple[int, int], list[tuple[Any, Any, Any]]] = {}
    for fid, oid, vt, vi, vd in rows:
        grouped.setdefault((int(fid), int(oid)), []).append((vt, vi, vd))
    return {key: _render_df_rows(type_by_id[key[0]], vals)[0] for key, vals in grouped.items()}


async def get_df_values(
    session: AsyncSession, field: DynamicFieldConfig, object_id: int
) -> list[str]:
    return (await _read_df_values(session, field, object_id))[0]


def df_values_differ(old: Sequence[str], new: Sequence[str]) -> bool:
    """``Driver/Base.pm::ValueIsDifferent``: both empty is not different."""
    if not old and not new:
        return False
    return list(old) != list(new)


def readable_history_value(field_type: str, values: Sequence[str]) -> str:
    """``ReadableValueRender`` as used for the history row."""
    if field_type == "Multiselect":
        return ", ".join(values)
    first = values[0] if values else ""
    if field_type == "Date":
        return first[:10]
    return first


def truncate_history_parts(field_name: str, value: str, old_value: str) -> tuple[str, str, str]:
    """Exact port of the 166-char rule in ``ObjectType/Ticket.pm::PostValueSet``."""
    limit = HISTORY_MAX_CHARS
    if len(field_name) + len(value) + len(old_value) <= limit:
        return field_name, value, old_value
    if len(old_value) > 55:
        old_value = old_value[:50] + "[...]"
    name_len = (limit - len(old_value)) // 2
    value_len = name_len
    if len(field_name) > name_len:
        if len(value) > value_len:
            name_len -= 5
            field_name = field_name[: max(name_len, 0)] + "[...]"
            value_len -= 5
            value = value[: max(value_len, 0)] + "[...]"
        else:
            name_len = limit - len(old_value) - len(value) - 5
            field_name = field_name[: max(name_len, 0)] + "[...]"
    else:
        value_len = limit - len(old_value) - len(field_name) - 5
        if len(value) > value_len:
            value = value[: max(value_len, 0)] + "[...]"
    return field_name, value, old_value


async def _replace_df_rows(
    session: AsyncSession, field: DynamicFieldConfig, object_id: int, new: Sequence[str]
) -> None:
    """``DynamicFieldValue.pm::ValueSet``: drop the object's rows, insert the typed new ones."""
    await session.execute(
        text("DELETE FROM dynamic_field_value WHERE field_id = :fid AND object_id = :oid"),
        {"fid": field.id, "oid": object_id},
    )
    for v in new:
        params: dict[str, Any] = {
            "fid": field.id,
            "oid": object_id,
            "vt": None,
            "vd": None,
            "vi": None,
        }
        if field.field_type in ("Date", "DateTime"):
            params["vd"] = datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        elif field.field_type == "Checkbox":
            params["vi"] = int(v)
        else:
            params["vt"] = v
        await session.execute(
            text(
                "INSERT INTO dynamic_field_value (field_id, object_id, value_text, value_date,"
                " value_int) VALUES (:fid, :oid, :vt, :vd, :vi)"
            ),
            params,
        )


async def set_ticket_dynamic_field(
    session: AsyncSession,
    *,
    ticket_id: int,
    field: DynamicFieldConfig,
    values: Sequence[str],
    user_id: int,
) -> bool:
    """Write ``values``; returns False (and writes nothing) when unchanged."""
    from tiqora.domain.ticket_write_service import _emit_event

    new = normalize_df_values(field.field_type, values)
    old, legacy = await _read_df_values(session, field, ticket_id)
    # A legacy value_text row of a typed field counts as changed so it is
    # rewritten into the typed column (only the stored form differs).
    if not legacy and not df_values_differ(old, new):
        return False

    await _replace_df_rows(session, field, ticket_id, new)

    await session.execute(
        text("UPDATE ticket SET change_time = current_timestamp, change_by = :uid WHERE id = :tid"),
        {"uid": user_id, "tid": ticket_id},
    )
    name, value, old_value = truncate_history_parts(
        field.name,
        readable_history_value(field.field_type, new),
        readable_history_value(field.field_type, old),
    )
    await add_dynamic_field_update(
        session,
        ticket_id=ticket_id,
        field_name=name,
        value=value,
        old_value=old_value,
        user_id=user_id,
    )
    await invalidate_ticket_cache(session, ticket_id)
    # Znuny names the event per field (``TicketDynamicFieldUpdate_<Name>``); that is
    # what notification events and GI invokers bind to.
    await _emit_event(
        session,
        f"{DF_UPDATE_EVENT_PREFIX}{field.name}",
        ticket_id,
        {"field": field.name, "values": new},
    )
    return True


async def set_article_dynamic_field(
    session: AsyncSession,
    *,
    ticket_id: int,
    article_id: int,
    field: DynamicFieldConfig,
    values: Sequence[str],
    user_id: int,
) -> bool:
    """Write an Article-object field value; False (nothing written) when unchanged.

    ``ObjectType/Article.pm::PostValueSet``: bumps the ticket's ``change_time``,
    clears the ticket cache and fires ``ArticleDynamicFieldUpdate`` -- but, unlike
    ticket fields, writes no history row.
    """
    from tiqora.domain.ticket_write_service import InvalidInput, _emit_event

    # The ticket whose change_time / cache / event this write touches must be the article's
    # own (same guard as add_time_accounting).
    owner = (
        await session.execute(
            text("SELECT ticket_id FROM article WHERE id = :aid"), {"aid": article_id}
        )
    ).scalar_one_or_none()
    if owner is None or int(owner) != ticket_id:
        raise InvalidInput("article_id does not belong to this ticket")

    new = normalize_df_values(field.field_type, values)
    old, legacy = await _read_df_values(session, field, article_id)
    if not legacy and not df_values_differ(old, new):
        return False
    await _replace_df_rows(session, field, article_id, new)
    await session.execute(
        text("UPDATE ticket SET change_time = current_timestamp, change_by = :uid WHERE id = :tid"),
        {"uid": user_id, "tid": ticket_id},
    )
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(
        session,
        "ArticleDynamicFieldUpdate",
        ticket_id,
        {"field": field.name, "values": new, "article_id": article_id},
    )
    return True
