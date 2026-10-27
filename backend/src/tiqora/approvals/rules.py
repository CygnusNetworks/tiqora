"""Pure voting rules and stage progression.

No database, network or settings access. Imports only the standard library and
``tiqora.approvals.contract``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from tiqora.approvals.contract import (
    ApprovalDefinition,
    ApprovalError,
    ApprovalStatus,
    Rule,
    StepStatus,
)

VoteResult = Literal["pending", "approved", "rejected"]
MAX_STEPS_PER_STAGE = 10


@dataclass(frozen=True, slots=True)
class VoterState:
    status: VoteResult  # only eligible rows (forwarded/removed/not_needed are not passed in)
    decisive: bool = False
    #: counts in the step's rule; None = ``not decisive`` (escalation-only voters stay outside the
    #: rule, a regular approver upgraded by an escalation is ``regular=True`` and decisive)
    regular: bool | None = None

    @property
    def in_rule(self) -> bool:
        return (not self.decisive) if self.regular is None else self.regular


def evaluate_step(rule: Rule, quorum: int | None, voters: Sequence[VoterState]) -> VoteResult:
    """Outcome of one step. Callers pass voters ordered by ``decided_at`` (pending last).

    1. The first decisive voter who has voted decides alone (a pending decisive voter never
       blocks and never rejects).
    2. Otherwise every rule looks at the voters that count in the rule (all non-decisive ones,
       plus regular approvers an escalation made decisive); none of them -> ``pending``
       (no voters at all: the caller applies ``on_no_approver``).
    3. ``any``: the first non-pending voter decides ("the first decision counts"). ``all``: any
       rejection rejects, all approved approves. ``quorum`` n / ``majority`` (n = k // 2 + 1,
       k = number of non-decisive voters): approved >= n approves, approved + pending < n rejects.
    """
    for voter in voters:
        if voter.decisive and voter.status != "pending":
            return voter.status
    regular = [v for v in voters if v.in_rule]
    if not regular:
        return "pending"
    counts = Counter(v.status for v in regular)
    approved, rejected, pending = counts["approved"], counts["rejected"], counts["pending"]
    if rule == "any":
        for voter in regular:
            if voter.status != "pending":
                return voter.status
        return "pending"
    if rule == "all":
        if rejected:
            return "rejected"
        return "approved" if not pending else "pending"
    needed = required_votes(rule, quorum, len(regular))
    if needed is None:
        raise ValueError("rule 'quorum' needs a quorum")
    if approved >= needed:
        return "approved"
    if approved + pending < needed:
        return "rejected"
    return "pending"


def required_votes(rule: Rule, quorum: int | None, voter_count: int) -> int | None:
    """Approvals the rule needs, for the UI ("2 von 3"); None when it cannot be told."""
    if voter_count < 1:
        return None
    if rule == "any":
        return 1
    if rule == "all":
        return voter_count
    if rule == "majority":
        return voter_count // 2 + 1
    return quorum


def quorum_reachable(rule: Rule, quorum: int | None, voter_count: int) -> bool:
    """False when the step can never be approved with this many voters."""
    needed = required_votes(rule, quorum, voter_count)
    return needed is not None and needed <= voter_count


def next_stage(steps: Sequence[tuple[int, StepStatus]]) -> int | None:
    """Lowest stage that still has a waiting step; None = nothing left to activate."""
    waiting = [stage for stage, status in steps if status == "waiting"]
    return min(waiting) if waiting else None


def request_result(steps: Sequence[tuple[int, StepStatus]]) -> ApprovalStatus:
    """Any rejected step rejects the request, all approved approves it, otherwise pending."""
    statuses = [status for _, status in steps]
    if "rejected" in statuses:
        return "rejected"
    if statuses and all(s == "approved" for s in statuses):
        return "approved"
    return "pending"


def validate_definition(defn: ApprovalDefinition) -> None:
    """Semantic checks beyond the models; raises ``ApprovalError("invalid_definition", 422)``."""
    names = Counter(step.name for step in defn.steps)
    duplicates = sorted(name for name, n in names.items() if n > 1)
    if duplicates:
        raise ApprovalError(
            "invalid_definition", 422, f"duplicate step names: {', '.join(duplicates)}"
        )
    per_stage = Counter(step.stage for step in defn.steps)
    for stage, n in sorted(per_stage.items()):
        if n > MAX_STEPS_PER_STAGE:
            raise ApprovalError(
                "invalid_definition",
                422,
                f"stage {stage} has more than {MAX_STEPS_PER_STAGE} steps",
            )
    for step in defn.steps:
        # Groups, roles, lists and resolvers expand later, so only individual entries are countable.
        if (
            step.rule == "quorum"
            and step.quorum is not None
            and all(e.kind in ("user", "customer_user") for e in step.approvers)
            and step.quorum > len({(e.kind, e.ref) for e in step.approvers})
        ):
            raise ApprovalError(
                "invalid_definition",
                422,
                f"step {step.name!r}: quorum {step.quorum} exceeds distinct approvers",
            )
