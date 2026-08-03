"""Unit tests for Meilisearch document building (no Meilisearch required)."""

from __future__ import annotations

from datetime import UTC, datetime

from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.schemas import SimilarTicketItem
from tiqora.domain.search import (
    build_similar_query,
    build_ticket_document,
    rank_similar_keyword,
)


def test_build_ticket_document_shape() -> None:
    t = Ticket(
        id=42,
        tn="20240719000001",
        title="Printer jammed",
        queue_id=3,
        ticket_lock_id=1,
        type_id=1,
        service_id=None,
        sla_id=None,
        user_id=5,
        responsible_user_id=1,
        ticket_priority_id=3,
        ticket_state_id=4,
        customer_id="ACME",
        customer_user_id="bob@acme.example",
        timeout=0,
        until_time=0,
        escalation_time=100,
        escalation_update_time=0,
        escalation_response_time=50,
        escalation_solution_time=0,
        archive_flag=0,
        create_time=datetime(2024, 7, 19, 10, 0, 0),
        create_by=1,
        change_time=datetime(2024, 7, 19, 11, 0, 0),
        change_by=1,
    )
    doc = build_ticket_document(
        t,
        queue_name="Support",
        state_name="open",
        state_type="open",
        priority_name="3 normal",
        owner_login="agent1",
        owner_name="A Gent",
        latest_excerpt="The fuser is hot.",
        dynamic_fields={"Process": "HW"},
    )
    assert doc["id"] == 42
    assert doc["tn"] == "20240719000001"
    assert doc["queue_id"] == 3
    assert doc["queue_name"] == "Support"
    assert doc["state_type"] == "open"
    assert doc["owner_id"] == 5
    assert doc["customer_id"] == "ACME"
    assert doc["has_escalation"] is True
    assert doc["latest_article_excerpt"] == "The fuser is hot."
    assert doc["dynamic_fields"]["Process"] == "HW"
    assert doc["created"] is not None
    assert doc["changed"] is not None
    assert doc["created_ts"] == int(datetime(2024, 7, 19, 10, 0, 0, tzinfo=UTC).timestamp())
    assert doc["changed_ts"] == int(datetime(2024, 7, 19, 11, 0, 0, tzinfo=UTC).timestamp())


def test_build_similar_query_joins_and_trims() -> None:
    assert build_similar_query("  Hello  ", "  world  ") == "Hello world"
    assert build_similar_query(None, None) == ""
    assert build_similar_query("", "  ") == ""
    long = "x" * 600
    assert len(build_similar_query(long, None)) == 500


def test_rank_similar_keyword_excludes_source_and_caps() -> None:
    cands = [
        SimilarTicketItem(id=1, tn="A", title="a", score=0.9),
        SimilarTicketItem(id=2, tn="B", title="b", score=0.8),
        SimilarTicketItem(id=3, tn="C", title="c", score=0.7),
        SimilarTicketItem(id=4, tn="D", title="d", score=0.6),
        SimilarTicketItem(id=5, tn="E", title="e", score=0.5),
        SimilarTicketItem(id=6, tn="F", title="f", score=0.4),
        SimilarTicketItem(id=7, tn="G", title="g", score=0.3),
    ]
    ranked = rank_similar_keyword(cands, exclude_id=1, limit=5)
    assert [r.id for r in ranked] == [2, 3, 4, 5, 6]
    # Source not present even if it would fit under limit.
    assert all(r.id != 1 for r in ranked)


def test_rank_similar_keyword_sorts_by_score_descending() -> None:
    cands = [
        SimilarTicketItem(id=1, tn="A", title="a", score=0.5),
        SimilarTicketItem(id=2, tn="B", title="b", score=0.9),
        SimilarTicketItem(id=3, tn="C", title="c", score=0.7),
    ]
    ranked = rank_similar_keyword(cands, exclude_id=99, limit=5)
    assert [r.id for r in ranked] == [2, 3, 1]


def test_rank_similar_keyword_stable_for_equal_scores() -> None:
    cands = [
        SimilarTicketItem(id=1, tn="A", title="a", score=0.5),
        SimilarTicketItem(id=2, tn="B", title="b", score=0.5),
        SimilarTicketItem(id=3, tn="C", title="c", score=0.5),
    ]
    ranked = rank_similar_keyword(cands, exclude_id=99, limit=5)
    # Equal scores keep their original (Meilisearch relevance) order.
    assert [r.id for r in ranked] == [1, 2, 3]
