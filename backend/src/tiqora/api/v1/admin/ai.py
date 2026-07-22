"""Admin API for the Tiqora AI subsystem — ``/api/v1/admin/ai/*`` (Phase A,
see ``~/TIQORA_LLM_PLAN.md``). Every route requires
:data:`tiqora.api.v1.admin.deps.AdminUser`.

Readiness-Gate enforcement (plan §3.0): enabling any of
``enabled_auto_reply`` / ``enabled_summary`` / ``enabled_manual_assist`` on a
queue policy raises :class:`~tiqora.ai.gate.AiGateError`, translated here to
**409** — the client action was rejected because of the current
``operation_mode``, not because the request itself was malformed (422 is
reserved for validation errors: bad autonomy value, missing
service_user_id/llm_provider_id on auto-reply enable, etc.).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status

from tiqora.ai import acl as ai_acl
from tiqora.ai import mcp as ai_mcp
from tiqora.ai import policies as ai_policies
from tiqora.ai import providers as ai_providers
from tiqora.ai import usage as ai_usage
from tiqora.ai.acl import AiAclValidationError
from tiqora.ai.gate import AiGateError, get_operation_mode, set_operation_mode
from tiqora.ai.models import TiqoraMcpClient
from tiqora.ai.policies import QueuePolicyValidationError
from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.ai_schemas import (
    AiAclCreate,
    AiAclOut,
    AiAclUpdate,
    AiQueuePolicyCreate,
    AiQueuePolicyOut,
    AiQueuePolicyUpdate,
    AiSettingsOut,
    AiSettingsUpdate,
    AiUsageOut,
    AiUsagePageOut,
    LlmProviderCreate,
    LlmProviderOut,
    LlmProviderTestOut,
    LlmProviderUpdate,
    McpClientCreate,
    McpClientOut,
    McpClientUpdate,
    McpDiscoverOut,
    McpToolPolicyOut,
    McpToolPolicyUpdate,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.config import get_settings
from tiqora.domain.settings_store import (
    KEY_AI_DISCLOSURE_DEFAULT,
    KEY_AI_GLOBAL_REPLIES_PER_HOUR,
    get_setting,
    set_setting,
)

router = APIRouter(prefix="/ai", tags=["admin:ai"])


def _not_found(what: str, ident: object) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found: {ident}")


# ---------------------------------------------------------------------------
# System settings
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=AiSettingsOut)
async def get_ai_settings(admin: AdminUser, session: DbSession) -> AiSettingsOut:
    _ = admin
    mode = await get_operation_mode(session)
    disclosure = await get_setting(session, KEY_AI_DISCLOSURE_DEFAULT) or ""
    global_cap_raw = await get_setting(session, KEY_AI_GLOBAL_REPLIES_PER_HOUR)
    global_cap = int(global_cap_raw) if global_cap_raw else None
    return AiSettingsOut(
        operation_mode=mode,  # type: ignore[arg-type]
        disclosure_default_text=disclosure,
        global_max_replies_per_hour=global_cap,
    )


@router.put("/settings", response_model=AiSettingsOut)
async def put_ai_settings(
    body: AiSettingsUpdate, admin: AdminUser, session: DbSession
) -> AiSettingsOut:
    _ = admin
    if body.operation_mode is not None:
        try:
            await set_operation_mode(session, body.operation_mode)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
    if body.disclosure_default_text is not None:
        await set_setting(session, KEY_AI_DISCLOSURE_DEFAULT, body.disclosure_default_text)
    if body.global_max_replies_per_hour is not None:
        await set_setting(
            session, KEY_AI_GLOBAL_REPLIES_PER_HOUR, str(body.global_max_replies_per_hour)
        )
    return await get_ai_settings(admin, session)


# ---------------------------------------------------------------------------
# LLM providers
# ---------------------------------------------------------------------------


@router.get("/providers", response_model=list[LlmProviderOut])
async def list_llm_providers(admin: AdminUser, session: DbSession) -> list[LlmProviderOut]:
    _ = admin
    rows = await ai_providers.list_providers(session)
    return [LlmProviderOut.model_validate(ai_providers.provider_to_public_dict(r)) for r in rows]


@router.post("/providers", response_model=LlmProviderOut, status_code=status.HTTP_201_CREATED)
async def create_llm_provider(
    body: LlmProviderCreate, admin: AdminUser, session: DbSession
) -> LlmProviderOut:
    settings = get_settings()
    row = await ai_providers.create_provider(
        session,
        settings=settings,
        change_by=admin.id,
        name=body.name,
        kind=body.kind,
        base_url=body.base_url,
        default_model=body.default_model,
        api_key=body.api_key,
        extra_json=body.extra_json,
        supports_tools=body.supports_tools,
        supports_streaming=body.supports_streaming,
        eu_hosted=body.eu_hosted,
    )
    return LlmProviderOut.model_validate(ai_providers.provider_to_public_dict(row))


@router.put("/providers/{provider_id}", response_model=LlmProviderOut)
async def update_llm_provider(
    provider_id: int, body: LlmProviderUpdate, admin: AdminUser, session: DbSession
) -> LlmProviderOut:
    row = await ai_providers.get_provider(session, provider_id)
    if row is None:
        raise _not_found("Provider", provider_id)
    settings = get_settings()
    updated = await ai_providers.update_provider(
        session,
        row,
        settings=settings,
        change_by=admin.id,
        **body.model_dump(exclude_unset=True),
    )
    return LlmProviderOut.model_validate(ai_providers.provider_to_public_dict(updated))


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_provider(provider_id: int, admin: AdminUser, session: DbSession) -> None:
    _ = admin
    row = await ai_providers.get_provider(session, provider_id)
    if row is None:
        raise _not_found("Provider", provider_id)
    await ai_providers.delete_provider(session, row)


@router.post("/providers/{provider_id}/test", response_model=LlmProviderTestOut)
async def test_llm_provider(
    provider_id: int, admin: AdminUser, session: DbSession
) -> LlmProviderTestOut:
    _ = admin
    row = await ai_providers.get_provider(session, provider_id)
    if row is None:
        raise _not_found("Provider", provider_id)
    result = await ai_providers.test_provider_connection(row, settings=get_settings())
    return LlmProviderTestOut(
        ok=result.ok, model=result.model, tool_calling_ok=result.tool_calling_ok, error=result.error
    )


# ---------------------------------------------------------------------------
# MCP clients + tool policies
# ---------------------------------------------------------------------------


@router.get("/mcp-clients", response_model=list[McpClientOut])
async def list_mcp_clients_route(admin: AdminUser, session: DbSession) -> list[McpClientOut]:
    _ = admin
    rows = await ai_mcp.list_mcp_clients(session)
    return [McpClientOut.model_validate(ai_mcp.mcp_client_to_public_dict(r)) for r in rows]


@router.post("/mcp-clients", response_model=McpClientOut, status_code=status.HTTP_201_CREATED)
async def create_mcp_client_route(
    body: McpClientCreate, admin: AdminUser, session: DbSession
) -> McpClientOut:
    settings = get_settings()
    row = await ai_mcp.create_mcp_client(
        session,
        settings=settings,
        change_by=admin.id,
        name=body.name,
        url=body.url,
        auth_token=body.auth_token,
        transport=body.transport,
    )
    return McpClientOut.model_validate(ai_mcp.mcp_client_to_public_dict(row))


async def _get_mcp_client_or_404(session: DbSession, client_id: int) -> TiqoraMcpClient:
    row = await ai_mcp.get_mcp_client(session, client_id)
    if row is None:
        raise _not_found("MCP client", client_id)
    return row


@router.put("/mcp-clients/{client_id}", response_model=McpClientOut)
async def update_mcp_client_route(
    client_id: int, body: McpClientUpdate, admin: AdminUser, session: DbSession
) -> McpClientOut:
    row = await _get_mcp_client_or_404(session, client_id)
    settings = get_settings()
    updated = await ai_mcp.update_mcp_client(
        session,
        row,
        settings=settings,
        change_by=admin.id,
        **body.model_dump(exclude_unset=True),
    )
    return McpClientOut.model_validate(ai_mcp.mcp_client_to_public_dict(updated))


@router.delete("/mcp-clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_client_route(client_id: int, admin: AdminUser, session: DbSession) -> None:
    _ = admin
    row = await _get_mcp_client_or_404(session, client_id)
    await ai_mcp.delete_mcp_client(session, row)


@router.post("/mcp-clients/{client_id}/discover", response_model=McpDiscoverOut)
async def discover_mcp_client_tools(
    client_id: int, admin: AdminUser, session: DbSession
) -> McpDiscoverOut:
    _ = admin
    row = await _get_mcp_client_or_404(session, client_id)
    result = await ai_mcp.refresh_tools(session, row, settings=get_settings())
    return McpDiscoverOut(tool_names=result.tool_names, added=result.added, removed=result.removed)


@router.get("/mcp-clients/{client_id}/tools", response_model=list[McpToolPolicyOut])
async def list_mcp_tool_policies(
    client_id: int, admin: AdminUser, session: DbSession
) -> list[McpToolPolicyOut]:
    _ = admin
    await _get_mcp_client_or_404(session, client_id)
    rows = await ai_mcp.list_tool_policies(session, client_id)
    return [McpToolPolicyOut.model_validate(r) for r in rows]


@router.put("/mcp-clients/{client_id}/tools/{tool_name}", response_model=McpToolPolicyOut)
async def update_mcp_tool_policy(
    client_id: int,
    tool_name: str,
    body: McpToolPolicyUpdate,
    admin: AdminUser,
    session: DbSession,
) -> McpToolPolicyOut:
    _ = admin
    await _get_mcp_client_or_404(session, client_id)
    updated = await ai_mcp.set_tool_policy(
        session, client_id, tool_name, enabled=body.enabled, mutating=body.mutating
    )
    if updated is None:
        raise _not_found("Tool policy", f"{client_id}/{tool_name}")
    return McpToolPolicyOut.model_validate(updated)


# ---------------------------------------------------------------------------
# Queue AI policies
# ---------------------------------------------------------------------------


@router.get("/queue-policies", response_model=list[AiQueuePolicyOut])
async def list_queue_policies_route(admin: AdminUser, session: DbSession) -> list[AiQueuePolicyOut]:
    _ = admin
    rows = await ai_policies.list_queue_policies(session)
    return [AiQueuePolicyOut.model_validate(r) for r in rows]


@router.post(
    "/queue-policies", response_model=AiQueuePolicyOut, status_code=status.HTTP_201_CREATED
)
async def create_queue_policy_route(
    body: AiQueuePolicyCreate, admin: AdminUser, session: DbSession
) -> AiQueuePolicyOut:
    try:
        row = await ai_policies.create_queue_policy(
            session, change_by=admin.id, **body.model_dump()
        )
    except QueuePolicyValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except AiGateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AiQueuePolicyOut.model_validate(row)


@router.put("/queue-policies/{policy_id}", response_model=AiQueuePolicyOut)
async def update_queue_policy_route(
    policy_id: int, body: AiQueuePolicyUpdate, admin: AdminUser, session: DbSession
) -> AiQueuePolicyOut:
    row = await ai_policies.get_queue_policy(session, policy_id)
    if row is None:
        raise _not_found("Queue policy", policy_id)
    try:
        updated = await ai_policies.update_queue_policy(
            session, row, change_by=admin.id, **body.model_dump(exclude_unset=True)
        )
    except QueuePolicyValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except AiGateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AiQueuePolicyOut.model_validate(updated)


@router.delete("/queue-policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_queue_policy_route(policy_id: int, admin: AdminUser, session: DbSession) -> None:
    _ = admin
    row = await ai_policies.get_queue_policy(session, policy_id)
    if row is None:
        raise _not_found("Queue policy", policy_id)
    await ai_policies.delete_queue_policy(session, row)


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


@router.get("/usage", response_model=AiUsagePageOut)
async def list_ai_usage(
    admin: AdminUser,
    session: DbSession,
    queue_id: int | None = None,
    feature: str | None = None,
    ts_from: datetime | None = Query(None, alias="from"),  # noqa: B008 — fastapi.Query, not a mutable default
    ts_to: datetime | None = Query(None, alias="to"),  # noqa: B008 — fastapi.Query, not a mutable default
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> AiUsagePageOut:
    _ = admin
    result = await ai_usage.list_usage(
        session,
        queue_id=queue_id,
        feature=feature,
        ts_from=ts_from,
        ts_to=ts_to,
        page=page,
        page_size=page_size,
    )
    return AiUsagePageOut(
        items=[AiUsageOut.model_validate(r) for r in result.items],
        total=result.total,
        total_prompt_tokens=result.total_prompt_tokens,
        total_completion_tokens=result.total_completion_tokens,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# ACL
# ---------------------------------------------------------------------------


@router.get("/acl", response_model=list[AiAclOut])
async def list_ai_acl(admin: AdminUser, session: DbSession) -> list[AiAclOut]:
    _ = admin
    rows = await ai_acl.list_acls(session)
    return [AiAclOut.model_validate(r) for r in rows]


@router.post("/acl", response_model=AiAclOut, status_code=status.HTTP_201_CREATED)
async def create_ai_acl(body: AiAclCreate, admin: AdminUser, session: DbSession) -> AiAclOut:
    _ = admin
    try:
        row = await ai_acl.create_acl(session, **body.model_dump())
    except AiAclValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return AiAclOut.model_validate(row)


@router.put("/acl/{acl_id}", response_model=AiAclOut)
async def update_ai_acl(
    acl_id: int, body: AiAclUpdate, admin: AdminUser, session: DbSession
) -> AiAclOut:
    _ = admin
    row = await ai_acl.get_acl(session, acl_id)
    if row is None:
        raise _not_found("ACL entry", acl_id)
    try:
        updated = await ai_acl.update_acl(session, row, **body.model_dump(exclude_unset=True))
    except AiAclValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return AiAclOut.model_validate(updated)


@router.delete("/acl/{acl_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ai_acl(acl_id: int, admin: AdminUser, session: DbSession) -> None:
    _ = admin
    row = await ai_acl.get_acl(session, acl_id)
    if row is None:
        raise _not_found("ACL entry", acl_id)
    await ai_acl.delete_acl(session, row)
