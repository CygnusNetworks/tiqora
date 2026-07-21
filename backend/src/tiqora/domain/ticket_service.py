"""Read-only ticket, article, attachment, and history access."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any

import yaml
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.article import (
    Article,
    ArticleDataMime,
    ArticleSenderType,
)
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.queue import Queue, QueueStandardTemplate, StandardTemplate
from tiqora.db.legacy.ticket import (
    Ticket,
    TicketHistory,
    TicketHistoryType,
    TicketLockType,
    TicketPriority,
    TicketState,
    TicketStateType,
    TicketWatcher,
)
from tiqora.db.legacy.user import Users
from tiqora.domain.article_html import RenderedArticleBody, render_article_body
from tiqora.domain.history_render import render_history_entry
from tiqora.domain.queue_service import OPEN_STATE_TYPES, age_seconds
from tiqora.domain.quoting import (
    build_reply_subject,
    build_ticket_subject,
    html_to_plaintext,
    quote_plaintext_body,
)
from tiqora.domain.schemas import (
    ArticleListItem,
    AttachmentMetaOut,
    DynamicFieldValueOut,
    HistoryEntry,
    PaginatedTickets,
    ReplyDraftOut,
    TemplateOut,
    TicketDetail,
    TicketListItem,
)
from tiqora.domain.subject_hook import load_subject_config
from tiqora.permissions.engine import PermissionEngine
from tiqora.storage.backend import AttachmentContent, DbMimeStorage

#: Named ``state_type`` query-param views resolved to one-or-more
#: ``ticket_state_type.name`` values. Mirrors Znuny's
#: ``Ticket::ViewableStateType`` sysconfig (new + open + pending reminder +
#: pending auto) so the default queue "Offen" view — previously a literal
#: equality match against the single state type named ``open`` — no longer
#: hides freshly-arrived ``new`` tickets. ``"new"`` is also exposed as its
#: own view for the dedicated "Neu" tab. Any ``state_type`` value not in this
#: map (e.g. ``"closed"``, or a raw ``ticket_state_type.name``) still falls
#: back to a literal single-name match.
VIEW_STATE_TYPES: dict[str, frozenset[str]] = {
    "open": OPEN_STATE_TYPES,
    "new": frozenset({"new"}),
    "pending": frozenset({"pending reminder", "pending auto"}),
}


class TicketAccessDenied(Exception):
    """User lacks ro permission on the ticket's queue group."""


class TicketNotFound(Exception):
    """Ticket id does not exist."""


