"""Every process role registers, follows restart requests and exits with 75 (D10 wiring).

Band 62120-62139 (the database part of the api registration lives in
``test_admin_plugin_restart_api.py``).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from tiqora import main as main_module
from tiqora.plugins import restart
from tiqora.plugins.restart import RESTART_EXIT_CODE


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(restart, "_REQUESTED", False)
    yield
    # run_worker / run_ai_worker close the loop they create; leave a usable one behind
    asyncio.set_event_loop(asyncio.new_event_loop())


class _Calls:
    """Recorder for a stubbed ``start_process_watch``."""

    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.stopped: list[object] = []

    async def start(self, factory: object, role: str, **kw: Any) -> object:
        self.starts.append({"role": role, **kw})
        return f"watch-{role}"

    async def stop(self, watch: object) -> None:
        self.stopped.append(watch)


@pytest.fixture(name="calls")
def calls_fixture(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    calls = _Calls()
    monkeypatch.setattr(restart, "start_process_watch", calls.start)
    monkeypatch.setattr(restart, "stop_process_watch", calls.stop)
    return calls


# ---------------------------------------------------------------- api


def test_api_main_exits_75_after_requested_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(restart, "_REQUESTED", True)
    with pytest.raises(SystemExit) as e:
        main_module.main(["api"])
    assert ran and e.value.code == RESTART_EXIT_CODE


def test_api_main_returns_normally_without_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: ran.append(a))
    main_module.main(["api"])  # no SystemExit
    assert len(ran) == 1


async def test_api_registration_helper_uses_api_role_and_sigterm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tiqora.api import app as app_module

    seen: dict[str, Any] = {}

    async def fake_start(factory: object, role: str, **kw: Any) -> str:
        seen.update(factory=factory, role=role, **kw)
        return "handle"

    monkeypatch.setattr(app_module, "start_process_watch", fake_start)
    settings = object()
    state = SimpleNamespace(session_factory="factory", settings=settings)
    got = await app_module._start_process_registration(SimpleNamespace(state=state))  # type: ignore[arg-type]
    assert got == "handle"
    assert seen["role"] == "api" and seen["factory"] == "factory"
    assert seen["settings"] is settings and seen["on_exit"] is restart.sigterm_self


async def test_api_registration_helper_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.api import app as app_module

    async def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("must not start")

    monkeypatch.setattr(app_module, "start_process_watch", boom)
    state = SimpleNamespace(session_factory="f", settings=object(), start_restart_watch=False)
    assert await app_module._start_process_registration(SimpleNamespace(state=state)) is None  # type: ignore[arg-type]


def test_api_lifespan_registers_after_registry_and_stops_in_finally() -> None:
    from tiqora.api import app as app_module

    src = inspect.getsource(app_module.lifespan)
    assert src.index("init_registry(") < src.index("_start_process_registration(app)")
    assert src.index("_start_process_registration(app)") < src.index("yield")
    # the row and the loop are released on shutdown, also after an error
    assert src.index("finally:") < src.index("stop_process_watch(process_watch)")
    assert src.count("_start_process_registration(app)") == 1  # no second registration


def test_sigterm_self_signals_own_process(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import signal

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(restart.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    restart.sigterm_self()
    assert sent == [(os.getpid(), signal.SIGTERM)]


# ---------------------------------------------------------------- worker


def _stub_worker(monkeypatch: pytest.MonkeyPatch, *, set_flag: bool) -> list[str]:
    from tiqora.plugins import bootstrap
    from tiqora.worker import __main__ as worker

    order: list[str] = []

    async def init_registry(**kw: Any) -> None:
        order.append(f"init:{kw['role']}")

    async def retry(**kw: Any) -> None:
        return None

    async def schema() -> None:
        return None

    async def loops(stop: asyncio.Event) -> None:
        order.append("loops")
        if set_flag:
            monkeypatch.setattr(restart, "_REQUESTED", True)

    monkeypatch.setattr(bootstrap, "init_registry", init_registry)
    monkeypatch.setattr(bootstrap, "retry_plugin_boot", retry)
    monkeypatch.setattr(worker, "get_session_factory", lambda: "factory")
    monkeypatch.setattr(worker, "_ensure_legacy_schema_at_worker_start", schema)
    monkeypatch.setattr(worker, "_run_all_loops", loops)
    return order


def test_worker_exit_code_after_restart(monkeypatch: pytest.MonkeyPatch, calls: _Calls) -> None:
    from tiqora.worker.__main__ import run_worker

    order = _stub_worker(monkeypatch, set_flag=True)
    with pytest.raises(SystemExit) as e:
        run_worker()
    assert e.value.code == RESTART_EXIT_CODE
    assert order == ["init:worker", "loops"]
    (start,) = calls.starts
    assert start["role"] == "worker"
    # the watch loop's on_exit stops the daemon loops through the shared stop event
    assert not start["stop"].is_set()
    start["on_exit"]()
    assert start["stop"].is_set()
    assert calls.stopped == ["watch-worker"]


def test_worker_exits_normally_without_restart(
    monkeypatch: pytest.MonkeyPatch, calls: _Calls
) -> None:
    from tiqora.worker.__main__ import run_worker

    _stub_worker(monkeypatch, set_flag=False)
    run_worker()  # no SystemExit
    assert [s["role"] for s in calls.starts] == ["worker"]
    assert calls.stopped == ["watch-worker"]


# ---------------------------------------------------------------- ai-worker


def _stub_ai(monkeypatch: pytest.MonkeyPatch, order: list[str]) -> None:
    from tiqora.ai import worker as ai

    async def init_registry(**kw: Any) -> None:
        order.append(f"init:{kw['role']}")

    async def retry(**kw: Any) -> None:
        return None

    async def idle(*a: Any) -> None:
        order.append("loop")

    monkeypatch.setattr(ai, "init_registry", init_registry)
    monkeypatch.setattr(ai, "retry_plugin_boot", retry)
    monkeypatch.setattr(ai, "get_session_factory", lambda: "factory")
    monkeypatch.setattr(ai, "_heartbeat_loop", idle)
    monkeypatch.setattr(ai, "_run_loop", idle)
    monkeypatch.setattr(ai, "run_chat_bot_loop", idle)


async def test_ai_worker_registers_once_after_init_registry(
    monkeypatch: pytest.MonkeyPatch, calls: _Calls
) -> None:
    from tiqora.ai import worker as ai

    order: list[str] = []
    _stub_ai(monkeypatch, order)
    real_start = calls.start

    async def start(factory: object, role: str, **kw: Any) -> object:
        order.append(f"register:{role}")
        return await real_start(factory, role, **kw)

    monkeypatch.setattr(restart, "start_process_watch", start)
    # the module imported the names at import time: bind the recording stubs there too
    monkeypatch.setattr(ai, "start_process_watch", start)
    monkeypatch.setattr(ai, "stop_process_watch", calls.stop)
    stop = asyncio.Event()
    await ai._run_all(stop)
    assert order[:2] == ["init:ai-worker", "register:ai-worker"]
    assert order.count("init:ai-worker") == 1  # no second activation
    (s,) = calls.starts
    assert s["role"] == "ai-worker" and s["stop"] is stop
    s["on_exit"]()
    assert stop.is_set()
    assert calls.stopped == ["watch-ai-worker"]


def test_ai_worker_exit_code_after_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.ai import worker as ai

    async def run_all(stop: asyncio.Event) -> None:
        monkeypatch.setattr(restart, "_REQUESTED", True)

    monkeypatch.setattr(ai, "_run_all", run_all)
    monkeypatch.setattr(ai, "restart_requested", restart.restart_requested)
    with pytest.raises(SystemExit) as e:
        ai.run_ai_worker()
    assert e.value.code == RESTART_EXIT_CODE


def test_ai_worker_source_registers_after_init_registry() -> None:
    from tiqora.ai import worker as ai

    src = inspect.getsource(ai._run_all)
    assert src.index("init_registry(") < src.index(
        'start_process_watch(\n        factory, "ai-worker"'
    )


# ---------------------------------------------------------------- mcp


def test_mcp_exit_code_after_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.mcp_server import server

    ran: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(restart, "_REQUESTED", True)
    with pytest.raises(SystemExit) as e:
        server.run_mcp_server("127.0.0.1", 8001)
    assert ran and e.value.code == RESTART_EXIT_CODE


def test_mcp_exits_normally_without_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.mcp_server import server

    ran: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: ran.append(a))
    server.run_mcp_server("127.0.0.1", 8001)
    assert len(ran) == 1


def test_mcp_lifespan_registers_role_mcp_with_sigterm() -> None:
    from tiqora.mcp_server import server

    src = inspect.getsource(server._app_lifespan)
    assert src.index("init_registry(") < src.index("start_process_watch(")
    assert '"mcp"' in src and "on_exit=sigterm_self" in src
    assert src.index("finally:") < src.index("stop_process_watch(process_watch)")


# ---------------------------------------------------------------- helpers


async def test_start_process_watch_still_starts_loop_after_register_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I1: a failed first registration must not leave the process out of restarts."""

    async def broken(*a: Any, **k: Any) -> str:
        raise RuntimeError("db down")

    seen: dict[str, Any] = {}

    async def fake_loop(factory: object, role: str, instance_id: str, **kw: Any) -> None:
        seen.update(role=role, instance_id=instance_id)

    monkeypatch.setattr(restart, "register_process", broken)
    monkeypatch.setattr(restart, "restart_watch_loop", fake_loop)
    got = await restart.start_process_watch(
        "factory",  # type: ignore[arg-type]
        "worker",
        settings=object(),  # type: ignore[arg-type]
        on_exit=lambda: None,
    )
    assert got is not None
    await got.task
    assert seen["role"] == "worker" and seen["instance_id"] == got.instance_id
    assert got.instance_id.startswith("worker:")
    await restart.stop_process_watch(None)  # no-op


def test_loaded_packages_from_sys_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    root = tmp_path / "plugins"
    good = root / "acme.demo" / "1.0.0-abcdef123456" / "site"
    other_root = tmp_path / "elsewhere" / "acme.x" / "1.0-aaaaaaaaaaaa" / "site"
    monkeypatch.setattr(
        "sys.path",
        ["/usr/lib/python", str(good), str(other_root), str(root / "acme.demo" / "noshaversion")],
    )
    assert restart.loaded_packages_from_sys_path(str(root)) == ["acme.demo@abcdef123456"]
    assert restart.loaded_packages_from_sys_path(None) == []


async def test_stop_process_watch_cancels_loop_and_unregisters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unregistered: list[str] = []

    async def unregister(factory: object, instance_id: str) -> None:
        unregistered.append(instance_id)

    async def never() -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(restart, "unregister_process", unregister)
    task = asyncio.create_task(never())
    watch = restart.ProcessWatch("worker:h:1:x", task, "factory")  # type: ignore[arg-type]
    await restart.stop_process_watch(watch)
    assert task.cancelled() and unregistered == ["worker:h:1:x"]
