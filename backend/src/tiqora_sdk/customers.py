"""Names for customer sources (``registry.customer_source``).

A customer source is a directory the plugin knows (an ERP, an LDAP, a CRM). Agents see its
hits beside the local customers when they search, and import one with a click; with
``materialize_on_inbound=True`` an e-mail from an address nobody owns is looked up too.
A hit is written **once** into the customer table; Tiqora never updates it from the source
afterwards. Imported customers cannot sign in to the portal until a password is set the
normal way. Return ``None`` or an empty list for "not found"; an exception or a timeout
(``timeout_seconds``, at most 10) counts as "not found" too.

``search`` is ``async def (ctx, query, limit) -> list[ExternalCustomer]``; ``by_login`` and
``by_email`` are ``async def (ctx, key) -> ExternalCustomer | None``. The e-mail address of the
person returned by ``by_email`` must be the one asked for.
"""

from __future__ import annotations

from tiqora.plugins.customer_sources import (
    CustomerLookup,
    CustomerSearch,
    CustomerSourceContext,
    ExternalCompany,
    ExternalCustomer,
)

__all__ = [
    "CustomerLookup",
    "CustomerSearch",
    "CustomerSourceContext",
    "ExternalCompany",
    "ExternalCustomer",
]
