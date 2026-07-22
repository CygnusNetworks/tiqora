"""LLM provider CRUD + connection/tool-calling test (plan §3.2).

Follows the Fernet-at-rest pattern from ``tiqora.domain.mail_outbound``: the
admin API never returns the decrypted API key, only ``has_api_key``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import TiqoraLlmProvider
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret, encrypt_secret

_TEST_TIMEOUT_SECONDS = 20.0

# Minimal tool schema used to probe tool-calling support on /chat/completions.
_TEST_TOOL_SCHEMA = [
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


@dataclass(frozen=True, slots=True)
class ProviderTestResult:
    ok: bool
    model: str | None
    tool_calling_ok: bool
    error: str | None


async def list_providers(session: AsyncSession) -> list[TiqoraLlmProvider]:
    rows = (
        (await session.execute(select(TiqoraLlmProvider).order_by(TiqoraLlmProvider.name)))
        .scalars()
        .all()
    )
    return list(rows)


async def get_provider(session: AsyncSession, provider_id: int) -> TiqoraLlmProvider | None:
    return await session.get(TiqoraLlmProvider, provider_id)


async def create_provider(
    session: AsyncSession,
    *,
    settings: Settings,
    change_by: int,
    name: str,
    kind: str,
    base_url: str,
    default_model: str,
    api_key: str | None,
    extra_json: str | None,
    supports_tools: bool,
    supports_streaming: bool,
    eu_hosted: bool,
) -> TiqoraLlmProvider:
    # Keys/URLs arrive via copy-paste; stray whitespace or a trailing newline
    # silently breaks the Bearer header at the provider (opaque 401s).
    api_key = api_key.strip() if api_key else None
    row = TiqoraLlmProvider(
        name=name,
        kind=kind,
        base_url=base_url.strip(),
        default_model=default_model.strip(),
        api_key_enc=encrypt_secret(settings.secret_key, api_key) if api_key else None,
        extra_json=extra_json,
        supports_tools=supports_tools,
        supports_streaming=supports_streaming,
        eu_hosted=eu_hosted,
        create_by=change_by,
        change_by=change_by,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_provider(
    session: AsyncSession,
    row: TiqoraLlmProvider,
    *,
    settings: Settings,
    change_by: int,
    name: str | None = None,
    kind: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
    api_key: str | None = None,
    extra_json: str | None = None,
    supports_tools: bool | None = None,
    supports_streaming: bool | None = None,
    eu_hosted: bool | None = None,
    valid_id: int | None = None,
) -> TiqoraLlmProvider:
    if name is not None:
        row.name = name
    if kind is not None:
        row.kind = kind
    if base_url is not None:
        row.base_url = base_url.strip()
    if default_model is not None:
        row.default_model = default_model.strip()
    if api_key is not None and api_key.strip() != "":
        row.api_key_enc = encrypt_secret(settings.secret_key, api_key.strip())
    if extra_json is not None:
        row.extra_json = extra_json
    if supports_tools is not None:
        row.supports_tools = supports_tools
    if supports_streaming is not None:
        row.supports_streaming = supports_streaming
    if eu_hosted is not None:
        row.eu_hosted = eu_hosted
    if valid_id is not None:
        row.valid_id = valid_id
    row.change_by = change_by
    row.change_time = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_provider(session: AsyncSession, row: TiqoraLlmProvider) -> None:
    await session.delete(row)
    await session.commit()


def provider_to_public_dict(row: TiqoraLlmProvider) -> dict[str, object]:
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "base_url": row.base_url,
        "default_model": row.default_model,
        "has_api_key": bool(row.api_key_enc),
        "extra_json": row.extra_json,
        "supports_tools": bool(row.supports_tools),
        "supports_streaming": bool(row.supports_streaming),
        "eu_hosted": bool(row.eu_hosted),
        "valid_id": int(row.valid_id),
        "create_time": row.create_time,
        "change_time": row.change_time,
    }


async def test_provider_connection(
    row: TiqoraLlmProvider, *, settings: Settings, client: httpx.AsyncClient | None = None
) -> ProviderTestResult:
    """Call ``POST {base_url}/chat/completions`` with a mini prompt + a mini
    tool schema to verify both connectivity/auth and tool-calling support.

    ``client`` is injectable (``httpx.AsyncClient(transport=httpx.MockTransport(...))``)
    so tests never hit the network; production callers omit it and a
    short-lived client is created for the single request.
    """
    api_key = decrypt_secret(settings.secret_key, row.api_key_enc) if row.api_key_enc else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict[str, object] = {
        "model": row.default_model,
        "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
        "max_tokens": 16,
    }
    if row.supports_tools:
        payload["tools"] = _TEST_TOOL_SCHEMA

    url = row.base_url.rstrip("/") + "/chat/completions"
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=_TEST_TIMEOUT_SECONDS)
    try:
        response = await http_client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            return ProviderTestResult(
                ok=False,
                model=None,
                tool_calling_ok=False,
                error=f"HTTP {response.status_code}: {response.text[:500]}",
            )
        data = response.json()
        model = data.get("model")
        choices = data.get("choices") or []
        tool_calling_ok = False
        if choices:
            message = choices[0].get("message") or {}
            tool_calling_ok = bool(message.get("tool_calls"))
        return ProviderTestResult(ok=True, model=model, tool_calling_ok=tool_calling_ok, error=None)
    except httpx.HTTPError as exc:
        return ProviderTestResult(ok=False, model=None, tool_calling_ok=False, error=str(exc))
    finally:
        if owns_client:
            await http_client.aclose()


__all__ = [
    "ProviderTestResult",
    "create_provider",
    "delete_provider",
    "get_provider",
    "list_providers",
    "provider_to_public_dict",
    "test_provider_connection",
    "update_provider",
]
