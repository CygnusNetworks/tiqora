from datetime import datetime
from typing import Any

from tiqora.db.legacy.customer import CustomerUser
from tiqora.domain.vcard import build_vcard, vcard_filename


def _cu(**kw: Any) -> CustomerUser:
    base: dict[str, Any] = dict(
        login="em",
        email="erika@example.org",
        customer_id="BSP",
        first_name="Erika",
        last_name="Mustermann",
        title=None,
        phone="0228 5550100",
        mobile="+49 171 5550199",
        fax=None,
        street="Beispielstr. 1",
        zip="12345",
        city="Musterstadt",
        country="DE",
        comments="intern",
        valid_id=1,
        change_time=datetime(2026, 10, 5, 8, 0, 0),
    )
    base.update(kw)
    return CustomerUser(**base)


def test_full_card() -> None:
    text = build_vcard(_cu(), "Beispiel-Werk")
    assert text.startswith("BEGIN:VCARD\r\nVERSION:3.0\r\n")
    assert "N:Mustermann;Erika;;;\r\n" in text
    assert "FN:Erika Mustermann\r\n" in text
    assert "ORG:Beispiel-Werk\r\n" in text
    assert "EMAIL;TYPE=INTERNET:erika@example.org\r\n" in text
    # libphonenumber: 0228 5550100 is a valid DE fixed line -> E.164.
    assert "TEL;TYPE=WORK:+492285550100\r\n" in text
    assert "TEL;TYPE=CELL:+491715550199\r\n" in text
    assert "ADR;TYPE=WORK:;;Beispielstr. 1;Musterstadt;;12345;DE\r\n" in text
    assert "UID:tiqora-customer-em\r\n" in text
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
    text = build_vcard(_cu(phone="kein Telefon", mobile=None, fax="0228 5550101"), None)
    assert "TEL;TYPE=WORK:kein Telefon\r\n" in text
    assert "TEL;TYPE=FAX:+492285550101\r\n" in text


def test_filename() -> None:
    assert vcard_filename("Erika Mustermann") == "Erika Mustermann.vcf"
    assert vcard_filename('a/b\\c:"x"') == "abcx.vcf"
    assert vcard_filename("") == "kontakt.vcf"
