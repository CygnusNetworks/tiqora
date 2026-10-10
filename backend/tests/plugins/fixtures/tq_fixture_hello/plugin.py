"""Fixture plugin using every phase-1 extension point through the public SDK only."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastmcp import Context
from pydantic import BaseModel, Field, SecretStr

from tiqora_sdk import (
    CurrentAgent,
    EventContext,
    JobContext,
    JobHandler,
    PluginRegistry,
    TaskContext,
    TicketEvent,
)

#: test hooks (module state, reset by tests)
RECEIVED: list[tuple[str, int, str]] = []  # (subscriber, history id, event)
FAIL_ON: set[int] = set()  # history ids the "flaky" subscriber fails on
SLOW_SECONDS: list[float] = [0.0]  # sleep in the flaky subscriber
TASK_RUNS: list[str] = []
TASK_FAIL: list[bool] = [False]
ECHO_CALLS: list[str] = []


class HelloSettings(BaseModel):
    greeting: str = Field("Hallo", title="Begrüßung")
    api_token: SecretStr | None = Field(None, title="Zugangsschlüssel")


class EchoPayload(BaseModel):
    text: str


router = APIRouter()
public_router = APIRouter()


@router.get("/hello")
async def hello(user: CurrentAgent) -> dict[str, str]:
    return {"hello": user.first_name}


@router.post("/echo")
async def echo(body: EchoPayload) -> dict[str, str]:
    ECHO_CALLS.append(body.text)
    return {"text": body.text}


@public_router.get("/ping")
async def ping(request: Request) -> dict[str, Any]:
    return {"pong": True, "app": request.app.state.settings.app_name}


async def on_ticket(ctx: EventContext, event: TicketEvent) -> None:
    RECEIVED.append(("notify", event.id, event.event))


async def flaky(ctx: EventContext, event: TicketEvent) -> None:
    if SLOW_SECONDS[0]:
        await asyncio.sleep(SLOW_SECONDS[0])
    if event.id in FAIL_ON:
        raise RuntimeError(f"kaputt bei {event.id}")
    RECEIVED.append(("flaky", event.id, event.event))


async def tick(ctx: TaskContext) -> dict[str, Any]:
    TASK_RUNS.append(ctx.scheduled_for.isoformat())
    if TASK_FAIL[0]:
        raise RuntimeError("Aufgabe fehlgeschlagen")
    return {"ok": 1}


async def run_echo_job(ctx: JobContext, payload: EchoPayload) -> dict[str, Any]:
    return {"echo": payload.text}


async def echo_tool(ctx: Context, text: str, times: int = 1) -> dict[str, Any]:
    """Echo text back.

    Parameters
    ----------
    text: The text to echo.
    times: How often.
    """
    return {"text": text * times}


class HelloPlugin:
    def register(self, registry: PluginRegistry) -> None:
        registry.settings(HelloSettings)
        registry.route(router)
        registry.route(public_router, public=True)
        registry.on_event("notify", ["TicketCreate", "TicketStateUpdate"], on_ticket)
        registry.on_event("flaky", ["*"], flaky, timeout_seconds=1)
        registry.scheduled_task("tick", tick, every_seconds=60)
        registry.job_handler(
            JobHandler(
                kind="org.example.hello.echo",
                payload_model=EchoPayload,
                run=run_echo_job,
                title_key="plugin.org.example.hello:jobs.echo",
            )
        )
        registry.mcp_tool("echo", echo_tool, description="Gibt den Text zurück.")


plugin = HelloPlugin()
