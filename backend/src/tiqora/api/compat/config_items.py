"""GenericInterface ``ConfigItem*`` operations on the Tiqora CMDB (plan D9, Tasks 21-24).

Port of ``Kernel/GenericInterface/Operation/ConfigItem/*.pm`` of ITSMConfigurationManagement 6.5.4
and 7.3.5 (the two versions are identical for ``ConfigItemGet``; notes with file and line
references in ``backend/tests/fixtures/itsm/gi_shapes.md``). This module is the common layer
(mode gate, authentication, class-group permission, ``GiLookups``) plus ``ConfigItemGet``; the
other four operations answer ``Operation.NotImplemented`` until Tasks 22-24.

Modes (open question 4): ``tiqora`` serves the Tiqora CMDB, ``znuny_readthrough`` (Mode A) serves
reads from Znuny's own tables through the importer facade and refuses writes with
``<Op>.NotAvailable``, ``off`` answers ``Operation.NotImplemented``. The mode is looked at after
authentication, so an anonymous caller does not learn it.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.article_input import gi_error as _err
from tiqora.api.compat.operations import _auth_from_params, _reprefix_auth_error
from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.config import Settings
from tiqora.domain.auth import SessionStore
from tiqora.itsm.importer import facades, gi_search
from tiqora.itsm.importer.gi_search import GiCriteria, GiRow, GiSearchError
from tiqora.itsm.importer.ports import CiView, Ports
from tiqora.itsm.znuny_format.definition import to_znuny_definition
from tiqora.itsm.znuny_format.gi_cixml import (
    CixmlError,
    GiLookups,
    clean_cixml,
    guard_cixml,
    parse_gi_datetime,
    xmldata_to_cixml,
)
from tiqora.itsm.znuny_format.xmldata import attributes_to_xmldata
from tiqora.util.perl import perl_truthy
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

Mode = Literal["off", "znuny_readthrough", "tiqora"]

#: ``AccessDenied`` texts (``<Op>.pm``: "Can not ... configuration item(s)!")
_DENIED: Final[dict[str, str]] = {
    "ConfigItemGet": "Can not get configuration item!",
    "ConfigItemSearch": "Can not search configuration items!",
    "ConfigItemCreate": "Can not create configuration items!",
    "ConfigItemUpdate": "Can not update configuration items!",
    "ConfigItemDelete": "Can not delete configuration item!",
}
_WRITE_OPS: Final = frozenset({"ConfigItemCreate", "ConfigItemUpdate", "ConfigItemDelete"})
#: ``ConfigItemGet`` takes a list of ids; Znuny has no limit, a request cannot grow without one
MAX_GET_IDS: Final = 500
_MAX_ID_DIGITS: Final = 18


# --------------------------------------------------------------------------- common layer


async def _gi_ports(session: AsyncSession) -> tuple[Ports, Mode]:
    """The importer ports and the effective ITSM mode."""
    ports = facades.get_ports(session)
    return ports, await ports.mode.effective_mode()


def _mode_error(op: str, mode: str) -> dict[str, Any] | None:
    """The answer for an operation the mode does not serve, ``None`` when it is served."""
    if mode == "off":
        return _err(
            "Operation.NotImplemented",
            f"{op}: ITSM is switched off (itsm.mode = off).",
        )
    if mode == "znuny_readthrough" and op in _WRITE_OPS:
        return _err(f"{op}.NotAvailable", "Die CMDB wird in Znuny geführt (Modus A)")
    return None


async def _agent_auth(
    op: str,
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    request: Any | None,
    settings: Settings | None,
) -> tuple[int, str] | dict[str, Any]:
    """``(user_id, login)`` of the agent, or the Znuny-style error dict.

    ``ConfigItem*`` is agent-only: Znuny's ``Auth`` hands a customer's *login* back as the user
    id, so every permission check fails and the answer is ``AccessDenied`` (after the parameter
    checks). A customer therefore comes back as ``(0, login)``; ``0`` holds no permission.
    """
    auth = await _auth_from_params(data, session, session_store, request=request, settings=settings)
    if isinstance(auth, dict):
        return _reprefix_auth_error(op, auth)
    user_id, login, user_type = auth
    return (user_id if user_type != "Customer" else 0, login)


def denied(op: str) -> dict[str, Any]:
    return _err(f"{op}.AccessDenied", f"{op}: {_DENIED[op]}")


async def _permission_type(session: AsyncSession, op: str) -> Literal["ro", "rw"]:
    """SysConfig ``GenericInterface::Operation::<Op>###Permission`` (stock ``ro``/``rw``)."""
    raw = await SysConfig(session).get_str(
        f"GenericInterface::Operation::{op}###Permission", "rw" if op in _WRITE_OPS else "ro"
    )
    return "rw" if raw.strip() == "rw" else "ro"


