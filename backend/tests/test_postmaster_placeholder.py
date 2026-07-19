"""Unit tests for channels/email/placeholder.py (<OTRS_...> tag expansion)."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.channels.email.placeholder import expand_placeholders
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
async def test_unsupported_tag_left_verbatim() -> None:
    text = "See <OTRS_AGENT_SUBJECT> here."
    result = await expand_placeholders(
        None,  # type: ignore[arg-type]
        _sysconfig(),
        text,
        ticket={},
        queue_name="Raw",
        customer_subject="",
        customer_email_lines=[],
    )
    assert result == text
