"""LLM routing: model catalog, profiles and task assignments.

Revision ID: 20260930_0053
Revises: 20260930_0052
Create Date: 2026-09-30

Replaces the per-queue model fields (``llm_provider_id``/``model_override``/
``llm_fallback_json``, the final-answer, triage and vision provider fields)
and the per-provider model settings (``default_model``, capability flags,
tool rounds, prices) with four levels:

* ``tiqora_llm_model`` — one model at one provider, with the provider's exact
  API model id, its capabilities and its price;
* ``tiqora_llm_profile`` + ``tiqora_llm_profile_entry`` — ordered fallback
  chains of models;
* ``tiqora_ai_task_default`` — the global profile per task;
* ``tiqora_ai_queue_task_profile`` — per-queue overrides (``profile_id`` NULL
  = "no own profile", i.e. the task's fallback behaviour).

Every existing queue policy is converted 1:1, so each queue resolves exactly
the models it used before (see :func:`_plan_conversion`, a pure function so
it can be unit-tested without a database). One deliberate difference: a
triage provider without a triage model now uses *that* provider's default
model, not the queue's ``model_override`` meant for another provider.
"""

# No ``from __future__ import annotations``: Alembic executes this file
# without registering it in sys.modules, and dataclasses resolve string
# annotations through sys.modules (AttributeError at import time).
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260930_0053"
down_revision: str | None = "20260930_0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASKS: tuple[str, ...] = ("agent", "final_answer", "triage", "summary", "refine", "vision")

_POLICY_DROPPED = (
    "llm_provider_id",
    "model_override",
    "llm_fallback_json",
    "final_answer_llm_provider_id",
    "final_answer_model_override",
    "vision_provider_id",
    "triage_llm_provider_id",
    "triage_model_override",
)
_PROVIDER_DROPPED = (
    "default_model",
    "supports_tools",
    "supports_streaming",
    "supports_vision",
    "max_tool_rounds",
    "price_input_per_1m",
    "price_output_per_1m",
)
_PROFILE_NAME_MAX = 200
_SYSTEM_USER_ID = 1


# ---------------------------------------------------------------------------
# Pure conversion plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderRow:
    id: int
    name: str
    default_model: str
    supports_tools: bool = True
    supports_vision: bool = False
    max_tool_rounds: int | None = None
    price_input_per_1m: float | None = None
    price_output_per_1m: float | None = None


@dataclass(frozen=True, slots=True)
class PolicyRow:
    id: int
    llm_provider_id: int | None = None
    model_override: str | None = None
    llm_fallback_json: str | None = None
    final_answer_llm_provider_id: int | None = None
    final_answer_model_override: str | None = None
    vision_provider_id: int | None = None
    triage_llm_provider_id: int | None = None
    triage_model_override: str | None = None


ModelKey = tuple[int, str]  # (provider_id, API model id)


@dataclass(frozen=True, slots=True)
class ModelPlan:
    provider_id: int
    model_id: str
    supports_tools: bool
    supports_vision: bool
    max_tool_rounds: int | None
    price_input_per_1m: float | None
    price_output_per_1m: float | None

    @property
    def key(self) -> ModelKey:
        return (self.provider_id, self.model_id)


@dataclass(frozen=True, slots=True)
class ProfilePlan:
    name: str
    models: tuple[ModelKey, ...]


@dataclass(slots=True)
class ConversionPlan:
    models: list[ModelPlan] = field(default_factory=list)
    # Profiles are referenced by their index in this list (the DB ids do not
    # exist yet); "lowest profile id" in the tie rule means lowest index.
    profiles: list[ProfilePlan] = field(default_factory=list)
    # task -> profile index; only tasks whose global default is a profile.
    task_defaults: dict[str, int] = field(default_factory=dict)
    # (policy id, task, profile index or None = "no own profile")
    queue_overrides: list[tuple[int, str, int | None]] = field(default_factory=list)
    # For tests/inspection: every policy's effective chain per task.
    chains: dict[tuple[int, str], tuple[ModelKey, ...]] = field(default_factory=dict)