class _Lookups:
    """:class:`GiLookups` over preloaded dicts (the conversion is synchronous)."""

    def __init__(
        self,
        names: Mapping[int, str],
        ids: Mapping[tuple[str, str], int],
        customer_users: frozenset[str] = frozenset(),
        customer_companies: frozenset[str] = frozenset(),
    ) -> None:
        self._names = names
        self._ids = ids
        self._users = customer_users
        self._companies = customer_companies

    def catalog_id(self, catalog_class: str, name: str) -> int | None:
        return self._ids.get((catalog_class, name))

    def catalog_name(self, catalog_id: int) -> str | None:
        return self._names.get(catalog_id)

    def customer_user_exists(self, login: str) -> bool:
        return login in self._users

    def customer_company_exists(self, customer_id: str) -> bool:
        return customer_id in self._companies


def _catalog_classes(definition: Sequence[Mapping[str, Any]]) -> set[str]:
    """``Input.Class`` of every ``GeneralCatalog`` attribute of a Znuny definition."""
    found: set[str] = set()
    for item in definition:
        inp = item.get("Input")
        if isinstance(inp, Mapping) and inp.get("Type") == "GeneralCatalog" and inp.get("Class"):
            found.add(str(inp["Class"]))
        sub = item.get("Sub")
        if isinstance(sub, list):
            found |= _catalog_classes(sub)
    return found


async def _gi_lookups(
    session: AsyncSession,
    definition: Sequence[Mapping[str, Any]],
    *,
    mode: Mode = "tiqora",
) -> GiLookups:
    """Catalog names/ids for the ``GeneralCatalog`` classes of ``definition``.

    ``tiqora``: ids of ``tiqora_cmdb_catalog``; ``znuny_readthrough``: Znuny ``general_catalog``
    ids (names only). Customer existence sets stay empty here; Tasks 23/24 pass them in.
    """
    names: dict[int, str] = {}
    ids: dict[tuple[str, str], int] = {}
    if mode == "znuny_readthrough":
        names = await facades.get_mode_a_reader(session).catalog_names()
        return _Lookups(names, ids)
    catalog = CatalogService(session)
    for cls in sorted(_catalog_classes(definition)):
        for item in await catalog.items(cls, include_invalid=True):
            names[item.id] = item.name
            ids[(cls, item.name)] = item.id
    return _Lookups(names, ids)


def gi_time(value: datetime | None) -> str:
    """Znuny's ``YYYY-MM-DD HH:MM:SS``."""
    return "" if value is None else value.strftime("%Y-%m-%d %H:%M:%S")


def _human_size(size: int) -> str:
    """``Kernel::Language`` ``FormatDataSize``: 1 decimal, units Bytes/KBytes/MBytes/GBytes."""
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GBytes"
    if size >= 1024**2:
        return f"{size / 1024**2:.1f} MBytes"
    if size >= 1024:
        return f"{size / 1024:.1f} KBytes"
    return f"{size} Bytes"


def _parse_id(raw: Any) -> int | None:
    """A CI id from a GI value; anything that is not a plain positive integer is ``None``."""
    text = str(raw).strip()
    if not text.isascii() or not text.isdigit() or len(text) > _MAX_ID_DIGITS:
        return None
    value = int(text)
    return value or None


def _split_ids(raw: Any) -> list[Any] | None:
    """``ConfigItemID`` as Znuny reads it: a non-empty string split at ``,`` (Perl ``split``
    drops trailing empty fields), or a non-empty list; ``None`` is ``WrongStructure``."""
    if isinstance(raw, str) and raw != "":
        parts = raw.split(",")
        while parts and parts[-1] == "":
            parts.pop()
        return parts
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return [raw]
    if isinstance(raw, list) and raw:
        return list(raw)
    return None


