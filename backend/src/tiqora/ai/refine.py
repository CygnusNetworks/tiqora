"""RefineService — polish the text an agent typed into a composer.

The agent writes a reply (or a new ticket) by hand and asks the LLM to clean
up spelling, grammar and style, optionally shifting the tone. This is a plain
completion: no tools, no ticket state written, nothing persisted anywhere —
the refined text goes straight back to the composer, where the agent still
has to press Send.

**Quoted text is never rewritten.** The frontend
(``frontend/src/lib/replyQuote.ts``) splits the composer body into alternating
``own``/``quote`` segments and sends all of them; quotes travel *into* the
prompt as read-only context (an inline reply like "Ja, das passt." is not
refinable without the line it answers) but only ``own`` sections come back.
The caller re-assembles the body from the original bytes of every quote
segment, so a model that ignored the instruction still could not alter a
quote.

Multiple own sections are refined in ONE call: each is labelled ``[SECTION
<index>]`` and the model answers with a JSON object keyed by those same
indices. If the response does not cover exactly the requested ids, the batch
is discarded and each section is retried on its own — a small, bounded
fallback rather than a half-applied rewrite.

Gating mirrors :mod:`tiqora.ai.summary`: per-queue ``enabled_refine`` plus the
per-agent ACL/limits for ``FEATURE_REFINE``. Not Readiness-Gate gated — this
writes nothing Sync-relevant (it writes nothing at all).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai import usage as usage_service
from tiqora.ai.acl import AclLimitExceededError as AiAclLimitExceededError
from tiqora.ai.acl import check_feature_access, check_feature_limits
from tiqora.ai.audit import FEATURE_REFINE as AUDIT_FEATURE_REFINE
from tiqora.ai.audit import AuditContext, AuditingLlmClient
from tiqora.ai.llm import LlmClient, LlmMessage
from tiqora.ai.models import FEATURE_REFINE
from tiqora.ai.pii import PiiMapper
from tiqora.ai.policies import get_queue_policy_by_queue
from tiqora.config import Settings, get_settings

logger = structlog.get_logger(__name__)

TONE_STANDARD = "standard"
TONE_FORMAL = "formal"
TONE_FRIENDLY = "friendly"
TONE_CONCISE = "concise"
REFINE_TONES = frozenset({TONE_STANDARD, TONE_FORMAL, TONE_FRIENDLY, TONE_CONCISE})

KIND_OWN = "own"

#: Guard against a runaway composer body; the composer is a reply box, not a
#: document editor. Enforced by the API layer, repeated here as a hard stop.
MAX_TOTAL_CHARS = 60_000

#: Role and the non-negotiable rules. Kept separate from the tone block and
#: the output contract so the tone can sit BETWEEN them — a tone sentence
#: appended after the JSON contract is the last thing the model reads and was
#: measurably ignored (all four tones produced near-identical German).
_RULES = (
    "You are a writing assistant for a customer-support agent. You are given "
    "one or more sections of a message the agent has just typed, and you "
    "rewrite each one so it reads well: correct spelling and grammar, clear "
    "sentence structure.\n\n"
    "Hard rules:\n"
    "- Do not add any information, fact, number, date, identifier, name, "
    "promise or offer that is not already in the agent's text.\n"
    "- Do not remove information. Every fact in the input must still be in "
    "your output.\n"
    "- Write each section in the same language the agent used for it.\n"
    "- Do not add a greeting, a closing line or a signature that is not "
    "already there; the system appends the signature itself.\n"
    "- Do not answer the customer, do not continue the message, do not "
    "comment on your changes.\n"
    "- Never add a commitment: no follow-up, next step, check-back, "
    "timeline or availability that the agent did not already write.\n"
    "- Sections marked as a quote are context only. Never reproduce, "
    "translate or rewrite them."
)

_OUTPUT_CONTRACT = (
    "Answer with a JSON object and nothing else, in this exact shape:\n"
    '{"sections": [{"id": <the section id as a NUMBER>, "text": "<the rewritten text>"}]}\n'
    "Include exactly one entry for every [SECTION <id>] block you were "
    "given, with the same id, and no other entries. The id is the bare "
    'number, not the label — write 1, not "SECTION 1".'
)

#: Concrete, contrastive, sentence-level. Each names what to DO and what NOT
#: to do, so the model has a direction to move away from instead of one ideal
#: to converge on. Naming a mood ("warm and approachable") does not work: the
#: hard rules above are specific and simply out-argue it.
_TONE_INSTRUCTIONS = {
    TONE_STANDARD: (
        "Keep the agent's register exactly as it is. Change only what is "
        "wrong or hard to read. Do not make it more formal and do not make "
        "it warmer."
    ),
    TONE_FORMAL: (
        "Raise it to a formal business register. Prefer a nominal style "
        "('die Bearbeitung erfolgt' over 'wir bearbeiten das'), full "
        "courtesy formulas ('Wir bitten um Ihr Verstaendnis', 'Vielen Dank "
        "fuer Ihre Geduld'), and complete, self-contained sentences. Keep "
        "distance: no contractions, no colloquialisms, no exclamation "
        "marks, and do not address the reader's feelings."
    ),
    TONE_FRIENDLY: (
        "Make it noticeably more personal. Use short sentences and active "
        "verbs ('wir schauen uns das an' over 'eine Pruefung erfolgt'), "
        "address the reader directly, and acknowledge the situation once "
        "where the text already implies it ('das ist aergerlich', 'wir "
        "verstehen den Aerger'). This is NOT the formal register: avoid "
        "nominal constructions and stock courtesy formulas, and do not sound "
        "like a form letter. Stay professional and do not gush.\n"
        "Warmth never buys you content: do not promise a follow-up, a next "
        "step, a check-back or a timeline unless the agent already wrote "
        "one. Sentences like 'wir melden uns' or 'wir schauen weiter nach' "
        "are forbidden if they are not in the input."
    ),
    TONE_CONCISE: (
        "Cut it down hard: the result must be at least a third shorter than "
        "the input. At most one subordinate clause per sentence. Drop "
        "filler, hedging and repetition, and merge sentences that say the "
        "same thing. Every fact stays. This is NOT about politeness: do not "
        "add courtesy formulas and do not soften anything."
    ),
}

#: A tone shift needs room to move. At the client default of 0.2 the model
#: converges on one 'best' rewrite and the tone instruction barely registers
#: (measured: all four tones came back near-identical). Standard stays low on
#: purpose -- it is only meant to fix language, not to vary.
_TONE_TEMPERATURES = {
    TONE_STANDARD: 0.2,
    TONE_FORMAL: 0.5,
    TONE_FRIENDLY: 0.5,
    TONE_CONCISE: 0.4,
}


class RefineError(Exception):
    """Base class for refine failures."""


class RefinePolicyDisabledError(RefineError):
    """``enabled_refine`` is off for this queue."""


class RefineAclDeniedError(RefineError):
    """The acting agent may not use the refine feature."""


class RefineAclLimitExceededError(RefineError):
    """The acting agent's per-day/month budget for refine is exhausted."""


