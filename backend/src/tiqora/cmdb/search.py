"""CI search (D6/C7): filter fields, SQL compiler, list search and typeahead.

Everything is scoped to the classes the caller may read. Attribute conditions run on the
index table ``tiqora_cmdb_ci_attr`` (only attributes marked ``searchable`` are indexed) as
``EXISTS`` sub-selects; text is compared on the casefolded ``value_key``. Only ``tiqora_*``
tables are involved, so the joins are safe for the prod collation mix.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    exists,
    false,
    func,
    not_,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from tiqora.cmdb.access import CmdbAccess, readable_class_ids
from tiqora.cmdb.definition import AttributeDef, parse_definition_json
from tiqora.cmdb.schemas import CiListItemOut, CiPage, CiRefOut, CiSearchIn
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.db.tiqora.models import (
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbCiAttr,
    TiqoraCmdbClass,
    TiqoraCmdbClassDefinition,
)
from tiqora.filters.catalog import FilterError, FilterFieldSpec, validate_filter
from tiqora.filters.model import AllOf, AnyOf, Condition, Filter, FilterNode, Not
from tiqora.filters.predicates import (
    NEGATED_OPS,
    column_predicate,
    datetime_predicate,
    like_escape,
    positive_predicate,
)
from tiqora.jobs.service import utc_now

#: ``total`` never counts further than this (a ``COUNT(*)`` over 100k CIs is not a list query)
COUNT_CAP: Final = 10000
MAX_Q_TOKENS: Final = 5
_KEY_MAX: Final = 191
_NUM_LIMIT: Final = Decimal(10) ** 24

_CI = TiqoraCmdbCi
_CLS = TiqoraCmdbClass
_ATTR = TiqoraCmdbCiAttr
_DEP = aliased(TiqoraCmdbCatalog, name="cmdb_dep")
_INC = aliased(TiqoraCmdbCatalog, name="cmdb_inc")

#: how an attribute is indexed and compared
_TEXT_TYPES: Final = frozenset(
    {"text", "textarea", "url", "email", "ip", "mac", "customer_user", "customer_company"}
)
_ID_TYPES: Final = frozenset(
    {"ci_ref", "agent", "service", "sla", "queue", "priority", "ticket_type", "state", "attachment"}
)

#: filter keys that map onto a column of the CI list query
_SORT_COLUMNS: Final[dict[str, Any]] = {
    "number": _CI.number,
    "name": _CI.name,
    "class": _CLS.name,
    "changed_at": _CI.changed_at,
    "deployment_state": _DEP.name,
    "incident_state": _INC.name,
}


@dataclass(frozen=True, slots=True)
class FieldSet:
    """Filter fields of one search plus how each attribute field is compared."""

    specs: list[FilterFieldSpec]
    #: ``attr.<path>`` -> ``text | int | decimal | id | date | catalog``
    kinds: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> FilterFieldSpec | None:
        for spec in self.specs:
            if spec.key == key:
                return spec
        return None


def _attr_kind(a: AttributeDef) -> str | None:
    if a.type in _TEXT_TYPES:
        return "text"
    if a.type == "integer":
        return "int"
    if a.type in ("decimal", "money"):
        return "decimal"
    if a.type in ("date", "datetime"):
        return "date"
    if a.type == "catalog":
        return "catalog"
    if a.type in _ID_TYPES:
        return "id"
    return None  # group: carries no value of its own


def _walk_searchable(
    attrs: Sequence[AttributeDef], prefix: str, label: str
) -> list[tuple[str, str, AttributeDef]]:
    out: list[tuple[str, str, AttributeDef]] = []
    for a in attrs:
        path = f"{prefix}{a.key}"
        name = f"{label}{a.name}"
        if a.searchable and _attr_kind(a) is not None:
            out.append((path, name, a))
        out.extend(_walk_searchable(a.sub, f"{path}.", f"{name} › "))
    return out


async def _load_fields(
    session: AsyncSession, class_id: int | None, readable: Sequence[int] | None
) -> FieldSet:
    class_stmt = select(_CLS.class_key).order_by(_CLS.position, _CLS.class_key)
    if readable is not None:
        class_stmt = class_stmt.where(_CLS.id.in_(list(readable)))
    class_keys = tuple((await session.execute(class_stmt)).scalars().all())
    catalog = (
        await session.execute(
            select(
                TiqoraCmdbCatalog.id,
                TiqoraCmdbCatalog.catalog_class,
                TiqoraCmdbCatalog.functionality,
            ).order_by(TiqoraCmdbCatalog.position, TiqoraCmdbCatalog.id)
        )
    ).all()
    dep_ids = tuple(str(r[0]) for r in catalog if r[1] == DEPLOYMENT_STATE)
    inc_ids = tuple(str(r[0]) for r in catalog if r[1] == INCIDENT_STATE)
    dep_func = tuple(sorted({str(r[2]) for r in catalog if r[1] == DEPLOYMENT_STATE and r[2]}))
    inc_func = tuple(sorted({str(r[2]) for r in catalog if r[1] == INCIDENT_STATE and r[2]}))
    p = "cmdb.filter.fields."
    specs = [
        FilterFieldSpec("number", "string", label_key=p + "number"),
        FilterFieldSpec("name", "string", label_key=p + "name"),
        FilterFieldSpec("class", "enum", label_key=p + "class", enum_values=class_keys),
        FilterFieldSpec(
            "deployment_state", "enum", label_key=p + "deployment_state", enum_values=dep_ids
        ),
        FilterFieldSpec(
            "deployment_functionality",
            "enum",
            label_key=p + "deployment_functionality",
            enum_values=dep_func,
        ),
        FilterFieldSpec(
            "incident_state", "enum", label_key=p + "incident_state", enum_values=inc_ids
        ),
        FilterFieldSpec(
            "incident_functionality",
            "enum",
            label_key=p + "incident_functionality",
            enum_values=inc_func,
        ),
        FilterFieldSpec("created_at", "datetime", label_key=p + "created_at"),
        FilterFieldSpec("changed_at", "datetime", label_key=p + "changed_at"),
    ]
    kinds: dict[str, str] = {}
    if class_id is not None:
        raw = (
            await session.execute(
                select(TiqoraCmdbClassDefinition.attributes)
                .join(_CLS, _CLS.current_definition_id == TiqoraCmdbClassDefinition.id)
                .where(_CLS.id == class_id)
            )
        ).scalar_one_or_none()
        attrs = [] if raw is None else parse_definition_json(str(raw))
        by_class: dict[str, list[str]] = {}
        for r in catalog:
            by_class.setdefault(str(r[1]), []).append(str(r[0]))
        for path, label, a in _walk_searchable(attrs, "", ""):
            kind = _attr_kind(a)
            assert kind is not None
            key = f"attr.{path}"
            kinds[key] = kind
            if kind == "text":
                spec = FilterFieldSpec(key, "string", label=label)
            elif kind == "int":
                spec = FilterFieldSpec(key, "int", label=label)
            elif kind == "decimal":
                # range filters on money/decimal are a known limitation (no new field type)
                spec = FilterFieldSpec(
                    key,
                    "string",
                    label=label,
                    ops=frozenset({"eq", "neq", "is_empty", "is_not_empty"}),
                )
            elif kind == "date":
                spec = FilterFieldSpec(key, "datetime", label=label)
            elif kind == "catalog":
                spec = FilterFieldSpec(
                    key,
                    "enum",
                    label=label,
                    enum_values=tuple(by_class.get(a.options.catalog_class or "", [])),
                )
            else:
                spec = FilterFieldSpec(key, "id", label=label)
            specs.append(spec)
    return FieldSet(specs, kinds)


async def cmdb_filter_fields(
    session: AsyncSession, class_id: int | None, *, readable_class_ids: Sequence[int] | None = None
) -> list[FilterFieldSpec]:
    """Fields of the CI filter; ``attr.<path>`` fields only for one class (D6).

    ``readable_class_ids`` limits the ``class`` enum to the classes the caller may read."""
    return (await _load_fields(session, class_id, readable_class_ids)).specs


# --- compiler ---------------------------------------------------------------------------


def _fold(value: str) -> str:
    return value.casefold()[:_KEY_MAX]


def _decimal(value: Any, path: str) -> Decimal:
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        raise FilterError("Bitte eine Zahl angeben.", path=path) from None
    if not d.is_finite():
        raise FilterError("Bitte eine Zahl angeben.", path=path)
    return d


def _num_cond(cond: Condition, path: str) -> Condition:
    """Condition with its values as ``Decimal`` (the index column is ``NUMERIC``)."""
    v = cond.value
    if cond.op in ("in", "not_in"):
        out: Any = [_decimal(x, path) for x in v]
    elif cond.op == "between":
        out = {"from": _decimal(v["from"], path), "to": _decimal(v["to"], path)}
    elif v is None:
        out = None
    else:
        out = _decimal(v, path)
    return cond.model_copy(update={"value": out})


def _text_positive(cond: Condition) -> ColumnElement[bool]:
    col = _ATTR.value_key
    op, v = cond.op, cond.value
    if op == "eq":
        return col == _fold(v)
    if op == "in":
        return col.in_([_fold(x) for x in v])
    if op == "contains":
        return col.like(f"%{like_escape(v.casefold())}%", escape="\\")
    if op == "starts_with":
        return col.like(f"{like_escape(v.casefold())}%", escape="\\")
    raise FilterError(f"„{op}“ ist hier nicht möglich.", path="where")  # pragma: no cover


def _attr_positive(
    kind: str, cond: Condition, vpath: str, now: datetime
) -> ColumnElement[bool] | None:
    """The "has an entry that ..." predicate, ``None`` when no entry can match (out of range)."""
    if kind == "text":
        return _text_positive(cond)
    if kind == "date":
        pred: ColumnElement[bool] = datetime_predicate(
            _ATTR.value_date, cond, vpath, now, epoch=False
        )
        return pred
    if kind == "catalog":  # enum values are catalog ids as strings
        ids = cond.model_copy(
            update={
                "value": [int(x) for x in cond.value]
                if cond.op in ("in", "not_in")
                else int(cond.value)
            }
        )
        return positive_predicate(_ATTR.value_num, "id", _num_cond(ids, vpath), vpath, now)
    num = _num_cond(cond, vpath)
    if kind == "decimal" and isinstance(num.value, Decimal) and abs(num.value) >= _NUM_LIMIT:
        return None
    result: ColumnElement[bool] = positive_predicate(
        _ATTR.value_num, "id" if kind == "id" else "int", num, vpath, now
    )
    return result


def _nonempty(kind: str) -> ColumnElement[bool]:
    if kind == "text":
        return and_(_ATTR.value_key.is_not(None), _ATTR.value_key != "")
    if kind == "date":
        return _ATTR.value_date.is_not(None)
    return _ATTR.value_num.is_not(None)


def _attr_condition(
    fields: FieldSet, cond: Condition, vpath: str, now: datetime
) -> ColumnElement[bool]:
    kind = fields.kinds[cond.field]
    apath = cond.field.removeprefix("attr.")
    base = and_(_ATTR.ci_id == _CI.id, _ATTR.attr_path == apath)

    def has(pred: ColumnElement[bool] | None) -> ColumnElement[bool]:
        if pred is None:
            return false()
        return exists().where(base, pred)

    if cond.op == "is_empty":
        return not_(has(_nonempty(kind)))
    if cond.op == "is_not_empty":
        return has(_nonempty(kind))
    if cond.op in NEGATED_OPS:
        positive = cond.model_copy(update={"op": NEGATED_OPS[cond.op]})
        return not_(has(_attr_positive(kind, positive, vpath, now)))
    return has(_attr_positive(kind, cond, vpath, now))


def _to_ints(cond: Condition) -> Condition:
    v = cond.value
    conv: Any = [int(x) for x in v] if cond.op in ("in", "not_in") else int(v)
    return cond.model_copy(update={"value": conv})


def _condition(
    fields: FieldSet, spec: FilterFieldSpec, cond: Condition, path: str, now: datetime
) -> ColumnElement[bool]:
    vpath = f"{path}.value"
    key = cond.field
    if key.startswith("attr."):
        return _attr_condition(fields, cond, vpath, now)
    if key in ("number", "name"):
        col: Any = _CI.number if key == "number" else _CI.name
        return column_predicate(col, "string", cond, vpath, now)
    if key == "class":
        return column_predicate(_CLS.class_key, "enum", cond, vpath, now)
    if key == "deployment_state":
        return column_predicate(_CI.deployment_state_id, "id", _to_ints(cond), vpath, now)
    if key == "incident_state":
        return column_predicate(_CI.cur_incident_state_id, "id", _to_ints(cond), vpath, now)
    if key == "deployment_functionality":
        return column_predicate(_DEP.functionality, "enum", cond, vpath, now)
    if key == "incident_functionality":
        return column_predicate(_INC.functionality, "enum", cond, vpath, now)
    if key in ("created_at", "changed_at"):
        dcol: Any = _CI.created_at if key == "created_at" else _CI.changed_at
        return column_predicate(dcol, "datetime", cond, vpath, now)
    raise FilterError(f"Unbekanntes Feld „{key}“.", path=f"{path}.field")  # pragma: no cover


def _node(fields: FieldSet, node: FilterNode, path: str, now: datetime) -> ColumnElement[bool]:
    if isinstance(node, Condition):
        spec = fields.get(node.field)
        if spec is None:
            raise FilterError(f"Unbekanntes Feld „{node.field}“.", path=f"{path}.field")
        return _condition(fields, spec, node, path, now)
    if isinstance(node, AllOf):
        return and_(*[_node(fields, c, f"{path}.all[{i}]", now) for i, c in enumerate(node.all)])
    if isinstance(node, AnyOf):
        return or_(*[_node(fields, c, f"{path}.any[{i}]", now) for i, c in enumerate(node.any)])
    assert isinstance(node, Not)
    return not_(_node(fields, node.not_, f"{path}.not", now))


def _q_predicate(q: str) -> list[ColumnElement[bool]]:
    """One predicate per token (at most five): number or name contains it, or an indexed
    attribute value starts with it. Tokens are casefolded like the index (``value_key``)."""
    out: list[ColumnElement[bool]] = []
    for token in q.split()[:MAX_Q_TOKENS]:
        low = like_escape(token.lower())
        key = like_escape(token.casefold()[:_KEY_MAX])
        out.append(
            or_(
                func.lower(_CI.number).like(f"%{low}%", escape="\\"),
                func.lower(_CI.name).like(f"%{low}%", escape="\\"),
                _CI.id.in_(select(_ATTR.ci_id).where(_ATTR.value_key.like(f"{key}%", escape="\\"))),
            )
        )
    return out


def compile_ci_search(
    fields: FieldSet,
    flt: Filter | None,
    *,
    q: str | None,
    class_ids: Sequence[int],
    readable_class_ids: Sequence[int],
    now: datetime | None = None,
) -> Select[Any]:
    """The CI list query without ordering and paging.

    Scope = readable classes, narrowed to ``class_ids`` when given. Conditions are validated
    by the caller (:func:`search_cis`); this raises :class:`FilterError` for values that cannot
    be compiled."""
    readable = set(readable_class_ids)
    scope = sorted(readable & set(class_ids) if class_ids else readable)
    stmt = (
        select(
            _CI.id,
            _CI.number,
            _CI.name,
            _CLS.class_key,
            _CLS.name.label("class_name"),
            _DEP.name.label("deployment_state"),
            _DEP.functionality.label("deployment_functionality"),
            _INC.name.label("incident_state"),
            _INC.functionality.label("incident_functionality"),
            _CI.changed_at,
        )
        .select_from(_CI)
        .join(_CLS, _CLS.id == _CI.class_id)
        .join(_DEP, _DEP.id == _CI.deployment_state_id)
        .join(_INC, _INC.id == _CI.cur_incident_state_id)
    )
    stmt = stmt.where(_CI.class_id.in_(scope) if scope else false())
    if q and q.strip():
        for pred in _q_predicate(q.strip()):
            stmt = stmt.where(pred)
    if flt is not None and flt.where is not None:
        stmt = stmt.where(_node(fields, flt.where, "where", now or utc_now()))
    return stmt


async def search_cis(session: AsyncSession, access: CmdbAccess, body: CiSearchIn) -> CiPage:
    readable = await readable_class_ids(session, access)
    scope = sorted(set(readable) & set(body.class_ids)) if body.class_ids else readable
    flt = body.filter
    has_attr = flt is not None and _uses_attr_field(flt)
    if has_attr and len(set(body.class_ids)) != 1:
        raise FilterError("Attributfilter brauchen genau eine Klasse", path="where")
    if not scope:
        return CiPage(items=[], total=0, total_capped=False)
    class_id = body.class_ids[0] if has_attr else None
    fields = await _load_fields(session, class_id, readable)
    if flt is not None:
        validate_filter(flt, fields.get)
    stmt = compile_ci_search(
        fields, flt, q=body.q, class_ids=body.class_ids, readable_class_ids=readable
    )
    sort_col = _SORT_COLUMNS[body.sort]
    primary = sort_col.desc() if body.order == "desc" else sort_col.asc()
    rows = (
        await session.execute(
            stmt.order_by(primary, _CI.id.asc()).offset(body.offset).limit(body.limit)
        )
    ).all()
    items = [
        CiListItemOut(
            id=int(r.id),
            number=r.number,
            name=r.name,
            class_key=r.class_key,
            class_name=r.class_name,
            deployment_state=r.deployment_state,
            deployment_functionality=r.deployment_functionality,
            incident_state=r.incident_state,
            incident_functionality=r.incident_functionality,
            changed_at=r.changed_at,
        )
        for r in rows
    ]
    if body.offset == 0 and len(items) < body.limit:
        return CiPage(items=items, total=len(items), total_capped=False)
    counted = (
        await session.execute(
            select(func.count()).select_from(
                stmt.with_only_columns(_CI.id, maintain_column_froms=True)
                .limit(COUNT_CAP + 1)
                .subquery()
            )
        )
    ).scalar_one()
    return CiPage(items=items, total=min(int(counted), COUNT_CAP), total_capped=counted > COUNT_CAP)


def _uses_attr_field(flt: Filter) -> bool:
    def walk(node: FilterNode | None) -> bool:
        if node is None:
            return False
        if isinstance(node, Condition):
            return node.field.startswith("attr.")
        if isinstance(node, AllOf):
            return any(walk(c) for c in node.all)
        if isinstance(node, AnyOf):
            return any(walk(c) for c in node.any)
        return walk(node.not_)

    return walk(flt.where)


async def lookup_cis(
    session: AsyncSession,
    access: CmdbAccess,
    q: str,
    *,
    class_keys: Sequence[str] = (),
    limit: int = 20,
) -> list[CiRefOut]:
    """Typeahead for CI pickers: an exact number match first, then the ``q`` search by name."""
    limit = max(1, min(int(limit), 100))
    readable = await readable_class_ids(session, access)
    if class_keys:
        keep = (
            (await session.execute(select(_CLS.id).where(_CLS.class_key.in_(list(class_keys)))))
            .scalars()
            .all()
        )
        readable = sorted(set(readable) & {int(i) for i in keep})
    if not readable:
        return []
    fields = FieldSet([])
    base = compile_ci_search(fields, None, q=q, class_ids=[], readable_class_ids=readable)

    def ref(r: Any) -> CiRefOut:
        return CiRefOut(
            id=int(r.id),
            number=r.number,
            name=r.name,
            class_key=r.class_key,
            class_name=r.class_name,
        )

    out: list[CiRefOut] = []
    text = q.strip()
    if text:
        exact = compile_ci_search(fields, None, q=None, class_ids=[], readable_class_ids=readable)
        exact = exact.where(func.lower(_CI.number) == text.lower()).limit(limit)
        out = [ref(r) for r in (await session.execute(exact)).all()]
    if len(out) < limit:
        seen = [r.id for r in out]
        stmt = base.order_by(_CI.name.asc(), _CI.id.asc()).limit(limit)
        if seen:
            stmt = stmt.where(_CI.id.not_in(seen))
        out.extend(ref(r) for r in (await session.execute(stmt)).all()[: limit - len(out)])
    return out


__all__ = [
    "COUNT_CAP",
    "FieldSet",
    "cmdb_filter_fields",
    "compile_ci_search",
    "lookup_cis",
    "search_cis",
]
