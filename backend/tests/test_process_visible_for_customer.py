"""Znuny process configs store flags as the STRINGS '0'/'1' -- Perl truthiness, never ``bool``.

``bool("0")`` is True, so an internal note configured with ``IsVisibleForCustomer: 0``
used to become customer-visible. The engine must apply Perl truthiness
(``perl_truthy``) at every site: ArticleSend (default 1), TicketArticleCreate (default 0),
TicketCreate and the activity-dialog Article field (read from the dialog *config*, never the
client, default 0 -- ``// 0`` in AgentTicketProcess.pm:5687 / CustomerTicketProcess.pm:3972).
``RequiredLock`` is ``if ($ActivityDialogHashRef->{RequiredLock})`` (AgentTicketProcess.pm:183).

The article writers are replaced by recorders (no DB needed, nothing committed).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.process import engine as eng
from tiqora.process.actor import ProcessActor
from tiqora.process.config import ActivityDialogConfig, ActivityDialogFieldConfig
from tiqora.znuny.sysconfig import SysConfig


def _sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Articles handed to the (stubbed) writers."""
    seen: list[Any] = []

    async def _add_article(session: Any, *, article: Any, **_: Any) -> int:
        seen.append(article)
        return 1

    async def _deliver(session: Any, sysconfig: Any, _none: Any, *, article: Any, **_: Any) -> None:
        seen.append(article)

    async def _create_ticket(
        session: Any, factory: Any, sysconfig: Any, *, params: Any, **_: Any
    ) -> int:
        seen.append(params.article)
        return 999

    async def _exists(session: Any, ticket_id: int) -> dict[str, Any]:
        return {"queue_id": 1}

    monkeypatch.setattr(eng, "add_article", _add_article)
    monkeypatch.setattr(eng, "create_ticket", _create_ticket)
    monkeypatch.setattr(eng, "_ticket_must_exist", _exists)
    monkeypatch.setattr("tiqora.channels.email.outbound_reply.deliver_agent_email_reply", _deliver)
    return seen


@pytest.mark.parametrize(
    ("raw", "expected"), [("0", False), ("1", True), (0, False), (1, True), ("", False)]
)
async def test_article_send(captured: list[Any], raw: Any, expected: bool) -> None:
    await eng._action_article_send(
        None,  # type: ignore[arg-type]
        {"Body": "x", "IsVisibleForCustomer": raw},
        1,
        1,
        _sysconfig(),
    )
    assert captured[0].is_visible_for_customer is expected


async def test_article_send_default_visible(captured: list[Any]) -> None:
    await eng._action_article_send(None, {"Body": "x"}, 1, 1, _sysconfig())  # type: ignore[arg-type]
    assert captured[0].is_visible_for_customer is True


@pytest.mark.parametrize(("raw", "expected"), [("0", False), ("1", True)])
async def test_ticket_article_create(captured: list[Any], raw: str, expected: bool) -> None:
    cfg = {"SenderType": "agent", "IsVisibleForCustomer": raw, "Body": "b"}
    await eng._action_ticket_article_create(None, cfg, 1, 1, _sysconfig())  # type: ignore[arg-type]
    assert captured[0].is_visible_for_customer is expected


async def test_ticket_article_create_default_internal(captured: list[Any]) -> None:
    await eng._action_ticket_article_create(
        None,  # type: ignore[arg-type]
        {"SenderType": "agent"},
        1,
        1,
        _sysconfig(),
    )
    assert captured[0].is_visible_for_customer is False


@pytest.mark.parametrize(("raw", "expected"), [("0", False), ("1", True)])
async def test_ticket_create(
    monkeypatch: pytest.MonkeyPatch, captured: list[Any], raw: str, expected: bool
) -> None:
    monkeypatch.setattr(eng, "_session_factory_from", lambda session: None)
    cfg = {
        "Title": "t",
        "QueueID": 1,
        "StateID": 1,
        "PriorityID": 3,
        "OwnerID": 1,
        "SenderType": "agent",
        "IsVisibleForCustomer": raw,
        "Body": "b",
    }
    await eng._action_ticket_create(None, cfg, 1, 1, _sysconfig())  # type: ignore[arg-type]
    assert captured[0].is_visible_for_customer is expected


@pytest.mark.parametrize(
    ("cfg_value", "client_value", "expected"),
    [("0", "1", False), ("1", "0", True), (None, "1", False), (True, "0", True), (1, "0", True)],
)
async def test_dialog_article_uses_dialog_config_not_client(
    captured: list[Any], cfg_value: Any, client_value: str, expected: bool
) -> None:
    field_cfg: dict[str, Any] = {"CommunicationChannel": "Internal"}
    if cfg_value is not None:
        field_cfg["IsVisibleForCustomer"] = cfg_value
    dialog = ActivityDialogConfig(
        field_order=["Article"],
        fields={"Article": ActivityDialogFieldConfig(config=field_cfg, display="1")},
    )
    session: AsyncSession = None  # type: ignore[assignment]
    await eng._apply_dialog_field_changes(
        session,
        dialog_config=dialog,
        field_values={"Subject": "s", "Body": "b", "IsVisibleForCustomer": client_value},
        ticket_id=1,
        user_id=1,
        sysconfig=_sysconfig(),
    )
    assert captured[0].is_visible_for_customer is expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("1", True), ("0", False), ("2", True), ("True", True), ("", False)]
)
async def test_required_lock_is_perl_truthy(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    locked: list[int] = []

    async def _lock(session: Any, *, ticket_id: int, **_: Any) -> None:
        locked.append(ticket_id)

    async def _apply(session: Any, **_: Any) -> None:
        return None

    monkeypatch.setattr(eng, "_ensure_required_lock", _lock)
    monkeypatch.setattr(eng, "_apply_dialog_field_changes", _apply)
    await eng.apply_activity_dialog(
        None,  # type: ignore[arg-type]
        ticket_id=7,
        dialog=ActivityDialogConfig(required_lock=raw),
        field_values={},
        actor=ProcessActor.agent(5),
        sysconfig=_sysconfig(),
    )
    assert locked == ([7] if expected else [])
