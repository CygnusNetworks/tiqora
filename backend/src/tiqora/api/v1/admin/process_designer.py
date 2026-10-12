"""Process designer admin API: draft CRUD for processes and their entities (plan D11).

Reads and writes the ``pm_*`` draft rows through :class:`ProcessWriter`; nothing here is
live until somebody deploys (``/admin/deploy``). Every write commits once, at the end of
the request. Validation, exchange and revision routes live in sibling modules.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.db.legacy.process_management import PmActivity, PmProcess
from tiqora.domain.ownership import get_ownership_state
from tiqora.process.designer.catalog import build_designer_catalog
from tiqora.process.designer.draft import (
    list_entities,
    list_process_summaries,
    load_entity,
    load_process_draft,
)
from tiqora.process.designer.exchange import (
    ImportRefused,
    ProcessImportIn,
    ProcessImportOut,
    export_process_yaml,
    import_process_yaml,
)
from tiqora.process.designer.schemas import (
    DesignerCatalogOut,
    DesignerEntityOut,
    EntityCreateIn,
    EntitySummaryOut,
    EntityUpdateIn,
    ProcessCreateIn,
    ProcessDraftOut,
    ProcessDraftSummaryOut,
    ProcessUpdateIn,
)
from tiqora.process.designer.validation import (
    ValidationIssueOut,
    load_reference_data,
    validate_process,
)
from tiqora.process.designer.writer import (
    EntityInUse,
    EntityNotFound,
    InvalidReference,
    ProcessNotInactive,
    ProcessWriter,
    VersionConflict,
)
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/process-designer", tags=["admin:process-designer"])

SubEntityPath = Literal["Activity", "ActivityDialog", "Transition", "TransitionAction"]

NOT_FOUND_DETAIL = "Eintrag nicht gefunden."
CONFLICT_DETAIL = (
    "Der Eintrag wurde inzwischen geändert. Bitte laden Sie die Seite neu und wiederholen Sie "
    "die Änderung."
)
NOT_INACTIVE_DETAIL = "Nur inaktive Prozesse können gelöscht werden."


@contextmanager
def _mapped_errors() -> Iterator[None]:
    """Translate the writer's domain errors into HTTP errors (German details)."""
    try:
        yield
    except EntityNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL) from None
    except VersionConflict:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=CONFLICT_DETAIL) from None
    except ProcessNotInactive:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=NOT_INACTIVE_DETAIL) from None
    except (InvalidReference, ImportRefused) as exc:
        raise HTTPException(422, detail=str(exc)) from None


async def _user_names(
    session: DbSession, entity_type: SubEntityPath, used_by: list[str]
) -> list[str]:
    """Names of the entities that use an entity (processes, or activities for dialogs)."""
    model = PmActivity if entity_type == "ActivityDialog" else PmProcess
    rows = (
        await session.execute(
            select(model.entity_id, model.name).where(model.entity_id.in_(used_by))
        )
    ).all()
    names = {eid: name for eid, name in rows}
    return [names.get(eid, eid) for eid in used_by]


@router.get("/catalog", response_model=DesignerCatalogOut)
async def get_catalog(admin: AdminUser, session: DbSession) -> DesignerCatalogOut:
    _ = admin
    return await build_designer_catalog(session, SysConfig(session))


@router.get("/processes", response_model=list[ProcessDraftSummaryOut])
async def list_processes(admin: AdminUser, session: DbSession) -> list[ProcessDraftSummaryOut]:
    _ = admin
    return await list_process_summaries(session)


@router.get("/entities/{entity_type}", response_model=list[EntitySummaryOut])
async def list_entity_summaries(
    entity_type: SubEntityPath, admin: AdminUser, session: DbSession
) -> list[EntitySummaryOut]:
    _ = admin
    return await list_entities(session, entity_type)


@router.get("/processes/{process_entity_id}/validation", response_model=list[ValidationIssueOut])
async def get_validation(
    process_entity_id: str, admin: AdminUser, session: DbSession, settings: AppSettings
) -> list[ValidationIssueOut]:
    _ = admin
    with _mapped_errors():
        draft = await load_process_draft(session, process_entity_id)
    refs = await load_reference_data(session)
    parallel = not (await get_ownership_state(session, settings)).active
    issues = validate_process(draft, refs, parallel_operation=parallel)
    return [ValidationIssueOut.of(i) for i in issues]


