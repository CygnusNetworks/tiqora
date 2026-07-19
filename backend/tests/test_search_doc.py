"""Unit tests for Meilisearch document building (no Meilisearch required)."""

from __future__ import annotations

from datetime import datetime

from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.search import build_ticket_document


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
