"""Unit tests for agent channel outbound queue ACL (security review)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tiqora.channels.common import assert_ticket_note_permission
from tiqora.domain.ticket_write_service import TicketAccessDenied, TicketNotFound


@pytest.mark.asyncio
async def test_assert_ticket_note_permission_missing_ticket() -> None:
    session = MagicMock()
    with patch(
        "tiqora.channels.common._ticket_must_exist",
        new=AsyncMock(side_effect=TicketNotFound(99)),
    ):
        with pytest.raises(TicketNotFound):
            await assert_ticket_note_permission(session, user_id=1, ticket_id=99)


@pytest.mark.asyncio
async def test_assert_ticket_note_permission_denied() -> None:
    session = MagicMock()
    with (
        patch(
            "tiqora.channels.common._ticket_must_exist",
            new=AsyncMock(return_value={"id": 1, "queue_id": 7}),
        ),
        patch("tiqora.channels.common.PermissionEngine") as pe_cls,
    ):
        pe_cls.return_value.check = AsyncMock(return_value=False)
        with pytest.raises(TicketAccessDenied):
            await assert_ticket_note_permission(session, user_id=2, ticket_id=1)
        pe_cls.return_value.check.assert_awaited_once_with(2, 7, "note")


@pytest.mark.asyncio
async def test_assert_ticket_note_permission_ok() -> None:
    session = MagicMock()
    with (
        patch(
            "tiqora.channels.common._ticket_must_exist",
            new=AsyncMock(return_value={"id": 1, "queue_id": 7}),
        ),
        patch("tiqora.channels.common.PermissionEngine") as pe_cls,
    ):
        pe_cls.return_value.check = AsyncMock(return_value=True)
        await assert_ticket_note_permission(session, user_id=2, ticket_id=1)


@pytest.mark.asyncio
async def test_whatsapp_send_outbound_checks_acl_before_article() -> None:
    from tiqora.channels.whatsapp import service as wa

    session = MagicMock()
    sysconfig = MagicMock()
    gateway = MagicMock()
    gateway.send_text = AsyncMock()

    with (
        patch(
            "tiqora.channels.whatsapp.service.assert_ticket_note_permission",
            new=AsyncMock(side_effect=TicketAccessDenied("nope")),
        ) as assert_perm,
        patch(
            "tiqora.channels.whatsapp.service.add_article",
            new=AsyncMock(return_value=123),
        ) as add_article,
    ):
        with pytest.raises(TicketAccessDenied):
            await wa.send_outbound_text(
                session,
                sysconfig,
                gateway,
                ticket_id=5,
                to="+491234",
                body="hi",
                user_id=9,
            )
        assert_perm.assert_awaited_once()
        add_article.assert_not_awaited()
        gateway.send_text.assert_not_awaited()