class RefineEmptyOutputError(RefineError):
    """Neither the batch call nor the per-section retries produced a usable
    response — the composer keeps the agent's original text untouched."""


@dataclass(frozen=True, slots=True)
class Segment:
    """One run of the composer body, as segmented by the frontend."""

    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class RefineResult:
    #: Refined text keyed by the segment index it replaces.
    sections: dict[int, str]
    prompt_tokens: int = 0
    completion_tokens: int = 0


def own_section_ids(segments: list[Segment]) -> list[int]:
    """Indices of the segments worth sending — own text with actual content."""
    return [i for i, s in enumerate(segments) if s.kind == KIND_OWN and s.text.strip()]


def _tone_instruction(tone: str) -> str:
    return _TONE_INSTRUCTIONS.get(tone, _TONE_INSTRUCTIONS[TONE_STANDARD])


def _temperature_for(tone: str) -> float:
    return _TONE_TEMPERATURES.get(tone, _TONE_TEMPERATURES[TONE_STANDARD])


def _build_system_prompt(tone: str) -> str:
    """Rules, then the tone block, then the output contract — in that order."""
    tone_block = f"TONE — apply this to every section:\n{_tone_instruction(tone)}"
    return f"{_RULES}\n\n{tone_block}\n\n{_OUTPUT_CONTRACT}"


