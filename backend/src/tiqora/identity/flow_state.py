"""Short-lived Redis state of SSO flows (D10): OIDC state and admin test runs.

One-shot keys are read with GET + DELETE ("pop"). The SAML request state is read
non-destructively first (``get_saml_request``) and only deleted once the response
signature has been verified, so a forged response naming a request id cannot burn
the genuine user's login. SAML logout requests (SP-initiated) are stored by request id and
bound to the provider: the redirect-binding LogoutResponse carries a signature pysaml2 cannot
check for us, so a response only counts when it answers a request we issued.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from tiqora.crypto.secret import decrypt_secret, encrypt_secret
from tiqora.identity.store import ProviderChanges
from tiqora.identity.types import UserKind

OIDC_STATE_PREFIX = "tiqora:oidc:state:"
OIDC_STATE_TTL = 300
SAML_REQUEST_PREFIX = "tiqora:saml:req:"
SAML_REQUEST_TTL = 600
SAML_SEEN_PREFIX = "tiqora:saml:seen:"
LOGOUT_REQUEST_PREFIX = "tiqora:saml:logout:"
LOGOUT_REQUEST_TTL = 600
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


@dataclass(frozen=True, slots=True)
class LogoutState:
    provider_id: int
    user_kind: UserKind


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


async def put_saml_request(redis: Any, request_id: str, st: LoginState) -> None:
    payload = {"p": st.provider_id, "n": st.next_path, "a": st.user_kind, "t": st.test_id}
    await redis.set(f"{SAML_REQUEST_PREFIX}{request_id}", json.dumps(payload), ex=SAML_REQUEST_TTL)


def _parse_saml_state(raw: str) -> LoginState | None:
    try:
        data = json.loads(raw)
        provider_id, user_kind, next_path = data["p"], data["a"], data.get("n") or "/"
    except (ValueError, KeyError, TypeError):
        return None
    test_id = data.get("t")
    if (
        not isinstance(provider_id, int)
        or isinstance(provider_id, bool)
        or user_kind not in ("agent", "customer")
        or not isinstance(next_path, str)
    ):
        return None
    return LoginState(
        provider_id=provider_id,
        next_path=next_path,
        user_kind=user_kind,
        test_id=test_id if isinstance(test_id, str) else None,
    )


async def get_saml_request(redis: Any, request_id: str) -> LoginState | None:
    """Non-destructive read; pair with :func:`pop_saml_request` after verification."""
    raw = await redis.get(f"{SAML_REQUEST_PREFIX}{request_id}")
    return None if raw is None else _parse_saml_state(_text(raw))


async def pop_saml_request(redis: Any, request_id: str) -> LoginState | None:
    """Atomic consume: only the caller whose DELETE removes the key gets the state."""
    key = f"{SAML_REQUEST_PREFIX}{request_id}"
    raw = await redis.get(key)
    if raw is None or await redis.delete(key) != 1:
        return None
    return _parse_saml_state(_text(raw))


async def mark_assertion_seen(redis: Any, provider_id: int, assertion_id: str, ttl: int) -> bool:
    """Record an assertion id; False when it was already seen (replay)."""
    digest = hashlib.sha256(assertion_id.encode()).hexdigest()
    key = f"{SAML_SEEN_PREFIX}{provider_id}:{digest}"
    return bool(await redis.set(key, "1", ex=max(ttl, 60), nx=True))


async def put_logout_request(redis: Any, request_id: str, st: LogoutState) -> None:
    payload = {"p": st.provider_id, "a": st.user_kind}
    await redis.set(
        f"{LOGOUT_REQUEST_PREFIX}{request_id}", json.dumps(payload), ex=LOGOUT_REQUEST_TTL
    )


async def pop_logout_request(
    redis: Any, request_id: str, *, provider_id: int
) -> LogoutState | None:
    """The stored logout request *request_id* of *provider_id*, consumed (one shot).

    Another provider's request id is neither returned nor deleted; garbage is dropped.
    """
    key = f"{LOGOUT_REQUEST_PREFIX}{request_id}"
    raw = await redis.get(key)
    if raw is None:
        return None
    try:
        data = json.loads(_text(raw))
        stored_provider, user_kind = data["p"], data["a"]
    except (ValueError, KeyError, TypeError):
        await redis.delete(key)
        return None
    if (
        not isinstance(stored_provider, int)
        or isinstance(stored_provider, bool)
        or user_kind not in ("agent", "customer")
    ):
        await redis.delete(key)
        return None
    if stored_provider != provider_id:
        return None
    await redis.delete(key)
    return LogoutState(provider_id=stored_provider, user_kind=user_kind)


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


async def put_test(redis: Any, test_id: str, data: TestRun, *, secret_key: str) -> None:
    """Store a test run. The draft may carry secrets (client secret): it is Fernet-encrypted."""
    payload = data.model_dump(mode="json", exclude={"draft"})
    if data.draft is not None:
        payload["draft_enc"] = encrypt_secret(secret_key, data.draft.model_dump_json())
    await redis.set(f"{TEST_PREFIX}{test_id}", json.dumps(payload), ex=TEST_TTL)


async def get_test(redis: Any, test_id: str, *, secret_key: str) -> TestRun | None:
    raw = await redis.get(f"{TEST_PREFIX}{test_id}")
    if raw is None:
        return None
    try:
        payload = json.loads(_text(raw))
        draft_enc = payload.pop("draft_enc", None)
        if draft_enc is not None:
            plain = decrypt_secret(secret_key, draft_enc) if isinstance(draft_enc, str) else None
            if plain is None:
                return None
            payload["draft"] = json.loads(plain)
        return TestRun.model_validate(payload)
    except (ValueError, ValidationError, AttributeError):
        return None
