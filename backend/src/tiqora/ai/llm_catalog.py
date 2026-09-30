"""Admin side of the LLM routing tables — models, profiles and task
assignments (the resolver itself is :mod:`tiqora.ai.llm_routing`).

Keeps the admin route handlers thin, like :mod:`tiqora.ai.providers` and
:mod:`tiqora.ai.policies` do:

* CRUD for models (``tiqora_llm_model``) and profiles (``tiqora_llm_profile``
  + ordered entries), the global task defaults and a queue's task overrides;
* **needs** (``TASK_NEEDS``) are enforced at write time on every entry of a
  profile: assigning a profile to a task, editing a profile's entries and
  switching a model's capability off are all rejected (422) when a directly
  assigned task would end up with a model that cannot do what it needs;
* deleting a model, profile or provider that is still referenced is a 409
  naming the users (the RESTRICT foreign keys would otherwise surface as 500);
* :func:`check_policy_llm_requirements` / :func:`newly_unserved_queues`:
  a queue feature that runs on the agent chain (auto-reply, manual assist,
  triage, summary, refine) can only be on when its task resolves to a profile.

Error messages are German — they are shown to admins as they are.
"""

from __future__ import annotations

import json
import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai import providers as ai_providers
from tiqora.ai.audit import FEATURE_TEST as AUDIT_FEATURE_TEST
from tiqora.ai.audit import AuditContext, write_audit_log
from tiqora.ai.llm_routing import (
    ALL_TASKS,
    NEED_TOOLS,
    NEED_VISION,
    TASK_AGENT,
    TASK_FINAL_ANSWER,
    TASK_NEEDS,
    TASK_REFINE,
    TASK_SUMMARY,
    TASK_TRIAGE,
    TASK_VISION,
    TASKS_FALLING_BACK_TO_AGENT,
)
from tiqora.ai.models import (
    TiqoraAiQueuePolicy,
    TiqoraAiQueueTaskProfile,
    TiqoraAiTaskDefault,
    TiqoraLlmModel,
    TiqoraLlmProfile,
    TiqoraLlmProfileEntry,
    TiqoraLlmProvider,
)
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret
from tiqora.db.legacy.queue import Queue

TASK_LABELS: dict[str, str] = {
    TASK_AGENT: "Recherche und Werkzeuge",
    TASK_FINAL_ANSWER: "Antwort formulieren",
    TASK_TRIAGE: "Triage",
    TASK_SUMMARY: "Zusammenfassen",
    TASK_REFINE: "Text verfeinern",
    TASK_VISION: "Bilder beschreiben",
}

_NEED_MISSING: dict[str, str] = {
    NEED_TOOLS: "kann keine Werkzeuge nutzen",
    NEED_VISION: "kann keine Bilder lesen",
}

# Queue policy flag → the task it runs on, and its name in the editor.
POLICY_FEATURE_TASKS: tuple[tuple[str, str, str], ...] = (
    ("enabled_auto_reply", TASK_AGENT, "Automatische Antworten"),
    ("enabled_manual_assist", TASK_AGENT, "KI-Entwurf"),
    ("enabled_triage", TASK_TRIAGE, "Triage"),
    ("enabled_summary", TASK_SUMMARY, "Zusammenfassen"),
    ("enabled_refine", TASK_REFINE, "Text verfeinern"),
)

_PROBE_TIMEOUT_SECONDS = 20.0


class CatalogValidationError(ValueError):
    """Invalid input (translated to 422)."""


