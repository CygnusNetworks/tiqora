"""Job handler extension point."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from tiqora.extensions.registry import ExtensionPoint

if TYPE_CHECKING:
    from tiqora.jobs.context import JobContext

JobRun = Callable[["JobContext", Any], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class JobHandler:
    kind: str  # "<owner>.<name>", e.g. "core.ticket_export"
    payload_model: type[BaseModel]
    run: JobRun  # receives the validated payload model instance
    title_key: str  # frontend i18n key, e.g. "jobs.kinds.core.ticket_export"
    timeout_seconds: int = 3600


JOB_HANDLERS: ExtensionPoint[JobHandler] = ExtensionPoint(
    key="core.job_handlers",
    item_key=lambda h: h.kind,
    description="Background job handlers, run by the worker's job runner.",
)
