from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter
from fastmcp import Context
from pydantic import BaseModel

from tiqora.channels.registry import CHANNELS
from tiqora.jobs.registry import JOB_HANDLERS, JobHandler
from tiqora.plugins import manifest as manifest_mod
from tiqora.plugins.context import PluginContext, PluginHttp, PluginTickets
from tiqora.plugins.events import EVENT_SUBSCRIBERS
from tiqora.plugins.manifest import PluginManifest, parse_manifest
from tiqora.plugins.mcp import MCP_TOOLS
from tiqora.plugins.registrar import PluginRegistrar, RegistrationRejected
from tiqora.plugins.routes import PLUGIN_ROUTES
from tiqora.plugins.tasks import SCHEDULED_TASKS
from tiqora.settings_sections.section import SETTINGS_SECTIONS


class HelloSettings(BaseModel):
    greeting: str = "Hallo"


class EchoPayload(BaseModel):
    text: str


async def echo_tool(ctx: Context, text: str) -> dict[str, Any]:
    return {"text": text}


async def on_ticket(ctx: Any, event: Any) -> None:
    return None


async def tick(ctx: Any) -> None:
    return None


async def run_echo_job(ctx: Any, payload: EchoPayload) -> None:
    return None


FIX = Path(__file__).parent / "plugins" / "fixtures"
ALL_POINTS = [
    "core.settings_sections",
    "core.http.routes",
    "core.events.subscribers",
    "core.worker.scheduled_tasks",
    "core.job_handlers",
    "core.mcp.tools",
    "core.http.public_routes",
]


def _registrar(
    short_id: str = "hello",
    points: list[str] | None = None,
    plugin_id: str = "org.example.hello",
) -> PluginRegistrar:
    manifest = PluginManifest.model_validate(
        {
            "manifest_version": 1,
            "id": plugin_id,
            "short_id": short_id,
            "name": "Test",
            "version": "1.0.0",
            "publisher": "Example GmbH",
            "entry": "x:plugin",
            "sdk": ">=0.1,<0.2",
            "capabilities": {"extension_points": ALL_POINTS if points is None else points},
        }
    )
    ctx = PluginContext(plugin_id, "1.0.0", http=PluginHttp([]), tickets=PluginTickets())
    return PluginRegistrar(manifest, ctx)


@pytest.fixture
def registrar() -> PluginRegistrar:
    return _registrar()


@pytest.fixture
def undeclared_registrar() -> PluginRegistrar:
    text = (FIX / "tq_fixture_undeclared" / "tiqora-plugin.toml").read_text()
    manifest = parse_manifest(text)
    ctx = PluginContext(manifest.id, "1.0.0", http=PluginHttp([]), tickets=PluginTickets())
    return PluginRegistrar(manifest, ctx)


def _job(kind: str) -> JobHandler:
    return JobHandler(kind=kind, payload_model=EchoPayload, run=run_echo_job, title_key="t")


def test_settings_registers_section_with_plugin_key(registrar: PluginRegistrar) -> None:
    section = registrar.settings(HelloSettings)
    assert section.key == "plugins.org.example.hello"
    assert section.title_key == "plugin.org.example.hello:settings.title"
    assert section.i18n_prefix == "plugin.org.example.hello:settings"
    assert (SETTINGS_SECTIONS, section) in registrar.staged


def test_settings_twice_rejected(registrar: PluginRegistrar) -> None:
    registrar.settings(HelloSettings)
    with pytest.raises(RegistrationRejected, match="Einstellungen"):
        registrar.settings(HelloSettings)


def test_settings_field_without_default_rejected(registrar: PluginRegistrar) -> None:
    class Bad(BaseModel):
        x: int

    with pytest.raises(RegistrationRejected, match="x"):
        registrar.settings(Bad)
    assert registrar.staged == ()


def test_settings_key_too_long_rejected(registrar: PluginRegistrar) -> None:
    Long = type(
        "Long",
        (BaseModel,),
        {"__annotations__": {"f" * 190: str}, "f" * 190: ""},
    )
    with pytest.raises(RegistrationRejected, match="Einstellungsname zu lang"):
        registrar.settings(Long)


@pytest.mark.parametrize(
    "kind",
    ["core.ticket_export", "org.example.other.x", "org.example.hello", "org.example.hello.Bad"],
)
def test_namespace_rules_reject(registrar: PluginRegistrar, kind: str) -> None:
    with pytest.raises(RegistrationRejected, match="Aufgabenart"):
        registrar.job_handler(_job(kind))