# --------------------------------------------------------------------------- ConfigItemGet


async def _attachments(
    session: AsyncSession, view: CiView, mode: Mode
) -> list[dict[str, Any]] | str:
    """``Attachment`` of one item: a list, or ``''`` when there is none (Znuny)."""
    if mode == "znuny_readthrough":
        files = await facades.get_mode_a_reader(session).attachment_contents(view.id)
    else:
        files = await facades.cmdb_attachment_contents(
            session, view.id, [a[0] for a in view.attachments]
        )
    out: list[dict[str, Any]] = []
    for filename, content_type, content in files:
        if not filename:
            continue
        out.append(
            {
                "Content": base64.encodebytes(content).decode("ascii"),
                "ContentType": content_type,
                "Filename": filename,
                "Filesize": str(len(content)),
                "Type": "attachment",
                "Preferences": {
                    "ConfigItemID": str(view.id),
                    "ContentType": content_type,
                    "Filesize": _human_size(len(content)),
                    "FilesizeRaw": str(len(content)),
                },
            }
        )
    return out or ""


async def _item_bundle(
    session: AsyncSession, view: CiView, mode: Mode, *, with_attachments: bool
) -> dict[str, Any]:
    """One entry of the ``ConfigItem`` answer (``ConfigItemGet.pm`` ``$ConfigItemBundle``)."""
    definition = to_znuny_definition(view.schema)
    lookups = await _gi_lookups(session, definition, mode=mode)
    xml_data = attributes_to_xmldata(view.attributes, view.schema)
    bundle: dict[str, Any] = {
        "VersionID": view.version_id,
        "ConfigItemID": view.id,
        "Name": view.name,
        "DefinitionID": view.definition_id if view.definition_id is not None else 0,
        "CreateTime": gi_time(view.changed_at),
        "CreateBy": view.changed_by if view.changed_by is not None else view.created_by,
        "DeplState": view.deployment_state,
        "DeplStateType": view.deployment_functionality or "",
        "InciState": view.incident_state,
        "InciStateType": view.incident_functionality or "",
        "Class": view.class_name,
        "LastVersionID": view.last_version_id
        if view.last_version_id is not None
        else view.version_id,
        "Number": view.number,
        "CurDeplState": view.cur_deployment_state or view.deployment_state,
        "CurDeplStateType": view.cur_deployment_functionality
        or view.deployment_functionality
        or "",
        "CurInciState": view.cur_incident_state or view.incident_state,
        "CurInciStateType": view.cur_incident_functionality or view.incident_functionality or "",
        "CIXMLData": xmldata_to_cixml(xml_data, definition, lookups=lookups),
    }
    if with_attachments:
        bundle["Attachment"] = await _attachments(session, view, mode)
    return bundle


async def _load_view(
    session: AsyncSession,
    ports: Ports,
    mode: Mode,
    ci_id: int | None,
    user_id: int,
    visible: set[int],
) -> CiView | None:
    """The CI when ``user_id`` may read its class, else ``None`` (also unknown ids)."""
    if ci_id is None or user_id == 0:
        return None
    if mode == "znuny_readthrough":
        return await facades.get_mode_a_reader(session).get_ci(ci_id, user_id=user_id)
    view = await ports.reader.get_ci(ci_id)
    return view if view is not None and view.class_id in visible else None


