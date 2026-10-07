"""parse_vcards: Apple Contacts 3.0, vCard 4.0, 2.1 quoted-printable, limits.

All names, addresses and numbers are invented.
"""

from __future__ import annotations

import base64
import io

from PIL import Image

from tiqora.domain.vcard import parse_vcards


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (120, 90), (180, 40, 40)).save(out, format="JPEG")
    return out.getvalue()


def _fold(line: str, width: int = 75) -> str:
    """RFC 2425 folding the way Apple Contacts writes it (CRLF + one space)."""
    chunks = [line[:width]] + [line[i : i + width - 1] for i in range(width, len(line), width - 1)]
    return "\r\n ".join(chunks)


def _apple_vcf(photo: bytes) -> str:
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        "PRODID:-//Apple Inc.//macOS 15.0//EN",
        "N:Musterfrau;Erika;;Dr.;",
        "FN:Dr. Erika Musterfrau",
        "ORG:Beispiel Werke GmbH;Vertrieb",
        "TITLE:Leiterin Vertrieb",
        "item1.EMAIL;type=INTERNET;type=pref:erika.musterfrau@beispiel-werke.example",
        "item1.X-ABLabel:_$!<Other>!$_",
        "EMAIL;type=INTERNET;type=HOME:erika.privat@mailbox.example",
        "TEL;type=CELL;type=VOICE;type=pref:+49 170 0000001",
        "TEL;type=WORK;type=VOICE:+49 228 0000002",
        "TEL;type=WORK;type=FAX:+49 228 0000003",
        "item2.ADR;type=WORK:;;Musterweg 1;Bonn;;53111;Deutschland",
        "NOTE:Messe 2026\\, Halle 3\\nRückruf erbeten",
        _fold("PHOTO;ENCODING=b;TYPE=JPEG:" + base64.b64encode(photo).decode("ascii")),
        "END:VCARD",
        "BEGIN:VCARD",
        "VERSION:3.0",
        "N:Beispiel;Max;;;",
        "FN:Max Beispiel",
        "EMAIL;type=INTERNET:max@beispiel-werke.example",
        "PHOTO;VALUE=uri:https://tracker.example/photo/max.jpg",
        "END:VCARD",
    ]
    return "\r\n".join(lines) + "\r\n"


def test_apple_contacts_export() -> None:
    photo = _jpeg()
    cards, truncated = parse_vcards(_apple_vcf(photo).encode("utf-8"))
    assert truncated is False
    assert len(cards) == 2
    erika, max_ = cards
    assert erika.index == 0 and max_.index == 1
    assert erika.display_name == "Dr. Erika Musterfrau"
    assert (erika.first_name, erika.last_name) == ("Erika", "Musterfrau")
    assert erika.emails == [
        "erika.musterfrau@beispiel-werke.example",
        "erika.privat@mailbox.example",
    ]
    assert erika.mobile == "+49 170 0000001"
    assert erika.phone == "+49 228 0000002"  # fax ignored
    assert erika.org == "Beispiel Werke GmbH"
    assert erika.title == "Leiterin Vertrieb"
    assert erika.photo == photo
    # An http(s) PHOTO is never fetched.
    assert max_.photo is None
    assert max_.emails == ["max@beispiel-werke.example"]


def test_vcard4_data_uri_photo_and_tel_uri() -> None:
    png = io.BytesIO()
    Image.new("RGB", (40, 40), (0, 0, 0)).save(png, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(png.getvalue()).decode("ascii")
    text = "\n".join(
        [
            "BEGIN:VCARD",
            "VERSION:4.0",
            "FN:Jo Beispiel",
            "N:Beispiel;Jo;;;",
            'TEL;VALUE=uri;TYPE="voice,cell":tel:+49-160-0000009',
            "TEL;VALUE=uri;TYPE=work:tel:+49-30-0000008",
            "EMAIL;PREF=1:jo@beispiel.example",
            _fold("PHOTO:" + data_uri).replace("\r\n", "\n"),
            "END:VCARD",
        ]
    )
    (card,), _ = parse_vcards(text)
    assert card.mobile == "+49-160-0000009"
    assert card.phone == "+49-30-0000008"
    assert card.photo == png.getvalue()


def test_folded_text_lines_and_escapes() -> None:
    text = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\n"
        "FN:Sehr langer Na\r\n me mit Umbruch\r\n"
        "N:Umbruch;Sehr langer\\, Name;;;\r\n"
        "ORG:Firma\\; mit Semikolon;Abteilung\r\n"
        "END:VCARD\r\n"
    )
    (card,), _ = parse_vcards(text)
    assert card.display_name == "Sehr langer Name mit Umbruch"
    assert card.first_name == "Sehr langer, Name"
    assert card.org == "Firma; mit Semikolon"


def test_vcard21_quoted_printable() -> None:
    text = (
        "BEGIN:VCARD\r\nVERSION:2.1\r\n"
        "N;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:M=C3=BCller;J=C3=BCr=\r\n"
        "gen;;;\r\n"
        "TEL;CELL:+49 151 0000004\r\n"
        "EMAIL;INTERNET:juergen@beispiel.example\r\n"
        "END:VCARD\r\n"
    )
    (card,), _ = parse_vcards(text)
    assert (card.first_name, card.last_name) == ("Jürgen", "Müller")
    assert card.display_name == "Jürgen Müller"
    assert card.mobile == "+49 151 0000004"
    assert card.emails == ["juergen@beispiel.example"]


def test_card_limit_truncates() -> None:
    one = "BEGIN:VCARD\nVERSION:3.0\nFN:X\nEND:VCARD\n"
    cards, truncated = parse_vcards(one * 5, max_cards=3)
    assert len(cards) == 3 and truncated is True
    cards, truncated = parse_vcards(one * 3, max_cards=3)
    assert len(cards) == 3 and truncated is False


def test_garbage_and_broken_photo() -> None:
    assert parse_vcards("hello world") == ([], False)
    text = "BEGIN:VCARD\nVERSION:3.0\nFN:Y\nPHOTO;ENCODING=b:!!!not-base64!!!\nEND:VCARD\n"
    (card,), _ = parse_vcards(text)
    assert card.photo is None
