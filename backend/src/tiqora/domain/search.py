"""Meilisearch ticket index document building and search queries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from meilisearch_python_sdk import AsyncClient
from meilisearch_python_sdk.errors import MeilisearchApiError
from meilisearch_python_sdk.models.settings import MeilisearchSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.legacy.article import Article, ArticleDataMime
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import (
    Ticket,
    TicketPriority,
    TicketState,
    TicketStateType,
)
from tiqora.db.legacy.user import Users
from tiqora.domain.queue_service import QueueService
from tiqora.domain.schemas import (
    SearchHit,
    SearchResponse,
    SimilarTicketItem,
    SimilarTicketsOut,
)

# Similar-tickets v1: pull a wider Meili window then rank down to the public top-N.
_SIMILAR_CANDIDATE_LIMIT = 20
_SIMILAR_RESULT_LIMIT = 5
_SIMILAR_QUERY_MAX_CHARS = 500


def _dt_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.isoformat()


def _dt_ts(value: datetime | None) -> int | None:
    """Unix timestamp for a Znuny datetime, which is naive and always UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


def _meili_escape_string(value: str) -> str:
    """Escape a string value for embedding in a Meilisearch filter expression.

    Meili string literals use single quotes; backslash escapes ``\\`` and ``'``.
    Without this, a crafted filter value could break out of the mandatory
    ``queue_id IN [...]`` permission clause (Meili binds AND tighter than OR).
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def build_similar_query(title: str | None, excerpt: str | None) -> str:
    """Compose the Meili query string for similar-ticket search."""
    parts = [p.strip() for p in (title or "", excerpt or "") if p and p.strip()]
    return " ".join(parts)[:_SIMILAR_QUERY_MAX_CHARS].strip()


def rank_similar_keyword(
    candidates: list[SimilarTicketItem],
    *,
    exclude_id: int,
    limit: int = _SIMILAR_RESULT_LIMIT,
) -> list[SimilarTicketItem]:
    """Keyword-only ranking for similar tickets (v1).

    Candidates are assumed pre-ordered by Meilisearch relevance (best first).
    This function only drops the source ticket and trims to *limit* so a later
    embedding cosine blend can replace or wrap it without touching the route.
    """
    ranked = [c for c in candidates if c.id != exclude_id]
    return ranked[: max(0, limit)]


def build_ticket_document(
    ticket: Ticket,
    *,
    queue_name: str | None,
    state_name: str | None,
    state_type: str | None,
    priority_name: str | None,
    owner_login: str | None,
    owner_name: str | None,
    latest_excerpt: str | None,
    dynamic_fields: dict[str, Any],
) -> dict[str, Any]:
    """Build a Meilisearch document for one ticket (pure, unit-testable)."""
    return {
        "id": ticket.id,
        "tn": ticket.tn,
        "title": ticket.title or "",
        "queue_id": ticket.queue_id,
        "queue_name": queue_name or "",
        "state": state_name or "",
        "state_type": state_type or "",
        "priority": priority_name or "",
        "owner_id": ticket.user_id,
        "owner_login": owner_login or "",
        "owner_name": owner_name or "",
        "customer_id": ticket.customer_id or "",
        "customer_user_id": ticket.customer_user_id or "",
        "created": _dt_iso(ticket.create_time),
        "changed": _dt_iso(ticket.change_time),
        "created_ts": _dt_ts(ticket.create_time),
        "changed_ts": _dt_ts(ticket.change_time),
        "escalation_time": ticket.escalation_time,
        "escalation_response_time": ticket.escalation_response_time,
        "escalation_update_time": ticket.escalation_update_time,
        "escalation_solution_time": ticket.escalation_solution_time,
        "has_escalation": bool(
            ticket.escalation_time
            or ticket.escalation_response_time
            or ticket.escalation_update_time
            or ticket.escalation_solution_time
        ),
        "latest_article_excerpt": (latest_excerpt or "")[:2000],
        "dynamic_fields": dynamic_fields,
    }


class SearchIndexService:
    """Index tickets into Meilisearch and run permission-filtered searches."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        client: AsyncClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> AsyncClient:
        if self._client is None:
            self._client = AsyncClient(
                url=self._settings.meili_url,
                api_key=self._settings.meili_master_key,
            )
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ensure_index(self) -> None:
        client = await self._get_client()
        index_name = self._settings.meili_tickets_index
        indexes = await client.get_indexes() or []
        names = {idx.uid for idx in indexes}
        if index_name not in names:
            await client.create_index(index_name, primary_key="id")
        index = client.index(index_name)
        settings = MeilisearchSettings(
            filterable_attributes=[
                "queue_id",
                "state_type",
                "owner_id",
                "customer_id",
                "has_escalation",
                "created_ts",
                "changed_ts",
            ],
            sortable_attributes=["changed", "created", "id"],
            searchable_attributes=[
                "tn",
                "title",
                "latest_article_excerpt",
                "customer_id",
                "customer_user_id",
                "owner_login",
                "owner_name",
                "queue_name",
                "dynamic_fields",
            ],
        )
        task = await index.update_settings(settings)
        await client.wait_for_task(task.task_uid, timeout_in_ms=60_000)

    async def build_document(self, ticket_id: int) -> dict[str, Any] | None:
        ticket = (
            await self._session.execute(select(Ticket).where(Ticket.id == ticket_id))
        ).scalar_one_or_none()
        if ticket is None:
            return None
        return await self._document_for_ticket(ticket)

    async def _document_for_ticket(self, ticket: Ticket) -> dict[str, Any]:
        queue_name = (
            await self._session.execute(select(Queue.name).where(Queue.id == ticket.queue_id))
        ).scalar_one_or_none()
        state_row = (
            await self._session.execute(
                select(TicketState.name, TicketStateType.name)
                .join(TicketStateType, TicketStateType.id == TicketState.type_id)
                .where(TicketState.id == ticket.ticket_state_id)
            )
        ).one_or_none()
        state_name = state_row[0] if state_row else None
        state_type = state_row[1] if state_row else None
        priority_name = (
            await self._session.execute(
                select(TicketPriority.name).where(TicketPriority.id == ticket.ticket_priority_id)
            )
        ).scalar_one_or_none()
        user = (
            await self._session.execute(select(Users).where(Users.id == ticket.user_id))
        ).scalar_one_or_none()
        owner_login = user.login if user else None
        owner_name = f"{user.first_name} {user.last_name}".strip() if user else None

        # Latest article excerpt (plaintext body)
        latest_art = (
            await self._session.execute(
                select(Article.id)
                .where(Article.ticket_id == ticket.id)
                .order_by(Article.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        excerpt: str | None = None
        if latest_art is not None:
            body = (
                await self._session.execute(
                    select(ArticleDataMime.a_body).where(ArticleDataMime.article_id == latest_art)
                )
            ).scalar_one_or_none()
            if body:
                excerpt = body[:2000]

        # Flatten dynamic field values
        fields = (
            (
                await self._session.execute(
                    select(DynamicField).where(
                        DynamicField.object_type == "Ticket", DynamicField.valid_id == 1
                    )
                )
            )
            .scalars()
            .all()
        )
        df_map: dict[str, Any] = {}
        if fields:
            fid_to_name = {f.id: f.name for f in fields}
            vals = (
                (
                    await self._session.execute(
                        select(DynamicFieldValue).where(
                            DynamicFieldValue.object_id == ticket.id,
                            DynamicFieldValue.field_id.in_(fid_to_name.keys()),
                        )
                    )
                )
                .scalars()
                .all()
            )
            multi: dict[str, list[Any]] = {}
            for v in vals:
                name = fid_to_name.get(v.field_id)
                if not name:
                    continue
                if v.value_text is not None:
                    multi.setdefault(name, []).append(v.value_text)
                elif v.value_int is not None:
                    multi.setdefault(name, []).append(v.value_int)
                elif v.value_date is not None:
                    multi.setdefault(name, []).append(v.value_date.isoformat())
            for name, items in multi.items():
                df_map[name] = items[0] if len(items) == 1 else items

        return build_ticket_document(
            ticket,
            queue_name=queue_name,
            state_name=state_name,
            state_type=state_type,
            priority_name=priority_name,
            owner_login=owner_login,
            owner_name=owner_name,
            latest_excerpt=excerpt,
            dynamic_fields=df_map,
        )

    async def index_tickets(self, ticket_ids: list[int]) -> int:
        """Re-index the given ticket ids. Returns number of documents sent."""
        if not ticket_ids:
            return 0
        await self.ensure_index()
        docs: list[dict[str, Any]] = []
        for tid in ticket_ids:
            doc = await self.build_document(tid)
            if doc is not None:
                docs.append(doc)
        if not docs:
            return 0
        client = await self._get_client()
        index = client.index(self._settings.meili_tickets_index)
        task = await index.add_documents(docs)
        await client.wait_for_task(task.task_uid, timeout_in_ms=120_000)
        return len(docs)

    async def search(
        self,
        user_id: int,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        queue_ids: list[int] | None = None,
        state_types: list[str] | None = None,
        owner_id: int | None = None,
        customer_id: str | None = None,
        created_from: int | None = None,
        created_to: int | None = None,
    ) -> SearchResponse:
        allowed = await QueueService(self._session).allowed_queue_ids(user_id, "ro")
        if not allowed:
            return SearchResponse(query=query, hits=[], estimated_total=0, facets={})

        await self.ensure_index()
        client = await self._get_client()
        index = client.index(self._settings.meili_tickets_index)
        # Mandatory permission filter — always ANDed, never widened by caller-supplied
        # queue_ids (an AND of two IN clauses is exactly their intersection).
        filters: list[str] = [f"queue_id IN [{','.join(str(qid) for qid in sorted(allowed))}]"]
        if queue_ids:
            filters.append(f"queue_id IN [{','.join(str(int(qid)) for qid in queue_ids)}]")
        if state_types:
            joined = ",".join(f"'{_meili_escape_string(st)}'" for st in state_types)
            filters.append(f"state_type IN [{joined}]")
        if owner_id is not None:
            filters.append(f"owner_id = {int(owner_id)}")
        if customer_id:
            filters.append(f"customer_id = '{_meili_escape_string(customer_id)}'")
        # Date filters require created_ts on documents. Documents indexed before
        # that field was added lack it and are excluded (silent empty results)
        # until `tiqora index rebuild --no-resume`. See docs/deployment.md.
        if created_from is not None:
            filters.append(f"created_ts >= {int(created_from)}")
        if created_to is not None:
            filters.append(f"created_ts <= {int(created_to)}")
        # Only facets the agent UI consumes. owner_id/customer_id are high-cardinality
        # and unused in the filter bar counts — requesting them bloats every response.
        result = await index.search(
            query,
            filter=" AND ".join(filters),
            facets=["queue_id", "state_type"],
            limit=min(limit, 100),
            offset=offset,
            sort=["changed:desc"],
        )
        facets: dict[str, dict[str, int]] = {
            name: {str(k): v for k, v in dist.items()}
            for name, dist in (result.facet_distribution or {}).items()
        }
        hits: list[SearchHit] = []
        for raw in result.hits or []:
            h = raw if isinstance(raw, dict) else dict(raw)
            hits.append(
                SearchHit(
                    id=int(h["id"]),
                    tn=h.get("tn"),
                    title=h.get("title"),
                    queue_id=h.get("queue_id"),
                    queue_name=h.get("queue_name"),
                    state=h.get("state"),
                    state_type=h.get("state_type"),
                    priority=h.get("priority"),
                    owner_login=h.get("owner_login"),
                    customer_id=h.get("customer_id") or None,
                    create_time=h.get("created"),
                    change_time=h.get("changed"),
                    excerpt=h.get("latest_article_excerpt"),
                )
            )
        return SearchResponse(
            query=query,
            hits=hits,
            estimated_total=int(result.estimated_total_hits or len(hits)),
            facets=facets,
        )

    async def get_indexed_document(self, ticket_id: int) -> dict[str, Any] | None:
        """Return the Meili document for *ticket_id*, or ``None`` if missing.

        Prefer this over :meth:`build_document` for read paths that only need
        fields already in the index (e.g. similar-ticket excerpt) — no SQL fan-out.
        """
        await self.ensure_index()
        client = await self._get_client()
        index = client.index(self._settings.meili_tickets_index)
        try:
            doc = await index.get_document(ticket_id)
        except MeilisearchApiError:
            # 404 document_not_found (and rare transient API errors): treat as miss.
            return None
        if doc is None:
            return None
        return doc if isinstance(doc, dict) else dict(doc)

    async def find_similar(
        self,
        user_id: int,
        ticket_id: int,
        *,
        title: str | None,
        excerpt: str | None = None,
        limit: int = _SIMILAR_RESULT_LIMIT,
        candidate_limit: int = _SIMILAR_CANDIDATE_LIMIT,
    ) -> SimilarTicketsOut:
        """Find closed tickets similar to *ticket_id* via Meili keyword ranking.

        Permission filter is mandatory. Only ``state_type = closed`` tickets are
        considered. The source ticket is excluded in :func:`rank_similar_keyword`.
        """
        query = build_similar_query(title, excerpt)
        if not query:
            return SimilarTicketsOut(items=[])

        allowed = await QueueService(self._session).allowed_queue_ids(user_id, "ro")
        if not allowed:
            return SimilarTicketsOut(items=[])

        await self.ensure_index()
        client = await self._get_client()
        index = client.index(self._settings.meili_tickets_index)
        filters = [
            f"queue_id IN [{','.join(str(qid) for qid in sorted(allowed))}]",
            "state_type = 'closed'",
        ]
        # No sort: keep Meili relevance order (sort would override ranking).
        result = await index.search(
            query,
            filter=" AND ".join(filters),
            limit=min(max(candidate_limit, limit), 100),
            offset=0,
            show_ranking_score=True,
        )
        candidates: list[SimilarTicketItem] = []
        for raw in result.hits or []:
            h = raw if isinstance(raw, dict) else dict(raw)
            score_raw = h.get("_rankingScore")
            try:
                score = float(score_raw) if score_raw is not None else 0.0
            except (TypeError, ValueError):
                score = 0.0
            candidates.append(
                SimilarTicketItem(
                    id=int(h["id"]),
                    tn=h.get("tn"),
                    title=h.get("title"),
                    state=h.get("state"),
                    queue_name=h.get("queue_name"),
                    score=score,
                )
            )
        return SimilarTicketsOut(
            items=rank_similar_keyword(candidates, exclude_id=ticket_id, limit=limit)
        )
