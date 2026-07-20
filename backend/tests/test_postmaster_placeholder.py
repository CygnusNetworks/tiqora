"""Unit tests for channels/email/placeholder.py (<OTRS_...> tag expansion)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tiqora.channels.email.placeholder import (
    PlaceholderContext,
    expand_placeholders,
)
from tiqora.znuny.sysconfig import SysConfig


def _sysconfig(values: dict[str, Any] | None = None) -> SysConfig:
    values = values or {}

    async def fetch(name: str) -> Any | None:
        return values.get(name)

    return SysConfig(fetch=fetch)


@pytest.mark.asyncio
async def test_ticket_tags_expand() -> None:
    result = await expand_placeholders(
        None,  # type: ignore[arg-type]
        _sysconfig(),
        "Your ticket <OTRS_TICKET_TicketNumber> (<OTRS_TICKET_State>) was updated.",
        ticket={"TicketNumber": "2026071910123", "State": "open"},
        queue_name="Raw",
        customer_subject="orig subject",
        customer_email_lines=["line1", "line2"],
    )
    assert result == "Your ticket 2026071910123 (open) was updated."


@pytest.mark.asyncio
async def test_queue_and_customer_subject_tags() -> None:
    result = await expand_placeholders(
        None,  # type: ignore[arg-type]
        _sysconfig(),
        "Queue: <OTRS_QUEUE> / Re: <OTRS_CUSTOMER_SUBJECT>",
        ticket={},
        queue_name="Support",
        customer_subject="Help needed",
        customer_email_lines=[],
    )
    assert result == "Queue: Support / Re: Help needed"


@pytest.mark.asyncio
async def test_customer_email_n_lines() -> None:
    result = await expand_placeholders(
        None,  # type: ignore[arg-type]
        _sysconfig(),
        "Quoted:\n<OTRS_CUSTOMER_EMAIL[2]>",
        ticket={},
        queue_name="Raw",
        customer_subject="",
        customer_email_lines=["one", "two", "three"],
    )
    assert result == "Quoted:\none\ntwo"


@pytest.mark.asyncio
async def test_config_tag_expands_via_sysconfig() -> None:
    result = await expand_placeholders(
        None,  # type: ignore[arg-type]
        _sysconfig({"FQDN": "tiqora.example.com"}),
        "Visit <OTRS_CONFIG_FQDN> for details.",
        ticket={},
        queue_name="Raw",
        customer_subject="",
        customer_email_lines=[],
    )
    assert result == "Visit tiqora.example.com for details."


@pytest.mark.asyncio
async def test_unknown_tag_replaced_with_empty() -> None:
    """Unresolved tags become empty — never left as raw <OTRS_...> markup."""
    result = await expand_placeholders(
        None,  # type: ignore[arg-type]
        _sysconfig(),
        "See <OTRS_COMPLETELY_UNKNOWN_TAG> here.",
        ticket={},
        queue_name="Raw",
        customer_subject="",
        customer_email_lines=[],
    )
    assert result == "See  here."
    assert "<OTRS_" not in result


@pytest.mark.asyncio
async def test_agent_and_current_tags_from_context() -> None:
    ctx = PlaceholderContext(
        current_user={
            "userfirstname": "Ada",
            "userlastname": "Lovelace",
            "userfullname": "Ada Lovelace",
            "userlogin": "ada",
        },
        ticket={"ticketnumber": "T1", "title": "Hello"},
        queue_name="Support",
    )
    result = await expand_placeholders(
        None,  # type: ignore[arg-type]
        _sysconfig(),
        "<OTRS_AGENT_UserFirstname> <OTRS_CURRENT_UserLastname> / <OTRS_TICKET_Title>",
        context=ctx,
    )
    assert result == "Ada Lovelace / Hello"


@pytest.mark.asyncio
async def test_customer_data_and_queue_field_from_context() -> None:
    ctx = PlaceholderContext(
        customer={"wpnum": "WP-42", "userfirstname": "Bob"},
        queue={"name": "Support", "comment": "main"},
        queue_name="Support",
    )
    result = await expand_placeholders(
        None,  # type: ignore[arg-type]
        _sysconfig(),
        "wp=<OTRS_CUSTOMER_DATA_wpnum> q=<OTRS_QUEUE_Name> c=<OTRS_QUEUE_Comment>",
        context=ctx,
    )
    assert result == "wp=WP-42 q=Support c=main"


@pytest.mark.asyncio
async def test_unknown_queue_field_empty_not_raw() -> None:
    ctx = PlaceholderContext(queue={"name": "Support"}, queue_name="Support")
    result = await expand_placeholders(
        None,  # type: ignore[arg-type]
        _sysconfig(),
        "https://startup.<OTRS_QUEUE_Domain>/?wpn=<OTRS_CUSTOMER_DATA_wpnum>",
        context=ctx,
    )
    assert result == "https://startup./?wpn="
    assert "<OTRS_" not in result


@pytest.mark.asyncio
async def test_expansion_error_returns_original_text() -> None:
    """Best-effort: failures must not raise; original text is returned."""
    with patch(
        "tiqora.channels.email.placeholder._expand_placeholders_inner",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        original = "Keep <OTRS_TICKET_TicketNumber> raw on error"
        result = await expand_placeholders(
            None,  # type: ignore[arg-type]
            _sysconfig(),
            original,
            ticket={"TicketNumber": "1"},
            queue_name="Raw",
            customer_subject="",
            customer_email_lines=[],
        )
    assert result == original
