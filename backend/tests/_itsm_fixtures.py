"""Seed helpers that write configuration items the way Znuny's ITSM packages store them.

Tests only. The rows mirror what ``ConfigItemAdd`` / ``VersionAdd`` / ``LinkAdd`` leave behind
(survey in ``docs/itsm-import.md``, "Speicher der Attribute"):

* ``configitem`` (number, class, ``last_version_id`` and the current states of the last version),
* one ``configitem_version`` per version (the highest id is the current one),
* the attributes of every version in the core table ``xml_storage``
  (``xml_type='ITSM::ConfigItem::<ClassID>'``, ``xml_key=<version id>``, flattened
  ``[1]{'Version'}[1]{'<Key>'}[<n>]{'Content'}`` paths plus one ``...{'TagKey'}`` row per node),
* ``configitem_history`` rows for the creation and every version,
* links in ``link_relation`` with the ``link_object`` / ``link_type`` rows created on first use.

All helpers take an ``AsyncSession`` on an ITSM database (``itsm_db_url``), flush and never commit.
The 6.5 and 7.3 storage is identical; ``profile`` is validated so a future profile has to be
handled here deliberately.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.seed_definitions import PROFILES

CLASS_CATALOG = "ITSM::ConfigItem::Class"
DEPL_CATALOG = "ITSM::ConfigItem::DeploymentState"
INCI_CATALOG = "ITSM::Core::IncidentState"
CI_LINK_OBJECT = "ITSMConfigItem"
XML_TYPE_PREFIX = "ITSM::ConfigItem::"
_BASE_TIME = datetime(2026, 1, 1, 8, 0, 0)  # noqa: DTZ001 - Znuny stores naive UTC


async def catalog_id(session: AsyncSession, catalog_class: str, name: str) -> int:
    """``general_catalog.id`` of one catalog entry (``LookupError`` when absent)."""
    found = (
        await session.execute(
            text("SELECT id FROM general_catalog WHERE general_catalog_class = :c AND name = :n"),
            {"c": catalog_class, "n": name},
        )
    ).scalar_one_or_none()
    if found is None:
        raise LookupError(f"general_catalog {catalog_class!r} has no entry {name!r}")
    return int(found)


def xml_storage_rows(attributes: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Flatten attribute values into ``(xml_content_key, xml_content_value)`` pairs.

    ``attributes`` maps a key to a scalar, a dict of sub attributes (an optional ``Content``
    key holds the node's own value), or a list of those for a repeated attribute (``CountMax >
    1``). ``None`` leaves the node without a ``Content`` row.
    """
    rows: list[tuple[str, str]] = []
    root = "[1]{'Version'}[1]"
    rows.append(("[1]{'TagKey'}", "[1]"))
    rows.append(("[1]{'Version'}[1]{'TagKey'}", root))
    _flatten(root, attributes, rows)
    return rows


def _flatten(prefix: str, attributes: Mapping[str, Any], rows: list[tuple[str, str]]) -> None:
    for key, value in attributes.items():
        occurrences = value if isinstance(value, list) else [value]
        for index, item in enumerate(occurrences, start=1):
            node = f"{prefix}{{'{key}'}}[{index}]"
            subs: Mapping[str, Any] = {}
            content = item
            if isinstance(item, Mapping):
                subs = {k: v for k, v in item.items() if k != "Content"}
                content = item.get("Content")
            if content is not None:
                rows.append((f"{node}{{'Content'}}", str(content)))
            rows.append((f"{node}{{'TagKey'}}", node))
            if subs:
                _flatten(node, subs, rows)


