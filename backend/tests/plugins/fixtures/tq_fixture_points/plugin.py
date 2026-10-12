"""Fixture plugin ``org.example.points``: one ``_register_<point>`` function per extension point."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text

from tiqora_sdk import PluginRegistry
from tiqora_sdk.fields import DfBaseType
from tiqora_sdk.hooks import (
    HookContext,
    OperationVetoed,
    SyncHandler,
    SyncOperation,
    TicketCreatePatch,
)
from tiqora_sdk.process import ActionContext
from tiqora_sdk.templates import NumberRequest, PlaceholderRequest

from . import behaviour

_SYNC_OPERATIONS: tuple[SyncOperation, ...] = (
    "ticket.create",
    "article.create",
    "ticket.queue_move",
    "ticket.state_change",
    "ticket.dynamic_fields_update",
)


def _sync_handler(operation: SyncOperation) -> SyncHandler:
    async def handler(ctx: HookContext, op: Any) -> TicketCreatePatch | None:
        behaviour.CALLS.append((f"hook.{operation}", op))
        mode = behaviour.MODE.get(f"hook.{operation}", "ok")
        if mode == "veto":
            raise OperationVetoed("Vertragsnummer fehlt")
        if mode == "hang":
            await asyncio.sleep(30)
        if mode == "raise":
            raise RuntimeError("kaputt")
        if mode == "enrich":
            return TicketCreatePatch(title="Angereichert")
        if mode == "enrich_df":  # title prefix + a dynamic field, built from the op
            return TicketCreatePatch(
                title=f"[geprüft] {op.title}", dynamic_fields={"PP55Flag": ["1"]}
            )
        if mode == "bad_priority":
            return TicketCreatePatch(priority="gibt-es-nicht")
        if mode == "bad_shape_scalar":
            return TicketCreatePatch(dynamic_fields={"X": 5})  # type: ignore[dict-item]
        if mode == "bad_shape_list":
            return TicketCreatePatch(dynamic_fields=["x"])  # type: ignore[arg-type]
        return None

    return handler


def _register_sync_hooks(registry: PluginRegistry) -> None:
    """One hook per operation; behaviour by ``MODE["hook.<operation>"]``."""
    for operation in _SYNC_OPERATIONS:
        registry.before(
            operation, operation.replace(".", "_"), _sync_handler(operation), timeout_ms=100
        )


async def _set_contract(ac: ActionContext) -> None:
    behaviour.CALLS.append(("action.SetContract", dict(ac.config)))
    await ac.plugin.tickets.set_dynamic_field(
        ac.ticket_id, "PP55Contract", [str(ac.config["Contract"])], session=ac.session
    )
    mode = behaviour.MODE.get("action", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "hang":
        await asyncio.sleep(30)
    if mode == "slow":  # succeeds, but takes a fixed time (shared-budget chain test)
        await asyncio.sleep(0.15)
    if mode == "db_hang":  # a long statement on the engine session (cancelled by the timeout)
        dialect = ac.session.get_bind().dialect.name
        sql = "SELECT SLEEP(5)" if dialect == "mysql" else "SELECT pg_sleep(5)"
        await ac.session.execute(text(sql))


def _is_vip(data: Mapping[str, str | list[str] | None], field: str) -> bool:
    mode = behaviour.MODE.get("cond", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "nonbool":
        return "yes"  # type: ignore[return-value]
    return data.get("CustomerID") == "pp55-vip"


def _register_process(registry: PluginRegistry) -> None:
    """Action ``SetContract`` and condition ``IsVip``; behaviour by ``MODE["action"|"cond"]``."""
    registry.transition_action("SetContract", _set_contract, timeout_seconds=2)
    registry.condition_module("IsVip", _is_vip)


async def _greeting(req: PlaceholderRequest) -> str:
    behaviour.CALLS.append(("placeholder.GREETING", req))
    return f"Hallo aus Ticket {req.ticket_id}"


async def _slow(req: PlaceholderRequest) -> str:
    behaviour.CALLS.append(("placeholder.SLOW", req))
    await asyncio.sleep(5)
    return "spät"


async def _broken(req: PlaceholderRequest) -> str:
    behaviour.CALLS.append(("placeholder.BROKEN", req))
    raise RuntimeError("kaputt")


async def _htmlish(req: PlaceholderRequest) -> str:
    return "<b>&</b>"


async def _whoami(req: PlaceholderRequest) -> str:
    behaviour.CALLS.append(("placeholder.WHOAMI", req))
    return f"{req.recipient.get('useremail', '-')}|{req.bracket}|{req.time_zone}"


def _register_placeholders(registry: PluginRegistry) -> None:
    """``GREETING``, ``SLOW`` (sleeps 5 s), ``BROKEN`` (raises), ``HTMLISH``, ``WHOAMI``."""
    registry.placeholder("GREETING", _greeting)
    registry.placeholder("SLOW", _slow, timeout_ms=100)
    registry.placeholder("BROKEN", _broken)
    registry.placeholder("HTMLISH", _htmlish)
    registry.placeholder("WHOAMI", _whoami)


def _yearly(req: NumberRequest) -> str:
    behaviour.CALLS.append(("number.Yearly", req))
    return f"{req.now_local:%Y}-{req.counter:06d}"


def _daily(req: NumberRequest) -> str:
    return f"{req.now_local:%Y%m%d}-{req.counter:04d}"


def _broken_number(req: NumberRequest) -> str:
    """``MODE["tn"]``: raise | empty | long | nomatch | hang | same (``MODE["tn.same"]``)."""
    behaviour.CALLS.append(("number.Broken", req))
    mode = behaviour.MODE.get("tn", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "empty":
        return ""
    if mode == "long":
        return "BRK-" + "9" * 56
    if mode == "nomatch":
        return "NOPE-1"
    if mode == "same":
        return behaviour.MODE["tn.same"]
    if mode == "hang":
        import time

        time.sleep(3)
    return f"BRK-{req.offset + 1:06d}"


def _register_number_generators(registry: PluginRegistry) -> None:
    """``Yearly`` (counter, no date reset), ``Daily`` (counter, resets daily), ``Broken``."""
    registry.number_generator(
        "Yearly", _yearly, label="Jahresnummer", pattern=r"\d{4}-\d{6}", uses_counter=True
    )
    registry.number_generator(
        "Daily",
        _daily,
        label="Tagesnummer",
        pattern=r"\d{8}-\d{4}",
        uses_counter=True,
        date_based=True,
    )
    registry.number_generator(
        "Broken", _broken_number, label="Defekt", pattern=r"BRK-\d{6}", uses_counter=False
    )


class ContractConfig(BaseModel):
    prefix: str = "V-"


_CONTRACT_RE = re.compile(r"V-\d{1,6}")


def _validate_contract(values: list[str], config: Mapping[str, Any]) -> list[str]:
    """``MODE["df"]``: raise (RuntimeError) | hang (3 s) | badresult (not a list of str)."""
    behaviour.CALLS.append(("df.validate", (list(values), dict(config))))
    mode = behaviour.MODE.get("df", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "hang":
        import time

        time.sleep(3)
    if mode == "badresult":
        return 5  # type: ignore[return-value]
    out: list[str] = []
    for value in values:
        upper = value.upper()
        if not _CONTRACT_RE.fullmatch(upper):
            raise ValueError("Vertragsnummer muss wie V-123 aussehen")
        out.append(upper)
    return out


def _display_contract(values: list[str], config: Mapping[str, Any]) -> str:
    if behaviour.MODE.get("df.display") == "raise":
        raise RuntimeError("kaputt")
    return ", ".join(f"Vertrag {v}" for v in values)


def _register_df_types(registry: PluginRegistry) -> None:
    """``Contract`` (base Text): ``V-<digits>``, upper-cased, shown as "Vertrag V-12"."""
    base: DfBaseType = "Text"
    registry.dynamic_field_type(
        "Contract",
        label="Vertragsnummer",
        base_type=base,
        validate=_validate_contract,
        display=_display_contract,
        config_model=ContractConfig,
    )


class PointsPlugin:
    def register(self, registry: PluginRegistry) -> None:
        # Each task of the plugin-points plan adds its ``_register_<point>(registry)`` call here.
        _register_sync_hooks(registry)
        _register_process(registry)
        _register_placeholders(registry)
        _register_number_generators(registry)
        _register_df_types(registry)


plugin = PointsPlugin()