class TicketService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._perms = PermissionEngine(session)
        self._storage = DbMimeStorage(session)

    async def _assert_ticket_ro(self, user_id: int, ticket_id: int) -> Ticket:
        result = await self._session.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise TicketNotFound(ticket_id)
        if not await self._perms.check(user_id, ticket.queue_id, "ro"):
            raise TicketAccessDenied(ticket_id)
        return ticket

    async def _lookup_maps(self) -> dict[str, Any]:
        states = {
            r.id: r.name for r in (await self._session.execute(select(TicketState))).scalars()
        }
        state_types_by_state: dict[int, str] = {}
        st_rows = await self._session.execute(
            select(TicketState.id, TicketStateType.name).join(
                TicketStateType, TicketStateType.id == TicketState.type_id
            )
        )
        for sid, stname in st_rows.all():
            state_types_by_state[sid] = stname
        priorities = {
            r.id: r.name for r in (await self._session.execute(select(TicketPriority))).scalars()
        }
        locks = {
            r.id: r.name for r in (await self._session.execute(select(TicketLockType))).scalars()
        }
        queues = {r.id: r.name for r in (await self._session.execute(select(Queue))).scalars()}
        users: dict[int, tuple[str, str]] = {
            r.id: (r.login, f"{r.first_name} {r.last_name}".strip())
            for r in (await self._session.execute(select(Users))).scalars()
        }
        return {
            "state": states,
            "state_type": state_types_by_state,
            "priority": priorities,
            "lock": locks,
            "queue": queues,
            "user": users,
        }

    def _to_list_item(self, t: Ticket, maps: dict[str, Any]) -> TicketListItem:
        owner = maps["user"].get(t.user_id)
        return TicketListItem(
            id=t.id,
            tn=t.tn,
            title=t.title,
            queue_id=t.queue_id,
            queue_name=maps["queue"].get(t.queue_id),
            state_id=t.ticket_state_id,
            state=maps["state"].get(t.ticket_state_id),
            state_type=maps["state_type"].get(t.ticket_state_id),
            priority_id=t.ticket_priority_id,
            priority=maps["priority"].get(t.ticket_priority_id),
            lock_id=t.ticket_lock_id,
            lock=maps["lock"].get(t.ticket_lock_id),
            owner_id=t.user_id,
            owner_login=owner[0] if owner else None,
            owner_name=owner[1] if owner else None,
            customer_id=t.customer_id,
            customer_user_id=t.customer_user_id,
            create_time=t.create_time,
            change_time=t.change_time,
            age_seconds=age_seconds(t.create_time),
            escalation_time=t.escalation_time,
            escalation_response_time=t.escalation_response_time,
            escalation_update_time=t.escalation_update_time,
            escalation_solution_time=t.escalation_solution_time,
            until_time=t.until_time,
        )

    _SORT_COLUMNS = {
        "age": Ticket.create_time,
        "created": Ticket.create_time,
        "changed": Ticket.change_time,
        "tn": Ticket.tn,
        "title": Ticket.title,
        "priority": Ticket.ticket_priority_id,
    }

    async def _filtered_ticket_stmt(
        self,
        user_id: int,
        *,
        queue_id: int | None,
        state_id: int | None,
        state_type: str | None,
        owner_id: int | None,
    ) -> Select[tuple[Ticket]] | None:
        """Build the permission-filtered, unordered ``Ticket`` select.

        Shared by :meth:`list_tickets` (paginated UI) and
        :meth:`iter_tickets_for_export` (unbounded CSV export) so both apply
        identical ``ro`` permission scoping and query filters. Returns
        ``None`` when the result set is guaranteed empty (no permission, no
        allowed queues, or a ``state_type`` with no matching states) — every
        caller treats ``None`` as "zero rows".
        """
        allowed_groups = await self._perms.groups_for_permission(user_id, "ro")
        if not allowed_groups:
            return None

        q_ids_result = await self._session.execute(
            select(Queue.id).where(Queue.group_id.in_(allowed_groups), Queue.valid_id == 1)
        )
        allowed_queues = set(q_ids_result.scalars().all())
        if not allowed_queues:
            return None

        if queue_id is not None:
            if queue_id not in allowed_queues:
                return None
            filter_queues = {queue_id}
        else:
            filter_queues = allowed_queues

        stmt = select(Ticket).where(
            Ticket.queue_id.in_(filter_queues),
            Ticket.archive_flag == 0,
        )

        if state_id is not None:
            stmt = stmt.where(Ticket.ticket_state_id == state_id)
        if owner_id is not None:
            stmt = stmt.where(Ticket.user_id == owner_id)
        if state_type is not None:
            type_names = VIEW_STATE_TYPES.get(state_type, {state_type})
            state_ids = (
                (
                    await self._session.execute(
                        select(TicketState.id)
                        .join(TicketStateType, TicketStateType.id == TicketState.type_id)
                        .where(TicketStateType.name.in_(type_names))
                    )
                )
                .scalars()
                .all()
            )
            if not state_ids:
                return None
            stmt = stmt.where(Ticket.ticket_state_id.in_(state_ids))

        return stmt

    def _order_by(
        self, stmt: Select[tuple[Ticket]], sort: str, order: str
    ) -> Select[tuple[Ticket]]:
        sort_col = self._SORT_COLUMNS.get(sort, Ticket.create_time)
        if order.lower() == "asc":
            return stmt.order_by(sort_col.asc())
        return stmt.order_by(sort_col.desc())

    async def list_tickets(
        self,
        user_id: int,
        *,
        queue_id: int | None = None,
        state_id: int | None = None,
        state_type: str | None = None,
        owner_id: int | None = None,
        offset: int = 0,
        limit: int = 50,
        sort: str = "age",
        order: str = "desc",
    ) -> PaginatedTickets:
        stmt = await self._filtered_ticket_stmt(
            user_id,
            queue_id=queue_id,
            state_id=state_id,
            state_type=state_type,
            owner_id=owner_id,
        )
        if stmt is None:
            return PaginatedTickets(items=[], total=0, offset=offset, limit=limit)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self._session.execute(count_stmt)).scalar_one())

        ordered = self._order_by(stmt, sort, order)
        result = await self._session.execute(ordered.offset(offset).limit(min(limit, 200)))
        tickets = list(result.scalars().all())
        maps = await self._lookup_maps()
        items = [self._to_list_item(t, maps) for t in tickets]
        return PaginatedTickets(items=items, total=total, offset=offset, limit=limit)

    async def iter_tickets_for_export(
        self,
        user_id: int,
        *,
        queue_id: int | None = None,
        state_id: int | None = None,
        state_type: str | None = None,
        owner_id: int | None = None,
        sort: str = "age",
        order: str = "desc",
        batch_size: int = 500,
    ) -> AsyncGenerator[TicketListItem, None]:
        """Yield every matching ticket (no page cap), same filters as ``list_tickets``.

        Streams server-side via ``AsyncSession.stream`` with ``yield_per`` so
        exporting a large queue never buffers the whole result set in memory.
        """
        stmt = await self._filtered_ticket_stmt(
            user_id,
            queue_id=queue_id,
            state_id=state_id,
            state_type=state_type,
            owner_id=owner_id,
        )
        if stmt is None:
            return

        ordered = self._order_by(stmt, sort, order).execution_options(yield_per=batch_size)
        maps = await self._lookup_maps()
        result = await self._session.stream(ordered)
        async for ticket in result.scalars():
            yield self._to_list_item(ticket, maps)

    #: State types hidden from the lightweight agent ticket picker (link/merge).
    _SEARCH_EXCLUDED_STATE_TYPES: frozenset[str] = frozenset({"merged", "removed"})

    async def search_tickets(
        self,
        user_id: int,
        *,
        q: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Permission-scoped ticket picker search by number (``tn``) or title.

        Only tickets in queues where the agent has at least ``ro`` are returned.
        Merged/removed tickets are excluded. Limit is capped at 50 (default 20).
        Empty ``q`` yields an empty list (no full dump).
        """
        term = (q or "").strip()
        if not term:
            return []
        limit = max(1, min(int(limit), 50))

        allowed_groups = await self._perms.groups_for_permission(user_id, "ro")
        if not allowed_groups:
            return []

        q_ids_result = await self._session.execute(
            select(Queue.id).where(Queue.group_id.in_(allowed_groups), Queue.valid_id == 1)
        )
        allowed_queues = set(q_ids_result.scalars().all())
        if not allowed_queues:
            return []

        like = f"%{term}%"
        stmt = (
            select(
                Ticket.id,
                Ticket.tn,
                Ticket.title,
                Queue.name,
                TicketState.name,
                TicketStateType.name,
            )
            .join(Queue, Queue.id == Ticket.queue_id)
            .join(TicketState, TicketState.id == Ticket.ticket_state_id)
            .join(TicketStateType, TicketStateType.id == TicketState.type_id)
            .where(
                Ticket.queue_id.in_(allowed_queues),
                Ticket.archive_flag == 0,
                TicketStateType.name.notin_(self._SEARCH_EXCLUDED_STATE_TYPES),
                or_(Ticket.tn.ilike(like), Ticket.title.ilike(like)),
            )
            .order_by(Ticket.change_time.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "ticket_id": int(r[0]),
                "tn": r[1] or "",
                "title": r[2] or "",
                "queue": r[3],
                "state": r[4],
                "state_type": r[5],
            }
            for r in rows
        ]

    async def count_owned(self, user_id: int) -> dict[str, int]:
        """Open/new ticket counts for tickets owned by ``user_id``.

        Powers the "My tickets" sidebar badges. Reuses the same permission
        scoping and ``state_type`` view resolution as the ticket list, so the
        numbers agree with what the agent sees after clicking through. Two
        cheap ``COUNT(*)`` queries — no rows or lookup maps are materialised.
        ``open`` uses the viewable-state view (new + open + pending), ``new``
        counts only freshly-arrived tickets.
        """
        counts: dict[str, int] = {"open": 0, "new": 0}
        for view in counts:
            stmt = await self._filtered_ticket_stmt(
                user_id,
                queue_id=None,
                state_id=None,
                state_type=view,
                owner_id=user_id,
            )
            if stmt is None:
                continue
            count_stmt = select(func.count()).select_from(stmt.subquery())
            counts[view] = int((await self._session.execute(count_stmt)).scalar_one())
        return counts

    async def count_dashboard_summary(self, user_id: int) -> dict[str, int]:
        """KPI-tile counts for the agent dashboard.

        Reuses the same ``ro`` permission scoping and ``state_type`` view
        resolution as the ticket list (:meth:`_filtered_ticket_stmt`) so each
        tile agrees with the filtered list it links to. Four cheap
        ``COUNT(*)`` queries — no rows or lookup maps are materialised.

        - ``my_open`` / ``my_new``: viewable-open / new tickets owned by the
          agent (same numbers as the "My tickets" sidebar badges).
        - ``unowned_new``: new tickets still owned by root (``owner_id=1``) in
          queues the agent can see — the unclaimed queue to pick up from.
        - ``escalated``: viewable-open tickets whose nearest escalation
          deadline has already passed (any ``escalation_*`` epoch in
          ``(0, now)``).
        """
        summary = {"my_open": 0, "my_new": 0, "unowned_new": 0, "escalated": 0}

        async def _count(stmt: Select[tuple[Ticket]] | None) -> int:
            if stmt is None:
                return 0
            count_stmt = select(func.count()).select_from(stmt.subquery())
            return int((await self._session.execute(count_stmt)).scalar_one())

        summary["my_open"] = await _count(
            await self._filtered_ticket_stmt(
                user_id, queue_id=None, state_id=None, state_type="open", owner_id=user_id
            )
        )
        summary["my_new"] = await _count(
            await self._filtered_ticket_stmt(
                user_id, queue_id=None, state_id=None, state_type="new", owner_id=user_id
            )
        )
        # "unowned" = still assigned to root (owner_id=1); this is how Znuny
        # leaves freshly-arrived tickets until an agent takes ownership.
        summary["unowned_new"] = await _count(
            await self._filtered_ticket_stmt(
                user_id, queue_id=None, state_id=None, state_type="new", owner_id=1
            )
        )

        esc_stmt = await self._filtered_ticket_stmt(
            user_id, queue_id=None, state_id=None, state_type="open", owner_id=None
        )
        if esc_stmt is not None:
            now = int(time.time())
            esc_stmt = esc_stmt.where(
                or_(
                    and_(Ticket.escalation_time > 0, Ticket.escalation_time < now),
                    and_(
                        Ticket.escalation_response_time > 0, Ticket.escalation_response_time < now
                    ),
                    and_(Ticket.escalation_update_time > 0, Ticket.escalation_update_time < now),
                    and_(
                        Ticket.escalation_solution_time > 0, Ticket.escalation_solution_time < now
                    ),
                )
            )
            summary["escalated"] = await _count(esc_stmt)
        return summary

    async def get_ticket(self, user_id: int, ticket_id: int) -> TicketDetail:
        ticket = await self._assert_ticket_ro(user_id, ticket_id)
        maps = await self._lookup_maps()
        base = self._to_list_item(ticket, maps)
        dfs = await self._load_dynamic_fields(ticket.id)
        is_watched = (
            await self._session.execute(
                select(TicketWatcher.user_id).where(
                    TicketWatcher.ticket_id == ticket_id,
                    TicketWatcher.user_id == user_id,
                )
            )
        ).first() is not None
        can_write = await self._perms.check(user_id, ticket.queue_id, "rw")
        return TicketDetail(
            **base.model_dump(),
            type_id=ticket.type_id,
            service_id=ticket.service_id,
            sla_id=ticket.sla_id,
            responsible_user_id=ticket.responsible_user_id,
            archive_flag=ticket.archive_flag,
            create_by=ticket.create_by,
            change_by=ticket.change_by,
            dynamic_fields=dfs,
            is_watched=is_watched,
            can_write=can_write,
        )

    async def _load_dynamic_fields(self, ticket_id: int) -> list[DynamicFieldValueOut]:
        fields = (
            (
                await self._session.execute(
                    select(DynamicField).where(
                        DynamicField.object_type == "Ticket",
                        DynamicField.valid_id == 1,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not fields:
            return []
        field_by_id = {f.id: f for f in fields}
        values = (
            (
                await self._session.execute(
                    select(DynamicFieldValue).where(
                        DynamicFieldValue.object_id == ticket_id,
                        DynamicFieldValue.field_id.in_(field_by_id.keys()),
                    )
                )
            )
            .scalars()
            .all()
        )
        grouped: dict[int, list[Any]] = {fid: [] for fid in field_by_id}
        for v in values:
            val: Any
            if v.value_text is not None:
                val = v.value_text
            elif v.value_int is not None:
                val = v.value_int
            elif v.value_date is not None:
                val = v.value_date.isoformat()
            else:
                val = None
            grouped.setdefault(v.field_id, []).append(val)

        out: list[DynamicFieldValueOut] = []
        for fid, field in sorted(field_by_id.items(), key=lambda x: x[1].field_order):
            vals = [v for v in grouped.get(fid, []) if v is not None and str(v) != ""]
            # Hide dynamic fields with no value for this ticket — the ticket
            # zoom omits empty fields (and hides the panel entirely when none
            # have a value). Fields with at least one non-empty value are kept.
            if not vals:
                continue
            # Optionally resolve label overrides from YAML config blob
            label = field.label
            if field.config:
                try:
                    raw_cfg: Any = field.config
                    if isinstance(raw_cfg, bytes):
                        raw_cfg = raw_cfg.decode("utf-8", errors="replace")
                    cfg = yaml.safe_load(raw_cfg)
                    if isinstance(cfg, dict) and cfg.get("Label"):
                        label = str(cfg["Label"])
                except (yaml.YAMLError, TypeError, UnicodeError):
                    pass
            out.append(
                DynamicFieldValueOut(
                    name=field.name,
                    label=label,
                    field_type=field.field_type,
                    values=vals,
                )
            )
        return out

    async def list_articles(self, user_id: int, ticket_id: int) -> list[ArticleListItem]:
        await self._assert_ticket_ro(user_id, ticket_id)
        sender_types = {
            r.id: r.name for r in (await self._session.execute(select(ArticleSenderType))).scalars()
        }
        articles = (
            (
                await self._session.execute(
                    select(Article).where(Article.ticket_id == ticket_id).order_by(Article.id)
                )
            )
            .scalars()
            .all()
        )
        if not articles:
            return []
        article_ids = [a.id for a in articles]
        mime_rows = (
            (
                await self._session.execute(
                    select(ArticleDataMime).where(ArticleDataMime.article_id.in_(article_ids))
                )
            )
            .scalars()
            .all()
        )
        mime_by_aid = {m.article_id: m for m in mime_rows}

        out: list[ArticleListItem] = []
        for a in articles:
            m = mime_by_aid.get(a.id)
            out.append(
                ArticleListItem(
                    id=a.id,
                    ticket_id=a.ticket_id,
                    sender_type=sender_types.get(a.article_sender_type_id),
                    sender_type_id=a.article_sender_type_id,
                    communication_channel_id=a.communication_channel_id,
                    is_visible_for_customer=bool(a.is_visible_for_customer),
                    create_time=a.create_time,
                    create_by=a.create_by,
                    subject=m.a_subject if m else None,
                    from_address=m.a_from if m else None,
                    to_address=m.a_to if m else None,
                    content_type=m.a_content_type if m else None,
                    incoming_time=m.incoming_time if m else None,
                )
            )
        return out

    async def get_article_body(
        self, user_id: int, ticket_id: int, article_id: int
    ) -> RenderedArticleBody:
        await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        mime = (
            await self._session.execute(
                select(ArticleDataMime).where(ArticleDataMime.article_id == article_id)
            )
        ).scalar_one_or_none()
        body = mime.a_body if mime else None
        ct = mime.a_content_type if mime else "text/plain"
        return render_article_body(
            body=body,
            content_type=ct,
            ticket_id=ticket_id,
            article_id=article_id,
        )

    async def list_attachments(
        self, user_id: int, ticket_id: int, article_id: int
    ) -> list[AttachmentMetaOut]:
        await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article.id).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        atts = await self._storage.list_attachments(article_id)
        return [
            AttachmentMetaOut(
                id=a.id,
                article_id=a.article_id,
                filename=a.filename,
                content_type=a.content_type,
                content_size=a.content_size,
                content_id=a.content_id,
                disposition=a.disposition,
            )
            for a in atts
        ]

    async def get_attachment(
        self,
        user_id: int,
        ticket_id: int,
        article_id: int,
        attachment_id: int,
    ) -> AttachmentContent:
        await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article.id).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        content = await self._storage.get_attachment(attachment_id)
        if content is None or content.meta.article_id != article_id:
            raise TicketNotFound(attachment_id)
        return content

    async def get_attachment_by_cid(
        self,
        user_id: int,
        ticket_id: int,
        article_id: int,
        content_id: str,
    ) -> AttachmentContent:
        await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article.id).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        content = await self._storage.get_by_content_id(article_id, content_id)
        if content is None:
            raise TicketNotFound(content_id)
        return content

    async def list_history(
        self, user_id: int, ticket_id: int, *, order: str = "desc"
    ) -> list[HistoryEntry]:
        await self._assert_ticket_ro(user_id, ticket_id)
        types = {
            r.id: r.name for r in (await self._session.execute(select(TicketHistoryType))).scalars()
        }
        # login-by-user-id map so the renderer can resolve numeric ids in the
        # %% payload (e.g. OwnerUpdate) and each row can show who acted.
        logins: dict[int, str] = {
            r.id: r.login for r in (await self._session.execute(select(Users))).scalars()
        }
        order_col = TicketHistory.id.asc() if order.lower() == "asc" else TicketHistory.id.desc()
        rows = (
            (
                await self._session.execute(
                    select(TicketHistory)
                    .where(TicketHistory.ticket_id == ticket_id)
                    .order_by(order_col)
                )
            )
            .scalars()
            .all()
        )

        def _resolve(uid: int | str | None) -> str | None:
            if uid is None:
                return None
            try:
                return logins.get(int(uid))
            except (TypeError, ValueError):
                return None

        return [
            HistoryEntry(
                id=h.id,
                ticket_id=h.ticket_id,
                name=h.name,
                rendered=render_history_entry(
                    history_type=types.get(h.history_type_id),
                    name=h.name,
                    resolve_user=_resolve,
                ),
                history_type_id=h.history_type_id,
                history_type=types.get(h.history_type_id),
                article_id=h.article_id,
                owner_id=h.owner_id,
                create_time=h.create_time,
                create_by=h.create_by,
                create_by_login=logins.get(h.create_by),
            )
            for h in rows
        ]

    async def get_reply_draft(
        self, user_id: int, ticket_id: int, article_id: int, *, reply_all: bool = False
    ) -> ReplyDraftOut:
        """Build a prefilled reply draft (Re: subject, To/Cc, quoted body).

        Ports Znuny's reply behaviour (TicketSubjectBuild + TemplateGenerator
        quoting): the answer area is empty and placed ABOVE the quoted
        original. Quoting is plaintext-only (HTML bodies are down-converted);
        see ``tiqora.domain.quoting``.

        Also loads the queue signature (expanded placeholders) for a read-only
        composer preview. The signature is **not** part of ``body`` — the send
        pipeline appends it via ``prepare_outgoing_agent_email``.
        """
        from tiqora.channels.email.outbound_reply import _queue_outbound_meta
        from tiqora.channels.email.placeholder import expand_placeholders
        from tiqora.znuny.sysconfig import SysConfig

        ticket = await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        mime = (
            await self._session.execute(
                select(ArticleDataMime).where(ArticleDataMime.article_id == article_id)
            )
        ).scalar_one_or_none()

        subject = build_reply_subject(mime.a_subject if mime else None)
        # Show the same hooked subject the send path will produce so the
        # composer preview matches the outbound mail (idempotent strip-then-add
        # in prepare_outgoing_agent_email still protects against mismatch).
        sysconfig = SysConfig(self._session)
        hook_cfg = await load_subject_config(self._session, sysconfig)
        if hook_cfg.enabled and ticket.tn:
            subject = build_ticket_subject(
                subject,
                hook=hook_cfg.hook,
                divider=hook_cfg.divider,
                tn=str(ticket.tn),
                subject_format=hook_cfg.subject_format,
                add_re=False,
                add_fwd=False,
            )
        from_addr = (mime.a_from if mime else None) or None
        to_addr = from_addr
        cc: str | None = None
        if reply_all:
            # Reply-all: keep original recipients (To + Cc) as Cc, minus the
            # sender we're already replying to. Simplified vs Znuny (no full
            # address parsing / self-address stripping beyond exact match).
            extras: list[str] = []
            for field in ((mime.a_to if mime else None), (mime.a_cc if mime else None)):
                if not field:
                    continue
                for addr in field.split(","):
                    a = addr.strip()
                    if a and a != from_addr and a not in extras:
                        extras.append(a)
            cc = ", ".join(extras) or None

        raw_body = (mime.a_body if mime else None) or ""
        ct = (mime.a_content_type if mime else "text/plain") or "text/plain"
        is_html = ct.split(";", 1)[0].strip().lower() in {"text/html", "application/xhtml+xml"}
        plain = html_to_plaintext(raw_body) if is_html else raw_body
        quoted = quote_plaintext_body(plain, from_address=from_addr, sent_at=art.create_time)
        # Empty answer area above the quote (two newlines), then the quote.
        body = f"\n\n{quoted}\n"

        signature = ""
        signature_is_html = False
        queue_id = int(ticket.queue_id) if ticket.queue_id else 0
        if queue_id:
            _from, queue_name, sig_text, sig_ct = await _queue_outbound_meta(
                self._session, queue_id
            )
            if sig_text and str(sig_text).strip():
                expanded = await expand_placeholders(
                    self._session,
                    sysconfig,
                    str(sig_text),
                    ticket_id=ticket_id,
                    user_id=user_id,
                    queue_name=queue_name or "",
                    customer_subject=subject or "",
                    customer_email_lines=[],
                )
                signature = expanded
                signature_is_html = "html" in (sig_ct or "").lower()

        return ReplyDraftOut(
            to_address=to_addr,
            cc=cc,
            subject=subject,
            body=body,
            is_html=False,
            in_reply_to=(mime.a_message_id if mime else None),
            references=(mime.a_message_id if mime else None),
            signature=signature,
            signature_is_html=signature_is_html,
        )

    async def list_templates(self, user_id: int, ticket_id: int) -> list[TemplateOut]:
        """Response templates for a ticket's queue (template_type='Answer').

        Znuny join: ``queue_standard_template`` → ``standard_template`` on the
        ticket's current ``queue_id``, valid Answer templates only.

        ``<OTRS_...>`` placeholders are expanded server-side against the ticket
        context (and the acting agent) so the frontend can insert the returned
        text verbatim.
        """
        from tiqora.channels.email.placeholder import expand_placeholders
        from tiqora.znuny.sysconfig import SysConfig

        ticket = await self._assert_ticket_ro(user_id, ticket_id)
        rows = (
            (
                await self._session.execute(
                    select(StandardTemplate)
                    .join(
                        QueueStandardTemplate,
                        QueueStandardTemplate.standard_template_id == StandardTemplate.id,
                    )
                    .where(
                        QueueStandardTemplate.queue_id == ticket.queue_id,
                        StandardTemplate.template_type == "Answer",
                        StandardTemplate.valid_id == 1,
                    )
                    .order_by(StandardTemplate.name)
                )
            )
            .scalars()
            .all()
        )
        sysconfig = SysConfig(self._session)
        out: list[TemplateOut] = []
        for r in rows:
            raw = r.text or ""
            expanded = await expand_placeholders(
                self._session,
                sysconfig,
                raw,
                ticket_id=ticket_id,
                user_id=user_id,
            )
            out.append(
                TemplateOut(
                    id=r.id,
                    name=r.name,
                    text=expanded,
                    content_type=r.content_type,
                    template_type=r.template_type,
                )
            )
        return out
