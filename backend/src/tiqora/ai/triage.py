"""AI ticket triage: route a new ticket into the right queue, and fix the
customer when the mail was forwarded.

Runs once per ticket, on its **first** article, before the reply agent
(:mod:`tiqora.ai.auto_worker`) sees it — see :mod:`tiqora.ai.triage_worker`
for the outbox plumbing and the guards. This module holds only the decision
and its application, so the ticket-UI "accept this suggestion" route can
reuse :func:`apply_decision` with the agent's own user id.

Two independent proposals come out of one run:

**Queue.** The model is offered the allowlisted target queues
(``policy.triage_target_queue_ids``) with each queue's own
``routing_description``, and picks one opaque ``q_<id>`` key or ``none``.
Opaque keys, not bare integers: an id that comes back stringified is a
known LLM failure mode, and any key outside the offered set is then
trivially detectable as invention rather than silently mis-parsed.

**Customer.** Purely deterministic header parsing
(:func:`extract_forwarded_sender`). The model is never asked for the
address: the apply-time guard demands a byte-exact substring of the article
body, which a parser satisfies by construction and an LLM answer does not
(it normalises case and "fixes" what looks like a typo). With
``pii_masking`` on, the model could not see a real address anyway.

Confidence is an int 0..100 from self-consistency voting, capped by the
model's own estimate — see :func:`aggregate_votes` for what that number
does and does not mean.
"""

from __future__ import annotations

import email.utils
import json
import re
import statistics
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.context import ArticleSnapshot, TicketSnapshot
from tiqora.ai.listfields import parse_int_list
from tiqora.ai.llm import LlmClient, LlmMessage
from tiqora.ai.models import (
    SOURCE_AUTO,
    TRIAGE_CUSTOMER_SOURCE_HEADER,
    TiqoraAiArticleOrigin,
    TiqoraAiQueuePolicy,
    TiqoraAiTriage,
)
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    add_article,
    move_queue,
    set_customer,
)
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# How much of the customer's message the model sees. Routing is decided by
# the topic, which is in the first paragraphs; the tail is kept because
# forwarded mails often carry the actual request below the cover note.
_BODY_HEAD_CHARS = 3000
_BODY_TAIL_CHARS = 1000
_MAX_TOKENS = 300

NO_QUEUE_KEY = "none"
_QUEUE_KEY_PREFIX = "q_"

_ROUTE_TOOL_NAME = "route_ticket"


def queue_key(queue_id: int) -> str:
    return f"{_QUEUE_KEY_PREFIX}{queue_id}"


def queue_id_from_key(key: str) -> int | None:
    if not key.startswith(_QUEUE_KEY_PREFIX):
        return None
    try:
        return int(key[len(_QUEUE_KEY_PREFIX) :])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Data shapes
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueueCandidate:
    queue_id: int
    key: str
    name: str
    description: str
    # True when no routing_description was configured and we fell back to
    # queue.comments or the bare name. Surfaced so the admin UI can warn:
    # a nameless candidate is the most common cause of bad routing.
    description_missing: bool = False


@dataclass(frozen=True, slots=True)
class TriageVote:
    queue_key: str
    confidence: int
    reason: str


@dataclass(frozen=True, slots=True)
class QueueProposal:
    queue_id: int | None
    confidence: int
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CustomerProposal:
    email: str | None = None
    login: str | None = None
    customer_id: str | None = None
    confidence: int = 0
    source: str | None = None


@dataclass(frozen=True, slots=True)
class ForwardedSender:
    email: str
    line: str
    # 100 for a single unambiguous forward, 70 when the mail was forwarded
    # more than once and we had to pick the outermost original sender.
    confidence: int


@dataclass(frozen=True, slots=True)
class TriageDecision:
    queue: QueueProposal
    customer: CustomerProposal
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str | None = None
    provider_id: int | None = None
    run_id: str = ""
    error: str | None = None


# --------------------------------------------------------------------------
# Forwarded-sender extraction (pure)
# --------------------------------------------------------------------------

