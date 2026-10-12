"""SCIM conformance: replay of recorded Entra ID and Okta request sequences (wave 4, Task 13).

The fixtures under ``tests/fixtures/scim`` hold made-up data in the request shapes the two IdPs
send. Each step has ``method``, ``path`` (``{user_id}``/``{group_id}``/... filled from earlier
``capture`` blocks), optional ``query``/``body``, the expected ``status`` and an ``expect`` JSON
that must be a subset of the answer. A step with ``action: role_sync_tick`` runs the role-sync
worker and checks the rule-mapped role of the named agents.

Seed band 63320-63349: role 63321 (rule target), provider slug ``sc63conf-*``, logins
``sc63conf.*@example.test``.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import FakeRedis
from tests._scim_helpers import KEY, scim_cleanup_statements, seed_scim_provider
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db, get_session_factory_dep
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import KEY_SCIM_ROLE_SYNC_ENABLED
from tiqora.identity.config_models import RoleRule
from tiqora.identity.scim import role_sync
from tiqora.identity.scim.role_sync import run_scim_role_sync_tick
from tiqora.identity.scim.tokens import create_token
from tiqora.identity.types import utc_now

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).parent / "fixtures" / "scim"
BASE = "/scim/v2"
ROLE = 63321
SEQUENCES = [
    "entra_user_lifecycle",
    "entra_group_lifecycle",
    "okta_user_lifecycle",
    "okta_group_push",
]
_URLS: set[str] = set()
_MARK: dict[str, int] = {}
_EVENT_MARK: dict[str, int] = {}


def _stmts(url: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        ("DELETE FROM tiqora_scim_event WHERE id > :mx", {"mx": _EVENT_MARK.get(url, 2**62)}),
        (
            "DELETE FROM tiqora_cache_invalidation WHERE id > :mx",
            {"mx": _MARK.get(url, 2**62)},
        ),
        *scim_cleanup_statements(slug_prefix="sc63conf", login_like="sc63conf.%", role_ids=(ROLE,)),
    )


def _run(url: str, stmts: tuple[tuple[str, dict[str, Any]], ...]) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in stmts:
                conn.execute(text(stmt), params)
            if stmts:
                conn.execute(
                    delete(TiqoraSettings).where(TiqoraSettings.key == KEY_SCIM_ROLE_SYNC_ENABLED)
                )
    finally:
        engine.dispose()


def _prepare(url: str) -> None:
    _run(url, ())
    if url not in _MARK:
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                row = conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
                )
                _MARK[url] = int(row.scalar_one())
                row = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM tiqora_scim_event"))
                _EVENT_MARK[url] = int(row.scalar_one())
        finally:
            engine.dispose()
    _run(url, _stmts(url))
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO roles (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:i, :n, 1, :t, 1, :t, 1)"
                ),
                {"i": ROLE, "n": f"sc63conf-role-{ROLE}", "t": utc_now()},
            )
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _run(url, _stmts(url))
    _URLS.clear()


class Env:
    def __init__(self, maker: async_sessionmaker[AsyncSession], client: httpx.AsyncClient) -> None:
        self.maker = maker
        self.client = client
        self.token = ""

    async def role_ids(self, login: str) -> set[int]:
        async with self.maker() as s:
            rows = await s.execute(
                text(
                    "SELECT ru.role_id FROM role_user ru JOIN users u ON u.id = ru.user_id"
                    " WHERE u.login = :l"
                ),
                {"l": login},
            )
            return {int(r[0]) for r in rows.all()}


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _prepare(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        environment="test",
        secret_key=KEY,
        public_base_url="https://helpdesk.example.test",
    )
    monkeypatch.setattr(role_sync, "get_session_factory", lambda: maker)
    monkeypatch.setattr(role_sync, "get_settings", lambda: settings)

    app = create_app(settings)
    app.state.redis = FakeRedis()

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_session_factory_dep] = lambda: maker
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://helpdesk.example.test"
    )
    async with maker() as db:
        rec = await seed_scim_provider(
            db,
            audience="agent",
            slug="sc63conf-a",
            role_rules=(RoleRule(attribute="scim:groups", value="Support", role_ids=[ROLE]),),
        )
        _, raw = await create_token(
            db, provider=rec, user_kind="agent", name="conf", expires_in_days=30, actor_id=1
        )
        await db.commit()
    e = Env(maker, client)
    e.token = raw
    async with client:
        yield e
    await engine.dispose()
    _run(url, _stmts(url))


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _fill(value: Any, captured: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: captured[m.group(1)], value)
    if isinstance(value, list):
        return [_fill(v, captured) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, captured) for k, v in value.items()}
    return value


def _subset(expected: Any, actual: Any, where: str = "$") -> None:
    """``expected`` must be contained in ``actual`` (dicts by key, lists element by element)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: expected object, got {actual!r}"
        for key, value in expected.items():
            assert key in actual, f"{where}: missing key {key!r} in {actual!r}"
            _subset(value, actual[key], f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{where}: expected array, got {actual!r}"
        assert len(expected) == len(actual), f"{where}: length {len(actual)} != {len(expected)}"
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            _subset(e, a, f"{where}[{i}]")
    else:
        assert expected == actual, f"{where}: {actual!r} != {expected!r}"


def _members_sorted(doc: Any) -> Any:
    """Member order is not part of the protocol: compare it sorted."""
    if isinstance(doc, dict):
        return {
            k: sorted(v, key=lambda m: m.get("value", "")) if k == "members" else _members_sorted(v)
            for k, v in doc.items()
        }
    return doc


def test_subset_helper_detects_differences() -> None:
    _subset({"a": {"b": 1}, "c": [1]}, {"a": {"b": 1, "x": 2}, "c": [1], "y": 0})
    with pytest.raises(AssertionError):
        _subset({"a": 1}, {"a": 2})
    with pytest.raises(AssertionError):
        _subset({"a": 1}, {})
    with pytest.raises(AssertionError):
        _subset({"c": [1]}, {"c": [1, 2]})


@pytest.mark.parametrize("sequence", SEQUENCES)
async def test_sequence(env: Env, sequence: str) -> None:
    doc = json.loads((FIXTURES / f"{sequence}.json").read_text(encoding="utf-8"))
    assert doc["steps"], sequence
    headers = {
        "Authorization": f"Bearer {env.token}",
        "Content-Type": "application/scim+json",
    }
    captured: dict[str, str] = {}
    for step in doc["steps"]:
        label = f"{sequence}: {step['name']}"
        if step.get("action") == "role_sync_tick":
            counts = await run_scim_role_sync_tick()
            assert "skipped" not in counts, label
            assert counts["errors"] == 0, f"{label}: {counts}"
            for login in step["users"]:
                roles = await env.role_ids(login)
                if step["role"] == "present":
                    assert ROLE in roles, f"{label}: {login} has {roles}"
                else:
                    assert ROLE not in roles, f"{label}: {login} has {roles}"
            continue
        body = _fill(step.get("body"), captured)
        resp = await env.client.request(
            step["method"],
            BASE + _fill(step["path"], captured),
            params=step.get("query"),
            json=body,
            headers=headers,
        )
        assert resp.status_code == step["status"], f"{label}: {resp.status_code} {resp.text}"
        if step["status"] != 204:
            assert resp.headers["content-type"].startswith("application/scim+json"), label
        answer = resp.json() if resp.content else {}
        _subset(
            _members_sorted(_fill(step.get("expect", {}), captured)),
            _members_sorted(answer),
            label,
        )
        for name, field in step.get("capture", {}).items():
            captured[name] = answer[field]
