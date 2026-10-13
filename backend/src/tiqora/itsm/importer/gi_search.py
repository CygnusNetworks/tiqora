"""Search backends of GI ``ConfigItemSearch`` (plan D9, Task 22).

Two backends answer the same question ("which CIs of one class match these Znuny search
parameters"): :func:`search_tiqora` narrows with the Tiqora CMDB search (cmdb's attribute index)
and then applies Znuny's wildcard rules exactly on the rows it loaded; :func:`search_mode_a`
scans Znuny's own CMDB through the :class:`ImportSource` port and is bounded (ruling F18: hard
result limit and a time budget, because a GI call cannot become a job).

Znuny semantics kept (``ITSMConfigItem.pm`` ``ConfigItemSearchExtended`` / ``Version.pm``
``VersionSearch``, 6.5.4 = 7.3.5): ``Number``/``Name`` patterns are case-insensitive SQL
``LIKE`` with ``*`` as wildcard anywhere (no wildcard = equality); states are the *current*
states of the CI; ``...NewerDate`` is ``>=``, ``...OlderDate`` is ``<=``; ``PreviousVersionSearch``
matches ``Name`` against every version, not only the current one.

This module imports only the ports and the cmdb tables, never :mod:`facades` (the facade
imports this module).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from itertools import product
from typing import Any, Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.definition import dump_definition_json
from tiqora.db.tiqora.models import (
    TiqoraCmdbCi,
    TiqoraCmdbCiVersion,
    TiqoraCmdbCiVersionArchive,
)
from tiqora.filters.catalog import FilterError
from tiqora.itsm.importer.ports import CiQuery, CmdbClassView, CmdbReadPort, ImportSource
from tiqora.itsm.znuny_format.definition import definition_format, parse_definition_with_issues
from tiqora.itsm.znuny_format.xmldata import xmldata_to_attributes
from tiqora.reporting.sql import ReportQueryTimeout, statement_timeout

logger = structlog.get_logger(__name__)

#: Mode A (ruling F18): at most this many ids come back, whatever ``Limit`` says
MODE_A_MAX_RESULTS: Final = 1000
#: Mode A: wall-clock and statement budget of one search, in seconds
MODE_A_BUDGET_SECONDS: Final = 30
#: Tiqora mode: safety bound on the candidates one search loads before sorting
TIQORA_SCAN_CAP: Final = 10000
_CHUNK: Final = 500
_PAGE: Final = 200
_MAX_COMBINATIONS: Final = 20


class GiSearchError(Exception):
    """A search that cannot be answered; ``code`` is the suffix after ``ConfigItemSearch.``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class GiRow:
    """What ordering and the post-filters need of one CI."""

    id: int
    number: str
    name: str
    class_id: int
    deployment_state_id: int
    incident_state_id: int
    created_at: datetime
    created_by: int
    changed_at: datetime
    changed_by: int


@dataclass(frozen=True, slots=True)
class GiCriteria:
    """The validated search parameters of one call (all of them optional but the class)."""

    class_name: str
    #: alternatives (Znuny accepts an array of numbers: exact IN list)
    numbers: list[str] | None = None
    name: str | None = None
    deployment_states: list[str] | None = None
    incident_states: list[str] | None = None
    #: ``Key`` / ``Key::Sub`` -> value patterns (alternatives)
    attributes: dict[str, list[str]] = field(default_factory=dict)
    created_newer: datetime | None = None
    created_older: datetime | None = None
    changed_newer: datetime | None = None
    changed_older: datetime | None = None
    previous_versions: bool = False


# --------------------------------------------------------------------------- wildcard helpers


def like_regex(pattern: str) -> re.Pattern[str]:
    """Znuny ``LIKE``: ``*`` (any run) is the only wildcard, the match is case-insensitive."""
    parts = re.split(r"\*+", pattern)
    return re.compile(".*".join(re.escape(p) for p in parts), re.IGNORECASE | re.DOTALL)


def _matches(patterns: Iterable[re.Pattern[str]], value: str) -> bool:
    return any(p.fullmatch(value) for p in patterns)