# Lower-cased; matched against a normalised, lower-cased body.
FORWARD_MARKERS: tuple[str, ...] = (
    "-----ursprüngliche nachricht-----",
    "-----urspruengliche nachricht-----",
    "-----original message-----",
    "---------- forwarded message ---------",
    "---------- weitergeleitete nachricht ----------",
    "-------- weitergeleitete nachricht --------",
    "begin forwarded message:",
    "von meinem iphone gesendet",  # never a marker on its own; see _find_markers
)
# The sign-off above is NOT a forward marker — kept out of the scan set.
_REAL_MARKERS: tuple[str, ...] = FORWARD_MARKERS[:-1]

_FROM_LINE_RE = re.compile(r"^\s*(?:von|from|absender)\s*:\s*(.+)$", re.IGNORECASE)
_MAILTO_RE = re.compile(r"mailto:\s*([^\s\]\>,;]+@[^\s\]\>,;]+)", re.IGNORECASE)
_ANGLE_RE = re.compile(r"<\s*([^<>\s]+@[^<>\s]+)\s*>")
_BARE_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Lines to scan after a marker for the From:/Von: header.
_HEADER_BLOCK_LINES = 15


def _normalise_body(body: str) -> str:
    """NBSP → space and strip one level of ``>`` quoting per line.

    Forwards routinely arrive quoted (someone replies to a forward), and
    Outlook/Exchange sprinkle non-breaking spaces into header blocks, which
    breaks a naive ``"von:"`` match.
    """
    out = body.replace("\xa0", " ").replace("\u202f", " ")
    lines = [re.sub(r"^\s*>+\s?", "", line) for line in out.splitlines()]
    return "\n".join(lines)


def _find_marker_positions(lowered: str) -> list[int]:
    positions: list[int] = []
    for marker in _REAL_MARKERS:
        start = 0
        while True:
            idx = lowered.find(marker, start)
            if idx < 0:
                break
            positions.append(idx)
            start = idx + len(marker)
    return sorted(positions)


def _address_from_line(line: str) -> str | None:
    """Pull one address out of a ``From:``/``Von:`` header value."""
    # parseaddr first: it handles `"Name, Vorname" <a@b>` correctly, which
    # a bare regex splits on the comma.
    _name, addr = email.utils.parseaddr(line)
    if addr and "@" in addr:
        return addr.strip().strip("<>").lower()
    for pattern in (_ANGLE_RE, _MAILTO_RE, _BARE_RE):
        match = pattern.search(line)
        if match:
            value = match.group(1) if match.groups() else match.group(0)
            return value.strip().strip("<>").lower()
    return None


def extract_forwarded_sender(
    body: str | None, *, exclude: Sequence[str] = ()
) -> ForwardedSender | None:
    """Best-effort original sender of a forwarded mail, or ``None``.

    Returns ``None`` unless the body actually carries a forward marker — a
    plain reply chain is not a forward, and guessing there would rewrite the
    customer of ordinary correspondence. ``exclude`` holds addresses that
    can never be the answer (the forwarder's own address, system/queue
    addresses). Raw ``From:`` headers are parseaddr-normalized; comparison
    is case-insensitive.
    """
    if not body:
        return None
    normalised = _normalise_body(body)
    lowered = normalised.lower()
    positions = _find_marker_positions(lowered)
    if not positions:
        return None

    blocked: set[str] = set()
    for raw in exclude:
        if not raw or not str(raw).strip():
            continue
        parsed = _address_from_line(str(raw).strip())
        blocked.add(parsed or str(raw).strip().lower())
    found: list[str] = []
    for pos in positions:
        block = normalised[pos:].splitlines()[1 : _HEADER_BLOCK_LINES + 1]
        for line in block:
            match = _FROM_LINE_RE.match(line)
            if not match:
                continue
            address = _address_from_line(match.group(1))
            if address and address not in blocked and address not in found:
                found.append(address)
            break  # first From: of this block wins

    if not found:
        return None
    # Outermost forward = the first marker in the body = the original author
    # of the chain as far as this ticket is concerned.
    return ForwardedSender(
        email=found[0],
        line=found[0],
        confidence=100 if len(found) == 1 else 70,
    )


# --------------------------------------------------------------------------
# Confidence (pure)
# --------------------------------------------------------------------------