class CatalogConflictError(Exception):
    """The row is still in use or would duplicate another (translated to 409)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def model_label(model: TiqoraLlmModel) -> str:
    return model.display_name or model.model_id


def _names(items: Iterable[str]) -> str:
    return ", ".join(f"„{name}“" for name in items)


@dataclass(frozen=True, slots=True)
class _Caps:
    """What a model can do — the current row or a would-be edit of it."""

    label: str
    supports_tools: bool
    supports_vision: bool

    @classmethod
    def of(cls, model: TiqoraLlmModel) -> _Caps:
        return cls(model_label(model), bool(model.supports_tools), bool(model.supports_vision))

    def meets(self, need: str) -> bool:
        return self.supports_tools if need == NEED_TOOLS else self.supports_vision


def _unmet(task: str, models: Sequence[_Caps]) -> list[str]:
    """``"<model> kann keine Werkzeuge nutzen"`` for every model of a chain
    that lacks something *task* needs."""
    return [
        f"„{caps.label}“ {_NEED_MISSING[need]}"
        for caps in models
        for need in sorted(TASK_NEEDS[task])
        if not caps.meets(need)
    ]


@dataclass(frozen=True, slots=True)
class Assignment:
    """One direct use of a profile: a global default (``queue_policy_id``
    None) or a queue override."""

    task: str
    queue_policy_id: int | None
    queue_name: str | None

    def label(self) -> str:
        where = (
            "global"
            if self.queue_policy_id is None
            else f"Queue {self.queue_name or f'#{self.queue_policy_id}'}"
        )
        return f"{TASK_LABELS.get(self.task, self.task)} ({where})"


async def _queue_names(session: AsyncSession, queue_ids: Collection[int]) -> dict[int, str]:
    if not queue_ids:
        return {}
    rows = (
        await session.execute(select(Queue.id, Queue.name).where(Queue.id.in_(queue_ids)))
    ).all()
    return {int(qid): str(name) for qid, name in rows}


async def profile_assignments(
    session: AsyncSession, profile_ids: Collection[int] | None = None
) -> dict[int, list[Assignment]]:
    """Direct task assignments per profile id (all profiles when *None*)."""
    defaults_q = select(TiqoraAiTaskDefault.profile_id, TiqoraAiTaskDefault.task).where(
        TiqoraAiTaskDefault.profile_id.is_not(None)
    )
    queue_q = select(
        TiqoraAiQueueTaskProfile.profile_id,
        TiqoraAiQueueTaskProfile.task,
        TiqoraAiQueueTaskProfile.queue_policy_id,
        TiqoraAiQueuePolicy.queue_id,
    ).join(TiqoraAiQueuePolicy, TiqoraAiQueuePolicy.id == TiqoraAiQueueTaskProfile.queue_policy_id)
    if profile_ids is not None:
        if not profile_ids:
            return {}
        defaults_q = defaults_q.where(TiqoraAiTaskDefault.profile_id.in_(profile_ids))
        queue_q = queue_q.where(TiqoraAiQueueTaskProfile.profile_id.in_(profile_ids))
    else:
        queue_q = queue_q.where(TiqoraAiQueueTaskProfile.profile_id.is_not(None))
    task_order = {task: i for i, task in enumerate(ALL_TASKS)}
    out: dict[int, list[Assignment]] = {}
    for profile_id, task in (await session.execute(defaults_q)).all():
        out.setdefault(int(profile_id), []).append(Assignment(task, None, None))
    queue_rows = (await session.execute(queue_q)).all()
    names = await _queue_names(session, {int(r[3]) for r in queue_rows})
    for profile_id, task, policy_id, queue_id in queue_rows:
        out.setdefault(int(profile_id), []).append(
            Assignment(task, int(policy_id), names.get(int(queue_id)))
        )
    for items in out.values():
        items.sort(
            key=lambda a: (
                a.queue_policy_id is not None,
                a.queue_name or "",
                a.queue_policy_id or 0,
                task_order.get(a.task, 99),
            )
        )
    return out


async def _profile_names_using(session: AsyncSession, model_ids: Collection[int]) -> list[str]:
    if not model_ids:
        return []
    rows = (
        await session.execute(
            select(TiqoraLlmProfile.name)
            .join(TiqoraLlmProfileEntry, TiqoraLlmProfileEntry.profile_id == TiqoraLlmProfile.id)
            .where(TiqoraLlmProfileEntry.llm_model_id.in_(model_ids))
            .distinct()
        )
    ).all()
    return sorted(str(r[0]) for r in rows)


async def _entry_models(session: AsyncSession, profile_id: int) -> list[TiqoraLlmModel]:
    return list(
        (
            await session.execute(
                select(TiqoraLlmModel)
                .join(
                    TiqoraLlmProfileEntry,
                    TiqoraLlmProfileEntry.llm_model_id == TiqoraLlmModel.id,
                )
                .where(TiqoraLlmProfileEntry.profile_id == profile_id)
                .order_by(TiqoraLlmProfileEntry.position)
            )
        )
        .scalars()
        .all()
    )


def _unmet_assignment_lines(
    assignments: Iterable[Assignment], models: Sequence[_Caps]
) -> list[str]:
    lines: list[str] = []
    for assignment in assignments:
        unmet = _unmet(assignment.task, models)
        if unmet:
            lines.append(f"{assignment.label()}: {', '.join(unmet)}")
    return lines


async def check_profile_fits_task(session: AsyncSession, profile_id: int, task: str) -> None:
    """422 unless the profile exists and every entry meets *task*'s needs."""
    profile = await session.get(TiqoraLlmProfile, profile_id)
    if profile is None:
        raise CatalogValidationError(f"Profil {profile_id} gibt es nicht.")
    unmet = _unmet(task, [_Caps.of(m) for m in await _entry_models(session, profile_id)])
    if unmet:
        raise CatalogValidationError(
            f"Profil „{profile.name}“ passt nicht zur Aufgabe „{TASK_LABELS[task]}“: "
            + ", ".join(unmet)
            + "."
        )


