"""Server-side resolution of the per-queue external customer-tool link
shown as a second button in the ticket-zoom header.

See ``tiqora.db.tiqora.models.TiqoraQueueCustomerLink`` for the config
table and ``tiqora.api.v1.admin.customer_links`` for its admin CRUD. The
resolved link is agent-auth-only (``GET /tickets/{id}/customer-link``,
``tiqora.api.v1.tickets``) — never exposed to the customer portal.
"""

from __future__ import annotations

from urllib.parse import quote

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.tiqora.models import TiqoraQueueCustomerLink

# Recognized {placeholder} tokens in url_template / admin_url_template.
# Values are URL-encoded (quote(), safe="") before substitution.
_PLACEHOLDERS = (
    "customer_user",
    "customer_id",
    "ticket_number",
    "customer_email",
    "customer_name",
)


class ResolvedCustomerLink(BaseModel):
    label: str | None
    url: str | None


def _strip_login_suffix(customer_user_id: str) -> str:
    """``z50test#3`` -> ``z50test`` (Znuny appends a ``#N`` disambiguator)."""
    return customer_user_id.split("#", 1)[0]


async def _customer_name_email(
    session: AsyncSession, login: str
) -> tuple[str, str]:
    """``(full_name, email)`` for a stripped ``customer_user.login``; empty
    strings when the login has no matching row (never a hard failure — the
    button should still render with blank placeholders rather than 500)."""
    row = (
        await session.execute(
            select(CustomerUser.first_name, CustomerUser.last_name, CustomerUser.email).where(
                CustomerUser.login == login
            )
        )
    ).first()
    if row is None:
        return "", ""
    first_name, last_name, email = row
    name = " ".join(part for part in (first_name, last_name) if part).strip()
    return name, email or ""


def _render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key in _PLACEHOLDERS:
        rendered = rendered.replace("{" + key + "}", quote(values.get(key, ""), safe=""))
    return rendered


async def resolve_customer_link(
    session: AsyncSession,
    *,
    queue_id: int,
    ticket_number: str,
    customer_id: str | None,
    customer_user_id: str | None,
    is_admin: bool,
) -> ResolvedCustomerLink:
    """Resolve the configured external customer link for a ticket, or
    ``ResolvedCustomerLink(label=None, url=None)`` when none applies —
    either no config exists for the ticket's queue, or ``visibility`` hides
    it from this (non-admin) agent."""
    row = (
        await session.execute(
            select(TiqoraQueueCustomerLink).where(TiqoraQueueCustomerLink.queue_id == queue_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return ResolvedCustomerLink(label=None, url=None)
    if row.visibility == "admins" and not is_admin:
        return ResolvedCustomerLink(label=None, url=None)

    template = row.url_template
    if is_admin and row.admin_url_template:
        template = row.admin_url_template

    login = _strip_login_suffix(customer_user_id) if customer_user_id else ""
    customer_name, customer_email = ("", "")
    if login:
        customer_name, customer_email = await _customer_name_email(session, login)

    url = _render_template(
        template,
        {
            "customer_user": login,
            "customer_id": customer_id or "",
            "ticket_number": ticket_number,
            "customer_email": customer_email,
            "customer_name": customer_name,
        },
    )
    return ResolvedCustomerLink(label=row.label, url=url)
