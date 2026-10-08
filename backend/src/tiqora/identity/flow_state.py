"""Short-lived Redis state of SSO flows (D10): OIDC state and admin test runs.

All one-shot keys are read with GET + DELETE ("pop"). SAML request / assertion
replay / logout keys arrive with the SAML tasks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from tiqora.identity.store import ProviderChanges
from tiqora.identity.types import UserKind

OIDC_STATE_PREFIX = "tiqora:oidc:state:"
OIDC_STATE_TTL = 300
TEST_PREFIX = "tiqora:idp:test:"
TEST_TTL = 900


@dataclass(frozen=True, slots=True)
class LoginState:
    provider_id: int
    next_path: str
    user_kind: UserKind
    test_id: str | None = None
    code_verifier: str | None = None
    nonce: str | None = None
    legacy: bool = False  # pre-upgrade env-oidc value


def _text(raw: Any) -> str:
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


async def put_oidc_state(redis: Any, state: str, st: LoginState) -> None:
    payload = {
        "v": 1,
        "p": st.provider_id,
        "k": st.code_verifier,
        "n": st.next_path,
        "a": st.user_kind,
        "t": st.test_id,
        "nonce": st.nonce,
    }
    await redis.set(f"{OIDC_STATE_PREFIX}{state}", json.dumps(payload), ex=OIDC_STATE_TTL)


def _parse_state(raw: str, *, env_oidc_provider_id: int | None) -> LoginState | None:
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        # Started before the upgrade: "1" (no PKCE) or the bare code verifier.
        if env_oidc_provider_id is None:
            return None
        return LoginState(
            provider_id=env_oidc_provider_id,
            next_path="/",
            user_kind="agent",
            code_verifier=None if raw == "1" else raw,
            legacy=True,
        )
    try:
        provider_id = data["p"]
        user_kind = data["a"]
        next_path = data.get("n") or "/"
        if (
            not isinstance(provider_id, int)
            or isinstance(provider_id, bool)
            or user_kind not in ("agent", "customer")
            or not isinstance(next_path, str)
        ):
            return None
        verifier, test_id, nonce = data.get("k"), data.get("t"), data.get("nonce")
        return LoginState(
            provider_id=provider_id,
            next_path=next_path,
            user_kind=user_kind,
            test_id=test_id if isinstance(test_id, str) else None,
            code_verifier=verifier if isinstance(verifier, str) else None,
            nonce=nonce if isinstance(nonce, str) else None,
        )
    except KeyError:
        return None


async def pop_oidc_state(
    redis: Any, state: str, *, env_oidc_provider_id: int | None
) -> LoginState | None:
    key = f"{OIDC_STATE_PREFIX}{state}"
    raw = await redis.get(key)
    if raw is None:
        return None
    await redis.delete(key)
    return _parse_state(_text(raw), env_oidc_provider_id=env_oidc_provider_id)


class TestResultData(BaseModel):
    __test__ = False  # not a pytest class
    subject: str
    attributes: dict[str, list[str]]
    plan: dict[str, Any]


class TestRun(BaseModel):
    __test__ = False
    provider_id: int
    admin_id: int
    audience: UserKind
    draft: ProviderChanges | None = None
    status: Literal["started", "done", "failed"] = "started"
    error: str | None = None
    result: TestResultData | None = None


async def put_test(redis: Any, test_id: str, data: TestRun) -> None:
    await redis.set(f"{TEST_PREFIX}{test_id}", data.model_dump_json(), ex=TEST_TTL)


async def get_test(redis: Any, test_id: str) -> TestRun | None:
    raw = await redis.get(f"{TEST_PREFIX}{test_id}")
    if raw is None:
        return None
    try:
        return TestRun.model_validate_json(_text(raw))
    except ValidationError:
        return None
