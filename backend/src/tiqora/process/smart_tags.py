"""Smart tags in TransitionAction config values (``<OTRS_TICKET_Title>`` and friends).

Port of the two replacement passes every Znuny TransitionAction runs before it reads its config
(``TransitionAction/Base.pm``):

1. :func:`replace_action_config` step 1 = ``_ReplaceTicketAttributes`` (``Base.pm:339-615``):
   ``<OTRS_TICKET_*>`` from ``TicketGet(DynamicFields => 1)`` (:func:`get_condition_data`), the
   dynamic-field key / display value, and ``<OTRS_FIRST_ARTICLE_*>`` / ``<OTRS_LAST_ARTICLE_*>``.
2. step 2 = ``_ReplaceAdditionalAttributes`` (``Base.pm:636-810``): every other ``<OTRS_*>`` tag
   through ``TemplateGenerator::_Replace`` (:func:`render_tags`), with the last customer and last
   agent article as ``Data`` / ``DataAgent`` and Znuny's ``-`` for unknown tags
   (``PlaceholderContext.znuny_dash``).

Known deviations, kept on purpose:

* Znuny renders step 2 through HTML (``Frontend::RichText``: ``ToHTML`` -> ``_Replace`` ->
  ``ToAscii``, a complete HTML document for ``Body``). We render plain text, which is what a
  config with ``ContentType: text/plain`` gets.
* ``<OTRS_FIRST/LAST_ARTICLE_Body>`` is inserted as plain text; Znuny turns the config into an HTML
  quotation with the article's attachments (``Base.pm:553-605``).
* Ticket attributes outside :func:`get_condition_data` (``Age``, ``UntilTime``, ``Escalation*``,
  ``GroupID``, ``CreateBy``, ``ChangeBy``...) render empty (step 1) like an unset attribute.
* The recipient time zone / ``AddTimezoneInfo`` is not used (times stay UTC, as in Znuny's
  process path).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.email.placeholder import (
    PlaceholderContext,
    expand_placeholders,
    load_placeholder_context,
)
from tiqora.domain.dynamic_field_config import config_from_yaml
from tiqora.domain.dynamic_field_values import df_display_value, readable_history_value
from tiqora.domain.quoting import clean_subject
from tiqora.process.conditions import ConditionData, get_condition_data
from tiqora.znuny.sysconfig import SysConfig

# Base.pm:373-376, 460: the ticket tag loop gives up after 1000 rounds.
_MAX_ROUNDS = 1000
# Base.pm:375 / 395 / 419: ``[A-Za-z0-9_]+`` between ``<OTRS_TICKET_`` and ``>``, case-insensitive.
_TICKET_TAG_RE = re.compile(r"<OTRS_TICKET_([A-Za-z0-9_]+)>", re.IGNORECASE)
_DF_VALUE_RE = re.compile(r"DynamicField_(\S+?)_Value")
_DF_RE = re.compile(r"DynamicField_(\S+)")
# Base.pm:754: any remaining tag that step 2 has to render. Deliberately also matches ``:``
# (``<OTRS_CONFIG_Ticket::Hook>``): Znuny's ``[A-Za-z0-9_]+`` skips step 2 for a value whose only
# tag is a config key, which is never what the process author means.
_ANY_TAG_RE = re.compile(r"<OTRS_[A-Za-z0-9_:]+(?:\[(?:.+?)\])?>", re.IGNORECASE | re.DOTALL)

_ARTICLE_SQL = (
    "SELECT a.id, a.ticket_id, a.article_sender_type_id, a.communication_channel_id,"
    " a.is_visible_for_customer, a.create_time, a.create_by, a.change_time, a.change_by,"
    " m.a_from, m.a_reply_to, m.a_to, m.a_cc, m.a_bcc, m.a_subject, m.a_message_id,"
    " m.a_in_reply_to, m.a_references, m.a_content_type, m.a_body, m.incoming_time"
    " FROM article a JOIN article_data_mime m ON m.article_id = a.id"
    " WHERE a.ticket_id = :tid{sender} ORDER BY a.id {order} LIMIT 1"
)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _fmt(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else _s(value)


async def _load_article(
    session: AsyncSession,
    ticket_id: int,
    *,
    sender_type: str | None = None,
    first: bool = False,
) -> dict[str, str] | None:
    """One article as Znuny's ``ArticleGet`` hash (``Subject``, ``Body``, ``From``, ...), or None.

    ``sender_type`` selects the newest article of that sender type (``ArticleList(SenderType =>
    ..., OnlyLast => 1)``, Base.pm:664-690); ``first`` the oldest article (``ArticleIndex[0]``,
    Base.pm:383-389). Sender type ids are looked up first, so no string JOIN is needed.
    """
    params: dict[str, Any] = {"tid": ticket_id}
    sender_sql = ""
    if sender_type is not None:
        sender_id = (
            await session.execute(
                text("SELECT id FROM article_sender_type WHERE name = :n"), {"n": sender_type}
            )
        ).scalar_one_or_none()
        if sender_id is None:
            return None
        params["sid"] = sender_id
        sender_sql = " AND a.article_sender_type_id = :sid"
    row = (
        (
            await session.execute(
                text(_ARTICLE_SQL.format(sender=sender_sql, order="ASC" if first else "DESC")),
                params,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    sender_name = (
        await session.execute(
            text("SELECT name FROM article_sender_type WHERE id = :i"),
            {"i": row["article_sender_type_id"]},
        )
    ).scalar_one_or_none()
    return {
        "ArticleID": _s(row["id"]),
        "TicketID": _s(row["ticket_id"]),
        "SenderTypeID": _s(row["article_sender_type_id"]),
        "SenderType": _s(sender_name),
        "CommunicationChannelID": _s(row["communication_channel_id"]),
        "IsVisibleForCustomer": _s(row["is_visible_for_customer"]),
        "From": _s(row["a_from"]),
        "ReplyTo": _s(row["a_reply_to"]),
        "To": _s(row["a_to"]),
        "Cc": _s(row["a_cc"]),
        "Bcc": _s(row["a_bcc"]),
        "Subject": _s(row["a_subject"]),
        "MessageID": _s(row["a_message_id"]),
        "InReplyTo": _s(row["a_in_reply_to"]),
        "References": _s(row["a_references"]),
        "ContentType": _s(row["a_content_type"]),
        "Body": _s(row["a_body"]),
        "IncomingTime": _s(row["incoming_time"]),
        "CreateTime": _fmt(row["create_time"]),
        "CreateBy": _s(row["create_by"]),
        "ChangeTime": _fmt(row["change_time"]),
        "ChangeBy": _s(row["change_by"]),
    }


async def _clean_subject(sysconfig: SysConfig, subject: str) -> str:
    """``TicketSubjectClean`` for ``GetSubjectSnippet`` (TemplateGenerator.pm:1865-1871)."""
    return clean_subject(
        subject, hook=await sysconfig.ticket_hook(), divider=await sysconfig.ticket_hook_divider()
    )


async def build_tag_context(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
    data: ConditionData | None = None,
) -> PlaceholderContext:
    """The placeholder context Znuny's ``_ReplaceAdditionalAttributes`` hands to ``_Replace``.

    ``TicketData`` is ``TicketGet`` (so ``<OTRS_TICKET_Owner>`` is the owner's login, not the full
    name e-mail templates show); ``Data`` / ``DataAgent`` are the last customer / agent article
    (Base.pm:664-690), exposed through ``<OTRS_CUSTOMER_SUBJECT|BODY|EMAIL|<column>>`` and
    ``<OTRS_AGENT_SUBJECT|BODY|<column>>`` (TemplateGenerator.pm:1456-1470, 1500-1525).
    ``data`` may carry an already loaded :func:`get_condition_data` result.
    """
    ctx = await load_placeholder_context(session, ticket_id=ticket_id, user_id=user_id)
    ctx.znuny_dash = True
    if data is None:
        data = await get_condition_data(session, ticket_id)
    for key, value in data.items():
        if isinstance(value, str):
            ctx.ticket[key.lower()] = value
    ctx.ticket["id"] = _s(data.get("TicketID"))
    ctx.ticket["number"] = _s(data.get("TicketNumber"))

    customer = await _load_article(session, ticket_id, sender_type="customer")
    if customer is not None:
        ctx.customer_subject = await _clean_subject(sysconfig, customer["Subject"])
        ctx.customer_body = customer["Body"]
        ctx.customer_email_lines = customer["Body"].splitlines()
        ctx.customer.update({k.lower(): v for k, v in customer.items()})
    agent = await _load_article(session, ticket_id, sender_type="agent")
    if agent is not None:
        ctx.agent_subject = await _clean_subject(sysconfig, agent["Subject"])
        ctx.agent_body = agent["Body"]
        ctx.current_user.update({k.lower(): v for k, v in agent.items()})
    return ctx


async def render_tags(text_: str, *, ctx: PlaceholderContext, sysconfig: SysConfig) -> str:
    """``TemplateGenerator::_Replace`` over *text_* with an already built context."""
    return await expand_placeholders(None, sysconfig, text_, context=ctx)


async def render_ticketless(
    text_: str,
    *,
    session: AsyncSession,
    user_id: int,
    sysconfig: SysConfig,
    values: Mapping[str, str],
) -> str:
    """Render tags for a ticket that does not exist yet (the title of a process ticket).

    Znuny renders it with ``TicketData`` = the submitted fields plus the resolved
    ``Queue`` / ``State`` / ``Priority`` / ``Lock`` names (AgentTicketProcess.pm:5183-5195);
    *values* is that map (keys case-insensitive). Only ``<OTRS_TICKET_*>`` and the acting
    agent's ``<OTRS_CURRENT_*>`` tags have data.
    """
    ctx = await load_placeholder_context(
        session, ticket_id=None, user_id=user_id, ticket=dict(values)
    )
    ctx.znuny_dash = True
    return await render_tags(text_, ctx=ctx, sysconfig=sysconfig)


async def _load_df(session: AsyncSession, name: str) -> tuple[str, dict[str, Any]] | None:
    """``DynamicFieldGet(Name => ...)``: field type and parsed config (any object type)."""
    row = (
        await session.execute(
            text("SELECT field_type, config FROM dynamic_field WHERE name = :n LIMIT 1"),
            {"n": name},
        )
    ).first()
    if row is None:
        return None
    return str(row[0]), config_from_yaml(row[1])


def _replace_tag(value: str, tag: str, replacement: str) -> str:
    return re.sub(re.escape(tag), lambda _m: replacement, value, flags=re.IGNORECASE)


async def _replace_ticket_attributes(
    session: AsyncSession,
    config: dict[str, Any],
    data: ConditionData,
    *,
    first: dict[str, str] | None,
    last: dict[str, str] | None,
    article_count_one: bool,
) -> None:
    """Step 1, ``_ReplaceTicketAttributes`` (Base.pm:407-612). Mutates *config*."""
    for attribute in sorted(config):
        value = config[attribute]
        if not isinstance(value, str):
            continue  # Perl would stringify; only strings can carry a tag
        rounds = 0
        while value and (match := _TICKET_TAG_RE.search(value)) and rounds < _MAX_ROUNDS:
            rounds += 1
            ticket_attribute = match.group(1)
            tag = match.group(0)
            display_match = _DF_VALUE_RE.search(ticket_attribute)
            df_match = display_match or _DF_RE.search(ticket_attribute)
            if df_match is not None:
                name = df_match.group(1)
                found = await _load_df(session, name)
                if found is None:
                    # Base.pm:421: ``next REPLACEMENT`` re-matches the same tag until the round
                    # cap; the tag is left for step 2 (TemplateGenerator -> "-").
                    break
                field_type, df_config = found
                stored = data.get(f"DynamicField_{name}")
                keys = [stored] if isinstance(stored, str) else list(stored or [])
                rendered = (
                    df_display_value(field_type, df_config, keys)
                    if display_match is not None
                    else readable_history_value(field_type, keys)
                )
                value = _replace_tag(value, tag, rendered)
                continue
            # Base.pm:450-466: a scalar replaces every instance; a list replaces the whole value.
            ticket_value = data.get(ticket_attribute)
            if isinstance(ticket_value, list):
                config[attribute] = list(ticket_value)
                value = ""
                break
            value = _replace_tag(value, tag, _s(ticket_value))
        if isinstance(config[attribute], list):
            continue  # a list attribute replaced the whole value
        config[attribute] = (
            _replace_article_tags(
                attribute, value, first=first, last=last, article_count_one=article_count_one
            )
            if value
            else value
        )


def _replace_article_tags(
    attribute: str,
    value: str,
    *,
    first: dict[str, str] | None,
    last: dict[str, str] | None,
    article_count_one: bool,
) -> str:
    """``<OTRS_FIRST_ARTICLE_*>`` / ``<OTRS_LAST_ARTICLE_*>`` (Base.pm:468-612), plain text."""
    for article_type, article in (("FIRST", first), ("LAST", last)):
        if not article:
            continue
        for article_attribute in sorted(article):
            current = f"<OTRS_{article_type}_ARTICLE_{article_attribute}>"
            article_value = article[article_attribute] or ""
            if attribute == "Body" and not article_value:
                article_value = " "  # Base.pm:490: an article without body text
            if not article_value:
                continue
            if (
                article_attribute == "Body"
                and current.lower() in value.lower()
                and article_count_one
                and article_type == "LAST"
            ):
                # Base.pm:517-526: LAST_ARTICLE_Body of a one-article ticket is dropped and the
                # remaining LAST attributes are skipped (``next TAG``).
                return _replace_tag(value, current, "")
            value = _replace_tag(value, current, article_value)
    return value


async def replace_action_config(
    config: Mapping[str, Any],
    *,
    session: AsyncSession,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> dict[str, Any]:
    """A deep copy of *config* with every smart tag in its top-level string values replaced.

    Step 1 (``_ReplaceTicketAttributes``) then step 2 (``_ReplaceAdditionalAttributes``, only for
    values that still contain an ``<OTRS_...>`` tag); non-string values are untouched. A
    dynamic-field tag yields the field's readable value (a Multiselect ``"a, b"``; the
    DynamicFieldSet action splits it again, ``_ConvertScalar2ArrayRef``, Base.pm:826-840).
    """
    out: dict[str, Any] = copy.deepcopy(dict(config))
    if not any(isinstance(v, str) and "<otrs_" in v.lower() for v in out.values()):
        return out
    data = await get_condition_data(session, ticket_id)
    first = await _load_article(session, ticket_id, first=True)
    last = await _load_article(session, ticket_id)
    count_one = first is not None and last is not None and first["ArticleID"] == last["ArticleID"]
    await _replace_ticket_attributes(
        session, out, data, first=first, last=last, article_count_one=count_one
    )

    pending = [k for k in sorted(out) if isinstance(out[k], str) and _ANY_TAG_RE.search(out[k])]
    if pending:
        ctx = await build_tag_context(
            session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig, data=data
        )
        for key in pending:
            out[key] = await render_tags(out[key], ctx=ctx, sysconfig=sysconfig)
    return out


def split_scalar_list(value: str) -> list[str]:
    """``_ConvertScalar2ArrayRef`` (Base.pm:826-840): split on ``,`` and trim each item."""
    return [item.strip() for item in value.split(",")]


__all__ = [
    "build_tag_context",
    "render_tags",
    "render_ticketless",
    "replace_action_config",
    "split_scalar_list",
]
