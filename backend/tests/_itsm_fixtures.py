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

The change management helpers (``seed_change`` ... ``seed_change_history``, survey in
``docs/itsm-import.md``, "Change-Verwaltung: Schema und Verhalten") write the rows of the
``ITSMChangeManagement`` package the way its API leaves them: catalog ids from ``general_catalog``,
the sentinel ``9999-01-01 00:00:00`` for a work order time that is not set, ``change_cab`` rows with
exactly one of ``user_id`` / ``customer_user_id``, links of the objects ``ITSMChange`` /
``ITSMWorkOrder``. The ids a test passes are in its own band (946000-946099 for the helper tests).

All helpers take an ``AsyncSession`` on an ITSM database (``itsm_db_url``), flush and never commit.
The 6.5 and 7.3 storage is identical; ``profile`` is validated so a future profile has to be
handled here deliberately.
"""

from __future__ import annotations

import re
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
CHANGE_STATE_CATALOG = "ITSM::ChangeManagement::Change::State"
CHANGE_CATEGORY_CATALOG = "ITSM::ChangeManagement::Category"
CHANGE_IMPACT_CATALOG = "ITSM::ChangeManagement::Impact"
CHANGE_PRIORITY_CATALOG = "ITSM::ChangeManagement::Priority"
WORKORDER_STATE_CATALOG = "ITSM::ChangeManagement::WorkOrder::State"
WORKORDER_TYPE_CATALOG = "ITSM::ChangeManagement::WorkOrder::Type"
CHANGE_LINK_OBJECT = "ITSMChange"
WORKORDER_LINK_OBJECT = "ITSMWorkOrder"
# Znuny's "time not set" for the work order times (column default of change_workorder).
CHANGE_TIME_SENTINEL = datetime(9999, 1, 1, 0, 0, 0)  # noqa: DTZ001 - Znuny stores naive UTC
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


# --- change management (ITSMChangeManagement) ----------------------------------------------------


def znuny_plain(value: str | None, profile: str = "itsm-7") -> str | None:
    """The ``*_plain`` column Znuny stores for ``value`` (``HTMLUtils::ToAscii``, survey Q6).

    Text without markup is unchanged; for a ``<p>`` block 7.3 ends with one newline, 6.5 starts
    with one and ends with two. Tags are dropped; this covers the simple fragments tests use.
    """
    if value is None or "<" not in value:
        return value
    stripped = re.sub(r"<[^>]+>", "", value)
    return f"\n{stripped}\n\n" if profile == "itsm-6" else f"{stripped}\n"


async def seed_change(
    session: AsyncSession,
    *,
    change_id: int,
    number: str,
    title: str,
    state: str,
    category: str = "3 normal",
    impact: str = "3 normal",
    priority: str = "3 normal",
    manager_id: int | None = None,
    builder_id: int,
    created_at: datetime,
    description: str | None = None,
    justification: str | None = None,
    description_plain: str | None = None,
    justification_plain: str | None = None,
    requested_time: datetime | None = None,
    profile: str = "itsm-7",
) -> int:
    """Insert one ``change_item`` row with explicit id; returns ``change_id``.

    ``state`` / ``category`` / ``impact`` / ``priority`` are catalog names (``"in progress"``,
    ``"3 normal"``); ``builder_id`` / ``manager_id`` must be existing users. The ``*_plain``
    columns default to what Znuny stores (``znuny_plain`` of the text for ``profile``); pass them
    to pin another value. A change has no planned/actual time columns, those come from its
    work orders.
    """
    await session.execute(
        text(
            "INSERT INTO change_item (id, change_number, title, description, description_plain,"
            " justification, justification_plain, change_state_id, change_manager_id,"
            " change_builder_id, category_id, impact_id, priority_id, requested_time,"
            " create_time, create_by, change_time, change_by) VALUES (:id, :num, :title, :d, :dp,"
            " :j, :jp, :state, :manager, :builder, :cat, :imp, :prio, :req, :t, :builder, :t,"
            " :builder)"
        ),
        {
            "id": change_id,
            "num": number,
            "title": title,
            "d": description,
            "dp": description_plain
            if description_plain is not None
            else znuny_plain(description, profile),
            "j": justification,
            "jp": justification_plain
            if justification_plain is not None
            else znuny_plain(justification, profile),
            "state": await catalog_id(session, CHANGE_STATE_CATALOG, state),
            "manager": manager_id,
            "builder": builder_id,
            "cat": await catalog_id(session, CHANGE_CATEGORY_CATALOG, category),
            "imp": await catalog_id(session, CHANGE_IMPACT_CATALOG, impact),
            "prio": await catalog_id(session, CHANGE_PRIORITY_CATALOG, priority),
            "req": requested_time,
            "t": created_at,
        },
    )
    await session.flush()
    return change_id


async def seed_workorder(
    session: AsyncSession,
    *,
    change_id: int,
    workorder_id: int,
    number: int,
    title: str,
    wo_type: str,
    state: str,
    agent_id: int | None = None,
    planned: tuple[datetime, datetime] | None = None,
    actual: tuple[datetime, datetime] | None = None,
    created_at: datetime = _BASE_TIME,
    instruction: str | None = None,
    report: str | None = None,
    instruction_plain: str | None = None,
    report_plain: str | None = None,
    planned_effort: float = 0,
    accounted_time: float = 0,
    profile: str = "itsm-7",
) -> int:
    """Insert one ``change_workorder`` row with explicit id; returns ``workorder_id``.

    ``wo_type`` is ``workorder`` / ``backout`` / ``pir`` / ``approval`` / ``decision``. A missing
    ``planned`` / ``actual`` pair stores Znuny's sentinel ``9999-01-01 00:00:00`` (never NULL),
    both of the pair are always written together. ``number`` is the 1-based position in the change.
    """
    planned_start, planned_end = planned or (CHANGE_TIME_SENTINEL, CHANGE_TIME_SENTINEL)
    actual_start, actual_end = actual or (CHANGE_TIME_SENTINEL, CHANGE_TIME_SENTINEL)
    await session.execute(
        text(
            "INSERT INTO change_workorder (id, change_id, workorder_number, title, instruction,"
            " instruction_plain, report, report_plain, workorder_state_id, workorder_type_id,"
            " workorder_agent_id, planned_start_time, planned_end_time, actual_start_time,"
            " actual_end_time, planned_effort, accounted_time, create_time, create_by,"
            " change_time, change_by) VALUES (:id, :change, :num, :title, :ins, :insp, :rep, :repp,"
            " :state, :type, :agent, :ps, :pe, :astart, :aend, :effort, :accounted, :t, 1, :t, 1)"
        ),
        {
            "id": workorder_id,
            "change": change_id,
            "num": number,
            "title": title,
            "ins": instruction,
            "insp": instruction_plain
            if instruction_plain is not None
            else znuny_plain(instruction, profile),
            "rep": report,
            "repp": report_plain if report_plain is not None else znuny_plain(report, profile),
            "state": await catalog_id(session, WORKORDER_STATE_CATALOG, state),
            "type": await catalog_id(session, WORKORDER_TYPE_CATALOG, wo_type),
            "agent": agent_id,
            "ps": planned_start,
            "pe": planned_end,
            "astart": actual_start,
            "aend": actual_end,
            "effort": planned_effort,
            "accounted": accounted_time,
            "t": created_at,
        },
    )
    await session.flush()
    return workorder_id


async def seed_change_cab(
    session: AsyncSession,
    *,
    change_id: int,
    cab_id: int,
    user_id: int | None = None,
    customer_user_id: str | None = None,
) -> int:
    """Insert one ``change_cab`` row: an agent (``user_id``) or a customer user, never both."""
    if (user_id is None) == (customer_user_id is None):
        raise ValueError("a CAB row holds exactly one of user_id and customer_user_id")
    await session.execute(
        text(
            "INSERT INTO change_cab (id, change_id, user_id, customer_user_id)"
            " VALUES (:id, :change, :user, :cust)"
        ),
        {"id": cab_id, "change": change_id, "user": user_id, "cust": customer_user_id},
    )
    await session.flush()
    return cab_id


# LinkObject::PossibleLink###3400-3420 (ITSMCore), survey Q1; source object first.
_ALLOWED_CHANGE_LINKS: dict[tuple[str, str], tuple[str, ...]] = {
    (CHANGE_LINK_OBJECT, "Ticket"): ("Normal",),
    (WORKORDER_LINK_OBJECT, "Ticket"): ("Normal",),
    (WORKORDER_LINK_OBJECT, "ITSMConfigItem"): ("Normal", "DependsOn"),
    (WORKORDER_LINK_OBJECT, "Service"): ("Normal", "DependsOn"),
}


async def seed_change_link(
    session: AsyncSession,
    *,
    source_object: str,
    source_key: int | str,
    target_object: str,
    target_key: int | str,
    link_type: str,
) -> None:
    """Insert one valid ``link_relation`` row of a change or work order (``ITSMChange`` /
    ``ITSMWorkOrder`` as object name; the package allows ``Normal`` to a ticket for changes, and
    ``Normal`` / ``DependsOn`` to CIs and services for work orders)."""
    if (source_object, target_object) not in _ALLOWED_CHANGE_LINKS or link_type not in (
        _ALLOWED_CHANGE_LINKS[(source_object, target_object)]
    ):
        raise ValueError(
            f"Znuny refuses a {link_type!r} link {source_object} -> {target_object}"
            " (a change links to tickets only, CIs and services hang off work orders)"
        )
    await seed_ci_link(
        session,
        source_object=source_object,
        source_key=source_key,
        target_object=target_object,
        target_key=target_key,
        link_type=link_type,
    )


async def seed_change_history(
    session: AsyncSession,
    *,
    history_id: int,
    change_id: int,
    history_type: str,
    created_at: datetime,
    workorder_id: int | None = None,
    fieldname: str | None = None,
    content_old: str | None = None,
    content_new: str | None = None,
    user_id: int = 1,
) -> int:
    """Insert one ``change_history`` row; ``history_type`` is a ``change_history_type.name``
    (``ChangeAdd``, ``ChangeUpdate``, ``WorkOrderAdd``, ...)."""
    type_id = (
        await session.execute(
            text("SELECT id FROM change_history_type WHERE name = :n"), {"n": history_type}
        )
    ).scalar_one_or_none()
    if type_id is None:
        raise LookupError(f"change_history_type has no entry {history_type!r}")
    await session.execute(
        text(
            "INSERT INTO change_history (id, change_id, workorder_id, type_id, content_new,"
            " content_old, fieldname, create_time, create_by) VALUES (:id, :change, :wo, :type,"
            " :new, :old, :field, :t, :user)"
        ),
        {
            "id": history_id,
            "change": change_id,
            "wo": workorder_id,
            "type": int(type_id),
            "new": content_new,
            "old": content_old,
            "field": fieldname,
            "t": created_at,
            "user": user_id,
        },
    )
    await session.flush()
    return history_id


def znuny_change_times(
    workorders: Sequence[Mapping[str, datetime | None]],
) -> dict[str, datetime | None]:
    """The change's planned/actual start/end as ``ChangeGet`` derives them from its work orders.

    Reference of ``WorkOrderChangeTimeGet`` (survey question 2). Each work order maps
    ``planned_start`` / ``planned_end`` / ``actual_start`` / ``actual_end`` to a datetime, or to
    ``None`` / the sentinel when not set. The sentinel is the largest value, so the MIN columns
    skip it while one work order without a time blanks the MAX columns (and, by the extra
    count query, the planned start). No work orders: nothing is set.
    """

    def value(order: Mapping[str, datetime | None], key: str) -> datetime:
        return order.get(key) or CHANGE_TIME_SENTINEL

    def shown(found: datetime | None) -> datetime | None:
        return None if found is None or found == CHANGE_TIME_SENTINEL else found

    if not workorders:
        return {
            "planned_start": None,
            "planned_end": None,
            "actual_start": None,
            "actual_end": None,
        }
    planned_starts = [value(o, "planned_start") for o in workorders]
    planned_start = shown(min(planned_starts))
    if CHANGE_TIME_SENTINEL in planned_starts:
        planned_start = None
    return {
        "planned_start": planned_start,
        "planned_end": shown(max(value(o, "planned_end") for o in workorders)),
        "actual_start": shown(min(value(o, "actual_start") for o in workorders)),
        "actual_end": shown(max(value(o, "actual_end") for o in workorders)),
    }
