from __future__ import annotations

import dataclasses
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
    "core.auth.password_providers",
    "core.customers.sources",
    "core.gi.operations",
    "core.gi.mappings",
    "core.gi.invokers",
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
            "sdk": ">=0.2,<0.3",
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
    (staged,) = [h for p, h in registrar.staged if p is JOB_HANDLERS]
    # staged with its run shielded against SystemExit & co. (B-I2), everything else unchanged
    assert staged.run.__wrapped__ is handler.run  # type: ignore[attr-defined]
    assert dataclasses.replace(staged, run=handler.run) == handler
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


async def _filter(ctx: Any, mail: Any) -> None:
    return None


def test_mail_filter_stage_picks_the_point() -> None:
    from tiqora.plugins.mail_filters import INBOUND_MAIL_FILTERS, OUTBOUND_MAIL_FILTERS

    reg = _registrar(points=["core.mail.inbound_filters", "core.mail.outbound_filters"])
    reg.mail_filter("vorher", "pre", _filter)
    reg.mail_filter("nachher", "post", _filter)
    reg.mail_filter("raus", "outbound", _filter)
    points = [(p.key, p.item_key(i)) for p, i in reg.staged]
    assert points == [
        (INBOUND_MAIL_FILTERS.key, "org.example.hello:vorher"),
        (INBOUND_MAIL_FILTERS.key, "org.example.hello:nachher"),
        (OUTBOUND_MAIL_FILTERS.key, "org.example.hello:raus"),
    ]


def test_mail_filter_needs_the_capability_of_its_stage() -> None:
    reg = _registrar(points=["core.mail.inbound_filters"])
    with pytest.raises(RegistrationRejected, match="core.mail.outbound_filters"):
        reg.mail_filter("raus", "outbound", _filter)
    reg = _registrar(points=["core.mail.outbound_filters"])
    with pytest.raises(RegistrationRejected, match="core.mail.inbound_filters"):
        reg.mail_filter("vorher", "pre", _filter)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"stage": "mid"}, "Unbekannte Stufe"),
        ({"name": "X"}, "Ungültiger Name"),
        ({"run": lambda ctx, mail: None}, "async"),
        ({"timeout_seconds": 31}, "1 und 30"),
        ({"timeout_seconds": 0}, "1 und 30"),
        ({"order": 1001}, "0 und 1000"),
    ],
)
def test_mail_filter_rejects_bad_arguments(kwargs: dict[str, Any], message: str) -> None:
    reg = _registrar(points=["core.mail.inbound_filters", "core.mail.outbound_filters"])
    args: dict[str, Any] = {"name": "pruefer", "stage": "pre", "run": _filter}
    args.update(kwargs)
    with pytest.raises(RegistrationRejected, match=message):
        reg.mail_filter(args.pop("name"), args.pop("stage"), args.pop("run"), **args)
    assert reg.staged == ()


async def _auth(ctx: Any, check: Any) -> str | None:
    return None


def _auth_sync(ctx: Any, check: Any) -> str | None:
    return None


def test_password_provider_audience_is_required(registrar: PluginRegistrar) -> None:
    with pytest.raises(TypeError):
        registrar.password_provider("pp", _auth)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("name", "fn", "kwargs", "match"),
    [
        ("Bad Name", _auth, {}, "Name"),
        ("pp", _auth_sync, {}, "async"),
        ("pp", _auth, {"audience": "admin"}, "Zielgruppe"),
        ("pp", _auth, {"timeout_seconds": 0}, "Zeitlimit"),
        ("pp", _auth, {"timeout_seconds": 16}, "Zeitlimit"),
        ("pp", _auth, {"timeout_seconds": True}, "Zeitlimit"),
        ("pp", _auth, {"order": -1}, "Reihenfolge"),
        ("pp", _auth, {"order": 1001}, "Reihenfolge"),
    ],
)
def test_password_provider_rejects(
    registrar: PluginRegistrar, name: str, fn: Any, kwargs: dict[str, Any], match: str
) -> None:
    args: dict[str, Any] = {"audience": "both", **kwargs}
    with pytest.raises(RegistrationRejected, match=match):
        registrar.password_provider(name, fn, **args)
    assert registrar.staged == ()


def test_password_provider_staged(registrar: PluginRegistrar) -> None:
    registrar.password_provider("pp", _auth, audience="agent")
    ((_, item),) = registrar.staged
    assert item.key == "org.example.hello:pp" and item.audience == "agent"


async def _cs_search(ctx: Any, q: str, limit: int) -> list[Any]:
    return []


async def _cs_lookup(ctx: Any, key: str) -> None:
    return None


def _cs_sync(ctx: Any, key: str) -> None:
    return None


@pytest.mark.parametrize(
    ("name", "kwargs", "match"),
    [
        ("Bad Name", {}, "Name"),
        ("cs", {"label": " "}, "Bezeichnung"),
        ("cs", {"search": _cs_sync}, "async"),
        ("cs", {"by_login": _cs_sync}, "async"),
        ("cs", {"by_email": _cs_sync}, "async"),
        ("cs", {"materialize_on_inbound": True}, "by_email"),
        ("cs", {"timeout_seconds": 0}, "Zeitlimit"),
        ("cs", {"timeout_seconds": 11}, "Zeitlimit"),
        ("cs", {"timeout_seconds": True}, "Zeitlimit"),
    ],
)
def test_customer_source_rejects(
    registrar: PluginRegistrar, name: str, kwargs: dict[str, Any], match: str
) -> None:
    args: dict[str, Any] = {
        "label": "Verzeichnis",
        "search": _cs_search,
        "by_login": _cs_lookup,
        **kwargs,
    }
    with pytest.raises(RegistrationRejected, match=match):
        registrar.customer_source(name, **args)
    assert registrar.staged == ()