def _loose(pattern: str) -> str | None:
    """A superset filter for the database: ``*<longest literal piece>*`` (``None``: no literal)."""
    pieces = [p for p in re.split(r"\*+", pattern) if p]
    return f"*{max(pieces, key=len)}*" if pieces else None


def _db_text(pattern: str) -> str:
    """cmdb's text index knows leading/trailing ``*`` only; an inner ``*`` becomes "contains the
    longest literal piece" (a superset: such a value can match more than Znuny's pattern)."""
    if "*" in pattern.strip("*"):
        return _loose(pattern) or pattern
    return pattern


def _in_range(row: GiRow, crit: GiCriteria) -> bool:
    for value, newer, older in (
        (row.created_at, crit.created_newer, crit.created_older),
        (row.changed_at, crit.changed_newer, crit.changed_older),
    ):
        if newer is not None and value < newer:
            return False
        if older is not None and value > older:
            return False
    return True


# --------------------------------------------------------------------------- Tiqora backend


async def _rows(session: AsyncSession, ids: Sequence[int]) -> list[GiRow]:
    out: list[GiRow] = []
    for start in range(0, len(ids), _CHUNK):
        chunk = list(ids[start : start + _CHUNK])
        found = await session.execute(
            select(
                TiqoraCmdbCi.id,
                TiqoraCmdbCi.number,
                TiqoraCmdbCi.name,
                TiqoraCmdbCi.class_id,
                TiqoraCmdbCi.deployment_state_id,
                TiqoraCmdbCi.cur_incident_state_id,
                TiqoraCmdbCi.created_at,
                TiqoraCmdbCi.created_by,
                TiqoraCmdbCi.changed_at,
                TiqoraCmdbCi.changed_by,
            ).where(TiqoraCmdbCi.id.in_(chunk))
        )
        out.extend(GiRow(*tuple(r)) for r in found.all())
    return out


async def _ids_with_version_name(
    session: AsyncSession, ids: Sequence[int], patterns: Sequence[re.Pattern[str]]
) -> set[int]:
    """CI ids of which *any* version (hot or archived) has a name matching ``patterns``."""
    hit: set[int] = set()
    for model in (TiqoraCmdbCiVersion, TiqoraCmdbCiVersionArchive):
        for start in range(0, len(ids), _CHUNK):
            chunk = list(ids[start : start + _CHUNK])
            found = await session.execute(
                select(model.ci_id, model.name).where(model.ci_id.in_(chunk))
            )
            hit.update(int(c) for c, n in found.all() if _matches(patterns, str(n)))
    return hit


def _leaf_paths(
    attributes: Sequence[Mapping[str, Any]], prefix: str = ""
) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for attr in attributes:
        path = f"{prefix}{attr['key']}"
        out[path] = attr
        out.update(_leaf_paths(list(attr.get("sub") or []), f"{path}::"))
    return out


