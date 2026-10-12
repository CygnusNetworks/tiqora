"""Fixture plugin ``org.example.points``: one ``_register_<point>`` function per extension point."""

from __future__ import annotations

import asyncio
from typing import Any

from tiqora_sdk import PluginRegistry
from tiqora_sdk.hooks import (
    HookContext,
    OperationVetoed,
    SyncHandler,
    SyncOperation,
    TicketCreatePatch,
)

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


class PointsPlugin:
    def register(self, registry: PluginRegistry) -> None:
        # Each task of the plugin-points plan adds its ``_register_<point>(registry)`` call here.
        _register_sync_hooks(registry)


plugin = PointsPlugin()