def test_job_handler_staged(registrar: PluginRegistrar) -> None:
    handler = _job("org.example.hello.echo")
    registrar.job_handler(handler)
    assert (JOB_HANDLERS, handler) in registrar.staged
    with pytest.raises(RegistrationRejected, match="bereits registriert"):
        registrar.job_handler(handler)


@pytest.mark.parametrize("name", ["Bad", "x", "bad-name", "a" * 42])
def test_bad_names_rejected(registrar: PluginRegistrar, name: str) -> None:
    with pytest.raises(RegistrationRejected, match="Ungültiger Name"):
        registrar.on_event(name, ["TicketCreate"], on_ticket)


def test_undeclared_point_rejected(undeclared_registrar: PluginRegistrar) -> None:
    with pytest.raises(RegistrationRejected, match="core.mcp.tools"):
        undeclared_registrar.mcp_tool("xx", echo_tool, description="xx")


def test_undeclared_point_checked_before_name() -> None:
    reg = _registrar(points=[])
    with pytest.raises(RegistrationRejected, match="im Manifest nicht angegeben"):
        reg.on_event("Bad Name", ["TicketCreate"], on_ticket)


def test_mcp_tool_name_collision_with_core_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manifest_mod, "RESERVED_SHORT_IDS", frozenset())
    reg = _registrar(short_id="list")
    with pytest.raises(RegistrationRejected, match="list_queues"):
        reg.mcp_tool("queues", echo_tool, description="x")


def test_mcp_tool_name_prefixed_with_short_id(registrar: PluginRegistrar) -> None:
    registrar.mcp_tool("echo", echo_tool, description="x")
    ((point, tool),) = registrar.staged
    assert point is MCP_TOOLS
    assert tool.name == "hello_echo"
    assert tool.context is registrar.context


def test_route_keys_numbered(registrar: PluginRegistrar) -> None:
    registrar.route(APIRouter())
    registrar.route(APIRouter(), public=True)
    keys = [item.key for point, item in registrar.staged if point is PLUGIN_ROUTES]
    assert keys == ["org.example.hello:0", "org.example.hello:1"]


def test_public_route_needs_capability() -> None:
    reg = _registrar(points=["core.http.routes"])
    reg.route(APIRouter())
    with pytest.raises(RegistrationRejected, match="core.http.public_routes"):
        reg.route(APIRouter(), public=True)
    assert len(reg.staged) == 1


async def _plain(request: Any) -> Any:
    return None


async def _ws(websocket: Any) -> None:
    return None


def _plain_route_router() -> APIRouter:
    r = APIRouter()
    r.add_route("/raw", _plain)
    return r


def _ws_router() -> APIRouter:
    r = APIRouter()
    r.add_websocket_route("/ws", _ws)
    return r


def _mount_router() -> APIRouter:
    from starlette.routing import Mount

    r = APIRouter()
    r.routes.append(Mount("/sub", app=lambda *a: None))
    return r


@pytest.mark.parametrize("make", [_plain_route_router, _ws_router, _mount_router])
def test_authenticated_router_only_takes_fastapi_routes(make: Any) -> None:
    reg = _registrar(points=["core.http.routes", "core.http.public_routes"])
    with pytest.raises(RegistrationRejected, match="Nur FastAPI-Routen"):
        reg.route(make())
    assert not reg.staged
    reg.route(make(), public=True)  # public routers keep their behaviour
    assert len(reg.staged) == 1


def test_scheduled_task_requires_exactly_one_schedule(registrar: PluginRegistrar) -> None:
    with pytest.raises(RegistrationRejected, match="Zeitplan"):
        registrar.scheduled_task("tt", tick)
    with pytest.raises(RegistrationRejected, match="Zeitplan"):
        registrar.scheduled_task("tt", tick, cron="61 * * * *")
    with pytest.raises(RegistrationRejected, match="Zeitplan"):
        registrar.scheduled_task("tt", tick, every_seconds=10)
    registrar.scheduled_task("tt", tick, every_seconds=60)
    ((point, task),) = registrar.staged
    assert point is SCHEDULED_TASKS
    assert task.key == "org.example.hello:tt"
    assert task.schedule.every_seconds == 60
    assert task.context is registrar.context


@pytest.mark.parametrize("timeout", [0, 301])
def test_event_timeout_bounds(registrar: PluginRegistrar, timeout: int) -> None:
    with pytest.raises(RegistrationRejected, match="Zeitlimit"):
        registrar.on_event("nn", ["TicketCreate"], on_ticket, timeout_seconds=timeout)


