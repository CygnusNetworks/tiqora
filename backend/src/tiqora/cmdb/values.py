"""CMDB attribute values (D4): normalisation against a definition. Pure part.

Value document: every attribute key maps to a list of entries ``{"v": scalar | None}``, plus
``"s"`` (sub attributes, same shape) when the definition has ``sub``. ``check_references``
(database lookups) is at the end of this module and used by the write path.

Order of the empty-entry rules (F18, binding for ``attributeModel.validateValues`` /
``pruneEmpty`` in the frontend): an empty entry of a ``required`` attribute is a ``required``
issue and is never dropped silently; empty entries of other attributes are dropped, and
``count_min`` is checked against what is left. Sub attributes are only checked inside entries
that are present (non-empty).
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.definition import INT_ID_TYPES, AttributeDef
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import (
    TiqoraCmdbAttachment,
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbClass,
)

#: types whose stored value is an id or a login (what ``check_references`` resolves)
REF_TYPES: frozenset[str] = frozenset(INT_ID_TYPES | {"customer_user", "customer_company"})

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAC = re.compile(r"^[0-9a-f]{12}$")
_INT = re.compile(r"^[+-]?\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$")
_MAX_INT = 2**63 - 1

_DEFAULT_MAX_LENGTH = {
    "text": 255,
    "textarea": 20000,
    "url": 2048,
    "email": 254,
    "ip": 64,
    "mac": 32,
    "customer_user": 250,
    "customer_company": 150,
}


@dataclass(frozen=True)
class ValueIssue:
    path: str
    code: str
    message: str


Values = dict[str, list[dict[str, Any]]]


def canonical_json(values: Mapping[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _is_empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


class _Bad(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _to_int(v: Any) -> int:
    if isinstance(v, bool):
        raise _Bad("bad_type", "Eine ganze Zahl wird erwartet.")
    if isinstance(v, int):
        n = v
    elif isinstance(v, float):
        if not v.is_integer():
            raise _Bad("bad_type", "Eine ganze Zahl wird erwartet.")
        n = int(v)
    elif isinstance(v, str) and _INT.match(v.strip()):
        if len(v.strip()) > 40:
            raise _Bad("out_of_range", "Die Zahl ist zu groß.")
        n = int(v.strip())
    else:
        raise _Bad("bad_type", "Eine ganze Zahl wird erwartet.")
    if abs(n) > _MAX_INT:
        raise _Bad("out_of_range", "Die Zahl ist zu groß.")
    return n


def _to_decimal(v: Any) -> Decimal:
    if isinstance(v, bool) or not isinstance(v, int | float | str | Decimal):
        raise _Bad("bad_type", "Eine Zahl wird erwartet.")
    try:
        d = Decimal(str(v).strip())
    except InvalidOperation as exc:
        raise _Bad("bad_type", "Eine Zahl wird erwartet.") from exc
    if not d.is_finite():
        raise _Bad("bad_type", "Eine Zahl wird erwartet.")
    if d != 0 and (d.adjusted() > 30 or d.adjusted() < -30 or len(d.as_tuple().digits) > 28):
        # also keeps normalize() inside the default context precision (no silent rounding)
        raise _Bad("out_of_range", "Die Zahl ist zu groß, zu klein oder zu genau.")
    return d


def _decimal_str(d: Decimal) -> str:
    # F21: canonical form; the scale is a display concern ("16.50" == "16.5")
    out = format(d.normalize(), "f")
    return "0" if out in ("-0", "") else out


def _to_datetime(v: Any, tz: str) -> str:
    if not isinstance(v, str):
        raise _Bad("bad_type", "Ein Zeitpunkt als Text wird erwartet.")
    s = v.strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise _Bad("bad_format", "Format: JJJJ-MM-TTTHH:MM[:SS].") from exc
    if dt.tzinfo is None:
        if not _DATETIME.match(s):
            raise _Bad("bad_format", "Format: JJJJ-MM-TTTHH:MM[:SS].")
        try:
            dt = dt.replace(tzinfo=UTC if tz.upper() == "UTC" else ZoneInfo(tz))
        except (ZoneInfoNotFoundError, ValueError, OSError):
            dt = dt.replace(tzinfo=UTC)
    try:
        return dt.astimezone(UTC).replace(tzinfo=None, microsecond=0).isoformat(timespec="seconds")
    except (OverflowError, ValueError) as exc:
        raise _Bad("out_of_range", "Der Zeitpunkt liegt außerhalb des erlaubten Bereichs.") from exc


def _coerce(a: AttributeDef, v: Any, tz: str) -> Any:
    """Normalised scalar for a non-empty ``v``; raises :class:`_Bad`."""
    t = a.type
    if t in ("text", "textarea", "url", "email", "ip", "mac", "customer_user", "customer_company"):
        if not isinstance(v, str):
            raise _Bad("bad_type", "Ein Text wird erwartet.")
        s = v.strip() if t not in ("text", "textarea") else v
        if t in ("text", "textarea") and not s.strip():
            raise _Bad("bad_type", "Ein Text wird erwartet.")
        limit = a.options.max_length if a.options.max_length is not None else _DEFAULT_MAX_LENGTH[t]
        if len(s) > limit:
            raise _Bad("too_long", f"Höchstens {limit} Zeichen.")
        if t == "email" and not _EMAIL.match(s):
            raise _Bad("bad_format", "Keine gültige E-Mail-Adresse.")
        if t == "url":
            try:
                parsed = urlparse(s)
            except ValueError as exc:
                raise _Bad(
                    "bad_format", "Eine Adresse mit http:// oder https:// wird erwartet."
                ) from exc
            if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
                raise _Bad("bad_format", "Eine Adresse mit http:// oder https:// wird erwartet.")
        if t == "ip":
            try:
                if "/" not in s:
                    return str(ipaddress.ip_address(s))
                return ipaddress.ip_interface(s).with_prefixlen
            except ValueError as exc:
                raise _Bad("bad_format", "Keine gültige IP-Adresse.") from exc
        if t == "mac":
            hexed = re.sub(r"[:\-.\s]", "", s).lower()
            if not _MAC.match(hexed):
                raise _Bad("bad_format", "Keine gültige MAC-Adresse.")
            return ":".join(hexed[i : i + 2] for i in range(0, 12, 2))
        return s
    if t == "integer" or t in INT_ID_TYPES:
        n = _to_int(v)
        if t == "integer":
            _check_range(a, Decimal(n))
        return n
    if t in ("decimal", "money"):
        d = _to_decimal(v)
        _check_range(a, d)
        return _decimal_str(d)
    if t == "date":
        if not isinstance(v, str) or not _DATE.match(v.strip()):
            raise _Bad("bad_format", "Format: JJJJ-MM-TT.")
        s = v.strip()
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError as exc:
            raise _Bad("bad_format", "Kein gültiges Datum.") from exc
        return s
    if t == "datetime":
        return _to_datetime(v, tz)
    raise _Bad("bad_type", "Dieser Typ nimmt keinen Wert auf.")  # pragma: no cover


def _check_range(a: AttributeDef, d: Decimal) -> None:
    lo, hi = a.options.min, a.options.max
    if (lo is not None and d < lo) or (hi is not None and d > hi):
        raise _Bad("out_of_range", "Der Wert liegt außerhalb des erlaubten Bereichs.")


def _normalize_level(
    attrs: list[AttributeDef],
    raw: Mapping[str, Any],
    prefix: str,
    tz: str,
    issues: list[ValueIssue],
) -> Values:
    out: Values = {}
    known = {a.key for a in attrs}
    for a in attrs:
        path = f"{prefix}{a.key}"
        entries_raw = raw.get(a.key)
        if entries_raw is None:
            entries_raw = []
        if not isinstance(entries_raw, list):
            issues.append(ValueIssue(path, "bad_type", "Eine Liste von Einträgen wird erwartet."))
            continue
        kept: list[dict[str, Any]] = []
        unusable = 0
        for idx, entry in enumerate(entries_raw, start=1):
            epath = f"{path}[{idx}]"
            if not isinstance(entry, Mapping):
                issues.append(ValueIssue(epath, "bad_type", "Ein Eintrag mit „v“ wird erwartet."))
                continue
            local: list[ValueIssue] = []
            value: Any = None
            value_bad = False
            v = entry.get("v")
            if a.type != "group" and not _is_empty(v):
                try:
                    value = _coerce(a, v, tz)
                except _Bad as bad:
                    value_bad = True
                    local.append(ValueIssue(epath, bad.code, bad.message))
                except (ValueError, OverflowError, ArithmeticError):
                    # last line of defence: untrusted input must never become a 500
                    value_bad = True
                    local.append(ValueIssue(epath, "bad_format", "Ungültiger Wert."))
            sub_vals: Values = {}
            raw_sub = entry.get("s")
            if a.sub:
                if raw_sub is not None and not isinstance(raw_sub, Mapping):
                    local.append(ValueIssue(epath, "bad_type", "„s“ muss ein Objekt sein."))
                elif raw_sub:
                    sub_vals = _normalize_level(a.sub, raw_sub, f"{epath}.", tz, local)
            elif raw_sub:
                if isinstance(raw_sub, Mapping):
                    for k in raw_sub:
                        local.append(
                            ValueIssue(f"{epath}.{k}", "unknown_key", "Unbekanntes Attribut.")
                        )
                else:
                    local.append(ValueIssue(epath, "bad_type", "„s“ ist hier nicht erlaubt."))
            if value_bad:
                # the entry is reported and left out; it still counts as present
                issues.extend(local)
                unusable += 1
                continue
            empty = value is None and not sub_vals
            if empty:
                # sub attributes of an entry that is not there cannot be "required"
                local = [i for i in local if i.code not in ("required", "too_few")]
                issues.extend(local)
                if a.required:
                    issues.append(ValueIssue(epath, "required", "Pflichtfeld."))
                    unusable += 1
                continue
            issues.extend(local)
            item: dict[str, Any] = {"v": value}
            if a.sub:
                item["s"] = sub_vals
            kept.append(item)
        if len(kept) + unusable > a.count_max:
            issues.append(
                ValueIssue(path, "too_many", f"Höchstens {a.count_max} Einträge sind erlaubt.")
            )
        if len(kept) + unusable < a.count_min:
            issues.append(
                ValueIssue(path, "too_few", f"Mindestens {a.count_min} Einträge sind nötig.")
            )
        if kept:
            out[a.key] = kept
    for key in raw:
        if key not in known:
            issues.append(ValueIssue(f"{prefix}{key}", "unknown_key", "Unbekanntes Attribut."))
    return out


def normalize_values(
    attrs: list[AttributeDef], raw: Mapping[str, Any], *, now_tz: str = "UTC"
) -> tuple[Values, list[ValueIssue]]:
    """Coerce ``raw`` to the stored shape; unknown keys are reported and removed."""
    issues: list[ValueIssue] = []
    return _normalize_level(attrs, raw, "", now_tz, issues), issues


def unknown_top_level_keys(attrs: list[AttributeDef], values: Mapping[str, Any]) -> list[str]:
    known = {a.key for a in attrs}
    return sorted(k for k in values if k not in known)


def collect_references(
    attrs: list[AttributeDef], values: Mapping[str, Any]
) -> dict[str, set[int | str]]:
    """Referenced ids/logins per attribute type, sub entries included."""
    refs: dict[str, set[int | str]] = {}

    def walk(items: list[AttributeDef], vals: Mapping[str, Any]) -> None:
        for a in items:
            for entry in vals.get(a.key) or []:
                if not isinstance(entry, Mapping):
                    continue
                v = entry.get("v")
                if a.type in REF_TYPES and isinstance(v, int | str) and not isinstance(v, bool):
                    refs.setdefault(a.type, set()).add(v)
                if a.sub and isinstance(entry.get("s"), Mapping):
                    walk(a.sub, entry["s"])

    walk(attrs, values)
    return refs


def collect_reference_paths(
    attrs: list[AttributeDef], values: Mapping[str, Any]
) -> dict[str, dict[int | str, str]]:
    """Referenced ids/logins per attribute **path** (``NIC.Peer``): value -> entry path of its
    first occurrence (``NIC[2].Peer[1]``), which is what an issue should point at."""
    refs: dict[str, dict[int | str, str]] = {}

    def walk(items: list[AttributeDef], vals: Mapping[str, Any], dpre: str, epre: str) -> None:
        for a in items:
            entries = vals.get(a.key)
            if not isinstance(entries, list):
                continue
            for idx, entry in enumerate(entries, start=1):
                if not isinstance(entry, Mapping):
                    continue
                v = entry.get("v")
                if a.type in REF_TYPES and isinstance(v, int | str) and not isinstance(v, bool):
                    refs.setdefault(f"{dpre}{a.key}", {}).setdefault(v, f"{epre}{a.key}[{idx}]")
                if a.sub and isinstance(entry.get("s"), Mapping):
                    walk(a.sub, entry["s"], f"{dpre}{a.key}.", f"{epre}{a.key}[{idx}].")

    walk(attrs, values, "", "")
    return refs


_INT32: Final = 2**31 - 1
_CHUNK: Final = 500
#: Znuny id tables the reference types point at (one IN-list query each)
_ZNUNY_ID_TABLES: Final[dict[str, Any]] = {
    "agent": Users.id,
    "service": Service.id,
    "sla": Sla.id,
    "queue": Queue.id,
    "priority": TicketPriority.id,
    "ticket_type": TicketType.id,
    "state": TicketState.id,
}


def _chunks(items: list[Any]) -> list[list[Any]]:
    return [items[i : i + _CHUNK] for i in range(0, len(items), _CHUNK)]


async def check_references(
    session: AsyncSession,
    refs: Mapping[str, Mapping[int | str, str]],
    *,
    attrs_by_path: Mapping[str, AttributeDef],
    ci_id: int | None,
    user_id: int,
) -> list[ValueIssue]:
    """Do the referenced rows exist (and fit the attribute's class filter)?

    ``refs`` is what :func:`collect_reference_paths` returns. Codes: ``unknown_reference`` and
    ``wrong_class`` (a catalog item of another catalog class, a CI of a class outside
    ``options.class_keys``). One IN-list query per reference type, Znuny and Tiqora tables are
    never joined. Attachments count when bound to ``ci_id`` or staged (unbound) by ``user_id``.
    """
    by_type: dict[str, set[int | str]] = {}
    for path, found in refs.items():
        a = attrs_by_path.get(path)
        if a is not None and a.type in REF_TYPES:
            by_type.setdefault(a.type, set()).update(found)

    # type -> value -> extra info (catalog class / CI class id) or None; absent = unknown
    known: dict[str, dict[int | str, Any]] = {}
    for t, values in by_type.items():
        if t in ("customer_user", "customer_company"):
            strs = sorted(v for v in values if isinstance(v, str))
            col = CustomerUser.login if t == "customer_user" else CustomerCompany.customer_id
            got: dict[int | str, Any] = {}
            for part in _chunks(strs):
                for (val,) in (await session.execute(select(col).where(col.in_(part)))).all():
                    got[str(val).casefold()] = None
            known[t] = got
            continue
        ids = sorted(v for v in values if isinstance(v, int))
        if t in _ZNUNY_ID_TABLES or t == "catalog":
            ids = [i for i in ids if 0 <= i <= _INT32]
        elif any(abs(i) > 2**63 - 1 for i in ids):  # pragma: no cover - normalize bounds this
            ids = [i for i in ids if abs(i) <= 2**63 - 1]
        got = {}
        for part in _chunks(ids):
            if t in _ZNUNY_ID_TABLES:
                col = _ZNUNY_ID_TABLES[t]
                for (val,) in (await session.execute(select(col).where(col.in_(part)))).all():
                    got[int(val)] = None
            elif t == "catalog":
                for val, cls in (
                    await session.execute(
                        select(TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.catalog_class).where(
                            TiqoraCmdbCatalog.id.in_(part)
                        )
                    )
                ).all():
                    got[int(val)] = str(cls)
            elif t == "ci_ref":
                for val, cid in (
                    await session.execute(
                        select(TiqoraCmdbCi.id, TiqoraCmdbCi.class_id).where(
                            TiqoraCmdbCi.id.in_(part)
                        )
                    )
                ).all():
                    got[int(val)] = int(cid)
            elif t == "attachment":
                mine = TiqoraCmdbAttachment.created_by == user_id
                bound: Any = TiqoraCmdbAttachment.ci_id.is_(None) & mine
                if ci_id is not None:
                    bound = or_(bound, TiqoraCmdbAttachment.ci_id == ci_id)
                for (val,) in (
                    await session.execute(
                        select(TiqoraCmdbAttachment.id).where(
                            TiqoraCmdbAttachment.id.in_(part), bound
                        )
                    )
                ).all():
                    got[int(val)] = None
        known[t] = got

    class_keys: dict[int, str] = {}
    class_ids = sorted({c for m in known.get("ci_ref", {}).values() for c in [m] if c is not None})
    if class_ids:
        class_keys = {
            int(i): str(k)
            for i, k in (
                await session.execute(
                    select(TiqoraCmdbClass.id, TiqoraCmdbClass.class_key).where(
                        TiqoraCmdbClass.id.in_(class_ids)
                    )
                )
            ).all()
        }

    issues: list[ValueIssue] = []
    for path in sorted(refs):
        a = attrs_by_path.get(path)
        if a is None or a.type not in REF_TYPES:
            continue
        found = known.get(a.type, {})
        for value, entry_path in refs[path].items():
            key: int | str = value.casefold() if isinstance(value, str) else value
            if key not in found:
                issues.append(
                    ValueIssue(entry_path, "unknown_reference", "Der Verweis existiert nicht.")
                )
                continue
            extra = found[key]
            if a.type == "catalog" and a.options.catalog_class and extra != a.options.catalog_class:
                issues.append(
                    ValueIssue(
                        entry_path, "wrong_class", "Der Eintrag gehört zu einem anderen Katalog."
                    )
                )
            elif (
                a.type == "ci_ref"
                and a.options.class_keys
                and class_keys.get(int(extra)) not in a.options.class_keys
            ):
                issues.append(
                    ValueIssue(
                        entry_path, "wrong_class", "Die Klasse des CI ist hier nicht erlaubt."
                    )
                )
    return issues
