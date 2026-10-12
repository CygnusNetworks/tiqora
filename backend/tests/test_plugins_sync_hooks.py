"""Synchronous before-hooks (D4): runner, budget, breaker, registration. No database."""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests._plugin_points import PLUGIN_ID, boot_points_plugin, points_behaviour
from tiqora.extensions.registry import Registry
from tiqora.plugins import sync_hooks
from tiqora.plugins.context import PluginContext, PluginHttp, PluginTickets, TicketSummary
from tiqora.plugins.manifest import PluginManifest
from tiqora.plugins.registrar import PluginRegistrar, RegistrationRejected
from tiqora.plugins.state import PluginLoadResult, RuntimeState, set_loaded_plugins
from tiqora.plugins.sync_hooks import (
    SYNC_HOOKS,
    OperationVetoed,
    StateChangeOp,
    SyncBreaker,
    SyncHook,
    TicketCreateOp,
    hook_key,
    run_sync_hooks,
)


@pytest.fixture(autouse=True)
def _breaker() -> Iterator[None]:
    SyncBreaker.reset_for_tests()
    SyncBreaker.persistence = False
    yield
    SyncBreaker.reset_for_tests()


def _create_op() -> TicketCreateOp:
    return TicketCreateOp(
        queue_id=2,
        queue="Raw",
        title="Drucker defekt",
        state="new",
        priority="3 normal",
        type=None,
        customer_user="kunde@example.org",
        customer_id="example-org",
        dynamic_fields={"Contract": ("V-1",)},
        article=None,
    )


def _state_op() -> StateChangeOp:
    ticket = TicketSummary(
        id=7,
        tn="2026101200001",
        title="t",
        queue_id=2,
        queue_name="Raw",
        state="new",
        state_type="new",
        priority="3 normal",
        owner_id=1,
        customer_user_id=None,
        customer_id=None,
    )
    return StateChangeOp(ticket=ticket, new_state="open", new_state_type="open", pending_until=None)


def _hook(registry: Registry, operation: str = "ticket.create") -> SyncHook:
    (hook,) = [h for h in registry.items(SYNC_HOOKS) if h.operation == operation]
    return hook


def _registry_of(*hooks: SyncHook) -> Registry:
    registry = Registry()
    for h in hooks:
        registry.add(SYNC_HOOKS, h, owner=h.plugin_id)
    return registry


async def _never_called(table: str, name: str) -> bool:
    raise AssertionError("no name lookup expected")


async def _run(op: Any, registry: Registry | None = None, operation: Any = "ticket.create") -> Any:
    return await run_sync_hooks(
        operation, op, agent_id=1, registry=registry, name_exists=_never_called
    )


async def test_ok_returns_op_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot_points_plugin(tmp_path, monkeypatch)
    op = _create_op()
    out = await _run(op)
    assert out is op
    calls = points_behaviour().CALLS
    assert len(calls) == 1 and calls[0][0] == "hook.ticket.create" and calls[0][1] is op


async def test_veto_raises_with_plugin_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    points_behaviour().MODE["hook.ticket.create"] = "veto"
    with pytest.raises(OperationVetoed) as caught:
        await _run(_create_op())
    assert str(caught.value) == "Vertragsnummer fehlt"
    assert caught.value.plugin_id == PLUGIN_ID
    assert (
        SyncBreaker.failures(hook_key(_hook(registry))) == 0
    )  # a veto is a decision, not a failure


async def test_timeout_fail_open_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    points_behaviour().MODE["hook.ticket.create"] = "hang"
    op = _create_op()
    started = time.monotonic()
    out = await _run(op)
    assert time.monotonic() - started < 0.5
    assert out is op
    assert SyncBreaker.failures(hook_key(_hook(registry))) == 1


async def test_fail_closed_vetoes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    points_behaviour().MODE["hook.ticket.create"] = "raise"
    deny = dataclasses.replace(_hook(registry), on_error="deny")
    with pytest.raises(OperationVetoed) as caught:
        await _run(_create_op(), _registry_of(deny))
    assert str(caught.value).startswith("Prüfung durch Erweiterung „ticket_create“ fehlgeschlagen")
    assert "kaputt" not in str(caught.value)  # the plugin's error text stays in the log
    assert caught.value.plugin_id == PLUGIN_ID


async def test_budget_shared_across_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(sync_hooks, "OPERATION_BUDGET_SECONDS", 0.3)
    points_behaviour().MODE["hook.ticket.create"] = "hang"
    first = dataclasses.replace(_hook(registry), timeout_ms=3000, order=1)
    second = dataclasses.replace(first, key=f"{PLUGIN_ID}:second", order=2)
    started = time.monotonic()
    out = await _run(_create_op(), _registry_of(first, second))
    assert time.monotonic() - started < 0.6
    assert out.title == "Drucker defekt"
    assert SyncBreaker.failures(hook_key(first)) == 1
    assert SyncBreaker.failures(hook_key(second)) == 1
    assert len(points_behaviour().CALLS) == 1  # the exhausted budget never reached the second


async def test_enrich_only_on_ticket_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    behaviour = points_behaviour()
    behaviour.MODE["hook.ticket.create"] = "enrich"
    op = _create_op()
    out = await _run(op)
    assert out.title == "Angereichert"
    assert (out.queue_id, out.priority, out.dynamic_fields) == (2, "3 normal", op.dynamic_fields)
    assert op.title == "Drucker defekt"  # the input object is never mutated
    assert SyncBreaker.failures(hook_key(_hook(registry))) == 0

    behaviour.MODE["hook.ticket.state_change"] = "enrich"
    state_op = _state_op()
    same = await _run(state_op, operation="ticket.state_change")
    assert same is state_op
    assert SyncBreaker.failures(hook_key(_hook(registry, "ticket.state_change"))) == 1


