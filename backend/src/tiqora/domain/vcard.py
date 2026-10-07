"""vCard 3.0 (RFC 2426) export of customer users, and .vcf import parsing."""

from __future__ import annotations

import base64
import binascii
import quopri
import re
from dataclasses import dataclass, field

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


# ---------------------------------------------------------------------------
# Import (vCard 2.1 / 3.0 / 4.0 — Apple Contacts, Outlook, Google, Thunderbird)
# ---------------------------------------------------------------------------

#: Largest .vcf accepted for import (Apple Contacts with photos: ~100 KiB/card).
MAX_VCARD_IMPORT_BYTES = 5 * 1024 * 1024
#: Cards read from one file; the rest is reported as truncated.
MAX_VCARD_IMPORT_CARDS = 500

_GROUP_PREFIX = re.compile(r"^[A-Za-z0-9-]+\.")
_TEXT_ESCAPE = re.compile(r"\\(.)")
_TEXT_ESCAPES = {"n": "\n", "N": "\n"}


@dataclass
class ParsedVCard:
    """One contact read from a .vcf. Text values are unescaped and trimmed."""

    index: int
    display_name: str = ""
    first_name: str | None = None
    last_name: str | None = None
    emails: list[str] = field(default_factory=list)
    phone: str | None = None
    mobile: str | None = None
    org: str | None = None
    title: str | None = None
    photo: bytes | None = None


@dataclass
class _Property:
    name: str
    params: dict[str, list[str]]
    value: str

    def types(self) -> set[str]:
        return {t.lower() for t in self.params.get("TYPE", [])}

    def encoding(self) -> str:
        values = self.params.get("ENCODING", [])
        return values[0].upper() if values else ""


def _split_unquoted(text: str, sep: str, maxsplit: int = -1) -> list[str]:
    """Split on *sep* outside double quotes."""
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    for ch in text:
        if ch == '"':
            quoted = not quoted
            current.append(ch)
        elif ch == sep and not quoted and (maxsplit < 0 or len(parts) < maxsplit):
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _split_escaped(value: str, sep: str = ";") -> list[str]:
    """Split a structured value (N, ORG) on separators not escaped by ``\\``."""
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in value:
        if escaped:
            current.append("\\" + ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == sep:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _unescape(value: str) -> str:
    return _TEXT_ESCAPE.sub(lambda m: _TEXT_ESCAPES.get(m.group(1), m.group(1)), value).strip()


def _parse_property(line: str) -> _Property | None:
    head_value = _split_unquoted(line, ":", maxsplit=1)
    if len(head_value) != 2:
        return None
    head, value = head_value
    pieces = _split_unquoted(head, ";")
    name = _GROUP_PREFIX.sub("", pieces[0].strip()).upper()
    params: dict[str, list[str]] = {}
    for piece in pieces[1:]:
        key, eq, raw = piece.partition("=")
        if not eq:
            # vCard 2.1 bare parameters: TEL;CELL / PHOTO;JPEG;BASE64
            bare = key.strip().upper()
            if bare in ("BASE64", "B", "QUOTED-PRINTABLE"):
                params.setdefault("ENCODING", []).append(bare)
            else:
                params.setdefault("TYPE", []).append(bare)
            continue
        # TYPE=work,voice / TYPE="voice,cell" (vCard 4.0) / repeated TYPE=.
        values = [v.strip() for v in raw.strip().strip('"').split(",")]
        params.setdefault(key.strip().upper(), []).extend(v for v in values if v)
    return _Property(name=name, params=params, value=value)


def _is_qp_soft_break(line: str) -> bool:
    if not line.endswith("="):
        return False
    head = _split_unquoted(line, ":", maxsplit=1)[0].upper()
    return "QUOTED-PRINTABLE" in head


def _logical_lines(text: str) -> list[str]:
    """Unfold RFC 2425/6350 continuation lines and vCard 2.1 QP soft breaks."""
    lines: list[str] = []
    for raw in re.split(r"\r\n|\r|\n", text):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif lines and _is_qp_soft_break(lines[-1]):
            lines[-1] = lines[-1][:-1] + raw
        else:
            lines.append(raw)
    return lines


def _decoded_text(prop: _Property) -> str:
    if prop.encoding() != "QUOTED-PRINTABLE":
        return prop.value
    charset = (prop.params.get("CHARSET") or ["utf-8"])[0]
    raw = quopri.decodestring(prop.value.encode("utf-8"))
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _photo_bytes(prop: _Property) -> bytes | None:
    """Inline photo only — a PHOTO URL is never fetched."""
    value = prop.value.strip()
    try:
        if prop.encoding() in ("B", "BASE64"):
            return base64.b64decode("".join(value.split())) or None
        if value.lower().startswith("data:"):
            meta, comma, payload = value.partition(",")
            if comma and ";base64" in meta.lower():
                return base64.b64decode("".join(payload.split())) or None
    except (ValueError, binascii.Error):
        return None
    return None


def _apply(card: ParsedVCard, prop: _Property) -> None:
    name = prop.name
    if name == "PHOTO":
        if card.photo is None:
            card.photo = _photo_bytes(prop)
        return
    value = _decoded_text(prop)
    if name == "FN":
        card.display_name = _unescape(value)
    elif name == "N":
        parts = [_unescape(p) for p in _split_escaped(value)] + ["", ""]
        card.last_name = parts[0] or None
        card.first_name = parts[1] or None
    elif name == "EMAIL":
        email = _unescape(value)
        if email.lower().startswith("mailto:"):
            email = email[7:].strip()
        if "@" in email and email.lower() not in {e.lower() for e in card.emails}:
            card.emails.append(email)
    elif name == "TEL":
        number = _unescape(value)
        if number.lower().startswith("tel:"):
            number = number[4:].strip()
        types = prop.types()
        if not number or "fax" in types:
            return
        if "cell" in types:
            card.mobile = card.mobile or number
        else:
            card.phone = card.phone or number
    elif name == "ORG":
        card.org = _unescape(_split_escaped(value)[0]) or None
    elif name == "TITLE":
        card.title = _unescape(value) or None


def decode_vcard_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_vcards(
    text: str | bytes, *, max_cards: int = MAX_VCARD_IMPORT_CARDS
) -> tuple[list[ParsedVCard], bool]:
    """Contacts of a .vcf (any number of cards); ``True`` when cut at *max_cards*."""
    if isinstance(text, bytes):
        text = decode_vcard_bytes(text)
    cards: list[ParsedVCard] = []
    current: ParsedVCard | None = None
    depth = 0
    for line in _logical_lines(text):
        if not line.strip():
            continue
        prop = _parse_property(line)
        if prop is None:
            continue
        if prop.name == "BEGIN" and prop.value.strip().upper() == "VCARD":
            depth += 1
            if depth == 1:
                if len(cards) >= max_cards:
                    return cards, True
                current = ParsedVCard(index=len(cards))
            continue
        if prop.name == "END" and prop.value.strip().upper() == "VCARD":
            depth = max(depth - 1, 0)
            if depth == 0 and current is not None:
                if not current.display_name:
                    full = " ".join(p for p in (current.first_name, current.last_name) if p)
                    current.display_name = full or current.org or next(iter(current.emails), "")
                cards.append(current)
                current = None
            continue
        if current is not None and depth == 1:
            _apply(current, prop)
    return cards, False
