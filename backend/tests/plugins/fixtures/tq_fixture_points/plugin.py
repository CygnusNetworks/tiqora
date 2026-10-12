"""Fixture plugin ``org.example.points``: one ``_register_<point>`` function per extension point."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text

from tiqora_sdk import PluginRegistry
from tiqora_sdk.fields import DfBaseType
from tiqora_sdk.hooks import (
    HookContext,
    OperationVetoed,
    SyncHandler,
    SyncOperation,
    TicketCreatePatch,
)
from tiqora_sdk.mail import (
    InboundMail,
    InboundMailResult,
    MailFilterContext,
    MailVetoed,
    OutboundMail,
)
from tiqora_sdk.notifications import NotificationMessage, TransportContext
from tiqora_sdk.process import ActionContext
from tiqora_sdk.templates import NumberRequest, PlaceholderRequest

from . import behaviour

_SYNC_OPERATIONS: tuple[SyncOperation, ...] = (
    "ticket.create",
    "article.create",
    "ticket.queue_move",
    "ticket.state_change",
    "ticket.dynamic_fields_update",
)


def _sync_handler(operation: SyncOperation) -> SyncHandler:
    async def handler(ctx: HookContext, op: Any) -> TicketCreatePatch | None:
        behaviour.CALLS.append((f"hook.{operation}", op))
        mode = behaviour.MODE.get(f"hook.{operation}", "ok")
        if mode == "veto":
            raise OperationVetoed("Vertragsnummer fehlt")
        if mode == "hang":
            await asyncio.sleep(30)
        if mode == "raise":
            raise RuntimeError("kaputt")
        if mode == "enrich":
            return TicketCreatePatch(title="Angereichert")
        if mode == "enrich_df":  # title prefix + a dynamic field, built from the op
            return TicketCreatePatch(
                title=f"[geprüft] {op.title}", dynamic_fields={"PP55Flag": ["1"]}
            )
        if mode == "bad_priority":
            return TicketCreatePatch(priority="gibt-es-nicht")
        if mode == "bad_shape_scalar":
            return TicketCreatePatch(dynamic_fields={"X": 5})  # type: ignore[dict-item]
        if mode == "bad_shape_list":
            return TicketCreatePatch(dynamic_fields=["x"])  # type: ignore[arg-type]
        return None

    return handler


def _register_sync_hooks(registry: PluginRegistry) -> None:
    """One hook per operation; behaviour by ``MODE["hook.<operation>"]``."""
    for operation in _SYNC_OPERATIONS:
        registry.before(
            operation, operation.replace(".", "_"), _sync_handler(operation), timeout_ms=100
        )


async def _set_contract(ac: ActionContext) -> None:
    behaviour.CALLS.append(("action.SetContract", dict(ac.config)))
    await ac.plugin.tickets.set_dynamic_field(
        ac.ticket_id, "PP55Contract", [str(ac.config["Contract"])], session=ac.session
    )
    mode = behaviour.MODE.get("action", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "hang":
        await asyncio.sleep(30)
    if mode == "slow":  # succeeds, but takes a fixed time (shared-budget chain test)
        await asyncio.sleep(0.15)
    if mode == "db_hang":  # a long statement on the engine session (cancelled by the timeout)
        dialect = ac.session.get_bind().dialect.name
        sql = "SELECT SLEEP(5)" if dialect == "mysql" else "SELECT pg_sleep(5)"
        await ac.session.execute(text(sql))


def _is_vip(data: Mapping[str, str | list[str] | None], field: str) -> bool:
    mode = behaviour.MODE.get("cond", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "nonbool":
        return "yes"  # type: ignore[return-value]
    return data.get("CustomerID") == "pp55-vip"


def _register_process(registry: PluginRegistry) -> None:
    """Action ``SetContract`` and condition ``IsVip``; behaviour by ``MODE["action"|"cond"]``."""
    registry.transition_action("SetContract", _set_contract, timeout_seconds=2)
    registry.condition_module("IsVip", _is_vip)


async def _greeting(req: PlaceholderRequest) -> str:
    behaviour.CALLS.append(("placeholder.GREETING", req))
    return f"Hallo aus Ticket {req.ticket_id}"


async def _slow(req: PlaceholderRequest) -> str:
    behaviour.CALLS.append(("placeholder.SLOW", req))
    await asyncio.sleep(5)
    return "spät"


async def _broken(req: PlaceholderRequest) -> str:
    behaviour.CALLS.append(("placeholder.BROKEN", req))
    raise RuntimeError("kaputt")


async def _htmlish(req: PlaceholderRequest) -> str:
    return "<b>&</b>"


async def _whoami(req: PlaceholderRequest) -> str:
    behaviour.CALLS.append(("placeholder.WHOAMI", req))
    return f"{req.recipient.get('useremail', '-')}|{req.bracket}|{req.time_zone}"


def _register_placeholders(registry: PluginRegistry) -> None:
    """``GREETING``, ``SLOW`` (sleeps 5 s), ``BROKEN`` (raises), ``HTMLISH``, ``WHOAMI``."""
    registry.placeholder("GREETING", _greeting)
    registry.placeholder("SLOW", _slow, timeout_ms=100)
    registry.placeholder("BROKEN", _broken)
    registry.placeholder("HTMLISH", _htmlish)
    registry.placeholder("WHOAMI", _whoami)


def _yearly(req: NumberRequest) -> str:
    behaviour.CALLS.append(("number.Yearly", req))
    return f"{req.now_local:%Y}-{req.counter:06d}"


def _daily(req: NumberRequest) -> str:
    return f"{req.now_local:%Y%m%d}-{req.counter:04d}"


def _broken_number(req: NumberRequest) -> str:
    """``MODE["tn"]``: raise | empty | long | nomatch | hang | same (``MODE["tn.same"]``)."""
    behaviour.CALLS.append(("number.Broken", req))
    mode = behaviour.MODE.get("tn", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "empty":
        return ""
    if mode == "long":
        return "BRK-" + "9" * 56
    if mode == "nomatch":
        return "NOPE-1"
    if mode == "same":
        return behaviour.MODE["tn.same"]
    if mode == "hang":
        import time

        time.sleep(3)
    return f"BRK-{req.offset + 1:06d}"


def _register_number_generators(registry: PluginRegistry) -> None:
    """``Yearly`` (counter, no date reset), ``Daily`` (counter, resets daily), ``Broken``."""
    registry.number_generator(
        "Yearly", _yearly, label="Jahresnummer", pattern=r"\d{4}-\d{6}", uses_counter=True
    )
    registry.number_generator(
        "Daily",
        _daily,
        label="Tagesnummer",
        pattern=r"\d{8}-\d{4}",
        uses_counter=True,
        date_based=True,
    )
    registry.number_generator(
        "Broken", _broken_number, label="Defekt", pattern=r"BRK-\d{6}", uses_counter=False
    )


class ContractStyle(Enum):
    PLAIN = "plain"
    BOLD = "bold"


class ContractConfig(BaseModel):
    prefix: str = "V-"
    style: ContractStyle = ContractStyle.PLAIN


_CONTRACT_RE = re.compile(r"V-\d{1,6}")


def _validate_contract(values: list[str], config: Mapping[str, Any]) -> list[str]:
    """``MODE["df"]``: raise (RuntimeError) | hang (3 s) | badresult (not a list of str)."""
    behaviour.CALLS.append(("df.validate", (list(values), dict(config))))
    mode = behaviour.MODE.get("df", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "hang":
        import time

        time.sleep(3)
    if mode == "badresult":
        return 5  # type: ignore[return-value]
    out: list[str] = []
    for value in values:
        upper = value.upper()
        if not _CONTRACT_RE.fullmatch(upper):
            raise ValueError("Vertragsnummer muss wie V-123 aussehen")
        out.append(upper)
    return out


def _display_contract(values: list[str], config: Mapping[str, Any]) -> str:
    behaviour.CALLS.append(("df.display", (list(values), dict(config))))
    mode = behaviour.MODE.get("df.display")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "hang":
        import time

        time.sleep(3)
    return ", ".join(f"Vertrag {v}" for v in values)


def _register_df_types(registry: PluginRegistry) -> None:
    """``Contract`` (base Text): ``V-<digits>``, upper-cased, shown as "Vertrag V-12"."""
    base: DfBaseType = "Text"
    registry.dynamic_field_type(
        "Contract",
        label="Vertragsnummer",
        base_type=base,
        validate=_validate_contract,
        display=_display_contract,
        config_model=ContractConfig,
    )


async def _tagger(ctx: MailFilterContext, mail: InboundMail) -> None:
    """``pre``: a subject with DRINGEND gets priority 5."""
    behaviour.CALLS.append(("mail.pre.tagger", mail.subject))
    if "DRINGEND" in mail.subject:
        mail.headers["X-OTRS-Priority"] = "5 very high"


async def _spam(ctx: MailFilterContext, mail: InboundMail) -> None:
    """``pre``: mail from @spam.example.net is dropped."""
    if mail.from_address.endswith("@spam.example.net"):
        mail.headers["X-OTRS-Ignore"] = "yes"


async def _broken_pre(ctx: MailFilterContext, mail: InboundMail) -> None:
    """``pre``, ``MODE["mail.pre"]``: raise (after dropping the mail) | hang."""
    mode = behaviour.MODE.get("mail.pre", "ok")
    if mode == "raise":
        mail.headers["X-OTRS-Ignore"] = "yes"  # must be undone: the filter failed
        raise RuntimeError("kaputt")
    if mode == "hang":
        mail.headers["X-OTRS-Ignore"] = "yes"
        await asyncio.sleep(30)


async def _recorder(ctx: MailFilterContext, result: InboundMailResult) -> None:
    """``post``: records ``(outcome, ticket_id)`` and the ticket title read through the SDK."""
    behaviour.CALLS.append(("mail.post", (result.outcome, result.ticket_id)))
    if result.ticket_id is not None:
        ticket = await ctx.plugin.tickets.get(result.ticket_id)
        behaviour.CALLS.append(("mail.post.title", None if ticket is None else ticket.title))
    behaviour.CALLS.append(("mail.post.headers", dict(result.headers)))
    if behaviour.MODE.get("mail.post") == "raise":
        raise RuntimeError("kaputt")


def _register_mail_filters(registry: PluginRegistry) -> None:
    """Inbound: ``tagger``, ``spam``, ``broken`` (pre) and ``recorder`` (post)."""
    registry.mail_filter("tagger", "pre", _tagger, order=100)
    registry.mail_filter("spam", "pre", _spam, order=110)
    registry.mail_filter("broken", "pre", _broken_pre, order=120, timeout_seconds=1)
    registry.mail_filter("recorder", "post", _recorder, order=100, timeout_seconds=2)


async def _stamp(ctx: MailFilterContext, mail: OutboundMail) -> None:
    """``outbound``: every mail gets ``X-Points-Checked: 1``."""
    behaviour.CALLS.append(("mail.out.stamp", mail.subject))
    mail.add_header("X-Points-Checked", "1")


async def _guard(ctx: MailFilterContext, mail: OutboundMail) -> None:
    """``outbound``: a recipient at @blocked.example.net stops the send."""
    if any(a.endswith("@blocked.example.net") for a in (*mail.to, *mail.cc)):
        raise MailVetoed("Empfänger gesperrt")


async def _broken_out(ctx: MailFilterContext, mail: OutboundMail) -> None:
    """``outbound``, ``MODE["mail.out"]``: raise | hang | bad_header (after a valid header)."""
    mode = behaviour.MODE.get("mail.out", "ok")
    if mode == "raise":
        mail.add_header("X-Broken", "1")  # must not reach the mail: the filter failed
        raise RuntimeError("kaputt")
    if mode == "hang":
        mail.add_header("X-Broken", "1")
        await asyncio.sleep(30)
    if mode == "bad_header":
        mail.add_header("X-Broken", "1")
        mail.add_header("Subject", "x")  # ValueError: only X- headers


async def _broken_out_late(ctx: MailFilterContext, mail: OutboundMail) -> None:
    """``outbound``, second hanging filter (for the shared budget)."""
    if behaviour.MODE.get("mail.out") == "hang":
        await asyncio.sleep(30)


def _register_outbound(registry: PluginRegistry) -> None:
    registry.mail_filter("stamp", "outbound", _stamp, order=100)
    registry.mail_filter("guard", "outbound", _guard, order=110)
    registry.mail_filter("broken_out", "outbound", _broken_out, order=120, timeout_seconds=1)
    registry.mail_filter("broken_late", "outbound", _broken_out_late, order=130, timeout_seconds=2)


async def _chat(ctx: TransportContext, message: NotificationMessage) -> None:
    """``MODE["transport"]``: raise | hang."""
    behaviour.CALLS.append(
        ("transport.Chat", (message.recipient.kind, message.recipient.email, message.subject))
    )
    behaviour.CALLS.append(("transport.Chat.message", message))
    mode = behaviour.MODE.get("transport", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "hang":
        await asyncio.sleep(30)


def _register_transports(registry: PluginRegistry) -> None:
    registry.notification_transport("Chat", _chat, label="Chat (Test)", timeout_seconds=1)


class PointsPlugin:
    def register(self, registry: PluginRegistry) -> None:
        # Each task of the plugin-points plan adds its ``_register_<point>(registry)`` call here.
        _register_sync_hooks(registry)
        _register_process(registry)
        _register_placeholders(registry)
        _register_number_generators(registry)
        _register_df_types(registry)
        _register_mail_filters(registry)
        _register_outbound(registry)
        _register_transports(registry)


plugin = PointsPlugin()