def _build_user_message(
    segments: list[Segment],
    section_ids: list[int],
    *,
    pii: PiiMapper,
    mask: bool,
) -> str:
    """Render the whole body in order: quotes as labelled read-only context,
    the requested own segments as ``[SECTION <id>]`` blocks to rewrite."""
    wanted = set(section_ids)
    parts: list[str] = []
    for i, segment in enumerate(segments):
        body = segment.text.strip()
        if not body:
            continue
        body = pii.mask(body) if mask else body
        if i in wanted:
            parts.append(f"[SECTION {i}]\n{body}")
        else:
            # Includes own segments that were not requested — still useful
            # context, still not rewritable.
            parts.append(f"[QUOTE {i} — context only, do not rewrite]\n{body}")
    return "\n\n".join(parts)


def _extract_json_object(content: str) -> str | None:
    """Pull the JSON object out of a response that may be fenced or padded."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    candidate = fenced.group(1) if fenced else content
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    return candidate[start : end + 1]


def _coerce_section_id(raw: Any) -> int | None:
    """Section id as an int, or ``None`` if it is not one.

    Models routinely answer ``"id": "2"`` even when the prompt shows an
    unquoted number, and sometimes echo the label back as ``"SECTION 1"``
    (both seen in prod from Qwen3-235B). A single number anywhere in the
    string is therefore accepted — rejecting it would throw away an otherwise
    good rewrite and force a needless retry.

    Anything ambiguous stays rejected: no number, several numbers, or a
    ``bool`` (which is an ``int`` subclass). Being liberal about the *spelling*
    of an id must not become liberal about *which* section it names.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        numbers = re.findall(r"\d+", raw)
        if len(numbers) != 1:
            return None
        return int(numbers[0])
    return None


def _parse_sections(content: str | None, expected_ids: list[int]) -> dict[int, str] | None:
    """Parse the model's JSON answer, or ``None`` if it is not usable.

    Usable means: valid JSON, and exactly the requested ids, each with
    non-blank text. Anything else is rejected wholesale — a partially applied
    rewrite is worse than none, because the agent cannot see what was skipped.
    """
    if not content:
        return None
    raw = _extract_json_object(content)
    if raw is None:
        return None
    try:
        payload: Any = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    entries = payload.get("sections")
    if not isinstance(entries, list):
        return None

    out: dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        section_id = _coerce_section_id(entry.get("id"))
        text_value = entry.get("text")
        if section_id is None:
            return None
        if not isinstance(text_value, str) or not text_value.strip():
            return None
        if section_id in out:
            return None
        out[section_id] = text_value.strip()

    if set(out) != set(expected_ids):
        return None
    return out