def _parse_fallback(raw: str | None, providers: dict[int, ProviderRow]) -> list[ModelKey]:
    """Same tolerance as the old runtime parser: malformed JSON, non-dict
    entries, non-int provider ids and providers that no longer exist are
    skipped, never fatal."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[ModelKey] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        provider_id = item.get("provider_id")
        if not isinstance(provider_id, int) or isinstance(provider_id, bool):
            continue
        provider = providers.get(provider_id)
        if provider is None:
            continue
        model = item.get("model")
        out.append(
            (provider_id, model if isinstance(model, str) and model else provider.default_model)
        )
    return out


def _key(
    providers: dict[int, ProviderRow], provider_id: int | None, model: str | None
) -> ModelKey | None:
    if provider_id is None:
        return None
    provider = providers.get(provider_id)
    if provider is None:
        return None
    return (provider_id, model or provider.default_model)


def _dedupe(chain: list[ModelKey]) -> tuple[ModelKey, ...]:
    seen: set[ModelKey] = set()
    out: list[ModelKey] = []
    for key in chain:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out)


def _policy_chains(
    policy: PolicyRow, providers: dict[int, ProviderRow]
) -> dict[str, tuple[ModelKey, ...]]:
    chains: dict[str, tuple[ModelKey, ...]] = {}
    fallback = _parse_fallback(policy.llm_fallback_json, providers)

    primary = _key(providers, policy.llm_provider_id, policy.model_override)
    if primary is not None:
        chains["agent"] = _dedupe([primary, *fallback])

    final = _key(providers, policy.final_answer_llm_provider_id, policy.final_answer_model_override)
    if final is not None:
        chains["final_answer"] = (final,)

    if policy.triage_llm_provider_id is not None or policy.triage_model_override:
        triage_provider = policy.triage_llm_provider_id or policy.llm_provider_id
        # The triage provider's own default model — never the queue's
        # model_override, which names a model at a (possibly) other provider.
        triage = _key(providers, triage_provider, policy.triage_model_override)
        if triage is not None:
            chains["triage"] = _dedupe([triage, *fallback])

    vision = _key(providers, policy.vision_provider_id, None)
    if vision is not None:
        chains["vision"] = (vision,)
    return chains


def _profile_base_name(chain: tuple[ModelKey, ...], providers: dict[int, ProviderRow]) -> str:
    provider_id, model_id = chain[0]
    base = f"{model_id} @ {providers[provider_id].name}"
    if len(chain) > 1:
        base += f" +{len(chain) - 1}"
    return base


def _unique_name(base: str, taken: set[str]) -> str:
    candidate = base[:_PROFILE_NAME_MAX]
    suffix = 2
    while candidate in taken:
        tail = f" ({suffix})"
        candidate = base[: _PROFILE_NAME_MAX - len(tail)] + tail
        suffix += 1
    taken.add(candidate)
    return candidate


def _global_value(values: list[int | None]) -> int | None:
    """Most frequent value; ties prefer "none", then the lowest profile index."""
    if not values:
        return None
    counts = Counter(values)
    best = max(counts.values())
    tied = [v for v, c in counts.items() if c == best]
    if None in tied:
        return None
    return min(v for v in tied if v is not None)


def _plan_conversion(providers: list[ProviderRow], policies: list[PolicyRow]) -> ConversionPlan:
    plan = ConversionPlan()
    by_id = {p.id: p for p in providers}
    known_models: dict[ModelKey, ModelPlan] = {}

    def _ensure_model(key: ModelKey) -> None:
        if key in known_models:
            return
        provider = by_id[key[0]]
        row = ModelPlan(
            provider_id=provider.id,
            model_id=key[1],
            supports_tools=bool(provider.supports_tools),
            supports_vision=bool(provider.supports_vision),
            max_tool_rounds=provider.max_tool_rounds,
            price_input_per_1m=provider.price_input_per_1m,
            price_output_per_1m=provider.price_output_per_1m,
        )
        known_models[key] = row
        plan.models.append(row)

    # 1. Every provider's default model.
    for provider in sorted(providers, key=lambda p: p.id):
        _ensure_model((provider.id, provider.default_model))

    # 2./3. Per-policy chains (creating any referenced model on the way).
    profile_index: dict[tuple[ModelKey, ...], int] = {}
    taken_names: set[str] = set()
    values: dict[str, dict[int, int | None]] = {task: {} for task in TASKS}
    for policy in sorted(policies, key=lambda p: p.id):
        chains = _policy_chains(policy, by_id)
        for task in TASKS:
            chain = chains.get(task)
            if chain is None:
                values[task][policy.id] = None
                continue
            plan.chains[(policy.id, task)] = chain
            for key in chain:
                _ensure_model(key)
            # 4. Identical chains share one profile.
            if chain not in profile_index:
                name = _unique_name(_profile_base_name(chain, by_id), taken_names)
                profile_index[chain] = len(plan.profiles)
                plan.profiles.append(ProfilePlan(name=name, models=chain))
            values[task][policy.id] = profile_index[chain]

    # 5. Global default = most frequent value; queue rows where they differ.
    for task in TASKS:
        per_policy = values[task]
        global_value = _global_value(list(per_policy.values()))
        if global_value is not None:
            plan.task_defaults[task] = global_value
        for policy_id, value in per_policy.items():
            if value != global_value:
                plan.queue_overrides.append((policy_id, task, value))
    return plan


# ---------------------------------------------------------------------------
# DDL + data movement
# ---------------------------------------------------------------------------


def _audit_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("valid_id", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("create_by", sa.Integer(), nullable=False),
        sa.Column(
            "create_time",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("change_by", sa.Integer(), nullable=False),
        sa.Column(
            "change_time",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    ]


def _create_tables() -> None:
    op.create_table(
        "tiqora_llm_model",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("context_tokens", sa.Integer(), nullable=True),
        sa.Column("max_tool_rounds", sa.Integer(), nullable=True),
        sa.Column("price_input_per_1m", sa.Float(), nullable=True),
        sa.Column("price_output_per_1m", sa.Float(), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_tiqora_llm_model_provider_model"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["tiqora_llm_provider.id"],
            name="fk_tiqora_llm_model_provider",
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "tiqora_llm_profile",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tiqora_llm_profile_name"),
    )
    op.create_table(
        "tiqora_llm_profile_entry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("llm_model_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "position", name="uq_tiqora_llm_profile_entry_position"),
        sa.UniqueConstraint("profile_id", "llm_model_id", name="uq_tiqora_llm_profile_entry_model"),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["tiqora_llm_profile.id"],
            name="fk_tiqora_llm_profile_entry_profile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["llm_model_id"],
            ["tiqora_llm_model.id"],
            name="fk_tiqora_llm_profile_entry_model",
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "tiqora_ai_task_default",
        sa.Column("task", sa.String(30), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("task"),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["tiqora_llm_profile.id"],
            name="fk_tiqora_ai_task_default_profile",
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "tiqora_ai_queue_task_profile",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("queue_policy_id", sa.Integer(), nullable=False),
        sa.Column("task", sa.String(30), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "queue_policy_id", "task", name="uq_tiqora_ai_queue_task_profile_policy_task"
        ),
        sa.ForeignKeyConstraint(
            ["queue_policy_id"],
            ["tiqora_ai_queue_policy.id"],
            name="fk_tiqora_ai_queue_task_profile_policy",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["tiqora_llm_profile.id"],
            name="fk_tiqora_ai_queue_task_profile_profile",
            ondelete="RESTRICT",
        ),
    )


# Minimal Table objects (with primary keys, so inserted_primary_key works on
# MariaDB and PostgreSQL alike) — never the ORM models, which follow HEAD.
_meta = sa.MetaData()
_model_t = sa.Table(
    "tiqora_llm_model",
    _meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("provider_id", sa.Integer),
    sa.Column("model_id", sa.String(200)),
    sa.Column("supports_tools", sa.Boolean),
    sa.Column("supports_vision", sa.Boolean),
    sa.Column("max_tool_rounds", sa.Integer),
    sa.Column("price_input_per_1m", sa.Float),
    sa.Column("price_output_per_1m", sa.Float),
    sa.Column("valid_id", sa.SmallInteger),
    sa.Column("create_by", sa.Integer),
    sa.Column("change_by", sa.Integer),
)
_profile_t = sa.Table(
    "tiqora_llm_profile",
    _meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String(200)),
    sa.Column("valid_id", sa.SmallInteger),
    sa.Column("create_by", sa.Integer),
    sa.Column("change_by", sa.Integer),
)
_entry_t = sa.Table(
    "tiqora_llm_profile_entry",
    _meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("profile_id", sa.Integer),
    sa.Column("position", sa.Integer),
    sa.Column("llm_model_id", sa.Integer),
)
_default_t = sa.Table(
    "tiqora_ai_task_default",
    _meta,
    sa.Column("task", sa.String(30), primary_key=True),
    sa.Column("profile_id", sa.Integer),
)
_queue_task_t = sa.Table(
    "tiqora_ai_queue_task_profile",
    _meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("queue_policy_id", sa.Integer),
    sa.Column("task", sa.String(30)),
    sa.Column("profile_id", sa.Integer),
)


def _insert_id(bind: sa.Connection, stmt: Any) -> int:
    result = bind.execute(stmt)
    pk = result.inserted_primary_key
    assert pk is not None and pk[0] is not None
    return int(pk[0])


def _write_plan(bind: sa.Connection, plan: ConversionPlan) -> None:
    model_ids: dict[ModelKey, int] = {}
    for model in plan.models:
        model_ids[model.key] = _insert_id(
            bind,
            _model_t.insert().values(
                provider_id=model.provider_id,
                model_id=model.model_id,
                supports_tools=model.supports_tools,
                supports_vision=model.supports_vision,
                max_tool_rounds=model.max_tool_rounds,
                price_input_per_1m=model.price_input_per_1m,
                price_output_per_1m=model.price_output_per_1m,
                valid_id=1,
                create_by=_SYSTEM_USER_ID,
                change_by=_SYSTEM_USER_ID,
            ),
        )
    profile_ids: list[int] = []
    for profile in plan.profiles:
        profile_id = _insert_id(
            bind,
            _profile_t.insert().values(
                name=profile.name,
                valid_id=1,
                create_by=_SYSTEM_USER_ID,
                change_by=_SYSTEM_USER_ID,
            ),
        )
        profile_ids.append(profile_id)
        for position, key in enumerate(profile.models):
            bind.execute(
                _entry_t.insert().values(
                    profile_id=profile_id, position=position, llm_model_id=model_ids[key]
                )
            )
    for task, index in plan.task_defaults.items():
        bind.execute(_default_t.insert().values(task=task, profile_id=profile_ids[index]))
    for policy_id, task, index in plan.queue_overrides:
        bind.execute(
            _queue_task_t.insert().values(
                queue_policy_id=policy_id,
                task=task,
                profile_id=None if index is None else profile_ids[index],
            )
        )


def _drop_fks_on(table: str, columns: Sequence[str]) -> None:
    """Drop every FK on *table* that covers one of *columns*.

    Introspected rather than hardcoded so a database whose constraint names
    differ from the migration chain's (built from metadata) still upgrades.
    """
    bind = op.get_bind()
    wanted = set(columns)
    for fk in sa.inspect(bind).get_foreign_keys(table):
        if fk.get("name") and wanted.intersection(fk["constrained_columns"]):
            op.drop_constraint(fk["name"], table, type_="foreignkey")


def upgrade() -> None:
    _create_tables()
    bind = op.get_bind()

    providers = [
        ProviderRow(
            id=int(r.id),
            name=str(r.name),
            default_model=str(r.default_model or ""),
            supports_tools=bool(r.supports_tools),
            supports_vision=bool(r.supports_vision),
            max_tool_rounds=r.max_tool_rounds,
            price_input_per_1m=r.price_input_per_1m,
            price_output_per_1m=r.price_output_per_1m,
        )
        for r in bind.execute(
            sa.text(
                "SELECT id, name, default_model, supports_tools, supports_vision, "
                "max_tool_rounds, price_input_per_1m, price_output_per_1m "
                "FROM tiqora_llm_provider"
            )
        )
    ]
    policies = [
        PolicyRow(
            id=int(r.id),
            llm_provider_id=r.llm_provider_id,
            model_override=r.model_override,
            llm_fallback_json=r.llm_fallback_json,
            final_answer_llm_provider_id=r.final_answer_llm_provider_id,
            final_answer_model_override=r.final_answer_model_override,
            vision_provider_id=r.vision_provider_id,
            triage_llm_provider_id=r.triage_llm_provider_id,
            triage_model_override=r.triage_model_override,
        )
        for r in bind.execute(
            sa.text(
                "SELECT id, llm_provider_id, model_override, llm_fallback_json, "
                "final_answer_llm_provider_id, final_answer_model_override, "
                "vision_provider_id, triage_llm_provider_id, triage_model_override "
                "FROM tiqora_ai_queue_policy"
            )
        )
    ]
    _write_plan(bind, _plan_conversion(providers, policies))

    _drop_fks_on("tiqora_ai_queue_policy", _POLICY_DROPPED)
    for column in _POLICY_DROPPED:
        op.drop_column("tiqora_ai_queue_policy", column)
    for column in _PROVIDER_DROPPED:
        op.drop_column("tiqora_llm_provider", column)


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def _load_chains(bind: sa.Connection) -> dict[int, list[tuple[int, str]]]:
    chains: dict[int, list[tuple[int, str]]] = {}
    rows = bind.execute(
        sa.text(
            "SELECT e.profile_id, m.provider_id, m.model_id "
            "FROM tiqora_llm_profile_entry e "
            "JOIN tiqora_llm_model m ON m.id = e.llm_model_id "
            "ORDER BY e.profile_id, e.position"
        )
    )
    for r in rows:
        chains.setdefault(int(r.profile_id), []).append((int(r.provider_id), str(r.model_id)))
    return chains


def downgrade() -> None:
    bind = op.get_bind()

    op.add_column("tiqora_llm_provider", sa.Column("default_model", sa.String(200), nullable=True))
    op.add_column(
        "tiqora_llm_provider",
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.add_column(
        "tiqora_llm_provider",
        sa.Column("supports_streaming", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.add_column(
        "tiqora_llm_provider",
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column("tiqora_llm_provider", sa.Column("max_tool_rounds", sa.Integer(), nullable=True))
    op.add_column("tiqora_llm_provider", sa.Column("price_input_per_1m", sa.Float(), nullable=True))
    op.add_column(
        "tiqora_llm_provider", sa.Column("price_output_per_1m", sa.Float(), nullable=True)
    )

    policy_columns: list[sa.Column[Any]] = [
        sa.Column("llm_provider_id", sa.Integer(), nullable=True),
        sa.Column("model_override", sa.String(200), nullable=True),
        sa.Column("llm_fallback_json", sa.Text(), nullable=True),
        sa.Column("final_answer_llm_provider_id", sa.Integer(), nullable=True),
        sa.Column("final_answer_model_override", sa.String(200), nullable=True),
        sa.Column("vision_provider_id", sa.Integer(), nullable=True),
        sa.Column("triage_llm_provider_id", sa.Integer(), nullable=True),
        sa.Column("triage_model_override", sa.String(200), nullable=True),
    ]
    for column in policy_columns:
        op.add_column("tiqora_ai_queue_policy", column)

    # Provider settings back from each provider's first model.
    first_models = bind.execute(
        sa.text(
            "SELECT m.provider_id, m.model_id, m.supports_tools, m.supports_vision, "
            "m.max_tool_rounds, m.price_input_per_1m, m.price_output_per_1m "
            "FROM tiqora_llm_model m "
            "WHERE m.id = (SELECT MIN(m2.id) FROM tiqora_llm_model m2 "
            "WHERE m2.provider_id = m.provider_id)"
        )
    ).fetchall()
    for r in first_models:
        bind.execute(
            sa.text(
                "UPDATE tiqora_llm_provider SET default_model = :model, "
                "supports_tools = :tools, supports_vision = :vision, "
                "max_tool_rounds = :rounds, price_input_per_1m = :pin, "
                "price_output_per_1m = :pout WHERE id = :pid"
            ),
            {
                "model": r.model_id,
                "tools": bool(r.supports_tools),
                "vision": bool(r.supports_vision),
                "rounds": r.max_tool_rounds,
                "pin": r.price_input_per_1m,
                "pout": r.price_output_per_1m,
                "pid": r.provider_id,
            },
        )
    bind.execute(
        sa.text("UPDATE tiqora_llm_provider SET default_model = '' WHERE default_model IS NULL")
    )
    op.alter_column(
        "tiqora_llm_provider", "default_model", existing_type=sa.String(200), nullable=False
    )

    # Policy fields back from each queue's effective resolution.
    chains = _load_chains(bind)
    defaults = {
        str(r.task): r.profile_id
        for r in bind.execute(sa.text("SELECT task, profile_id FROM tiqora_ai_task_default"))
    }
    overrides: dict[int, dict[str, int | None]] = {}
    for r in bind.execute(
        sa.text("SELECT queue_policy_id, task, profile_id FROM tiqora_ai_queue_task_profile")
    ):
        overrides.setdefault(int(r.queue_policy_id), {})[str(r.task)] = r.profile_id
    policy_ids = [int(r.id) for r in bind.execute(sa.text("SELECT id FROM tiqora_ai_queue_policy"))]
    for policy_id in policy_ids:
        own = overrides.get(policy_id, {})

        def _chain(task: str, own: dict[str, int | None] = own) -> list[tuple[int, str]]:
            profile_id = own[task] if task in own else defaults.get(task)
            return chains.get(int(profile_id), []) if profile_id is not None else []

        values: dict[str, Any] = {}
        agent = _chain("agent")
        if agent:
            values["llm_provider_id"], values["model_override"] = agent[0]
            if len(agent) > 1:
                values["llm_fallback_json"] = json.dumps(
                    [{"provider_id": p, "model": m} for p, m in agent[1:]]
                )
        final = _chain("final_answer")
        if final:
            values["final_answer_llm_provider_id"], values["final_answer_model_override"] = final[0]
        triage = _chain("triage")
        if triage:
            values["triage_llm_provider_id"], values["triage_model_override"] = triage[0]
        vision = _chain("vision")
        if vision:
            values["vision_provider_id"] = vision[0][0]
        if values:
            assignments = ", ".join(f"{name} = :{name}" for name in values)
            bind.execute(
                sa.text(f"UPDATE tiqora_ai_queue_policy SET {assignments} WHERE id = :pid"),
                {**values, "pid": policy_id},
            )

    for name, column in (
        ("fk_tiqora_ai_queue_policy_provider", "llm_provider_id"),
        ("fk_tiqora_ai_queue_policy_vision_provider", "vision_provider_id"),
        ("fk_tiqora_ai_queue_policy_triage_llm_provider", "triage_llm_provider_id"),
        ("fk_tiqora_ai_queue_policy_final_answer_llm_provider", "final_answer_llm_provider_id"),
    ):
        op.create_foreign_key(
            name,
            "tiqora_ai_queue_policy",
            "tiqora_llm_provider",
            [column],
            ["id"],
            ondelete="SET NULL",
        )

    op.drop_table("tiqora_ai_queue_task_profile")
    op.drop_table("tiqora_ai_task_default")
    op.drop_table("tiqora_llm_profile_entry")
    op.drop_table("tiqora_llm_profile")
    op.drop_table("tiqora_llm_model")