# ---------------------------------------------------------------------------
# Which queue features have a profile (in-memory mirror of llm_routing)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RoutingState:
    defaults: dict[str, int | None]  # task → global profile id (no row = absent)
    usable: frozenset[int]  # profile ids with valid_id == 1


async def _routing_state(session: AsyncSession) -> _RoutingState:
    defaults = {
        str(task): (int(pid) if pid is not None else None)
        for task, pid in (
            await session.execute(select(TiqoraAiTaskDefault.task, TiqoraAiTaskDefault.profile_id))
        ).all()
    }
    usable = frozenset(
        int(pid)
        for pid in (
            await session.execute(select(TiqoraLlmProfile.id).where(TiqoraLlmProfile.valid_id == 1))
        ).scalars()
    )
    return _RoutingState(defaults, usable)


def _effective_profile(
    task: str, overrides: Mapping[str, int | None], state: _RoutingState
) -> int | None:
    """Same rules as :func:`tiqora.ai.llm_routing.resolve_task_profile_id`:
    queue row > global row > none (a disabled profile counts as none), then
    triage/summary/refine fall back to the agent's profile."""

    def own(t: str) -> int | None:
        pid = overrides[t] if t in overrides else state.defaults.get(t)
        return pid if pid is not None and pid in state.usable else None

    pid = own(task)
    if pid is None and task in TASKS_FALLING_BACK_TO_AGENT:
        pid = own(TASK_AGENT)
    return pid


def _unserved_features(
    flags: Mapping[str, Any], overrides: Mapping[str, int | None], state: _RoutingState
) -> list[str]:
    return [
        label
        for flag, task, label in POLICY_FEATURE_TASKS
        if flags.get(flag) and _effective_profile(task, overrides, state) is None
    ]


def _unserved_message(features: Sequence[str]) -> str:
    return (
        f"Kein Modellprofil für: {', '.join(features)}. Bitte der Aufgabe "
        f"„{TASK_LABELS[TASK_AGENT]}“ ein Profil zuweisen – in dieser Queue oder "
        "global unter KI → Modelle → Aufgaben."
    )


async def check_policy_llm_requirements(
    session: AsyncSession,
    *,
    flags: Mapping[str, Any],
    overrides: Mapping[str, int | None],
    previous_flags: Mapping[str, Any] | None = None,
    previous_overrides: Mapping[str, int | None] | None = None,
) -> None:
    """422 when a create/update would newly leave an enabled feature of a
    queue without a profile to run on — the equivalent of the old
    "auto-reply needs a provider" check. *previous_** describe the stored
    policy (omit on create); a feature that was already enabled and unserved
    before does not block an unrelated edit. Triage/summary/refine without
    their own profile use the agent's, so an agent profile always satisfies
    every feature."""
    state = await _routing_state(session)
    already = (
        set(_unserved_features(previous_flags, previous_overrides or {}, state))
        if previous_flags is not None
        else set()
    )
    unserved = [f for f in _unserved_features(flags, overrides, state) if f not in already]
    if unserved:
        raise CatalogValidationError(_unserved_message(unserved))


async def newly_unserved_queues(
    session: AsyncSession,
    *,
    defaults: Mapping[str, int | None] | None = None,
    disabled_profile_id: int | None = None,
) -> list[str]:
    """Queues (valid policies) that have a profile for every enabled feature
    today but would lose one if the global defaults became *defaults* and/or
    the profile *disabled_profile_id* were disabled. Queues already missing
    one are not reported — a global edit must not be blocked by an unrelated
    queue."""
    before = await _routing_state(session)
    after = _RoutingState(
        dict(defaults) if defaults is not None else before.defaults,
        before.usable - {disabled_profile_id} if disabled_profile_id is not None else before.usable,
    )
    feature_filter = [
        getattr(TiqoraAiQueuePolicy, flag).is_(True) for flag, _, _ in POLICY_FEATURE_TASKS
    ]
    policies = list(
        (
            await session.execute(
                select(TiqoraAiQueuePolicy).where(
                    TiqoraAiQueuePolicy.valid_id == 1, or_(*feature_filter)
                )
            )
        )
        .scalars()
        .all()
    )
    if not policies:
        return []
    overrides = await load_queue_task_profiles(session, [p.id for p in policies])
    names = await _queue_names(session, {p.queue_id for p in policies})
    lines: list[str] = []
    for policy in sorted(policies, key=lambda p: names.get(p.queue_id, "")):
        flags = {flag: getattr(policy, flag) for flag, _, _ in POLICY_FEATURE_TASKS}
        own = overrides.get(policy.id, {})
        was = set(_unserved_features(flags, own, before))
        lost = [f for f in _unserved_features(flags, own, after) if f not in was]
        if lost:
            name = names.get(policy.queue_id, f"#{policy.queue_id}")
            lines.append(f"{name} ({', '.join(lost)})")
    return lines


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelFields:
    provider_id: int
    model_id: str
    display_name: str | None = None
    supports_tools: bool = True
    supports_vision: bool = False
    context_tokens: int | None = None
    max_tool_rounds: int | None = None
    price_input_per_1m: float | None = None
    price_output_per_1m: float | None = None
    valid_id: int = 1


