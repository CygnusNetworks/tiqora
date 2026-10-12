"""Runtime context handed to plugin code (HTTP egress, ticket reads, settings, logging)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

import httpx
import structlog
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.engine import get_session_factory
from tiqora.plugins import writes as _writes
from tiqora.plugins.customers import PluginCustomers
from tiqora.security.outbound import HostResolver, pin_outbound_url

if TYPE_CHECKING:
    from tiqora.plugins.channels import PluginChannels
    from tiqora.plugins.discovery import DiscoveredPlugin


class OutboundHostNotAllowed(PermissionError):
    """The plugin tried to reach a host that its manifest does not list."""


@dataclass(frozen=True, slots=True)
class TicketSummary:
    id: int
    tn: str
    title: str
    queue_id: int
    queue_name: str
    state: str
    state_type: str
    priority: str
    owner_id: int
    customer_user_id: str | None
    customer_id: str | None


class PluginHttp:
    """Outbound HTTP limited to the hosts in the plugin manifest (SSRF-pinned)."""

    def __init__(
        self,
        allowed_hosts: Sequence[str],
        *,
        resolver: HostResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # ``resolver`` and ``transport`` exist for tests only.
        self._allowed = tuple(h.strip().lower() for h in allowed_hosts)
        self._resolver = resolver
        self._transport = transport

    def host_allowed(self, host: str) -> bool:
        """Exact (case-insensitive) match, or ``*.suffix`` for subdomains (not the bare suffix)."""
        host = host.strip().lower().rstrip(".")
        if not host:
            return False
        for pattern in self._allowed:
            if pattern.startswith("*."):
                suffix = pattern[1:]  # ".example.net"
                if host.endswith(suffix) and len(host) > len(suffix):
                    return True
            elif host == pattern:
                return True
        return False

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,  # noqa: ASYNC109  (httpx request timeout, not a task deadline)
    ) -> httpx.Response:
        host = urlparse(url).hostname
        if not host or not self.host_allowed(host):
            raise OutboundHostNotAllowed(
                f"Der Host „{host or url}“ ist im Manifest der Erweiterung nicht freigegeben"
            )
        # pin_outbound_url resolves DNS with a blocking getaddrinfo: keep it off the event loop.
        pinned = await asyncio.to_thread(pin_outbound_url, url, resolver=self._resolver)
        async with httpx.AsyncClient(
            follow_redirects=False, transport=self._transport, timeout=timeout
        ) as client:
            return await client.request(
                method,
                pinned.request_url,
                json=json,
                content=content,
                headers=pinned.request_headers(dict(headers) if headers else None),
                extensions=pinned.request_extensions(),
            )


_TICKET_SQL = text(
    "SELECT t.id, t.tn, t.title, t.queue_id, q.name, s.name, st.name, p.name, "
    "t.user_id, t.customer_user_id, t.customer_id "
    "FROM ticket t "
    "JOIN queue q ON q.id = t.queue_id "
    "JOIN ticket_state s ON s.id = t.ticket_state_id "
    "JOIN ticket_state_type st ON st.id = s.type_id "
    "JOIN ticket_priority p ON p.id = t.ticket_priority_id "
    "WHERE t.id = :id"
)


class PluginTickets:
    """Ticket access for plugins: reads, plus writes by name (see ``tiqora.plugins.writes``).

    Without ``as_agent`` a write runs as the system user (no permission check, no sync
    hooks; the plugin is trusted). With ``as_agent`` permissions are checked and hooks fire.
    Names are exact: MariaDB matches case-insensitively, PostgreSQL case-sensitively.
    A passed ``session`` is never committed; without one the call commits or rolls back itself.
    """

    async def get(
        self,
        ticket_id: int,
        *,
        as_agent: int | None = None,
        session: AsyncSession | None = None,
    ) -> TicketSummary | None:
        """Summary of a ticket, or None when it does not exist.

        Without ``as_agent`` this runs in system context and checks no permission.
        In MCP tools and HTTP routes always pass ``current_agent_id(ctx)`` / ``user.id``:
        the ticket is then only returned when that agent holds ``ro`` on its queue.
        """
        if session is None:
            async with get_session_factory()() as own:
                return await self.get(ticket_id, as_agent=as_agent, session=own)
        row = (await session.execute(_TICKET_SQL, {"id": ticket_id})).first()
        if row is None:
            return None
        if as_agent is not None:
            from tiqora.permissions.engine import PermissionEngine

            if not await PermissionEngine(session).check(as_agent, int(row[3]), "ro"):
                return None
        return TicketSummary(
            id=int(row[0]),
            tn=str(row[1]),
            title="" if row[2] is None else str(row[2]),
            queue_id=int(row[3]),
            queue_name=str(row[4]),
            state=str(row[5]),
            state_type=str(row[6]),
            priority=str(row[7]),
            owner_id=int(row[8]),
            customer_user_id=None if row[9] is None else str(row[9]),
            customer_id=None if row[10] is None else str(row[10]),
        )

    async def create(
        self,
        draft: _writes.TicketDraft,
        *,
        as_agent: int | None = None,
        session: AsyncSession | None = None,
    ) -> int:
        """Create a ticket (queue, state, priority and type by name); returns its id."""
        return await _writes.create_ticket(draft, as_agent=as_agent, session=session)

    async def add_article(
        self,
        ticket_id: int,
        *,
        body: str,
        subject: str = "",
        sender_type: Literal["agent", "customer", "system"] = "agent",
        channel: str = "internal",
        visible_for_customer: bool = False,
        from_address: str | None = None,
        content_type: str = "text/plain; charset=utf-8",
        as_agent: int | None = None,
        session: AsyncSession | None = None,
    ) -> int:
        """Add a note or phone article; mail goes through a plugin channel, never from here."""
        return await _writes.add_article(
            ticket_id,
            body=body,
            subject=subject,
            sender_type=sender_type,
            channel=channel,
            visible_for_customer=visible_for_customer,
            from_address=from_address,
            content_type=content_type,
            as_agent=as_agent,
            session=session,
        )

    async def set_state(
        self,
        ticket_id: int,
        state: str,
        *,
        as_agent: int | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        await _writes.set_state(ticket_id, state, as_agent=as_agent, session=session)

    async def move(
        self,
        ticket_id: int,
        queue: str,
        *,
        as_agent: int | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        await _writes.move(ticket_id, queue, as_agent=as_agent, session=session)

    async def set_dynamic_field(
        self,
        ticket_id: int,
        name: str,
        values: Sequence[str],
        *,
        as_agent: int | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        await _writes.set_dynamic_field(ticket_id, name, values, as_agent=as_agent, session=session)


@dataclass(frozen=True)
class PluginContext:
    plugin_id: str
    version: str
    http: PluginHttp
    tickets: PluginTickets
    customers: PluginCustomers = field(default_factory=PluginCustomers)
    channels: PluginChannels | None = None

    @property
    def logger(self) -> structlog.typing.FilteringBoundLogger:
        return structlog.get_logger("plugin").bind(plugin=self.plugin_id)  # type: ignore[no-any-return]

    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return get_session_factory()

    async def settings(self, session: AsyncSession | None = None) -> BaseModel:
        """Current values of the plugin's settings section (secrets decrypted)."""
        from tiqora.config import get_settings
        from tiqora.extensions.core import get_registry
        from tiqora.settings_sections.section import SETTINGS_SECTIONS
        from tiqora.settings_sections.store import load_section_values

        section = get_registry().get(SETTINGS_SECTIONS, f"plugins.{self.plugin_id}")
        if section is None:
            raise LookupError("Erweiterung hat keine Einstellungen")
        secret_key = get_settings().secret_key
        if session is None:
            async with get_session_factory()() as own:
                return await load_section_values(own, section, secret_key=secret_key)
        return await load_section_values(session, section, secret_key=secret_key)


def minimal_context(plugin_id: str) -> PluginContext:
    """Fallback context for items staged without one (tests, direct construction)."""
    from tiqora.plugins.channels import PluginChannels  # channels imports this module

    return PluginContext(
        plugin_id,
        "0",
        http=PluginHttp([]),
        tickets=PluginTickets(),
        channels=PluginChannels(plugin_id),
    )


def make_plugin_context(plugin: DiscoveredPlugin) -> PluginContext:
    """Context for a discovered plugin: its manifest's egress allowlist and version."""
    if plugin.manifest is None:
        from tiqora.plugins.registrar import RegistrationRejected  # registrar imports this module

        raise RegistrationRejected("Erweiterung hat kein gültiges Manifest")
    from tiqora.plugins.channels import PluginChannels  # channels imports this module

    return PluginContext(
        plugin_id=plugin.plugin_id,
        version=plugin.manifest.version,
        http=PluginHttp(plugin.manifest.capabilities.outbound_hosts),
        tickets=PluginTickets(),
        channels=PluginChannels(plugin.plugin_id),
    )
