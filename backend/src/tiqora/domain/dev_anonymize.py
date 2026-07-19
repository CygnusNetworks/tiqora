"""PII scrubber for a restored dump copy (``tiqora dev anonymize``).

This is a bulk data-scrubbing tool, not a business-logic writer: it updates
rows with plain SQL (no history/outbox/cache invariants — there is nothing
left to keep consistent once the source Znuny process is gone). It must
never be pointed at a live/production database; the CLI layer enforces an
explicit ``--database-url`` with no fallback to the configured
``DATABASE_URL``.

Referential consistency (design)
---------------------------------
The same original value must map to the same replacement everywhere it
occurs (e.g. a customer's email in ``customer_user.email`` and inside
``article_data_mime.a_from``/``a_to``). :class:`ValueMapper` gets there by
hashing ``(seed, kind, original_value)`` into a per-value Faker seed, so:

* the mapping is a pure function of (seed, kind, original value) — no
  shared mutable RNG state to keep synchronised across tables or runs;
* the same ``--seed`` reproduces the same anonymized output across runs
  (useful for diffing/testing);
* different original values map to different replacements with
  overwhelming probability (SHA-256 spread), without needing a global
  "already used" registry.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.legacy.article import ArticleDataMime
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.db.legacy.user import Users

_EMAIL_RE = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+")


class AnonymizeError(Exception):
    """Raised for anonymizer configuration/usage errors."""


def _require_faker() -> type:
    try:
        from faker import Faker
    except ImportError as exc:  # pragma: no cover - exercised only w/o faker installed
        raise AnonymizeError(
            "The 'faker' package is required for `tiqora dev anonymize` but is not "
            "installed. It ships in the backend 'dev' dependency group — run "
            "`uv sync --all-extras` (or `uv sync --group dev`) from backend/."
        ) from exc
    return Faker


def _chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class ValueMapper:
    """Deterministic ``(kind, original) -> replacement`` mapping, cached per run.

    Pure and DB-free — safe to unit-test directly. ``kind`` namespaces the
    replacement pool (e.g. ``"email"`` vs ``"first_name"``) so the same raw
    string used in two different roles doesn't accidentally collide.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._seed = seed if seed is not None else 0
        self._cache: dict[tuple[str, str], str] = {}

    def _faker_for(self, kind: str, original: str) -> Any:
        Faker = _require_faker()
        digest = hashlib.sha256(f"{self._seed}:{kind}:{original}".encode()).hexdigest()
        fake = Faker()
        fake.seed_instance(int(digest[:16], 16))
        return fake

    def map_value(self, original: str | None, kind: str) -> str | None:
        if not original:
            return original
        key = (kind, original)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        fake = self._faker_for(kind, original)
        if kind == "first_name":
            value = str(fake.first_name())
        elif kind == "last_name":
            value = str(fake.last_name())
        elif kind == "email":
            value = str(fake.email())
        elif kind == "login":
            value = str(fake.user_name())
        elif kind == "company":
            value = str(fake.company())
        else:
            value = str(fake.word())
        self._cache[key] = value
        return value

    def anonymize_address_field(self, value: str | None) -> str | None:
        """Replace every email address found in *value*, leaving the rest intact."""
        if not value:
            return value
        return _EMAIL_RE.sub(lambda m: self.map_value(m.group(0), "email") or "", value)

    def anonymize_body(self, value: str | None) -> str | None:
        """Lorem-scrub a body, preserving line count and rough per-line length."""
        if not value:
            return value
        fake = self._faker_for("body", value)
        out_lines: list[str] = []
        for line in value.split("\n"):
            length = len(line)
            if length == 0:
                out_lines.append("")
                continue
            text_line = fake.text(max_nb_chars=max(length, 5))
            out_lines.append(text_line[:length])
        return "\n".join(out_lines)


@dataclass
class AnonymizeResult:
    customer_users: int = 0
    customer_companies: int = 0
    users: int = 0
    articles: int = 0
    progress: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "Anonymization summary",
            "======================",
            f"customer_user rows updated:     {self.customer_users}",
            f"customer_company rows updated:  {self.customer_companies}",
            f"users rows updated:             {self.users}",
            f"article_data_mime rows updated: {self.articles}",
        ]
        return "\n".join(lines)