def aggregate_votes(
    votes: Sequence[TriageVote], offered_keys: Sequence[str]
) -> tuple[str, int, str, list[dict[str, Any]]]:
    """Combine ``k`` samples into ``(winner_key, confidence, reason, dist)``.

    Confidence is ``min(agreement, self_reported_median)`` where *agreement*
    is the winner's share of **all** ``k`` samples — votes for a key that
    was never offered are discarded but still count against the denominator,
    so a hallucinating model loses confidence rather than being silently
    forgiven.

    What this number is not: a probability. Agreement measures how
    unambiguous the *prompt* is, not whether the answer is right — a
    misleading ``routing_description`` produces unanimous votes for the
    wrong queue. It is only useful as a relative ranking to threshold
    against once accept/reject data exists.
    """
    if not votes:
        return NO_QUEUE_KEY, 0, "", []

    allowed = set(offered_keys) | {NO_QUEUE_KEY}
    valid = [v for v in votes if v.queue_key in allowed]
    if not valid:
        return NO_QUEUE_KEY, 0, "", []

    counts = Counter(v.queue_key for v in valid)
    winner, hits = counts.most_common(1)[0]
    distribution = [
        {
            "key": key,
            "votes": count,
            "self": int(
                statistics.median([v.confidence for v in valid if v.queue_key == key]) or 0
            ),
        }
        for key, count in counts.most_common()
    ]

    if winner == NO_QUEUE_KEY:
        return NO_QUEUE_KEY, 0, "", distribution

    agreement = round(100 * hits / len(votes))
    winning = [v for v in valid if v.queue_key == winner]
    self_reported = int(statistics.median([v.confidence for v in winning]))
    reason = next((v.reason for v in winning if v.reason.strip()), "")
    return winner, max(0, min(100, min(agreement, self_reported))), reason, distribution


# --------------------------------------------------------------------------
# Candidates + prompt
# --------------------------------------------------------------------------


