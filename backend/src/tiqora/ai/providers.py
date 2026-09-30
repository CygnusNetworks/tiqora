"""LLM provider CRUD + the provider's remote model list (plan §3.2).

The per-model connection/tool-calling probe lives in
:mod:`tiqora.ai.llm_catalog` (models belong to providers since the LLM
routing rework).

Follows the Fernet-at-rest pattern from ``tiqora.domain.mail_outbound``: the
admin API never returns the decrypted API key, only ``has_api_key``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import PROVIDER_KIND_OPENAI_COMPAT, TiqoraLlmProvider
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret, encrypt_secret

REMOTE_MODELS_TIMEOUT_SECONDS = 10.0
ERROR_TEXT_LIMIT = 300
KIND_NOT_SUPPORTED = "Nur OpenAI-kompatible Provider werden unterstützt."


class ProviderValidationError(Exception):
    """Raised for invalid provider fields (translated to 422)."""


class RemoteModelsError(Exception):
    """The provider's model list could not be read (translated to 502)."""


def _validate_kind(kind: str | None) -> None:
    # The runtime only has the OpenAI-compatible client; any other kind would
    # silently be spoken to as OpenAI. Existing rows are left alone.
    if kind is not None and kind != PROVIDER_KIND_OPENAI_COMPAT:
        raise ProviderValidationError(KIND_NOT_SUPPORTED)


def _validate_pricing(*, price_currency: str | None) -> None:
    if price_currency is not None and not (
        len(price_currency) == 3 and price_currency.isalpha() and price_currency.isupper()
    ):
        raise ProviderValidationError(
            "price_currency must be exactly 3 uppercase letters (e.g. 'USD')"
        )


