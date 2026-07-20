"""Ticket statistics / reporting subsystem.

Modern equivalent of Znuny's ``Kernel::System::Stats`` (AgentStatistics): a
fixed set of purpose-built, permission-filtered aggregation queries rather
than Znuny's dynamic report-object framework. See :mod:`tiqora.stats.service`.
"""

from __future__ import annotations

from tiqora.stats.service import (
    AgentWorkloadItem,
    BacklogPoint,
    DimensionCount,
    SlaStats,
    StatsFilters,
    StatsService,
    VolumePoint,
)

__all__ = [
    "AgentWorkloadItem",
    "BacklogPoint",
    "DimensionCount",
    "SlaStats",
    "StatsFilters",
    "StatsService",
    "VolumePoint",
]
