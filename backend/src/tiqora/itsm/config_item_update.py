"""``ConfigItemUpdate`` attribute merge, ported from Znuny (pure part).

Port of ``ConfigItemAttributesGet`` (Znuny 6.5 ``ProcessManagement/TransitionAction/
ConfigItemUpdate.pm``): the flat transition-action parameters become one CI update. Header keys
are kept as header fields, every other key is a (stringified) attribute path, list keys are
expanded, and the result is merged with the current attribute values along the class definition.
Catalog and CI reference values stay ids (no resolution happens here).

:func:`action_config_item_update` is the transition-action handler on the Tiqora CMDB (mode
``tiqora`` only). Like Znuny's module it runs as the acting user without a class permission check,
logs and skips a CI it cannot update, and never stops the transition.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.definition import AttributeDef, dump_definition_json
from tiqora.cmdb.errors import CmdbError
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.cmdb.service import CiService, system_access
from tiqora.db.legacy.customer import CustomerUser
from tiqora.itsm.mode import get_itsm_mode
from tiqora.itsm.znuny_format.definition import to_znuny_definition
from tiqora.itsm.znuny_format.stringified import (
    HEADER_KEYS,
    expand_list_keys,
    stringified_to_xmldata,
    xmldata_to_stringified,
)
from tiqora.itsm.znuny_format.xmldata import (
    attributes_to_xmldata,
    xmldata_to_attributes,
)
from tiqora.process.actions.registry import ActionUnsupported
from tiqora.util.perl import perl_truthy
from tiqora.znuny.sysconfig import SysConfig

__all__ = [
    "HEADER_KEYS",
    "CiUpdate",
    "UnsupportedInMode",
    "action_config_item_update",
    "build_update",
]

logger = logging.getLogger(__name__)

CHANGE_NOTE = "Prozess-Aktion ConfigItemUpdate"
_CUSTOMER_VALUES_MAX = 50


@dataclass(frozen=True)
class CiUpdate:
    name: str | None
    depl_state_name: str | None
    inci_state_name: str | None
    depl_state_id: int | None
    inci_state_id: int | None
    attributes: dict[str, Any]


class _IdResolver:
    """Ids stay ids: Znuny ids of catalog entries and CIs are kept as they are."""

    def ci(self, znuny_ci_id: str) -> int | None:
        return _to_int(znuny_ci_id)

    def catalog(self, znuny_gc_id: str) -> int | None:
        return _to_int(znuny_gc_id)


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _header_text(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    return str(value) if perl_truthy(value) else None


def _header_id(config: Mapping[str, Any], key: str) -> int | None:
    value = config.get(key)
    return _to_int(value) if perl_truthy(value) else None


def build_update(
    config: Mapping[str, Any],
    *,
    current_attributes: Mapping[str, Any],
    schema: Mapping[str, Any],
    separator: str = ",",
    customer_lookup: Callable[[str], list[str]] | None = None,
) -> CiUpdate:
    """Merge the transition-action ``config`` into the CI's current attributes.

    ``separator`` is the default; a truthy ``Separator`` in ``config`` overrides it (Znuny's
    ``$Param{Separator} || ','``). Keys the class definition does not name are dropped.
    """
    sep = config.get("Separator")
    effective = str(sep) if perl_truthy(sep) else separator
    definition = to_znuny_definition(schema)
    original = xmldata_to_stringified(attributes_to_xmldata(current_attributes, schema))
    flat = expand_list_keys(config, effective)
    merged = stringified_to_xmldata(flat, definition, original, customer_lookup=customer_lookup)
    attributes, _issues = xmldata_to_attributes(merged, schema, _IdResolver())
    return CiUpdate(
        name=_header_text(config, "Name"),
        depl_state_name=_header_text(config, "DeplStateName"),
        inci_state_name=_header_text(config, "InciStateName"),
        depl_state_id=_header_id(config, "DeplStateID"),
        inci_state_id=_header_id(config, "InciStateID"),
        attributes=attributes,
    )


# --- the transition action ----------------------------------------------------------------


class UnsupportedInMode(ActionUnsupported):
    """The action only acts in mode ``tiqora``; the engine reports it as unsupported."""


def _split_list(value: Any) -> list[str]:
    """Znuny's ``split /\\s*,\\s*/`` over a comma list; empty entries are dropped."""
    return [part for part in (p.strip() for p in str(value).split(",")) if part]


def _has_customer_input(attrs: list[AttributeDef]) -> bool:
    return any(a.type == "customer_user" or _has_customer_input(list(a.sub)) for a in attrs)


async def _customer_logins(session: AsyncSession, value: str) -> list[str]:
    """Customer search: exact login first, else a ``LIKE`` over login, e-mail and names.

    Valid users only, at most two rows (enough to tell "unique" from "ambiguous").
    """
    exact = (
        await session.execute(
            select(CustomerUser.login)
            .where(CustomerUser.login == value, CustomerUser.valid_id == 1)
            .limit(2)
        )
    ).scalars()
    found = [str(x) for x in exact.all()]
    if found:
        return found[:1]
    like = (
        await session.execute(
            select(CustomerUser.login)
            .where(
                CustomerUser.valid_id == 1,
                or_(
                    CustomerUser.login.contains(value, autoescape=True),
                    CustomerUser.email.contains(value, autoescape=True),
                    CustomerUser.first_name.contains(value, autoescape=True),
                    CustomerUser.last_name.contains(value, autoescape=True),
                ),
            )
            .order_by(CustomerUser.login)
            .limit(2)
        )
    ).scalars()
    return [str(x) for x in like.all()]