async def list_models(session: AsyncSession) -> list[TiqoraLlmModel]:
    return list(
        (
            await session.execute(
                select(TiqoraLlmModel)
                .join(TiqoraLlmProvider, TiqoraLlmProvider.id == TiqoraLlmModel.provider_id)
                .order_by(TiqoraLlmProvider.name, TiqoraLlmModel.model_id)
            )
        )
        .scalars()
        .all()
    )


async def get_model(session: AsyncSession, model_id: int) -> TiqoraLlmModel | None:
    return await session.get(TiqoraLlmModel, model_id)


async def models_to_public_dicts(
    session: AsyncSession, models: Sequence[TiqoraLlmModel]
) -> list[dict[str, Any]]:
    providers = {
        p.id: p
        for p in (
            await session.execute(
                select(TiqoraLlmProvider).where(
                    TiqoraLlmProvider.id.in_({m.provider_id for m in models})
                )
            )
        ).scalars()
    }
    used: dict[int, list[str]] = {}
    if models:
        rows = (
            await session.execute(
                select(TiqoraLlmProfileEntry.llm_model_id, TiqoraLlmProfile.name)
                .join(TiqoraLlmProfile, TiqoraLlmProfile.id == TiqoraLlmProfileEntry.profile_id)
                .where(TiqoraLlmProfileEntry.llm_model_id.in_([m.id for m in models]))
            )
        ).all()
        for model_id, name in rows:
            used.setdefault(int(model_id), []).append(str(name))
    out: list[dict[str, Any]] = []
    for m in models:
        provider = providers.get(m.provider_id)
        out.append(
            {
                "id": m.id,
                "provider_id": m.provider_id,
                "provider_name": provider.name if provider else "",
                "price_currency": provider.price_currency if provider else None,
                "model_id": m.model_id,
                "display_name": m.display_name,
                "label": model_label(m),
                "supports_tools": bool(m.supports_tools),
                "supports_vision": bool(m.supports_vision),
                "context_tokens": m.context_tokens,
                "max_tool_rounds": m.max_tool_rounds,
                "price_input_per_1m": m.price_input_per_1m,
                "price_output_per_1m": m.price_output_per_1m,
                "valid_id": int(m.valid_id),
                "used_in_profiles": sorted(used.get(m.id, [])),
                "create_time": m.create_time,
                "change_time": m.change_time,
            }
        )
    return out


async def _validated_model_fields(
    session: AsyncSession, fields: ModelFields, *, own_id: int | None
) -> ModelFields:
    provider = await session.get(TiqoraLlmProvider, fields.provider_id)
    if provider is None:
        raise CatalogValidationError(f"Provider {fields.provider_id} gibt es nicht.")
    model_id = fields.model_id.strip()
    if not model_id:
        raise CatalogValidationError("Die Modell-ID beim Provider darf nicht leer sein.")
    for price in (fields.price_input_per_1m, fields.price_output_per_1m):
        if price is not None and price < 0:
            raise CatalogValidationError("Preise dürfen nicht negativ sein.")
    clash = (
        await session.execute(
            select(TiqoraLlmModel.id).where(
                TiqoraLlmModel.provider_id == provider.id,
                TiqoraLlmModel.model_id == model_id,
                TiqoraLlmModel.id != (own_id or 0),
            )
        )
    ).first()
    if clash is not None:
        raise CatalogConflictError(
            f"Das Modell „{model_id}“ ist bei Provider „{provider.name}“ schon angelegt."
        )
    display_name = (fields.display_name or "").strip() or None
    # 0 / negative = what an emptied number input sends → "not set".
    context_tokens = fields.context_tokens if (fields.context_tokens or 0) >= 1 else None
    tool_rounds = fields.max_tool_rounds if (fields.max_tool_rounds or 0) >= 1 else None
    return ModelFields(
        provider_id=provider.id,
        model_id=model_id,
        display_name=display_name,
        supports_tools=fields.supports_tools,
        supports_vision=fields.supports_vision,
        context_tokens=context_tokens,
        max_tool_rounds=tool_rounds,
        price_input_per_1m=fields.price_input_per_1m,
        price_output_per_1m=fields.price_output_per_1m,
        valid_id=fields.valid_id,
    )


