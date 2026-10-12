"""SCIM protocol layer (pure): errors, filters, list params, projection, discovery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tiqora.identity.scim.errors import ScimError, scim_error_body
from tiqora.identity.scim.protocol import (
    SCHEMA_GROUP,
    SCHEMA_LIST,
    SCHEMA_USER,
    ScimFilter,
    list_response,
    meta,
    parse_filter,
    parse_list_params,
    project,
    resource_types,
    schemas_document,
    service_provider_config,
)


def test_error_body_status_is_string() -> None:
    assert scim_error_body(409, "uniqueness", "dup") == {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": "409",
        "scimType": "uniqueness",
        "detail": "dup",
    }
    assert "scimType" not in scim_error_body(404, None, "x")


@pytest.mark.parametrize(
    ("raw", "attr", "value"),
    [
        ('userName eq "erika@example.test"', "userName", "erika@example.test"),
        ('USERNAME EQ "Erika"', "userName", "Erika"),
        ('externalId eq "00aa-11"', "externalId", "00aa-11"),
        ('id eq "7"', "id", "7"),
        ('emails[type eq "work"].value eq "a@example.test"', "emails.value", "a@example.test"),
        ('emails.value eq "a@example.test"', "emails.value", "a@example.test"),
        ('userName eq "say \\"hi\\""', "userName", 'say "hi"'),
        ('  userName   eq   "x"  ', "userName", "x"),
    ],
)
def test_parse_user_filters(raw: str, attr: str, value: str) -> None:
    assert parse_filter(raw, resource="User") == ScimFilter(attr, value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw",
    [
        'userName co "e"',
        'userName eq "a" and active eq true',
        "active eq true",
        "userName eq a",
        'userName eq "a" x',
        'members eq "1"',
        'userName eq "' + "x" * 1001 + '"',
        'userName eq "unterminated',
        'userName eq "bad \\q escape"',
        'displayName eq "x"',
        'emails[type eq "work"].display eq "x"',
        'userNameeq "x"',
        "userName pr",
    ],
)
def test_rejects_unsupported(raw: str) -> None:
    with pytest.raises(ScimError) as exc:
        parse_filter(raw, resource="User")
    assert exc.value.status == 400
    assert exc.value.scim_type == "invalidFilter"


def test_blank_filter_is_none() -> None:
    assert parse_filter(None, resource="User") is None
    assert parse_filter("   ", resource="Group") is None


def test_group_filter_display_name() -> None:
    assert parse_filter('displayName eq "Support"', resource="Group") == ScimFilter(
        "displayName", "Support"
    )
    with pytest.raises(ScimError):
        parse_filter('userName eq "x"', resource="Group")
    with pytest.raises(ScimError):
        parse_filter('emails.value eq "a@example.test"', resource="Group")


def test_long_filter_scan_is_fast() -> None:
    with pytest.raises(ScimError):
        parse_filter('userName eq "' + "\\" * 990 + "x", resource="User")


def test_list_params_bounds() -> None:
    p = parse_list_params({"startIndex": "0", "count": "500"})
    assert (p.start_index, p.count) == (1, 200)
    assert parse_list_params({}).count == 100
    assert parse_list_params({}).start_index == 1
    assert parse_list_params({"count": "-3"}).count == 0
    with pytest.raises(ScimError) as exc:
        parse_list_params({"count": "ten"})
    assert exc.value.scim_type == "invalidValue"
    with pytest.raises(ScimError):
        parse_list_params({"startIndex": ""})


def test_projection_keeps_id_schemas_meta() -> None:
    doc = {
        "schemas": [SCHEMA_USER],
        "id": "x",
        "meta": {},
        "userName": "u",
        "emails": [],
        "members": [],
    }
    only = project(doc, parse_list_params({"attributes": "userName"}))
    assert set(only) == {"schemas", "id", "meta", "userName"}
    assert "members" not in project(doc, parse_list_params({"excludedAttributes": "members"}))
    sub = project(doc, parse_list_params({"attributes": "emails.value"}))
    assert set(sub) == {"schemas", "id", "meta", "emails"}
    keep = project(doc, parse_list_params({"excludedAttributes": "id,meta"}))
    assert {"id", "meta", "schemas"} <= set(keep)


def test_list_response_shape() -> None:
    r = list_response(3, [{"id": "a"}], parse_list_params({"startIndex": "2", "count": "1"}))
    assert r == {
        "schemas": [SCHEMA_LIST],
        "totalResults": 3,
        "startIndex": 2,
        "itemsPerPage": 1,
        "Resources": [{"id": "a"}],
    }


def test_service_provider_config_flags() -> None:
    c = service_provider_config("https://helpdesk.example.org/scim/v2")
    assert c["patch"]["supported"]
    assert not c["bulk"]["supported"]
    assert c["filter"]["maxResults"] == 200
    assert not c["etag"]["supported"]
    assert not c["sort"]["supported"]
    assert not c["changePassword"]["supported"]
    assert c["authenticationSchemes"][0]["type"] == "oauthbearertoken"


def test_resource_types_groups_flag() -> None:
    base = "https://helpdesk.example.org/scim/v2"
    assert [t["id"] for t in resource_types(base, groups=False)] == ["User"]
    both = resource_types(base, groups=True)
    assert [t["id"] for t in both] == ["User", "Group"]
    assert both[1]["meta"]["location"] == f"{base}/ResourceTypes/Group"
    assert both[1]["schema"] == SCHEMA_GROUP


def test_schemas_document_lists_user_and_group() -> None:
    docs = schemas_document()
    assert [d["id"] for d in docs] == [SCHEMA_USER, SCHEMA_GROUP]
    user_names = {a["name"] for a in docs[0]["attributes"]}
    assert {"userName", "emails", "active"} <= user_names
    id_attr = next(a for a in docs[0]["attributes"] if a["name"] == "id")
    assert id_attr["mutability"] == "readOnly"


def test_meta_version_is_weak_etag() -> None:
    created = datetime(2026, 10, 1, 8, 30, 5)
    changed = datetime(2026, 10, 2, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    m = meta("User", "https://h.example.org/scim/v2/Users/7", created, changed, 3)
    assert m["version"] == 'W/"3"'
    assert m["created"] == "2026-10-01T08:30:05Z"
    assert m["lastModified"] == "2026-10-02T08:00:00Z"
    assert m["resourceType"] == "User"
