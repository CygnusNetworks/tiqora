"""Fixture plugin whose register() stages an item and then raises."""

from __future__ import annotations

from pydantic import BaseModel

from tiqora_sdk import JobContext, JobHandler, PluginRegistry


class Payload(BaseModel):
    pass


async def run(ctx: JobContext, payload: Payload) -> None:
    return None


class BrokenPlugin:
    def register(self, registry: PluginRegistry) -> None:
        registry.job_handler(
            JobHandler(kind="org.example.broken.one", payload_model=Payload, run=run, title_key="t")
        )
        raise RuntimeError("register kaputt")


plugin = BrokenPlugin()
