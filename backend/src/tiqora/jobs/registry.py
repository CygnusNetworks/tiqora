"""Job handler extension point.

A handler declares, besides ``kind`` / ``payload_model`` / ``run``:

* ``lane`` - the concurrency lane its jobs run in. The runner fills every lane on its own,
  each in job id order: ``"default"`` has ``jobs.max_concurrency`` slots (admin setting),
  every other lane has the slots in :data:`LANE_SLOTS` (1 when not listed). A job in one
  lane never waits for a slot of another, so a lane with one slot runs its jobs strictly
  one after the other, in id order (e.g. ``"mail"``: outgoing mails, like Znuny's
  ``MailQueue`` daemon task). Jobs of kinds without a handler run in ``"default"``.
* ``requeue_if_interrupted`` - a job interrupted by a worker restart or crash (shutdown
  grace exceeded, or reaped after 180 s without heartbeat) is put back into the queue
  instead of failing, as long as its handler had not passed
  :meth:`~tiqora.jobs.context.JobContext.checkpoint` yet (nothing irreversible happened).
  A job interrupted after its checkpoint fails, and so does one that already had
  ``max_attempts`` claims (a job that kills its worker is not requeued forever). Default
  ``False``: every interrupted job fails ("interrupted"), as before.
* ``max_attempts`` - the most claims a job of this kind gets (first run + retries +
  requeues after an interruption). Default 1.
* ``on_interrupted`` - called (with a fresh :class:`~tiqora.jobs.context.JobContext` and
  the validated payload) after an interrupted or timed-out job was marked failed, so the
  handler can record the failure in its own domain (e.g. a mail's transmission error).
  Errors in the hook are logged and ignored.

* ``result_private`` - the job result holds data selected with the creator's permissions
  (e.g. a report computed in the creator's queue scope): ``GET /jobs`` and ``GET /jobs/{id}``
  show it to the creator only; anyone else (admins included) gets status and progress
  without ``result``. Job files are creator-only for every kind anyway.

Retries: a handler raises :class:`~tiqora.jobs.context.JobRetry` to put its job back into
the queue with a delay (``due_at``); ``ctx.attempt`` tells the handler which claim this is,
so it decides itself when to stop retrying (and records the final failure); a
``JobRetry`` on the last allowed attempt fails the job with the retry's message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from tiqora.extensions.registry import ExtensionPoint

if TYPE_CHECKING:
    from tiqora.jobs.context import JobContext

JobRun = Callable[["JobContext", Any], Awaitable[dict[str, Any] | None]]
InterruptHook = Callable[["JobContext", Any], Awaitable[None]]

DEFAULT_LANE = "default"
MAIL_LANE = "mail"
# Slots per lane other than "default" (which uses jobs.max_concurrency); unlisted lanes get 1.
# "gi": GenericInterface invokers (2 slots); "gi_sync": invokers with Asynchronous: 0, one slot
# so they run strictly in id order (the GI job handlers in tiqora.gi.jobs).
LANE_SLOTS: Mapping[str, int] = MappingProxyType(
    {MAIL_LANE: 1, "gi": 2, "gi_sync": 1, "itsm_import": 1}
)


def lane_slots(lane: str, default_slots: int) -> int:
    """Concurrent jobs allowed in ``lane``."""
    if lane == DEFAULT_LANE:
        return default_slots
    return LANE_SLOTS.get(lane, 1)


@dataclass(frozen=True, slots=True)
class JobHandler:
    kind: str  # "<owner>.<name>", e.g. "core.ticket_export"
    payload_model: type[BaseModel]
    run: JobRun  # receives the validated payload model instance
    title_key: str  # frontend i18n key, e.g. "jobs.kinds.core.ticket_export"
    timeout_seconds: int = 3600
    lane: str = DEFAULT_LANE
    max_attempts: int = 1
    requeue_if_interrupted: bool = False
    on_interrupted: InterruptHook | None = None
    result_private: bool = False


JOB_HANDLERS: ExtensionPoint[JobHandler] = ExtensionPoint(
    key="core.job_handlers",
    item_key=lambda h: h.kind,
    description="Background job handlers, run by the worker's job runner.",
    sdk_stable=True,
)
