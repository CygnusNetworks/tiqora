"""Pure unit tests for ``evaluate_transition`` (no DB) — subtask 2.

Builds ``TransitionConfig``/``TransitionConditionBlock``/
``TransitionConditionField`` objects directly (as subtask 1's
``test_process_config.py`` does via YAML parsing) and feeds them straight to
``evaluate_transition`` with a hand-built ``ticket_attrs`` dict.

Also hosts a mocked ``TicketCreate`` transition-action unit test (no DB).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tiqora.process.config import (
    TransitionConditionBlock,
    TransitionConditionField,
    TransitionConfig,
)
from tiqora.process.engine import _action_ticket_create, evaluate_transition
from tiqora.process.exceptions import RequiredFieldMissing

ATTRS = {
    "Queue": "Raw",
    "State": "open",
    "Priority": "3 normal",
    "Title": "Please help me with my printer",
    "DynamicField_Make": "Toyota",
}


def _config(
    *fields: dict[str, tuple[object, str]],
    block_type: str = "and",
    condition_linking: str = "and",
) -> TransitionConfig:
    """Build a TransitionConfig with one condition block per *fields* dict."""
    blocks = [
        TransitionConditionBlock(
            type_=block_type,
            fields={
                name: TransitionConditionField(match=match, type_=type_)
                for name, (match, type_) in field_map.items()
            },
        )
        for field_map in fields
    ]
    return TransitionConfig(condition_linking=condition_linking, conditions=blocks)


def test_no_condition_key_matches_unconditionally() -> None:
    config = TransitionConfig(condition_linking="and", conditions=[])
    assert evaluate_transition(config, ATTRS) is True


def test_empty_fields_in_block_is_vacuously_true() -> None:
    config = _config({})
    assert evaluate_transition(config, ATTRS) is True


# --- String -----------------------------------------------------------------


def test_string_exact_match() -> None:
    config = _config({"Queue": ("Raw", "String")})
    assert evaluate_transition(config, ATTRS) is True


def test_string_is_case_sensitive() -> None:
    # Verified against znuny-6.5.22 TransitionValidation/String.pm: plain
    # Perl `eq`, no lc() — case-SENSITIVE, unlike Equal/NotEqual.
    config = _config({"Queue": ("raw", "String")})
    assert evaluate_transition(config, ATTRS) is False


def test_string_mismatch() -> None:
    config = _config({"Queue": ("Misc", "String")})
    assert evaluate_transition(config, ATTRS) is False


def test_string_non_string_match_never_matches() -> None:
    config = _config({"Queue": (123, "String")})
    assert evaluate_transition(config, ATTRS) is False


# --- Regexp -------------------------------------------------------------


def test_regexp_matches() -> None:
    config = _config({"Title": (r"printer", "Regexp")})
    assert evaluate_transition(config, ATTRS) is True


def test_regexp_no_match() -> None:
    config = _config({"Title": (r"^printer", "Regexp")})
    assert evaluate_transition(config, ATTRS) is False


def test_regexp_invalid_pattern_is_non_matching_not_raising() -> None:
    config = _config({"Title": (r"[unclosed", "Regexp")})
    assert evaluate_transition(config, ATTRS) is False


# --- Contains / NotContains ----------------------------------------------


def test_contains_is_case_insensitive_substring() -> None:
    config = _config({"Title": ("PRINTER", "Contains")})
    assert evaluate_transition(config, ATTRS) is True


def test_contains_no_match() -> None:
    config = _config({"Title": ("scanner", "Contains")})
    assert evaluate_transition(config, ATTRS) is False


def test_not_contains_true_when_absent() -> None:
    config = _config({"Title": ("scanner", "NotContains")})
    assert evaluate_transition(config, ATTRS) is True


def test_not_contains_false_when_present() -> None:
    config = _config({"Title": ("printer", "NotContains")})
    assert evaluate_transition(config, ATTRS) is False


# --- Equal / NotEqual ------------------------------------------------------


def test_equal_is_case_insensitive() -> None:
    config = _config({"State": ("OPEN", "Equal")})
    assert evaluate_transition(config, ATTRS) is True


def test_equal_mismatch() -> None:
    config = _config({"State": ("closed", "Equal")})
    assert evaluate_transition(config, ATTRS) is False


def test_not_equal_true_when_different() -> None:
    config = _config({"State": ("closed", "NotEqual")})
    assert evaluate_transition(config, ATTRS) is True


def test_not_equal_false_when_same_case_insensitive() -> None:
    config = _config({"State": ("OPEN", "NotEqual")})
    assert evaluate_transition(config, ATTRS) is False


# --- Missing field defaults to "" -------------------------------------------


def test_missing_field_treated_as_empty_string() -> None:
    config = _config({"DynamicField_Missing": ("", "String")})
    assert evaluate_transition(config, ATTRS) is True


def test_missing_field_nonempty_match_fails() -> None:
    config = _config({"DynamicField_Missing": ("anything", "String")})
    assert evaluate_transition(config, ATTRS) is False


# --- GreaterThan / GreaterThanOrEqual / LessThan / LessThanOrEqual ----------


def test_greater_than_numeric() -> None:
    config = _config({"Score": ("5", "GreaterThan")})
    assert evaluate_transition(config, {**ATTRS, "Score": "10"}) is True
    assert evaluate_transition(config, {**ATTRS, "Score": "5"}) is False
    assert evaluate_transition(config, {**ATTRS, "Score": "3"}) is False


def test_greater_than_non_integer_is_non_matching() -> None:
    # Znuny IsInteger gate: Priority "3 normal" is not an integer → False.
    config = _config({"Priority": ("2", "GreaterThan")})
    assert evaluate_transition(config, ATTRS) is False


def test_greater_than_or_equal() -> None:
    config = _config({"Score": ("5", "GreaterThanOrEqual")})
    assert evaluate_transition(config, {**ATTRS, "Score": "5"}) is True
    assert evaluate_transition(config, {**ATTRS, "Score": "6"}) is True
    assert evaluate_transition(config, {**ATTRS, "Score": "4"}) is False


def test_greater_than_equals_alias() -> None:
    config = _config({"Score": ("5", "GreaterThanEquals")})
    assert evaluate_transition(config, {**ATTRS, "Score": "5"}) is True


def test_less_than_numeric() -> None:
    config = _config({"Score": ("5", "LessThan")})
    assert evaluate_transition(config, {**ATTRS, "Score": "3"}) is True
    assert evaluate_transition(config, {**ATTRS, "Score": "5"}) is False
    assert evaluate_transition(config, {**ATTRS, "Score": "8"}) is False


def test_less_than_or_equal() -> None:
    config = _config({"Score": ("5", "LessThanOrEqual")})
    assert evaluate_transition(config, {**ATTRS, "Score": "5"}) is True
    assert evaluate_transition(config, {**ATTRS, "Score": "4"}) is True
    assert evaluate_transition(config, {**ATTRS, "Score": "6"}) is False


def test_less_than_equals_alias() -> None:
    config = _config({"Score": ("5", "LessThanEquals")})
    assert evaluate_transition(config, {**ATTRS, "Score": "5"}) is True


def test_greater_than_datetime_compares_as_epoch() -> None:
    # ValueValidate converts YYYY-MM-DD HH:MM:SS → epoch before IsInteger compare.
    config = _config({"Due": ("2020-01-01 00:00:00", "GreaterThan")})
    assert evaluate_transition(config, {**ATTRS, "Due": "2021-06-15 12:00:00"}) is True
    assert evaluate_transition(config, {**ATTRS, "Due": "2019-01-01 00:00:00"}) is False


def test_greater_than_date_only() -> None:
    config = _config({"Due": ("2020-01-01", "GreaterThan")})
    assert evaluate_transition(config, {**ATTRS, "Due": "2020-06-01"}) is True
    assert evaluate_transition(config, {**ATTRS, "Due": "2019-12-31"}) is False


def test_module_based_condition_is_unsupported_and_non_matching() -> None:
    config = _config({"Queue": ("Raw", "Module")})
    assert evaluate_transition(config, ATTRS) is False


# --- TicketCreate transition action (mocked, no DB) -------------------------


@pytest.mark.asyncio
async def test_action_ticket_create_builds_ticket_in_and_links() -> None:
    """TicketCreate resolves Config → TicketIn, calls create_ticket, optional LinkAs."""
    session = MagicMock()
    sysconfig = MagicMock()
    sysconfig.get_str = AsyncMock(side_effect=lambda name, default="": default)

    created_params: dict[str, Any] = {}

    async def _fake_create(
        _session: Any,
        _factory: Any,
        _sysconfig: Any,
        *,
        params: Any,
        user_id: int,
    ) -> int:
        created_params["params"] = params
        created_params["user_id"] = user_id
        return 99

    async def _resolve_queue(_s: Any, name: str | None, id_: int | None) -> int | None:
        if id_ is not None:
            return int(id_)
        return 7 if name == "Raw" else None

    async def _resolve_state(_s: Any, name: str | None, id_: int | None) -> int | None:
        if id_ is not None:
            return int(id_)
        return 4 if name == "new" else None

    async def _resolve_priority(_s: Any, name: str | None, id_: int | None) -> int | None:
        if id_ is not None:
            return int(id_)
        return 3 if name == "3 normal" else None

    async def _resolve_user(_s: Any, login: str | None, id_: int | None) -> int | None:
        if id_ is not None:
            return int(id_)
        return 5 if login == "agent" else None

    link_calls: list[dict[str, Any]] = []

    async def _fake_link(
        _session: Any,
        *,
        source_ticket_id: int,
        target_ticket_id: int,
        link_type: str,
        user_id: int,
    ) -> None:
        link_calls.append(
            {
                "source": source_ticket_id,
                "target": target_ticket_id,
                "type": link_type,
                "user_id": user_id,
            }
        )

    with (
        patch("tiqora.process.engine.create_ticket", side_effect=_fake_create),
        patch("tiqora.process.engine.link_tickets", side_effect=_fake_link),
        patch("tiqora.process.engine._resolve_queue_id", side_effect=_resolve_queue),
        patch("tiqora.process.engine._resolve_state_id", side_effect=_resolve_state),
        patch("tiqora.process.engine._resolve_priority_id", side_effect=_resolve_priority),
        patch("tiqora.process.engine._resolve_user_id", side_effect=_resolve_user),
        patch("tiqora.process.engine._resolve_type_id", new_callable=AsyncMock, return_value=None),
        patch(
            "tiqora.process.engine._resolve_service_id", new_callable=AsyncMock, return_value=None
        ),
        patch("tiqora.process.engine._resolve_sla_id", new_callable=AsyncMock, return_value=None),
        patch(
            "tiqora.process.engine._resolve_link_as",
            new_callable=AsyncMock,
            return_value=("ParentChild", "Source"),
        ),
        patch("tiqora.process.engine._session_factory_from", return_value=MagicMock()),
    ):
        await _action_ticket_create(
            session,
            {
                "Title": "Child ticket",
                "Queue": "Raw",
                "State": "new",
                "Priority": "3 normal",
                "Owner": "agent",
                "CustomerID": "ACME",
                "CustomerUserID": "bob@example.com",
                "Body": "Hello",
                "Subject": "Subj",
                "SenderType": "agent",
                "IsVisibleForCustomer": 1,
                "CommunicationChannel": "Internal",
                "LinkAs": "Parent",
            },
            ticket_id=42,
            user_id=1,
            sysconfig=sysconfig,
        )

    params = created_params["params"]
    assert created_params["user_id"] == 1
    assert params.title == "Child ticket"
    assert params.queue_id == 7
    assert params.state_id == 4
    assert params.priority_id == 3
    assert params.owner_id == 5
    assert params.customer_id == "ACME"
    assert params.customer_user_id == "bob@example.com"
    assert params.article is not None
    assert params.article.body == "Hello"
    assert params.article.subject == "Subj"
    assert link_calls == [
        {"source": 99, "target": 42, "type": "ParentChild", "user_id": 1},
    ]


@pytest.mark.asyncio
async def test_action_ticket_create_requires_title() -> None:
    session = MagicMock()
    sysconfig = MagicMock()
    with pytest.raises(RequiredFieldMissing, match="Title"):
        await _action_ticket_create(session, {}, ticket_id=1, user_id=1, sysconfig=sysconfig)


# --- Field combination within a block (and/or) ------------------------------


def test_block_and_requires_all_fields() -> None:
    config = _config(
        {"Queue": ("Raw", "String"), "State": ("closed", "String")},
        block_type="and",
    )
    assert evaluate_transition(config, ATTRS) is False


def test_block_or_requires_any_field() -> None:
    config = _config(
        {"Queue": ("Raw", "String"), "State": ("closed", "String")},
        block_type="or",
    )
    assert evaluate_transition(config, ATTRS) is True


# --- Block combination (condition_linking and/or) ---------------------------


def test_condition_linking_and_requires_all_blocks() -> None:
    config = _config(
        {"Queue": ("Raw", "String")},
        {"State": ("closed", "String")},
        condition_linking="and",
    )
    assert evaluate_transition(config, ATTRS) is False


def test_condition_linking_or_requires_any_block() -> None:
    config = _config(
        {"Queue": ("Raw", "String")},
        {"State": ("closed", "String")},
        condition_linking="or",
    )
    assert evaluate_transition(config, ATTRS) is True