async def op_config_item_get(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """``ConfigItemGet``: one or more items by ``ConfigItemID`` (comma list or array)."""
    op = "ConfigItemGet"
    auth = await _agent_auth(op, data, session, session_store, request, settings)
    if isinstance(auth, dict):
        return auth
    user_id, _login = auth
    ports, mode = await _gi_ports(session)
    if (gate := _mode_error(op, mode)) is not None:
        return gate

    raw = data.get("ConfigItemID")
    if not (perl_truthy(raw) if not isinstance(raw, list | dict) else True):
        return _err(f"{op}.MissingParameter", f"{op}: ConfigItemID parameter is missing!")
    id_list = _split_ids(raw)
    if id_list is None:
        return _err(f"{op}.WrongStructure", f"{op}: Structure for ConfigItemID is not correct!")
    if len(id_list) > MAX_GET_IDS:
        return _err(
            f"{op}.InvalidParameter",
            f"{op}: At most {MAX_GET_IDS} ConfigItemID values are allowed!",
        )
    with_attachments = perl_truthy(data.get("Attachments"))

    visible: set[int] = set()
    if mode == "tiqora" and user_id != 0:
        visible = await ports.reader.visible_class_ids(user_id, await _permission_type(session, op))
    items: list[dict[str, Any]] = []
    for raw_id in id_list:
        view = await _load_view(session, ports, mode, _parse_id(raw_id), user_id, visible)
        if view is None:  # unknown id and missing right look the same (ItemClassGroupCheck)
            return denied(op)
        try:
            items.append(await _item_bundle(session, view, mode, with_attachments=with_attachments))
        except ValueError as exc:  # a stored value the CIXMLData conversion refuses
            logger.warning("gi_config_item_get_unconvertible", ci_id=view.id, error=str(exc))
            return _err(f"{op}.InvalidParameter", f"{op}: Could not get ConfigItem data")
    if not items:
        return _err(f"{op}.NoConfigItemData", f"{op}: Could not get ConfigItem data")
    return {"ConfigItem": items}


# --------------------------------------------------------------------------- not yet built


async def _not_implemented(
    op: str,
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    request: Any | None,
    settings: Settings | None,
) -> dict[str, Any]:
    auth = await _agent_auth(op, data, session, session_store, request, settings)
    if isinstance(auth, dict):
        return auth
    _ports, mode = await _gi_ports(session)
    if (gate := _mode_error(op, mode)) is not None:
        return gate
    return _err("Operation.NotImplemented", f"{op}: not implemented yet")


# --------------------------------------------------------------------------- ConfigItemSearch

_ORDER_KEYS: Final[dict[str, Callable[[GiRow], Any]]] = {
    "ConfigItemID": lambda r: r.id,
    "Number": lambda r: r.number.casefold(),
    "ClassID": lambda r: r.class_id,
    "DeplStateID": lambda r: r.deployment_state_id,
    "InciStateID": lambda r: r.incident_state_id,
    "CreateTime": lambda r: r.created_at,
    "CreateBy": lambda r: r.created_by,
    "ChangeTime": lambda r: r.changed_at,
    "ChangeBy": lambda r: r.changed_by,
    "Name": lambda r: r.name.casefold(),
}
_STRICT_TIME: Final = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")
_NUMBER: Final = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")
_TIME_PARAMS: Final = (
    "CreateTimeNewerDate",
    "CreateTimeOlderDate",
    "ChangeTimeNewerDate",
    "ChangeTimeOlderDate",
)


def _with_data(value: Any) -> bool:
    """Znuny ``IsStringWithData``: a defined, non-empty scalar."""
    return isinstance(value, str | int | float) and not isinstance(value, bool) and value != ""


def _csv_or_list(raw: Any) -> list[Any] | None:
    """A non-empty string split at ``,`` (Perl ``split`` drops trailing empty fields) or a
    non-empty list; ``None`` is ``WrongStructure``."""
    if _with_data(raw) and not isinstance(raw, list):
        return _split_ids(str(raw))
    if isinstance(raw, list) and raw:
        return list(raw)
    return None


def _flatten_search(xml: Mapping[str, Any], parent: str = "") -> dict[str, list[str]]:
    """``_FormatSearchXMLData``: ``CIXMLData`` -> ``Key`` / ``Key::Sub`` -> value alternatives.

    A hash entry carries its own value under its own key (dropped when Perl-falsy, like
    Znuny). Scalars of any other type are skipped.
    """
    found: dict[str, list[str]] = {}

    def add(key: str, value: Any) -> None:
        if isinstance(value, str | int | float) and not isinstance(value, bool) and value != "":
            found.setdefault(key, []).append(str(value))

    for root in sorted(xml):
        value = xml[root]
        key = f"{parent}{root}"
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, Mapping):
                rest = {k: v for k, v in entry.items() if k != root}
                content = entry.get(root)
                if perl_truthy(content):
                    add(key, content)
                for sub_key, items in _flatten_search(rest, f"{key}::").items():
                    found.setdefault(sub_key, []).extend(items)
            else:
                add(key, entry)
    return found


