"""GenericInterface ``ConfigItem*`` operations on the Tiqora CMDB (plan D9, Tasks 21-24).

Port of ``Kernel/GenericInterface/Operation/ConfigItem/*.pm`` of ITSMConfigurationManagement 6.5.4
and 7.3.5 (the two versions are identical for ``ConfigItemGet``; notes with file and line
references in ``backend/tests/fixtures/itsm/gi_shapes.md``). This module is the common layer
(mode gate, authentication, class-group permission, ``GiLookups``) plus ``ConfigItemGet``,
``ConfigItemSearch``, ``ConfigItemCreate`` and ``ConfigItemUpdate``; ``ConfigItemDelete`` answers
``Operation.NotImplemented`` until Task 24.

``ConfigItemCreate`` / ``ConfigItemUpdate`` (Task 23) write through cmdb's own ``CiService``
(numbering, history, class-group check, index and incident propagation are its job) and
``AttachmentService``, not through the importer's ``CmdbWritePort``: that port keeps Znuny
numbers and timestamps and has no create/attachment calls for live writes.

Modes (open question 4): ``tiqora`` serves the Tiqora CMDB, ``znuny_readthrough`` (Mode A) serves
reads from Znuny's own tables through the importer facade and refuses writes with
``<Op>.NotAvailable``, ``off`` answers ``Operation.NotImplemented``. The mode is looked at after
authentication, so an anonymous caller does not learn it.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.article_input import gi_error as _err
from tiqora.api.compat.operations import _auth_from_params, _reprefix_auth_error
from tiqora.cmdb.access import load_access
from tiqora.cmdb.attachments import AttachmentInUse, AttachmentService
from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.definition import dump_definition_json
from tiqora.cmdb.errors import CmdbError, CmdbForbidden, CmdbNotFound
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.cmdb.service import CiService, InvalidValues
from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.domain.auth import SessionStore
from tiqora.itsm.importer import facades, gi_search
from tiqora.itsm.importer.gi_search import GiCriteria, GiRow, GiSearchError
from tiqora.itsm.importer.ports import CiView, Ports
from tiqora.itsm.znuny_format.definition import to_znuny_definition
from tiqora.itsm.znuny_format.gi_cixml import (
    CixmlError,
    GiLookups,
    cixml_to_xmldata,
    clean_cixml,
    guard_cixml,
    parse_gi_datetime,
    xmldata_to_cixml,
)
from tiqora.itsm.znuny_format.xmldata import attributes_to_xmldata, xmldata_to_attributes
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
#: one attachment of ``ConfigItemCreate`` / ``ConfigItemUpdate`` (Znuny has no limit; the whole
#: request is bounded by the global body limit, this keeps a single file inside the CMDB's budget)
MAX_ATTACHMENT_BYTES: Final = 50 * 1024 * 1024
_CUSTOMER_PROBE_MAX: Final = 1000
_CUSTOMER_PROBE_LEN: Final = 250


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


def _has_input_type(definition: Sequence[Mapping[str, Any]], kinds: frozenset[str]) -> bool:
    for item in definition:
        inp = item.get("Input")
        if isinstance(inp, Mapping) and inp.get("Type") in kinds:
            return True
        sub = item.get("Sub")
        if isinstance(sub, list) and _has_input_type(sub, kinds):
            return True
    return False


def _string_leaves(node: Any, out: set[str]) -> None:
    """Short string values of ``CIXMLData`` (bounded; the caller already guarded its size)."""
    stack = [node]
    while stack and len(out) < _CUSTOMER_PROBE_MAX:
        cur = stack.pop()
        if isinstance(cur, Mapping):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str) and 0 < len(cur) <= _CUSTOMER_PROBE_LEN:
            out.add(cur)


async def _existing_customers(
    session: AsyncSession, definition: Sequence[Mapping[str, Any]], cixml: Any
) -> tuple[frozenset[str], frozenset[str]]:
    """Which strings of ``cixml`` are valid customer logins / customer ids (only looked up when
    the definition has ``Customer`` / ``CustomerCompany`` inputs)."""
    users: frozenset[str] = frozenset()
    companies: frozenset[str] = frozenset()
    wants_user = _has_input_type(definition, frozenset({"Customer"}))
    wants_company = _has_input_type(definition, frozenset({"CustomerCompany"}))
    if cixml is None or not (wants_user or wants_company):
        return users, companies
    probes: set[str] = set()
    _string_leaves(cixml, probes)
    values = sorted(probes)
    if wants_user and values:
        rows = await session.execute(
            select(CustomerUser.login).where(
                CustomerUser.login.in_(values), CustomerUser.valid_id == 1
            )
        )
        users = frozenset(str(r) for r in rows.scalars())
    if wants_company and values:
        rows = await session.execute(
            select(CustomerCompany.customer_id).where(
                CustomerCompany.customer_id.in_(values), CustomerCompany.valid_id == 1
            )
        )
        companies = frozenset(str(r) for r in rows.scalars())
    return users, companies


async def _gi_lookups(
    session: AsyncSession,
    definition: Sequence[Mapping[str, Any]],
    *,
    mode: Mode = "tiqora",
    cixml: Any = None,
) -> GiLookups:
    """Catalog names/ids for the ``GeneralCatalog`` classes of ``definition``.

    ``tiqora``: ids of ``tiqora_cmdb_catalog``; ``znuny_readthrough``: Znuny ``general_catalog``
    ids (names only). ``cixml`` (writes only) is probed for existing customers and companies.
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
    users, companies = await _existing_customers(session, definition, cixml)
    return _Lookups(names, ids, users, companies)


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