@router.post("/processes", response_model=ProcessDraftOut, status_code=status.HTTP_201_CREATED)
async def create_process(
    body: ProcessCreateIn, admin: AdminUser, session: DbSession
) -> ProcessDraftOut:
    with _mapped_errors():
        written = await ProcessWriter(session, user_id=admin.id).create_process(
            name=body.name, description=body.description
        )
        await session.commit()
        return await load_process_draft(session, written.entity_id)


@router.get("/processes/{process_entity_id}", response_model=ProcessDraftOut)
async def get_process(
    process_entity_id: str, admin: AdminUser, session: DbSession
) -> ProcessDraftOut:
    _ = admin
    with _mapped_errors():
        return await load_process_draft(session, process_entity_id)


@router.put("/processes/{process_entity_id}", response_model=ProcessDraftOut)
async def update_process(
    process_entity_id: str, body: ProcessUpdateIn, admin: AdminUser, session: DbSession
) -> ProcessDraftOut:
    with _mapped_errors():
        await ProcessWriter(session, user_id=admin.id).update_process(
            process_entity_id,
            expected_version=body.expected_version,
            name=body.name,
            description=body.description,
            state_entity_id=body.state_entity_id,
            start_activity=body.start_activity,
            start_activity_dialog=body.start_activity_dialog,
            path=body.path,
            layout=body.layout,
        )
        await session.commit()
        return await load_process_draft(session, process_entity_id)


@router.post(
    "/processes/{process_entity_id}/copy",
    response_model=ProcessDraftOut,
    status_code=status.HTTP_201_CREATED,
)
async def copy_process(
    process_entity_id: str, admin: AdminUser, session: DbSession
) -> ProcessDraftOut:
    with _mapped_errors():
        written = await ProcessWriter(session, user_id=admin.id).copy_process(process_entity_id)
        await session.commit()
        return await load_process_draft(session, written.entity_id)


@router.delete("/processes/{process_entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_process(process_entity_id: str, admin: AdminUser, session: DbSession) -> Response:
    with _mapped_errors():
        await ProcessWriter(session, user_id=admin.id).delete_process(process_entity_id)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/entities/{entity_type}",
    response_model=DesignerEntityOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_entity(
    entity_type: SubEntityPath, body: EntityCreateIn, admin: AdminUser, session: DbSession
) -> DesignerEntityOut:
    with _mapped_errors():
        written = await ProcessWriter(session, user_id=admin.id).create_entity(
            entity_type, name=body.name, config=body.config
        )
        await session.commit()
        return await load_entity(session, entity_type, written.entity_id)


@router.put("/entities/{entity_type}/{entity_id}", response_model=DesignerEntityOut)
async def update_entity(
    entity_type: SubEntityPath,
    entity_id: str,
    body: EntityUpdateIn,
    admin: AdminUser,
    session: DbSession,
) -> DesignerEntityOut:
    with _mapped_errors():
        await ProcessWriter(session, user_id=admin.id).update_entity(
            entity_type,
            entity_id,
            expected_version=body.expected_version,
            name=body.name,
            config=body.config,
        )
        await session.commit()
        return await load_entity(session, entity_type, entity_id)


@router.delete("/entities/{entity_type}/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entity(
    entity_type: SubEntityPath, entity_id: str, admin: AdminUser, session: DbSession
) -> Response:
    try:
        with _mapped_errors():
            await ProcessWriter(session, user_id=admin.id).delete_entity(entity_type, entity_id)
            await session.commit()
    except EntityInUse as exc:
        names = await _user_names(session, entity_type, exc.used_by)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Wird noch verwendet von: " + ", ".join(names),
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/processes/{process_entity_id}/export")
async def export_process(process_entity_id: str, admin: AdminUser, session: DbSession) -> Response:
    """Znuny-format process file (``Export_ProcessEntityID_<id>.yml``), a plain download."""
    _ = admin
    with _mapped_errors():
        filename, content = await export_process_yaml(session, process_entity_id)
    return Response(
        content=content,
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", response_model=ProcessImportOut)
async def import_process(
    body: ProcessImportIn, admin: AdminUser, session: DbSession
) -> ProcessImportOut:
    """Import a Znuny process file as a draft; all or nothing."""
    with _mapped_errors():
        result = await import_process_yaml(
            session,
            ProcessWriter(session, user_id=admin.id),
            body.content,
            overwrite_existing=body.overwrite_existing,
        )
        await session.commit()
        return result