def _order(rows: list[GiRow], order_by: list[Any], directions: list[Any], *, version: bool) -> None:
    """Znuny's ORDER BY, applied in place. ``version``: the order of ``VersionSearch`` (used
    when ``Name``, ``CIXMLData`` or ``PreviousVersionSearch`` is given), else the one of
    ``ConfigItemSearch``."""
    keys: list[tuple[str, bool]] = []  # (order key, descending)
    given = order_by if not version else (order_by or ["ConfigItemID"])
    dirs = directions if not (version and not directions) else ["Up"]
    for index, name in enumerate(given):
        if name == "Name" and not version:
            continue  # ConfigItemSearch skips it (the version search orders by name)
        direction = dirs[index] if index < len(dirs) and dirs[index] else "Down"
        keys.append((str(name), direction != "Up"))
    if "ConfigItemID" not in given:
        keys.append(("ConfigItemID", False))
    for name, descending in reversed(keys):
        rows.sort(key=_ORDER_KEYS[name], reverse=descending)


def _valid_order(order_by: list[Any], directions: list[Any]) -> bool:
    """Znuny returns undef (-> an empty answer) for unknown or repeated ``OrderBy`` values and
    for directions other than ``Up``/``Down``."""
    seen: set[str] = set()
    for name in order_by:
        if not isinstance(name, str) or name not in _ORDER_KEYS or name in seen:
            return False
        seen.add(name)
    return all(d in ("Up", "Down") for d in directions)


async def _search_names_valid(reader: Any, catalog_class: str, names: list[Any]) -> bool:
    for name in names:
        if not isinstance(name, str | int | float) or not str(name):
            return False
        item = await reader.catalog_item(catalog_class, str(name))
        if item is None or item.name != str(name):
            return False
    return True


def _search_answer(rows: list[GiRow], limit: int | None) -> dict[str, Any]:
    ids = [r.id for r in rows]
    if limit is not None:
        ids = ids[:limit]
    return {"ConfigItemIDs": ids if ids else ""}


