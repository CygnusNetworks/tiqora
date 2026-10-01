"""Queue tree ordering (sidebar / pickers)."""

from __future__ import annotations

from tiqora.domain.queue_service import sort_queue_tree
from tiqora.domain.schemas import QueueNode


def _node(qid: int, name: str, children: list[QueueNode] | None = None) -> QueueNode:
    return QueueNode(id=qid, name=name, group_id=1, children=children or [])


def test_sorts_case_insensitively_at_every_level() -> None:
    tree = sort_queue_tree(
        [
            _node(3, "Junk"),
            _node(1, "Postmaster"),
            _node(10, "support", [_node(12, "support::zweite"), _node(11, "support::Erste")]),
            _node(2, "Raw"),
            _node(5, "abuse"),
        ]
    )
    assert [n.name for n in tree] == ["abuse", "Junk", "Postmaster", "Raw", "support"]
    assert [n.name for n in tree[-1].children] == ["support::Erste", "support::zweite"]
