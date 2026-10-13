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
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.article_input import gi_error as _err
from tiqora.api.compat.operations import _auth_from_params, _reprefix_auth_error
from tiqora.cmdb.catalog import CatalogService
from tiqora.config import Settings
from tiqora.domain.auth import SessionStore
from tiqora.itsm.importer import facades
from tiqora.itsm.importer.ports import CiView, Ports
from tiqora.itsm.znuny_format.definition import to_znuny_definition
from tiqora.itsm.znuny_format.gi_cixml import GiLookups, xmldata_to_cixml
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


async def op_config_item_search(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    return await _not_implemented(
        "ConfigItemSearch", data, session, session_store, request, settings
    )


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
