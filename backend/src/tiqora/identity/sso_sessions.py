"""SSO session info stored beside a session, plus the Redis index for single logout.

A session created by an SSO login remembers which provider / subject / IdP
session produced it. Index sets (token members) let a logout request or an OIDC
back-channel logout find every live session without scanning Redis.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal, get_args

from tiqora.identity.types import ProviderKind

if TYPE_CHECKING:
    from tiqora.config import Settings

AudienceKey = Literal["a", "c"]  # agent / customer key space

_KINDS: tuple[str, ...] = get_args(ProviderKind)
_OPTIONAL_FIELDS = (
    "session_index",
    "name_id",
    "name_id_format",
    "name_qualifier",
    "sp_name_qualifier",
    "sid",
    "id_token_hint",
)


@dataclass(frozen=True, slots=True)
class SsoSessionInfo:
    provider_id: int
    slug: str
    kind: ProviderKind
    subject_hash: str
    session_index: str | None = None
    name_id: str | None = None
    name_id_format: str | None = None
    name_qualifier: str | None = None
    sp_name_qualifier: str | None = None
    sid: str | None = None
    id_token_hint: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> SsoSessionInfo | None:
        """Parse a stored value; ``None`` on garbage."""
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            provider_id = data["provider_id"]
            slug = data["slug"]
            kind = data["kind"]
            subject_hash = data["subject_hash"]
            if (
                not isinstance(provider_id, int)
                or isinstance(provider_id, bool)
                or not isinstance(slug, str)
                or kind not in _KINDS
                or not isinstance(subject_hash, str)
            ):
                return None
            optional: dict[str, Any] = {}
            for name in _OPTIONAL_FIELDS:
                value = data.get(name)
                if value is not None and not isinstance(value, str):
                    return None
                optional[name] = value
            return cls(
                provider_id=provider_id,
                slug=slug,
                kind=kind,
                subject_hash=subject_hash,
                **optional,
            )
        except (ValueError, TypeError, KeyError):
            return None


def sso_key(audience: AudienceKey, token: str) -> str:
    if audience == "a":
        return f"tiqora:session:sso:{token}"
    return f"tiqora:csession:sso:{token}"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _index_keys(audience: AudienceKey, info: SsoSessionInfo) -> list[str]:
    base = f"tiqora:sso:idx:{audience}:{info.provider_id}"
    keys = [f"{base}:sub:{info.subject_hash}"]
    if info.sid:
        keys.append(f"{base}:sid:{_sha(info.sid)}")
    if info.session_index:
        keys.append(f"{base}:si:{_sha(info.session_index)}")
    return keys


def _text(raw: Any) -> str:
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


async def store_sso(
    redis: Any, audience: AudienceKey, token: str, info: SsoSessionInfo, ttl: int
) -> None:
    await redis.set(sso_key(audience, token), info.to_json(), ex=ttl)
    with contextlib.suppress(Exception):
        for key in _index_keys(audience, info):
            await redis.sadd(key, token)
            await redis.expire(key, ttl)


async def load_sso(redis: Any, audience: AudienceKey, token: str) -> SsoSessionInfo | None:
    raw = await redis.get(sso_key(audience, token))
    if raw is None:
        return None
    return SsoSessionInfo.from_json(_text(raw))


async def drop_sso(redis: Any, audience: AudienceKey, token: str) -> None:
    info = None
    with contextlib.suppress(Exception):
        info = await load_sso(redis, audience, token)
    if info is not None:
        with contextlib.suppress(Exception):
            for key in _index_keys(audience, info):
                await redis.srem(key, token)
    await redis.delete(sso_key(audience, token))


async def _members(redis: Any, audience: AudienceKey, key: str) -> set[str]:
    """Live tokens of index set *key*; members whose SSO info expired are pruned from it.

    Index sets live as long as their newest member, so an active subject would otherwise
    accumulate the tokens of every expired session.
    """
    try:
        members = {_text(m) for m in (await redis.smembers(key) or set())}
    except Exception:  # noqa: BLE001 — a missing index must not break logout
        return set()
    live: set[str] = set()
    for token in members:
        try:
            alive = bool(await redis.exists(sso_key(audience, token)))
        except Exception:  # noqa: BLE001 - when in doubt, keep it (revoking twice is harmless)
            alive = True
        if alive:
            live.add(token)
            continue
        with contextlib.suppress(Exception):
            await redis.srem(key, token)
    return live


async def tokens_for(
    redis: Any,
    audience: AudienceKey,
    provider_id: int,
    *,
    subject_hash: str | None = None,
    sid: str | None = None,
    session_indexes: Sequence[str] = (),
) -> set[str]:
    """Live tokens matching the given selectors.

    Subject AND session index when both are given; ``sid`` is unioned with the rest, so
    callers that want a pure sid match (OIDC back-channel) pass ``sid`` alone.
    """
    base = f"tiqora:sso:idx:{audience}:{provider_id}"
    by_subject: set[str] | None = None
    if subject_hash is not None:
        by_subject = await _members(redis, audience, f"{base}:sub:{subject_hash}")
    by_index: set[str] | None = None
    if session_indexes:
        by_index = set()
        for si in session_indexes:
            by_index |= await _members(redis, audience, f"{base}:si:{_sha(si)}")
    found: set[str] = set()
    if by_subject is not None and by_index is not None:
        found |= by_subject & by_index
    elif by_subject is not None:
        found |= by_subject
    elif by_index is not None:
        found |= by_index
    if sid is not None:
        found |= await _members(redis, audience, f"{base}:sid:{_sha(sid)}")
    return found


async def revoke_sso_sessions(
    redis: Any,
    settings: Settings,
    *,
    provider_id: int,
    subject_hash: str | None = None,
    sid: str | None = None,
    session_indexes: Sequence[str] = (),
) -> int:
    """Delete matching agent and customer sessions; return how many were removed."""
    # Lazy: the session stores import SsoSessionInfo from this module.
    from tiqora.domain.auth import SessionStore
    from tiqora.domain.customer_auth import CustomerSessionStore

    removed = 0
    agent_tokens = await tokens_for(
        redis,
        "a",
        provider_id,
        subject_hash=subject_hash,
        sid=sid,
        session_indexes=session_indexes,
    )
    agents = SessionStore(redis, settings)
    for token in agent_tokens:
        await agents.delete(token)
        removed += 1
    customer_tokens = await tokens_for(
        redis,
        "c",
        provider_id,
        subject_hash=subject_hash,
        sid=sid,
        session_indexes=session_indexes,
    )
    customers = CustomerSessionStore(redis, settings)
    for token in customer_tokens:
        await customers.delete(token)
        removed += 1
    return removed
