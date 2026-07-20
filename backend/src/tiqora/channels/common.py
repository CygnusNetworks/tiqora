"""Shared helpers for inbound/outbound channel plugins (SMS, WhatsApp, Phone, ...).

Every additional :class:`~tiqora.channels` plugin (SMS, WhatsApp Business,
Phone/CTI) needs the same three building blocks that the email pipeline
already has bespoke versions of:

- registering its ``communication_channel`` row on first use,
- resolving an inbound sender to a ``customer_user`` (here: by phone number
  instead of email address),
- finding an existing open ticket to append to, or creating a new one — this
  reuses :func:`tiqora.znuny.followup.detect_followup` (subject/body ticket-
  number tag scan) exactly like the email pipeline, then falls back to
  "most recent non-closed ticket for this customer_user" for channels whose
  messages rarely echo the ``Ticket::Hook`` tag back (SMS/WhatsApp replies).

All config for these channels is intentionally kept in ``tiqora_settings``
(namespaced ``channel.<name>.<key>``) rather than a new table/migration —
see docs/channels.md. Every channel defaults to *disabled*.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.domain.settings_store import get_setting, get_setting_bool
from tiqora.domain.ticket_write_service import TicketIn, create_ticket
from tiqora.znuny.followup import detect_followup
from tiqora.znuny.sysconfig import SysConfig


def setting_key(channel: str, name: str) -> str:
    """Namespaced ``tiqora_settings`` key for one channel config value."""
    return f"channel.{channel}.{name}"


async def channel_enabled(session: AsyncSession, channel: str) -> bool:
    return await get_setting_bool(session, setting_key(channel, "enabled"), False)


async def channel_setting(
    session: AsyncSession, channel: str, name: str, default: str | None = None
) -> str | None:
    value = await get_setting(session, setting_key(channel, name))
    return value if value not in (None, "") else default


async def _lookup_id(session: AsyncSession, table: str, name_col: str, value: str) -> int | None:
    row = (
        await session.execute(
            text(f"SELECT id FROM {table} WHERE {name_col} = :v LIMIT 1"), {"v": value}
        )
    ).first()
    return int(row[0]) if row is not None else None


async def ensure_channel_row(session: AsyncSession, name: str, module: str) -> int:
    """Return ``communication_channel.id`` for *name*, inserting it if absent.

    Znuny's ``communication_channel.channel_data`` column is a Perl
    ``Storable::nfreeze`` blob. Since Tiqora writes no Perl code, and these
    are Tiqora-only channels Znuny's own UI never renders, we copy the
    ``channel_data`` bytes of an existing built-in row (``Internal``)
    verbatim rather than trying to hand-construct Storable bytes.
    """
    existing = await _lookup_id(session, "communication_channel", "name", name)
    if existing is not None:
        return existing

    template = (
        await session.execute(
            text("SELECT channel_data FROM communication_channel WHERE name = 'Internal' LIMIT 1")
        )
    ).first()
    channel_data = template[0] if template is not None and template[0] is not None else b""

    await session.execute(
        text(
            "INSERT INTO communication_channel"
            " (name, module, package_name, channel_data, valid_id,"
            "  create_time, create_by, change_time, change_by)"
            " VALUES (:n, :m, 'Tiqora', :cd, 1, current_timestamp, 1, current_timestamp, 1)"
        ),
        {"n": name, "m": module, "cd": channel_data},
    )
    channel_id = await _lookup_id(session, "communication_channel", "name", name)
    if channel_id is None:
        raise RuntimeError(f"communication_channel insert for {name!r} failed")
    return channel_id


def normalize_phone(raw: str | None) -> str:
    """Strip everything but digits (leading ``+`` dropped too) for suffix matching."""
    if not raw:
        return ""
    return "".join(ch for ch in raw if ch.isdigit())


async def resolve_customer_by_phone(
    session: AsyncSession, phone: str | None
) -> tuple[str | None, str | None]:
    """Match ``customer_user.phone``/``.mobile`` against *phone* (suffix match,
    normalized to digits-only, to tolerate ``+49``/``0``/spaces/dashes
    formatting differences). Returns ``(customer_id, customer_user_login)``."""
    norm = normalize_phone(phone)
    if not norm:
        return None, None
    suffix = norm[-9:] if len(norm) > 9 else norm
    pattern = f"%{suffix}"
    row = (
        await session.execute(
            text(
                "SELECT login, customer_id FROM customer_user"
                " WHERE REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(phone, ''),"
                "  ' ', ''), '-', ''), '/', ''), '+', '') LIKE :pat"
                " OR REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(mobile, ''),"
                "  ' ', ''), '-', ''), '/', ''), '+', '') LIKE :pat"
                " LIMIT 1"
            ),
            {"pat": pattern},
        )
    ).first()
    if row is None:
        return None, None
    login, customer_id = row
    return (str(customer_id) if customer_id else None), str(login)


async def resolve_ticket_for_inbound(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    channel: str,
    body_text: str,
    customer_no: str | None,
    customer_user_id: str | None,
    title: str,
    user_id: int,
    queue_name: str | None = None,
) -> tuple[int, bool]:
    """Return ``(ticket_id, created)``: append to an existing ticket when
    *body_text* contains a ``Ticket::Hook`` tag (:func:`detect_followup`, same
    check the email pipeline uses) or when the customer already has a
    non-closed ticket; otherwise create a new one."""
    followup = await detect_followup(session, sysconfig, subject=body_text, references=[])
    if followup is not None:
        _tn, ticket_id = followup
        return ticket_id, False

    if customer_user_id:
        row = (
            await session.execute(
                text(
                    "SELECT t.id FROM ticket t"
                    " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                    " WHERE t.customer_user_id = :cu AND tst.name NOT IN ('closed', 'removed')"
                    " ORDER BY t.id DESC LIMIT 1"
                ),
                {"cu": customer_user_id},
            )
        ).first()
        if row is not None:
            return int(row[0]), False

    resolved_queue = queue_name or await channel_setting(session, channel, "queue_name")
    resolved_queue = resolved_queue or await sysconfig.postmaster_default_queue()
    state_name = await sysconfig.postmaster_default_state()
    priority_name = await sysconfig.postmaster_default_priority()

    queue_id = await _lookup_id(session, "queue", "name", resolved_queue) or 1
    state_id = await _lookup_id(session, "ticket_state", "name", state_name) or 1
    priority_id = await _lookup_id(session, "ticket_priority", "name", priority_name) or 3

    params = TicketIn(
        title=title[:255],
        queue_id=queue_id,
        state_id=state_id,
        priority_id=priority_id,
        owner_id=user_id,
        customer_id=customer_no,
        customer_user_id=customer_user_id,
    )
    ticket_id = await create_ticket(
        session, session_factory, sysconfig, params=params, user_id=user_id
    )
    return ticket_id, True


def verify_shared_secret(expected: str | None, provided: str | None) -> bool:
    """Constant-time shared-secret check; False (deny) if either side is empty."""
    import hmac

    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)
