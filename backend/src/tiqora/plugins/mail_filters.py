"""Mail filters from plugins (D9).

Inbound filters are **fail-open**: a plugin filter that raises, hangs or runs out of time never
drops or changes the fate of a mail.

* ``pre`` filters run inside the mail's transaction, right after Znuny's postmaster filters
  (:func:`tiqora.channels.email.filters.apply_filters`) and before the ticket is matched. They
  may set ``X-OTRS-*`` headers (including ``X-OTRS-Ignore: yes``) on ``mail.headers``. A filter
  that fails has its header changes undone.
* ``post`` filters run after the transaction of the message committed, so they see the ticket
  (``ctx.plugin.tickets.get``). Their failure changes nothing.

Outbound filters run at the start of :meth:`SmtpMailSender.send`. They may only add ``X-``
headers or veto the send (permanent 550). A filter that raises, hangs or breaks the header rules
contributes nothing and the mail goes out unchanged. One :class:`PluginCallBudget` of
``OUTBOUND_BUDGET_SECONDS`` is shared by all outbound filters of one ``send()``, each capped at
``OUTBOUND_FILTER_CAP_SECONDS``; an exhausted budget sends the mail unchanged.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from email.header import Header
from email.message import EmailMessage
from email.utils import getaddresses
from typing import TYPE_CHECKING, Any, Final, Literal

import aiosmtplib
import structlog

from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import ExtensionPoint
from tiqora.plugins.calls import PluginCallBudget, call_plugin
from tiqora.plugins.context import PluginContext, minimal_context
from tiqora.plugins.state import is_active

if TYPE_CHECKING:
    from tiqora.channels.email.parser import ParsedEmail
    from tiqora.channels.email.pipeline import PipelineResult
    from tiqora.db.legacy.mail_account import MailAccount

logger = structlog.get_logger(__name__)

__all__ = [
    "INBOUND_BUDGET_SECONDS",
    "INBOUND_MAIL_FILTERS",
    "OUTBOUND_MAIL_FILTERS",
    "InboundMail",
    "InboundMailResult",
    "MailFilter",
    "MailFilterContext",
    "MailFilterStage",
    "MailVetoed",
    "OutboundMail",
    "OutboundMailVetoed",
    "OUTBOUND_BUDGET_SECONDS",
    "OUTBOUND_FILTER_CAP_SECONDS",
    "apply_outbound_headers",
    "run_inbound_post_filters",
    "run_inbound_pre_filters",
    "run_outbound_filters",
]

MailFilterStage = Literal["pre", "post", "outbound"]

#: all inbound filters of one stage together; filters beyond it are skipped (fail-open)
INBOUND_BUDGET_SECONDS: float = 30.0

#: all outbound filters of one ``send()`` together (the request that sends may still be waiting)
OUTBOUND_BUDGET_SECONDS: float = 10.0
#: upper bound for one outbound filter, whatever its ``timeout_seconds`` says
OUTBOUND_FILTER_CAP_SECONDS: float = 5.0
_VETO_MAX: Final = 300

_HEADER_NAME_RE: Final = re.compile(r"^X-[A-Za-z0-9-]{1,60}$")
_MAX_HEADER_VALUE: Final = 998


@dataclass(frozen=True, slots=True)
class InboundMail:
    """What a ``pre`` filter sees: the mail after Znuny's database filters."""

    #: the pipeline's own header dict; set ``X-OTRS-*`` keys here (canonical spelling)
    headers: MutableMapping[str, str]
    subject: str
    from_address: str
    to: str | None
    body: str  # read-only plain text
    attachment_names: tuple[str, ...]
    account_id: int
    trusted: bool


@dataclass(frozen=True, slots=True)
class InboundMailResult:
    """What a ``post`` filter sees: the outcome of a mail whose transaction committed."""

    outcome: str  # PipelineResult.outcome
    ticket_id: int | None
    article_id: int | None
    headers: Mapping[str, str]  # copy of the pipeline's headers after all filters
    account_id: int


class MailVetoed(Exception):  # noqa: N818  (public SDK name, D2)
    """Raised by an outbound filter to stop the send; the message is German, <= 300 chars."""


class OutboundMailVetoed(aiosmtplib.SMTPResponseException):
    """A plugin refused to send a mail: reported like a permanent SMTP rejection (550)."""

    def __init__(self, message: str) -> None:
        super().__init__(550, message)

    def __str__(self) -> str:
        # SMTPResponseException prints "(550, '...')", which reads like a relay rejection.
        return self.message