async def search_tiqora(
    session: AsyncSession,
    reader: CmdbReadPort,
    cls: CmdbClassView,
    crit: GiCriteria,
    *,
    cap: int = TIQORA_SCAN_CAP,
) -> list[GiRow]:
    """Rows of the matching CIs of ``cls`` (unordered). ``GiSearchError`` for attribute filters
    cmdb's index cannot serve."""
    name_patterns = [like_regex(crit.name)] if crit.name else []
    number_patterns = [like_regex(n) for n in crit.numbers or []]
    slack = timedelta(seconds=1)

    query_base = CiQuery(
        number=_loose(crit.numbers[0]) if crit.numbers and len(crit.numbers) == 1 else None,
        name=None if crit.previous_versions or not crit.name else _loose(crit.name),
        class_keys=[cls.key],
        deployment_states=crit.deployment_states,
        incident_states=crit.incident_states,
        created_after=crit.created_newer - slack if crit.created_newer else None,
        created_before=crit.created_older + slack if crit.created_older else None,
        changed_after=crit.changed_newer - slack if crit.changed_newer else None,
        changed_before=crit.changed_older + slack if crit.changed_older else None,
    )
    ids: list[int] = []
    for attributes in await _attribute_combinations(reader, cls, crit):
        query = replace(query_base, attributes=attributes or None)
        try:
            found = await reader.search_cis(query, class_ids={cls.id}, limit=cap, offset=0)
        except FilterError as exc:
            raise GiSearchError(
                "InvalidParameter", "ConfigItemSearch: ConfigItem->CIXMLData cannot be searched"
            ) from exc
        for ci_id in found:
            if ci_id not in ids:
                ids.append(ci_id)
    if len(ids) >= cap:
        logger.warning("gi_search_truncated", cap=cap)
    rows = [r for r in await _rows(session, ids[:cap]) if _in_range(r, crit)]
    if number_patterns:
        rows = [r for r in rows if _matches(number_patterns, r.number)]
    if name_patterns:
        if crit.previous_versions:
            keep = await _ids_with_version_name(session, [r.id for r in rows], name_patterns)
            rows = [r for r in rows if r.id in keep]
        else:
            rows = [r for r in rows if _matches(name_patterns, r.name)]
    return rows


async def _attribute_combinations(
    reader: CmdbReadPort, cls: CmdbClassView, crit: GiCriteria
) -> list[dict[str, str]]:
    """One ``attributes`` dict per combination of alternatives (``[{}]`` without filters).

    Unknown keys cannot match anything (Znuny: no XML row), known but not searchable ones are an
    error: cmdb's index holds searchable attributes only.
    """
    if not crit.attributes:
        return [{}]
    known = _leaf_paths(list(cls.schema.get("attributes", [])))
    choices: list[list[tuple[str, str]]] = []
    for path, patterns in sorted(crit.attributes.items()):
        attr = known.get(path)
        if attr is None:
            return []
        if not attr.get("searchable"):
            raise GiSearchError(
                "InvalidParameter",
                f"ConfigItemSearch: ConfigItem->CIXMLData->{path} is not searchable",
            )
        values: list[tuple[str, str]] = []
        for pattern in patterns:
            if attr.get("type") == "catalog":
                catalog_class = (attr.get("options") or {}).get("catalog_class") or ""
                item = await reader.catalog_item(catalog_class, pattern)
                if item is None or item.name != pattern:
                    raise GiSearchError(
                        "InvalidParameter",
                        f"ConfigItemSearch: ConfigItem->CIXMLData->{path} parameter value"
                        f" is not a valid for General Catalog '{catalog_class}'!",
                    )
                values.append((path.replace("::", "."), str(item.id)))
            elif attr.get("type") == "integer":
                if not re.fullmatch(r"-?[0-9]+", pattern):
                    raise GiSearchError(
                        "InvalidParameter",
                        f"ConfigItemSearch: ConfigItem->CIXMLData->{path} parameter value"
                        " is not a valid Integer or out of range!",
                    )
                values.append((path.replace("::", "."), pattern))
            elif attr.get("type") in ("text", "textarea"):
                values.append((path.replace("::", "."), _db_text(pattern)))
            else:
                values.append((path.replace("::", "."), pattern))
        if not values:
            return []
        choices.append(values)
    combos = list(product(*choices))
    if len(combos) > _MAX_COMBINATIONS:
        raise GiSearchError(
            "InvalidParameter", "ConfigItemSearch: ConfigItem->CIXMLData has too many alternatives"
        )
    return [dict(combo) for combo in combos]


# --------------------------------------------------------------------------- Mode A backend


def _walk_attribute_values(attributes: Mapping[str, Any], path: tuple[str, ...]) -> list[Any]:
    """Values at ``Key::Sub`` of cmdb-shaped attributes (``{"Key": [{"v":..,"s":{..}}]}``)."""
    entries = attributes.get(path[0])
    if not isinstance(entries, list):
        return []
    out: list[Any] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if len(path) == 1:
            out.append(entry.get("v"))
        elif isinstance(entry.get("s"), Mapping):
            out.extend(_walk_attribute_values(entry["s"], path[1:]))
    return out


