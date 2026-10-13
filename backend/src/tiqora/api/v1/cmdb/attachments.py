"""Agent API: CI attachments (staged upload, download, delete)."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Response, UploadFile, status

from tiqora.api.uploads import read_upload_within_limit
from tiqora.api.v1.cmdb.deps import Ctx, cmdb_http_error
from tiqora.cmdb.attachments import AttachmentService
from tiqora.cmdb.errors import CmdbError
from tiqora.cmdb.schemas import CmdbAttachmentOut
from tiqora.domain.attachments_in import MAX_ATTACHMENT_BYTES

router = APIRouter()


def content_disposition(filename: str) -> str:
    """``attachment`` with an ASCII fallback and the RFC 5987 ``filename*``."""
    ascii_name = filename.encode("ascii", "ignore").decode("ascii")
    ascii_name = "".join(c for c in ascii_name if c not in '"\\;' and c.isprintable()).strip()
    return f"attachment; filename=\"{ascii_name or 'datei'}\"; filename*=UTF-8''{quote(filename)}"


@router.post("/attachments", response_model=CmdbAttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    ctx: Ctx,
    file: Annotated[UploadFile, File()],
    ci_id: Annotated[int | None, Form(ge=1)] = None,
) -> CmdbAttachmentOut:
    """Without ``ci_id`` the file is staged (visible to the uploader until it is bound)."""
    data = await read_upload_within_limit(file, MAX_ATTACHMENT_BYTES)
    try:
        rec = await AttachmentService(ctx.session).upload(
            ctx.access,
            ci_id=ci_id,
            filename=file.filename,
            content_type=file.content_type,
            data=data,
        )
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    await ctx.session.commit()
    return CmdbAttachmentOut.model_validate(rec, from_attributes=True)


@router.get("/cis/{ci_id}/attachments", response_model=list[CmdbAttachmentOut])
async def list_ci_attachments(ci_id: int, ctx: Ctx) -> list[CmdbAttachmentOut]:
    try:
        recs = await AttachmentService(ctx.session).list_for_ci(ctx.access, ci_id)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    return [CmdbAttachmentOut.model_validate(r, from_attributes=True) for r in recs]


@router.get("/attachments/{attachment_id}/meta", response_model=CmdbAttachmentOut)
async def attachment_meta(attachment_id: int, ctx: Ctx) -> CmdbAttachmentOut:
    try:
        rec = await AttachmentService(ctx.session).get(ctx.access, attachment_id)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    return CmdbAttachmentOut.model_validate(rec, from_attributes=True)


@router.get("/attachments/{attachment_id}")
async def download_attachment(attachment_id: int, ctx: Ctx) -> Response:
    svc = AttachmentService(ctx.session)
    try:
        rec = await svc.get(ctx.access, attachment_id)
        data = await svc.content(ctx.access, attachment_id)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    return Response(
        content=data,
        media_type=rec.content_type,
        headers={
            "Content-Disposition": content_disposition(rec.filename),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(attachment_id: int, ctx: Ctx) -> Response:
    try:
        await AttachmentService(ctx.session).delete(ctx.access, attachment_id)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