def _apply_model_fields(row: TiqoraLlmModel, fields: ModelFields) -> None:
    row.provider_id = fields.provider_id
    row.model_id = fields.model_id
    row.display_name = fields.display_name
    row.supports_tools = fields.supports_tools
    row.supports_vision = fields.supports_vision
    row.context_tokens = fields.context_tokens
    row.max_tool_rounds = fields.max_tool_rounds
    row.price_input_per_1m = fields.price_input_per_1m
    row.price_output_per_1m = fields.price_output_per_1m
    row.valid_id = fields.valid_id


async def create_model(
    session: AsyncSession, fields: ModelFields, *, change_by: int
) -> TiqoraLlmModel:
    clean = await _validated_model_fields(session, fields, own_id=None)
    row = TiqoraLlmModel(provider_id=clean.provider_id, create_by=change_by, change_by=change_by)
    _apply_model_fields(row, clean)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_model(
    session: AsyncSession, row: TiqoraLlmModel, fields: ModelFields, *, change_by: int
) -> TiqoraLlmModel:
    """Full replace. Switching off a capability that a task using one of the
    model's profiles needs is rejected (422, naming the tasks)."""
    clean = await _validated_model_fields(session, fields, own_id=row.id)
    new_caps = _Caps(
        clean.display_name or clean.model_id, clean.supports_tools, clean.supports_vision
    )
    profile_ids = [
        int(pid)
        for pid in (
            await session.execute(
                select(TiqoraLlmProfileEntry.profile_id).where(
                    TiqoraLlmProfileEntry.llm_model_id == row.id
                )
            )
        ).scalars()
    ]
    assignments = await profile_assignments(session, profile_ids)
    lines: list[str] = []
    for pid in profile_ids:
        if pid not in assignments:
            continue
        chain = [
            new_caps if m.id == row.id else _Caps.of(m) for m in await _entry_models(session, pid)
        ]
        lines.extend(_unmet_assignment_lines(assignments[pid], chain))
    if lines:
        raise CatalogValidationError(
            "Das Modell steckt in Profilen für Aufgaben, die das brauchen: "
            + "; ".join(lines)
            + "."
        )
    _apply_model_fields(row, clean)
    row.change_by = change_by
    row.change_time = _now()
    await session.commit()
    await session.refresh(row)
    return row


async def delete_model(session: AsyncSession, row: TiqoraLlmModel) -> None:
    used = await _profile_names_using(session, [row.id])
    if used:
        raise CatalogConflictError(f"Modell wird in Profil(en) {_names(used)} verwendet.")
    await session.delete(row)
    await session.commit()


async def ensure_provider_deletable(session: AsyncSession, provider_id: int) -> None:
    """Deleting a provider cascades to its models; a model inside a profile
    must not disappear that way (409 instead of the RESTRICT FK's 500)."""
    model_ids = [
        int(mid)
        for mid in (
            await session.execute(
                select(TiqoraLlmModel.id).where(TiqoraLlmModel.provider_id == provider_id)
            )
        ).scalars()
    ]
    used = await _profile_names_using(session, model_ids)
    if used:
        raise CatalogConflictError(
            f"Modelle dieses Providers werden in Profil(en) {_names(used)} verwendet."
        )


@dataclass(frozen=True, slots=True)
class ModelProbeResult:
    ok: bool
    model: str | None
    tool_calling_ok: bool
    error: str | None


# Minimal tool schema used to probe tool-calling support on /chat/completions.
_PROBE_TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "ping",
            "description": "Respond with pong.",
            "parameters": {
                "type": "object",
                "properties": {"echo": {"type": "string"}},
                "required": [],
            },
        },
    }
]