@dataclass(slots=True)
class OutboundMail:
    """What an ``outbound`` filter sees; it may only add ``X-`` headers or veto."""

    to: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    from_address: str
    message_id: str | None
    headers: Mapping[str, str]  # read-only view of the message headers
    added: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def add_header(self, name: str, value: str) -> None:
        """Add ``X-<name>: value``; rejects anything that could change the mail's content."""
        if not isinstance(name, str) or not _HEADER_NAME_RE.fullmatch(name):
            raise ValueError("Nur Kopfzeilen der Form X-Name sind erlaubt")
        if name.upper().startswith("X-OTRS-"):
            raise ValueError("Kopfzeilen X-OTRS-* sind für Tiqora reserviert")
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise ValueError("Der Wert einer Kopfzeile darf keinen Zeilenumbruch enthalten")
        if len(value) > _MAX_HEADER_VALUE:
            raise ValueError(
                f"Der Wert einer Kopfzeile darf höchstens {_MAX_HEADER_VALUE} Zeichen haben"
            )
        self.added[name] = value


@dataclass(frozen=True, slots=True)
class MailFilterContext:
    plugin: PluginContext


@dataclass(frozen=True, slots=True)
class MailFilter:
    key: str  # "<plugin_id>:<name>"
    plugin_id: str
    stage: MailFilterStage
    run: Callable[[MailFilterContext, Any], Awaitable[None]]
    order: int
    timeout_seconds: int  # 1..30
    context: PluginContext | None = None


INBOUND_MAIL_FILTERS: Final[ExtensionPoint[MailFilter]] = ExtensionPoint(
    key="core.mail.inbound_filters",
    item_key=lambda f: f.key,
    description="Filters a plugin runs on incoming mail, before (pre) and after (post) handling.",
    sdk_stable=True,
)
OUTBOUND_MAIL_FILTERS: Final[ExtensionPoint[MailFilter]] = ExtensionPoint(
    key="core.mail.outbound_filters",
    item_key=lambda f: f.key,
    description="Filters a plugin runs on outgoing mail (add X- headers or veto).",
    sdk_stable=True,
)


def _inbound_filters(stage: MailFilterStage) -> list[MailFilter]:
    found = [
        f
        for f in get_registry().items(INBOUND_MAIL_FILTERS)
        if f.stage == stage and is_active(f.plugin_id)
    ]
    found.sort(key=lambda f: (f.order, f.key))
    return found


async def _run_filter(f: MailFilter, arg: object, budget: PluginCallBudget) -> bool:
    remaining = budget.remaining()
    if remaining <= 0:
        logger.warning(
            "plugin_mail_filter_skipped", plugin=f.plugin_id, filter=f.key, reason="budget"
        )
        return False
    ctx = MailFilterContext(plugin=f.context or minimal_context(f.plugin_id))
    outcome = await call_plugin(
        f.plugin_id,
        f"mail filter {f.key} ({f.stage})",
        lambda: f.run(ctx, arg),
        timeout=budget.timeout_for(float(f.timeout_seconds)),
    )
    return outcome.ok


async def run_inbound_pre_filters(
    get_param: dict[str, str], parsed: ParsedEmail, account: MailAccount
) -> None:
    """Run the ``pre`` filters in ``(order, key)`` order; a failing one is undone and skipped."""
    filters = _inbound_filters("pre")
    if not filters:
        return
    budget = PluginCallBudget(INBOUND_BUDGET_SECONDS)
    for f in filters:
        mail = InboundMail(
            headers=get_param,
            subject=parsed.subject,
            from_address=parsed.from_address,
            to=parsed.to_header or None,
            body=parsed.body,
            attachment_names=tuple(a.filename for a in parsed.attachments),
            account_id=int(account.id),
            trusted=bool(account.trusted),
        )
        before = dict(get_param)
        if not await _run_filter(f, mail, budget):
            get_param.clear()
            get_param.update(before)


async def run_inbound_post_filters(
    result: PipelineResult, get_param: Mapping[str, str], account_id: int
) -> None:
    """Run the ``post`` filters after the message's transaction committed; never raises."""
    try:
        filters = _inbound_filters("post")
        if not filters:
            return
        budget = PluginCallBudget(INBOUND_BUDGET_SECONDS)
        for f in filters:
            mail_result = InboundMailResult(
                outcome=result.outcome,
                ticket_id=result.ticket_id,
                article_id=result.article_id,
                headers=dict(get_param),
                account_id=account_id,
            )
            await _run_filter(f, mail_result, budget)
    except Exception:
        logger.exception("plugin_mail_post_filters_failed", account_id=account_id)