async def _anonymize_customer_users(
    session_factory: async_sessionmaker[AsyncSession],
    mapper: ValueMapper,
    batch_size: int,
    progress: list[str],
) -> int:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    CustomerUser.id,
                    CustomerUser.first_name,
                    CustomerUser.last_name,
                    CustomerUser.email,
                    CustomerUser.login,
                )
            )
        ).all()
    total = 0
    for chunk in _chunks(rows, batch_size):
        params = [
            {
                "id": row.id,
                "first_name": mapper.map_value(row.first_name, "first_name"),
                "last_name": mapper.map_value(row.last_name, "last_name"),
                "email": mapper.map_value(row.email, "email"),
                "login": mapper.map_value(row.login, "login"),
            }
            for row in chunk
        ]
        if params:
            async with session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE customer_user SET first_name = :first_name,"
                        " last_name = :last_name, email = :email, login = :login"
                        " WHERE id = :id"
                    ),
                    params,
                )
        total += len(chunk)
        progress.append(f"customer_user: {total}/{len(rows)} rows updated")
    return total


async def _anonymize_customer_companies(
    session_factory: async_sessionmaker[AsyncSession],
    mapper: ValueMapper,
    batch_size: int,
    progress: list[str],
) -> int:
    async with session_factory() as session:
        rows = (
            await session.execute(select(CustomerCompany.customer_id, CustomerCompany.name))
        ).all()
    total = 0
    for chunk in _chunks(rows, batch_size):
        params = [
            {"customer_id": row.customer_id, "name": mapper.map_value(row.name, "company")}
            for row in chunk
        ]
        if params:
            async with session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE customer_company SET name = :name WHERE customer_id = :customer_id"
                    ),
                    params,
                )
        total += len(chunk)
        progress.append(f"customer_company: {total}/{len(rows)} rows updated")
    return total


async def _anonymize_users(
    session_factory: async_sessionmaker[AsyncSession],
    mapper: ValueMapper,
    batch_size: int,
    progress: list[str],
) -> int:
    """Replace agent first/last names, but keep ``login`` intact.

    Design choice: agent logins map to real people who debug/operate the
    system (SSO/auth wiring, escalation ownership, audit trails). Renaming
    display names satisfies the PII-scrub goal while keeping a restored
    dump's agent accounts debuggable/correlatable against the live system.
    """
    async with session_factory() as session:
        rows = (await session.execute(select(Users.id, Users.first_name, Users.last_name))).all()
    total = 0
    for chunk in _chunks(rows, batch_size):
        params = [
            {
                "id": row.id,
                "first_name": mapper.map_value(row.first_name, "first_name"),
                "last_name": mapper.map_value(row.last_name, "last_name"),
            }
            for row in chunk
        ]
        if params:
            async with session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE users SET first_name = :first_name, last_name = :last_name"
                        " WHERE id = :id"
                    ),
                    params,
                )
        total += len(chunk)
        progress.append(f"users: {total}/{len(rows)} rows updated")
    return total


async def _anonymize_articles(
    session_factory: async_sessionmaker[AsyncSession],
    mapper: ValueMapper,
    batch_size: int,
    progress: list[str],
) -> int:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    ArticleDataMime.id,
                    ArticleDataMime.a_from,
                    ArticleDataMime.a_to,
                    ArticleDataMime.a_cc,
                    ArticleDataMime.a_body,
                )
            )
        ).all()
    total = 0
    for chunk in _chunks(rows, batch_size):
        params = [
            {
                "id": row.id,
                "a_from": mapper.anonymize_address_field(row.a_from),
                "a_to": mapper.anonymize_address_field(row.a_to),
                "a_cc": mapper.anonymize_address_field(row.a_cc),
                "a_body": mapper.anonymize_body(row.a_body),
            }
            for row in chunk
        ]
        if params:
            async with session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE article_data_mime SET a_from = :a_from, a_to = :a_to,"
                        " a_cc = :a_cc, a_body = :a_body WHERE id = :id"
                    ),
                    params,
                )
        total += len(chunk)
        progress.append(f"article_data_mime: {total}/{len(rows)} rows updated")
    return total


async def anonymize_database(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    seed: int | None = None,
    batch_size: int = 500,
) -> AnonymizeResult:
    """Scrub PII in-place across customer_user, customer_company, users, and
    article_data_mime. Caller is responsible for pointing *session_factory*
    at a restored dump copy, never a live database.
    """
    mapper = ValueMapper(seed=seed)
    progress: list[str] = []
    result = AnonymizeResult(progress=progress)
    result.customer_users = await _anonymize_customer_users(
        session_factory, mapper, batch_size, progress
    )
    result.customer_companies = await _anonymize_customer_companies(
        session_factory, mapper, batch_size, progress
    )
    result.users = await _anonymize_users(session_factory, mapper, batch_size, progress)
    result.articles = await _anonymize_articles(session_factory, mapper, batch_size, progress)
    return result
