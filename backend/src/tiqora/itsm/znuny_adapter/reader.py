"""Read-only SQL access to Znuny's CMDB tables (Mode A).

Every statement is ``text()`` with bound parameters; table and column names come from this module
and the :class:`SchemaProfile` only, never from input. Joins stay inside Znuny's own tables
(``configitem`` with ``configitem_version``); nothing here touches a ``tiqora_*`` table. The reader
never writes and never commits.

Search (``ZnunyItsmReader.search``) matches the CI number and the current version's name by
case-insensitive *contains*. A text of at least three characters also matches the values of
**searchable** attributes of the **current** versions of the readable classes: the scan reads
``xml_storage`` rows of ``xml_type = <prefix><class id>`` (older versions live under the archive
prefix and are never scanned), is bounded by ``statement_timeout`` (a hit is reported as
``CiSearchTooBroad``) and yields at most ``candidate_cap`` versions; beyond that the result is
``truncated`` and its ``total`` is a lower bound.
"""

from __future__ import annotations

import functools
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import structlog
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.ci_provider import CiAttribute, CiQuery, CiSearchTooBroad, CiUnavailable
from tiqora.itsm.znuny_adapter import attributes as attrs
from tiqora.itsm.znuny_adapter.catalog import chunks, class_names
from tiqora.itsm.znuny_adapter.definitions import AttrDef, DefinitionError, parse_definition
from tiqora.itsm.znuny_adapter.detect import ZnunyItsmInfo, detect_znuny_itsm
from tiqora.itsm.znuny_adapter.profiles import PROFILES, SchemaProfile
from tiqora.reporting.sql import ReportQueryTimeout, statement_timeout
from tiqora.znuny.xml_hash import XmlHashError, build_xml_hash

logger = structlog.get_logger(__name__)

ATTRIBUTE_CANDIDATE_CAP: Final = 500
ATTRIBUTE_SEARCH_MIN_CHARS: Final = 3
SEARCH_TIMEOUT_SECONDS: Final = 10
MAX_PAGE: Final = 100


@dataclass(frozen=True, slots=True)
class DefinitionRow:
    id: int
    class_id: int
    version: int
    fmt: str
    text: str
    created_at: datetime | None
    created_by: int | None

    def attributes(self) -> tuple[AttrDef, ...]:
        """The prepared attribute tree (parsed once per distinct text)."""
        return _parse_cached(self.text, self.fmt)


@functools.lru_cache(maxsize=256)
def _parse_cached(definition_text: str, fmt: str) -> tuple[AttrDef, ...]:
    return parse_definition(definition_text, fmt)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class CiRow:
    id: int
    number: str
    class_id: int
    last_version_id: int | None
    cur_depl_state_id: int | None
    cur_inci_state_id: int | None
    created_at: datetime
    created_by: int | None
    changed_at: datetime
    changed_by: int | None


@dataclass(frozen=True, slots=True)
class VersionRow:
    id: int
    ci_id: int
    name: str
    definition_id: int
    depl_state_id: int | None
    inci_state_id: int | None
    created_at: datetime
    created_by: int | None


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    rows: list[CiRow]
    total: int
    #: the attribute scan hit its candidate cap: ``total`` counts only the candidates found
    truncated: bool


