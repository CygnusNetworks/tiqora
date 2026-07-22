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
import string
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from email.errors import MessageError
from email.message import Message
from email.parser import BytesParser, Parser
from email.policy import compat32
from email.utils import formataddr, getaddresses
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.legacy.article import ArticleDataMime
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.db.legacy.user import Users

_EMAIL_RE = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+")

# Address-like headers on a raw MIME message: display name + address both get
# anonymized (via anonymize_address_field), structure preserved.
_MIME_ADDRESS_HEADERS = frozenset(
    {"from", "to", "cc", "bcc", "reply-to", "return-path", "delivered-to"}
)
# Identifier headers: replaced with a deterministic opaque token (same
# treatment run_erasure already applies to article_data_mime.a_message_id).
_MIME_ID_HEADERS = frozenset({"message-id", "in-reply-to", "references"})


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


# Kinds whose replacements land in a column with a UNIQUE constraint (or that is
# effectively unique). The mapper guarantees no duplicate replacement per kind.
_UNIQUE_KINDS = frozenset({"company", "login", "email"})

# Kinds with dedicated Faker-based replacement logic in ``_draw``. Any other
# kind (i.e. an unmapped site-specific column, where callers pass the column
# name itself as "kind" — see ``tiqora.gdpr.erasure._kind_for``) gets
# format-preserving character substitution instead: digits/letters replaced
# in place, everything else (separators, whitespace) left untouched, and the
# length is always identical to the original.
_FAKER_KINDS = frozenset(
    {
        "first_name",
        "last_name",
        "email",
        "login",
        "company",
        "phone",
        "street",
        "city",
        "zip",
        "country",
        "body",
    }
)