def _completion_budget(segments: list[Segment], section_ids: list[int]) -> int:
    """Roughly 4 chars per token, doubled for headroom, within sane bounds."""
    chars = sum(len(segments[i].text) for i in section_ids)
    return max(512, min(4096, (chars // 2) + 256))


async def refine_text(
    session: AsyncSession,
    *,
    llm: LlmClient,
    queue_id: int,
    segments: list[Segment],
    tone: str,
    acting_user_id: int,
    ticket_id: int | None = None,
    settings: Settings | None = None,
    run_id: str | None = None,
) -> RefineResult:
    """Rewrite the agent's own sections of *segments*; quotes are context only.

    Returns the refined text keyed by segment index. Raises
    :class:`RefineEmptyOutputError` when nothing usable came back, so the
    caller can leave the composer exactly as the agent left it.
    """
    policy = await get_queue_policy_by_queue(session, queue_id)
    if policy is None or not policy.enabled_refine:
        raise RefinePolicyDisabledError(f"Refine is disabled for queue {queue_id}")

    if not await check_feature_access(session, acting_user_id, FEATURE_REFINE):
        raise RefineAclDeniedError(f"User {acting_user_id} is not allowed to use {FEATURE_REFINE}")
    try:
        await check_feature_limits(session, acting_user_id, FEATURE_REFINE)
    except AiAclLimitExceededError as exc:
        raise RefineAclLimitExceededError(str(exc)) from exc

    total_chars = sum(len(s.text) for s in segments)
    if total_chars > MAX_TOTAL_CHARS:
        raise RefineError(f"Text too long to refine ({total_chars} characters)")

    section_ids = own_section_ids(segments)
    if not section_ids:
        raise RefineError("Nothing to refine — no own text in this composer")

    mask = bool(policy.pii_masking)
    pii = PiiMapper()

    audit_context = AuditContext(
        feature=AUDIT_FEATURE_REFINE,
        run_id=run_id or uuid.uuid4().hex,
        ticket_id=ticket_id,
        queue_id=queue_id,
        acting_user_id=acting_user_id,
        trigger="manual",
        provider_id=policy.llm_provider_id,
        model=policy.model_override,
    )
    raw_llm = llm
    audited = AuditingLlmClient(
        llm,
        settings=settings or get_settings(),
        context=audit_context,
        session=session,
        pii_mapper=pii,
    )

    system_prompt = _build_system_prompt(tone)
    temperature = _temperature_for(tone)
    prompt_tokens = 0
    completion_tokens = 0
    model_served: str | None = None

    async def _ask(ids: list[int]) -> dict[int, str] | None:
        nonlocal prompt_tokens, completion_tokens, model_served
        response = await audited.chat(
            messages=[
                LlmMessage(role="system", content=system_prompt),
                LlmMessage(
                    role="user",
                    content=_build_user_message(segments, ids, pii=pii, mask=mask),
                ),
            ],
            tools=None,
            max_tokens=_completion_budget(segments, ids),
            temperature=temperature,
        )
        prompt_tokens += response.usage.prompt_tokens
        completion_tokens += response.usage.completion_tokens
        model_served = response.model or model_served
        return _parse_sections(response.content, ids)

    sections = await _ask(section_ids)
    if sections is None:
        # The batch answer was unusable. Retry each section on its own — a
        # single-section request is the shape models get right most reliably.
        logger.info("ai.refine.batch_unusable", queue_id=queue_id, sections=len(section_ids))
        recovered: dict[int, str] = {}
        for section_id in section_ids:
            single = await _ask([section_id])
            if single is not None:
                recovered.update(single)
        sections = recovered or None

    await usage_service.record_usage(
        session,
        user_id=acting_user_id,
        queue_id=queue_id,
        ticket_id=ticket_id,
        feature=FEATURE_REFINE,
        provider_id=getattr(raw_llm, "active_provider_id", None) or policy.llm_provider_id,
        model=model_served or getattr(raw_llm, "active_model", None) or policy.model_override,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        success=bool(sections),
    )
    await session.commit()

    if not sections:
        raise RefineEmptyOutputError("The model returned no usable rewrite")

    if mask:
        sections = {sid: pii.unmask(text) for sid, text in sections.items()}
    return RefineResult(
        sections=sections,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


__all__ = [
    "MAX_TOTAL_CHARS",
    "REFINE_TONES",
    "TONE_CONCISE",
    "TONE_FORMAL",
    "TONE_FRIENDLY",
    "TONE_STANDARD",
    "RefineAclDeniedError",
    "RefineAclLimitExceededError",
    "RefineEmptyOutputError",
    "RefineError",
    "RefinePolicyDisabledError",
    "RefineResult",
    "Segment",
    "own_section_ids",
    "refine_text",
]
