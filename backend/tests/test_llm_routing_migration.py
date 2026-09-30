"""Migration 20260930_0053 (LLM routing): the pure conversion plan.

``_plan_conversion`` turns the old per-queue model fields into models,
profiles, global task defaults and per-queue overrides. Each queue must
resolve exactly the models it used before (except the triage bug fix).
The DB round trip lives in ``test_llm_routing_migration_db.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions_tiqora"
    / "20260930_0053_llm_routing.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_mig_0053", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Deliberately not registered in sys.modules — Alembic does not either.
    spec.loader.exec_module(module)
    return module


mig = _load()


def _provider(pid: int, name: str, model: str, **kw: Any) -> Any:
    return mig.ProviderRow(id=pid, name=name, default_model=model, **kw)


def _policy(pid: int, **kw: Any) -> Any:
    return mig.PolicyRow(id=pid, **kw)


def _resolve(plan: Any, policy_id: int, task: str) -> tuple[tuple[int, str], ...] | None:
    """Resolve like the runtime: queue row > global default > nothing."""
    for pol, t, index in plan.queue_overrides:
        if pol == policy_id and t == task:
            return None if index is None else plan.profiles[index].models
    index = plan.task_defaults.get(task)
    return None if index is None else plan.profiles[index].models


def test_single_provider_single_policy() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "Nebius", "qwen", max_tool_rounds=7, price_input_per_1m=0.5)],
        [_policy(10, llm_provider_id=1)],
    )
    assert [(m.provider_id, m.model_id) for m in plan.models] == [(1, "qwen")]
    assert plan.models[0].max_tool_rounds == 7
    assert plan.models[0].price_input_per_1m == 0.5
    assert [p.name for p in plan.profiles] == ["qwen @ Nebius"]
    assert plan.task_defaults == {"agent": 0}
    assert plan.queue_overrides == []
    assert _resolve(plan, 10, "agent") == ((1, "qwen"),)
    for task in ("final_answer", "triage", "summary", "refine", "vision"):
        assert _resolve(plan, 10, task) is None


def test_fallback_to_deleted_provider_is_skipped() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "A", "a-model"), _provider(2, "B", "b-model")],
        [
            _policy(
                10,
                llm_provider_id=1,
                llm_fallback_json=json.dumps(
                    [
                        {"provider_id": 99, "model": "gone"},
                        {"provider_id": 2, "model": None},
                        {"provider_id": "x"},
                    ]
                ),
            )
        ],
    )
    assert _resolve(plan, 10, "agent") == ((1, "a-model"), (2, "b-model"))
    assert [p.name for p in plan.profiles] == ["a-model @ A +1"]
    assert all(m.provider_id != 99 for m in plan.models)


def test_identical_chains_share_a_profile() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "A", "a-model")],
        [
            _policy(10, llm_provider_id=1, model_override="big"),
            _policy(11, llm_provider_id=1, model_override="big"),
        ],
    )
    assert len(plan.profiles) == 1
    assert plan.task_defaults == {"agent": 0}
    assert _resolve(plan, 10, "agent") == _resolve(plan, 11, "agent") == ((1, "big"),)


def test_override_equal_to_default_model_creates_no_duplicate_model() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "A", "a-model")],
        [_policy(10, llm_provider_id=1, model_override="a-model")],
    )
    assert [(m.provider_id, m.model_id) for m in plan.models] == [(1, "a-model")]


def test_profile_name_collision_gets_suffix() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "A", "a"), _provider(2, "B", "b"), _provider(3, "C", "c")],
        [
            _policy(10, llm_provider_id=1, llm_fallback_json=json.dumps([{"provider_id": 2}])),
            _policy(11, llm_provider_id=1, llm_fallback_json=json.dumps([{"provider_id": 3}])),
            _policy(
                12,
                llm_provider_id=1,
                llm_fallback_json=json.dumps([{"provider_id": 2}, {"provider_id": 3}]),
            ),
        ],
    )
    assert [p.name for p in plan.profiles] == ["a @ A +1", "a @ A +1 (2)", "a @ A +2"]


def test_triage_provider_without_model_uses_its_own_default_model() -> None:
    """Bug fix: the old runtime sent the queue's model_override (a model at
    provider A) to the triage provider B."""
    plan = mig._plan_conversion(
        [_provider(1, "A", "a-model"), _provider(2, "B", "b-small")],
        [
            _policy(
                10,
                llm_provider_id=1,
                model_override="a-big",
                triage_llm_provider_id=2,
                llm_fallback_json=json.dumps([{"provider_id": 1, "model": "a-mini"}]),
            )
        ],
    )
    assert _resolve(plan, 10, "triage") == ((2, "b-small"), (1, "a-mini"))
    assert _resolve(plan, 10, "agent") == ((1, "a-big"), (1, "a-mini"))


def test_triage_model_without_provider_uses_the_agent_provider() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "A", "a-model")],
        [_policy(10, llm_provider_id=1, triage_model_override="a-cheap")],
    )
    assert _resolve(plan, 10, "triage") == ((1, "a-cheap"),)


def test_final_answer_and_vision_chains() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "A", "a"), _provider(2, "V", "v-model", supports_vision=True)],
        [
            _policy(
                10,
                llm_provider_id=1,
                final_answer_llm_provider_id=1,
                final_answer_model_override="a-strong",
                vision_provider_id=2,
            )
        ],
    )
    assert _resolve(plan, 10, "final_answer") == ((1, "a-strong"),)
    assert _resolve(plan, 10, "vision") == ((2, "v-model"),)
    vision_model = next(m for m in plan.models if m.provider_id == 2)
    assert vision_model.supports_vision is True


def test_global_default_is_most_frequent_and_queue_rows_only_differ() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "A", "a"), _provider(2, "V", "v", supports_vision=True)],
        [
            _policy(10, llm_provider_id=1, vision_provider_id=2),
            _policy(11, llm_provider_id=1, vision_provider_id=2),
            _policy(12, llm_provider_id=1, final_answer_llm_provider_id=2),
        ],
    )
    # agent: all three share → global, no queue rows.
    # vision: 2 of 3 → global; policy 12 gets an explicit NULL row.
    # final_answer: 1 of 3 → global "none"; policy 12 gets a profile row.
    agent = plan.task_defaults["agent"]
    vision = plan.task_defaults["vision"]
    assert "final_answer" not in plan.task_defaults
    assert plan.profiles[agent].models == ((1, "a"),)
    assert plan.profiles[vision].models == ((2, "v"),)
    rows = sorted(plan.queue_overrides, key=lambda r: (r[0], r[1]))
    final_index = next(i for i, p in enumerate(plan.profiles) if p.models == ((2, "v"),))
    assert rows == [(12, "final_answer", final_index), (12, "vision", None)]
    for pid in (10, 11, 12):
        assert _resolve(plan, pid, "agent") == ((1, "a"),)
    assert _resolve(plan, 12, "vision") is None
    assert _resolve(plan, 10, "vision") == ((2, "v"),)


def test_tie_prefers_none_then_lowest_profile() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "A", "a"), _provider(2, "B", "b")],
        [
            _policy(10, llm_provider_id=2),
            _policy(11, llm_provider_id=1),
            _policy(12, final_answer_llm_provider_id=1),
            _policy(13),
        ],
    )
    # agent: b, a, none, none → "none" is the most frequent value.
    assert "agent" not in plan.task_defaults
    tie = mig._plan_conversion(
        [_provider(1, "A", "a"), _provider(2, "B", "b")],
        [_policy(10, llm_provider_id=2), _policy(11, llm_provider_id=1)],
    )
    # b's profile was created first → lowest index wins the 1:1 tie.
    assert tie.profiles[tie.task_defaults["agent"]].models == ((2, "b"),)
    assert tie.queue_overrides == [
        (11, "agent", next(i for i, p in enumerate(tie.profiles) if p.models == ((1, "a"),)))
    ]
    none_tie = mig._plan_conversion(
        [_provider(1, "A", "a")], [_policy(10, llm_provider_id=1), _policy(11)]
    )
    assert "agent" not in none_tie.task_defaults
    assert none_tie.queue_overrides == [(10, "agent", 0)]


def test_policy_without_provider_has_no_agent() -> None:
    plan = mig._plan_conversion([_provider(1, "A", "a")], [_policy(10)])
    assert plan.profiles == []
    assert plan.task_defaults == {}
    assert plan.queue_overrides == []
    assert _resolve(plan, 10, "agent") is None
    # Every provider still gets its default model row.
    assert [(m.provider_id, m.model_id) for m in plan.models] == [(1, "a")]


def test_duplicate_fallback_entries_are_removed_keeping_first() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "A", "a"), _provider(2, "B", "b")],
        [
            _policy(
                10,
                llm_provider_id=1,
                llm_fallback_json=json.dumps(
                    [{"provider_id": 1, "model": None}, {"provider_id": 2}, {"provider_id": 2}]
                ),
            )
        ],
    )
    assert _resolve(plan, 10, "agent") == ((1, "a"), (2, "b"))


def test_triage_on_the_agent_provider_without_model_keeps_the_agent_model() -> None:
    """Narrowed fix: same provider as the agent → the agent's model_override
    (the old, correct behaviour), not the provider's default model."""
    plan = mig._plan_conversion(
        [_provider(1, "A", "a-default")],
        [
            _policy(
                10,
                llm_provider_id=1,
                model_override="a-big",
                triage_llm_provider_id=1,
            )
        ],
    )
    assert _resolve(plan, 10, "triage") == ((1, "a-big"),)
    # Identical to the agent chain → shares its profile.
    assert _resolve(plan, 10, "agent") == ((1, "a-big"),)
    assert len(plan.profiles) == 1


def test_triage_on_the_agent_provider_without_any_override_uses_default() -> None:
    plan = mig._plan_conversion(
        [_provider(1, "A", "a-default")],
        [_policy(10, llm_provider_id=1, triage_llm_provider_id=1)],
    )
    assert _resolve(plan, 10, "triage") == ((1, "a-default"),)
