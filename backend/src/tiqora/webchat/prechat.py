"""Pre-chat form validation and customer resolution for web chats.

``validate_prechat`` checks the visitor's answers against the config's ``PrechatField`` list
(pure). Addresses use the one strict rule of :mod:`tiqora.webchat.text` (R-INPUT: dot-atom
local part, IDNA 2008 domain, lower-cased domain) — ``EMAIL_RE`` is re-exported from there,
never redefined. ``resolve_chat_customer`` maps an address to a Znuny customer like the
email pipeline does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.webchat.models import PrechatField
from tiqora.webchat.text import (
    CONTROL_CHARS,
    EMAIL_RE,
    MAX_EMAIL_LENGTH,
    MessageRejected,
    normalize_email,
    sanitize_message,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "CHECKBOX_NO",
    "CHECKBOX_YES",
    "EMAIL_RE",
    "MAX_TEXT_LENGTH",
    "MAX_TEXTAREA_LENGTH",
    "PrechatInvalid",
    "PrechatResult",
    "resolve_chat_customer",
    "validate_prechat",
]

MAX_TEXT_LENGTH: Final = 200
MAX_TEXTAREA_LENGTH: Final = 2000
CHECKBOX_YES: Final = "Ja"
CHECKBOX_NO: Final = "Nein"
#: Key of the address when ``require_email`` is set and no field maps to ``email``.
EMAIL_KEY: Final = "email"
#: Values beyond this are rejected as too long before any cleaning work is done.
_HARD_CAP: Final = 20_000


@dataclass(frozen=True, slots=True)
class PrechatResult:
    name: str | None
    email: str | None
    df_values: dict[str, list[str]]
    extra_lines: list[tuple[str, str]]  # (label, value) for the article block


class PrechatInvalid(Exception):
    """``errors``: field key → ``required`` | ``email`` | ``option`` | ``too_long`` | ``type``."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("invalid_prechat")
        self.errors = errors


@dataclass(slots=True)
class _Collector:
    errors: dict[str, str] = field(default_factory=dict)
    name: str | None = None
    email: str | None = None
    df_values: dict[str, list[str]] = field(default_factory=dict)
    extra_lines: list[tuple[str, str]] = field(default_factory=list)


def _clean_single_line(raw: str) -> str:
    """Controls/bidi → space, whitespace collapsed (names, select values, addresses)."""
    return " ".join(CONTROL_CHARS.sub(" ", raw).split())


def _clean_multiline(raw: str) -> str:
    """Same rules as a chat message (keeps \\n and \\t); empty → ``""``."""
    try:
        return sanitize_message(raw, max_length=_HARD_CAP)
    except MessageRejected:
        return ""


def _check_email(raw: str) -> tuple[str | None, str | None]:
    """``(normalised address, None)`` or ``(None, error code)``; empty → ``(None, None)``."""
    value = _clean_single_line(raw)
    if not value:
        return None, None
    if len(value) > MAX_EMAIL_LENGTH:
        return None, "too_long"
    address = normalize_email(value)
    if address is None:
        return None, "email"
    return address, None


def _field_value(
    f: PrechatField, raw: str | bool | None, out: _Collector, *, required: bool
) -> None:
    if f.kind == "checkbox":
        if raw is None:
            if required:
                out.errors[f.key] = "required"
            return
        if not isinstance(raw, bool):
            out.errors[f.key] = "type"
            return
        if required and raw is not True:
            out.errors[f.key] = "required"
            return
        _assign(f, "1" if raw else "0", CHECKBOX_YES if raw else CHECKBOX_NO, out)
        return

    if raw is not None and not isinstance(raw, str):
        out.errors[f.key] = "type"
        return
    raw_text = raw or ""
    if len(raw_text) > _HARD_CAP:
        out.errors[f.key] = "too_long"
        return

    if f.kind == "email":
        address, error = _check_email(raw_text)
        if error is not None:
            out.errors[f.key] = error
        elif address is None:
            if required:
                out.errors[f.key] = "required"
        else:
            _assign(f, address, address, out)
        return

    if f.kind == "select":
        value = _clean_single_line(raw_text)
        if not value:
            if required:
                out.errors[f.key] = "required"
            return
        option = next((o for o in f.options if o.value == value), None)
        if option is None:
            out.errors[f.key] = "option"
            return
        _assign(f, option.value, option.label, out)
        return

    if f.kind == "textarea":
        value, limit = _clean_multiline(raw_text), MAX_TEXTAREA_LENGTH
    else:
        value, limit = _clean_single_line(raw_text), MAX_TEXT_LENGTH
    if not value:
        if required:
            out.errors[f.key] = "required"
        return
    if len(value) > limit:
        out.errors[f.key] = "too_long"
        return
    _assign(f, value, value, out)