def _outbound_filters() -> list[MailFilter]:
    found = [f for f in get_registry().items(OUTBOUND_MAIL_FILTERS) if is_active(f.plugin_id)]
    found.sort(key=lambda f: (f.order, f.key))
    return found


def _address_list(values: list[str]) -> tuple[str, ...]:
    return tuple(addr for _name, addr in getaddresses(values) if addr)


def _outbound_view(message: EmailMessage, added: Mapping[str, str]) -> OutboundMail:
    headers = {str(k): str(v) for k, v in message.items()}
    headers.update(added)
    return OutboundMail(
        to=_address_list([str(v) for v in message.get_all("To", [])]),
        cc=_address_list([str(v) for v in message.get_all("Cc", [])]),
        subject=str(message.get("Subject", "")),
        from_address=next(iter(_address_list([str(message.get("From", ""))])), ""),
        message_id=str(message["Message-ID"]) if message.get("Message-ID") else None,
        headers=headers,
    )


async def run_outbound_filters(message: EmailMessage) -> dict[str, str]:
    """Run the ``outbound`` filters in ``(order, key)`` order; return the ``X-`` headers to add.

    Raises :class:`OutboundMailVetoed` (permanent 550) when a filter raises
    :class:`MailVetoed`. Any other failure, a timeout or an exhausted budget contributes no
    header and never stops the send. Never touches *message*.
    """
    filters = _outbound_filters()
    if not filters:
        return {}
    budget = PluginCallBudget(OUTBOUND_BUDGET_SECONDS)
    added: dict[str, str] = {}
    for f in filters:
        if budget.remaining() <= 0:
            logger.warning(
                "plugin_mail_filter_skipped", plugin=f.plugin_id, filter=f.key, reason="budget"
            )
            break
        mail = _outbound_view(message, added)
        ctx = MailFilterContext(plugin=f.context or minimal_context(f.plugin_id))
        vetoes: list[str] = []

        async def _call(
            f: MailFilter = f,
            ctx: MailFilterContext = ctx,
            mail: OutboundMail = mail,
            vetoes: list[str] = vetoes,
        ) -> None:
            try:
                await f.run(ctx, mail)
            except MailVetoed as exc:
                vetoes.append(str(exc))

        outcome = await call_plugin(
            f.plugin_id,
            f"mail filter {f.key} (outbound)",
            _call,
            timeout=budget.timeout_for(min(float(f.timeout_seconds), OUTBOUND_FILTER_CAP_SECONDS)),
        )
        if vetoes:
            reason = vetoes[0].strip()[:_VETO_MAX] or "ohne Begründung"
            logger.info("plugin_mail_vetoed", plugin=f.plugin_id, filter=f.key)
            raise OutboundMailVetoed(
                f"Versand durch Erweiterung „{f.plugin_id}“ abgelehnt: {reason}"
            )
        if outcome.ok:
            added.update(mail.added)
    return added


def _header_line(name: str, value: str) -> bytes:
    if value.isascii():
        return f"{name}: {value}\r\n".encode("ascii")
    return f"{name}: {Header(value, 'utf-8').encode()}\r\n".encode("ascii")


def apply_outbound_headers(message: EmailMessage, added: Mapping[str, str]) -> EmailMessage:
    """A copy of *message* carrying the *added* ``X-`` headers; *message* stays untouched.

    A message with fixed wire bytes (``tiqora_raw``: signed or encrypted mail) gets the header
    lines prepended to those bytes, so no MIME part (and no signature) changes.
    """
    if not added:
        return message
    out = copy.deepcopy(message)
    raw = getattr(message, "tiqora_raw", None)
    if raw:
        lowered = {n.lower() for n in added}
        # A header of the same name already in the top-level block is replaced, not duplicated.
        head, sep, rest = bytes(raw).partition(b"\r\n\r\n")
        kept = _drop_top_headers(head, lowered)
        prefix = b"".join(_header_line(n, v) for n, v in added.items())
        out.tiqora_raw = prefix + kept + sep + rest  # type: ignore[attr-defined]
    for name, value in added.items():
        del out[name]
        out[name] = value
    return out


def _drop_top_headers(head: bytes, lowered: set[str]) -> bytes:
    lines = head.split(b"\r\n")
    kept: list[bytes] = []
    skipping = False
    for line in lines:
        if line[:1] in (b" ", b"\t"):
            if not skipping:
                kept.append(line)
            continue
        name = line.partition(b":")[0].decode("ascii", "replace").strip().lower()
        skipping = name in lowered
        if not skipping:
            kept.append(line)
    return b"\r\n".join(kept)
