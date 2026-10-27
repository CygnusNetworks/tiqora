"""Change templates (design I8).

Only the pure task part exists so far: :class:`TemplateTask` (what a template stores per task)
and :func:`expand_tasks`, which the ``core.itsm_change_tasks`` job uses to turn a template into
task inputs. Template CRUD, defaults and standard templates follow with the templates task.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from tiqora.itsm.change.tasks import TaskIn


class TemplateTask(BaseModel):
    key: str = Field(..., pattern=r"^[a-z0-9_]{1,50}$")
    title: str = Field(..., min_length=1, max_length=255)
    task_kind: Literal["workorder", "backout", "decision", "pir"] = "workorder"
    queue_id: int | None = None
    owner_id: int | None = None
    #: relative to the change's planned start
    offset_minutes: int = Field(0, ge=-43200, le=525600)
    duration_minutes: int | None = Field(None, ge=1, le=525600)
    body: str = Field("", max_length=20000)
    position: int = 0


def ordered_tasks(tasks: Sequence[TemplateTask]) -> list[TemplateTask]:
    """Template order: by ``position``, ties in list order."""
    return [t for _, t in sorted(enumerate(tasks), key=lambda item: (item[1].position, item[0]))]


def expand_tasks(tasks: Sequence[TemplateTask], planned_start: datetime | None) -> list[TaskIn]:
    """One :class:`TaskIn` per template task, in template order, ``position`` = its index.

    Times are only set when the change has a planned start (naive UTC): start = change start +
    offset, end = start + duration.
    """
    out: list[TaskIn] = []
    for index, task in enumerate(ordered_tasks(tasks)):
        start = (
            None
            if planned_start is None
            else planned_start + timedelta(minutes=task.offset_minutes)
        )
        end = None
        if start is not None and task.duration_minutes is not None:
            end = start + timedelta(minutes=task.duration_minutes)
        out.append(
            TaskIn(
                title=task.title,
                task_kind=task.task_kind,
                owner_id=task.owner_id,
                queue_id=task.queue_id,
                planned_start=start,
                planned_end=end,
                planned_effort_minutes=task.duration_minutes,
                body=task.body,
                position=index,
            )
        )
    return out
