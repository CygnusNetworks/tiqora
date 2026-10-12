"""The documented GI event list must match what the requester really fires."""

from pathlib import Path

from tiqora.gi.config import _TICKET_EVENTS, is_ticket_event

_DOC = (Path(__file__).resolve().parents[2] / "docs" / "compatibility.md").read_text()


def _events_row() -> str:
    return next(line for line in _DOC.splitlines() if line.startswith("| Events | Fired:"))


def test_every_fired_event_is_documented() -> None:
    row = _events_row()
    for event in sorted(_TICKET_EVENTS):
        assert f"`{event}`" in row, event


def test_unfired_events_are_not_claimed() -> None:
    row = _events_row()
    for event in ("EscalationResponseTimeStart", "TicketDelete", "TicketFlagSet", "ArticleUpdate"):
        assert not is_ticket_event(event)
        assert event not in row.split("; synchronous")[0]
    assert "ticket_history" not in row


def test_unfired_row_and_outbound_wording() -> None:
    assert "| Ticket events not in the fired list above:" in _DOC
    assert "never allowed, even when listed" in _DOC
    assert "OAuth2Token" in _DOC and "X509CertFile" in _DOC
