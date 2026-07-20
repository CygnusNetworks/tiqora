"""Unit tests for the ticket-history renderer (no DB).

Table-driven coverage of every ``history_type`` the renderer maps, plus the
generic fallback for unknown/legacy types and graceful handling of
space-only / empty names.
"""

from __future__ import annotations

import pytest

from tiqora.domain.history_render import render_history_entry


def _resolve(uid: int | str | None) -> str | None:
    return {2: "valerius", 5: "boss"}.get(int(uid)) if uid is not None else None


# (history_type, name, expected_rendered)
CASES: list[tuple[str | None, str, str]] = [
    (
        "NewTicket",
        "%%2026062610000016%%studentenwerk-bonn%%3 normal%%open%%42866",
        'Ticket 2026062610000016 created in queue "studentenwerk-bonn" with'
        ' priority "3 normal" and state "open".',
    ),
    (
        "StateUpdate",
        "%%open%%closed successful%%",
        'State changed from "open" to "closed successful".',
    ),
    ("Move", "%%Junk%%3%%Raw%%2", 'Queue changed to "Junk" from "Raw".'),
    ("TitleUpdate", "%%Old title%%New title", 'Title changed from "Old title" to "New title".'),
    (
        "TypeUpdate",
        "%%Incident%%2%%Unclassified%%1",
        'Type changed from "Unclassified" to "Incident".',
    ),
    (
        "PriorityUpdate",
        "%%3 normal%%3%%5 very high%%5",
        'Priority changed from "3 normal" to "5 very high".',
    ),
    ("OwnerUpdate", "%%valerius%%2", "Owner set to valerius."),
    ("ResponsibleUpdate", "%%boss%%5", "Responsible set to boss."),
    ("Lock", "%%lock", "Ticket locked."),
    ("Unlock", "%%unlock", "Ticket unlocked."),
    (
        "CustomerUpdate",
        "%%CustomerID=abc;CustomerUser=jdoe;",
        'Customer ID set to "abc", Customer user set to "jdoe".',
    ),
    ("SetPendingTime", "%%2026-07-01 09:00", "Pending time set to 2026-07-01 09:00."),
    ("Subscribe", "%%Jane Doe", "Jane Doe started watching this ticket."),
    ("Unsubscribe", "%%Jane Doe", "Jane Doe stopped watching this ticket."),
    (
        "TicketDynamicFieldUpdate",
        "%%FieldName%%ProcessID%%Value%%P2%%OldValue%%P1",
        'Field "ProcessID" changed from "P1" to "P2".',
    ),
    ("ArchiveFlagUpdate", "%%y", "Ticket archived."),
    ("ArchiveFlagUpdate", "%%n", "Ticket unarchived."),
    ("Merged", "%%2026...02%%22%%2026...01%%11", "Merged into ticket 2026...01."),
    ("Misc", "%%Reset of unlock time.", "Reset of unlock time."),
    ("Forward", "%%agent@corp.de", 'Forwarded to "agent@corp.de".'),
    ("Bounce", "%%other@corp.de", 'Bounced to "other@corp.de".'),
    ("EmailAgent", "%% Re: something", "Email sent to customer."),
    ("PhoneCallAgent", "%%", "Phone call to customer logged."),
    ("PhoneCallCustomer", "%%", "Phone call from customer logged."),
    ("WebRequestCustomer", "%%", "Web request received from customer."),
]


@pytest.mark.parametrize(("htype", "name", "expected"), CASES)
def test_render_known_types(htype: str | None, name: str, expected: str) -> None:
    assert render_history_entry(history_type=htype, name=name, resolve_user=_resolve) == expected


def test_owner_update_falls_back_to_raw_login_without_resolver() -> None:
    assert (
        render_history_entry(history_type="OwnerUpdate", name="%%valerius%%2")
        == "Owner set to valerius."
    )


def test_unknown_type_generic_fallback_drops_trailing_numeric_id() -> None:
    out = render_history_entry(history_type="SomethingNew", name="%%foo%%bar%%42866")
    assert out == "SomethingNew: foo, bar"


def test_plain_non_encoded_name_passthrough() -> None:
    assert (
        render_history_entry(history_type=None, name="Reset of unlock time.")
        == "Reset of unlock time."
    )


def test_empty_and_space_only_names_are_graceful() -> None:
    assert render_history_entry(history_type="FollowUp", name="") == "Follow-up."
    assert render_history_entry(history_type="Misc", name="%%") == "—"
    assert render_history_entry(history_type=None, name="  ") == "(no detail)"


def test_followup_with_tn() -> None:
    out = render_history_entry(history_type="FollowUp", name="%%2026...16")
    assert "2026...16" in out
