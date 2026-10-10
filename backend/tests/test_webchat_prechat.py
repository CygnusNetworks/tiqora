"""Pre-chat form validation (pure)."""

from __future__ import annotations

import pytest

from tiqora.webchat import text as webchat_text
from tiqora.webchat.models import PrechatField, PrechatOption
from tiqora.webchat.prechat import EMAIL_RE, PrechatInvalid, validate_prechat

FIELDS = [
    PrechatField(key="name", kind="text", label="Name", maps_to="name"),
    PrechatField(key="email", kind="email", label="E-Mail", required=True, maps_to="email"),
    PrechatField(
        key="contract",
        kind="select",
        label="Vertrag",
        options=[PrechatOption(value="a", label="A")],
        maps_to="dynamic_field",
        dynamic_field="Contract",
    ),
    PrechatField(key="newsletter", kind="checkbox", label="Newsletter"),
]


def test_valid_prechat() -> None:
    r = validate_prechat(
        FIELDS,
        {
            "name": "Erika Muster",
            "email": "erika@example.org",
            "contract": "a",
            "newsletter": True,
            "unknown": "x",
        },
    )
    assert r.name == "Erika Muster" and r.email == "erika@example.org"
    assert r.df_values == {"Contract": ["a"]} and r.extra_lines == [("Newsletter", "Ja")]


def test_invalid_prechat_collects_all_errors() -> None:
    with pytest.raises(PrechatInvalid) as e:
        validate_prechat(
            FIELDS, {"email": "nope", "contract": "zzz", "newsletter": "yes", "name": "x" * 201}
        )
    assert e.value.errors == {
        "email": "email",
        "contract": "option",
        "newsletter": "type",
        "name": "too_long",
    }


def test_offline_requires_email() -> None:
    with pytest.raises(PrechatInvalid) as e:
        validate_prechat([], {}, require_email=True)
    assert e.value.errors == {"email": "required"}
    r = validate_prechat([], {"email": "Max@Example.ORG"}, require_email=True)
    assert r.email == "Max@example.org"


def test_offline_require_email_uses_the_mapped_field() -> None:
    fields = [PrechatField(key="mail", kind="email", label="Mail", maps_to="email")]
    with pytest.raises(PrechatInvalid) as e:
        validate_prechat(fields, {"email": "x@example.org"}, require_email=True)
    assert e.value.errors == {"mail": "required"}
    assert validate_prechat(fields, {"mail": "x@example.org"}, require_email=True).email == (
        "x@example.org"
    )


def test_email_rule_is_the_strict_one_from_text() -> None:
    assert EMAIL_RE is webchat_text.EMAIL_RE
    for bad in (
        '"x"@example.org',
        "a<b>@example.org",
        "a,b@example.org",
        "a@exa mple.org",
        "a@example",
        "a..b@example.org",
    ):
        with pytest.raises(PrechatInvalid) as e:
            validate_prechat(FIELDS, {"email": bad})
        assert e.value.errors == {"email": "email"}, bad
    with pytest.raises(PrechatInvalid) as e:
        validate_prechat(FIELDS, {"email": "a" * 240 + "@example.org"})
    assert e.value.errors == {"email": "too_long"}
    # IDNA 2008: the sharp s stays a sharp s (never strasse.de)
    r = validate_prechat(FIELDS, {"email": "erika@straße.de"})
    assert r.email == "erika@xn--strae-oqa.de"


def test_strings_lose_controls_and_empty_optional_fields_are_absent() -> None:
    fields = [
        PrechatField(key="name", kind="text", label="Name", maps_to="name"),
        PrechatField(key="note", kind="textarea", label="Notiz"),
        PrechatField(key="company", kind="text", label="Firma"),
    ]
    r = validate_prechat(
        fields,
        {
            "name": "Erika‮ Muster\x00\n",
            "note": "Zeile 1\r\nZeile​ 2",
            "company": "  ⁦ ",
        },
    )
    assert r.name == "Erika Muster"
    assert r.extra_lines == [("Notiz", "Zeile 1\nZeile 2")]
    # empty optional value: no line, no error
    assert all(label != "Firma" for label, _ in r.extra_lines)


def test_required_fields_and_types() -> None:
    fields = [
        PrechatField(key="company", kind="text", label="Firma", required=True),
        PrechatField(key="consent", kind="checkbox", label="Einwilligung", required=True),
        PrechatField(
            key="plan",
            kind="select",
            label="Tarif",
            required=True,
            options=[PrechatOption(value="s", label="Small")],
        ),
        PrechatField(key="text", kind="textarea", label="Text"),
    ]
    with pytest.raises(PrechatInvalid) as e:
        validate_prechat(fields, {"company": "   ", "consent": False, "text": True})
    assert e.value.errors == {
        "company": "required",
        "consent": "required",
        "plan": "required",
        "text": "type",
    }
    r = validate_prechat(fields, {"company": "ACME", "consent": True, "plan": "s"})
    # a select outside a dynamic field shows the option label
    assert r.extra_lines == [("Firma", "ACME"), ("Einwilligung", "Ja"), ("Tarif", "Small")]


def test_textarea_limit_and_checkbox_no() -> None:
    fields = [
        PrechatField(key="text", kind="textarea", label="Text"),
        PrechatField(key="flag", kind="checkbox", label="Flag"),
        PrechatField(
            key="df", kind="checkbox", label="DF", maps_to="dynamic_field", dynamic_field="Cb"
        ),
    ]
    r = validate_prechat(fields, {"text": "x" * 2000, "flag": False, "df": False})
    assert r.extra_lines == [("Text", "x" * 2000), ("Flag", "Nein")]
    assert r.df_values == {"Cb": ["0"]}
    with pytest.raises(PrechatInvalid) as e:
        validate_prechat(fields, {"text": "x" * 2001})
    assert e.value.errors == {"text": "too_long"}


def test_visitor_from_address_quotes_and_falls_back_to_invalid_tld() -> None:
    from tiqora.webchat.sessions import visitor_from_address

    assert visitor_from_address("Erika", "erika@example.org", "v" * 22) == (
        '"Erika" <erika@example.org>'
    )
    assert visitor_from_address(None, "erika@example.org", "v" * 22) == "erika@example.org"
    # quotes, angle brackets, backslashes and controls cannot break out of the display name
    assert visitor_from_address('Eve" <evil@example.test>\r\n', "e@example.org", "v" * 22) == (
        '"Eve evil@example.test" <e@example.org>'
    )
    assert visitor_from_address(None, None, "AbC_def-123456789") == (
        '"Besucher" <visitor-AbC_def-1234@chat.invalid>'
    )
    assert (
        visitor_from_address("Max", None, "x" * 30) == '"Max" <visitor-xxxxxxxxxxxx@chat.invalid>'
    )
