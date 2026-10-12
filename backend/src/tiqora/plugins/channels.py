"""Channel drivers from plugins (D11): the point, the channel row and inbound messages.

A plugin channel is a customer-facing way of talking to customers (a chat, a messenger) that
Tiqora itself does not know. Inbound messages arrive through :meth:`PluginChannels.ingest`
(called from the plugin's own public route or task); they become customer articles on the
plugin's channel, with the same ticket matching as the other chat channels. The channel is
registered in Znuny's ``communication_channel`` table like the WebChat row (module
``Kernel::System::CommunicationChannel::Internal``, package ``Tiqora``, no article data tables),
so Znuny can read the articles. Outbound agent replies are send-then-store
(:func:`deliver_plugin_channel_reply`): the plugin's ``send`` runs first, inside the request and
under a hard timeout; only when it succeeds is the article stored, so a failed delivery leaves
nothing behind and the agent can retry.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.common import (
    EMPTY_TABLES_CHANNEL_DATA,
    ensure_channel_row,
    normalize_phone,
    resolve_customer_by_phone,
    resolve_ticket_for_inbound,
)
from tiqora.db.engine import get_session_factory
from tiqora.domain import ticket_write_service as tws
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import ExtensionPoint
from tiqora.identity.types import SYSTEM_USER_ID
from tiqora.plugins.calls import call_plugin
from tiqora.plugins.context import PluginContext, PluginTickets, TicketSummary, minimal_context
from tiqora.plugins.state import is_active
from tiqora.plugins.writes import PluginWriteError, _lookup_id, run_in_session
from tiqora.znuny.search_flag import message_id_md5
from tiqora.znuny.sysconfig import SysConfig

__all__ = [
    "CHANNEL_DRIVERS",
    "MAX_CHANNEL_TIMEOUT",
    "MIN_CHANNEL_TIMEOUT",
    "TIQORA_CHANNEL_MODULE",
    "ChannelCapabilities",
    "ChannelContext",
    "ChannelDeliveryError",
    "ChannelDriver",
    "ChannelSend",
    "DeliveryResult",
    "IngestResult",
    "InboundChannelMessage",
    "OutboundChannelMessage",
    "PluginChannels",
    "deliver_plugin_channel_reply",
    "driver_for",
    "ensure_tiqora_channel_row",
]

#: ``communication_channel.module`` of channels Tiqora owns (no Perl driver of its own)
TIQORA_CHANNEL_MODULE: Final = "Kernel::System::CommunicationChannel::Internal"
MIN_CHANNEL_TIMEOUT: Final = 1
MAX_CHANNEL_TIMEOUT: Final = 30

#: ``external_id`` ends up in a Message-ID header-like string: keep it boring
_PHONE_SHAPE_RE: Final = re.compile(r"^\+?[0-9 ()/.-]+$")
_MIN_PHONE_DIGITS: Final = 7
_EXTERNAL_ID_RE: Final = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
#: ``customer_user_id`` is varchar(250); the driver key prefix plus 150 characters fits
_MAX_SENDER: Final = 150


@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    subject: bool = False
    html: bool = False
    attachments: bool = False
    max_length: int | None = None  # characters of the plain body; None = unlimited


@dataclass(frozen=True, slots=True)
class OutboundChannelMessage:
    ticket: TicketSummary
    agent_id: int
    to: str | None  # ArticleIn.to_address (the composer fills it from the replied-to article)
    subject: str
    body: str
    content_type: str
    attachments: tuple[tuple[str, str, bytes], ...]


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    external_id: str | None = None
    to: str | None = None


@dataclass(frozen=True, slots=True)
class ChannelContext:
    plugin: PluginContext


ChannelSend = Callable[[ChannelContext, OutboundChannelMessage], Awaitable[DeliveryResult]]


@dataclass(frozen=True, slots=True)
class InboundChannelMessage:
    sender: str  # address/handle as the channel knows it (stored as from_address)
    body: str
    sender_display: str | None = None  # shown in the ticket title only
    customer_user: str | None = None  # explicit customer login, if the plugin knows it
    subject: str | None = None
    content_type: str = "text/plain; charset=utf-8"
    attachments: tuple[tuple[str, str, bytes], ...] = ()
    external_id: str | None = None  # idempotency key (webhook retries)
    queue: str | None = None  # queue name for new tickets; None = the channel default


@dataclass(frozen=True, slots=True)
class IngestResult:
    ticket_id: int
    article_id: int
    created: bool
    duplicate: bool


@dataclass(frozen=True, slots=True)
class ChannelDriver:
    key: str  # channel_key, e.g. "plugin_points_chat" (the ArticleIn.channel value)
    plugin_id: str
    name: str  # qualified, = communication_channel.name, e.g. "Plugin_points_Chat"
    label: str
    capabilities: ChannelCapabilities
    send: ChannelSend | None  # None = inbound only
    timeout_seconds: int  # 1..30
    context: PluginContext | None = None


CHANNEL_DRIVERS: Final[ExtensionPoint[ChannelDriver]] = ExtensionPoint(
    key="core.channels.drivers",
    item_key=lambda d: d.key,
    description="Customer channels (chats, messengers) a plugin adds next to e-mail and phone.",
    sdk_stable=True,
)


class ChannelDeliveryError(Exception):
    """The plugin could not deliver an agent reply (API: 502, nothing was stored)."""


def driver_for(channel: str) -> ChannelDriver | None:
    """The driver of a loaded plugin for an ``ArticleIn.channel`` value (case-insensitive)."""
    wanted = channel.lower()
    for driver in get_registry().items(CHANNEL_DRIVERS):
        if driver.key == wanted and is_active(driver.plugin_id):
            return driver
    return None


async def ensure_tiqora_channel_row(session: AsyncSession, name: str) -> int:
    """``communication_channel.id`` of a Tiqora-owned channel, registered on first use.

    Generalisation of the WebChat row: Znuny's internal module, package ``Tiqora`` and an
    empty ``ArticleDataTables`` list, so a ``ChannelDrop`` can never drop the shared MIME tables.
    """
    return await ensure_channel_row(
        session, name, TIQORA_CHANNEL_MODULE, channel_data_yaml=EMPTY_TABLES_CHANNEL_DATA
    )


class PluginChannels:
    """``ctx.channels``: what a plugin can do with its own channels."""

    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id

    def _own_driver(self, name: str) -> ChannelDriver:
        for driver in get_registry().items(CHANNEL_DRIVERS):
            # the qualified name is "Plugin_<short_id>_<Name>" and <Name> has no underscore
            if (
                driver.plugin_id == self._plugin_id
                and name in (driver.name, driver.name.rsplit("_", 1)[-1])
                and is_active(driver.plugin_id)
            ):
                return driver
        raise PluginWriteError(f"Kanal „{name}“ gehört nicht zu dieser Erweiterung")

    async def ingest(
        self, name: str, msg: InboundChannelMessage, *, session: AsyncSession | None = None
    ) -> IngestResult:
        """Store an inbound customer message of one of the plugin's channels.

        A repeated ``external_id`` returns the existing article with ``duplicate=True`` and
        writes nothing. The message is appended to the customer's open ticket or opens a new
        one. Runs as the system user; a given ``session`` is committed by the caller.
        """
        driver = self._own_driver(name)
        sender = msg.sender.strip() if isinstance(msg.sender, str) else ""
        if not sender or len(sender) > _MAX_SENDER:
            raise PluginWriteError("Absender fehlt oder ist länger als 150 Zeichen")
        if msg.external_id is not None and not _EXTERNAL_ID_RE.fullmatch(msg.external_id):
            raise PluginWriteError(
                "Die externe Kennung darf nur A-Z, a-z, 0-9, Punkt, Unterstrich und Minus "
                "enthalten (höchstens 100 Zeichen)"
            )

        async def work(s: AsyncSession) -> IngestResult:
            return await _ingest(s, driver, msg, sender)

        return await run_in_session(session, work)


async def _find_customer(
    session: AsyncSession, msg: InboundChannelMessage, sender: str
) -> tuple[str | None, str | None]:
    """``(customer_id, customer_user_id)`` of the sender, or ``(None, None)``."""
    if msg.customer_user:
        row = (
            await session.execute(
                text(
                    "SELECT login, customer_id FROM customer_user"
                    " WHERE login = :l AND valid_id = 1 LIMIT 1"
                ),
                {"l": msg.customer_user},
            )
        ).first()
        if row is not None:
            return (str(row[1]) if row[1] else None), str(row[0])
    if "@" in sender:
        row = (
            await session.execute(
                text(
                    "SELECT login, customer_id FROM customer_user"
                    " WHERE LOWER(email) = :e AND valid_id = 1 ORDER BY id LIMIT 1"
                ),
                {"e": sender.lower()},
            )
        ).first()
        if row is not None:
            return (str(row[1]) if row[1] else None), str(row[0])
    elif _PHONE_SHAPE_RE.fullmatch(sender) and len(normalize_phone(sender)) >= _MIN_PHONE_DIGITS:
        # only real phone numbers: the resolver suffix-matches the digits, so a handle like
        # "max-1" would otherwise hit any customer whose number ends in 1
        customer_id, login = await resolve_customer_by_phone(session, sender)
        if login is not None:
            return customer_id, login
    return None, None


async def _ingest(
    session: AsyncSession, driver: ChannelDriver, msg: InboundChannelMessage, sender: str
) -> IngestResult:
    channel_id = await ensure_tiqora_channel_row(session, driver.name)
    message_id: str | None = None
    if msg.external_id is not None:
        message_id = f"<{driver.key}.{msg.external_id}@tiqora.invalid>"
        existing = (
            await session.execute(
                text(
                    "SELECT a.id, a.ticket_id FROM article a"
                    " JOIN article_data_mime m ON m.article_id = a.id"
                    " WHERE a.communication_channel_id = :cc AND m.a_message_id_md5 = :md5"
                    " AND m.a_message_id = :mid ORDER BY a.id LIMIT 1"
                ),
                {"cc": channel_id, "md5": message_id_md5(message_id), "mid": message_id},
            )
        ).first()
        if existing is not None:
            return IngestResult(
                ticket_id=int(existing[1]),
                article_id=int(existing[0]),
                created=False,
                duplicate=True,
            )

    queue_name = msg.queue
    if queue_name is not None:
        await _lookup_id(session, "queue", "Queue", queue_name)  # PluginWriteError if unknown

    customer_no, customer_user = await _find_customer(session, msg, sender)
    if customer_user is None:
        # The unknown sender is the "customer user", so the next message of the same sender
        # lands on the same open ticket. E-mail addresses behave like the mail pipeline;
        # other handles get a driver prefix so they can never equal a customer login.
        customer_user = sender if "@" in sender else f"{driver.key}:{sender}"

    title = (msg.subject or f"{driver.label}: {msg.sender_display or sender}")[:255]
    sysconfig = SysConfig(session)
    ticket_id, created = await resolve_ticket_for_inbound(
        session,
        get_session_factory(),
        sysconfig,
        channel=driver.key,
        body_text=msg.body,
        customer_no=customer_no,
        customer_user_id=customer_user,
        title=title,
        user_id=SYSTEM_USER_ID,
        queue_name=queue_name,
    )
    article = tws.ArticleIn(
        sender_type="customer",
        is_visible_for_customer=True,
        subject=title if created else (msg.subject or driver.label),
        body=msg.body,
        content_type=msg.content_type,
        from_address=sender,
        message_id=message_id,
        channel=driver.key,
        attachments=list(msg.attachments),
    )
    article_id = await tws.add_article(
        session,
        ticket_id=ticket_id,
        article=article,
        user_id=SYSTEM_USER_ID,
        sysconfig=sysconfig,
    )
    return IngestResult(
        ticket_id=ticket_id, article_id=article_id, created=created, duplicate=False
    )


def _delivery_failed(driver: ChannelDriver, reason: str) -> ChannelDeliveryError:
    return ChannelDeliveryError(f"Zustellung über „{driver.label}“ fehlgeschlagen: {reason}")


async def deliver_plugin_channel_reply(
    session: AsyncSession,
    sysconfig: SysConfig,
    driver: ChannelDriver,
    *,
    ticket_id: int,
    user_id: int,
    article: tws.ArticleIn,
) -> int:
    """Send an agent reply over a plugin channel, then store it. Returns the article id.

    Send-then-store like e-mail and Telegram: the plugin's ``send`` runs first (hard timeout
    ``min(driver.timeout_seconds, 30)`` s, inside the request); a refusal, an exception or a
    timeout raises :class:`ChannelDeliveryError` and nothing is stored. The plugin's error text
    is logged by :func:`call_plugin` but never shown to the agent (it may carry secrets).
    """
    if driver.send is None:
        raise tws.InvalidInput("Über diesen Kanal kann nicht geantwortet werden")
    limit = driver.capabilities.max_length
    if limit is not None and len(article.body) > limit:
        raise tws.InvalidInput(
            f"Die Nachricht ist zu lang für {driver.label} (höchstens {limit} Zeichen)"
        )
    if article.attachments and not driver.capabilities.attachments:
        raise tws.InvalidInput(f"{driver.label} kann keine Anhänge übertragen")
    summary = await PluginTickets().get(ticket_id, session=session)
    if summary is None:
        raise tws.TicketNotFound(ticket_id)
    message = OutboundChannelMessage(
        ticket=summary,
        agent_id=user_id,
        to=article.to_address,
        subject=article.subject if driver.capabilities.subject else "",
        body=article.body,
        content_type=article.content_type,
        attachments=tuple(article.attachments),
    )
    send = driver.send
    ctx = ChannelContext(plugin=driver.context or minimal_context(driver.plugin_id))
    outcome = await call_plugin(
        driver.plugin_id,
        f"channel {driver.name} send",
        lambda: send(ctx, message),
        timeout=float(min(driver.timeout_seconds, MAX_CHANNEL_TIMEOUT)),
    )
    if not outcome.ok:
        raise _delivery_failed(
            driver,
            "Zeitüberschreitung" if outcome.timed_out else "die Erweiterung meldet einen Fehler",
        )
    result = outcome.value
    if not isinstance(result, DeliveryResult):
        raise _delivery_failed(driver, "ungültige Antwort der Erweiterung")
    external_id = result.external_id
    message_id = article.message_id
    if external_id is not None and _EXTERNAL_ID_RE.fullmatch(external_id):
        message_id = f"<{driver.key}.{external_id}@tiqora.invalid>"
    stored = replace(
        article,
        message_id=message_id,
        to_address=(result.to if isinstance(result.to, str) and result.to else article.to_address),
    )
    return await tws.add_article(
        session, ticket_id=ticket_id, article=stored, user_id=user_id, sysconfig=sysconfig
    )
