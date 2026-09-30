"""Admin API for LLM models, profiles and global task assignments —
``/api/v1/admin/ai/models``, ``/profiles``, ``/task-defaults`` (same prefix
and tag as :mod:`tiqora.api.v1.admin.ai`; split out to keep that file
readable). Logic lives in :mod:`tiqora.ai.llm_catalog`; every route requires
:data:`tiqora.api.v1.admin.deps.AdminUser`.

Status codes: 404 unknown id, 409 still in use / duplicate, 422 invalid input
or a task whose needs a profile would no longer meet. ``detail`` is a German
sentence meant to be shown to the admin as is.
"""

from __future__ import annotations

from collections.abc import Awaitable

from fastapi import APIRouter, HTTPException, status

from tiqora.ai import llm_catalog
from tiqora.ai import providers as ai_providers
from tiqora.ai.models import TiqoraLlmModel, TiqoraLlmProfile
from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.ai_schemas import (
    AiTaskProfileItem,
    LlmModelIn,
    LlmModelOut,
    LlmModelTestOut,
    LlmProfileIn,
    LlmProfileOut,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.config import get_settings

router = APIRouter(prefix="/ai", tags=["admin:ai"])


async def _catalog_call[T](call: Awaitable[T]) -> T:
    """Run a catalog service call, mapping its errors to HTTP."""
    try:
        return await call
    except llm_catalog.CatalogValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except llm_catalog.CatalogConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


async def _model_or_404(session: DbSession, model_id: int) -> TiqoraLlmModel:
    row = await llm_catalog.get_model(session, model_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Modell {model_id} nicht gefunden."
        )
    return row


async def _model_out(session: DbSession, row: TiqoraLlmModel) -> LlmModelOut:
    return LlmModelOut.model_validate((await llm_catalog.models_to_public_dicts(session, [row]))[0])


def _model_fields(body: LlmModelIn) -> llm_catalog.ModelFields:
    return llm_catalog.ModelFields(**body.model_dump())


@router.get("/models", response_model=list[LlmModelOut])
async def list_llm_models(admin: AdminUser, session: DbSession) -> list[LlmModelOut]:
    _ = admin
    rows = await llm_catalog.list_models(session)
    return [
        LlmModelOut.model_validate(d)
        for d in await llm_catalog.models_to_public_dicts(session, rows)
    ]


@router.post("/models", response_model=LlmModelOut, status_code=status.HTTP_201_CREATED)
async def create_llm_model(body: LlmModelIn, admin: AdminUser, session: DbSession) -> LlmModelOut:
    row = await _catalog_call(
        llm_catalog.create_model(session, _model_fields(body), change_by=admin.id)
    )
    return await _model_out(session, row)


@router.put("/models/{model_id}", response_model=LlmModelOut)
async def update_llm_model(
    model_id: int, body: LlmModelIn, admin: AdminUser, session: DbSession
) -> LlmModelOut:
    row = await _model_or_404(session, model_id)
    updated = await _catalog_call(
        llm_catalog.update_model(session, row, _model_fields(body), change_by=admin.id)
    )
    return await _model_out(session, updated)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_model(model_id: int, admin: AdminUser, session: DbSession) -> None:
    _ = admin
    row = await _model_or_404(session, model_id)
    await _catalog_call(llm_catalog.delete_model(session, row))


@router.post("/models/{model_id}/test", response_model=LlmModelTestOut)
async def test_llm_model(model_id: int, admin: AdminUser, session: DbSession) -> LlmModelTestOut:
    """Chat call with a tool schema (when the model supports tools) against
    the model's provider: checks key, model id and tool calling."""
    _ = admin
    row = await _model_or_404(session, model_id)
    provider = await ai_providers.get_provider(session, row.provider_id)
    if provider is None:  # pragma: no cover — FK guarantees it
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider fehlt.")
    result = await llm_catalog.probe_model_connection(
        provider, row, settings=get_settings(), session=session
    )
    return LlmModelTestOut(
        ok=result.ok, model=result.model, tool_calling_ok=result.tool_calling_ok, error=result.error
    )


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


async def _profile_or_404(session: DbSession, profile_id: int) -> TiqoraLlmProfile:
    row = await llm_catalog.get_profile(session, profile_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Profil {profile_id} nicht gefunden."
        )
    return row


async def _profile_out(session: DbSession, row: TiqoraLlmProfile) -> LlmProfileOut:
    return LlmProfileOut.model_validate(
        (await llm_catalog.profiles_to_public_dicts(session, [row]))[0]
    )


def _profile_fields(body: LlmProfileIn) -> llm_catalog.ProfileFields:
    return llm_catalog.ProfileFields(**body.model_dump())


@router.get("/profiles", response_model=list[LlmProfileOut])
async def list_llm_profiles(admin: AdminUser, session: DbSession) -> list[LlmProfileOut]:
    _ = admin
    rows = await llm_catalog.list_profiles(session)
    return [
        LlmProfileOut.model_validate(d)
        for d in await llm_catalog.profiles_to_public_dicts(session, rows)
    ]


@router.post("/profiles", response_model=LlmProfileOut, status_code=status.HTTP_201_CREATED)
async def create_llm_profile(
    body: LlmProfileIn, admin: AdminUser, session: DbSession
) -> LlmProfileOut:
    row = await _catalog_call(
        llm_catalog.create_profile(session, _profile_fields(body), change_by=admin.id)
    )
    return await _profile_out(session, row)


@router.put("/profiles/{profile_id}", response_model=LlmProfileOut)
async def update_llm_profile(
    profile_id: int, body: LlmProfileIn, admin: AdminUser, session: DbSession
) -> LlmProfileOut:
    row = await _profile_or_404(session, profile_id)
    updated = await _catalog_call(
        llm_catalog.update_profile(session, row, _profile_fields(body), change_by=admin.id)
    )
    return await _profile_out(session, updated)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_llm_profile(profile_id: int, admin: AdminUser, session: DbSession) -> None:
    _ = admin
    row = await _profile_or_404(session, profile_id)
    await _catalog_call(llm_catalog.delete_profile(session, row))


# ---------------------------------------------------------------------------
# Global task defaults
# ---------------------------------------------------------------------------


@router.get("/task-defaults", response_model=list[AiTaskProfileItem])
async def get_ai_task_defaults(admin: AdminUser, session: DbSession) -> list[AiTaskProfileItem]:
    """All six tasks; ``profile_id`` null = no global profile."""
    _ = admin
    return [
        AiTaskProfileItem.model_validate(d) for d in await llm_catalog.get_task_defaults(session)
    ]


@router.put("/task-defaults", response_model=list[AiTaskProfileItem])
async def put_ai_task_defaults(
    body: list[AiTaskProfileItem], admin: AdminUser, session: DbSession
) -> list[AiTaskProfileItem]:
    """Replaces all global defaults (a task not listed → no profile)."""
    _ = admin
    items = await _catalog_call(
        llm_catalog.put_task_defaults(session, [(i.task, i.profile_id) for i in body])
    )
    return [AiTaskProfileItem.model_validate(d) for d in items]
