"""Customer directories from plugins: "lookup and take over" (D13).

A plugin source is asked in three situations only: an agent searches for a customer (the
result is shown beside the local ones), an agent imports one hit, and (when the plugin opted
in with ``materialize_on_inbound``) an e-mail arrives from an address no local customer owns.
A hit is written **once** into ``customer_user`` (and ``customer_company`` when it names a
company that does not exist yet) through the existing write services. Rows are never updated
from the source later, and there is no virtual ``customer_user`` table: eighty-odd modules read
that table directly.

Imported customers get an unusable password: they cannot sign in to the portal until a
password is set the normal way. SCIM creates no companies and has no caller of
``ensure_customer_company``; the take-over here is the only plugin path that creates one.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerUser
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.calls import PluginCallBudget, call_plugin
from tiqora.plugins.context import PluginContext, minimal_context
from tiqora.plugins.state import is_active

logger = structlog.get_logger(__name__)

__all__ = [
    "CUSTOMER_SOURCES",
    "SEARCH_BUDGET_SECONDS",
    "CustomerLookup",
    "CustomerSearch",
    "CustomerSource",
    "CustomerSourceConflict",
    "CustomerSourceContext",
    "ExternalCompany",
    "ExternalCustomer",
    "active_sources",
    "lookup_by_email",
    "lookup_by_login",
    "materialize",
    "search_sources",
]

#: all sources of one search / lookup together
SEARCH_BUDGET_SECONDS: float = 3.0

_LOGIN_MAX: Final = 200
_EMAIL_MAX: Final = 150
_NAME_MAX: Final = 100
_CUSTOMER_ID_MAX: Final = 150
_COMPANY_NAME_MAX: Final = 200


@dataclass(frozen=True, slots=True)
class ExternalCompany:
    customer_id: str
    name: str
    street: str | None = None
    zip: str | None = None
    city: str | None = None
    country: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalCustomer:
    login: str
    email: str
    first_name: str
    last_name: str
    customer_id: str | None = None
    title: str | None = None
    phone: str | None = None
    mobile: str | None = None
    street: str | None = None
    zip: str | None = None
    city: str | None = None
    country: str | None = None
    company: ExternalCompany | None = None


@dataclass(frozen=True, slots=True)
class CustomerSourceContext:
    plugin: PluginContext


#: ``async def (ctx, query, limit) -> list[ExternalCustomer]``
CustomerSearch = Callable[[CustomerSourceContext, str, int], Awaitable[Sequence[ExternalCustomer]]]
#: ``async def (ctx, login_or_email) -> ExternalCustomer | None``
CustomerLookup = Callable[[CustomerSourceContext, str], Awaitable[ExternalCustomer | None]]


@dataclass(frozen=True, slots=True)
class CustomerSource:
    key: str  # "<plugin_id>:<name>"
    plugin_id: str
    label: str
    search: CustomerSearch
    by_login: CustomerLookup
    by_email: CustomerLookup | None
    materialize_on_inbound: bool
    timeout_seconds: int  # 1..10
    context: PluginContext | None = None


CUSTOMER_SOURCES: Final[ExtensionPoint[CustomerSource]] = ExtensionPoint(
    key="core.customers.sources",
    item_key=lambda s: s.key,
    description="Customer directories a plugin offers for search, import and unknown senders.",
    sdk_stable=True,
)


class CustomerSourceConflict(ValueError):  # noqa: N818  (D13 name)
    """The hit cannot be taken over; the message is German and safe to show to an agent."""


def active_sources() -> list[CustomerSource]:
    """Sources of plugins that are loaded in this process, ordered by key."""
    found = [s for s in get_registry().items(CUSTOMER_SOURCES) if is_active(s.plugin_id)]
    found.sort(key=lambda s: s.key)
    return found


def _context(source: CustomerSource) -> CustomerSourceContext:
    return CustomerSourceContext(plugin=source.context or minimal_context(source.plugin_id))


def _is_text(value: object, *, optional: bool = False) -> bool:
    return isinstance(value, str) or (optional and value is None)


def _well_formed(ext: object) -> bool:
    """A plugin may return anything; only complete ``ExternalCustomer`` objects get through."""
    if not isinstance(ext, ExternalCustomer):
        return False
    required = (ext.login, ext.email, ext.first_name, ext.last_name)
    optional = (
        ext.customer_id,
        ext.title,
        ext.phone,
        ext.mobile,
        ext.street,
        ext.zip,
        ext.city,
        ext.country,
    )
    if not all(_is_text(v) for v in required) or not all(
        _is_text(v, optional=True) for v in optional
    ):
        return False
    company = ext.company
    if company is None:
        return True
    return (
        isinstance(company, ExternalCompany)
        and _is_text(company.customer_id)
        and _is_text(company.name)
    )


async def _search_one(
    source: CustomerSource, q: str, limit: int, budget: PluginCallBudget
) -> list[ExternalCustomer]:
    timeout = budget.timeout_for(float(source.timeout_seconds))
    if timeout <= 0:
        return []
    outcome = await call_plugin(
        source.plugin_id,
        f"customer source {source.key} search",
        functools.partial(source.search, _context(source), q, limit),
        timeout=timeout,
    )
    if not outcome.ok or outcome.value is None:
        return []
    try:
        hits = list(outcome.value)
    except TypeError:
        logger.warning("plugin_customer_source_bad_result", source=source.key)
        return []
    good = [h for h in hits if _well_formed(h) and h.login and h.login.strip()]
    if len(good) != len(hits):
        logger.warning("plugin_customer_source_bad_result", source=source.key)
    return good[:limit]


async def search_sources(q: str, limit: int) -> list[tuple[CustomerSource, ExternalCustomer]]:
    """Hits of every active source, in source order. A failing or slow source is skipped.

    All sources run in parallel under one shared budget.
    """
    sources = active_sources()
    if not sources or not q.strip() or limit < 1:
        return []
    budget = PluginCallBudget(SEARCH_BUDGET_SECONDS)
    results = await asyncio.gather(*(_search_one(s, q, limit, budget) for s in sources))
    return [(s, hit) for s, hits in zip(sources, results, strict=True) for hit in hits]


async def _lookup(
    source: CustomerSource, lookup: CustomerLookup, what: str, key: str, budget: PluginCallBudget
) -> ExternalCustomer | None:
    timeout = budget.timeout_for(float(source.timeout_seconds))
    if timeout <= 0:
        return None
    outcome = await call_plugin(
        source.plugin_id,
        f"customer source {source.key} {what}",
        functools.partial(lookup, _context(source), key),
        timeout=timeout,
    )
    if not outcome.ok or outcome.value is None:
        return None
    if not _well_formed(outcome.value):
        logger.warning("plugin_customer_source_bad_result", source=source.key)
        return None
    return outcome.value


async def lookup_by_login(source: CustomerSource, login: str) -> ExternalCustomer | None:
    """Ask one source for one login (the agent's import); a failure counts as "not found"."""
    ext = await _lookup(
        source, source.by_login, "by_login", login, PluginCallBudget(SEARCH_BUDGET_SECONDS)
    )
    if ext is None or ext.login != login:
        return None
    return ext


async def lookup_by_email(
    email: str, *, inbound_only: bool
) -> tuple[CustomerSource, ExternalCustomer] | None:
    """The first source that knows ``email``; ``inbound_only`` restricts to opted-in sources.

    A hit whose e-mail address differs from the one asked for is ignored: an inbound mail must
    never be attached to somebody else.
    """
    wanted = email.strip().lower()
    if not wanted:
        return None
    budget = PluginCallBudget(SEARCH_BUDGET_SECONDS)
    for source in active_sources():
        if source.by_email is None or (inbound_only and not source.materialize_on_inbound):
            continue
        ext = await _lookup(source, source.by_email, "by_email", email.strip(), budget)
        if ext is not None and ext.email.strip().lower() == wanted:
            return source, ext
    return None


def _clean(value: str | None) -> str | None:
    stripped = (value or "").strip()
    return stripped or None


async def materialize(session: AsyncSession, ext: ExternalCustomer, *, actor_id: int) -> str:
    """Write ``ext`` into ``customer_user`` (and ``customer_company``); return the login.

    An existing login is returned unchanged (no update, ever). Does not commit.
    """
    # imported here: these modules reach the api package, which imports the registrar
    from tiqora.domain.customer_company_service import ensure_customer_company
    from tiqora.domain.password_setup import unusable_password_hash
    from tiqora.domain.user_provisioning import ProvisioningError, create_customer_user

    login = ext.login.strip()
    email = ext.email.strip()
    first_name = ext.first_name.strip()
    last_name = ext.last_name.strip()
    if not login or len(login) > _LOGIN_MAX:
        raise CustomerSourceConflict("Das Kundenverzeichnis liefert keinen gültigen Login.")
    existing = (
        await session.execute(
            select(CustomerUser.login, CustomerUser.email).where(CustomerUser.login == login)
        )
    ).first()
    if existing is not None:
        # a login that exists under another e-mail is another person: never hand it back
        if (existing.email or "").strip().lower() != email.lower():
            raise CustomerSourceConflict("Der Login gehört schon zu einem anderen Kunden.")
        return str(existing.login)
    if not email or len(email) > _EMAIL_MAX:
        raise CustomerSourceConflict("Das Kundenverzeichnis liefert keine gültige E-Mail-Adresse.")
    if not last_name or len(last_name) > _NAME_MAX or len(first_name) > _NAME_MAX:
        raise CustomerSourceConflict("Das Kundenverzeichnis liefert keinen gültigen Namen.")
    owner = (
        (
            await session.execute(
                select(CustomerUser.login).where(
                    func.lower(CustomerUser.email) == email.lower(), CustomerUser.login != login
                )
            )
        )
        .scalars()
        .first()
    )
    if owner is not None:
        raise CustomerSourceConflict("Diese E-Mail-Adresse gehört schon zu einem anderen Kunden.")

    for value, limit in (
        (ext.title, 50),
        (ext.phone, 150),
        (ext.mobile, 150),
        (ext.street, 150),
        (ext.zip, 200),
        (ext.city, 200),
        (ext.country, 200),
        *(
            (v, 200)
            for v in (
                (
                    ext.company.street,
                    ext.company.zip,
                    ext.company.city,
                    ext.company.country,
                    ext.company.url,
                )
                if ext.company
                else ()
            )
        ),
    ):
        if value is not None and len(value.strip()) > limit:
            raise CustomerSourceConflict("Das Kundenverzeichnis liefert zu lange Angaben.")

    company = ext.company
    customer_id = (
        _clean(ext.customer_id) or (_clean(company.customer_id) if company else None) or email
    )
    if len(customer_id) > _CUSTOMER_ID_MAX:
        raise CustomerSourceConflict("Das Kundenverzeichnis liefert keine gültige Kundennummer.")
    if company is not None:
        company_id = _clean(company.customer_id)
        company_name = _clean(company.name)
        if company_id and company_name:
            if len(company_id) > _CUSTOMER_ID_MAX or len(company_name) > _COMPANY_NAME_MAX:
                raise CustomerSourceConflict(
                    "Das Kundenverzeichnis liefert keine gültigen Firmendaten."
                )
            await ensure_customer_company(
                session,
                customer_id=company_id,
                name=company_name,
                actor_id=actor_id,
                street=_clean(company.street),
                zip=_clean(company.zip),
                city=_clean(company.city),
                country=_clean(company.country),
                url=_clean(company.url),
            )
    try:
        cu = await create_customer_user(
            session,
            login=login,
            email=email,
            customer_id=customer_id,
            first_name=first_name,
            last_name=last_name,
            pw_hash=unusable_password_hash(),
            actor_id=actor_id,
            title=_clean(ext.title),
            phone=_clean(ext.phone),
            mobile=_clean(ext.mobile),
            street=_clean(ext.street),
            zip=_clean(ext.zip),
            city=_clean(ext.city),
            country=_clean(ext.country),
        )
    except ProvisioningError as exc:
        if exc.code == "email_in_use":
            raise CustomerSourceConflict(
                "Diese E-Mail-Adresse gehört schon zu einem anderen Kunden."
            ) from exc
        raise CustomerSourceConflict(
            "Das Kundenverzeichnis liefert keine gültige E-Mail-Adresse."
        ) from exc
    return cu.login
