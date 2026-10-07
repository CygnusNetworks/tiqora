"""Customer avatars and company logos: read proxy + agent uploads.

``GET /avatars/email/{sha256}`` and ``GET /avatars/domain/{domain}`` serve a
picture from Tiqora's own origin (the page CSP stays ``img-src 'self'``); the
key is the SHA-256 of the address, so no plaintext e-mail address ever lands in
a URL or an access log. Uploads (``customer_edit``) live under ``/customers``.
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from tiqora.api.attachment_response import safe_attachment_response
from tiqora.api.deps import CurrentUser, DbSession, get_redis
from tiqora.api.uploads import read_upload_within_limit
from tiqora.api.v1.feature_deps import CustomerEditUser
from tiqora.avatars.normalize import Fit, InvalidImageError
from tiqora.avatars.service import (
    DOMAIN,
    EMAIL,
    MAX_UPLOAD_BYTES,
    SHA256_PATTERN,
    AvatarImage,
    AvatarService,
    domain_from_url,
    email_key,
    normalize_domain,
    prepare_upload,
    upload_version,
)
from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.domain.customer_fill import is_blank

router = APIRouter(prefix="/avatars", tags=["avatars"])
customer_router = APIRouter(prefix="/customers", tags=["customers"])

HIT_CACHE_CONTROL = "private, max-age=3600"
MISS_CACHE_CONTROL = "private, max-age=600"

_IMAGE_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"content": {"image/png": {}, "image/webp": {}}, "description": "The picture"},
    404: {"description": "No picture for this key"},
}


async def get_avatar_service(
    session: DbSession,
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
) -> AvatarService:
    return AvatarService(session, redis_client)


AvatarServiceDep = Annotated[AvatarService, Depends(get_avatar_service)]


def _image_response(image: AvatarImage | None) -> Response:
    if image is None:
        return Response(
            status_code=status.HTTP_404_NOT_FOUND,
            headers={
                "Content-Security-Policy": "sandbox",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": MISS_CACHE_CONTROL,
            },
        )
    response = safe_attachment_response(image.data, image.content_type, None, "inline")
    response.headers["Cache-Control"] = HIT_CACHE_CONTROL
    return response


@router.get("/email/{sha256}", response_class=Response, responses=_IMAGE_RESPONSES)
async def person_avatar(
    sha256: Annotated[
        str, Path(pattern=SHA256_PATTERN, description="SHA-256 hex of the lowercased address")
    ],
    user: CurrentUser,
    service: AvatarServiceDep,
    v: Annotated[str | None, Query(description="Cache buster; ignored")] = None,
) -> Response:
    """Uploaded photo, else Gravatar (when enabled), else 404."""
    _ = user, v
    return _image_response(await service.person(sha256))


@router.get("/domain/{domain}", response_class=Response, responses=_IMAGE_RESPONSES)
async def domain_avatar(
    domain: Annotated[
        str, Path(max_length=253, description="Domain without scheme, e.g. example.com")
    ],
    user: CurrentUser,
    service: AvatarServiceDep,
    v: Annotated[str | None, Query(description="Cache buster; ignored")] = None,
) -> Response:
    """Uploaded logo, else the website's icon (when enabled, not for freemail), else 404."""
    _ = user, v
    normalized = normalize_domain(domain)
    if normalized is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid domain"
        )
    return _image_response(await service.company(normalized))


class AvatarUploadOut(BaseModel):
    """``version`` changes with every upload; append it as ``?v=``."""

    version: str


async def _read_picture(file: UploadFile, *, fit: Fit) -> bytes:
    content = await read_upload_within_limit(file, MAX_UPLOAD_BYTES)
    try:
        return prepare_upload(content, fit=fit)
    except InvalidImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


async def _customer(session: DbSession, login: str) -> CustomerUser:
    cu = (
        await session.execute(select(CustomerUser).where(CustomerUser.login == login))
    ).scalar_one_or_none()
    if cu is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return cu


async def _company(session: DbSession, customer_id: str) -> CustomerCompany:
    company = await session.get(CustomerCompany, customer_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return company


# `:path`: company ids may contain "/" (same as /companies/{customer_id}/vcards).
@customer_router.put("/companies/{customer_id:path}/logo", response_model=AvatarUploadOut)
async def upload_company_logo(
    customer_id: str,
    user: CustomerEditUser,
    session: DbSession,
    service: AvatarServiceDep,
    file: UploadFile = File(...),  # noqa: B008
) -> AvatarUploadOut:
    """Store a logo for the company's website domain (``customer_company.url``)."""
    company = await _company(session, customer_id)
    domain = domain_from_url(company.url)
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="company has no website URL",
        )
    png = await _read_picture(file, fit="contain")
    updated_at = await service.save_upload(
        DOMAIN, domain, png, owner_ref=company.customer_id, user_id=user.id
    )
    return AvatarUploadOut(version=upload_version(updated_at))


@customer_router.delete(
    "/companies/{customer_id:path}/logo", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_company_logo(
    customer_id: str,
    user: CustomerEditUser,
    session: DbSession,
    service: AvatarServiceDep,
) -> Response:
    _ = user
    company = await _company(session, customer_id)
    domain = domain_from_url(company.url)
    if domain is not None:
        await service.delete_upload(DOMAIN, domain)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@customer_router.put("/{login}/avatar", response_model=AvatarUploadOut)
async def upload_customer_avatar(
    login: str,
    user: CustomerEditUser,
    session: DbSession,
    service: AvatarServiceDep,
    file: UploadFile = File(...),  # noqa: B008
) -> AvatarUploadOut:
    """Store a photo for the customer's e-mail address (PNG/JPEG/WebP, max 2 MiB)."""
    cu = await _customer(session, login)
    if is_blank(cu.email):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="customer has no e-mail address",
        )
    png = await _read_picture(file, fit="cover")
    updated_at = await service.save_upload(
        EMAIL, email_key(cu.email), png, owner_ref=cu.login, user_id=user.id
    )
    return AvatarUploadOut(version=upload_version(updated_at))


@customer_router.delete("/{login}/avatar", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer_avatar(
    login: str,
    user: CustomerEditUser,
    session: DbSession,
    service: AvatarServiceDep,
) -> Response:
    _ = user
    cu = await _customer(session, login)
    if not is_blank(cu.email):
        await service.delete_upload(EMAIL, email_key(cu.email))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
