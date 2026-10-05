from datetime import datetime
from typing import Any

from tiqora.db.legacy.customer import CustomerUser
from tiqora.domain.vcard import build_vcard, vcard_filename


def _cu(**kw: Any) -> CustomerUser:
    base: dict[str, Any] = dict(
        login="ak",
        email="anna@stw.de",
        customer_id="STW",
        first_name="Anna",
        last_name="Kettler",
        title=None,
        phone="0228 73700",
        mobile="+49 171 7630944",
        fax=None,
        street="Nassestr. 11",
        zip="53113",
        city="Bonn",
        country="DE",
        comments="intern",
        valid_id=1,
        change_time=datetime(2026, 10, 5, 8, 0, 0),
    )
    base.update(kw)
    return CustomerUser(**base)


def test_full_card() -> None:
    text = build_vcard(_cu(), "Studierendenwerk Bonn")
    assert text.startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n")
    assert "N:Kettler;Anna;;;\r\n" in text
    assert "FN:Anna Kettler\r\n" in text
    assert "ORG:Studierendenwerk Bonn\r\n" in text
    assert "EMAIL;TYPE=INTERNET:anna@stw.de\r\n" in text
    # libphonenumber: 0228 73700 is a valid DE fixed line -> E.164.
    assert "TEL;TYPE=WORK:+4922873700\r\n" in text
    assert "TEL;TYPE=CELL:+491717630944\r\n" in text
    assert "ADR;TYPE=WORK:;;Nassestr. 11;Bonn;;53113;DE\r\n" in text
    assert "UID:tiqora-customer-ak\r\n" in text
    assert "REV:20261005T080000Z\r\n" in text
    assert "intern" not in text
    assert text.endswith("END:VCARD\r\n")


def test_minimal_card_and_escaping() -> None:
    text = build_vcard(
        _cu(
            first_name="",
            last_name="Müller, Jr.",
            email="",
            phone=None,
            mobile=None,
            street=None,
            zip=None,
            city=None,
            country=None,
        ),
        None,
    )
    assert "N:Müller\\, Jr.;;;;\r\n" in text
    assert "ORG:" not in text and "EMAIL" not in text and "ADR" not in text


def test_long_lines_are_folded() -> None:
    text = build_vcard(_cu(street="Ä" * 60), None)
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75
    unfolded = text.replace("\r\n ", "")
    assert "Ä" * 60 in unfolded


def test_phone_types_and_raw_fallback() -> None:
    text = build_vcard(_cu(phone="kein Telefon", mobile=None, fax="0228 73701"), None)
    assert "TEL;TYPE=WORK:kein Telefon\r\n" in text
    assert "TEL;TYPE=FAX:+4922873701\r\n" in text


def test_filename() -> None:
    assert vcard_filename("Anna Kettler") == "Anna Kettler.vcf"
    assert vcard_filename('a/b\\c:"x"') == "abcx.vcf"
    assert vcard_filename("") == "kontakt.vcf"
