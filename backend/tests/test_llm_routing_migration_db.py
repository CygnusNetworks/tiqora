"""Migration 20260930_0053 (LLM routing) on real databases: upgrade converts
existing queue policies so every queue resolves the models it used before;
downgrade restores the old columns from the effective resolution.

Runs the Alembic chain up to the previous head, seeds provider/policy rows
the old way, upgrades, resolves through ``tiqora.ai.llm_routing``, then
downgrades and upgrades again. Sync test on purpose: Alembic's env.py runs
its own event loop. Leaves the schema at head with no rows (the
tables are shared with every other module).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._schema_reset import reset_tiqora_schema
from tiqora.ai.llm_routing import (
    TASK_AGENT,
    TASK_FINAL_ANSWER,
    TASK_REFINE,
    TASK_SUMMARY,
    TASK_TRIAGE,
    TASK_VISION,
    NoUsableModel,
    build_task_llm,
)
from tiqora.ai.models import TiqoraAiQueuePolicy
from tiqora.cli.migrate import build_alembic_config
from tiqora.config import get_settings

pytestmark = pytest.mark.db

_PREVIOUS = "20260930_0052"
_THIS = "20260930_0053"


def _sync_url(url: str) -> str:
    return (
        url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("mysql+aiomysql://", "mysql+pymysql://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )


def _async_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("mysql+pymysql://", "mysql+aiomysql://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


@pytest.fixture
def alembic_env(request: pytest.FixtureRequest) -> Iterator[str]:
    url = request.getfixturevalue(request.param)
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _async_url(url)
    get_settings.cache_clear()
    try:
        yield _sync_url(url)
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old
        get_settings.cache_clear()


def _migrate(target: str, *, down: bool = False) -> None:
    cfg = build_alembic_config(include_owned=False)
    if down:
        command.downgrade(cfg, target)
    else:
        command.upgrade(cfg, target)


def _seed_old_schema(sync_url: str) -> dict[str, int]:
    engine = create_engine(sync_url)
    ids: dict[str, int] = {}
    with engine.begin() as conn:
        for key, name, model, vision, price in (
            ("p1", "Main", "main-model", False, 2.0),
            ("p2", "Eyes", "eye-model", True, None),
        ):
            conn.execute(
                text(
                    "INSERT INTO tiqora_llm_provider (name, kind, base_url, default_model,"
                    " supports_tools, supports_streaming, eu_hosted, supports_vision,"
                    " max_tool_rounds, price_input_per_1m, valid_id, create_by, change_by)"
                    " VALUES (:n, 'openai_compat', 'https://x/v1', :m, :t, :t, :f, :v,"
                    " :r, :p, 1, 1, 1)"
                ),
                {
                    "n": name,
                    "m": model,
                    "t": True,
                    "f": False,
                    "v": vision,
                    "r": 6 if key == "p1" else None,
                    "p": price,
                },
            )
            ids[key] = int(
                conn.execute(
                    text("SELECT id FROM tiqora_llm_provider WHERE name = :n"), {"n": name}
                ).scalar_one()
            )
        fallback = json.dumps(
            [{"provider_id": 99999, "model": "gone"}, {"provider_id": ids["p2"], "model": None}]
        )
        for key, queue_id, values in (
            (
                "q1",
                9901,
                {
                    "llm_provider_id": ids["p1"],
                    "llm_fallback_json": fallback,
                    "triage_llm_provider_id": ids["p2"],
                    "model_override": None,
                    "vision_provider_id": None,
                },
            ),
            (
                "q2",
                9902,
                {
                    "llm_provider_id": ids["p1"],
                    "llm_fallback_json": fallback,
                    "triage_llm_provider_id": None,
                    "model_override": None,
                    "vision_provider_id": ids["p2"],
                },
            ),
            (
                "q3",
                9903,
                {
                    "llm_provider_id": None,
                    "llm_fallback_json": None,
                    "triage_llm_provider_id": None,
                    "model_override": None,
                    "vision_provider_id": None,
                },
            ),
        ):
            conn.execute(
                text(
                    "INSERT INTO tiqora_ai_queue_policy (queue_id, system_prompt, autonomy,"
                    " identity_mode, llm_provider_id, model_override, llm_fallback_json,"
                    " triage_llm_provider_id, vision_provider_id, create_by, change_by)"
                    " VALUES (:q, '', 'off', 'ticket_customer_id', :llm_provider_id,"
                    " :model_override, :llm_fallback_json, :triage_llm_provider_id,"
                    " :vision_provider_id, 1, 1)"
                ),
                {"q": queue_id, **values},
            )
            ids[key] = int(
                conn.execute(
                    text("SELECT id FROM tiqora_ai_queue_policy WHERE queue_id = :q"),
                    {"q": queue_id},
                ).scalar_one()
            )
    engine.dispose()
    return ids


async def _models(session: Any, policy: TiqoraAiQueuePolicy | None, task: str) -> list[Any]:
    try:
        task_llm = await build_task_llm(session, get_settings(), policy, task)
    except NoUsableModel:
        return []
    return [] if task_llm is None else task_llm.models


async def _resolved(
    sync_url: str, ids: dict[str, int]
) -> dict[str, dict[str, list[tuple[int, str]]]]:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    out: dict[str, dict[str, list[tuple[int, str]]]] = {}
    try:
        async with factory() as session:
            for key in ("q1", "q2", "q3"):
                policy = await session.get(TiqoraAiQueuePolicy, ids[key])
                out[key] = {
                    task: [(m.provider_id, m.model) for m in await _models(session, policy, task)]
                    for task in (
                        TASK_AGENT,
                        TASK_FINAL_ANSWER,
                        TASK_TRIAGE,
                        TASK_SUMMARY,
                        TASK_REFINE,
                        TASK_VISION,
                    )
                }
    finally:
        await engine.dispose()
    return out


def _wipe_rows(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for table in (
            "tiqora_ai_queue_task_profile",
            "tiqora_ai_task_default",
            "tiqora_llm_profile_entry",
            "tiqora_llm_profile",
            "tiqora_llm_model",
            "tiqora_ai_queue_policy",
            "tiqora_llm_provider",
            "tiqora_alembic_version",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
    engine.dispose()


@pytest.mark.parametrize("alembic_env", ["mariadb_znuny_url", "postgres_znuny_url"], indirect=True)
def test_upgrade_resolves_like_before_and_downgrade_restores(alembic_env: str) -> None:
    sync_url = alembic_env
    reset_tiqora_schema(sync_url)
    try:
        _migrate(_PREVIOUS)
        ids = _seed_old_schema(sync_url)
        _migrate(_THIS)

        p1, p2 = ids["p1"], ids["p2"]
        agent = [(p1, "main-model"), (p2, "eye-model")]
        resolved = asyncio.run(_resolved(sync_url, ids))
        # q1: agent chain (deleted fallback provider skipped); triage on the
        # triage provider's own default model + the agent fallbacks.
        assert resolved["q1"] == {
            "agent": agent,
            "final_answer": [],
            "triage": [(p2, "eye-model")],
            "summary": agent,
            "refine": agent,
            "vision": [],
        }
        assert resolved["q2"]["agent"] == agent
        assert resolved["q2"]["triage"] == agent
        assert resolved["q2"]["vision"] == [(p2, "eye-model")]
        assert all(chain == [] for chain in resolved["q3"].values())

        engine = create_engine(sync_url)
        with engine.connect() as conn:
            # q1 and q2 share the agent chain → one profile, the global default.
            defaults = dict(
                conn.execute(text("SELECT task, profile_id FROM tiqora_ai_task_default")).all()
            )
            names = dict(conn.execute(text("SELECT id, name FROM tiqora_llm_profile")).all())
            model = conn.execute(
                text(
                    "SELECT max_tool_rounds, price_input_per_1m FROM tiqora_llm_model"
                    " WHERE provider_id = :p AND model_id = 'main-model'"
                ),
                {"p": p1},
            ).one()
            provider_columns = {c["name"] for c in inspect(conn).get_columns("tiqora_llm_provider")}
            policy_columns = {
                c["name"] for c in inspect(conn).get_columns("tiqora_ai_queue_policy")
            }
            usage_columns = {c["name"] for c in inspect(conn).get_columns("tiqora_ai_usage")}
            audit_columns = {c["name"] for c in inspect(conn).get_columns("tiqora_ai_audit_log")}
        engine.dispose()
        assert names[defaults["agent"]] == "main-model @ Main +1"
        assert tuple(model) == (6, 2.0)
        assert "default_model" not in provider_columns
        assert "llm_provider_id" not in policy_columns
        assert "llm_fallback_json" not in policy_columns
        assert "llm_model_id" in usage_columns and "llm_model_id" in audit_columns

        _migrate(_PREVIOUS, down=True)
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            rows = {
                r.queue_id: r
                for r in conn.execute(
                    text(
                        "SELECT queue_id, llm_provider_id, model_override, llm_fallback_json,"
                        " triage_llm_provider_id, triage_model_override, vision_provider_id"
                        " FROM tiqora_ai_queue_policy"
                    )
                )
            }
            providers = dict(
                conn.execute(text("SELECT id, default_model FROM tiqora_llm_provider")).all()
            )
            tables = set(inspect(conn).get_table_names())
            usage_after = {c["name"] for c in inspect(conn).get_columns("tiqora_ai_usage")}
        engine.dispose()
        assert providers == {p1: "main-model", p2: "eye-model"}
        assert "tiqora_llm_model" not in tables
        assert "llm_model_id" not in usage_after
        q1 = rows[9901]
        assert (q1.llm_provider_id, q1.model_override) == (p1, "main-model")
        assert json.loads(q1.llm_fallback_json) == [{"provider_id": p2, "model": "eye-model"}]
        assert (q1.triage_llm_provider_id, q1.triage_model_override) == (p2, "eye-model")
        assert rows[9902].vision_provider_id == p2
        assert rows[9903].llm_provider_id is None

        # And forward again from the restored columns.
        _migrate("head")
        assert asyncio.run(_resolved(sync_url, ids))["q1"]["agent"] == agent
    finally:
        _migrate("head")
        _wipe_rows(sync_url)