async def op_config_item_search(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """``ConfigItemSearch``: ids of the items of ONE class (``Class`` is required) that match.

    Parameter checks and their order follow ``ConfigItemSearch.pm``; the answer is
    ``{"ConfigItemIDs": [..]}`` or ``{"ConfigItemIDs": ""}`` when nothing matches (Znuny).
    """
    op = "ConfigItemSearch"
    auth = await _agent_auth(op, data, session, session_store, request, settings)
    if isinstance(auth, dict):
        return auth
    user_id, _login = auth
    ports, mode = await _gi_ports(session)
    if (gate := _mode_error(op, mode)) is not None:
        return gate

    raw_item = data.get("ConfigItem")
    if not (isinstance(raw_item, Mapping) and raw_item):
        return _err(
            f"{op}.MissingParameter", f"{op}: ConfigItem parameter is missing or not valid!"
        )
    item: dict[str, Any] = {
        k: (v.strip() if isinstance(v, str) else v) for k, v in raw_item.items()
    }

    cixml = item.get("CIXMLData")
    if cixml is not None:
        if not (isinstance(cixml, Mapping) and cixml):
            return _err(f"{op}.InvalidParameter", f"{op}: ConfigItem->CIXMLData is invalid!")
        try:
            guard_cixml(cixml, op)
        except CixmlError as exc:
            return _err(f"{op}.{exc.code}", exc.message)
        cixml = clean_cixml(cixml)
    if not perl_truthy(item.get("Class")):
        return _err(f"{op}.MissingParameter", f"{op}: ConfigItem->Class parameter is missing!")

    lists: dict[str, list[Any]] = {}
    for key, label in (
        ("InciStates", "ConfigItem->InciStates"),
        ("DeplStates", "DeplStates"),
        ("OrderBy", "OrderBy"),
        ("OrderByDirection", "OrderByDirection"),
    ):
        if item.get(key) is None:
            continue
        parsed = _csv_or_list(item[key])
        if parsed is None:
            return _err(f"{op}.WrongStructure", f"{op}: Structure for {label} is not correct!")
        lists[key] = parsed

    # --- _CheckConfigItem
    class_name = str(item["Class"])
    cls = None
    if mode == "tiqora":
        found = await ports.reader.class_by_key_or_name(class_name)
        cls = found if found is not None and found.name == class_name else None
        if cls is None:
            return _err(f"{op}.InvalidParameter", f"{op}: ConfigItem->Class parameter is invalid!")
        for key, catalog_class in (
            ("InciStates", INCIDENT_STATE),
            ("DeplStates", DEPLOYMENT_STATE),
        ):
            if key in lists and not await _search_names_valid(
                ports.reader, catalog_class, lists[key]
            ):
                return _err(
                    f"{op}.InvalidParameter", f"{op}: ConfigItem->{key} parameter is invalid!"
                )
    times: dict[str, datetime | None] = {}
    for param in _TIME_PARAMS:
        value = item.get(f"ConfigItem{param}")
        if value is None:
            continue
        parsed_time = parse_gi_datetime(value)
        if parsed_time is None:
            return _err(
                f"{op}.InvalidParameter",
                f"{op}: ConfigItem->ConfigItem{param} parameter is invalid!",
            )
        # ConfigItemSearch itself only takes the strict format; anything else gives no result
        times[param] = parsed_time if _STRICT_TIME.fullmatch(str(value)) else None
    limit: int | None = None
    if item.get("Limit") is not None:
        raw_limit = item["Limit"]
        if isinstance(raw_limit, bool) or not _NUMBER.fullmatch(str(raw_limit)):
            return _err(f"{op}.InvalidParameter", f"{op}: ConfigItem->Limit parameter is invalid!")
        limit = max(1, int(float(raw_limit))) if float(raw_limit) > 0 else None
    attributes: dict[str, list[str]] = {}
    if isinstance(cixml, Mapping):
        attributes = _flatten_search(cixml)

    # --- permission (customers hold none)
    if mode == "tiqora":
        assert cls is not None
        visible: set[int] = set()
        if user_id != 0:
            visible = await ports.reader.visible_class_ids(
                user_id, await _permission_type(session, op)
            )
        if cls.id not in visible:
            return denied(op)

    order_by, directions = lists.get("OrderBy", []), lists.get("OrderByDirection", [])
    if not _valid_order(order_by, directions) or any(
        param in times and times[param] is None for param in _TIME_PARAMS
    ):
        logger.info("gi_config_item_search_invalid_order_or_time")
        return {"ConfigItemIDs": ""}
    number = item.get("Number")
    number_list = (
        [str(n) for n in number if _with_data(n)]
        if isinstance(number, list)
        else ([str(number)] if _with_data(number) else None)
    )
    name = str(item["Name"]) if _with_data(item.get("Name")) else None
    previous = perl_truthy(item.get("PreviousVersionSearch"))
    if previous and attributes:
        logger.warning("gi_previous_version_search_unsupported", detail="CIXMLData")
    crit = GiCriteria(
        class_name=class_name,
        numbers=number_list or None,
        name=name,
        deployment_states=[str(x) for x in lists["DeplStates"]] if "DeplStates" in lists else None,
        incident_states=[str(x) for x in lists["InciStates"]] if "InciStates" in lists else None,
        attributes=attributes,
        created_newer=times.get("CreateTimeNewerDate"),
        created_older=times.get("CreateTimeOlderDate"),
        changed_newer=times.get("ChangeTimeNewerDate"),
        changed_older=times.get("ChangeTimeOlderDate"),
        previous_versions=previous,
    )
    try:
        if mode == "znuny_readthrough":
            rows = await facades.get_mode_a_reader(session).search_rows(crit, user_id=user_id)
        else:
            assert cls is not None
            rows = await gi_search.search_tiqora(session, ports.reader, cls, crit)
    except GiSearchError as exc:
        if exc.code == "AccessDenied":
            return denied(op)
        return _err(f"{op}.{exc.code}", exc.message)
    version_mode = bool(name or attributes or previous or "Name" in order_by)
    _order(rows, order_by, directions, version=version_mode)
    return _search_answer(rows, limit)


async def op_config_item_create(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    return await _not_implemented(
        "ConfigItemCreate", data, session, session_store, request, settings
    )


async def op_config_item_update(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    return await _not_implemented(
        "ConfigItemUpdate", data, session, session_store, request, settings
    )


async def op_config_item_delete(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    return await _not_implemented(
        "ConfigItemDelete", data, session, session_store, request, settings
    )


__all__ = [
    "op_config_item_create",
    "op_config_item_delete",
    "op_config_item_get",
    "op_config_item_search",
    "op_config_item_update",
]
