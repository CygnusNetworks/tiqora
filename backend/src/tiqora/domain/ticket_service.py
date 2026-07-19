"""Read-only ticket, article, attachment, and history access."""

from __future__ import annotations

from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.article import (
    Article,
    ArticleDataMime,
    ArticleSenderType,
)
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import (
    Ticket,
    TicketHistory,
    TicketHistoryType,
    TicketLockType,
    TicketPriority,
    TicketState,
    TicketStateType,
)
from tiqora.db.legacy.user import Users
from tiqora.domain.article_html import RenderedArticleBody, render_article_body
from tiqora.domain.queue_service import age_seconds
from tiqora.domain.schemas import (
    ArticleListItem,
    AttachmentMetaOut,
    DynamicFieldValueOut,
    HistoryEntry,
    PaginatedTickets,
    TicketDetail,
    TicketListItem,
)
from tiqora.permissions.engine import PermissionEngine
from tiqora.storage.backend import AttachmentContent, DbMimeStorage


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
            r.id: r.name
            for r in (await self._session.execute(select(TicketState))).scalars()
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
            r.id: r.name
            for r in (await self._session.execute(select(TicketPriority))).scalars()
        }
        locks = {
            r.id: r.name
            for r in (await self._session.execute(select(TicketLockType))).scalars()
        }
        queues = {
            r.id: r.name for r in (await self._session.execute(select(Queue))).scalars()
        }
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
        allowed_groups = await self._perms.groups_for_permission(user_id, "ro")
        if not allowed_groups:
            return PaginatedTickets(items=[], total=0, offset=offset, limit=limit)

        q_ids_result = await self._session.execute(
            select(Queue.id).where(Queue.group_id.in_(allowed_groups), Queue.valid_id == 1)
        )
        allowed_queues = set(q_ids_result.scalars().all())
        if not allowed_queues:
            return PaginatedTickets(items=[], total=0, offset=offset, limit=limit)

        if queue_id is not None:
            if queue_id not in allowed_queues:
                return PaginatedTickets(items=[], total=0, offset=offset, limit=limit)
            filter_queues = {queue_id}
        else:
            filter_queues = allowed_queues

        stmt = select(Ticket).where(
            Ticket.queue_id.in_(filter_queues),
            Ticket.archive_flag == 0,
        )
        count_stmt = (
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.queue_id.in_(filter_queues), Ticket.archive_flag == 0)
        )

        if state_id is not None:
            stmt = stmt.where(Ticket.ticket_state_id == state_id)
            count_stmt = count_stmt.where(Ticket.ticket_state_id == state_id)
        if owner_id is not None:
            stmt = stmt.where(Ticket.user_id == owner_id)
            count_stmt = count_stmt.where(Ticket.user_id == owner_id)
        if state_type is not None:
            state_ids = (
                await self._session.execute(
                    select(TicketState.id)
                    .join(TicketStateType, TicketStateType.id == TicketState.type_id)
                    .where(TicketStateType.name == state_type)
                )
            ).scalars().all()
            if not state_ids:
                return PaginatedTickets(items=[], total=0, offset=offset, limit=limit)
            stmt = stmt.where(Ticket.ticket_state_id.in_(state_ids))
            count_stmt = count_stmt.where(Ticket.ticket_state_id.in_(state_ids))

        sort_col = {
            "age": Ticket.create_time,
            "created": Ticket.create_time,
            "changed": Ticket.change_time,
            "tn": Ticket.tn,
            "title": Ticket.title,
            "priority": Ticket.ticket_priority_id,
        }.get(sort, Ticket.create_time)
        if order.lower() == "asc":
            stmt = stmt.order_by(sort_col.asc())
        else:
            stmt = stmt.order_by(sort_col.desc())

        total = int((await self._session.execute(count_stmt)).scalar_one())
        result = await self._session.execute(stmt.offset(offset).limit(min(limit, 200)))
        tickets = list(result.scalars().all())
        maps = await self._lookup_maps()
        items = [self._to_list_item(t, maps) for t in tickets]
        return PaginatedTickets(items=items, total=total, offset=offset, limit=limit)

    async def get_ticket(self, user_id: int, ticket_id: int) -> TicketDetail:
        ticket = await self._assert_ticket_ro(user_id, ticket_id)
        maps = await self._lookup_maps()
        base = self._to_list_item(ticket, maps)
        dfs = await self._load_dynamic_fields(ticket.id)
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
        )

    async def _load_dynamic_fields(self, ticket_id: int) -> list[DynamicFieldValueOut]:
        fields = (
            await self._session.execute(
                select(DynamicField).where(
                    DynamicField.object_type == "Ticket",
                    DynamicField.valid_id == 1,
                )
            )
        ).scalars().all()
        if not fields:
            return []
        field_by_id = {f.id: f for f in fields}
        values = (
            await self._session.execute(
                select(DynamicFieldValue).where(
                    DynamicFieldValue.object_id == ticket_id,
                    DynamicFieldValue.field_id.in_(field_by_id.keys()),
                )
            )
        ).scalars().all()
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
            vals = [v for v in grouped.get(fid, []) if v is not None]
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
            r.id: r.name
            for r in (await self._session.execute(select(ArticleSenderType))).scalars()
        }
        articles = (
            await self._session.execute(
                select(Article).where(Article.ticket_id == ticket_id).order_by(Article.id)
            )
        ).scalars().all()
        if not articles:
            return []
        article_ids = [a.id for a in articles]
        mime_rows = (
            await self._session.execute(
                select(ArticleDataMime).where(ArticleDataMime.article_id.in_(article_ids))
            )
        ).scalars().all()
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
                select(Article.id).where(
                    Article.id == article_id, Article.ticket_id == ticket_id
                )
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
                select(Article.id).where(
                    Article.id == article_id, Article.ticket_id == ticket_id
                )
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
                select(Article.id).where(
                    Article.id == article_id, Article.ticket_id == ticket_id
                )
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        content = await self._storage.get_by_content_id(article_id, content_id)
        if content is None:
            raise TicketNotFound(content_id)
        return content

    async def list_history(self, user_id: int, ticket_id: int) -> list[HistoryEntry]:
        await self._assert_ticket_ro(user_id, ticket_id)
        types = {
            r.id: r.name
            for r in (await self._session.execute(select(TicketHistoryType))).scalars()
        }
        rows = (
            await self._session.execute(
                select(TicketHistory)
                .where(TicketHistory.ticket_id == ticket_id)
                .order_by(TicketHistory.id)
            )
        ).scalars().all()
        return [
            HistoryEntry(
                id=h.id,
                ticket_id=h.ticket_id,
                name=h.name,
                history_type_id=h.history_type_id,
                history_type=types.get(h.history_type_id),
                article_id=h.article_id,
                owner_id=h.owner_id,
                create_time=h.create_time,
                create_by=h.create_by,
            )
            for h in rows
        ]