# ----------------------------------------------------------------- ConfigItemCreate/Update

_ATTACHMENT_FIELDS: Final = ("Content", "ContentType", "Filename")
_CHARSET: Final = re.compile(r"charset=[\"']?(\w+)")
_MIME: Final = re.compile(r"(\w+/\w+)")


class _Attachment:
    __slots__ = ("content", "content_type", "filename")

    def __init__(self, filename: str, content_type: str, content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self.content = content


class _GiResolver:
    """``GeneralCatalog`` values are Tiqora catalog ids already and ``CIClassReference`` values
    are Tiqora CI ids (Mode B): plain integers stay what they are."""

    def ci(self, znuny_ci_id: str) -> int | None:
        return int(znuny_ci_id) if znuny_ci_id.isascii() and znuny_ci_id.isdigit() else None

    def catalog(self, znuny_gc_id: str) -> int | None:
        return int(znuny_gc_id) if znuny_gc_id.isascii() and znuny_gc_id.isdigit() else None


def _max_base64_length() -> int:
    """Base64 text longer than this cannot decode to ``MAX_ATTACHMENT_BYTES`` (line breaks)."""
    return (MAX_ATTACHMENT_BYTES // 3 + 1) * 4 + MAX_ATTACHMENT_BYTES // 40 + 4


def _strip_all(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}


def _check_attachments(op: str, raw: Any) -> list[_Attachment] | dict[str, Any]:
    """``ConfigItem->Attachment`` (one hash or a list): ``_CheckAttachment`` of both operations
    plus the size limit; decoded here so that nothing is written when one file is bad."""
    invalid = _err(f"{op}.InvalidParameter", f"{op}: ConfigItem->Attachment parameter is invalid!")
    if isinstance(raw, Mapping) and raw:
        items: list[Any] = [raw]
    elif isinstance(raw, list) and raw:
        items = list(raw)
    else:
        return invalid
    update = op == "ConfigItemUpdate"
    out: list[_Attachment] = []
    for entry in items:
        if not (isinstance(entry, Mapping) and entry):
            return invalid
        item = _strip_all(entry)
        for needed in _ATTACHMENT_FIELDS:
            value = item.get(needed)
            if (value is None) if update else (not perl_truthy(value)):
                gap = "  " if update else " "  # ConfigItemUpdate.pm has two spaces here
                return _err(
                    f"{op}.MissingParameter",
                    f"{op}: Attachment->{needed}{gap}parameter is missing!",
                )
        content_type = str(item["ContentType"]).lower()
        bad_type = _err(f"{op}.InvalidParameter", f"{op}: Attachment->ContentType is invalid!")
        if content_type:
            charset = _CHARSET.search(content_type)
            if charset is not None:
                try:
                    codecs.lookup(charset.group(1))
                except LookupError:
                    return bad_type
            if _MIME.match(content_type) is None:
                return bad_type
        encoded = item["Content"]
        too_large = _err(
            f"{op}.InvalidParameter",
            f"{op}: Attachment->Content is too large (at most {MAX_ATTACHMENT_BYTES >> 20} MB)!",
        )
        if not isinstance(encoded, str):
            return _err(f"{op}.InvalidParameter", f"{op}: Attachment->Content is invalid!")
        if len(encoded) > _max_base64_length():
            return too_large
        try:
            content = base64.b64decode(encoded)
        except (binascii.Error, ValueError):
            return _err(f"{op}.InvalidParameter", f"{op}: Attachment->Content is invalid!")
        if len(content) > MAX_ATTACHMENT_BYTES:
            return too_large
        out.append(_Attachment(str(item["Filename"]), content_type, content))
    return out


async def _state_ids(session: AsyncSession, catalog_class: str) -> dict[str, int]:
    """Valid catalog items by exact name (``ItemList`` reversed, like ``Common.pm``)."""
    return {i.name: i.id for i in await CatalogService(session).items(catalog_class)}


def _definition_schema(definition: Any) -> dict[str, Any]:
    return {"attributes": json.loads(dump_definition_json(list(definition.attributes)))}


def _value_error(op: str, exc: InvalidValues) -> dict[str, Any]:
    issue = exc.issues[0] if exc.issues else None
    if issue is not None and issue.path.split(".")[0] == "name":
        return _err(f"{op}.InvalidParameter", f"{op}: ConfigItem->Name parameter is invalid!")
    where = f" ({issue.path}: {issue.message})" if issue is not None else ""
    return _err(f"{op}.InvalidParameter", f"{op}: ConfigItem->CIXMLData is invalid{where}!")


async def _gi_write(
    op: Literal["ConfigItemCreate", "ConfigItemUpdate"],
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    request: Any | None,
    settings: Settings | None,
) -> dict[str, Any]:
    """Shared body of ``ConfigItemCreate`` and ``ConfigItemUpdate`` (check order of the Perl
    ``Run()``: parameters, ``_CheckConfigItem``, permission, attachments, then the write)."""
    auth = await _agent_auth(op, data, session, session_store, request, settings)
    if isinstance(auth, dict):
        return auth
    user_id, _login = auth
    ports, mode = await _gi_ports(session)
    if (gate := _mode_error(op, mode)) is not None:
        return gate
    update = op == "ConfigItemUpdate"

    raw_item = data.get("ConfigItem")
    if not (isinstance(raw_item, Mapping) and raw_item):
        return _err(
            f"{op}.MissingParameter", f"{op}: ConfigItem parameter is missing or not valid!"
        )
    item = _strip_all(raw_item)

    writable: set[int] = set()
    if user_id != 0:
        writable = await ports.reader.visible_class_ids(
            user_id, await _permission_type(session, op)
        )
    view: CiView | None = None
    if update:
        ci_id = (
            _parse_id(data.get("ConfigItemID"))
            if not isinstance(data.get("ConfigItemID"), bool)
            else None
        )
        if ci_id is None:
            return _err(
                f"{op}.MissingParameter", f"{op}: ConfigItemID parameter is missing or not valid!"
            )
        if not writable:  # a caller without any write right learns nothing about ids
            return denied(op)
        view = await ports.reader.get_ci(ci_id)
        if view is None:
            return _err(f"{op}.InvalidParameter", f"{op}: ConfigItemID is invalid!")
        if view.class_id not in writable:  # the class of the item decides, not the request
            return denied(op)

    missing_cixml = _err(
        f"{op}.MissingParameter", f"{op}: ConfigItem->CIXMLData is missing or invalid!"
    )
    cixml: Any = item.get("CIXMLData")
    replace = update and perl_truthy(data.get("ReplaceExistingData"))
    if update:
        if not replace or cixml is None:
            cixml = cixml if perl_truthy(cixml) else {}
            if not isinstance(cixml, Mapping):
                return missing_cixml
        # ReplaceExistingData with CIXMLData: taken as given, "empty or invalid" is checked below
    elif cixml is not None and not (isinstance(cixml, Mapping) and cixml):
        return missing_cixml
    if isinstance(cixml, Mapping):
        try:
            guard_cixml(cixml, op)
        except CixmlError as exc:
            return _err(f"{op}.{exc.code}", exc.message)
        cixml = clean_cixml(cixml)

    # --- the header (Perl _CheckConfigItem)
    for needed in ("Class", "Name", "DeplState", "InciState"):
        value = item.get(needed)
        if (value is None) if update else (not perl_truthy(value)):
            return _err(
                f"{op}.MissingParameter", f"{op}: ConfigItem->{needed} parameter is missing!"
            )
    class_name = str(item["Class"])
    found = await ports.reader.class_by_key_or_name(class_name)
    class_rec = None
    if found is not None and found.name == class_name:
        class_rec = await ClassService(session).get(found.id)
    if (
        class_rec is None
        or not class_rec.valid
        or (view is not None and class_rec.id != view.class_id)
    ):
        return _err(f"{op}.InvalidParameter", f"{op}: ConfigItem->Class parameter is invalid!")
    depl_ids = await _state_ids(session, DEPLOYMENT_STATE)
    inci_ids = await _state_ids(session, INCIDENT_STATE)
    if str(item["DeplState"]) not in depl_ids:
        return _err(f"{op}.InvalidParameter", f"{op}: ConfigItem->DeplState parameter is invalid!")
    if str(item["InciState"]) not in inci_ids:
        return _err(f"{op}.InvalidParameter", f"{op}: ConfigItem->InciState parameter is invalid!")

    class_def = await ClassService(session).current_definition(class_rec.id)
    schema = _definition_schema(class_def)
    definition = to_znuny_definition(schema)
    lookups: GiLookups
    if update:
        assert view is not None
        old_definition = to_znuny_definition(view.schema)
        if not replace or item.get("CIXMLData") is None:
            lookups = await _gi_lookups(session, [*old_definition, *definition], mode=mode)
            try:
                existing = xmldata_to_cixml(
                    attributes_to_xmldata(view.attributes, view.schema),
                    old_definition,
                    lookups=lookups,
                )
            except ValueError as exc:
                logger.warning("gi_config_item_update_unconvertible", ci_id=view.id, error=str(exc))
                return _err(f"{op}.InvalidParameter", f"{op}: Could not get ConfigItem data")
            cixml = {**existing, **(cixml if isinstance(cixml, Mapping) else {})}
        if not (isinstance(cixml, Mapping) and cixml):
            return _err(
                f"{op}.InvalidParameter", f"{op}: ConfigItem->CIXMLData is empty or invalid!"
            )
    lookups = await _gi_lookups(session, definition, mode=mode, cixml=cixml)
    try:
        xml_data = cixml_to_xmldata(
            cixml if isinstance(cixml, Mapping) else {}, definition, lookups=lookups, operation=op
        )
    except CixmlError as exc:
        return _err(f"{op}.{exc.code}", exc.message)

    # --- permission on the class (the one of the request == the one of the item)
    if class_rec.id not in writable:
        return denied(op)

    attachments: list[_Attachment] = []
    if item.get("Attachment") is not None:
        checked = _check_attachments(op, item["Attachment"])
        if isinstance(checked, dict):
            return checked
        attachments = checked

    attributes, issues = xmldata_to_attributes(xml_data, schema, _GiResolver())
    for issue in issues:
        if issue.code != "unknown_attribute":  # keys outside the definition are dropped
            return _err(
                f"{op}.InvalidParameter",
                f"{op}: ConfigItem->CIXMLData->{issue.path} parameter value is invalid!",
            )
    known = {str(a["key"]) for a in schema["attributes"]}
    attributes = {k: v for k, v in attributes.items() if k in known}

    # --- write (one transaction; any failure leaves nothing behind)
    try:
        access = await load_access(session, user_id)
        svc = CiService(session)
        name = str(item["Name"])
        dep_id, inc_id = depl_ids[str(item["DeplState"])], inci_ids[str(item["InciState"])]
        files = AttachmentService(session)
        if update:
            assert view is not None
            ci_id = view.id
            await svc.update(
                access,
                ci_id,
                expected_lock_version=None,
                name=name,
                deployment_state_id=dep_id,
                incident_state_id=inc_id,
                attributes=attributes,
                drop_unknown=True,
            )
            if replace:
                for old in await files.list_for_ci(access, ci_id):
                    try:
                        await files.delete(access, old.id)
                    except AttachmentInUse:  # an attribute of a stored version still points at it
                        logger.info("gi_config_item_update_attachment_kept", attachment_id=old.id)
            number = view.number
        else:
            created = await svc.create(
                access,
                class_id=class_rec.id,
                name=name,
                deployment_state_id=dep_id,
                incident_state_id=inc_id,
                attributes=attributes,
            )
            ci_id, number = created.id, created.number
        for att in attachments:
            await files.upload(
                access,
                ci_id=ci_id,
                filename=att.filename,
                content_type=att.content_type,
                data=att.content,
            )
        await session.commit()
    except InvalidValues as exc:
        await session.rollback()
        return _value_error(op, exc)
    except (CmdbForbidden, CmdbNotFound):
        await session.rollback()
        return denied(op)
    except CmdbError as exc:
        await session.rollback()
        logger.warning("gi_config_item_write_refused", op=op, error=str(exc))
        return _err(f"{op}.InvalidParameter", f"{op}: ConfigItem parameter is invalid!")
    except Exception:
        await session.rollback()
        raise
    return {"ConfigItemID": ci_id, "Number": number}


async def op_config_item_create(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """``ConfigItemCreate``: a new item of the Tiqora CMDB; answers ``ConfigItemID`` + ``Number``.

    ``ConfigItem->Number`` is ignored: numbers come from the class's own generator.
    """
    return await _gi_write("ConfigItemCreate", data, session, session_store, request, settings)


async def op_config_item_update(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """``ConfigItemUpdate``: a new version of ``ConfigItemID`` (none when nothing changed).

    ``CIXMLData`` is merged over the current data at top level; ``ReplaceExistingData`` takes it
    as given and replaces the attachments instead of adding to them.
    """
    return await _gi_write("ConfigItemUpdate", data, session, session_store, request, settings)


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