def test_bare_string_events_rejected(registrar: PluginRegistrar) -> None:
    with pytest.raises(RegistrationRejected, match="Liste"):
        registrar.on_event("nn", "TicketCreate", on_ticket)  # type: ignore[arg-type]
    assert registrar.staged == ()


def test_empty_events_rejected(registrar: PluginRegistrar) -> None:
    with pytest.raises(RegistrationRejected, match="Ereignis"):
        registrar.on_event("nn", [], on_ticket)


def test_event_subscriber_staged_with_context(registrar: PluginRegistrar) -> None:
    registrar.on_event("nn", ["TicketCreate", "TicketDynamicFieldUpdate_*"], on_ticket)
    ((point, sub),) = registrar.staged
    assert point is EVENT_SUBSCRIBERS
    assert sub.key == "org.example.hello:nn"
    assert sub.context is registrar.context
    assert sub.matches("TicketCreate") and sub.matches("TicketDynamicFieldUpdate_Prio")
    assert not sub.matches("TicketMerge")
    with pytest.raises(RegistrationRejected, match="bereits registriert"):
        registrar.on_event("nn", ["TicketCreate"], on_ticket)


def test_internal_point_refused(registrar: PluginRegistrar) -> None:
    assert CHANNELS.sdk_stable is False
    with pytest.raises(RegistrationRejected, match="core.channels"):
        registrar._stage(CHANNELS, object())


def _unused(_: Any) -> None:  # keep Any import honest for strict mypy settings
    return None


def _sync_handler(ctx: Any, event: Any) -> None:
    return None


class _AsyncCallable:
    async def __call__(self, ctx: Any, event: Any) -> None:
        return None


class _SyncCallable:
    def __call__(self, ctx: Any, event: Any) -> None:
        return None


def test_sync_event_handler_rejected(registrar: PluginRegistrar) -> None:
    with pytest.raises(RegistrationRejected, match="Abonnent „sy“ muss eine async-Funktion"):
        registrar.on_event("sy", ["TicketCreate"], _sync_handler)
    with pytest.raises(RegistrationRejected, match="async-Funktion"):
        registrar.on_event("sc", ["TicketCreate"], _SyncCallable())


def test_async_callables_accepted(registrar: PluginRegistrar) -> None:
    import functools

    registrar.on_event("ac", ["TicketCreate"], _AsyncCallable())
    registrar.on_event("pa", ["TicketCreate"], functools.partial(on_ticket, 1))
    assert len(registrar.staged) == 2


def test_sync_scheduled_task_rejected(registrar: PluginRegistrar) -> None:
    def sync_run(ctx: Any) -> None:
        return None

    with pytest.raises(RegistrationRejected, match="Aufgabe „sy“ muss eine async-Funktion"):
        registrar.scheduled_task("sy", sync_run, every_seconds=60)  # type: ignore[arg-type]


def test_sync_job_handler_rejected(registrar: PluginRegistrar) -> None:
    def sync_job(ctx: Any, payload: EchoPayload) -> None:
        return None

    bad = JobHandler(
        kind="org.example.hello.sy",
        payload_model=EchoPayload,
        run=sync_job,
        title_key="t",  # type: ignore[arg-type]
    )
    with pytest.raises(RegistrationRejected, match="Job-Handler „org.example.hello.sy“ muss"):
        registrar.job_handler(bad)


def test_sync_mcp_tool_rejected(registrar: PluginRegistrar) -> None:
    def sync_tool(ctx: Context, text: str) -> dict[str, str]:
        return {}

    with pytest.raises(RegistrationRejected, match="async-Funktion"):
        registrar.mcp_tool("sy", sync_tool, description="x")  # type: ignore[arg-type]


def test_mcp_tool_with_unsupported_signature_rejected(registrar: PluginRegistrar) -> None:
    import threading

    async def varargs(ctx: Context, *args: Any) -> dict[str, str]:
        return {}

    async def odd_type(ctx: Context, lock: threading.Lock) -> dict[str, str]:
        return {}

    with pytest.raises(RegistrationRejected, match="hello_va.*Signatur"):
        registrar.mcp_tool("va", varargs, description="x")
    with pytest.raises(RegistrationRejected, match="hello_ot.*Signatur"):
        registrar.mcp_tool("ot", odd_type, description="x")
    assert len(registrar.staged) == 0