class ValueMapper:
    """Deterministic ``(kind, original) -> replacement`` mapping, cached per run.

    Pure and DB-free — safe to unit-test directly. ``kind`` namespaces the
    replacement pool (e.g. ``"email"`` vs ``"first_name"``) so the same raw
    string used in two different roles doesn't accidentally collide.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._seed = seed if seed is not None else 0
        self._cache: dict[tuple[str, str], str] = {}
        # Per-kind registry of already-issued replacements so that kinds backing
        # UNIQUE columns (customer_company.name, customer_user.login) never emit a
        # duplicate — Faker's pools are finite and two distinct originals can
        # otherwise draw the same value, violating the constraint at UPDATE time.
        self._used: dict[str, set[str]] = {}

    def _faker_for(self, kind: str, original: str) -> Any:
        Faker = _require_faker()
        digest = hashlib.sha256(f"{self._seed}:{kind}:{original}".encode()).hexdigest()
        fake = Faker()
        fake.seed_instance(int(digest[:16], 16))
        return fake

    @staticmethod
    def _draw(fake: Any, kind: str) -> str:
        if kind == "first_name":
            return str(fake.first_name())
        if kind == "last_name":
            return str(fake.last_name())
        if kind == "email":
            return str(fake.email())
        if kind == "login":
            return str(fake.user_name())
        if kind == "company":
            return str(fake.company())
        if kind == "phone":
            return str(fake.phone_number())
        if kind == "street":
            return str(fake.street_address())
        if kind == "city":
            return str(fake.city())
        if kind == "zip":
            return str(fake.postcode())
        if kind == "country":
            return str(fake.country())
        return str(fake.word())

    @staticmethod
    def _format_preserving(fake: Any, original: str) -> str:
        """Replace digits/letters in place, keep separators, keep the length.

        Used for site-specific columns with no known PII shape (e.g. a
        numeric member ID or a mixed alphanumeric code) — a fixed-length
        Faker value (or one that overflows a narrow ``varchar``) would either
        look wrong or break the column's format invariants downstream.
        """
        out_chars: list[str] = []
        for ch in original:
            if ch.isdigit():
                out_chars.append(str(fake.random_int(min=0, max=9)))
            elif ch.isalpha():
                pool = string.ascii_uppercase if ch.isupper() else string.ascii_lowercase
                out_chars.append(str(fake.random_element(elements=pool)))
            else:
                out_chars.append(ch)
        return "".join(out_chars)

    def map_value(self, original: str | None, kind: str) -> str | None:
        if not original:
            return original
        key = (kind, original)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        # Re-draw with an attempt-salt on collision so unique-column kinds get a
        # distinct-but-still-deterministic replacement. Non-unique kinds always
        # take the first draw (attempt 0), preserving previous behaviour.
        attempt = 0
        while True:
            salt = original if attempt == 0 else f"{original}#{attempt}"
            fake = self._faker_for(kind, salt)
            if kind in _FAKER_KINDS:
                value = self._draw(fake, kind)
            else:
                value = self._format_preserving(fake, original)
            if kind not in _UNIQUE_KINDS or value not in self._used.setdefault(kind, set()):
                break
            attempt += 1
        if kind in _UNIQUE_KINDS:
            self._used[kind].add(value)
        self._cache[key] = value
        return value

    def _anonymize_display_name(self, name: str) -> str:
        """Deterministic fake full name for an address header display name.

        Handles both "First Last" and "Last, First" forms the same way: the
        whole string is replaced by a fake full name, so no fragment of the
        original (in either order) survives.
        """
        if not name:
            return name
        fake = self._faker_for("address_display_name", name)
        return str(fake.name())

    def anonymize_address_field(self, value: str | None) -> str | None:
        """Anonymize an RFC 5322 address-list header value.

        Replaces both the email address and any display name
        (``"Name" <addr>``, ``Name <addr>``, or bare ``addr``) for every
        address in the list, preserving the ``Name <mail>`` structure and
        comma-separation. Falls back to a bare email-regex substitution if
        the value doesn't parse as an address list at all (e.g. free text
        that merely contains an address).
        """
        if not value:
            return value
        pairs = getaddresses([value])
        if not pairs or all(not name and not addr for name, addr in pairs):
            return _EMAIL_RE.sub(lambda m: self.map_value(m.group(0), "email") or "", value)
        parts: list[str] = []
        for name, addr in pairs:
            new_addr = self.map_value(addr, "email") if addr else addr
            new_name = self._anonymize_display_name(name) if name else name
            parts.append(formataddr((new_name, new_addr or "")))
        return ", ".join(parts)

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

    def _scrub_mime_headers(self, msg: Message) -> None:
        """Anonymize address/subject/identifier headers on *msg*, in place."""
        original_casing: dict[str, str] = {}
        for key, _value in msg.items():
            original_casing.setdefault(key.lower(), key)

        for lower_name, orig_name in original_casing.items():
            if lower_name in _MIME_ADDRESS_HEADERS:
                values = msg.get_all(orig_name) or []
                new_values = [self.anonymize_address_field(v) for v in values]
                del msg[orig_name]
                for v in new_values:
                    if v:
                        msg[orig_name] = v
            elif lower_name == "subject":
                value = msg.get(orig_name)
                if value:
                    new_value = self.anonymize_body(str(value))
                    del msg[orig_name]
                    if new_value:
                        msg[orig_name] = new_value
            elif lower_name in _MIME_ID_HEADERS:
                values = msg.get_all(orig_name) or []
                id_values = [str(self.map_value(str(v), "login") or v) for v in values]
                del msg[orig_name]
                for v in id_values:
                    msg[orig_name] = v

    def _scrub_mime_body_parts(self, msg: Message) -> None:
        """Lorem-scrub text parts, empty out non-text (attachment) parts."""
        for part in msg.walk():
            if part.is_multipart():
                continue
            if part.get_content_maintype() == "text":
                payload_raw = part.get_payload(decode=True)
                if not payload_raw:
                    continue
                payload = cast(bytes, payload_raw)
                charset = part.get_content_charset() or "utf-8"
                try:
                    text_payload = payload.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    text_payload = payload.decode("utf-8", errors="replace")
                new_text = self.anonymize_body(text_payload) or ""
                part.set_payload(new_text)
                if "Content-Transfer-Encoding" in part:
                    del part["Content-Transfer-Encoding"]
            else:
                # Attachments: emptied, consistent with the
                # article_data_mime_attachment scrub path (content -> b""/"").
                part.set_payload("")
                if "Content-Transfer-Encoding" in part:
                    del part["Content-Transfer-Encoding"]

    def anonymize_mime_message(self, raw: str | bytes | None) -> str | bytes | None:
        """Structure-preserving anonymization of a raw MIME message.

        Parses *raw* (``article_data_mime_plain.body`` — bytes on MariaDB,
        str on the Postgres test fixture) as an RFC 5322 message: headers are
        kept, but address/subject/identifier headers are anonymized in
        place; text body parts are lorem-scrubbed; non-text parts
        (attachments) are emptied. Falls back to the previous whole-text
        line-scrub (:meth:`anonymize_body`) if *raw* doesn't parse as a
        message with any headers (e.g. a corrupt/partial mail), so the
        anonymizer never raises on bad input.
        """
        if not raw:
            return raw
        if isinstance(raw, (bytes, memoryview)):
            is_bytes = True
            raw_bytes: bytes | None = bytes(raw)
        else:
            is_bytes = False
            raw_bytes = None
        try:
            msg: Message = (
                BytesParser(policy=compat32).parsebytes(raw_bytes)
                if raw_bytes is not None
                else Parser(policy=compat32).parsestr(str(raw))
            )
            if not msg.items():
                raise MessageError("no headers parsed — not a MIME message")
        except Exception:
            if is_bytes:
                text_in = raw_bytes.decode("utf-8", errors="replace") if raw_bytes else ""
                scrubbed = self.anonymize_body(text_in) or ""
                return scrubbed.encode("utf-8")
            return self.anonymize_body(str(raw))

        self._scrub_mime_headers(msg)
        self._scrub_mime_body_parts(msg)
        return msg.as_bytes() if is_bytes else msg.as_string()


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
                ).order_by(CustomerUser.id)
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
            await session.execute(
                select(CustomerCompany.customer_id, CustomerCompany.name).order_by(
                    CustomerCompany.customer_id
                )
            )
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
