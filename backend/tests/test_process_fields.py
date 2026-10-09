"""Pure tests for Znuny dialog field semantics (tiqora.process.fields / actor)."""

from __future__ import annotations

from tiqora.process.actor import ProcessActor
from tiqora.process.config import ActivityDialogConfig
from tiqora.process.fields import (
    canonical_field_name,
    dialog_field_order,
    effective_field_values,
    missing_required_fields,
    normalize_field_values,
    visible_fields,
)

DIALOG = ActivityDialogConfig.from_yaml("""
FieldOrder: [Title, QueueID, DynamicField_Region, DynamicField_Note, Article, Owner]
Fields:
  Title: {Display: '1'}
  QueueID: {Display: '0', DefaultValue: 'Support'}
  DynamicField_Region: {Display: '2'}
  DynamicField_Note: {Display: '0'}
  Article: {Display: '2', Config: {CommunicationChannel: Internal, IsVisibleForCustomer: '0'}}
  Owner: {Display: '1'}
Interface: [AgentInterface, CustomerInterface]
""")


def test_display_semantics_match_znuny() -> None:
    assert DIALOG.fields["Title"].required is False  # 1 = shown, optional
    assert DIALOG.fields["DynamicField_Region"].required is True  # 2 = mandatory
    assert DIALOG.fields["QueueID"].hidden is True  # 0 = hidden


def test_missing_display_key_means_hidden() -> None:
    d = ActivityDialogConfig.from_yaml("FieldOrder: [Title]\nFields:\n  Title: {}\n")
    assert d.fields["Title"].hidden is True
    assert d.fields["Title"].required is False


def test_integer_display_values_from_yaml() -> None:
    d = ActivityDialogConfig.from_yaml("Fields:\n  Title: {Display: 2}\n  Owner: {Display: 1}\n")
    assert d.fields["Title"].required is True
    assert d.fields["Owner"].required is False
    assert d.fields["Owner"].hidden is False


def test_aliases_are_canonical() -> None:
    assert canonical_field_name("QueueID") == "Queue"
    assert canonical_field_name("SLAID") == "SLA"
    assert canonical_field_name("DynamicField_X") == "DynamicField_X"


def test_field_order_is_canonical_and_deduplicated() -> None:
    d = ActivityDialogConfig.from_yaml(
        "FieldOrder: [QueueID, Queue, Title]\n"
        "Fields:\n  QueueID: {Display: '1'}\n  Title: {Display: '1'}\n"
    )
    assert dialog_field_order(d) == ["Queue", "Title"]
    # FieldOrder missing -> Fields keys
    d2 = ActivityDialogConfig.from_yaml(
        "Fields:\n  TypeID: {Display: '1'}\n  Title: {Display: '1'}\n"
    )
    assert dialog_field_order(d2) == ["Type", "Title"]


def test_visible_fields_skip_hidden_and_customer_fields() -> None:
    assert visible_fields(DIALOG, "AgentInterface") == [
        "Title",
        "DynamicField_Region",
        "Article",
        "Owner",
    ]
    assert visible_fields(DIALOG, "CustomerInterface") == [
        "Title",
        "DynamicField_Region",
        "Article",
    ]


def test_normalize_flattens_article_and_canonicalizes() -> None:
    out = normalize_field_values(
        {"QueueID": "3", "Article": {"Subject": "s", "Body": "b", "SenderType": "x"}}
    )
    assert out == {"Queue": "3", "Subject": "s", "Body": "b"}


def test_hidden_field_default_overrides_submitted_value() -> None:
    values = effective_field_values(DIALOG, {"Queue": "Junk", "Title": "x"}, "AgentInterface")
    assert values["Queue"] == "Support"
    assert values["Title"] == "x"


def test_alias_submission_hits_hidden_default() -> None:
    values = effective_field_values(DIALOG, {"QueueID": "Junk"}, "AgentInterface")
    assert values["Queue"] == "Support"
    assert "QueueID" not in values


def test_hidden_dynamic_field_without_default_is_cleared() -> None:
    values = effective_field_values(DIALOG, {"DynamicField_Note": "keep?"}, "AgentInterface")
    assert values["DynamicField_Note"] == []


def test_unlisted_submitted_fields_are_dropped() -> None:
    values = effective_field_values(
        DIALOG,
        {"Title": "t", "Priority": "1 very low", "IsVisibleForCustomer": True},
        "AgentInterface",
    )
    assert "Priority" not in values
    assert "IsVisibleForCustomer" not in values


def test_customer_cannot_submit_owner() -> None:
    values = effective_field_values(DIALOG, {"Owner": "2"}, "CustomerInterface")
    assert "Owner" not in values


def test_customer_skipped_hidden_field_gets_no_default() -> None:
    d = ActivityDialogConfig.from_yaml(
        "FieldOrder: [Lock]\nFields:\n  Lock: {Display: '0', DefaultValue: lock}\n"
    )
    assert effective_field_values(d, {}, "CustomerInterface") == {}
    assert effective_field_values(d, {}, "AgentInterface") == {"Lock": "lock"}


def test_article_needs_subject_and_body() -> None:
    v = effective_field_values(
        DIALOG,
        {"Article": {"Subject": "s", "Body": ""}, "DynamicField_Region": "EU"},
        "AgentInterface",
    )
    assert missing_required_fields(DIALOG, v, "AgentInterface") == ["Article"]
    v2 = effective_field_values(
        DIALOG, {"Subject": "s", "Body": "b", "DynamicField_Region": "EU"}, "AgentInterface"
    )
    assert missing_required_fields(DIALOG, v2, "AgentInterface") == []


def test_missing_required_lists_in_dialog_order_and_ignores_optional() -> None:
    v = effective_field_values(DIALOG, {}, "AgentInterface")
    assert missing_required_fields(DIALOG, v, "AgentInterface") == [
        "DynamicField_Region",
        "Article",
    ]


def test_required_hidden_for_customer_is_not_required() -> None:
    d = ActivityDialogConfig.from_yaml("FieldOrder: [Owner]\nFields:\n  Owner: {Display: '2'}\n")
    assert missing_required_fields(d, {}, "CustomerInterface") == []
    assert missing_required_fields(d, {}, "AgentInterface") == ["Owner"]


def test_actor_agent_and_customer() -> None:
    agent = ProcessActor.agent(7)
    assert (agent.interface, agent.user_id, agent.frontend_action) == (
        "AgentInterface",
        7,
        "AgentTicketProcess",
    )
    cust = ProcessActor.customer(login="c@example.org", customer_id="ACME")
    assert cust.interface == "CustomerInterface"
    assert cust.user_id == 1  # Znuny CustomerPanelUserID = portal system user
    assert cust.customer_user_login == "c@example.org"
    assert cust.customer_id == "ACME"
    assert cust.frontend_action == "CustomerTicketProcess"
