from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tiqora.identity.scim.errors import ScimError
from tiqora.identity.scim.protocol import SCHEMA_ENTERPRISE, SCHEMA_USER
from tiqora.identity.scim.users_map import parse_user, to_profile, user_resource

DT = datetime(2026, 10, 12, 8, 30, 5, tzinfo=UTC)


def test_parse_minimal_entra_user() -> None:
    inp = parse_user(
        {
            "schemas": [SCHEMA_USER, SCHEMA_ENTERPRISE],
            "userName": " sc63.erika@example.test ",
            "active": "True",
            "externalId": "erika",
            "displayName": "Erika Muster",
            "name": {"givenName": "Erika", "familyName": "Muster"},
            "emails": [{"primary": True, "type": "work", "value": "Erika@EXAMPLE.test"}],
            SCHEMA_ENTERPRISE: {"department": "IT"},
        }
    )
    assert (inp.user_name, inp.active, inp.email, inp.external_id) == (
        "sc63.erika@example.test",
        True,
        "Erika@example.test",
        "erika",
    )
    assert (inp.given_name, inp.family_name, inp.display_name) == (
        "Erika",
        "Muster",
        "Erika Muster",
    )
    assert SCHEMA_ENTERPRISE not in inp.sent
    assert {"userName", "active", "emails", "name"} <= inp.sent


def _emails(*entries: dict[str, Any]) -> str | None:
    return parse_user({"userName": "u@example.test", "emails": list(entries)}).email


def test_email_choice_order() -> None:
    first = {"value": "a@example.test"}
    work = {"value": "b@example.test", "type": "work"}
    primary = {"value": "c@example.test", "type": "home", "primary": True}
    assert _emails(first, work, primary) == "c@example.test"
    assert _emails(first, work) == "b@example.test"
    assert _emails(first, {"value": "z@example.test", "type": "home"}) == "a@example.test"
    assert _emails() is None
    assert _emails(first, {"value": "p@example.test", "primary": "True"}) == "p@example.test"


def test_mobile_only_from_type_mobile() -> None:
    def mobile(*entries: dict[str, Any]) -> str | None:
        return parse_user({"userName": "u@example.test", "phoneNumbers": list(entries)}).mobile

    assert (
        mobile({"value": "+49 1", "type": "work"}, {"value": "+49 2", "type": "mobile"}) == "+49 2"
    )
    assert mobile({"value": "+49 1", "type": "work"}, {"value": "+49 3"}) is None
    assert mobile() is None


@pytest.mark.parametrize(
    "doc",
    [
        {},
        {"userName": ""},
        {"userName": "   "},
        {"userName": "a\x00b"},
        {"userName": "x" * 201},
        {"userName": "a", "active": "maybe"},
        {"userName": 5},
        {"userName": "a", "name": "x"},
        {"userName": "a", "emails": "x@example.test"},
    ],
)
def test_invalid_users(doc: dict[str, Any]) -> None:
    with pytest.raises(ScimError) as exc:
        parse_user(doc)
    assert exc.value.status == 400
    assert exc.value.scim_type in ("invalidValue", "invalidSyntax")


def test_password_and_readonly_ignored() -> None:
    inp = parse_user(
        {
            "userName": "u@example.test",
            "password": "secret",
            "id": "abc",
            "meta": {"x": 1},
            "groups": [{"value": "g"}],
            "schemas": [SCHEMA_USER],
        }
    )
    assert inp.sent == frozenset({"userName"})
    assert inp.active is None and inp.email is None


def test_names_strip_control_and_bidi() -> None:
    inp = parse_user(
        {"userName": "u@example.test", "name": {"givenName": "Er‮ika\x07", "familyName": " Muster "}}
    )
    assert (inp.given_name, inp.family_name) == ("Erika", "Muster")


def test_to_profile_display_name_fallback() -> None:
    p = to_profile(parse_user({"userName": "u@example.test", "displayName": "Max Beispiel Junior"}))
    assert (p.first_name, p.last_name) == ("Max", "Beispiel Junior") and p.customer_id is None
    assert p.login == "u@example.test"


def test_to_profile_prefers_name_parts() -> None:
    p = to_profile(
        parse_user(
            {
                "userName": "u@example.test",
                "displayName": "Ignored Name",
                "name": {"givenName": "Erika", "familyName": "Muster"},
                "emails": [{"value": "u@Example.TEST"}],
                "phoneNumbers": [{"value": "+49 5", "type": "mobile"}],
            }
        )
    )
    assert (p.first_name, p.last_name, p.email, p.mobile) == (
        "Erika",
        "Muster",
        "u@example.test",
        "+49 5",
    )


def test_user_resource_shape() -> None:
    loc = "https://helpdesk.example.org/scim/v2/Users/3f0c"
    doc = user_resource(
        scim_id="3f0c",
        user_name="u@example.test",
        external_id=None,
        active=False,
        first_name="Max",
        last_name="Beispiel",
        email="u@example.test",
        mobile=None,
        groups=[("g1", "Support")],
        location=loc,
        created=DT,
        changed=DT,
        version=2,
    )
    assert doc["schemas"] == [SCHEMA_USER] and doc["id"] == "3f0c"
    assert doc["active"] is False and "externalId" not in doc and "phoneNumbers" not in doc
    assert doc["name"] == {
        "givenName": "Max",
        "familyName": "Beispiel",
        "formatted": "Max Beispiel",
    }
    assert doc["displayName"] == "Max Beispiel"
    assert doc["emails"] == [{"value": "u@example.test", "type": "work", "primary": True}]
    assert doc["groups"][0] == {
        "value": "g1",
        "display": "Support",
        "$ref": "https://helpdesk.example.org/scim/v2/Groups/g1",
    }
    assert doc["meta"]["version"] == 'W/"2"'
    assert doc["meta"]["location"] == loc and doc["meta"]["resourceType"] == "User"


def test_user_resource_optional_parts() -> None:
    doc = user_resource(
        scim_id="1",
        user_name="u",
        external_id="ext",
        active=True,
        first_name="A",
        last_name="B",
        email=None,
        mobile="+49 1",
        groups=[],
        location="https://h.example.org/scim/v2/Users/1",
        created=DT,
        changed=DT,
        version=1,
    )
    assert doc["externalId"] == "ext" and "emails" not in doc and doc["groups"] == []
    assert doc["phoneNumbers"] == [{"value": "+49 1", "type": "mobile"}]