async def test_patch_with_unknown_priority_is_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    points_behaviour().MODE["hook.ticket.create"] = "bad_priority"
    seen: list[tuple[str, str]] = []

    async def lookup(table: str, name: str) -> bool:
        seen.append((table, name))
        return False

    op = _create_op()
    out = await run_sync_hooks(
        "ticket.create", op, agent_id=1, registry=registry, name_exists=lookup
    )
    assert out is op
    assert seen == [("ticket_priority", "gibt-es-nicht")]
    assert SyncBreaker.failures(hook_key(_hook(registry))) == 1

    async def lookup_ok(table: str, name: str) -> bool:
        return True

    patched = await run_sync_hooks(
        "ticket.create", op, agent_id=1, registry=registry, name_exists=lookup_ok
    )
    assert patched.priority == "gibt-es-nicht" and patched.title == op.title


async def test_trip_after_five_and_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    behaviour = points_behaviour()
    behaviour.MODE["hook.ticket.create"] = "raise"
    hook = _hook(registry)
    key = hook_key(hook)
    op = _create_op()
    for _ in range(5):
        assert await _run(op) is op
    assert len(behaviour.CALLS) == 5 and SyncBreaker.is_tripped(key)

    assert await _run(op) is op  # fail-open: skipped without calling the plugin
    assert len(behaviour.CALLS) == 5

    deny = _registry_of(dataclasses.replace(hook, on_error="deny"))
    with pytest.raises(OperationVetoed, match="angehalten"):
        await _run(op, deny)
    assert len(behaviour.CALLS) == 5

    SyncBreaker.reset(key)
    assert not SyncBreaker.is_tripped(key)
    await _run(op)
    assert len(behaviour.CALLS) == 6


async def test_half_open_probe_closes_breaker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    behaviour = points_behaviour()
    behaviour.MODE["hook.ticket.create"] = "raise"
    key = hook_key(_hook(registry))
    now = [1000.0]
    SyncBreaker.clock = lambda: now[0]
    for _ in range(5):
        await _run(_create_op())
    assert SyncBreaker.is_tripped(key) and len(behaviour.CALLS) == 5

    now[0] += sync_hooks.BREAKER_REFRESH_SECONDS + 1
    await _run(_create_op())  # one probe, still failing: stays tripped
    assert len(behaviour.CALLS) == 6 and SyncBreaker.is_tripped(key)
    await _run(_create_op())  # next probe only after another interval
    assert len(behaviour.CALLS) == 6

    behaviour.MODE["hook.ticket.create"] = "ok"
    now[0] += sync_hooks.BREAKER_REFRESH_SECONDS + 1
    await _run(_create_op())
    assert len(behaviour.CALLS) == 7
    assert not SyncBreaker.is_tripped(key) and SyncBreaker.failures(key) == 0


async def test_inactive_plugin_hooks_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot_points_plugin(tmp_path, monkeypatch)
    points_behaviour().MODE["hook.ticket.create"] = "veto"
    set_loaded_plugins([PluginLoadResult(PLUGIN_ID, "1.0.0", RuntimeState.DISABLED, None, ())])
    op = _create_op()
    assert await _run(op) is op
    assert points_behaviour().CALLS == []


def _registrar(points: list[str] | None = None) -> PluginRegistrar:
    manifest = PluginManifest.model_validate(
        {
            "manifest_version": 1,
            "id": "org.example.hello",
            "short_id": "hello",
            "name": "Test",
            "version": "1.0.0",
            "publisher": "Example GmbH",
            "entry": "x:plugin",
            "sdk": ">=0.2,<0.3",
            "capabilities": {
                "extension_points": ["core.events.sync_hooks"] if points is None else points
            },
        }
    )
    ctx = PluginContext("org.example.hello", "1.0.0", http=PluginHttp([]), tickets=PluginTickets())
    return PluginRegistrar(manifest, ctx)


async def _handler(ctx: Any, op: Any) -> None:
    return None


def _sync_handler(ctx: Any, op: Any) -> None:
    return None


def test_registration_rules() -> None:
    r = _registrar()
    r.before("ticket.create", "check_contract", _handler, timeout_ms=50, order=0)
    r.before("article.create", "check_body", _handler, timeout_ms=3000, order=1000, on_error="deny")
    (create, article) = [i for _p, i in r.staged]
    assert (create.key, create.operation, create.on_error) == (
        "org.example.hello:check_contract",
        "ticket.create",
        "allow",
    )
    assert article.on_error == "deny" and article.context is r.context

    bad: list[tuple[str, Any, dict[str, Any], str]] = [
        ("ticket.delete", _handler, {}, "Unbekannter Vorgang"),
        ("ticket.create", _handler, {"timeout_ms": 0}, "50 und 3000"),
        ("ticket.create", _handler, {"timeout_ms": 4000}, "50 und 3000"),
        ("ticket.create", _handler, {"on_error": "maybe"}, "on_error"),
        ("ticket.create", _sync_handler, {}, "async"),
        ("ticket.create", _handler, {"order": 1001}, "0 und 1000"),
        ("ticket.create", _handler, {"order": -1}, "0 und 1000"),
    ]
    for operation, handler, kwargs, message in bad:
        with pytest.raises(RegistrationRejected, match=message):
            _registrar().before(operation, "other_check", handler, **kwargs)
    with pytest.raises(RegistrationRejected, match="Ungültiger Name"):
        _registrar().before("ticket.create", "Bad Name", _handler)

    with pytest.raises(RegistrationRejected, match="im Manifest nicht angegeben"):
        _registrar([]).before("ticket.create", "check_contract", _handler)
    with pytest.raises(RegistrationRejected, match="bereits registriert"):
        r.before("ticket.create", "check_contract", _handler)