def _opt_int(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[call-overload]


def _decode(value: object) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def like_contains(value: str) -> str:
    """``%value%`` with LIKE metacharacters escaped for ``ESCAPE '!'``."""
    escaped = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _like_literal(value: str) -> str:
    return like_contains(value)[1:-1]


def attribute_key_pattern(chain: Sequence[str]) -> str:
    """LIKE pattern (``ESCAPE '!'``) for the ``Content`` row of the attribute at ``chain``.

    ``('NIC', 'IPAddress')`` -> ``[1]{'Version'}[1]{'NIC'}[%]{'IPAddress'}[%]{'Content'}``.
    """
    body = "".join(f"{{'{_like_literal(key)}'}}[%]" for key in chain)
    return f"[1]{{'Version'}}[1]{body}{{'Content'}}"


def searchable_chains(defs: Sequence[AttrDef]) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []

    def walk(items: Sequence[AttrDef], prefix: tuple[str, ...]) -> None:
        for attr in items:
            chain = (*prefix, attr.key)
            if attr.searchable:
                out.append(chain)
            walk(attr.sub, chain)

    walk(defs, ())
    return out


def _expanding(sql: str, *names: str) -> Any:
    stmt = text(sql)
    return stmt.bindparams(*(bindparam(n, expanding=True) for n in names)) if names else stmt


_CI_COLS: Final = (
    "id, configitem_number, class_id, last_version_id, cur_depl_state_id, cur_inci_state_id,"
    " create_time, create_by, change_time, change_by"
)
_VERSION_COLS: Final = (
    "id, configitem_id, name, definition_id, depl_state_id, inci_state_id, create_time, create_by"
)


def _ci_row(r: Any) -> CiRow:
    return CiRow(
        id=int(r[0]),
        number=str(r[1]),
        class_id=int(r[2]),
        last_version_id=_opt_int(r[3]),
        cur_depl_state_id=_opt_int(r[4]),
        cur_inci_state_id=_opt_int(r[5]),
        created_at=r[6],
        created_by=_opt_int(r[7]),
        changed_at=r[8],
        changed_by=_opt_int(r[9]),
    )


def _version_row(r: Any) -> VersionRow:
    return VersionRow(
        id=int(r[0]),
        ci_id=int(r[1]),
        name=str(r[2]),
        definition_id=int(r[3]),
        depl_state_id=_opt_int(r[4]),
        inci_state_id=_opt_int(r[5]),
        created_at=r[6],
        created_by=_opt_int(r[7]),
    )


class ZnunyItsmReader:
    """Reads CIs, versions, definitions and decoded attributes for one schema profile."""

    def __init__(
        self,
        profile: SchemaProfile,
        *,
        candidate_cap: int = ATTRIBUTE_CANDIDATE_CAP,
        timeout_seconds: int = SEARCH_TIMEOUT_SECONDS,
    ) -> None:
        self.profile = profile
        self._cap = candidate_cap
        self._timeout = timeout_seconds

    # ------------------------------------------------------------------ plain gets

    async def get_cis(self, session: AsyncSession, ci_ids: Collection[int]) -> dict[int, CiRow]:
        stmt = _expanding(f"SELECT {_CI_COLS} FROM configitem WHERE id IN :ids", "ids")
        out: dict[int, CiRow] = {}
        for part in chunks(sorted(set(ci_ids))):
            for r in (await session.execute(stmt, {"ids": list(part)})).all():
                out[int(r[0])] = _ci_row(r)
        return out

    async def get_ci(self, session: AsyncSession, ci_id: int) -> CiRow | None:
        return (await self.get_cis(session, [ci_id])).get(ci_id)

    async def get_versions(
        self, session: AsyncSession, version_ids: Collection[int]
    ) -> dict[int, VersionRow]:
        stmt = _expanding(f"SELECT {_VERSION_COLS} FROM configitem_version WHERE id IN :ids", "ids")
        out: dict[int, VersionRow] = {}
        for part in chunks(sorted(set(version_ids))):
            for r in (await session.execute(stmt, {"ids": list(part)})).all():
                out[int(r[0])] = _version_row(r)
        return out

    async def get_version(self, session: AsyncSession, version_id: int) -> VersionRow | None:
        return (await self.get_versions(session, [version_id])).get(version_id)

    async def definitions(
        self, session: AsyncSession, definition_ids: Collection[int]
    ) -> dict[int, DefinitionRow]:
        stmt = _expanding(
            "SELECT id, class_id, version, configitem_definition, create_time, create_by"
            " FROM configitem_definition WHERE id IN :ids",
            "ids",
        )
        out: dict[int, DefinitionRow] = {}
        for part in chunks(sorted(set(definition_ids))):
            for r in (await session.execute(stmt, {"ids": list(part)})).all():
                out[int(r[0])] = self._definition_row(r)
        return out

    def _definition_row(self, r: Any) -> DefinitionRow:
        return DefinitionRow(
            id=int(r[0]),
            class_id=int(r[1]),
            version=int(r[2]),
            fmt=self.profile.definition_format,
            text=_decode(r[3]),
            created_at=r[4],
            created_by=_opt_int(r[5]),
        )

    # ------------------------------------------------------------------ attributes

    async def _xml_hashes(
        self,
        session: AsyncSession,
        versions: Sequence[VersionRow],
        definitions: Mapping[int, DefinitionRow],
    ) -> dict[int, list[Any]]:
        """``build_xml_hash`` per version id; one query per class and 500 versions."""
        by_class: dict[int, list[int]] = {}
        for version in versions:
            definition = definitions.get(version.definition_id)
            if definition is not None:
                by_class.setdefault(definition.class_id, []).append(version.id)
        stmt = _expanding(
            "SELECT xml_key, xml_content_key, xml_content_value FROM xml_storage"
            " WHERE xml_type IN :types AND xml_key IN :keys",
            "types",
            "keys",
        )
        out: dict[int, list[Any]] = {}
        for class_id, version_ids in by_class.items():
            types = [
                self.profile.current_xml_type(class_id),
                self.profile.archive_xml_type(class_id),
            ]
            for part in chunks(version_ids):
                rows_by_key: dict[str, list[tuple[str, str | None]]] = {}
                params = {"types": types, "keys": [str(v) for v in part]}
                for key, ckey, cvalue in (await session.execute(stmt, params)).all():
                    rows_by_key.setdefault(str(key), []).append(
                        (str(ckey), None if cvalue is None else str(cvalue))
                    )
                for version_id in part:
                    try:
                        out[version_id] = build_xml_hash(rows_by_key.get(str(version_id), []))
                    except XmlHashError as exc:
                        logger.warning(
                            "itsm_ci_attributes_unreadable", version_id=version_id, error=str(exc)
                        )
        return out

    async def version_attributes(
        self, session: AsyncSession, version_rows: Sequence[VersionRow]
    ) -> dict[int, list[attrs.AttrValue]]:
        """Decoded attribute values per version id, each with the version's own definition.

        A version whose definition or stored attributes cannot be read yields an empty list (and
        a warning), so one damaged record does not take the whole CI page down.
        """
        definitions = await self.definitions(session, {v.definition_id for v in version_rows})
        hashes = await self._xml_hashes(session, version_rows, definitions)
        out: dict[int, list[attrs.AttrValue]] = {}
        for version in version_rows:
            definition = definitions.get(version.definition_id)
            xml_data = hashes.get(version.id)
            if definition is None or xml_data is None:
                out[version.id] = []
                continue
            try:
                defs = definition.attributes()
            except DefinitionError as exc:
                logger.warning("itsm_ci_definition_unreadable", id=definition.id, error=str(exc))
                out[version.id] = []
                continue
            out[version.id] = attrs.values_from_xml_data(defs, xml_data)
        return out

    async def display_attributes(
        self,
        session: AsyncSession,
        version: VersionRow,
        *,
        readable_class_ids: Collection[int] | None = None,
    ) -> tuple[CiAttribute, ...]:
        """The version's attributes resolved for display (names instead of ids)."""
        values = (await self.version_attributes(session, [version])).get(version.id, [])
        if not values:
            return ()
        definition = (await self.definitions(session, [version.definition_id]))[
            version.definition_id
        ]
        defs = definition.attributes()
        keys = attrs.collect_lookup_keys(values, defs)
        lookups = await attrs.load_lookups(session, keys, readable_class_ids=readable_class_ids)
        return attrs.resolve_display(values, defs, lookups)

    # ------------------------------------------------------------------ search

    async def search(
        self, session: AsyncSession, q: CiQuery, *, class_ids: Collection[int] | None
    ) -> tuple[list[CiRow], int]:
        outcome = await self.search_ex(session, q, class_ids=class_ids)
        return outcome.rows, outcome.total

    async def search_ex(
        self, session: AsyncSession, q: CiQuery, *, class_ids: Collection[int] | None
    ) -> SearchOutcome:
        """:meth:`search` plus the ``truncated`` flag.

        ``class_ids`` are the classes the caller may read (``None``: all); ``q.class_keys`` narrow
        them further. An empty result set of classes short-circuits to nothing.
        """
        limit = max(1, min(q.limit, MAX_PAGE))
        offset = max(0, q.offset)
        allowed: set[int] | None = None if class_ids is None else {int(c) for c in class_ids}
        if q.class_keys:
            wanted = {int(k) for k in q.class_keys if k.isdigit()}
            allowed = wanted if allowed is None else allowed & wanted
        if allowed is not None and not allowed:
            return SearchOutcome([], 0, False)

        where: list[str] = []
        params: dict[str, Any] = {}
        expanding: list[str] = []

        def in_clause(column: str, name: str, values: Collection[int]) -> None:
            where.append(f"{column} IN :{name}")
            params[name] = sorted(values)
            expanding.append(name)

        if allowed is not None:
            in_clause("c.class_id", "classes", allowed)
        for name, column, keys in (
            ("depl", "c.cur_depl_state_id", q.deployment_state_keys),
            ("inci", "c.cur_inci_state_id", q.incident_state_keys),
        ):
            if keys:
                ids = [int(k) for k in keys if k.isdigit()]
                if not ids:
                    return SearchOutcome([], 0, False)
                in_clause(column, name, ids)
        if q.refs:
            ref_ids = [r.id for r in q.refs if r.source == "znuny"]
            if not ref_ids:
                return SearchOutcome([], 0, False)
            in_clause("c.id", "refs", ref_ids)

        truncated = False
        needle = (q.text or "").strip()
        if needle:
            params["pat"] = like_contains(needle)
            parts = [
                "lower(c.configitem_number) LIKE lower(:pat) ESCAPE '!'",
                "lower(v.name) LIKE lower(:pat) ESCAPE '!'",
            ]
            if len(needle) >= ATTRIBUTE_SEARCH_MIN_CHARS:
                candidates, truncated = await self._attribute_candidates(session, needle, allowed)
                if candidates:
                    parts.append("c.last_version_id IN :cands")
                    params["cands"] = candidates
                    expanding.append("cands")
            where.append("(" + " OR ".join(parts) + ")")

        clause = " AND ".join(where) if where else "1 = 1"
        base = (
            " FROM configitem c LEFT JOIN configitem_version v ON v.id = c.last_version_id"
            f" WHERE {clause}"
        )
        total = int(
            (
                await session.execute(_expanding("SELECT COUNT(*)" + base, *expanding), params)
            ).scalar_one()
        )
        cols = ", ".join(f"c.{col.strip()}" for col in _CI_COLS.split(","))
        page = _expanding(
            f"SELECT {cols}{base} ORDER BY lower(v.name), c.id LIMIT :limit OFFSET :offset",
            *expanding,
        )
        rows = (await session.execute(page, {**params, "limit": limit, "offset": offset})).all()
        return SearchOutcome([_ci_row(r) for r in rows], total, truncated)

    async def _attribute_candidates(
        self, session: AsyncSession, needle: str, allowed: set[int] | None
    ) -> tuple[list[int], bool]:
        """Version ids whose searchable attribute values contain ``needle`` (capped)."""
        class_map = await class_names(session, self.profile)
        class_list = sorted(class_map if allowed is None else allowed & set(class_map))
        if not class_list:
            return [], False
        def_stmt = _expanding(
            "SELECT id, class_id, version, configitem_definition, create_time, create_by"
            " FROM configitem_definition WHERE class_id IN :classes",
            "classes",
        )
        chains: dict[int, set[tuple[str, ...]]] = {}
        for r in (await session.execute(def_stmt, {"classes": class_list})).all():
            row = self._definition_row(r)
            try:
                found = searchable_chains(row.attributes())
            except DefinitionError as exc:
                logger.warning("itsm_ci_definition_unreadable", id=row.id, error=str(exc))
                continue
            chains.setdefault(row.class_id, set()).update(found)

        params: dict[str, Any] = {"pat": like_contains(needle), "cap": self._cap + 1}
        branches: list[str] = []
        for i, class_id in enumerate(sorted(chains)):
            patterns = sorted(attribute_key_pattern(c) for c in chains[class_id])
            if not patterns:
                continue
            params[f"t{i}"] = self.profile.current_xml_type(class_id)
            likes = []
            for j, pattern in enumerate(patterns):
                params[f"k{i}_{j}"] = pattern
                likes.append(f"xml_content_key LIKE :k{i}_{j} ESCAPE '!'")
            branches.append(f"(xml_type = :t{i} AND ({' OR '.join(likes)}))")
        if not branches:
            return [], False
        stmt = text(
            "SELECT DISTINCT xml_key FROM xml_storage WHERE ("
            + " OR ".join(branches)
            + ") AND lower(xml_content_value) LIKE lower(:pat) ESCAPE '!'"
            " ORDER BY xml_key LIMIT :cap"
        )
        try:
            async with statement_timeout(session, self._timeout):
                keys = (await session.execute(stmt, params)).scalars().all()
        except ReportQueryTimeout as exc:
            raise CiSearchTooBroad("attribute search exceeded its time limit") from exc
        if session.get_bind().dialect.name == "postgresql":
            # SET LOCAL lasts until the transaction ends; later statements of the request are
            # not meant to share this limit (MariaDB's variant is reset by statement_timeout).
            await session.execute(text("RESET statement_timeout"))
        ids = [int(k) for k in keys if str(k).isdigit()]
        return ids[: self._cap], len(ids) > self._cap


def reader_for_info(info: ZnunyItsmInfo) -> ZnunyItsmReader:
    """The reader for what is installed, or ``CiUnavailable`` with the reason code."""
    if not info.core_installed:
        raise CiUnavailable("znuny_itsm_missing", "the Znuny ITSM tables are not installed")
    if info.profile is None:
        raise CiUnavailable(
            "unsupported_znuny_itsm_version",
            info.unsupported_reason or "ITSM package versions not surveyed",
        )
    if not info.cmdb_installed:
        raise CiUnavailable("znuny_itsm_missing", "ITSMConfigurationManagement is not installed")
    return ZnunyItsmReader(PROFILES[info.profile])


async def get_reader(session: AsyncSession) -> ZnunyItsmReader:
    return reader_for_info(await detect_znuny_itsm(session))