async def probe_model_connection(
    provider: TiqoraLlmProvider,
    model: TiqoraLlmModel,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    session: AsyncSession | None = None,
) -> ModelProbeResult:
    """Call ``POST {base_url}/chat/completions`` for *model* with a mini
    prompt (+ a mini tool schema when the model supports tools) to verify
    connectivity/auth, the model id and tool calling.

    ``client`` is injectable (``httpx.MockTransport``); without it a client
    comes from :func:`tiqora.ai.providers.http_client`. With ``session`` the
    call is written to ``tiqora_ai_audit_log`` (``feature="test"``; the probe
    never carries PII).
    """
    from tiqora.security.outbound import OutboundURLError, pin_outbound_url

    api_key = (
        decrypt_secret(settings.secret_key, provider.api_key_enc) if provider.api_key_enc else None
    )
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict[str, object] = {
        "model": model.model_id,
        "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
        "max_tokens": 16,
    }
    if model.supports_tools:
        payload["tools"] = _PROBE_TOOL_SCHEMA

    url = provider.base_url.rstrip("/") + "/chat/completions"
    try:
        pinned = pin_outbound_url(url, allow_private_networks=True)
    except OutboundURLError as exc:
        return ModelProbeResult(
            ok=False, model=None, tool_calling_ok=False, error=f"outbound URL rejected: {exc}"
        )
    owns_client = client is None
    http = client or ai_providers.http_client(_PROBE_TIMEOUT_SECONDS)
    start = time.monotonic()
    status_code: int | None = None
    error: str | None = None
    response_json: str | None = None
    served_model: str | None = None
    try:
        response = await http.post(
            pinned.request_url,
            headers=pinned.request_headers(headers),
            json=payload,
            extensions=pinned.request_extensions(),
        )
        status_code = response.status_code
        if response.status_code >= 400:
            error = f"HTTP {response.status_code}: {response.text[:500]}"
            return ModelProbeResult(ok=False, model=None, tool_calling_ok=False, error=error)
        data = response.json()
        response_json = json.dumps(data)
        if not isinstance(data, dict):
            error = f"Unerwartete Antwort (kein JSON-Objekt): {response.text[:200]}"
            return ModelProbeResult(ok=False, model=None, tool_calling_ok=False, error=error)
        served_model = data.get("model")
        choices = data.get("choices") or []
        tool_calling_ok = False
        if choices:
            message = choices[0].get("message") or {}
            tool_calling_ok = bool(message.get("tool_calls"))
        return ModelProbeResult(
            ok=True, model=served_model, tool_calling_ok=tool_calling_ok, error=None
        )
    except (httpx.HTTPError, ValueError) as exc:
        error = str(exc) or type(exc).__name__
        return ModelProbeResult(ok=False, model=None, tool_calling_ok=False, error=error)
    finally:
        if owns_client:
            await http.aclose()
        if session is not None:
            await write_audit_log(
                session,
                settings=settings,
                context=AuditContext(
                    feature=AUDIT_FEATURE_TEST,
                    provider_id=provider.id,
                    model=served_model or model.model_id,
                    llm_model_id=model.id,
                ),
                request_json=json.dumps(payload),
                response_json=response_json,
                status_code=status_code,
                error=error,
                duration_ms=int((time.monotonic() - start) * 1000),
                prompt_tokens=None,
                completion_tokens=None,
            )


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfileFields:
    name: str
    llm_model_ids: Sequence[int]
    description: str | None = None
    timeout_seconds: int | None = None
    valid_id: int = 1


async def list_profiles(session: AsyncSession) -> list[TiqoraLlmProfile]:
    return list(
        (await session.execute(select(TiqoraLlmProfile).order_by(TiqoraLlmProfile.name)))
        .scalars()
        .all()
    )


async def get_profile(session: AsyncSession, profile_id: int) -> TiqoraLlmProfile | None:
    return await session.get(TiqoraLlmProfile, profile_id)


async def profiles_to_public_dicts(
    session: AsyncSession, profiles: Sequence[TiqoraLlmProfile]
) -> list[dict[str, Any]]:
    ids = [p.id for p in profiles]
    entries: dict[int, list[dict[str, Any]]] = {}
    if ids:
        rows = (
            await session.execute(
                select(TiqoraLlmProfileEntry.profile_id, TiqoraLlmModel, TiqoraLlmProvider)
                .join(TiqoraLlmModel, TiqoraLlmModel.id == TiqoraLlmProfileEntry.llm_model_id)
                .join(TiqoraLlmProvider, TiqoraLlmProvider.id == TiqoraLlmModel.provider_id)
                .where(TiqoraLlmProfileEntry.profile_id.in_(ids))
                .order_by(TiqoraLlmProfileEntry.profile_id, TiqoraLlmProfileEntry.position)
            )
        ).all()
        for profile_id, model, provider in rows:
            entries.setdefault(int(profile_id), []).append(
                {
                    "llm_model_id": model.id,
                    "model_id": model.model_id,
                    "model_label": model_label(model),
                    "provider_id": provider.id,
                    "provider_name": provider.name,
                    "supports_tools": bool(model.supports_tools),
                    "supports_vision": bool(model.supports_vision),
                    "valid_id": int(model.valid_id),
                }
            )
    assignments = await profile_assignments(session, ids)
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "timeout_seconds": p.timeout_seconds,
            "valid_id": int(p.valid_id),
            "entries": entries.get(p.id, []),
            "used_by": [
                {"task": a.task, "queue_policy_id": a.queue_policy_id, "queue_name": a.queue_name}
                for a in assignments.get(p.id, [])
            ],
            "create_time": p.create_time,
            "change_time": p.change_time,
        }
        for p in profiles
    ]


