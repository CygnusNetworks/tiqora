"""Fixture plugin ``org.example.points``: one ``_register_<point>`` function per extension point."""

from __future__ import annotations

import asyncio
import dataclasses
import re
from collections.abc import Mapping
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import column, table, text

from tiqora_sdk import PluginRegistry
from tiqora_sdk.auth import AuthContext, PasswordCheck
from tiqora_sdk.channels import (
    ChannelCapabilities,
    ChannelContext,
    DeliveryResult,
    InboundChannelMessage,
    OutboundChannelMessage,
)
from tiqora_sdk.customers import CustomerSourceContext, ExternalCompany, ExternalCustomer
from tiqora_sdk.fields import DfBaseType
from tiqora_sdk.gi import GiOperationContext, GiPrincipal, InvokerContext, InvokerResponse
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
from tiqora_sdk.reporting import (
    CompileContext,
    Dimension,
    Measure,
    ReportDefinition,
    ReportSource,
)
from tiqora_sdk.templates import NumberRequest, PlaceholderRequest
from tiqora_sdk.tickets import PluginWriteError

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
        if mode == "veto_listed":  # only the tickets in behaviour.VETO_TICKETS
            ticket_id = op.ticket.id if hasattr(op, "ticket") else getattr(op, "ticket_id", None)
            if ticket_id in behaviour.VETO_TICKETS:
                raise OperationVetoed(f"Ticket {ticket_id}: Vertragsnummer fehlt")
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
        if mode == "enrich_bad_df":  # two values for the single-valued Text field PP55Flag
            return TicketCreatePatch(dynamic_fields={"PP55Flag": ["1", "2"]})
        if mode == "enrich_unknown_df":
            return TicketCreatePatch(dynamic_fields={"PP55GibtEsNicht": ["1"]})
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
    # MODE["as_agent"]: write as the acting agent (the write then runs the before-hooks)
    await ac.plugin.tickets.set_dynamic_field(
        ac.ticket_id,
        "PP55Contract",
        [str(ac.config["Contract"])],
        as_agent=ac.user_id if behaviour.MODE.get("as_agent") else None,
        session=ac.session,
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


async def _send(ctx: ChannelContext, message: OutboundChannelMessage) -> DeliveryResult:
    """``MODE["channel.send"]``: raise | hang (Task 14)."""
    behaviour.CALLS.append(("channel.Chat.send", message))
    mode = behaviour.MODE.get("channel.send", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "hang":
        await asyncio.sleep(30)
    return DeliveryResult(external_id="m-1")


class InboundBody(BaseModel):
    sender: str
    body: str
    external_id: str | None = None
    customer_user: str | None = None
    sender_display: str | None = None
    queue: str | None = None
    channel: str = "Chat"


def _register_channels(registry: PluginRegistry) -> None:
    """Channel ``Chat`` (500 characters) and the public inbound route ``POST /inbound``."""
    registry.channel(
        "Chat", label="Chat (Test)", capabilities=ChannelCapabilities(max_length=500), send=_send
    )
    ctx = registry.context
    router = APIRouter()

    @router.post("/inbound")
    async def inbound(payload: InboundBody) -> dict[str, Any]:
        assert ctx.channels is not None
        try:
            result = await ctx.channels.ingest(
                payload.channel,
                InboundChannelMessage(
                    sender=payload.sender,
                    body=payload.body,
                    external_id=payload.external_id,
                    customer_user=payload.customer_user,
                    sender_display=payload.sender_display,
                    queue=payload.queue,
                ),
            )
        except PluginWriteError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        behaviour.CALLS.append(("channel.Chat.ingest", result))
        return {
            "ticket_id": result.ticket_id,
            "article_id": result.article_id,
            "created": result.created,
            "duplicate": result.duplicate,
        }

    registry.route(router, public=True)


async def _directory(ctx: AuthContext, check: PasswordCheck) -> str | None:
    """Provider ``directory`` (both audiences): password ``richtig-<login>`` is right.

    ``MODE["auth"]``: raise | raise_with_password | hang | other_login | unknown_login |
    invalid_login. The call log never holds the password.
    """
    behaviour.CALLS.append(("auth.directory", (check.login, check.user_kind, check.client_ip)))
    mode = behaviour.MODE.get("auth", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "raise_with_password":
        raise RuntimeError(f"Verzeichnis lehnt {check.password} ab")
    if mode == "hang":
        await asyncio.sleep(30)
    if mode == "other_login":
        return "pp55-agent2"
    if mode == "unknown_login":
        return "gibt-es-nicht"
    if mode == "invalid_login":
        return "pp55-invalid"
    return check.login if check.password == f"richtig-{check.login}" else None


async def _backup(ctx: AuthContext, check: PasswordCheck) -> str | None:
    """Second provider (both): says no; ``MODE["auth.backup"]``: hang | other_login (names
    ``pp55-agent2``) | typed_login (names the typed login)."""
    behaviour.CALLS.append(("auth.backup", (check.login, check.user_kind)))
    mode = behaviour.MODE.get("auth.backup")
    if mode == "hang":
        await asyncio.sleep(30)
    if mode == "other_login":
        return "pp55-agent2"
    if mode == "typed_login":
        return check.login
    return None


async def _customers_only(ctx: AuthContext, check: PasswordCheck) -> str | None:
    """Customer-only provider: must never be asked on the agent login."""
    behaviour.CALLS.append(("auth.customers_only", (check.login, check.user_kind)))
    return None


def _register_auth(registry: PluginRegistry) -> None:
    registry.password_provider(
        "directory", _directory, audience="both", order=100, timeout_seconds=1
    )
    registry.password_provider("backup", _backup, audience="both", order=110, timeout_seconds=1)
    registry.password_provider(
        "customers_only", _customers_only, audience="customer", order=120, timeout_seconds=1
    )


_DIRECTORY = (
    ExternalCustomer("pp55-ext1", "pp55-ext1@example.org", "Erika", "Extern"),
    ExternalCustomer(
        "pp55-ext2",
        "pp55-ext2@example.org",
        "Emil",
        "Extern",
        company=ExternalCompany("PP55-FIRMA", "Beispiel GmbH", city="Musterstadt"),
    ),
    ExternalCustomer("pp55-ext3", "pp55-ext3@example.org", "Elsa", "Extern"),
)


async def _cs_gate(what: str) -> None:
    behaviour.CALLS.append((f"customers.{what}", None))
    mode = behaviour.MODE.get("cs")
    if mode == "raise":
        raise RuntimeError("Verzeichnis kaputt")
    if mode == "hang":
        await asyncio.sleep(30)


async def _cs_search(ctx: CustomerSourceContext, q: str, limit: int) -> list[ExternalCustomer]:
    await _cs_gate("search")
    needle = q.lower()
    return [c for c in _DIRECTORY if needle in c.login.lower() or needle in c.email.lower()][:limit]


async def _cs_by_login(ctx: CustomerSourceContext, login: str) -> ExternalCustomer | None:
    await _cs_gate("by_login")
    return next((c for c in _DIRECTORY if c.login == login), None)


async def _cs_by_email(ctx: CustomerSourceContext, email: str) -> ExternalCustomer | None:
    await _cs_gate("by_email")
    return next((c for c in _DIRECTORY if c.email.lower() == email.lower()), None)


def _register_customer_source(registry: PluginRegistry) -> None:
    registry.customer_source(
        "directory",
        label="Beispielverzeichnis",
        search=_cs_search,
        by_login=_cs_by_login,
        by_email=_cs_by_email,
        materialize_on_inbound=True,
        timeout_seconds=1,
    )


async def _contract_get(ctx: GiOperationContext, data: dict[str, Any]) -> dict[str, Any]:
    behaviour.CALLS.append(("gi.op", (ctx.webservice, ctx.operation, dict(data))))
    mode = behaviour.MODE.get("gi.op", "ok")
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "hang":
        await asyncio.sleep(30)
    if mode == "nondict":
        return ["nicht", "ok"]  # type: ignore[return-value]
    principal = await ctx.authenticate(data)
    if not isinstance(principal, GiPrincipal):
        return principal
    return {"Success": 1, "Data": {"Contract": "V-1", "UserID": principal.user_id}}


class _UpperMapper:
    def map(self, data: Any, data_include: Any = None) -> Any:
        if behaviour.MODE.get("gi.map") == "raise":
            raise RuntimeError("kaputt")
        return _upper(data)


def _upper(value: Any) -> Any:
    if isinstance(value, str):
        return value.upper()
    if isinstance(value, dict):
        return {k: _upper(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_upper(v) for v in value]
    return value


def _upper_factory(config: dict[str, Any] | None) -> _UpperMapper:
    if behaviour.MODE.get("gi.map") == "factory_raise":
        raise RuntimeError("Konfiguration kaputt")
    return _UpperMapper()


async def _contract_sync_prepare(
    ctx: InvokerContext, data: dict[str, Any]
) -> dict[str, Any] | None:
    behaviour.CALLS.append(("gi.inv.prepare", (ctx.invoker, ctx.ticket_id, dict(data))))
    mode = behaviour.MODE.get("gi.inv", "ok")
    if mode == "skip":
        return None
    if mode == "raise":
        raise RuntimeError("kaputt")
    if mode == "hang":
        await asyncio.sleep(30)
    return {"TicketID": data["TicketID"], "Hello": "Welt"}


async def _contract_sync_response(ctx: InvokerContext, response: InvokerResponse) -> None:
    behaviour.CALLS.append(("gi.inv.response", (response.success, response.data)))
    if behaviour.MODE.get("gi.inv.response") == "raise":
        raise RuntimeError("kaputt")


def _register_gi(registry: PluginRegistry) -> None:
    registry.gi_operation("ContractGet", _contract_get, timeout_seconds=1)
    registry.gi_mapping("Upper", _upper_factory)
    registry.gi_invoker(
        "ContractSync", _contract_sync_prepare, _contract_sync_response, timeout_seconds=1
    )


_LABELS = "plugin.org.example.points:reports"
_ARTICLE = table("article", column("id"), column("ticket_id"), column("communication_channel_id"))
_TICKET = table("ticket", column("id"), column("queue_id"))


def _channel_dimension() -> Dimension:
    return Dimension(
        key="channel",
        label_key=f"{_LABELS}.dimensions.channel",
        kind="id",
        expr=lambda ctx: _ARTICLE.c.communication_channel_id,
    )


def _count_measure() -> Measure:
    return Measure(key="count", label_key=f"{_LABELS}.measures.count", agg="count")


def _source(key: str, from_table: Any, ticket_join: Any = None) -> ReportSource:
    def from_clause(ctx: CompileContext) -> Any:
        return from_table if ticket_join is None else from_table.join(_TICKET, ticket_join)

    return ReportSource(
        key=key,
        label_key=f"{_LABELS}.sources.{key.rsplit('.', 1)[-1]}",
        from_clause=from_clause,
        scope_column=lambda ctx: _TICKET.c.queue_id,
        dimensions=(_channel_dimension(),),
        measures=(_count_measure(),),
        requires_facts=False,
    )


def articles_per_channel_source() -> ReportSource:
    """Znuny ``article`` joined to ``ticket`` (integer join): articles per channel, queue-scoped."""
    return _source(
        "plugin.points.articles_per_channel", _ARTICLE, _ARTICLE.c.ticket_id == _TICKET.c.id
    )


# Sources kept OUT of ``register()``: negative tests feed them to a registrar (D15 table check).
def own_source() -> ReportSource:
    """Only the plugin's own table (it need not exist at registration)."""
    own = table("tqp_points_x", column("id"), column("communication_channel_id"))
    return dataclasses.replace(
        _source("plugin.points.own", own),
        scope_column=lambda ctx: own.c.id,
        dimensions=(
            dataclasses.replace(
                _channel_dimension(), expr=lambda ctx: own.c.communication_channel_id
            ),
        ),
    )


def mixed_source() -> ReportSource:
    """Znuny ``article`` next to the plugin's own table: refused (collation 1267)."""
    own = table("tqp_points_x", column("article_id"))
    return _source("plugin.points.mixed", _ARTICLE, _ARTICLE.c.id == own.c.article_id)


def tiqora_source() -> ReportSource:
    fact = table("tiqora_report_fact", column("ticket_id"))
    return _source("plugin.points.facts", _ARTICLE, _ARTICLE.c.ticket_id == fact.c.ticket_id)


def other_plugin_source() -> ReportSource:
    other = table("tqp_other_x", column("article_id"))
    return _source("plugin.points.other", _ARTICLE, _ARTICLE.c.id == other.c.article_id)


def _register_reports(registry: PluginRegistry) -> None:
    registry.report_source(articles_per_channel_source())
    registry.builtin_report(
        "articles_by_channel",
        category="volume",
        definition=ReportDefinition.model_validate(
            {
                "source": "plugin.points.articles_per_channel",
                "dimensions": [{"key": "channel"}],
                "measures": [{"key": "count"}],
            }
        ),
    )


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
        _register_channels(registry)
        _register_auth(registry)
        _register_customer_source(registry)
        _register_gi(registry)
        _register_reports(registry)


plugin = PointsPlugin()
