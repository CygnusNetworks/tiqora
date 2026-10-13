"""Write-capable ticket services for plugins (names instead of ids, German errors).

Without ``as_agent`` the module-level write functions run as the system user
(no permission check, no sync hooks: the plugin is trusted). With ``as_agent``
the :class:`~tiqora.domain.ticket_write_service.TicketWriteService` is used, so
queue permissions are checked and a hook veto reaches the plugin. Znuny tables
are only written through those existing services; names are resolved with plain
single-table lookups.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.engine import get_session_factory
from tiqora.domain import ticket_write_service as tws
from tiqora.domain.dynamic_field_values import load_ticket_dynamic_field
from tiqora.identity.types import SYSTEM_USER_ID
from tiqora.znuny.sysconfig import SysConfig

__all__ = ["PluginPermissionDenied", "PluginWriteError", "TicketDraft"]

#: core channels a plugin may write through ``add_article``; besides these only its own channels
_PLAIN_CHANNELS = frozenset({"internal", "phone"})


class PluginWriteError(ValueError):
    """The write was refused or the input names something that does not exist (German text)."""


class PluginPermissionDenied(PermissionError):
    """``as_agent`` lacks the queue permission for this write."""


@dataclass(frozen=True, slots=True)
class TicketDraft:
    title: str
    queue: str  # Znuny full queue name, e.g. "Raw" or "Support::Level 2"
    customer_user: str | None = None
    customer_id: str | None = None
    state: str = "new"
    priority: str = "3 normal"
    type: str | None = None
    body: str = ""  # first article; "" = ticket without article
    subject: str | None = None  # default: title
    content_type: str = "text/plain; charset=utf-8"
    sender_type: Literal["agent", "customer", "system"] = "agent"
    channel: str = "internal"
    from_address: str | None = None
    visible_for_customer: bool = False
    dynamic_fields: Mapping[str, Sequence[str]] = field(default_factory=dict)


async def run_in_session[T](
    session: AsyncSession | None, work: Callable[[AsyncSession], Awaitable[T]]
) -> T:
    """Run ``work`` in the caller's session (caller commits) or in an own one.

    An own session commits on success and rolls back on any exception.
    """
    if session is not None:
        return await _translated(work, session)
    async with get_session_factory()() as own:
        try:
            result = await _translated(work, own)
            await own.commit()
            return result
        except BaseException:
            await own.rollback()
            raise


async def _translated[T](work: Callable[[AsyncSession], Awaitable[T]], session: AsyncSession) -> T:
    try:
        return await work(session)
    except tws.TicketAccessDenied as exc:
        raise PluginPermissionDenied(str(exc)) from exc
    except tws.TicketNotFound as exc:
        raise PluginWriteError(f"Ticket {exc} gibt es nicht") from exc
    except tws.InvalidInput as exc:
        raise PluginWriteError(str(exc)) from exc


async def _lookup_id(session: AsyncSession, table: str, label: str, name: str) -> int:
    row = (
        await session.execute(
            text(f"SELECT id FROM {table} WHERE name = :n AND valid_id = 1 LIMIT 1"),  # noqa: S608
            {"n": name},
        )
    ).first()
    if row is None:
        raise PluginWriteError(f"{label} „{name}“ gibt es nicht")
    return int(row[0])


def _check_channel(channel: str, plugin_id: str | None) -> str:
    """``internal``, ``phone`` or a loaded channel of the calling plugin (D3/D11); else refused.

    E-mail, Telegram, chat and other plugins' channels are never written from here.
    """
    key = channel.strip().lower()
    if key in _PLAIN_CHANNELS:
        return key
    if plugin_id is not None:
        from tiqora.plugins.channels import driver_for  # channels imports this module

        driver = driver_for(key)
        if driver is not None and driver.plugin_id == plugin_id:
            return driver.key
    raise PluginWriteError(
        f"Kanal „{channel}“ ist hier nicht erlaubt: eine Erweiterung schreibt nur über "
        "„internal“, „phone“ oder einen eigenen Kanal"
    )


def _service(session: AsyncSession) -> tws.TicketWriteService:
    return tws.TicketWriteService(session, get_session_factory(), SysConfig(session))


async def _check_dynamic_field_names(session: AsyncSession, names: Sequence[str]) -> None:
    for name in names:
        if await load_ticket_dynamic_field(session, name) is None:
            raise PluginWriteError(f"Dynamisches Feld „{name}“ gibt es nicht")


async def create_ticket(
    draft: TicketDraft,
    *,
    as_agent: int | None,
    session: AsyncSession | None,
    plugin_id: str | None = None,
) -> int:
    channel = _check_channel(draft.channel, plugin_id) if draft.body else "internal"

    async def work(s: AsyncSession) -> int:
        queue_id = await _lookup_id(s, "queue", "Queue", draft.queue)
        state_id = await _lookup_id(s, "ticket_state", "Status", draft.state)
        priority_id = await _lookup_id(s, "ticket_priority", "Priorität", draft.priority)
        type_id = (
            await _lookup_id(s, "ticket_type", "Typ", draft.type)
            if draft.type is not None
            else None
        )
        await _check_dynamic_field_names(s, list(draft.dynamic_fields))
        article = (
            tws.ArticleIn(
                sender_type=draft.sender_type,
                is_visible_for_customer=draft.visible_for_customer,
                subject=draft.subject if draft.subject is not None else draft.title,
                body=draft.body,
                content_type=draft.content_type,
                from_address=draft.from_address,
                channel=channel,
            )
            if draft.body
            else None
        )
        params = tws.TicketIn(
            title=draft.title,
            queue_id=queue_id,
            state_id=state_id,
            priority_id=priority_id,
            owner_id=as_agent if as_agent is not None else SYSTEM_USER_ID,
            type_id=type_id,
            customer_id=draft.customer_id,
            customer_user_id=draft.customer_user,
            dynamic_fields={k: list(v) for k, v in draft.dynamic_fields.items()},
            article=article,
        )
        if as_agent is None:
            return await tws.create_ticket(
                s, get_session_factory(), SysConfig(s), params=params, user_id=SYSTEM_USER_ID
            )
        return await _service(s).create_ticket(as_agent, params)

    return await run_in_session(session, work)


async def add_article(
    ticket_id: int,
    *,
    body: str,
    subject: str,
    sender_type: Literal["agent", "customer", "system"],
    channel: str,
    visible_for_customer: bool,
    from_address: str | None,
    content_type: str,
    as_agent: int | None,
    session: AsyncSession | None,
    plugin_id: str | None = None,
) -> int:
    key = _check_channel(channel, plugin_id)

    async def work(s: AsyncSession) -> int:
        article = tws.ArticleIn(
            sender_type=sender_type,
            is_visible_for_customer=visible_for_customer,
            subject=subject,
            body=body,
            content_type=content_type,
            from_address=from_address,
            channel=key,
        )
        if as_agent is None:
            return await tws.add_article(
                s,
                ticket_id=ticket_id,
                article=article,
                user_id=SYSTEM_USER_ID,
                sysconfig=SysConfig(s),
            )
        return await _service(s).add_article(as_agent, ticket_id, article)

    return await run_in_session(session, work)


async def set_state(
    ticket_id: int, state: str, *, as_agent: int | None, session: AsyncSession | None
) -> None:
    async def work(s: AsyncSession) -> None:
        state_id = await _lookup_id(s, "ticket_state", "Status", state)
        if as_agent is None:
            await tws.change_state(
                s,
                ticket_id=ticket_id,
                new_state_id=state_id,
                user_id=SYSTEM_USER_ID,
                sysconfig=SysConfig(s),
            )
        else:
            await _service(s).change_state(as_agent, ticket_id, state_id)

    await run_in_session(session, work)


async def move(
    ticket_id: int, queue: str, *, as_agent: int | None, session: AsyncSession | None
) -> None:
    async def work(s: AsyncSession) -> None:
        queue_id = await _lookup_id(s, "queue", "Queue", queue)
        if as_agent is None:
            await tws.move_queue(
                s,
                ticket_id=ticket_id,
                new_queue_id=queue_id,
                user_id=SYSTEM_USER_ID,
                sysconfig=SysConfig(s),
            )
        else:
            await _service(s).move_queue(as_agent, ticket_id, queue_id)

    await run_in_session(session, work)


async def set_dynamic_field(
    ticket_id: int,
    name: str,
    values: Sequence[str],
    *,
    as_agent: int | None,
    session: AsyncSession | None,
) -> None:
    async def work(s: AsyncSession) -> None:
        await _check_dynamic_field_names(s, [name])
        if as_agent is None:
            await tws._ticket_must_exist(s, ticket_id)
            await tws.update_dynamic_fields_strict(
                s, ticket_id=ticket_id, values={name: list(values)}, user_id=SYSTEM_USER_ID
            )
        else:
            await _service(s).update_dynamic_field(
                as_agent, ticket_id, field_name=name, values=list(values)
            )

    await run_in_session(session, work)