async def _validated_profile_fields(
    session: AsyncSession, fields: ProfileFields, *, own_id: int | None
) -> tuple[ProfileFields, list[TiqoraLlmModel]]:
    name = fields.name.strip()
    if not name:
        raise CatalogValidationError("Der Profilname darf nicht leer sein.")
    ids = list(fields.llm_model_ids)
    if not ids:
        raise CatalogValidationError("Ein Profil braucht mindestens ein Modell.")
    if len(set(ids)) != len(ids):
        raise CatalogValidationError("Ein Modell darf in einem Profil nur einmal vorkommen.")
    found = {
        m.id: m
        for m in (
            await session.execute(select(TiqoraLlmModel).where(TiqoraLlmModel.id.in_(ids)))
        ).scalars()
    }
    missing = [i for i in ids if i not in found]
    if missing:
        raise CatalogValidationError(
            f"Modell(e) {', '.join(str(i) for i in missing)} gibt es nicht."
        )
    clash = (
        await session.execute(
            select(TiqoraLlmProfile.id).where(
                TiqoraLlmProfile.name == name, TiqoraLlmProfile.id != (own_id or 0)
            )
        )
    ).first()
    if clash is not None:
        raise CatalogConflictError(f"Ein Profil „{name}“ gibt es schon.")
    timeout = fields.timeout_seconds if (fields.timeout_seconds or 0) >= 1 else None
    clean = ProfileFields(
        name=name,
        llm_model_ids=ids,
        description=(fields.description or "").strip() or None,
        timeout_seconds=timeout,
        valid_id=fields.valid_id,
    )
    return clean, [found[i] for i in ids]


def _add_entries(session: AsyncSession, profile_id: int, model_ids: Sequence[int]) -> None:
    for position, model_id in enumerate(model_ids):
        session.add(
            TiqoraLlmProfileEntry(profile_id=profile_id, position=position, llm_model_id=model_id)
        )


async def create_profile(
    session: AsyncSession, fields: ProfileFields, *, change_by: int
) -> TiqoraLlmProfile:
    clean, _models = await _validated_profile_fields(session, fields, own_id=None)
    row = TiqoraLlmProfile(
        name=clean.name,
        description=clean.description,
        timeout_seconds=clean.timeout_seconds,
        valid_id=clean.valid_id,
        create_by=change_by,
        change_by=change_by,
    )
    session.add(row)
    await session.flush()
    _add_entries(session, row.id, clean.llm_model_ids)
    await session.commit()
    await session.refresh(row)
    return row


async def update_profile(
    session: AsyncSession, row: TiqoraLlmProfile, fields: ProfileFields, *, change_by: int
) -> TiqoraLlmProfile:
    """Full replace incl. the ordered entries. Rejected (422) when a task the
    profile is assigned to would get a model lacking what it needs, or when
    disabling it would leave a queue feature without a profile."""
    clean, models = await _validated_profile_fields(session, fields, own_id=row.id)
    assignments = (await profile_assignments(session, [row.id])).get(row.id, [])
    lines = _unmet_assignment_lines(assignments, [_Caps.of(m) for m in models])
    if lines:
        raise CatalogValidationError(
            "Das Profil ist Aufgaben zugewiesen, die das brauchen: " + "; ".join(lines) + "."
        )
    if clean.valid_id != 1 and row.valid_id == 1:
        lost = await newly_unserved_queues(session, disabled_profile_id=row.id)
        if lost:
            raise CatalogValidationError(
                "Ohne dieses Profil hätten diese Queues kein Modell mehr für: "
                + "; ".join(lost)
                + "."
            )
    row.name = clean.name
    row.description = clean.description
    row.timeout_seconds = clean.timeout_seconds
    row.valid_id = clean.valid_id
    row.change_by = change_by
    row.change_time = _now()
    await session.execute(
        delete(TiqoraLlmProfileEntry).where(TiqoraLlmProfileEntry.profile_id == row.id)
    )
    await session.flush()
    _add_entries(session, row.id, clean.llm_model_ids)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_profile(session: AsyncSession, row: TiqoraLlmProfile) -> None:
    assignments = (await profile_assignments(session, [row.id])).get(row.id, [])
    if assignments:
        raise CatalogConflictError(
            "Profil wird verwendet für: " + ", ".join(a.label() for a in assignments) + "."
        )
    await session.delete(row)
    await session.commit()


# ---------------------------------------------------------------------------
# Task assignments
# ---------------------------------------------------------------------------


