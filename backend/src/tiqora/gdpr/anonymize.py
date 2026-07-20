"""Customer anonymization: scrub PII for one customer across the shared DB.

Given a ``customer_user.login`` (or a ``customer_id``, anonymizing every
customer_user under that company), this replaces:

* ``customer_user``: first/last name, email, login, phone/fax/mobile,
  street/zip/city/country;
* ``article_data_mime`` for every article on that customer's tickets:
  ``a_from``/``a_to``/``a_cc`` (address occurrences only) and ``a_body``
  (lorem-scrubbed, structure preserved);
* ``customer_company`` (optional, ``anonymize_company=True`` — off by
  default since a company may have other, non-anonymized customer_users).

Tickets themselves (title, queue, state, timestamps) are left untouched —
they remain intact for analytics. Referential consistency (the same
original value always maps to the same replacement) is delegated to
:class:`tiqora.domain.dev_anonymize.ValueMapper`, reused here rather than
duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.legacy.article import Article, ArticleDataMime
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.dev_anonymize import ValueMapper
from tiqora.gdpr.audit import record_audit
from tiqora.gdpr.gate import require_write_gate


class CustomerNotFoundError(ValueError):
    """Raised when neither a matching login nor customer_id is found."""


@dataclass
class CustomerAnonymizeResult:
    customer_users: int = 0
    customer_companies: int = 0
    articles: int = 0
    tickets_touched: int = 0
    progress: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "Customer anonymization summary",
            "===============================",
            f"customer_user rows updated:     {self.customer_users}",
            f"customer_company rows updated:  {self.customer_companies}",
            f"article_data_mime rows updated: {self.articles}",
            f"tickets touched:                {self.tickets_touched}",
        ]
        return "\n".join(lines)

    def as_counts(self) -> dict[str, int]:
        return {
            "customer_user": self.customer_users,
            "customer_company": self.customer_companies,
            "article_data_mime": self.articles,
            "tickets_touched": self.tickets_touched,
        }


async def anonymize_customer(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    login: str | None = None,
    customer_id: str | None = None,
    seed: int | None = None,
    anonymize_company: bool = False,
    force_parallel: bool = False,
    actor: str = "cli",
) -> CustomerAnonymizeResult:
    """Anonymize one customer_user (by login) or every customer_user under a
    customer_id. Refuses unless schema-ownership is active or
    ``force_parallel=True`` (see :mod:`tiqora.gdpr.gate`).
    """
    if not login and not customer_id:
        raise ValueError("anonymize_customer requires either login or customer_id")

    async with session_factory() as session:
        await require_write_gate(
            session, settings, force_parallel=force_parallel, operation="anonymize_customer"
        )

    mapper = ValueMapper(seed=seed)
    progress: list[str] = []
    result = CustomerAnonymizeResult(progress=progress)

    async with session_factory() as session:
        query = select(CustomerUser)
        if login:
            query = query.where(CustomerUser.login == login)
        else:
            query = query.where(CustomerUser.customer_id == customer_id)
        customers = (await session.execute(query)).scalars().all()

    if not customers:
        raise CustomerNotFoundError(
            f"No customer_user found for login={login!r} customer_id={customer_id!r}"
        )

    logins = [c.login for c in customers]
    resolved_customer_id = customer_id or customers[0].customer_id

    # --- customer_user PII ---
    async with session_factory() as session, session.begin():
        for cust in customers:
            new_first = mapper.map_value(cust.first_name, "first_name")
            new_last = mapper.map_value(cust.last_name, "last_name")
            new_email = mapper.map_value(cust.email, "email")
            new_login = mapper.map_value(cust.login, "login")
            new_phone = mapper.map_value(cust.phone, "phone")
            new_fax = mapper.map_value(cust.fax, "phone")
            new_mobile = mapper.map_value(cust.mobile, "phone")
            new_street = mapper.map_value(cust.street, "street")
            new_zip = mapper.map_value(cust.zip, "zip")
            new_city = mapper.map_value(cust.city, "city")
            new_country = mapper.map_value(cust.country, "country")
            await session.execute(
                text(
                    "UPDATE customer_user SET first_name=:first_name, last_name=:last_name,"
                    " email=:email, login=:login, phone=:phone, fax=:fax, mobile=:mobile,"
                    " street=:street, zip=:zip, city=:city, country=:country WHERE id=:id"
                ),
                {
                    "id": cust.id,
                    "first_name": new_first,
                    "last_name": new_last,
                    "email": new_email,
                    "login": new_login,
                    "phone": new_phone,
                    "fax": new_fax,
                    "mobile": new_mobile,
                    "street": new_street,
                    "zip": new_zip,
                    "city": new_city,
                    "country": new_country,
                },
            )
            result.customer_users += 1
    progress.append(f"customer_user: {result.customer_users} rows updated")

    # --- optional customer_company ---
    if anonymize_company and resolved_customer_id:
        async with session_factory() as session:
            company = (
                await session.execute(
                    select(CustomerCompany).where(
                        CustomerCompany.customer_id == resolved_customer_id
                    )
                )
            ).scalar_one_or_none()
        if company is not None:
            new_name = mapper.map_value(company.name, "company")
            async with session_factory() as session, session.begin():
                await session.execute(
                    text("UPDATE customer_company SET name=:name WHERE customer_id=:customer_id"),
                    {"name": new_name, "customer_id": resolved_customer_id},
                )
            result.customer_companies = 1
            progress.append("customer_company: 1 row updated")

    # --- articles on this customer's tickets ---
    async with session_factory() as session:
        ticket_ids = (
            (await session.execute(select(Ticket.id).where(Ticket.customer_user_id.in_(logins))))
            .scalars()
            .all()
        )
    result.tickets_touched = len(ticket_ids)

    if ticket_ids:
        async with session_factory() as session:
            article_ids = (
                (await session.execute(select(Article.id).where(Article.ticket_id.in_(ticket_ids))))
                .scalars()
                .all()
            )
        if article_ids:
            async with session_factory() as session:
                rows = (
                    await session.execute(
                        select(
                            ArticleDataMime.id,
                            ArticleDataMime.a_from,
                            ArticleDataMime.a_to,
                            ArticleDataMime.a_cc,
                            ArticleDataMime.a_body,
                        ).where(ArticleDataMime.article_id.in_(article_ids))
                    )
                ).all()
            for row in rows:
                async with session_factory() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE article_data_mime SET a_from=:a_from, a_to=:a_to,"
                            " a_cc=:a_cc, a_body=:a_body WHERE id=:id"
                        ),
                        {
                            "id": row.id,
                            "a_from": mapper.anonymize_address_field(row.a_from),
                            "a_to": mapper.anonymize_address_field(row.a_to),
                            "a_cc": mapper.anonymize_address_field(row.a_cc),
                            "a_body": mapper.anonymize_body(row.a_body),
                        },
                    )
                result.articles += 1
    progress.append(f"article_data_mime: {result.articles} rows updated")

    async with session_factory() as session:
        await record_audit(
            session,
            action="anonymize_customer",
            target=login or f"customer_id:{customer_id}",
            actor=actor,
            counts=result.as_counts(),
            force_parallel=force_parallel,
        )

    return result
