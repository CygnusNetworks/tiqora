"""GDPR/DSGVO customer erasure: anonymize (default) or hard-delete with backup.

Destructive operations run only after admin auth + explicit ``confirm=true``
(API layer) and :func:`tiqora.gdpr.gate.require_write_gate` (engine). Safe
default is anonymize-in-place + ``valid_id`` invalidation. Hard-delete removes
only customer *master* rows (never tickets/articles) and tokenizes ticket
references. Every mutation is snapshotted into ``tiqora_gdpr_backup`` first
for a 30-day rollback window.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.legacy.article import (
    Article,
    ArticleDataMime,
    ArticleDataMimeAttachment,
    ArticleDataMimePlain,
    ArticleSearchIndex,
)
from tiqora.db.legacy.customer import (
    CustomerCompany,
    CustomerPreferences,
    CustomerUser,
    CustomerUserCustomer,
)
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.ticket import Ticket, TicketHistory, TicketState, TicketStateType
from tiqora.db.legacy.user import GroupCustomerUser
from tiqora.db.tiqora.models import TiqoraGdprBackup, TiqoraGdprJob
from tiqora.domain.dev_anonymize import ValueMapper
from tiqora.domain.settings_store import get_setting
from tiqora.gdpr.audit import record_audit
from tiqora.gdpr.gate import require_write_gate
from tiqora.znuny.cache_invalidation import invalidate_cache_type, invalidate_ticket_cache

# Znuny CacheType sets (mirrored from admin/common — avoid gdpr→api layering).
_CUSTOMER_USER_CACHE_TYPES: tuple[str, ...] = (
    "CustomerUser",
    "CustomerUser_CustomerIDList",
    "CustomerUser_CustomerSearch",
    "CustomerUser_CustomerSearchDetail",
    "CustomerUser_CustomerSearchDetailDynamicFields",
    "CustomerGroup",
)
_CUSTOMER_COMPANY_CACHE_TYPES: tuple[str, ...] = (
    "CustomerCompany",
    "CustomerCompany_CustomerCompanyList",
    "CustomerCompany_CustomerCompanySearchDetail",
    "CustomerCompany_CustomerSearchDetailDynamicFields",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEY_GDPR_CUSTOMER_EXTRA_PII = "gdpr.customer_extra_pii_columns"
KEY_GDPR_ERASURE_PURGE_ENABLED = "gdpr.erasure.purge_enabled"

BACKUP_RETENTION_DAYS = 30
INVALID_VALID_ID = 2
# Placeholder written into free-text columns (ticket.title, ticket_history.name,
# dynamic_field_value.value_text) when anonymizing — rollback-able via backup.
_ANON_PLACEHOLDER = "[anonymisiert]"

# Guard admin-supplied selector regexes against ReDoS (L-1). Patterns longer
# than this are rejected before compile; nested quantifiers that commonly
# cause catastrophic backtracking are also refused. Matching still runs in
# process (candidates are already bounded by SQL filters).
_MAX_SELECTOR_REGEX_LEN = 200
# Nested/adjacent quantifiers on the same atom — e.g. (a+)+, (a*)*, (a+){2,}.
_CATASTROPHIC_REGEX = re.compile(
    r"\((?:[^()\\]|\\.)*[+*{](?:[^()\\]|\\.)*\)[+*{]"
    r"|(?:[*+?])[+*?{]"  # a++ / a+* / a+{ — possessive-ish stacking
)

ErasureMode = Literal["anonymize", "delete"]
JobStatus = Literal["applied", "rolled_back", "purged"]

# Columns never treated as PII / never overwritten by anonymize.
_CUSTOMER_USER_SKIP = frozenset(
    {
        "id",
        "customer_id",
        "valid_id",
        "create_time",
        "create_by",
        "change_time",
        "change_by",
    }
)

# Vanilla Znuny customer_user PII (plus title/comments/pw).
_CUSTOMER_USER_VANILLA_PII = frozenset(
    {
        "login",
        "email",
        "pw",
        "title",
        "first_name",
        "last_name",
        "phone",
        "fax",
        "mobile",
        "street",
        "zip",
        "city",
        "country",
        "comments",
    }
)

_CUSTOMER_COMPANY_PII = frozenset({"name", "street", "zip", "city", "country", "url", "comments"})

_MIME_PII_COLS = (
    "a_from",
    "a_to",
    "a_cc",
    "a_bcc",
    "a_reply_to",
    "a_subject",
    "a_body",
    "a_message_id",
    "a_message_id_md5",
    "a_in_reply_to",
    "a_references",
)

_KIND_FOR_COL: dict[str, str] = {
    "login": "login",
    "email": "email",
    "first_name": "first_name",
    "last_name": "last_name",
    "phone": "phone",
    "fax": "phone",
    "mobile": "phone",
    "street": "street",
    "zip": "zip",
    "city": "city",
    "country": "country",
    "title": "first_name",
    "comments": "body",
    "name": "company",
    "url": "street",
    "pw": "login",
}


class ErasureError(ValueError):
    """Raised for invalid selector / job state / confirm failures."""


class ErasureNotFoundError(LookupError):
    """Raised when a job id is missing."""


# ---------------------------------------------------------------------------
# Selector + preview dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ErasureSelector:
    """Combinable AND filter for customer_user resolution."""

    logins: list[str] = field(default_factory=list)
    customer_ids: list[str] = field(default_factory=list)
    login_regex: str | None = None
    customer_id_regex: str | None = None
    changed_before: datetime | None = None
    changed_after: datetime | None = None
    # "no_tickets" | "no_open_tickets" | "inactive_since:YYYY-MM-DD"
    activity: str | None = None
    valid_id: int | None = None

    def to_json(self) -> str:
        def _ser(v: Any) -> Any:
            if isinstance(v, datetime):
                return v.isoformat()
            return v

        return json.dumps({k: _ser(v) for k, v in asdict(self).items()}, sort_keys=True)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> ErasureSelector:
        if not data:
            return cls()
        cb = data.get("changed_before")
        ca = data.get("changed_after")
        return cls(
            logins=list(data.get("logins") or []),
            customer_ids=list(data.get("customer_ids") or []),
            login_regex=data.get("login_regex"),
            customer_id_regex=data.get("customer_id_regex"),
            changed_before=_parse_dt(cb) if cb else None,
            changed_after=_parse_dt(ca) if ca else None,
            activity=data.get("activity"),
            valid_id=data.get("valid_id"),
        )


@dataclass
class ResolvedCustomer:
    id: int
    login: str
    email: str
    customer_id: str


@dataclass
class SampleRow:
    table: str
    id: Any
    summary: str


@dataclass
class ErasurePreview:
    mode: ErasureMode
    customers: list[ResolvedCustomer]
    counts: dict[str, int]
    sample: list[SampleRow]
    columns_changed: dict[str, list[str]]
    tables_deleted: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "customers": [asdict(c) for c in self.customers],
            "counts": self.counts,
            "sample": [asdict(s) for s in self.sample],
            "columns_changed": self.columns_changed,
            "tables_deleted": self.tables_deleted,
        }


@dataclass
class ErasureResult:
    job_id: int
    mode: ErasureMode
    counts: dict[str, int]
    resolved_logins: list[str]


# ---------------------------------------------------------------------------
# JSON helpers (datetime / bytes)
# ---------------------------------------------------------------------------


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        raw = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    raise ErasureError(f"invalid datetime: {value!r}")


def _json_dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=_json_default)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return {"__dt__": obj.isoformat()}
    if isinstance(obj, bytes):
        return {"__bytes__": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, memoryview):
        return {"__bytes__": base64.b64encode(obj.tobytes()).decode("ascii")}
    raise TypeError(f"not JSON serializable: {type(obj)!r}")


def _json_load_row(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    return {k: _json_revive(v) for k, v in data.items()}


def _json_revive(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value.keys()) == {"__dt__"}:
            return _parse_dt(value["__dt__"])
        if set(value.keys()) == {"__bytes__"}:
            return base64.b64decode(value["__bytes__"])
    return value


def _row_to_dict(mapping: Any) -> dict[str, Any]:
    """Convert a SQLAlchemy RowMapping / ORM-ish mapping to a plain dict."""
    if hasattr(mapping, "_mapping"):
        return dict(mapping._mapping)
    if isinstance(mapping, dict):
        return dict(mapping)
    return dict(mapping)


# ---------------------------------------------------------------------------
# Column introspection
# ---------------------------------------------------------------------------


async def list_customer_user_columns(session: AsyncSession) -> list[str]:
    """All ``customer_user`` column names via information_schema (both dialects)."""
    result = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = :t"
            " ORDER BY ordinal_position"
        ),
        {"t": "customer_user"},
    )
    # MariaDB may return mixed case; normalize to lowercase for Znuny tables.
    return [str(row[0]).lower() for row in result.all()]


async def resolve_customer_user_pii_columns(session: AsyncSession) -> list[str]:
    """PII columns on ``customer_user``: vanilla + extras + settings list."""
    cols = set(await list_customer_user_columns(session))
    pii = set(_CUSTOMER_USER_VANILLA_PII) & cols
    # Site-specific string columns (e.g. wpnum) present on the live table.
    for col in cols:
        if col in _CUSTOMER_USER_SKIP or col in pii:
            continue
        # Treat unknown non-skip columns as PII when they look string-like
        # (we only overwrite if the row has a non-null string value later).
        if col not in ("id",):
            pii.add(col)
    raw = await get_setting(session, KEY_GDPR_CUSTOMER_EXTRA_PII)
    if raw:
        try:
            extra = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ErasureError(f"{KEY_GDPR_CUSTOMER_EXTRA_PII} is not valid JSON: {exc}") from exc
        if not isinstance(extra, list):
            raise ErasureError(f"{KEY_GDPR_CUSTOMER_EXTRA_PII} must be a JSON list")
        for item in extra:
            name = str(item).lower()
            if name in cols and name not in _CUSTOMER_USER_SKIP:
                pii.add(name)
    # Stable order: vanilla first, then extras alphabetically.
    vanilla_ordered = [c for c in sorted(_CUSTOMER_USER_VANILLA_PII) if c in pii]
    extras = sorted(c for c in pii if c not in _CUSTOMER_USER_VANILLA_PII)
    return vanilla_ordered + extras


def _kind_for(column: str) -> str:
    return _KIND_FOR_COL.get(column, column)


# ---------------------------------------------------------------------------
# Selector resolution
# ---------------------------------------------------------------------------


def _has_any_selector(selector: ErasureSelector) -> bool:
    return bool(
        selector.logins
        or selector.customer_ids
        or selector.login_regex
        or selector.customer_id_regex
        or selector.changed_before is not None
        or selector.changed_after is not None
        or selector.activity
        or selector.valid_id is not None
    )


def _naive_change_time(dt: datetime) -> datetime:
    """Normalize a pydantic/API datetime to naive wall-clock for Znuny change_time.

    ``customer_user.change_time`` is a naive ``LegacyDateTime`` column. API-bound
    values may be tz-aware; convert to local wall-clock then drop tzinfo.
    """
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _compile_selector_regex(pattern: str, *, field: str) -> re.Pattern[str]:
    """Compile an admin-supplied selector regex with ReDoS guards.

    Rejects over-long patterns and common catastrophic-backtracking shapes
    with a clean :class:`ErasureError` (API 4xx path) instead of hanging the
    worker on ``re.search``.
    """
    if len(pattern) > _MAX_SELECTOR_REGEX_LEN:
        raise ErasureError(
            f"{field} exceeds maximum length of {_MAX_SELECTOR_REGEX_LEN} characters"
        )
    if _CATASTROPHIC_REGEX.search(pattern):
        raise ErasureError(
            f"{field} rejected: nested or stacked quantifiers are not allowed (ReDoS guard)"
        )
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ErasureError(f"invalid {field}: {exc}") from exc


async def resolve_selector(session: AsyncSession, selector: ErasureSelector) -> list[int]:
    """Resolve *selector* to ``customer_user.id`` list (AND of all criteria).

    Returns an empty list when no criteria are provided (refuse to match all).
    """
    if not _has_any_selector(selector):
        return []

    # Compile regexes first so invalid/catastrophic patterns fail fast with
    # ErasureError before we load candidate rows.
    login_cre = (
        _compile_selector_regex(selector.login_regex, field="login_regex")
        if selector.login_regex
        else None
    )
    customer_id_cre = (
        _compile_selector_regex(selector.customer_id_regex, field="customer_id_regex")
        if selector.customer_id_regex
        else None
    )

    # Candidate set: start broad, narrow with SQL where possible.
    # Regex-only selectors load the whole customer_user (id, login, customer_id)
    # into memory — tens of thousands of small tuples is fine for admin preview.
    stmt = select(CustomerUser.id, CustomerUser.login, CustomerUser.customer_id)
    if selector.logins:
        stmt = stmt.where(CustomerUser.login.in_(list(selector.logins)))
    if selector.customer_ids:
        stmt = stmt.where(CustomerUser.customer_id.in_(list(selector.customer_ids)))
    if selector.changed_before is not None:
        # Normalize tz-aware bounds so they compare cleanly with naive change_time.
        stmt = stmt.where(CustomerUser.change_time < _naive_change_time(selector.changed_before))
    if selector.changed_after is not None:
        stmt = stmt.where(CustomerUser.change_time > _naive_change_time(selector.changed_after))
    if selector.valid_id is not None:
        stmt = stmt.where(CustomerUser.valid_id == selector.valid_id)

    rows = (await session.execute(stmt)).all()
    candidates: list[tuple[int, str, str]] = [
        (int(r.id), str(r.login), str(r.customer_id)) for r in rows
    ]
    if not candidates:
        return []

    # Apply regex in Python (candidates already loaded). Native SQL REGEXP/~ does
    # not understand Python patterns like \\d, \\w, (?i) and would return empty.
    if login_cre is not None:
        candidates = [c for c in candidates if login_cre.search(c[1])]

    if customer_id_cre is not None:
        candidates = [c for c in candidates if customer_id_cre.search(c[2])]

    if selector.activity:
        candidates = await _filter_activity(session, candidates, selector.activity)

    return [c[0] for c in candidates]


async def _filter_activity(
    session: AsyncSession,
    candidates: list[tuple[int, str, str]],
    activity: str,
) -> list[tuple[int, str, str]]:
    if activity == "no_tickets":
        out: list[tuple[int, str, str]] = []
        for cid, login, company in candidates:
            exists = (
                await session.execute(
                    select(Ticket.id).where(Ticket.customer_user_id == login).limit(1)
                )
            ).scalar_one_or_none()
            if exists is None:
                out.append((cid, login, company))
        return out

    if activity == "no_open_tickets":
        out = []
        for cid, login, company in candidates:
            exists = (
                await session.execute(
                    select(Ticket.id)
                    .join(TicketState, TicketState.id == Ticket.ticket_state_id)
                    .join(TicketStateType, TicketStateType.id == TicketState.type_id)
                    .where(
                        Ticket.customer_user_id == login,
                        TicketStateType.name == "open",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if exists is None:
                out.append((cid, login, company))
        return out

    if activity.startswith("inactive_since:"):
        raw = activity.split(":", 1)[1].strip()
        try:
            since = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError as exc:
            raise ErasureError(f"activity inactive_since expects YYYY-MM-DD, got {raw!r}") from exc
        out = []
        for cid, login, company in candidates:
            recent = (
                await session.execute(
                    select(Ticket.id)
                    .where(
                        Ticket.customer_user_id == login,
                        Ticket.change_time >= since,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if recent is None:
                out.append((cid, login, company))
        return out

    raise ErasureError(
        f"unknown activity filter {activity!r}; expected no_tickets, "
        "no_open_tickets, or inactive_since:YYYY-MM-DD"
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


async def build_erasure_preview(
    session: AsyncSession,
    selector: ErasureSelector,
    mode: ErasureMode = "anonymize",
) -> ErasurePreview:
    """Resolve selector and return exact customers + counts/samples of impact."""
    if mode not in ("anonymize", "delete"):
        raise ErasureError(f"mode must be 'anonymize' or 'delete', got {mode!r}")

    ids = await resolve_selector(session, selector)
    return await build_erasure_preview_for_ids(session, ids, mode)


async def build_erasure_preview_for_ids(
    session: AsyncSession,
    customer_user_ids: list[int],
    mode: ErasureMode = "anonymize",
) -> ErasurePreview:
    pii_cols = await resolve_customer_user_pii_columns(session)
    customers: list[ResolvedCustomer] = []
    if customer_user_ids:
        rows = (
            await session.execute(
                select(
                    CustomerUser.id,
                    CustomerUser.login,
                    CustomerUser.email,
                    CustomerUser.customer_id,
                ).where(CustomerUser.id.in_(customer_user_ids))
            )
        ).all()
        by_id = {int(r.id): r for r in rows}
        for i in customer_user_ids:
            r = by_id.get(i)
            if r is None:
                continue
            customers.append(
                ResolvedCustomer(
                    id=int(r.id),
                    login=str(r.login),
                    email=str(r.email),
                    customer_id=str(r.customer_id),
                )
            )

    logins = [c.login for c in customers]
    company_ids = sorted({c.customer_id for c in customers if c.customer_id})

    ticket_ids: list[int] = []
    if logins:
        ticket_ids = list(
            (await session.execute(select(Ticket.id).where(Ticket.customer_user_id.in_(logins))))
            .scalars()
            .all()
        )

    article_ids: list[int] = []
    if ticket_ids:
        article_ids = list(
            (await session.execute(select(Article.id).where(Article.ticket_id.in_(ticket_ids))))
            .scalars()
            .all()
        )

    mime_count = 0
    plain_count = 0
    attach_count = 0
    search_count = 0
    if article_ids:
        mime_ids = (
            (
                await session.execute(
                    select(ArticleDataMime.id).where(ArticleDataMime.article_id.in_(article_ids))
                )
            )
            .scalars()
            .all()
        )
        mime_count = len(list(mime_ids))
        plain_count = len(
            list(
                (
                    await session.execute(
                        select(ArticleDataMimePlain.id).where(
                            ArticleDataMimePlain.article_id.in_(article_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
        )
        attach_count = len(
            list(
                (
                    await session.execute(
                        select(ArticleDataMimeAttachment.id).where(
                            ArticleDataMimeAttachment.article_id.in_(article_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
        )
        search_count = len(
            list(
                (
                    await session.execute(
                        select(ArticleSearchIndex.id).where(
                            ArticleSearchIndex.article_id.in_(article_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
        )

    company_count = 0
    if company_ids:
        company_count = len(
            list(
                (
                    await session.execute(
                        select(CustomerCompany.customer_id).where(
                            CustomerCompany.customer_id.in_(company_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
        )

    # Master-side related rows (delete only).
    pref_count = 0
    cuc_count = 0
    gcu_count = 0
    if logins:
        pref_count = len(
            list(
                (
                    await session.execute(
                        select(CustomerPreferences.user_id).where(
                            CustomerPreferences.user_id.in_(logins)
                        )
                    )
                )
                .scalars()
                .all()
            )
        )
        cuc_count = len(
            (
                await session.execute(
                    select(CustomerUserCustomer).where(CustomerUserCustomer.user_id.in_(logins))
                )
            )
            .scalars()
            .all()
        )
        gcu_count = len(
            (
                await session.execute(
                    select(GroupCustomerUser).where(GroupCustomerUser.user_id.in_(logins))
                )
            )
            .scalars()
            .all()
        )

    counts: dict[str, int] = {
        "customer_user": len(customers),
        "customer_company": company_count,
        "tickets": len(ticket_ids),
        "articles": len(article_ids),
        "article_data_mime": mime_count,
        "article_data_mime_plain": plain_count,
        "article_data_mime_attachment": attach_count,
        "article_search_index": search_count,
        "customer_preferences": pref_count,
        "customer_user_customer": cuc_count,
        "group_customer_user": gcu_count,
    }

    sample: list[SampleRow] = []
    for c in customers[:10]:
        sample.append(
            SampleRow(
                table="customer_user",
                id=c.id,
                summary=f"{c.login} <{c.email}> company={c.customer_id}",
            )
        )
    for tid in ticket_ids[:5]:
        sample.append(SampleRow(table="ticket", id=tid, summary=f"ticket_id={tid}"))
    for aid in article_ids[:5]:
        sample.append(SampleRow(table="article", id=aid, summary=f"article_id={aid}"))

    columns_changed: dict[str, list[str]] = {
        "customer_user": sorted(set(pii_cols) | {"valid_id"}),
        "customer_company": sorted(_CUSTOMER_COMPANY_PII),
        "ticket": ["customer_user_id", "customer_id", "title"],
        "ticket_history": ["name"],
        "dynamic_field_value": ["value_text"],
        "article_data_mime": list(_MIME_PII_COLS),
        "article_data_mime_plain": ["body"],
        "article_data_mime_attachment": ["filename", "content"],
        "article_search_index": ["article_value"],
    }
    tables_deleted: list[str] = []
    if mode == "delete":
        tables_deleted = [
            "customer_user",
            "customer_user_customer",
            "customer_preferences",
            "group_customer_user",
            "customer_company",  # only when no remaining users
        ]

    return ErasurePreview(
        mode=mode,
        customers=customers,
        counts=counts,
        sample=sample,
        columns_changed=columns_changed,
        tables_deleted=tables_deleted,
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


async def run_erasure(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    customer_user_ids: list[int],
    mode: ErasureMode = "anonymize",
    seed: int | None = None,
    actor: str = "admin",
    force_parallel: bool = True,
    selector: ErasureSelector | None = None,
) -> ErasureResult:
    """Snapshot + apply erasure on an explicit confirmed id list (not a re-resolve)."""
    if mode not in ("anonymize", "delete"):
        raise ErasureError(f"mode must be 'anonymize' or 'delete', got {mode!r}")
    if not customer_user_ids:
        raise ErasureError("customer_user_ids must not be empty")

    async with session_factory() as session:
        await require_write_gate(
            session,
            settings,
            force_parallel=force_parallel,
            operation=f"gdpr_erasure_{mode}",
        )

    mapper = ValueMapper(seed=seed)
    selector = selector or ErasureSelector()
    now = datetime.now(UTC).replace(tzinfo=None)
    expires = now + timedelta(days=BACKUP_RETENTION_DAYS)
    counts: dict[str, int] = {
        "customer_user": 0,
        "customer_company": 0,
        "tickets": 0,
        "article_data_mime": 0,
        "article_data_mime_plain": 0,
        "article_data_mime_attachment": 0,
        "article_search_index": 0,
        "customer_preferences": 0,
        "customer_user_customer": 0,
        "group_customer_user": 0,
        "customer_company_deleted": 0,
        "ticket_history": 0,
        "dynamic_field_value": 0,
    }

    async with session_factory() as session, session.begin():
        pii_cols = await resolve_customer_user_pii_columns(session)
        customers = (
            (
                await session.execute(
                    select(CustomerUser).where(CustomerUser.id.in_(customer_user_ids))
                )
            )
            .scalars()
            .all()
        )
        if not customers:
            raise ErasureError("no customer_user rows match the given ids")

        # Preserve requested order where possible.
        by_id = {int(c.id): c for c in customers}
        ordered = [by_id[i] for i in customer_user_ids if i in by_id]
        logins = [c.login for c in ordered]
        login_to_new: dict[str, str] = {}
        company_to_new: dict[str, str] = {}

        job = TiqoraGdprJob(
            mode=mode,
            selector=selector.to_json(),
            resolved_logins=_json_dump(logins),
            status="applied",
            counts="{}",
            seed=seed,
            actor=actor,
            force_parallel=force_parallel,
            created=now,
            applied_at=now,
            backup_expires_at=expires,
        )
        session.add(job)
        await session.flush()
        job_id = int(job.id)

        # ---- Preload tickets / articles for these logins ----
        ticket_rows = (
            await session.execute(
                select(Ticket.id, Ticket.customer_user_id, Ticket.customer_id, Ticket.title).where(
                    Ticket.customer_user_id.in_(logins)
                )
            )
        ).all()
        ticket_ids = [int(r.id) for r in ticket_rows]

        article_ids: list[int] = []
        if ticket_ids:
            article_ids = list(
                (await session.execute(select(Article.id).where(Article.ticket_id.in_(ticket_ids))))
                .scalars()
                .all()
            )

        # ---- customer_user: snapshot + anonymize or prepare delete ----
        for cust in ordered:
            full = await _fetch_full_row(session, "customer_user", {"id": int(cust.id)})
            if full is None:
                continue
            old_login = str(full["login"])
            old_company = str(full["customer_id"])
            new_login = mapper.map_value(old_login, "login") or f"gdpr-user-{cust.id}"
            login_to_new[old_login] = new_login
            if old_company not in company_to_new:
                company_to_new[old_company] = (
                    mapper.map_value(old_company, "login") or f"gdpr-co-{old_company[:8]}"
                )

            if mode == "anonymize":
                changed: dict[str, Any] = {}
                updates: dict[str, Any] = {}
                for col in pii_cols:
                    if col not in full:
                        continue
                    orig = full[col]
                    if col == "login":
                        new_val: Any = new_login
                    elif col == "pw":
                        new_val = None
                    else:
                        new_val = mapper.map_value(
                            str(orig) if orig is not None else None, _kind_for(col)
                        )
                    if orig != new_val:
                        changed[col] = orig
                        updates[col] = new_val
                # Always invalidate.
                if full.get("valid_id") != INVALID_VALID_ID:
                    changed["valid_id"] = full.get("valid_id")
                    updates["valid_id"] = INVALID_VALID_ID
                changed["change_time"] = full.get("change_time")
                updates["change_time"] = now
                await _add_backup(
                    session,
                    job_id=job_id,
                    table_name="customer_user",
                    row_pk={"id": int(cust.id)},
                    original_row=changed,
                    created=now,
                )
                if updates:
                    await _update_row(session, "customer_user", {"id": int(cust.id)}, updates)
                counts["customer_user"] += 1
            else:
                # delete: full-row backup; delete later after related masters.
                await _add_backup(
                    session,
                    job_id=job_id,
                    table_name="customer_user",
                    row_pk={"id": int(cust.id)},
                    original_row=full,
                    created=now,
                )
                counts["customer_user"] += 1

        # ---- related master tables (snapshot; delete mode removes) ----
        for old_login in logins:
            # customer_preferences (composite PK user_id + preferences_key)
            prefs = (
                (
                    await session.execute(
                        text(
                            "SELECT user_id, preferences_key, preferences_value"
                            " FROM customer_preferences WHERE user_id = :u"
                        ),
                        {"u": old_login},
                    )
                )
                .mappings()
                .all()
            )
            for pref in prefs:
                full_pref = dict(pref)
                await _add_backup(
                    session,
                    job_id=job_id,
                    table_name="customer_preferences",
                    row_pk={
                        "user_id": full_pref["user_id"],
                        "preferences_key": full_pref["preferences_key"],
                    },
                    original_row=(
                        full_pref if mode == "delete" else {"user_id": full_pref["user_id"]}
                    ),
                    created=now,
                )
                if mode == "anonymize":
                    new_u = login_to_new[old_login]
                    await session.execute(
                        text(
                            "UPDATE customer_preferences SET user_id = :new_u"
                            " WHERE user_id = :old_u AND preferences_key = :k"
                        ),
                        {
                            "new_u": new_u,
                            "old_u": old_login,
                            "k": full_pref["preferences_key"],
                        },
                    )
                counts["customer_preferences"] += 1

            cucs = (
                (
                    await session.execute(
                        text(
                            "SELECT user_id, customer_id, create_time, create_by,"
                            " change_time, change_by FROM customer_user_customer"
                            " WHERE user_id = :u"
                        ),
                        {"u": old_login},
                    )
                )
                .mappings()
                .all()
            )
            for cuc in cucs:
                full_cuc = dict(cuc)
                await _add_backup(
                    session,
                    job_id=job_id,
                    table_name="customer_user_customer",
                    row_pk={
                        "user_id": full_cuc["user_id"],
                        "customer_id": full_cuc["customer_id"],
                    },
                    original_row=full_cuc if mode == "delete" else {"user_id": full_cuc["user_id"]},
                    created=now,
                )
                if mode == "anonymize":
                    await session.execute(
                        text(
                            "UPDATE customer_user_customer SET user_id = :new_u,"
                            " change_time = :ct WHERE user_id = :old_u AND customer_id = :cid"
                        ),
                        {
                            "new_u": login_to_new[old_login],
                            "old_u": old_login,
                            "cid": full_cuc["customer_id"],
                            "ct": now,
                        },
                    )
                counts["customer_user_customer"] += 1

            gcus = (
                (
                    await session.execute(
                        text(
                            "SELECT user_id, group_id, permission_key, permission_value,"
                            " create_time, create_by, change_time, change_by"
                            " FROM group_customer_user WHERE user_id = :u"
                        ),
                        {"u": old_login},
                    )
                )
                .mappings()
                .all()
            )
            for gcu in gcus:
                full_gcu = dict(gcu)
                await _add_backup(
                    session,
                    job_id=job_id,
                    table_name="group_customer_user",
                    row_pk={
                        "user_id": full_gcu["user_id"],
                        "group_id": full_gcu["group_id"],
                        "permission_key": full_gcu["permission_key"],
                    },
                    original_row=full_gcu if mode == "delete" else {"user_id": full_gcu["user_id"]},
                    created=now,
                )
                if mode == "anonymize":
                    await session.execute(
                        text(
                            "UPDATE group_customer_user SET user_id = :new_u,"
                            " change_time = :ct"
                            " WHERE user_id = :old_u AND group_id = :gid"
                            " AND permission_key = :pk"
                        ),
                        {
                            "new_u": login_to_new[old_login],
                            "old_u": old_login,
                            "gid": full_gcu["group_id"],
                            "pk": full_gcu["permission_key"],
                            "ct": now,
                        },
                    )
                counts["group_customer_user"] += 1

        # ---- tickets: rewrite refs ----
        # anonymize: customer_user_id follows the new login; customer_id stays
        # (company PK is kept). delete: both become stable tokens (masters gone).
        for trow in ticket_rows:
            tid = int(trow.id)
            old_cu = trow.customer_user_id
            old_cid = trow.customer_id
            changed_t: dict[str, Any] = {}
            updates_t: dict[str, Any] = {}
            if mode == "anonymize":
                new_cu = login_to_new.get(str(old_cu), str(old_cu)) if old_cu else old_cu
                if old_cu != new_cu:
                    changed_t["customer_user_id"] = old_cu
                    updates_t["customer_user_id"] = new_cu
                # ticket.title routinely carries the mail subject / customer name.
                old_title = trow.title
                if old_title:
                    changed_t["title"] = old_title
                    updates_t["title"] = _ANON_PLACEHOLDER
            else:
                if old_cu:
                    new_cu = login_to_new.get(str(old_cu)) or mapper.map_value(str(old_cu), "login")
                    if old_cu != new_cu:
                        changed_t["customer_user_id"] = old_cu
                        updates_t["customer_user_id"] = new_cu
                if old_cid:
                    new_cid = company_to_new.get(str(old_cid)) or mapper.map_value(
                        str(old_cid), "login"
                    )
                    if old_cid != new_cid:
                        changed_t["customer_id"] = old_cid
                        updates_t["customer_id"] = new_cid
            if updates_t:
                await _add_backup(
                    session,
                    job_id=job_id,
                    table_name="ticket",
                    row_pk={"id": tid},
                    original_row=changed_t,
                    created=now,
                )
                await _update_row(session, "ticket", {"id": tid}, updates_t)
                counts["tickets"] += 1

        # ---- ticket_history + dynamic_field_value: scrub free-text PII ----
        # Only on anonymize (delete removes these rows outright, batch B). The
        # customer's whole ticket is being anonymized, so scrub the history text
        # (subject/name snippets) and any custom-field values wholesale.
        if mode == "anonymize" and ticket_ids:
            hist_rows = (
                await session.execute(
                    select(TicketHistory.id, TicketHistory.name).where(
                        TicketHistory.ticket_id.in_(ticket_ids)
                    )
                )
            ).all()
            for hid, hname in hist_rows:
                if not hname:
                    continue
                await _add_backup(
                    session,
                    job_id=job_id,
                    table_name="ticket_history",
                    row_pk={"id": int(hid)},
                    original_row={"name": hname},
                    created=now,
                )
                await _update_row(
                    session, "ticket_history", {"id": int(hid)}, {"name": _ANON_PLACEHOLDER}
                )
                counts["ticket_history"] += 1

            # Dynamic-field values: scrub value_text for Ticket-object fields on
            # these tickets and Article-object fields on these articles.
            field_types: dict[int, str] = {
                int(fid): str(ot)
                for fid, ot in (
                    await session.execute(select(DynamicField.id, DynamicField.object_type))
                ).all()
            }
            for obj_ids, wanted in (
                (ticket_ids, "Ticket"),
                (article_ids, "Article"),
            ):
                field_ids = [fid for fid, ot in field_types.items() if ot == wanted]
                if not obj_ids or not field_ids:
                    continue
                dfv_rows = (
                    await session.execute(
                        select(DynamicFieldValue.id, DynamicFieldValue.value_text).where(
                            DynamicFieldValue.object_id.in_(obj_ids),
                            DynamicFieldValue.field_id.in_(field_ids),
                        )
                    )
                ).all()
                for did, val in dfv_rows:
                    if not val:
                        continue
                    await _add_backup(
                        session,
                        job_id=job_id,
                        table_name="dynamic_field_value",
                        row_pk={"id": int(did)},
                        original_row={"value_text": val},
                        created=now,
                    )
                    await _update_row(
                        session,
                        "dynamic_field_value",
                        {"id": int(did)},
                        {"value_text": _ANON_PLACEHOLDER},
                    )
                    counts["dynamic_field_value"] += 1

        # ---- articles: scrub mime / plain / attachment / search_index ----
        if article_ids:
            mime_rows = (
                (
                    await session.execute(
                        select(ArticleDataMime).where(ArticleDataMime.article_id.in_(article_ids))
                    )
                )
                .scalars()
                .all()
            )
            for mime in mime_rows:
                changed_m: dict[str, Any] = {}
                updates_m: dict[str, Any] = {}
                for col in _MIME_PII_COLS:
                    orig = getattr(mime, col, None)
                    if col in ("a_from", "a_to", "a_cc", "a_bcc", "a_reply_to"):
                        new_val = mapper.anonymize_address_field(orig)
                    elif col == "a_body":
                        new_val = mapper.anonymize_body(orig)
                    elif col == "a_subject":
                        new_val = mapper.anonymize_body(orig) if orig else orig
                    elif col in ("a_message_id", "a_in_reply_to", "a_references"):
                        new_val = mapper.map_value(str(orig), "login") if orig else orig
                    elif col == "a_message_id_md5":
                        mapped = mapper.map_value(str(orig), "login") if orig else None
                        new_val = mapped[:32] if mapped else orig
                    else:
                        new_val = mapper.map_value(str(orig), "login") if orig else orig
                    if orig != new_val:
                        changed_m[col] = orig
                        updates_m[col] = new_val
                if updates_m:
                    await _add_backup(
                        session,
                        job_id=job_id,
                        table_name="article_data_mime",
                        row_pk={"id": int(mime.id)},
                        original_row=changed_m,
                        created=now,
                    )
                    await _update_row(session, "article_data_mime", {"id": int(mime.id)}, updates_m)
                    counts["article_data_mime"] += 1

            plain_rows = (
                (
                    await session.execute(
                        select(ArticleDataMimePlain).where(
                            ArticleDataMimePlain.article_id.in_(article_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for plain in plain_rows:
                orig_body = plain.body
                # MariaDB: LONGBLOB (bytes). PG fixture: TEXT (str).
                if isinstance(orig_body, (bytes, memoryview)):
                    text_body = (
                        bytes(orig_body).decode("utf-8", errors="replace") if orig_body else ""
                    )
                    store_as_bytes = True
                else:
                    text_body = str(orig_body or "")
                    store_as_bytes = False
                new_text = mapper.anonymize_body(text_body) or ""
                new_body: Any = new_text.encode("utf-8") if store_as_bytes else new_text
                if isinstance(orig_body, (bytes, memoryview)):
                    backup_body: Any = bytes(orig_body)
                else:
                    backup_body = orig_body if orig_body is not None else ""
                await _add_backup(
                    session,
                    job_id=job_id,
                    table_name="article_data_mime_plain",
                    row_pk={"id": int(plain.id)},
                    original_row={"body": backup_body},
                    created=now,
                )
                await session.execute(
                    text("UPDATE article_data_mime_plain SET body = :body WHERE id = :id"),
                    {"body": new_body, "id": int(plain.id)},
                )
                counts["article_data_mime_plain"] += 1

            attach_rows = (
                (
                    await session.execute(
                        select(ArticleDataMimeAttachment).where(
                            ArticleDataMimeAttachment.article_id.in_(article_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for att in attach_rows:
                changed_a: dict[str, Any] = {}
                updates_a: dict[str, Any] = {}
                if att.filename:
                    new_fn = mapper.map_value(att.filename, "login") or "redacted.bin"
                    changed_a["filename"] = att.filename
                    updates_a["filename"] = new_fn
                if att.content is not None:
                    raw = att.content
                    if isinstance(raw, (bytes, memoryview)):
                        changed_a["content"] = bytes(raw)
                        updates_a["content"] = b""
                    else:
                        # PG fixture may map BLOB→TEXT.
                        changed_a["content"] = raw
                        updates_a["content"] = ""
                    updates_a["content_size"] = "0"
                    if att.content_size is not None:
                        changed_a["content_size"] = att.content_size
                if updates_a:
                    await _add_backup(
                        session,
                        job_id=job_id,
                        table_name="article_data_mime_attachment",
                        row_pk={"id": int(att.id)},
                        original_row=changed_a,
                        created=now,
                    )
                    await _update_row(
                        session,
                        "article_data_mime_attachment",
                        {"id": int(att.id)},
                        updates_a,
                    )
                    counts["article_data_mime_attachment"] += 1

            search_rows = (
                (
                    await session.execute(
                        select(ArticleSearchIndex).where(
                            ArticleSearchIndex.article_id.in_(article_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for srow in search_rows:
                orig_val = srow.article_value
                if not orig_val:
                    continue
                new_val = mapper.anonymize_body(orig_val)
                if orig_val == new_val:
                    continue
                await _add_backup(
                    session,
                    job_id=job_id,
                    table_name="article_search_index",
                    row_pk={"id": int(srow.id)},
                    original_row={"article_value": orig_val},
                    created=now,
                )
                await session.execute(
                    text("UPDATE article_search_index SET article_value = :v WHERE id = :id"),
                    {"v": new_val, "id": int(srow.id)},
                )
                counts["article_search_index"] += 1

        # ---- customer_company: anonymize PII (keep PK) or delete if orphan ----
        company_ids = sorted({str(c.customer_id) for c in ordered if c.customer_id})
        for company_id in company_ids:
            company = (
                await session.execute(
                    select(CustomerCompany).where(CustomerCompany.customer_id == company_id)
                )
            ).scalar_one_or_none()
            if company is None:
                continue
            full_co = await _fetch_full_row(
                session, "customer_company", {"customer_id": company_id}
            )
            if full_co is None:
                continue

            if mode == "anonymize":
                changed_c: dict[str, Any] = {}
                updates_c: dict[str, Any] = {}
                for col in _CUSTOMER_COMPANY_PII:
                    if col not in full_co:
                        continue
                    orig = full_co[col]
                    new_val = mapper.map_value(
                        str(orig) if orig is not None else None, _kind_for(col)
                    )
                    if orig != new_val:
                        changed_c[col] = orig
                        updates_c[col] = new_val
                if updates_c:
                    await _add_backup(
                        session,
                        job_id=job_id,
                        table_name="customer_company",
                        row_pk={"customer_id": company_id},
                        original_row=changed_c,
                        created=now,
                    )
                    await _update_row(
                        session,
                        "customer_company",
                        {"customer_id": company_id},
                        updates_c,
                    )
                    counts["customer_company"] += 1
            else:
                # Delete company only if no other customer_user remains.
                remaining = (
                    (
                        await session.execute(
                            select(CustomerUser.id).where(
                                CustomerUser.customer_id == company_id,
                                CustomerUser.id.notin_([int(c.id) for c in ordered]),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if remaining:
                    # Still anonymize company PII when other users remain.
                    changed_c = {}
                    updates_c = {}
                    for col in _CUSTOMER_COMPANY_PII:
                        if col not in full_co:
                            continue
                        orig = full_co[col]
                        new_val = mapper.map_value(
                            str(orig) if orig is not None else None, _kind_for(col)
                        )
                        if orig != new_val:
                            changed_c[col] = orig
                            updates_c[col] = new_val
                    if updates_c:
                        await _add_backup(
                            session,
                            job_id=job_id,
                            table_name="customer_company",
                            row_pk={"customer_id": company_id},
                            original_row=changed_c,
                            created=now,
                        )
                        await _update_row(
                            session,
                            "customer_company",
                            {"customer_id": company_id},
                            updates_c,
                        )
                        counts["customer_company"] += 1
                else:
                    await _add_backup(
                        session,
                        job_id=job_id,
                        table_name="customer_company",
                        row_pk={"customer_id": company_id},
                        original_row=full_co,
                        created=now,
                    )
                    await session.execute(
                        text("DELETE FROM customer_company WHERE customer_id = :cid"),
                        {"cid": company_id},
                    )
                    counts["customer_company_deleted"] += 1

        # ---- hard-delete master rows (delete mode) ----
        if mode == "delete":
            for old_login in logins:
                await session.execute(
                    text("DELETE FROM customer_preferences WHERE user_id = :u"),
                    {"u": old_login},
                )
                await session.execute(
                    text("DELETE FROM customer_user_customer WHERE user_id = :u"),
                    {"u": old_login},
                )
                await session.execute(
                    text("DELETE FROM group_customer_user WHERE user_id = :u"),
                    {"u": old_login},
                )
            await session.execute(
                text(
                    "DELETE FROM customer_user WHERE id IN ("
                    + ",".join(str(int(i)) for i in customer_user_ids)
                    + ")"
                )
            )

        # ---- cache invalidation ----
        for ctype in _CUSTOMER_USER_CACHE_TYPES:
            await invalidate_cache_type(session, ctype)
        for ctype in _CUSTOMER_COMPANY_CACHE_TYPES:
            await invalidate_cache_type(session, ctype)
        for tid in ticket_ids:
            await invalidate_ticket_cache(session, tid)

        job.counts = _json_dump(counts)
        job.resolved_logins = _json_dump(
            [login_to_new.get(lg, lg) if mode == "anonymize" else lg for lg in logins]
            if mode == "anonymize"
            else logins
        )

    # Audit outside the big txn (record_audit commits itself).
    async with session_factory() as session:
        await record_audit(
            session,
            action=f"erasure_{mode}",
            target=f"job:{job_id}",
            actor=actor,
            counts=counts,
            force_parallel=force_parallel,
        )

    return ErasureResult(
        job_id=job_id,
        mode=mode,
        counts=counts,
        resolved_logins=logins,
    )


# ---------------------------------------------------------------------------
# Rollback / purge
# ---------------------------------------------------------------------------


async def rollback_job(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    job_id: int,
    *,
    actor: str = "admin",
    force_parallel: bool = True,
) -> dict[str, int]:
    """Restore all rows from ``tiqora_gdpr_backup`` for *job_id*."""
    async with session_factory() as session:
        await require_write_gate(
            session,
            settings,
            force_parallel=force_parallel,
            operation="gdpr_erasure_rollback",
        )

    async with session_factory() as session, session.begin():
        job = await session.get(TiqoraGdprJob, job_id)
        if job is None:
            raise ErasureNotFoundError(f"gdpr job {job_id} not found")
        if job.status != "applied":
            raise ErasureError(f"refuse rollback: job status is {job.status!r} (need 'applied')")

        backups = (
            (
                await session.execute(
                    select(TiqoraGdprBackup)
                    .where(TiqoraGdprBackup.job_id == job_id)
                    .order_by(TiqoraGdprBackup.id.desc())
                )
            )
            .scalars()
            .all()
        )
        if not backups:
            raise ErasureError("no backup rows available (already purged?)")

        restored = 0
        ticket_ids: list[int] = []
        # Reverse order so deletes (which were applied last) re-INSERT first
        # for masters, and ticket/article UPDATEs restore afterward.
        # We stored backups in apply order; reverse restores dependent first.
        for bak in backups:
            pk = _json_load_row(bak.row_pk)
            original = _json_load_row(bak.original_row)
            table = bak.table_name

            if table == "customer_user" and job.mode == "delete":
                await _insert_row(session, table, original)
            elif table == "customer_company" and "name" in original and len(original) > 3:
                # Full-row company backup (deleted) vs changed-cols (anonymize).
                exists = await _fetch_full_row(session, table, pk)
                if exists is None:
                    await _insert_row(session, table, original)
                else:
                    await _update_row(session, table, pk, original)
            elif table in (
                "customer_preferences",
                "customer_user_customer",
                "group_customer_user",
            ):
                if job.mode == "delete":
                    await _insert_row(session, table, original)
                else:
                    # Anonymize path: original has only old user_id; reverse
                    # login rename by looking up current row via new mapping
                    # is hard — restore by pk if still present under new login.
                    # We stored only {"user_id": old}; re-resolve via full backup
                    # path used for delete. For anonymize, re-apply user_id.
                    if "user_id" in original and len(original) == 1:
                        # Find rows that were renamed away from old login:
                        # we cannot know new login from this slim backup alone.
                        # Re-fetch by remaining pk cols isn't available.
                        # Skip slim backups for rename — full path only on delete.
                        # For anonymize rollback of login rename, customer_user
                        # restore already puts login back; related tables need
                        # user_id flipped back. We stored old user_id only.
                        # Strategy: if table row has composite and we have old
                        # user_id, UPDATE user_id from any current value matching
                        # other pk parts is incomplete. Store was slim.
                        # Re-run: update any row whose other keys match and
                        # user_id is not old to old. Better approach below.
                        pass
                    else:
                        exists = await _fetch_full_row(session, table, pk)
                        if exists is None:
                            await _insert_row(session, table, original)
                        else:
                            await _update_row(session, table, pk, original)
            else:
                # UPDATE changed columns back (ticket, mime, search_index, …)
                # or full row if present.
                exists = await _fetch_full_row(session, table, pk)
                if exists is None and table in (
                    "customer_user",
                    "customer_company",
                    "customer_preferences",
                    "customer_user_customer",
                    "group_customer_user",
                ):
                    await _insert_row(session, table, {**pk, **original})
                else:
                    await _update_row(session, table, pk, original)

            if table == "ticket" and "id" in pk:
                ticket_ids.append(int(pk["id"]))
            restored += 1

        # Fix anonymize-mode login renames on related tables: after customer_user
        # is restored, rename related user_id from new→old using job.resolved_logins
        # and selector snapshot is insufficient. Re-derive from backups of
        # customer_user (changed login).
        if job.mode == "anonymize":
            await _rollback_login_renames(session, job_id)

        now = datetime.now(UTC).replace(tzinfo=None)
        job.status = "rolled_back"
        job.rolled_back_at = now

        for ctype in _CUSTOMER_USER_CACHE_TYPES:
            await invalidate_cache_type(session, ctype)
        for ctype in _CUSTOMER_COMPANY_CACHE_TYPES:
            await invalidate_cache_type(session, ctype)
        for tid in set(ticket_ids):
            await invalidate_ticket_cache(session, tid)

    async with session_factory() as session:
        await record_audit(
            session,
            action="erasure_rollback",
            target=f"job:{job_id}",
            actor=actor,
            counts={"restored_rows": restored},
            force_parallel=force_parallel,
        )
    return {"restored_rows": restored}


async def _rollback_login_renames(session: AsyncSession, job_id: int) -> None:
    """After customer_user login is restored, flip related tables back."""
    # Find customer_user backups that include login.
    rows = (
        (
            await session.execute(
                select(TiqoraGdprBackup).where(
                    TiqoraGdprBackup.job_id == job_id,
                    TiqoraGdprBackup.table_name == "customer_user",
                )
            )
        )
        .scalars()
        .all()
    )
    for bak in rows:
        pk = _json_load_row(bak.row_pk)
        original = _json_load_row(bak.original_row)
        old_login = original.get("login")
        if not old_login or "id" not in pk:
            continue
        # Current login after other restores may already be old; if not, get current.
        cur = await _fetch_full_row(session, "customer_user", pk)
        if cur is None:
            continue
        new_login = cur.get("login")
        # After UPDATE restore, login should already be old_login. Related tables
        # may still hold new_login if they were updated during anonymize.
        # Also handle case where customer_user restore set login back.
        # Discover the "new" login from... we don't store it. Derive: any
        # customer_preferences with user_id != old that we backup-touched.
        # Simpler: related-table backups store only old user_id; during apply
        # we UPDATE user_id old→new. On rollback we need new→old. Find new by
        # scanning current related tables for this customer id's tickets etc.
        # Best: look at ticket backups (customer_user_id changed).
        pass

    # Ticket backups hold old customer_user_id; after ticket restore it's fixed.
    # Related master tables (prefs/cuc/gcu): find rows that still have non-old
    # user_id matching the login_to_new reverse via customer_user current+backup.
    for bak in rows:
        pk = _json_load_row(bak.row_pk)
        original = _json_load_row(bak.original_row)
        old_login = original.get("login")
        if not old_login:
            continue
        # What is the anonymized login? Compare: if login was in changed cols,
        # current customer_user.login is already restored to old. We need the
        # intermediate new login. Recompute from ValueMapper is impossible without
        # seed alone… actually we have job.seed. Recompute:
        job = await session.get(TiqoraGdprJob, job_id)
        if job is None or old_login is None:
            continue
        mapper = ValueMapper(seed=job.seed)
        new_login = mapper.map_value(str(old_login), "login")
        if not new_login or new_login == old_login:
            continue
        for table in (
            "customer_preferences",
            "customer_user_customer",
            "group_customer_user",
        ):
            await session.execute(
                text(f"UPDATE {table} SET user_id = :old WHERE user_id = :new"),
                {"old": old_login, "new": new_login},
            )


async def purge_expired_backups(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete backup rows past ``backup_expires_at``; flip jobs to ``purged``."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    purged_jobs = 0
    deleted_backups = 0
    async with session_factory() as session, session.begin():
        jobs = (
            (
                await session.execute(
                    select(TiqoraGdprJob).where(
                        TiqoraGdprJob.status == "applied",
                        TiqoraGdprJob.backup_expires_at < now,
                    )
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            count_before = (
                await session.execute(
                    text("SELECT COUNT(*) FROM tiqora_gdpr_backup WHERE job_id = :jid"),
                    {"jid": int(job.id)},
                )
            ).scalar_one()
            await session.execute(
                text("DELETE FROM tiqora_gdpr_backup WHERE job_id = :jid"),
                {"jid": int(job.id)},
            )
            deleted_backups += int(count_before or 0)
            job.status = "purged"
            purged_jobs += 1
    return {"purged_jobs": purged_jobs, "deleted_backups": deleted_backups}


async def purge_job_backup(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
    *,
    actor: str = "admin",
) -> dict[str, int]:
    """Manually purge backups for one job (admin action)."""
    async with session_factory() as session, session.begin():
        job = await session.get(TiqoraGdprJob, job_id)
        if job is None:
            raise ErasureNotFoundError(f"gdpr job {job_id} not found")
        if job.status == "purged":
            return {"deleted_backups": 0}
        if job.status == "rolled_back":
            # Still allow purging residual backups after rollback.
            pass
        count_before = (
            await session.execute(
                text("SELECT COUNT(*) FROM tiqora_gdpr_backup WHERE job_id = :jid"),
                {"jid": job_id},
            )
        ).scalar_one()
        await session.execute(
            text("DELETE FROM tiqora_gdpr_backup WHERE job_id = :jid"),
            {"jid": job_id},
        )
        deleted = int(count_before or 0)
        job.status = "purged"
    async with session_factory() as session:
        await record_audit(
            session,
            action="erasure_purge",
            target=f"job:{job_id}",
            actor=actor,
            counts={"deleted_backups": deleted},
            force_parallel=False,
        )
    return {"deleted_backups": deleted}


async def load_job_backups_export(session: AsyncSession, job_id: int) -> list[dict[str, Any]]:
    """All backup rows for download (JSON-safe)."""
    rows = (
        (
            await session.execute(
                select(TiqoraGdprBackup)
                .where(TiqoraGdprBackup.job_id == job_id)
                .order_by(TiqoraGdprBackup.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": int(r.id),
            "job_id": int(r.job_id),
            "table_name": r.table_name,
            "row_pk": json.loads(r.row_pk),
            "original_row": json.loads(r.original_row),
            "created": r.created.isoformat() if r.created else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Low-level SQL helpers
# ---------------------------------------------------------------------------


async def _add_backup(
    session: AsyncSession,
    *,
    job_id: int,
    table_name: str,
    row_pk: dict[str, Any],
    original_row: dict[str, Any],
    created: datetime,
) -> None:
    session.add(
        TiqoraGdprBackup(
            job_id=job_id,
            table_name=table_name,
            row_pk=_json_dump(row_pk),
            original_row=_json_dump(original_row),
            created=created,
        )
    )


async def _fetch_full_row(
    session: AsyncSession, table: str, pk: dict[str, Any]
) -> dict[str, Any] | None:
    if not pk:
        return None
    where = " AND ".join(f"{_qi(k)} = :{k}" for k in pk)
    sql = f"SELECT * FROM {_qi(table)} WHERE {where}"
    row = (await session.execute(text(sql), pk)).mappings().first()
    return dict(row) if row is not None else None


async def _update_row(
    session: AsyncSession,
    table: str,
    pk: dict[str, Any],
    updates: dict[str, Any],
) -> None:
    if not updates:
        return
    # Avoid clobbering pk columns in SET if present.
    sets = {k: v for k, v in updates.items() if k not in pk}
    if not sets:
        return
    set_sql = ", ".join(f"{_qi(k)} = :set_{k}" for k in sets)
    where = " AND ".join(f"{_qi(k)} = :pk_{k}" for k in pk)
    params: dict[str, Any] = {f"set_{k}": v for k, v in sets.items()}
    params.update({f"pk_{k}": v for k, v in pk.items()})
    await session.execute(text(f"UPDATE {_qi(table)} SET {set_sql} WHERE {where}"), params)


async def _insert_row(session: AsyncSession, table: str, row: dict[str, Any]) -> None:
    if not row:
        return
    cols = list(row.keys())
    col_sql = ", ".join(_qi(c) for c in cols)
    val_sql = ", ".join(f":{c}" for c in cols)
    await session.execute(
        text(f"INSERT INTO {_qi(table)} ({col_sql}) VALUES ({val_sql})"),
        row,
    )


def _qi(name: str) -> str:
    """Quote an identifier conservatively (alphanumeric + underscore only)."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ErasureError(f"unsafe SQL identifier: {name!r}")
    return name
