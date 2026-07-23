"""LLM provider CRUD + connection/tool-calling test (plan §3.2).

Follows the Fernet-at-rest pattern from ``tiqora.domain.mail_outbound``: the
admin API never returns the decrypted API key, only ``has_api_key``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.audit import FEATURE_TEST as AUDIT_FEATURE_TEST
from tiqora.ai.audit import AuditContext, write_audit_log
from tiqora.ai.models import TiqoraLlmProvider
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret, encrypt_secret

_TEST_TIMEOUT_SECONDS = 20.0


class ProviderValidationError(Exception):
    """Raised for invalid provider pricing fields (translated to 422)."""


def _validate_pricing(
    *,
    price_input_per_1m: float | None,
    price_output_per_1m: float | None,
    price_currency: str | None,
) -> None:
    for label, value in (
        ("price_input_per_1m", price_input_per_1m),
        ("price_output_per_1m", price_output_per_1m),
    ):
        if value is not None and value < 0:
            raise ProviderValidationError(f"{label} must be >= 0")
    if price_currency is not None and not (
        len(price_currency) == 3 and price_currency.isalpha() and price_currency.isupper()
    ):
        raise ProviderValidationError(
            "price_currency must be exactly 3 uppercase letters (e.g. 'USD')"
        )


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
    supports_vision: bool = False,
    price_input_per_1m: float | None = None,
    price_output_per_1m: float | None = None,
    price_currency: str | None = None,
) -> TiqoraLlmProvider:
    _validate_pricing(
        price_input_per_1m=price_input_per_1m,
        price_output_per_1m=price_output_per_1m,
        price_currency=price_currency,
    )
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
        supports_vision=supports_vision,
        price_input_per_1m=price_input_per_1m,
        price_output_per_1m=price_output_per_1m,
        price_currency=price_currency,
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
    supports_vision: bool | None = None,
    price_input_per_1m: float | None = None,
    price_output_per_1m: float | None = None,
    price_currency: str | None = None,
    valid_id: int | None = None,
) -> TiqoraLlmProvider:
    _validate_pricing(
        price_input_per_1m=price_input_per_1m,
        price_output_per_1m=price_output_per_1m,
        price_currency=price_currency,
    )
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
    if supports_vision is not None:
        row.supports_vision = supports_vision
    if price_input_per_1m is not None:
        row.price_input_per_1m = price_input_per_1m
    if price_output_per_1m is not None:
        row.price_output_per_1m = price_output_per_1m
    if price_currency is not None:
        row.price_currency = price_currency
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


async def _next_copy_name(session: AsyncSession, base_name: str) -> str:
    """``"<name> (Kopie)"``, or ``"<name> (Kopie 2)"``, ``"(Kopie 3)"``, … on
    collision with the ``name`` unique constraint."""
    existing = set((await session.execute(select(TiqoraLlmProvider.name))).scalars().all())
    candidate = f"{base_name} (Kopie)"
    suffix = 2
    while candidate in existing:
        candidate = f"{base_name} (Kopie {suffix})"
        suffix += 1
    return candidate


async def duplicate_provider(
    session: AsyncSession, row: TiqoraLlmProvider, *, change_by: int
) -> TiqoraLlmProvider:
    """Copy a provider row, including its encrypted API key ciphertext (the
    plaintext key never leaves the server — this is a same-process copy of
    the Fernet-encrypted column, not a re-entry). Typical use case: several
    models at the same provider/API key.
    """
    name = await _next_copy_name(session, row.name)
    copy = TiqoraLlmProvider(
        name=name,
        kind=row.kind,
        base_url=row.base_url,
        api_key_enc=row.api_key_enc,
        default_model=row.default_model,
        extra_json=row.extra_json,
        supports_tools=row.supports_tools,
        supports_streaming=row.supports_streaming,
        eu_hosted=row.eu_hosted,
        supports_vision=row.supports_vision,
        price_input_per_1m=row.price_input_per_1m,
        price_output_per_1m=row.price_output_per_1m,
        price_currency=row.price_currency,
        valid_id=row.valid_id,
        create_by=change_by,
        change_by=change_by,
    )
    session.add(copy)
    await session.commit()
    await session.refresh(copy)
    return copy


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
        "supports_vision": bool(row.supports_vision),
        "price_input_per_1m": row.price_input_per_1m,
        "price_output_per_1m": row.price_output_per_1m,
        "price_currency": row.price_currency,
        "valid_id": int(row.valid_id),
        "create_time": row.create_time,
        "change_time": row.change_time,
    }


async def test_provider_connection(
    row: TiqoraLlmProvider,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
    session: AsyncSession | None = None,
) -> ProviderTestResult:
    """Call ``POST {base_url}/chat/completions`` with a mini prompt + a mini
    tool schema to verify both connectivity/auth and tool-calling support.

    ``client`` is injectable (``httpx.AsyncClient(transport=httpx.MockTransport(...))``)
    so tests never hit the network; production callers omit it and a
    short-lived client is created for the single request. When ``session``
    is given, the call (request/response, redacted of nothing — this probe
    never carries PII) is written to ``tiqora_ai_audit_log`` with
    ``feature="test"`` (see :mod:`tiqora.ai.audit`).
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
    start = time.monotonic()
    status_code: int | None = None
    error: str | None = None
    response_json: str | None = None
    model: str | None = None
    try:
        response = await http_client.post(url, headers=headers, json=payload)
        status_code = response.status_code
        if response.status_code >= 400:
            error = f"HTTP {response.status_code}: {response.text[:500]}"
            return ProviderTestResult(ok=False, model=None, tool_calling_ok=False, error=error)
        data = response.json()
        response_json = json.dumps(data)
        model = data.get("model")
        choices = data.get("choices") or []
        tool_calling_ok = False
        if choices:
            message = choices[0].get("message") or {}
            tool_calling_ok = bool(message.get("tool_calls"))
        return ProviderTestResult(ok=True, model=model, tool_calling_ok=tool_calling_ok, error=None)
    except httpx.HTTPError as exc:
        error = str(exc)
        return ProviderTestResult(ok=False, model=None, tool_calling_ok=False, error=error)
    finally:
        if owns_client:
            await http_client.aclose()
        if session is not None:
            await write_audit_log(
                session,
                settings=settings,
                context=AuditContext(
                    feature=AUDIT_FEATURE_TEST,
                    provider_id=row.id,
                    model=model or row.default_model,
                ),
                request_json=json.dumps(payload),
                response_json=response_json,
                status_code=status_code,
                error=error,
                duration_ms=int((time.monotonic() - start) * 1000),
                prompt_tokens=None,
                completion_tokens=None,
            )


__all__ = [
    "ProviderTestResult",
    "ProviderValidationError",
    "create_provider",
    "delete_provider",
    "duplicate_provider",
    "get_provider",
    "list_providers",
    "provider_to_public_dict",
    "test_provider_connection",
    "update_provider",
]
