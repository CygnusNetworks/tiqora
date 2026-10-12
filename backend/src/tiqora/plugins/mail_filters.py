"""Mail filters from plugins (D9).

Inbound filters are **fail-open**: a plugin filter that raises, hangs or runs out of time never
drops or changes the fate of a mail.

* ``pre`` filters run inside the mail's transaction, right after Znuny's postmaster filters
  (:func:`tiqora.channels.email.filters.apply_filters`) and before the ticket is matched. They
  may set ``X-OTRS-*`` headers (including ``X-OTRS-Ignore: yes``) on ``mail.headers``. A filter
  that fails has its header changes undone.
* ``post`` filters run after the transaction of the message committed, so they see the ticket
  (``ctx.plugin.tickets.get``). Their failure changes nothing.

The outbound types (``OutboundMail`` and friends) live here too; the runner follows with the
outbound wiring.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
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
    "run_inbound_post_filters",
    "run_inbound_pre_filters",
]

MailFilterStage = Literal["pre", "post", "outbound"]

#: all inbound filters of one stage together; filters beyond it are skipped (fail-open)
INBOUND_BUDGET_SECONDS: float = 30.0

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