def _assign(f: PrechatField, value: str, display: str, out: _Collector) -> None:
    if f.maps_to == "name":
        out.name = value
    elif f.maps_to == "email":
        out.email = value
    elif f.maps_to == "dynamic_field" and f.dynamic_field:
        out.df_values[f.dynamic_field] = [value]
    else:
        out.extra_lines.append((f.label, display))


def validate_prechat(
    fields: Sequence[PrechatField],
    values: Mapping[str, str | bool],
    *,
    require_email: bool = False,
    identity_fields: bool = False,
) -> PrechatResult:
    """Validate the visitor's pre-chat answers; every error is collected, then raised.

    Unknown keys are ignored. ``text`` ≤ 200, ``textarea`` ≤ 2000, ``email`` ≤ 250 and the
    strict address rule (stored normalised); a ``select`` value must be one of its option
    values; a ``checkbox`` must be a JSON boolean (a required one must be ``true``). Strings
    lose control/bidi characters first; an empty optional field is simply absent.
    ``require_email`` (offline form): an address is required even when no field maps to
    ``email`` — then it is read from ``values["email"]``.
    ``identity_fields`` (a verified identity supplies name and address): fields mapped to
    ``name`` / ``email`` are neither required nor read, and ``require_email`` is satisfied.
    """
    out = _Collector()
    email_field = next((f for f in fields if f.maps_to == "email"), None)
    for f in fields:
        if identity_fields and f.maps_to in ("name", "email"):
            continue
        required = f.required or (require_email and f is email_field)
        _field_value(f, values.get(f.key), out, required=required)

    if require_email and email_field is None and not identity_fields:
        raw = values.get(EMAIL_KEY)
        if raw is not None and not isinstance(raw, str):
            out.errors[EMAIL_KEY] = "type"
        else:
            address, error = _check_email(raw or "")
            if error is not None:
                out.errors[EMAIL_KEY] = error
            elif address is None:
                out.errors[EMAIL_KEY] = "required"
            else:
                out.email = address

    if out.errors:
        raise PrechatInvalid(out.errors)
    return PrechatResult(
        name=out.name, email=out.email, df_values=out.df_values, extra_lines=out.extra_lines
    )


async def resolve_chat_customer(
    db: AsyncSession, *, email: str | None, default_customer_user: str | None
) -> tuple[str | None, str | None]:
    """``(customer_id, customer_user_id)`` for a new chat ticket.

    An address is matched against ``customer_user`` login/email case-insensitively
    (``channels/email/pipeline._resolve_customer``'s rule) → ``(customer_id, login)``,
    otherwise ``(email, email)``. Without an address the config's ``default_customer_user``
    is used (its ``customer_id`` looked up; a login that no longer exists counts as none),
    else ``(None, None)``. Znuny tables only — no JOIN with ``tiqora_*``.
    """
    if email:
        row = (
            await db.execute(
                text(
                    "SELECT login, customer_id FROM customer_user"
                    " WHERE LOWER(login) = LOWER(:s) OR LOWER(email) = LOWER(:s)"
                    " ORDER BY id LIMIT 1"
                ),
                {"s": email},
            )
        ).first()
        if row is not None:
            return (str(row[1]) if row[1] else email), str(row[0])
        return email, email
    if default_customer_user:
        row = (
            await db.execute(
                text("SELECT customer_id FROM customer_user WHERE login = :l LIMIT 1"),
                {"l": default_customer_user},
            )
        ).first()
        if row is None:
            # Deleted since the admin saved the config: no dangling customer reference.
            logger.warning("webchat_default_customer_user_missing")
            return None, None
        return (str(row[0]) if row[0] else None), default_customer_user
    return None, None