async def _customer_lookup(
    session: AsyncSession, config: Mapping[str, Any]
) -> Callable[[str], list[str]]:
    """``build_update`` wants a sync lookup: search every distinct text value up front."""
    values: list[str] = []
    for key, raw in config.items():
        text = str(raw).strip() if isinstance(raw, str | int) else ""
        if text and key not in HEADER_KEYS and text not in values:
            values.append(text)
    table = {v: await _customer_logins(session, v) for v in values[:_CUSTOMER_VALUES_MAX]}
    return lambda value: table.get(value, [])


async def _state_id(
    session: AsyncSession, catalog_class: str, name: str | None, id_: int | None
) -> int | None:
    """The valid catalog item named ``name`` (the id wins when both are given)."""
    for item in await CatalogService(session).items(catalog_class):
        if id_ is not None:
            if item.id == id_:
                return item.id
        elif name is not None and item.name.casefold() == name.casefold():
            return item.id
    return None


async def _update_one(
    session: AsyncSession,
    ci_id: int,
    config: Mapping[str, Any],
    acting_user: int,
) -> bool:
    """One CI; ``False`` when it was skipped (the reason is logged)."""
    access = system_access(acting_user)
    svc = CiService(session)
    record = await svc.get(access, ci_id, enforce_permissions=False)
    classes = ClassService(session)
    definition = await classes.current_definition(record.class_id)
    schema = {"attributes": json.loads(dump_definition_json(list(definition.attributes)))}
    lookup = (
        await _customer_lookup(session, config)
        if _has_customer_input(list(definition.attributes))
        else None
    )
    upd = build_update(
        config,
        current_attributes=record.attributes,
        schema=schema,
        customer_lookup=lookup,
    )
    deployment: int | None = None
    incident: int | None = None
    if upd.depl_state_id is not None or upd.depl_state_name is not None:
        deployment = await _state_id(
            session, DEPLOYMENT_STATE, upd.depl_state_name, upd.depl_state_id
        )
        if deployment is None:  # Znuny: the version add fails, nothing is written
            logger.warning(
                "ConfigItemUpdate: unknown deployment state %r for CI %s",
                upd.depl_state_name or upd.depl_state_id,
                ci_id,
            )
            return False
    if upd.inci_state_id is not None or upd.inci_state_name is not None:
        incident = await _state_id(session, INCIDENT_STATE, upd.inci_state_name, upd.inci_state_id)
        if incident is None:
            logger.warning(
                "ConfigItemUpdate: unknown incident state %r for CI %s",
                upd.inci_state_name or upd.inci_state_id,
                ci_id,
            )
            return False
    await svc.update(
        access,
        ci_id,
        expected_lock_version=None,
        name=upd.name,
        deployment_state_id=deployment,
        incident_state_id=incident,
        attributes=upd.attributes,
        change_note=CHANGE_NOTE,
        enforce_permissions=False,
    )
    return True


async def action_config_item_update(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Znuny's ``ConfigItemUpdate`` transition action on the Tiqora CMDB.

    ``ConfigItemID`` / ``ConfigItemNumber`` are comma lists (the id wins); every CI is written in
    its own savepoint, so one bad CI (unknown number, unknown state, invalid value) is logged and
    leaves the others and the transition untouched. ``UserID`` / ``DynamicField_UserID`` replace
    the acting user. Outside mode ``tiqora`` the action raises :class:`UnsupportedInMode`.
    """
    del sysconfig
    if (await get_itsm_mode(session, fresh=True)).mode != "tiqora":
        raise UnsupportedInMode("ConfigItemUpdate")
    from tiqora.process.engine import _acting_user

    acting = _acting_user(config, user_id, "ConfigItemUpdate")
    ids: list[int] = []
    if perl_truthy(config.get("ConfigItemID")):
        for part in _split_list(config["ConfigItemID"]):
            value = _to_int(part)
            if value is None:
                logger.warning("ConfigItemUpdate: ConfigItemID %r is not a number", part)
            elif value not in ids:
                ids.append(value)
    elif perl_truthy(config.get("ConfigItemNumber")):
        svc = CiService(session)
        for number in _split_list(config["ConfigItemNumber"]):
            try:
                found = await svc.get_by_number(
                    system_access(acting), number, enforce_permissions=False
                )
            except CmdbError:
                logger.warning(
                    "ConfigItemUpdate: no CI with number %r (ticket %s)", number, ticket_id
                )
                continue
            if found.id not in ids:
                ids.append(found.id)
    else:
        logger.warning("ConfigItemUpdate: ConfigItemID or ConfigItemNumber is required")
        return
    for ci_id in ids:
        try:
            async with session.begin_nested():
                await _update_one(session, ci_id, config, acting)
        except Exception:
            logger.warning(
                "ConfigItemUpdate: CI %s not updated (ticket %s)", ci_id, ticket_id, exc_info=True
            )
