"""Change-management administration — ``/api/v1/admin/itsm/change`` (admins only).

Templates, freeze windows, CABs, risk questions and the legacy archive (Znuny changes, Mode B: the
archive runs as a job, ``POST /legacy-archive`` starts it, ``GET /api/v1/jobs/{id}`` is polled).
Settings are edited through the generic settings-sections API (section ``itsm_change``).
``ChangeError`` is an ``HTTPException``: the answer is ``{"detail": {"code", ...}}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.itsm_change_schemas import (
    ChangeTemplateOut,
    FreezeWindowOut,
    LegacyArchiveStartIn,
    LegacyArchiveStatusOut,
    freeze_out,
    legacy_archive_status_out,
    template_out,
)
from tiqora.api.v1.itsm_change_schemas import CabOut, cab_out
from tiqora.itsm.change.cab import (
    CabIn,
    CabMemberIn,
    create_cab,
    delete_cab,
    list_cabs,
    set_members,
    update_cab,
)
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.freeze import (
    FreezeIn,
    create_freeze,
    delete_freeze,
    get_freeze,
    list_freezes,
    update_freeze,
)
from tiqora.itsm.change.legacy.archive import (
    ARCHIVE_DEDUPE_KEY,
    ARCHIVE_JOB_KIND,
    LegacyArchivePayload,
    archive_blocker,
    archive_status,
)
from tiqora.itsm.change.risk import (
    ChangeRiskExamplesOut,
    ChangeRiskQuestionIn,
    ChangeRiskQuestionOrderIn,
    ChangeRiskQuestionOut,
    create_question,
    delete_question,
    insert_examples,
    list_questions,
    reorder_questions,
    update_question,
)
from tiqora.itsm.change.templates import (
    TemplateIn,
    create_template,
    delete_template,
    get_template,
    list_templates,
    update_template,
)
from tiqora.jobs.schemas import JobAccepted
from tiqora.jobs.service import enqueue_job

router = APIRouter(prefix="/itsm/change", tags=["admin:itsm-change"])


@router.get("/templates", response_model=list[ChangeTemplateOut])
async def get_templates(admin: AdminUser, session: DbSession) -> list[ChangeTemplateOut]:
    """All templates, valid or not."""
    _ = admin
    return [template_out(v) for v in await list_templates(session, include_invalid=True)]


@router.post("/templates", response_model=ChangeTemplateOut, status_code=status.HTTP_201_CREATED)
async def post_template(
    body: TemplateIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> ChangeTemplateOut:
    out = template_out(await create_template(session, settings, data=body, user_id=admin.id))
    await session.commit()
    return out


@router.get("/templates/{template_id}", response_model=ChangeTemplateOut)
async def get_template_detail(
    template_id: int, admin: AdminUser, session: DbSession
) -> ChangeTemplateOut:
    _ = admin
    return template_out(await get_template(session, template_id))


@router.put("/templates/{template_id}", response_model=ChangeTemplateOut)
async def put_template(
    template_id: int,
    body: TemplateIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> ChangeTemplateOut:
    """Replace the template. Changes created from it earlier keep what they got."""
    out = template_out(
        await update_template(
            session, settings, template_id=template_id, data=body, user_id=admin.id
        )
    )
    await session.commit()
    return out


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template_route(template_id: int, admin: AdminUser, session: DbSession) -> Response:
    """Delete the template; changes created from it stay (their ``template_id`` becomes empty)."""
    _ = admin
    await delete_template(session, template_id=template_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/freeze-windows", response_model=list[FreezeWindowOut])
async def get_freeze_windows(admin: AdminUser, session: DbSession) -> list[FreezeWindowOut]:
    """All freeze windows (Sperrzeiträume), valid or not; times are UTC."""
    _ = admin
    return [freeze_out(v) for v in await list_freezes(session)]


@router.post("/freeze-windows", response_model=FreezeWindowOut, status_code=status.HTTP_201_CREATED)
async def post_freeze_window(
    body: FreezeIn, admin: AdminUser, session: DbSession
) -> FreezeWindowOut:
    out = freeze_out(await create_freeze(session, data=body, user_id=admin.id))
    await session.commit()
    return out


@router.get("/freeze-windows/{freeze_id}", response_model=FreezeWindowOut)
async def get_freeze_window(
    freeze_id: int, admin: AdminUser, session: DbSession
) -> FreezeWindowOut:
    _ = admin
    return freeze_out(await get_freeze(session, freeze_id))


@router.put("/freeze-windows/{freeze_id}", response_model=FreezeWindowOut)
async def put_freeze_window(
    freeze_id: int, body: FreezeIn, admin: AdminUser, session: DbSession
) -> FreezeWindowOut:
    """Replace the window."""
    out = freeze_out(await update_freeze(session, freeze_id=freeze_id, data=body, user_id=admin.id))
    await session.commit()
    return out


@router.delete("/freeze-windows/{freeze_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_freeze_window(freeze_id: int, admin: AdminUser, session: DbSession) -> Response:
    _ = admin
    await delete_freeze(session, freeze_id=freeze_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/cabs", response_model=list[CabOut])
async def get_cabs(admin: AdminUser, session: DbSession) -> list[CabOut]:
    """All CABs with their members, valid or not."""
    _ = admin
    return [cab_out(v) for v in await list_cabs(session, include_invalid=True)]


@router.post("/cabs", response_model=CabOut, status_code=status.HTTP_201_CREATED)
async def post_cab(body: CabIn, admin: AdminUser, session: DbSession) -> CabOut:
    """A new CAB without members; add them with ``PUT /cabs/{id}/members``."""
    out = cab_out(await create_cab(session, data=body, user_id=admin.id))
    await session.commit()
    return out


@router.put("/cabs/{cab_id}", response_model=CabOut)
async def put_cab(cab_id: int, body: CabIn, admin: AdminUser, session: DbSession) -> CabOut:
    """Replace name, rule and flags; the members stay and must fit the rule."""
    out = cab_out(await update_cab(session, cab_id=cab_id, data=body, user_id=admin.id))
    await session.commit()
    return out


@router.put("/cabs/{cab_id}/members", response_model=CabOut)
async def put_cab_members(
    cab_id: int, body: list[CabMemberIn], admin: AdminUser, session: DbSession
) -> CabOut:
    """Replace the members (at most 50). Kinds: user, group, role (ids), list (approval list id),
    customer_user (login), resolver (key); users, groups and roles must exist."""
    out = cab_out(await set_members(session, cab_id=cab_id, members=body, user_id=admin.id))
    await session.commit()
    return out


@router.delete("/cabs/{cab_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cab_route(cab_id: int, admin: AdminUser, session: DbSession) -> Response:
    """Delete the CAB; refused (409 ``cab_has_meetings``) while it has meetings."""
    _ = admin
    await delete_cab(session, cab_id=cab_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/risk-questions", response_model=list[ChangeRiskQuestionOut])
async def get_risk_questions(admin: AdminUser, session: DbSession) -> list[ChangeRiskQuestionOut]:
    """The risk questionnaire in order, valid or not."""
    _ = admin
    return await list_questions(session, include_invalid=True)


@router.post(
    "/risk-questions", response_model=ChangeRiskQuestionOut, status_code=status.HTTP_201_CREATED
)
async def post_risk_question(
    body: ChangeRiskQuestionIn, admin: AdminUser, session: DbSession
) -> ChangeRiskQuestionOut:
    out = await create_question(session, data=body, user_id=admin.id)
    await session.commit()
    return out


@router.put("/risk-questions/order", response_model=list[ChangeRiskQuestionOut])
async def put_risk_question_order(
    body: ChangeRiskQuestionOrderIn, admin: AdminUser, session: DbSession
) -> list[ChangeRiskQuestionOut]:
    """Put the questions in the given order (every question exactly once)."""
    _ = admin
    out = await reorder_questions(session, order=body.order)
    await session.commit()
    return out


@router.post(
    "/risk-questions/examples",
    response_model=ChangeRiskExamplesOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_risk_question_examples(
    admin: AdminUser, session: DbSession
) -> ChangeRiskExamplesOut:
    """Insert the four example questions whose keys do not exist yet."""
    created = await insert_examples(session, user_id=admin.id)
    await session.commit()
    return ChangeRiskExamplesOut(created=created)


@router.put("/risk-questions/{question_id:int}", response_model=ChangeRiskQuestionOut)
async def put_risk_question(
    question_id: int, body: ChangeRiskQuestionIn, admin: AdminUser, session: DbSession
) -> ChangeRiskQuestionOut:
    """Replace the question; its key stays."""
    out = await update_question(session, question_id=question_id, data=body, user_id=admin.id)
    await session.commit()
    return out


@router.delete("/risk-questions/{question_id:int}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_risk_question(question_id: int, admin: AdminUser, session: DbSession) -> Response:
    """Delete the question and the answers given to it (mark it invalid to keep them)."""
    _ = admin
    await delete_question(session, question_id=question_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/legacy-archive", response_model=LegacyArchiveStatusOut)
async def get_legacy_archive(admin: AdminUser, session: DbSession) -> LegacyArchiveStatusOut:
    """How many Znuny changes are archived, and whether the archive job can start now."""
    _ = admin
    return legacy_archive_status_out(await archive_status(session))


@router.post("/legacy-archive", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def post_legacy_archive(
    admin: AdminUser, session: DbSession, body: LegacyArchiveStartIn | None = None
) -> JobAccepted:
    """Archive the Znuny changes again in the background (Mode B only; 409
    ``legacy_archive_mode_b_only`` / ``legacy_archive_no_tables`` otherwise). An active archive
    job is answered instead of a second one. Poll ``GET /api/v1/jobs/{job_id}``."""
    blocked = await archive_blocker(session)
    if blocked is not None:
        raise ChangeError(blocked.code, 409, message=blocked.message)
    job = await enqueue_job(
        session,
        kind=ARCHIVE_JOB_KIND,
        payload=LegacyArchivePayload(
            user_id=admin.id, dry_run=False if body is None else body.dry_run
        ),
        created_by=admin.id,
        dedupe_key=ARCHIVE_DEDUPE_KEY,
    )
    out = JobAccepted(job_id=int(job.id), status="running" if job.status == "running" else "queued")
    await session.commit()
    return out