async def load_candidates(
    session: AsyncSession, policy: TiqoraAiQueuePolicy
) -> list[QueueCandidate]:
    """Resolve the policy's target-queue allowlist to prompt candidates.

    A target without its own ``routing_description`` falls back to
    ``queue.comments`` and then to the bare name; ``description_missing``
    marks that so the admin UI can warn about it.
    """
    target_ids = [qid for qid in parse_int_list(policy.triage_target_queue_ids)]
    if not target_ids:
        return []
    rows = (
        (
            await session.execute(
                text(
                    "SELECT q.id, q.name, q.comments, p.routing_description"
                    " FROM queue q"
                    " LEFT JOIN tiqora_ai_queue_policy p"
                    "   ON p.queue_id = q.id AND p.valid_id = 1"
                    " WHERE q.id IN :ids AND q.valid_id = 1"
                    " ORDER BY q.name"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": target_ids},
            )
        )
        .mappings()
        .all()
    )
    out: list[QueueCandidate] = []
    for row in rows:
        qid = int(row["id"])
        if qid == policy.queue_id:
            continue  # never offer the source queue as its own target
        described = (row["routing_description"] or "").strip()
        fallback = (row["comments"] or "").strip()
        out.append(
            QueueCandidate(
                queue_id=qid,
                key=queue_key(qid),
                name=str(row["name"]),
                description=described or fallback or str(row["name"]),
                description_missing=not described,
            )
        )
    return out


def _truncate_body(body: str | None) -> str:
    if not body:
        return ""
    text_ = body.strip()
    if len(text_) <= _BODY_HEAD_CHARS + _BODY_TAIL_CHARS:
        return text_
    return text_[:_BODY_HEAD_CHARS] + "\n[... gekürzt ...]\n" + text_[-_BODY_TAIL_CHARS:]


_SYSTEM_PROMPT = """\
Du bist die Eingangs-Triage eines Ticketsystems. Deine einzige Aufgabe ist \
zu entscheiden, ob ein neu eingegangenes Ticket in einer der angebotenen \
Ziel-Queues besser aufgehoben ist als in seiner aktuellen Queue.

Regeln:
- Antworte ausschliesslich ueber den Funktionsaufruf route_ticket.
- queue_key muss exakt einer der angebotenen Schluessel sein oder "none".
- Erfinde niemals einen Schluessel.
- Waehle "none", wenn das Ticket in der aktuellen Queue richtig liegt, wenn \
die Nachricht zu unklar ist, oder wenn keine der Beschreibungen wirklich passt.
- Entscheide nur anhand des Nachrichteninhalts und der Queue-Beschreibungen. \
Rate nicht anhand des Absendernamens oder der Domain.
- confidence ist deine eigene Einschaetzung von 0 bis 100, wie sicher die \
Zuordnung ist. Sei ehrlich; 100 nur bei voelliger Eindeutigkeit.
- reason ist ein kurzer interner Satz fuer die Sachbearbeitung, nicht fuer \
den Kunden."""


def build_route_tool(candidates: Sequence[QueueCandidate]) -> dict[str, Any]:
    keys = [c.key for c in candidates] + [NO_QUEUE_KEY]
    return {
        "type": "function",
        "function": {
            "name": _ROUTE_TOOL_NAME,
            "description": "Ordne das Ticket einer Ziel-Queue zu oder lehne ab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queue_key": {
                        "type": "string",
                        "enum": keys,
                        "description": "Einer der angebotenen Queue-Schluessel oder 'none'.",
                    },
                    "confidence": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "reason": {"type": "string"},
                },
                "required": ["queue_key", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
    }


def build_user_message(
    *,
    ticket: TicketSnapshot,
    candidates: Sequence[QueueCandidate],
    subject: str,
    body: str,
    from_address: str,
) -> str:
    lines = [
        f"Betreff: {subject}",
        f"Von: {from_address}",
        f"Aktuelle Queue: {ticket.queue_name or ticket.queue_id}",
        "",
        "--- Nachricht ---",
        body,
        "",
        "--- Kandidaten-Queues ---",
    ]
    for candidate in candidates:
        lines.append(f"[{candidate.key}] {candidate.name}")
        lines.append(f"  {candidate.description}")
    lines.append("")
    lines.append(f"[{NO_QUEUE_KEY}] Ticket bleibt, wo es ist.")
    return "\n".join(lines)


def _parse_vote(raw_arguments: dict[str, Any] | None, content: str | None) -> TriageVote | None:
    """One sample → a vote. Falls back to JSON in the message content for
    providers that ignore ``tool_choice``."""
    data: dict[str, Any] | None = raw_arguments
    if not data and content:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
            stripped = re.sub(r"\n?```$", "", stripped).strip()
        try:
            parsed = json.loads(stripped)
        except (TypeError, ValueError):
            return None
        if isinstance(parsed, dict):
            data = parsed
    if not isinstance(data, dict):
        return None
    key = data.get("queue_key")
    if not isinstance(key, str) or not key.strip():
        return None
    try:
        confidence = int(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    reason = data.get("reason")
    return TriageVote(
        queue_key=key.strip(),
        confidence=max(0, min(100, confidence)),
        reason=reason if isinstance(reason, str) else "",
    )


async def decide(
    session: AsyncSession,
    *,
    llm: LlmClient,
    raw_llm: Any = None,
    policy: TiqoraAiQueuePolicy,
    ticket: TicketSnapshot,
    article: ArticleSnapshot,
    candidates: Sequence[QueueCandidate],
    samples: int | None = None,
    run_id: str | None = None,
    mask: Callable[[str | None], str] | None = None,
) -> TriageDecision:
    """Run the triage model ``samples`` times and fold the votes together.

    ``mask`` (the run's :class:`~tiqora.ai.pii.PiiMapper` ``mask``) is
    applied to the subject, From header and body — never to the queue
    descriptions, which are admin-authored config, not customer data.
    """
    run_id = run_id or uuid.uuid4().hex
    k = max(1, int(samples if samples is not None else policy.triage_samples))

    # Deterministic parsing on the RAW body, before any masking: the
    # apply-time guard needs the address as it literally appears.
    forwarded = extract_forwarded_sender(
        article.body,
        exclude=[a for a in (article.from_address, ticket.customer_user_id) if a],
    )
    customer = CustomerProposal()
    if forwarded is not None:
        resolved = await resolve_customer_user(session, forwarded.email)
        customer = CustomerProposal(
            email=forwarded.email,
            login=resolved[0] if resolved else None,
            customer_id=resolved[1] if resolved else None,
            confidence=forwarded.confidence if resolved else 0,
            source=TRIAGE_CUSTOMER_SOURCE_HEADER,
        )

    if not candidates:
        return TriageDecision(
            queue=QueueProposal(queue_id=None, confidence=0, reason=""),
            customer=customer,
            run_id=run_id,
        )

    apply_mask = mask or (lambda value: value or "")
    subject = apply_mask(article.subject or ticket.title)
    body = apply_mask(_truncate_body(article.body))
    from_address = apply_mask(article.from_address) if article.from_address else "(unbekannt)"

    messages = [
        LlmMessage(role="system", content=_SYSTEM_PROMPT),
        LlmMessage(
            role="user",
            content=build_user_message(
                ticket=ticket,
                candidates=candidates,
                subject=subject,
                body=body,
                from_address=from_address,
            ),
        ),
    ]
    tool = build_route_tool(candidates)
    tool_choice = {"type": "function", "function": {"name": _ROUTE_TOOL_NAME}}

    votes: list[TriageVote] = []
    prompt_tokens = 0
    completion_tokens = 0
    last_error: str | None = None
    # k=1 means "no voting" — run it near-deterministically. For k>1 the
    # samples must actually differ or the vote carries no information.
    temperature = 0.2 if k == 1 else 0.7

    for _ in range(k):
        try:
            response = await llm.chat(
                messages=messages,
                tools=[tool],
                tool_choice=tool_choice,
                max_tokens=_MAX_TOKENS,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001 — one bad sample is not fatal
            last_error = str(exc)
            logger.warning("ai_triage_sample_error", run_id=run_id, error=str(exc))
            continue
        prompt_tokens += response.usage.prompt_tokens
        completion_tokens += response.usage.completion_tokens
        arguments = response.tool_calls[0].arguments if response.tool_calls else None
        vote = _parse_vote(arguments, response.content)
        if vote is None:
            last_error = last_error or "unparsable model response"
            continue
        votes.append(vote)

    offered = [c.key for c in candidates]
    winner, confidence, reason, distribution = aggregate_votes(votes, offered)
    target_id = queue_id_from_key(winner) if winner != NO_QUEUE_KEY else None
    # Never propose the queue the ticket is already in.
    if target_id is not None and target_id == ticket.queue_id:
        target_id, confidence, reason = None, 0, reason

    model = getattr(raw_llm, "active_model", None) or getattr(llm, "active_model", None)
    provider_id = getattr(raw_llm, "active_provider_id", None) or getattr(
        llm, "active_provider_id", None
    )

    return TriageDecision(
        queue=QueueProposal(
            queue_id=target_id,
            confidence=confidence,
            reason=reason,
            candidates=distribution,
        ),
        customer=customer,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=model,
        provider_id=provider_id,
        run_id=run_id,
        error=None if votes else (last_error or "no usable model response"),
    )


# --------------------------------------------------------------------------
# Customer resolution + application
# --------------------------------------------------------------------------


async def resolve_customer_user(session: AsyncSession, address: str) -> tuple[str, str] | None:
    """``(login, customer_id)`` for an e-mail address, or ``None``.

    Must match **exactly one** valid row. ``customer_user`` is unique on
    ``login`` but not on ``email`` (see the bootstrap schema), so duplicates
    are possible — and picking one of two people at random is worse than
    leaving the ticket alone. Falls back to matching the login itself, which
    many installs set to the address.
    """
    cleaned = (address or "").strip().lower()
    if not cleaned or "@" not in cleaned:
        return None
    for column in ("email", "login"):
        rows = (
            (
                await session.execute(
                    text(
                        f"SELECT login, customer_id FROM customer_user"  # noqa: S608
                        f" WHERE LOWER({column}) = :addr AND valid_id = 1"
                        " LIMIT 2"
                    ),
                    {"addr": cleaned},
                )
            )
            .mappings()
            .all()
        )
        if len(rows) == 1:
            row = rows[0]
            return str(row["login"]), str(row["customer_id"] or "")
        if len(rows) > 1:
            logger.info("ai_triage_customer_ambiguous", address=cleaned, column=column)
            return None
    return None


async def article_body(session: AsyncSession, article_id: int) -> str | None:
    row = (
        await session.execute(
            text("SELECT a_body FROM article_data_mime WHERE article_id = :aid LIMIT 1"),
            {"aid": article_id},
        )
    ).first()
    return str(row[0]) if row and row[0] is not None else None


async def _queue_name(session: AsyncSession, queue_id: int) -> str:
    row = (
        await session.execute(
            text("SELECT name FROM queue WHERE id = :qid LIMIT 1"), {"qid": queue_id}
        )
    ).first()
    return str(row[0]) if row else str(queue_id)


async def _ticket_customer(session: AsyncSession, ticket_id: int) -> tuple[str | None, str | None]:
    row = (
        await session.execute(
            text("SELECT customer_id, customer_user_id FROM ticket WHERE id = :tid LIMIT 1"),
            {"tid": ticket_id},
        )
    ).first()
    if row is None:
        return None, None
    return row[0], row[1]


async def _ticket_queue_id(session: AsyncSession, ticket_id: int) -> int | None:
    row = (
        await session.execute(
            text("SELECT queue_id FROM ticket WHERE id = :tid LIMIT 1"),
            {"tid": ticket_id},
        )
    ).first()
    if row is None or row[0] is None:
        return None
    return int(row[0])


MoveFn = Callable[[int], Awaitable[None]]
SetCustomerFn = Callable[[str | None, str | None], Awaitable[None]]


async def apply_decision(
    session: AsyncSession,
    *,
    sysconfig: SysConfig,
    row: TiqoraAiTriage,
    acting_user_id: int,
    apply_queue: bool,
    apply_customer: bool,
    write_note: bool = True,
    move_fn: MoveFn | None = None,
    set_customer_fn: SetCustomerFn | None = None,
) -> list[str]:
    """Apply the stored proposals. Returns what was actually applied.

    Works off the persisted row, not a live :class:`TriageDecision`, so the
    ticket-UI accept route can call it hours later. Every guard is therefore
    re-evaluated here rather than trusted from decision time.

    ``move_fn`` / ``set_customer_fn`` let the caller choose the mutator. The
    worker leaves them unset and gets the module-level, **permission-free**
    mutators: the AI service user has no group membership on the target
    queues, so the permission-checked ``TicketWriteService`` wrapper would
    reject every move. The allowlist an admin configured in
    ``triage_target_queue_ids`` is the authorization for that path. The
    accept route passes permission-checked callables instead, because a
    human acting must be checked as a human.

    Order matters: customer first, queue second. The other way round files
    the customer-change history against the new queue, and a failing
    ``set_customer`` would leave a ticket already moved.

    The queue half re-reads ``ticket.queue_id`` at apply time: a proposal
    can sit OPEN for hours, and a later human / GenericAgent / process
    move must not be undone by accepting a stale row.
    """
    applied: list[str] = []
    old_customer_id, old_customer_user_id = await _ticket_customer(session, row.ticket_id)

    if apply_customer and row.extracted_email:
        # G1: the address must appear verbatim in the article body. Cheap,
        # and it is what makes a parsed address trustworthy.
        body = await article_body(session, row.article_id) or ""
        if row.extracted_email.lower() not in body.lower():
            logger.info(
                "ai_triage_customer_guard_body", ticket_id=row.ticket_id, guard="not_in_body"
            )
        else:
            # G2: re-resolve — the customer_user table may have changed.
            resolved = await resolve_customer_user(session, row.extracted_email)
            if resolved is None:
                logger.info(
                    "ai_triage_customer_guard_lookup",
                    ticket_id=row.ticket_id,
                    guard="unresolvable_or_ambiguous",
                )
            elif resolved[0] == old_customer_user_id:
                # G3: already correct, nothing to do.
                logger.info("ai_triage_customer_noop", ticket_id=row.ticket_id)
            else:
                login, customer_id = resolved
                if set_customer_fn is not None:
                    await set_customer_fn(customer_id or None, login)
                else:
                    await set_customer(
                        session,
                        ticket_id=row.ticket_id,
                        customer_id=customer_id or None,
                        customer_user_id=login,
                        user_id=acting_user_id,
                    )
                row.suggested_customer_user_id = login
                row.suggested_customer_id = customer_id or None
                row.customer_applied = True
                applied.append("customer")

    old_queue_name = ""
    new_queue_name = ""
    if apply_queue and row.suggested_queue_id is not None:
        current_queue_id = await _ticket_queue_id(session, row.ticket_id)
        if current_queue_id == row.suggested_queue_id:
            logger.info("ai_triage_queue_noop", ticket_id=row.ticket_id)
        elif current_queue_id != row.source_queue_id:
            logger.info(
                "ai_triage_queue_stale",
                ticket_id=row.ticket_id,
                current_queue_id=current_queue_id,
                source_queue_id=row.source_queue_id,
                suggested_queue_id=row.suggested_queue_id,
            )
        else:
            old_queue_name = await _queue_name(session, row.source_queue_id)
            new_queue_name = await _queue_name(session, row.suggested_queue_id)
            if move_fn is not None:
                await move_fn(row.suggested_queue_id)
            else:
                await move_queue(
                    session,
                    ticket_id=row.ticket_id,
                    new_queue_id=row.suggested_queue_id,
                    user_id=acting_user_id,
                    sysconfig=sysconfig,
                )
            row.queue_applied = True
            applied.append("queue")

    if applied and write_note:
        await _write_note(
            session,
            row=row,
            acting_user_id=acting_user_id,
            sysconfig=sysconfig,
            applied=applied,
            old_queue_name=old_queue_name,
            new_queue_name=new_queue_name,
            old_customer_user_id=old_customer_user_id,
        )
    return applied


async def _write_note(
    session: AsyncSession,
    *,
    row: TiqoraAiTriage,
    acting_user_id: int,
    sysconfig: SysConfig,
    applied: Sequence[str],
    old_queue_name: str,
    new_queue_name: str,
    old_customer_user_id: str | None,
) -> None:
    lines = ["Tiqora KI-Triage"]
    if "queue" in applied:
        votes = ""
        if row.candidates_json:
            try:
                dist = json.loads(row.candidates_json)
                top = next(
                    (d for d in dist if d.get("key") == queue_key(row.suggested_queue_id or 0)),
                    None,
                )
                if top:
                    votes = f", {top.get('votes')} Stimmen"
            except (TypeError, ValueError):
                votes = ""
        lines.append(
            f"Queue: {old_queue_name} → {new_queue_name} (Konfidenz {row.queue_confidence}%{votes})"
        )
        if row.queue_reason:
            lines.append(f"Begruendung: {row.queue_reason}")
    if "customer" in applied:
        lines.append(
            f"Kunde: {old_customer_user_id or '(leer)'} → "
            f"{row.suggested_customer_user_id} "
            f"(weitergeleitete Mail, Adresse {row.extracted_email} im Text belegt)"
        )
    if row.run_id:
        lines.append(f"Run-ID: {row.run_id}")

    article_id = await add_article(
        session,
        ticket_id=row.ticket_id,
        article=ArticleIn(
            sender_type="agent",
            is_visible_for_customer=False,
            subject="KI-Triage",
            body="\n".join(lines),
            channel="note",
        ),
        user_id=acting_user_id,
        sysconfig=sysconfig,
    )
    # Without this marker the reply agent reads its own triage rationale --
    # including the customer's real address, which masking will not catch
    # inside an agent-authored article -- back as ticket context next turn.
    session.add(
        TiqoraAiArticleOrigin(
            article_id=article_id,
            source=SOURCE_AUTO,
            queue_id=row.suggested_queue_id or row.source_queue_id,
            service_user_id=acting_user_id,
            run_id=row.run_id,
        )
    )


__all__ = [
    "FORWARD_MARKERS",
    "NO_QUEUE_KEY",
    "CustomerProposal",
    "ForwardedSender",
    "QueueCandidate",
    "QueueProposal",
    "TriageDecision",
    "TriageVote",
    "aggregate_votes",
    "apply_decision",
    "article_body",
    "build_route_tool",
    "build_user_message",
    "decide",
    "extract_forwarded_sender",
    "load_candidates",
    "queue_id_from_key",
    "queue_key",
    "resolve_customer_user",
]