def _validate_budget(
    *,
    budget_cost_day: float | None,
    budget_cost_week: float | None,
    budget_cost_month: float | None,
) -> None:
    for label, value in (
        ("budget_cost_day", budget_cost_day),
        ("budget_cost_week", budget_cost_week),
        ("budget_cost_month", budget_cost_month),
    ):
        if value is not None and value < 0:
            raise ProviderValidationError(f"{label} must be >= 0")


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
    api_key: str | None,
    extra_json: str | None = None,
    eu_hosted: bool = False,
    price_currency: str | None = None,
    budget_cost_day: float | None = None,
    budget_cost_week: float | None = None,
    budget_cost_month: float | None = None,
) -> TiqoraLlmProvider:
    _validate_kind(kind)
    _validate_pricing(price_currency=price_currency)
    _validate_budget(
        budget_cost_day=budget_cost_day,
        budget_cost_week=budget_cost_week,
        budget_cost_month=budget_cost_month,
    )
    # Keys/URLs arrive via copy-paste; stray whitespace or a trailing newline
    # silently breaks the Bearer header at the provider (opaque 401s).
    api_key = api_key.strip() if api_key else None
    row = TiqoraLlmProvider(
        name=name,
        kind=kind,
        base_url=base_url.strip(),
        api_key_enc=encrypt_secret(settings.secret_key, api_key) if api_key else None,
        extra_json=extra_json,
        eu_hosted=eu_hosted,
        price_currency=price_currency,
        budget_cost_day=budget_cost_day,
        budget_cost_week=budget_cost_week,
        budget_cost_month=budget_cost_month,
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
    api_key: str | None = None,
    extra_json: str | None = None,
    eu_hosted: bool | None = None,
    price_currency: str | None = None,
    budget_cost_day: float | None = None,
    budget_cost_week: float | None = None,
    budget_cost_month: float | None = None,
    valid_id: int | None = None,
) -> TiqoraLlmProvider:
    _validate_kind(kind)
    _validate_pricing(price_currency=price_currency)
    _validate_budget(
        budget_cost_day=budget_cost_day,
        budget_cost_week=budget_cost_week,
        budget_cost_month=budget_cost_month,
    )
    if name is not None:
        row.name = name
    if kind is not None:
        row.kind = kind
    if base_url is not None:
        row.base_url = base_url.strip()
    if api_key is not None and api_key.strip() != "":
        row.api_key_enc = encrypt_secret(settings.secret_key, api_key.strip())
    if extra_json is not None:
        row.extra_json = extra_json
    if eu_hosted is not None:
        row.eu_hosted = eu_hosted
    if price_currency is not None:
        row.price_currency = price_currency
    if budget_cost_day is not None:
        row.budget_cost_day = budget_cost_day
    if budget_cost_week is not None:
        row.budget_cost_week = budget_cost_week
    if budget_cost_month is not None:
        row.budget_cost_month = budget_cost_month
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
    the Fernet-encrypted column, not a re-entry). Models are not copied.
    """
    name = await _next_copy_name(session, row.name)
    copy = TiqoraLlmProvider(
        name=name,
        kind=row.kind,
        base_url=row.base_url,
        api_key_enc=row.api_key_enc,
        extra_json=row.extra_json,
        eu_hosted=row.eu_hosted,
        price_currency=row.price_currency,
        budget_cost_day=row.budget_cost_day,
        budget_cost_week=row.budget_cost_week,
        budget_cost_month=row.budget_cost_month,
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
        "has_api_key": bool(row.api_key_enc),
        "extra_json": row.extra_json,
        "eu_hosted": bool(row.eu_hosted),
        "price_currency": row.price_currency,
        "budget_cost_day": row.budget_cost_day,
        "budget_cost_week": row.budget_cost_week,
        "budget_cost_month": row.budget_cost_month,
        "valid_id": int(row.valid_id),
        "create_time": row.create_time,
        "change_time": row.change_time,
    }


def http_client(timeout_seconds: float) -> httpx.AsyncClient:
    """The one place an outbound HTTP client for provider calls is built
    (admin probes only; tests replace it with a ``MockTransport`` client).
    Redirects are never followed — see :mod:`tiqora.security.outbound`."""
    return httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)


def shorten(text: str, limit: int = ERROR_TEXT_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def list_remote_models(
    row: TiqoraLlmProvider,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """``GET {base_url}/models`` with the provider's key → the sorted,
    de-duplicated ``data[].id`` values (OpenAI list shape).

    Raises :class:`RemoteModelsError` with a short, human-readable reason for
    anything that is not a usable list: blocked URL, network error, HTTP
    error status (with the provider's own error text), unexpected body.
    """
    from tiqora.security.outbound import OutboundURLError, pin_outbound_url

    api_key = decrypt_secret(settings.secret_key, row.api_key_enc) if row.api_key_enc else None
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = row.base_url.rstrip("/") + "/models"
    try:
        pinned = pin_outbound_url(url, allow_private_networks=True)
    except OutboundURLError as exc:
        raise RemoteModelsError(f"URL abgelehnt: {exc}") from exc
    owns_client = client is None
    http = client or http_client(REMOTE_MODELS_TIMEOUT_SECONDS)
    try:
        response = await http.get(
            pinned.request_url,
            headers=pinned.request_headers(headers),
            extensions=pinned.request_extensions(),
        )
    except httpx.HTTPError as exc:
        raise RemoteModelsError(str(exc) or type(exc).__name__) from exc
    finally:
        if owns_client:
            await http.aclose()
    if response.status_code >= 400:
        raise RemoteModelsError(f"HTTP {response.status_code}: {response.text}")
    try:
        data = response.json()
    except ValueError as exc:
        raise RemoteModelsError("Antwort ist kein JSON") from exc
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise RemoteModelsError("Antwort enthält keine Modellliste (data[])")
    ids = {
        item["id"].strip()
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
    }
    return sorted(ids)


__all__ = [
    "ERROR_TEXT_LIMIT",
    "KIND_NOT_SUPPORTED",
    "REMOTE_MODELS_TIMEOUT_SECONDS",
    "ProviderValidationError",
    "RemoteModelsError",
    "create_provider",
    "delete_provider",
    "duplicate_provider",
    "get_provider",
    "http_client",
    "list_providers",
    "list_remote_models",
    "provider_to_public_dict",
    "shorten",
    "update_provider",
]
