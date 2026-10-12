"""Names for process transition actions and condition modules (``registry.transition_action``).

An action receives an :class:`ActionContext`; pass ``ctx.session`` only to
``ctx.plugin.tickets.*(..., session=ctx.session)``. A condition module is a plain, fast ``def``.
"""

from __future__ import annotations

from tiqora.plugins.process_points import ActionContext, ConditionRun, TransitionActionRun
from tiqora.process.actions.registry import ActionParam

__all__ = ["ActionContext", "ActionParam", "ConditionRun", "TransitionActionRun"]