def _catalog_paths(attrs: Sequence[Mapping[str, Any]], prefix: str = "") -> set[str]:
    out: set[str] = set()
    for attr in attrs:
        path = f"{prefix}{attr['key']}"
        if attr.get("type") == "catalog":
            out.add(path)
        out |= _catalog_paths(list(attr.get("sub") or []), f"{path}::")
    return out


class _Identity:
    """Mode A keeps Znuny ids."""

    def ci(self, znuny_ci_id: str) -> int | None:
        return int(znuny_ci_id) if znuny_ci_id.isdigit() else None

    def catalog(self, znuny_gc_id: str) -> int | None:
        return int(znuny_gc_id) if znuny_gc_id.isdigit() else None


async def search_mode_a(
    session: AsyncSession,
    source: ImportSource,
    crit: GiCriteria,
    *,
    user_id: int,
    budget_seconds: float | None = None,
    now: Callable[[], float] = time.monotonic,
) -> list[GiRow]:
    """Scan Znuny's CMDB for ``crit`` (Mode A). At most ``MODE_A_MAX_RESULTS`` rows (the caller
    sorts them and applies ``Limit``). ``GiSearchError`` code ``InvalidParameter`` (unknown
    class or state), ``AccessDenied`` or ``Timeout`` (the budget ran out; nothing partial)."""
    budget = MODE_A_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    deadline = now() + budget
    cap = MODE_A_MAX_RESULTS

    catalog = await source.catalog_items()
    by_class = {c.name: c for c in catalog if c.catalog_class == "ITSM::ConfigItem::Class"}
    cls = by_class.get(crit.class_name)
    if cls is None:
        raise GiSearchError(
            "InvalidParameter", "ConfigItemSearch: ConfigItem->Class parameter is invalid!"
        )
    from tiqora.permissions.engine import PermissionEngine

    if user_id == 0 or cls.permission_group is None:
        raise GiSearchError("AccessDenied", "ConfigItemSearch: Can not search configuration items!")
    if cls.permission_group not in await PermissionEngine(session).groups_for_permission(
        user_id, "ro"
    ):
        raise GiSearchError("AccessDenied", "ConfigItemSearch: Can not search configuration items!")

    def state_ids(names: list[str] | None, catalog_class: str) -> set[int] | None:
        if names is None:
            return None
        by_name = {c.name: c.id for c in catalog if c.catalog_class == catalog_class}
        return {by_name[n] for n in names if n in by_name}

    depl = state_ids(crit.deployment_states, "ITSM::ConfigItem::DeploymentState")
    inci = state_ids(crit.incident_states, "ITSM::Core::IncidentState")
    for field_name, wanted, found in (
        ("DeplStates", crit.deployment_states, depl),
        ("InciStates", crit.incident_states, inci),
    ):
        if wanted is not None and found is not None and len(found) != len(set(wanted)):
            raise GiSearchError(
                "InvalidParameter",
                f"ConfigItemSearch: ConfigItem->{field_name} parameter is invalid!",
            )
    catalog_names = {c.id: c.name for c in catalog}
    number_patterns = [like_regex(n) for n in crit.numbers or []]
    name_patterns = [like_regex(crit.name)] if crit.name else []
    attr_patterns = {
        tuple(path.split("::")): [like_regex(p) for p in patterns]
        for path, patterns in crit.attributes.items()
    }
    need_version = bool(name_patterns or attr_patterns)
    definitions = {d.id: d for d in await source.class_definitions()}
    schemas: dict[int, tuple[dict[str, Any], set[str]]] = {}

    def schema_of(definition_id: int) -> tuple[dict[str, Any], set[str]] | None:
        if definition_id not in schemas:
            definition = definitions.get(definition_id)
            if definition is None:
                return None
            parsed = parse_definition_with_issues(definition.raw, definition_format(definition.raw))
            attrs = json.loads(dump_definition_json(list(parsed.result.attributes)))
            schemas[definition_id] = ({"attributes": attrs}, _catalog_paths(attrs))
        return schemas[definition_id]

    out: list[GiRow] = []
    after_id = 0

    async def scan() -> None:
        nonlocal after_id
        while len(out) < cap:
            if now() > deadline:
                raise GiSearchError("Timeout", "ConfigItemSearch: the search took too long")
            ids = await source.ci_ids_page(after_id, _PAGE)
            if not ids:
                return
            after_id = max(ids)
            cis = [
                c
                for c in await source.cis(ids)
                if c.class_id == cls.id
                and (depl is None or (c.deployment_state_id or 0) in depl)
                and (inci is None or (c.incident_state_id or 0) in inci)
            ]
            versions = await source.versions([c.id for c in cis]) if cis and need_version else {}
            for ci in cis:
                row = GiRow(
                    ci.id,
                    ci.number,
                    "",
                    ci.class_id,
                    ci.deployment_state_id or 0,
                    ci.incident_state_id or 0,
                    ci.created_at,
                    ci.created_by,
                    ci.changed_at,
                    ci.changed_by,
                )
                if not _in_range(row, crit):
                    continue
                if number_patterns and not _matches(number_patterns, ci.number):
                    continue
                vlist = versions.get(ci.id) or []
                current = next((v for v in vlist if v.id == ci.last_version_id), None)
                if need_version and current is None:
                    continue
                if name_patterns:
                    names = [v.name for v in vlist] if crit.previous_versions else [current.name]  # type: ignore[union-attr]
                    if not any(_matches(name_patterns, n) for n in names):
                        continue
                if (
                    attr_patterns
                    and current is not None
                    and not _attributes_match(current, attr_patterns, schema_of, catalog_names)
                ):
                    continue
                name = current.name if current is not None else ""
                out.append(replace(row, name=name))
                if len(out) >= cap:
                    return

    try:
        async with statement_timeout(session, max(1, int(budget))):
            await scan()
    except ReportQueryTimeout as exc:
        raise GiSearchError("Timeout", "ConfigItemSearch: the search took too long") from exc
    if len(out) >= cap:
        logger.warning("gi_search_truncated", cap=cap, mode="znuny_readthrough")
    return await _with_current_names(source, out)


