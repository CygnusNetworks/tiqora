"""Pydantic response models for the ``/api/v1/stats/*`` REST endpoints.

Thin wrappers around the :mod:`tiqora.stats.service` dataclasses — kept in
the ``stats`` subsystem (not ``domain/schemas.py``) so this feature touches
no shared files beyond router registration.
"""

from __future__ import annotations

from pydantic import BaseModel

from tiqora.stats.service import (
    AgentWorkloadItem,
    BacklogPoint,
    DimensionCount,
    SlaStats,
    VolumePoint,
)


class VolumePointOut(BaseModel):
    bucket: str
    created: int
    closed: int

    @classmethod
    def from_dataclass(cls, p: VolumePoint) -> VolumePointOut:
        return cls(bucket=p.bucket, created=p.created, closed=p.closed)


class TicketVolumeOut(BaseModel):
    granularity: str
    points: list[VolumePointOut]


class DimensionCountOut(BaseModel):
    id: int | None
    label: str
    count: int

    @classmethod
    def from_dataclass(cls, d: DimensionCount) -> DimensionCountOut:
        return cls(id=d.id, label=d.label, count=d.count)


class OpenSnapshotOut(BaseModel):
    dimension: str
    items: list[DimensionCountOut]


class SlaStatsOut(BaseModel):
    total: int
    escalated: int
    first_response_breached: int
    update_breached: int
    solution_breached: int
    first_response_minutes: list[float]
    solution_minutes: list[float]

    @classmethod
    def from_dataclass(cls, s: SlaStats) -> SlaStatsOut:
        return cls(
            total=s.total,
            escalated=s.escalated,
            first_response_breached=s.first_response_breached,
            update_breached=s.update_breached,
            solution_breached=s.solution_breached,
            first_response_minutes=s.first_response_minutes,
            solution_minutes=s.solution_minutes,
        )


class AgentWorkloadItemOut(BaseModel):
    user_id: int
    login: str
    name: str
    owned_open: int
    closed_in_period: int

    @classmethod
    def from_dataclass(cls, a: AgentWorkloadItem) -> AgentWorkloadItemOut:
        return cls(
            user_id=a.user_id,
            login=a.login,
            name=a.name,
            owned_open=a.owned_open,
            closed_in_period=a.closed_in_period,
        )


class BacklogPointOut(BaseModel):
    bucket: str
    open_count: int

    @classmethod
    def from_dataclass(cls, b: BacklogPoint) -> BacklogPointOut:
        return cls(bucket=b.bucket, open_count=b.open_count)


class BacklogTrendOut(BaseModel):
    granularity: str
    points: list[BacklogPointOut]
