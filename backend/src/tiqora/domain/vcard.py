"""vCard 3.0 (RFC 2426) export of customer users."""

from __future__ import annotations

import re

import phonenumbers
from phonenumbers import PhoneNumberType

from tiqora.db.legacy.customer import CustomerUser

_MAX_OCTETS = 75
_FILENAME_STRIP = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _esc(value: str | None) -> str:
    text = (value or "").strip()
    return (
        text.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _fold(line: str) -> str:
    """Fold to <= 75 octets per line (RFC 2425), never splitting a UTF-8 char."""
    out: list[str] = []
    current = ""
    size = 0
    limit = _MAX_OCTETS
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            out.append(current)
            current, size, limit = " ", 1, _MAX_OCTETS
        current += ch
        size += n
    out.append(current)
    return "\r\n".join(out)


def _tel(raw: str | None, kind: str, region: str) -> tuple[str, str] | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        parsed = phonenumbers.parse(value, region)
    except phonenumbers.NumberParseException:
        return kind, value
    if not phonenumbers.is_valid_number(parsed):
        return kind, value
    if kind == "WORK" and phonenumbers.number_type(parsed) == PhoneNumberType.MOBILE:
        kind = "CELL"
    return kind, phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def build_vcard(cu: CustomerUser, company_name: str | None, region: str = "DE") -> str:
    first = (cu.first_name or "").strip()
    last = (cu.last_name or "").strip()
    title = (cu.title or "").strip()
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{_esc(last)};{_esc(first)};;{_esc(title)};",
        f"FN:{_esc(' '.join(p for p in (first, last) if p) or cu.login)}",
    ]
    if company_name and company_name.strip():
        lines.append(f"ORG:{_esc(company_name)}")
    if (cu.email or "").strip():
        lines.append(f"EMAIL;TYPE=INTERNET:{_esc(cu.email)}")
    for raw, kind in ((cu.phone, "WORK"), (cu.mobile, "CELL"), (cu.fax, "FAX")):
        tel = _tel(raw, kind, region)
        if tel is not None:
            lines.append(f"TEL;TYPE={tel[0]}:{_esc(tel[1])}")
    if any((v or "").strip() for v in (cu.street, cu.city, cu.zip, cu.country)):
        lines.append(
            f"ADR;TYPE=WORK:;;{_esc(cu.street)};{_esc(cu.city)};;{_esc(cu.zip)};{_esc(cu.country)}"
        )
    lines.append(f"UID:tiqora-customer-{_esc(cu.login)}")
    if cu.change_time is not None:
        lines.append(f"REV:{cu.change_time:%Y%m%dT%H%M%SZ}")
    lines.append("END:VCARD")
    return "".join(_fold(line) + "\r\n" for line in lines)


def vcard_filename(text: str) -> str:
    cleaned = _FILENAME_STRIP.sub("", text or "").strip()
    return f"{cleaned or 'kontakt'}.vcf"