def test_customer_source_staged(registrar: PluginRegistrar) -> None:
    registrar.customer_source(
        "cs",
        label="Verzeichnis",
        search=_cs_search,
        by_login=_cs_lookup,
        by_email=_cs_lookup,
        materialize_on_inbound=True,
    )
    ((point, item),) = registrar.staged
    assert point.key == "core.customers.sources"
    assert item.key == "org.example.hello:cs" and item.timeout_seconds == 3
    assert item.materialize_on_inbound is True and item.by_email is _cs_lookup


def test_gi_operation_registers_qualified_type(registrar: PluginRegistrar) -> None:
    async def run(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
        return {"Success": 1}

    assert registrar.gi_operation("Echo", run, timeout_seconds=45) == "Plugin_hello::Echo"
    ((point, item),) = registrar.staged
    assert point.key == "core.gi.operations"
    assert (item.type, item.timeout_seconds, item.plugin_id) == (
        "Plugin_hello::Echo",
        45,
        "org.example.hello",
    )


@pytest.mark.parametrize("timeout", [0, 46, True, 1.5])
def test_gi_operation_timeout_bounds(registrar: PluginRegistrar, timeout: Any) -> None:
    async def run(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
        return {}

    with pytest.raises(RegistrationRejected, match="Zeitlimit"):
        registrar.gi_operation("Echo", run, timeout_seconds=timeout)
    assert registrar.staged == ()


def test_gi_operation_must_be_async(registrar: PluginRegistrar) -> None:
    def run(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
        return {}

    with pytest.raises(RegistrationRejected, match="async"):
        registrar.gi_operation("Echo", run)  # type: ignore[arg-type]


def test_gi_mapping_registers_and_rejects_async(registrar: PluginRegistrar) -> None:
    def factory(config: dict[str, Any] | None) -> Any:
        return object()

    async def bad(config: dict[str, Any] | None) -> Any:
        return object()

    assert registrar.gi_mapping("Upper", factory) == "Plugin_hello_Upper"
    with pytest.raises(RegistrationRejected, match="gewöhnliche Funktion"):
        registrar.gi_mapping("Other", bad)  # type: ignore[arg-type]
    with pytest.raises(RegistrationRejected, match="bereits registriert"):
        registrar.gi_mapping("Upper", factory)


def test_gi_points_need_manifest_declaration() -> None:
    async def run(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
        return {}

    with pytest.raises(RegistrationRejected, match="core.gi.operations"):
        _registrar(points=[]).gi_operation("Echo", run)
    with pytest.raises(RegistrationRejected, match="core.gi.mappings"):
        _registrar(points=[]).gi_mapping("Echo", lambda config: object())  # type: ignore[arg-type,return-value]


def test_gi_invoker_registers_qualified_type(registrar: PluginRegistrar) -> None:
    async def prepare(ctx: Any, data: dict[str, Any]) -> dict[str, Any] | None:
        return data

    async def handle(ctx: Any, response: Any) -> None:
        return None

    assert (
        registrar.gi_invoker("Sync", prepare, handle, timeout_seconds=120) == "Plugin_hello::Sync"
    )
    ((point, item),) = registrar.staged
    assert point.key == "core.gi.invokers"
    assert (item.type, item.timeout_seconds, item.handle_response) == (
        "Plugin_hello::Sync",
        120,
        handle,
    )


@pytest.mark.parametrize("timeout", [0, 121, True, 1.5])
def test_gi_invoker_timeout_bounds(registrar: PluginRegistrar, timeout: Any) -> None:
    async def prepare(ctx: Any, data: dict[str, Any]) -> dict[str, Any] | None:
        return data

    with pytest.raises(RegistrationRejected, match="Zeitlimit"):
        registrar.gi_invoker("Sync", prepare, timeout_seconds=timeout)
    assert registrar.staged == ()


def test_gi_invoker_must_be_async_and_named(registrar: PluginRegistrar) -> None:
    async def prepare(ctx: Any, data: dict[str, Any]) -> dict[str, Any] | None:
        return data

    def sync_fn(*args: Any) -> Any:
        return None

    with pytest.raises(RegistrationRejected, match="async"):
        registrar.gi_invoker("Sync", sync_fn)  # type: ignore[arg-type]
    with pytest.raises(RegistrationRejected, match="async"):
        registrar.gi_invoker("Sync", prepare, sync_fn)  # type: ignore[arg-type]
    with pytest.raises(RegistrationRejected, match="Ungültiger Name"):
        registrar.gi_invoker("Ticket::Generic", prepare)
    assert registrar.staged == ()
    with pytest.raises(RegistrationRejected, match="core.gi.invokers"):
        _registrar(points=[]).gi_invoker("Sync", prepare)
