"""Znuny Ticket Attribute Relations (``acl_ticket_attribute_relations``).

CSV matrices map Attribute1 values → allowed Attribute2 values (Znuny
``Kernel::System::TicketAttributeRelations``). Used to restrict form pickers
(e.g. Service → Queue) after group/role and TicketACL filtering.

DB layout is 1:1 with Znuny: ``filename``, ``attribute_1``, ``attribute_2``,
``acl_data`` (CSV text), ``priority``.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.config import AclTicketAttributeRelations

# Map Znuny attribute names (CSV headers / ticket fields) → field-options keys.
_ATTR_TO_FIELD: dict[str, str] = {
    "Queue": "queue",
    "QueueID": "queue",
    "State": "state",
    "StateID": "state",
    "NextState": "state",
    "NextStateID": "state",
    "Priority": "priority",
    "PriorityID": "priority",
    "NewPriority": "priority",
    "NewPriorityID": "priority",
    "Type": "type",
    "TypeID": "type",
    "Service": "service",
    "ServiceID": "service",
    "SLA": "sla",
    "SLAID": "sla",
}


@dataclass(frozen=True, slots=True)
class ParsedAttributeRelations:
    attribute_1: str
    attribute_2: str
    rows: list[dict[str, str]]  # each {attr1: val, attr2: val}


def parse_attribute_relations_csv(data: str) -> ParsedAttributeRelations:
    """Parse Znuny-style CSV: header Attribute1;Attribute2, then value pairs.

    Accepts ``;`` or ``,`` as separator (auto-detect from header). Quotes as
    in standard CSV. BOM is stripped.
    """
    raw = (data or "").lstrip("\ufeff").strip()
    if not raw:
        raise ValueError("empty attribute relations data")
    # Prefer semicolon (Znuny default for DE locales).
    dialect = csv.excel
    sample = raw.splitlines()[0] if raw else ""
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(raw), delimiter=delimiter, quotechar='"', dialect=dialect)
    rows_iter = list(reader)
    if not rows_iter:
        raise ValueError("empty attribute relations data")
    header = [c.strip() for c in rows_iter[0]]
    if len(header) != 2 or not header[0] or not header[1]:
        raise ValueError("attribute relations CSV must have exactly two header columns")
    attr1, attr2 = header[0], header[1]
    data_rows: list[dict[str, str]] = []
    for row in rows_iter[1:]:
        if not row or all(not (c or "").strip() for c in row):
            continue
        v1 = (row[0] if len(row) > 0 else "").strip()
        v2 = (row[1] if len(row) > 1 else "").strip()
        if not v1 and not v2:
            continue
        data_rows.append({attr1: v1, attr2: v2})
    if not data_rows:
        raise ValueError("attribute relations CSV has no data rows")
    return ParsedAttributeRelations(attribute_1=attr1, attribute_2=attr2, rows=data_rows)


def allowed_attribute2_values(
    parsed: ParsedAttributeRelations, *, attribute1_value: str
) -> set[str]:
    """Return allowed Attribute2 values for a given Attribute1 value."""
    key = str(attribute1_value)
    return {
        r[parsed.attribute_2]
        for r in parsed.rows
        if str(r.get(parsed.attribute_1, "")) == key and r.get(parsed.attribute_2) is not None
    }


async def list_relations(session: AsyncSession) -> list[AclTicketAttributeRelations]:
    result = await session.execute(
        select(AclTicketAttributeRelations).order_by(
            AclTicketAttributeRelations.priority, AclTicketAttributeRelations.id
        )
    )
    return list(result.scalars().all())


async def get_relation(
    session: AsyncSession, relation_id: int
) -> AclTicketAttributeRelations | None:
    return await session.get(AclTicketAttributeRelations, relation_id)


def field_key_for_attribute(attr: str) -> str | None:
    """Map CSV attribute name to ticket-field-options key, if known."""
    if attr in _ATTR_TO_FIELD:
        return _ATTR_TO_FIELD[attr]
    # DynamicField_Foo — not in base field maps; callers handle separately.
    if attr.startswith("DynamicField_"):
        return None
    return None


def filter_id_name_map_by_allowed_names(
    items: dict[int, str],
    *,
    allowed_names: set[str],
    allow_ids: bool = True,
) -> dict[int, str]:
    """Keep items whose *name* (or string id) is in *allowed_names*."""
    if not allowed_names:
        return {}
    out: dict[int, str] = {}
    for iid, name in items.items():
        if name in allowed_names or (allow_ids and str(iid) in allowed_names):
            out[iid] = name
    return out


async def apply_attribute_relations_to_field_maps(
    session: AsyncSession,
    maps: dict[str, dict[int, str]],
    *,
    ticket_context: dict[str, Any],
) -> dict[str, dict[int, str]]:
    """Restrict field maps using all TAR matrices ordered by priority.

    *ticket_context* holds current form values under Znuny attribute names
    (e.g. ``{"Service": "Hardware", "Queue": "Support"}``) and/or field keys
    (``service``, ``queue``). Multiple relations chain restrictively: each
    subsequent relation further intersects Attribute2 candidates.
    """
    relations = await list_relations(session)
    if not relations:
        return maps

    # Build lookup of context values by attribute name and field key.
    ctx: dict[str, str] = {}
    for k, v in ticket_context.items():
        if v is None or v == "":
            continue
        ctx[str(k)] = str(v)

    # Also accept field-key aliases (service → Service for matching).
    _field_to_attrs = {
        "queue": ("Queue", "QueueID"),
        "state": ("State", "StateID"),
        "priority": ("Priority", "PriorityID"),
        "type": ("Type", "TypeID"),
        "service": ("Service", "ServiceID"),
        "sla": ("SLA", "SLAID"),
    }
    for field, attrs in _field_to_attrs.items():
        if field in ctx:
            for a in attrs:
                ctx.setdefault(a, ctx[field])

    result = {k: dict(v) for k, v in maps.items()}

    for rel in relations:
        try:
            parsed = parse_attribute_relations_csv(rel.acl_data)
        except ValueError:
            continue
        # Prefer live attribute names on the row (may differ if CSV re-parsed).
        attr1 = rel.attribute_1 or parsed.attribute_1
        attr2 = rel.attribute_2 or parsed.attribute_2
        # Resolve context value for attr1.
        a1_val = ctx.get(attr1)
        if a1_val is None:
            # Try ID form: QueueID when attr is Queue, or the attr itself if *ID.
            a1_val = ctx.get(attr1) if attr1.endswith("ID") else ctx.get(attr1 + "ID")
        if a1_val is None:
            continue
        # If context used id but CSV uses names, try resolve from maps.
        field1 = field_key_for_attribute(attr1)
        if field1 and field1 in result and a1_val.isdigit():
            iid = int(a1_val)
            if iid in result[field1]:
                a1_val = result[field1][iid]

        allowed = allowed_attribute2_values(
            ParsedAttributeRelations(attr1, attr2, parsed.rows),
            attribute1_value=a1_val,
        )
        field2 = field_key_for_attribute(attr2)
        if field2 is None or field2 not in result:
            continue
        if not allowed:
            result[field2] = {}
            continue
        result[field2] = filter_id_name_map_by_allowed_names(result[field2], allowed_names=allowed)

    return result