async def _with_current_names(source: ImportSource, rows: list[GiRow]) -> list[GiRow]:
    """Rows whose ``name`` was not loaded (no name/attribute filter) get the current name."""
    missing = [r.id for r in rows if not r.name]
    if not missing:
        return rows
    versions = await source.versions(missing)
    cis = {c.id: c for c in await source.cis(missing)}
    names: dict[int, str] = {}
    for ci_id, vlist in versions.items():
        ci = cis.get(ci_id)
        current = next((v for v in vlist if ci is not None and v.id == ci.last_version_id), None)
        if current is not None:
            names[ci_id] = current.name
    return [
        GiRow(
            r.id,
            r.number,
            r.name or names.get(r.id, ""),
            r.class_id,
            r.deployment_state_id,
            r.incident_state_id,
            r.created_at,
            r.created_by,
            r.changed_at,
            r.changed_by,
        )
        for r in rows
    ]


def _attributes_match(
    version: Any,
    attr_patterns: Mapping[Any, list[re.Pattern[str]]],
    schema_of: Callable[[int], tuple[dict[str, Any], set[str]] | None],
    catalog_names: Mapping[int, str],
) -> bool:
    loaded = schema_of(version.definition_id)
    if loaded is None:
        return False
    schema, catalog_paths = loaded
    attributes, _issues = xmldata_to_attributes(version.xml_data, schema, _Identity())
    for path, patterns in attr_patterns.items():
        values = _walk_attribute_values(attributes, tuple(path))
        joined = "::".join(path)
        texts = [
            catalog_names.get(int(v), "")
            if joined in catalog_paths and isinstance(v, int)
            else ("" if v is None else str(v))
            for v in values
        ]
        if not any(_matches(patterns, t) for t in texts):
            return False
    return True