async def seed_ci(
    session: AsyncSession,
    profile: str,
    *,
    number: str,
    class_name: str,
    name: str,
    versions: Sequence[Mapping[str, Any]] = (),
    depl_state: str = "Production",
    inci_state: str = "Operational",
    created: datetime | None = None,
) -> int:
    """Insert one CI with its versions as Znuny would; returns ``configitem.id``.

    ``versions`` is oldest first; the last one is the current version. A version is a dict with
    ``attributes`` (see :func:`xml_storage_rows`) and optionally ``name``, ``depl_state``,
    ``inci_state`` (default: the ``name`` / states given for the CI). No versions at all creates
    one version without attributes. The CI's current states are those of the last version.
    """
    if profile not in PROFILES:
        raise ValueError(f"unknown ITSM profile {profile!r}")
    class_id = await catalog_id(session, CLASS_CATALOG, class_name)
    definition_id = (
        await session.execute(
            text(
                "SELECT id FROM configitem_definition WHERE class_id = :c ORDER BY version DESC"
                " LIMIT 1"
            ),
            {"c": class_id},
        )
    ).scalar_one_or_none()
    if definition_id is None:
        raise LookupError(f"class {class_name!r} has no definition")
    start = created or _BASE_TIME
    await session.execute(
        text(
            "INSERT INTO configitem (configitem_number, class_id, create_time, create_by,"
            " change_time, change_by) VALUES (:num, :cls, :t, 1, :t, 1)"
        ),
        {"num": number, "cls": class_id, "t": start},
    )
    ci_id = int(
        (
            await session.execute(
                text("SELECT id FROM configitem WHERE configitem_number = :num"), {"num": number}
            )
        ).scalar_one()
    )
    await _history(session, ci_id, "ConfigItemCreate", str(ci_id), start)

    last_version_id = 0
    last_depl = last_inci = 0
    for position, version in enumerate(versions or [{}]):
        stamp = start + timedelta(minutes=position)
        depl_id = await catalog_id(session, DEPL_CATALOG, version.get("depl_state", depl_state))
        inci_id = await catalog_id(session, INCI_CATALOG, version.get("inci_state", inci_state))
        await session.execute(
            text(
                "INSERT INTO configitem_version (configitem_id, name, definition_id,"
                " depl_state_id, inci_state_id, create_time, create_by) VALUES"
                " (:ci, :n, :d, :depl, :inci, :t, 1)"
            ),
            {
                "ci": ci_id,
                "n": version.get("name", name),
                "d": int(definition_id),
                "depl": depl_id,
                "inci": inci_id,
                "t": stamp,
            },
        )
        last_version_id = int(
            (
                await session.execute(
                    text("SELECT MAX(id) FROM configitem_version WHERE configitem_id = :ci"),
                    {"ci": ci_id},
                )
            ).scalar_one()
        )
        rows = xml_storage_rows(version.get("attributes", {}))
        await session.execute(
            text(
                "INSERT INTO xml_storage (xml_type, xml_key, xml_content_key, xml_content_value)"
                " VALUES (:t, :k, :ck, :cv)"
            ),
            [
                {
                    "t": f"{XML_TYPE_PREFIX}{class_id}",
                    "k": str(last_version_id),
                    "ck": key,
                    "cv": value,
                }
                for key, value in rows
            ],
        )
        await _history(session, ci_id, "VersionCreate", str(last_version_id), stamp)
        last_depl, last_inci = depl_id, inci_id
    await session.execute(
        text(
            "UPDATE configitem SET last_version_id = :v, cur_depl_state_id = :depl,"
            " cur_inci_state_id = :inci WHERE id = :ci"
        ),
        {"v": last_version_id, "depl": last_depl, "inci": last_inci, "ci": ci_id},
    )
    await session.flush()
    return ci_id


async def _history(
    session: AsyncSession, ci_id: int, type_name: str, content: str, stamp: datetime
) -> None:
    await session.execute(
        text(
            "INSERT INTO configitem_history (configitem_id, type_id, content, create_time,"
            " create_by) SELECT :ci, id, :c, :t, 1 FROM configitem_history_type WHERE name = :n"
        ),
        {"ci": ci_id, "c": content, "t": stamp, "n": type_name},
    )


async def _lookup_or_create(session: AsyncSession, table: str, name: str) -> int:
    """``link_object`` / ``link_type`` rows are created on first use, as ``LinkObject`` does."""
    found = (
        await session.execute(text(f"SELECT id FROM {table} WHERE name = :n"), {"n": name})
    ).scalar_one_or_none()
    if found is not None:
        return int(found)
    if table == "link_object":
        await session.execute(text("INSERT INTO link_object (name) VALUES (:n)"), {"n": name})
    else:
        await session.execute(
            text(
                f"INSERT INTO {table} (name, valid_id, create_by, create_time, change_by,"
                " change_time) VALUES (:n, 1, 1, :t, 1, :t)"
            ),
            {"n": name, "t": _BASE_TIME},
        )
    return int(
        (
            await session.execute(text(f"SELECT id FROM {table} WHERE name = :n"), {"n": name})
        ).scalar_one()
    )


async def seed_ci_link(
    session: AsyncSession,
    *,
    source_key: int | str,
    target_key: int | str,
    link_type: str = "DependsOn",
    source_object: str = CI_LINK_OBJECT,
    target_object: str = CI_LINK_OBJECT,
    state: str = "Valid",
) -> None:
    """Insert one ``link_relation`` row: ``source`` DependsOn ``target`` (CI ids as keys).

    ``source_object`` / ``target_object`` default to ``ITSMConfigItem``; use ``"Ticket"`` with a
    ticket id as key for a ticket-to-CI link.
    """
    source_object_id = await _lookup_or_create(session, "link_object", source_object)
    target_object_id = await _lookup_or_create(session, "link_object", target_object)
    type_id = await _lookup_or_create(session, "link_type", link_type)
    state_id = (
        await session.execute(text("SELECT id FROM link_state WHERE name = :n"), {"n": state})
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO link_relation (source_object_id, source_key, target_object_id,"
            " target_key, type_id, state_id, create_time, create_by) VALUES"
            " (:so, :sk, :tobj, :tk, :ty, :st, :t, 1)"
        ),
        {
            "so": source_object_id,
            "sk": str(source_key),
            "tobj": target_object_id,
            "tk": str(target_key),
            "ty": type_id,
            "st": int(state_id),
            "t": _BASE_TIME,
        },
    )
    await session.flush()
