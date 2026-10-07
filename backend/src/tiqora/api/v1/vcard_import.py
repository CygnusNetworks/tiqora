"""vCard import (Apple Contacts drag & drop, any .vcf): preview, then apply.

The preview matches every card to a customer user through any of its e-mail
addresses and reports what an import would change; it writes nothing. Apply
re-reads the same file and, per selected card, fills blank fields (same
fill-only rule as ``PATCH /customers/{login}``) and/or stores the card photo
as the customer's avatar. Cards without a match are never created here — the
frontend pre-fills the regular create dialog for them.
"""

from __future__ import annotations

import base64
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import func, select

from tiqora.api.deps import DbSession
from tiqora.api.uploads import read_upload_within_limit
from tiqora.api.v1.admin.common import (
    CUSTOMER_USER_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.avatars import AvatarServiceDep
from tiqora.api.v1.feature_deps import CustomerEditUser
from tiqora.avatars.normalize import InvalidImageError, normalize_image, sniff_upload_type
from tiqora.avatars.service import EMAIL, MAX_UPLOAD_BYTES, email_key, prepare_upload
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.tiqora.models import TiqoraAvatar
from tiqora.domain.customer_fill import (
    FILL_MAX_LENGTHS,
    CustomerEmailTakenError,
    fill_blank_fields,
    fillable_fields,
    is_blank,
)
from tiqora.domain.vcard import MAX_VCARD_IMPORT_BYTES, ParsedVCard, parse_vcards

router = APIRouter(prefix="/customers/vcard-import", tags=["customers"])

THUMBNAIL_PX = 64


class VCardImportCard(BaseModel):
    index: int
    display_name: str
    first_name: str | None
    last_name: str | None
    emails: list[str]
    phone: str | None
    mobile: str | None
    org: str | None
    title: str | None
    has_photo: bool
    #: ``data:image/png;base64,...`` 64 px thumbnail of the card photo.
    photo_preview: str | None
    #: Customer user matched through any of the card's addresses.
    match_login: str | None
    match_name: str | None
    #: Blank fields of the match the card would fill.
    fill_fields: list[str]
    has_existing_avatar: bool


class VCardImportPreview(BaseModel):
    cards: list[VCardImportCard]
    #: More cards than the import limit; only the first ones are listed.
    truncated: bool


class VCardImportSelection(BaseModel):
    index: int
    login: str = Field(..., min_length=1, max_length=200)
    photo: bool = False
    fields: bool = False


class VCardImportApplied(BaseModel):
    login: str
    photo: bool
    filled: list[str]


class VCardImportApplyOut(BaseModel):
    applied: list[VCardImportApplied]


_SELECTIONS = TypeAdapter(list[VCardImportSelection])


async def _read_cards(file: UploadFile) -> tuple[list[ParsedVCard], bool]:
    content = await read_upload_within_limit(file, MAX_VCARD_IMPORT_BYTES)
    cards, truncated = parse_vcards(content)
    if not cards:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="file contains no vCard",
        )
    return cards, truncated


def _card_values(card: ParsedVCard) -> dict[str, str | None]:
    """The card as fill-field values; values too long for the column are dropped."""
    values: dict[str, str | None] = {
        "first_name": card.first_name,
        "last_name": card.last_name,
        "email": card.emails[0] if card.emails else None,
        "phone": card.phone,
        "mobile": card.mobile,
    }
    return {
        name: value
        for name, value in values.items()
        if value is not None and len(value.strip()) <= FILL_MAX_LENGTHS[name]
    }


def _usable_photo(card: ParsedVCard) -> bytes | None:
    photo = card.photo
    if photo is None or len(photo) > MAX_UPLOAD_BYTES or sniff_upload_type(photo) is None:
        return None
    return photo


def _thumbnail(photo: bytes | None) -> str | None:
    if photo is None:
        return None
    try:
        png = normalize_image(photo, fit="cover", size=THUMBNAIL_PX)
    except InvalidImageError:
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


async def _match_customers(session: DbSession, cards: list[ParsedVCard]) -> dict[int, CustomerUser]:
    """Card index → valid customer user owning one of the card's addresses."""
    addresses = {email.lower() for card in cards for email in card.emails}
    if not addresses:
        return {}
    rows = (
        (
            await session.execute(
                select(CustomerUser)
                .where(
                    func.lower(CustomerUser.email).in_(sorted(addresses)),
                    CustomerUser.valid_id == 1,
                )
                .order_by(CustomerUser.login)
            )
        )
        .scalars()
        .all()
    )
    by_address: dict[str, CustomerUser] = {}
    for cu in rows:
        by_address.setdefault((cu.email or "").lower(), cu)
    matches: dict[int, CustomerUser] = {}
    for card in cards:
        for email in card.emails:
            match = by_address.get(email.lower())
            if match is not None:
                matches[card.index] = match
                break
    return matches