async def normalize_task_profiles(
    session: AsyncSession, items: Iterable[tuple[str, int | None]]
) -> dict[str, int | None]:
    """Validate ``(task, profile_id)`` pairs → ``{task: profile_id}``: known
    task, each task once, profile exists and meets the task's needs."""
    out: dict[str, int | None] = {}
    for task, profile_id in items:
        if task not in ALL_TASKS:
            raise CatalogValidationError(
                f"Unbekannte Aufgabe {task!r} (erlaubt: {', '.join(ALL_TASKS)})."
            )
        if task in out:
            raise CatalogValidationError(f"Aufgabe „{TASK_LABELS[task]}“ ist mehrfach angegeben.")
        if profile_id is not None:
            await check_profile_fits_task(session, profile_id, task)
        out[task] = profile_id
    return out


async def get_task_defaults(session: AsyncSession) -> list[dict[str, Any]]:
    """All six tasks in :data:`ALL_TASKS` order; no row = ``None``."""
    rows = (
        await session.execute(select(TiqoraAiTaskDefault.task, TiqoraAiTaskDefault.profile_id))
    ).all()
    stored = {str(task): pid for task, pid in rows}
    return [{"task": task, "profile_id": stored.get(task)} for task in ALL_TASKS]


async def put_task_defaults(
    session: AsyncSession, items: Iterable[tuple[str, int | None]]
) -> list[dict[str, Any]]:
    """Replace all global defaults (tasks not listed → no profile). 422 on
    bad input or when a queue feature would lose its profile."""
    mapping = await normalize_task_profiles(session, items)
    lost = await newly_unserved_queues(session, defaults=mapping)
    if lost:
        raise CatalogValidationError(
            "Mit diesen Vorgaben hätten diese Queues kein Modell mehr für: " + "; ".join(lost) + "."
        )
    await session.execute(delete(TiqoraAiTaskDefault))
    for task in ALL_TASKS:
        profile_id = mapping.get(task)
        if profile_id is not None:
            session.add(TiqoraAiTaskDefault(task=task, profile_id=profile_id))
    await session.commit()
    return await get_task_defaults(session)


async def load_queue_task_profiles(
    session: AsyncSession, policy_ids: Collection[int]
) -> dict[int, dict[str, int | None]]:
    """``{policy_id: {task: profile_id | None}}`` — only the overrides."""
    if not policy_ids:
        return {}
    rows = (
        await session.execute(
            select(
                TiqoraAiQueueTaskProfile.queue_policy_id,
                TiqoraAiQueueTaskProfile.task,
                TiqoraAiQueueTaskProfile.profile_id,
            ).where(TiqoraAiQueueTaskProfile.queue_policy_id.in_(policy_ids))
        )
    ).all()
    out: dict[int, dict[str, int | None]] = {}
    for policy_id, task, profile_id in rows:
        out.setdefault(int(policy_id), {})[str(task)] = (
            int(profile_id) if profile_id is not None else None
        )
    return out


def task_profile_items(overrides: Mapping[str, int | None]) -> list[dict[str, Any]]:
    """Overrides as API items, in :data:`ALL_TASKS` order."""
    return [
        {"task": task, "profile_id": overrides[task]} for task in ALL_TASKS if task in overrides
    ]


async def replace_queue_task_profiles(
    session: AsyncSession, policy_id: int, overrides: Mapping[str, int | None]
) -> None:
    """Replace a queue's overrides (no commit — the caller's transaction)."""
    await session.execute(
        delete(TiqoraAiQueueTaskProfile).where(
            TiqoraAiQueueTaskProfile.queue_policy_id == policy_id
        )
    )
    for task in ALL_TASKS:
        if task in overrides:
            session.add(
                TiqoraAiQueueTaskProfile(
                    queue_policy_id=policy_id, task=task, profile_id=overrides[task]
                )
            )


__all__ = [
    "POLICY_FEATURE_TASKS",
    "TASK_LABELS",
    "Assignment",
    "CatalogConflictError",
    "CatalogValidationError",
    "ModelFields",
    "ModelProbeResult",
    "ProfileFields",
    "check_policy_llm_requirements",
    "check_profile_fits_task",
    "create_model",
    "create_profile",
    "delete_model",
    "delete_profile",
    "ensure_provider_deletable",
    "get_model",
    "get_profile",
    "get_task_defaults",
    "list_models",
    "list_profiles",
    "load_queue_task_profiles",
    "model_label",
    "models_to_public_dicts",
    "newly_unserved_queues",
    "normalize_task_profiles",
    "probe_model_connection",
    "profile_assignments",
    "profiles_to_public_dicts",
    "put_task_defaults",
    "replace_queue_task_profiles",
    "task_profile_items",
    "update_model",
    "update_profile",
]