def _display(cu: CustomerUser) -> str:
    name = " ".join(p for p in ((cu.first_name or "").strip(), (cu.last_name or "").strip()) if p)
    return name or cu.login


@router.post("/preview", response_model=VCardImportPreview)
async def preview_vcard_import(
    user: CustomerEditUser,
    session: DbSession,
    file: UploadFile = File(...),  # noqa: B008
) -> VCardImportPreview:
    """What importing this .vcf would do, per card. Writes nothing."""
    _ = user
    cards, truncated = await _read_cards(file)
    matches = await _match_customers(session, cards)
    keys = {email_key(cu.email) for cu in matches.values() if not is_blank(cu.email)}
    with_avatar: set[str] = set()
    if keys:
        with_avatar = set(
            (
                await session.execute(
                    select(TiqoraAvatar.subject_key).where(
                        TiqoraAvatar.kind == EMAIL, TiqoraAvatar.subject_key.in_(sorted(keys))
                    )
                )
            )
            .scalars()
            .all()
        )
    out: list[VCardImportCard] = []
    for card in cards:
        cu = matches.get(card.index)
        preview = _thumbnail(_usable_photo(card))
        out.append(
            VCardImportCard(
                index=card.index,
                display_name=card.display_name,
                first_name=card.first_name,
                last_name=card.last_name,
                emails=card.emails,
                phone=card.phone,
                mobile=card.mobile,
                org=card.org,
                title=card.title,
                has_photo=preview is not None,
                photo_preview=preview,
                match_login=cu.login if cu is not None else None,
                match_name=_display(cu) if cu is not None else None,
                fill_fields=fillable_fields(cu, _card_values(card)) if cu is not None else [],
                has_existing_avatar=(
                    cu is not None and not is_blank(cu.email) and email_key(cu.email) in with_avatar
                ),
            )
        )
    return VCardImportPreview(cards=out, truncated=truncated)


@router.post("/apply", response_model=VCardImportApplyOut)
async def apply_vcard_import(
    user: CustomerEditUser,
    session: DbSession,
    service: AvatarServiceDep,
    selections: Annotated[str, Form(description='JSON: [{"index", "login", "photo", "fields"}]')],
    file: UploadFile = File(...),  # noqa: B008
) -> VCardImportApplyOut:
    """Fill blank fields and/or store photos for the selected cards."""
    try:
        chosen = _SELECTIONS.validate_json(selections)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid selections"
        ) from exc
    cards, _ = await _read_cards(file)
    by_index = {card.index: card for card in cards}
    applied: list[VCardImportApplied] = []
    photo_keys: list[str] = []
    any_filled = False
    for selection in chosen:
        card = by_index.get(selection.index)
        if card is None:
            continue
        cu = (
            await session.execute(select(CustomerUser).where(CustomerUser.login == selection.login))
        ).scalar_one_or_none()
        if cu is None:
            continue
        filled: list[str] = []
        if selection.fields:
            values = _card_values(card)
            try:
                filled = await fill_blank_fields(session, cu, values)
            except CustomerEmailTakenError:
                # Fill the rest; the address already belongs to someone else.
                values.pop("email", None)
                filled = await fill_blank_fields(session, cu, values)
            if filled:
                cu.change_time = now()
                cu.change_by = user.id
                any_filled = True
        photo_stored = False
        photo = _usable_photo(card) if selection.photo else None
        if photo is not None and not is_blank(cu.email):
            try:
                png = prepare_upload(photo, fit="cover")
            except InvalidImageError:
                png = None
            if png is not None:
                key = email_key(cu.email)
                await service.stage_upload(EMAIL, key, png, owner_ref=cu.login, user_id=user.id)
                photo_keys.append(key)
                photo_stored = True
        applied.append(VCardImportApplied(login=cu.login, photo=photo_stored, filled=filled))
    if any_filled:
        await invalidate_znuny_cache_types(session, CUSTOMER_USER_CACHE_TYPES)
    await session.commit()
    for key in photo_keys:
        await service.forget(EMAIL, key)
    return VCardImportApplyOut(applied=applied)
