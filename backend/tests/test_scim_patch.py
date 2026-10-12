"""PATCH engine: Entra ID and Okta request shapes (pure, no DB)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tiqora.identity.scim.errors import ScimError
from tiqora.identity.scim.patch import apply_group_patch, apply_user_patch, parse_patch
from tiqora.identity.scim.protocol import SCHEMA_ENTERPRISE, SCHEMA_PATCH, SCHEMA_USER

BASE: dict[str, Any] = {
    "schemas": [SCHEMA_USER],
    "id": "u1",
    "userName": "erika@example.test",
    "active": True,
    "name": {"givenName": "Erika", "familyName": "Muster"},
    "emails": [{"value": "erika@example.test", "type": "work", "primary": True}],
}


def _p(ops: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schemas": [SCHEMA_PATCH], "Operations": ops}


def _user(ops: list[dict[str, Any]], resource: dict[str, Any] | None = None):
    return apply_user_patch(resource or BASE, parse_patch(_p(ops)))


def _err(fn: Any) -> ScimError:
    with pytest.raises(ScimError) as info:
        fn()
    return info.value


def test_entra_replace_active_string_false() -> None:
    doc, touched = _user([{"op": "Replace", "path": "active", "value": "False"}])
    assert doc["active"] is False and touched == {"active"}
    assert BASE["active"] is True  # input is not mutated


def test_okta_valueless_path_dict() -> None:
    doc, touched = _user([{"op": "replace", "value": {"active": False}}])
    assert doc["active"] is False and touched == {"active"}


def test_entra_email_filter_path() -> None:
    doc, touched = _user(
        [{"op": "Replace", "path": 'emails[type eq "work"].value', "value": "neu@example.test"}]
    )
    assert doc["emails"] == [{"value": "neu@example.test", "type": "work", "primary": True}]
    assert touched == {"emails"}


def test_filter_path_without_match_adds() -> None:
    doc, _ = _user(
        [{"op": "Replace", "path": 'phoneNumbers[type eq "mobile"].value', "value": "+4930111"}]
    )
    assert doc["phoneNumbers"] == [{"type": "mobile", "value": "+4930111"}]


def test_name_given_name() -> None:
    doc, touched = _user([{"op": "Replace", "path": "name.givenName", "value": "Eri"}])
    assert doc["name"] == {"givenName": "Eri", "familyName": "Muster"} and touched == {"name"}


def test_remove_username_is_mutability_error() -> None:
    e = _err(lambda: _user([{"op": "remove", "path": "userName"}]))
    assert (e.status, e.scim_type) == (400, "mutability")


def test_unknown_path_invalid_path() -> None:
    e = _err(lambda: _user([{"op": "replace", "path": "nickName", "value": "x"}]))
    assert (e.status, e.scim_type) == (400, "invalidPath")


def test_enterprise_extension_paths_ignored() -> None:
    doc, touched = _user([{"op": "Add", "path": f"{SCHEMA_ENTERPRISE}:department", "value": "IT"}])
    assert doc == BASE and touched == frozenset()


@pytest.mark.parametrize(
    "payload",
    [
        {"schemas": ["urn:wrong"], "Operations": [{"op": "add", "path": "active", "value": True}]},
        {"schemas": [SCHEMA_PATCH], "Operations": []},
        {"schemas": [SCHEMA_PATCH]},
        {"schemas": [SCHEMA_PATCH], "Operations": [{"op": "move", "path": "active"}]},
        {"schemas": [SCHEMA_PATCH], "Operations": ["x"]},
    ],
)
def test_bad_schema_or_empty_ops_invalid_syntax(payload: dict[str, Any]) -> None:
    e = _err(lambda: parse_patch(payload))
    assert (e.status, e.scim_type) == (400, "invalidSyntax")


def test_too_many_ops() -> None:
    ops = [{"op": "add", "path": "active", "value": True}] * 3
    e = _err(lambda: parse_patch(_p(ops), max_ops=2))
    assert e.status == 400


def test_group_add_and_remove_members() -> None:
    d = apply_group_patch(
        parse_patch(
            _p(
                [
                    {"op": "add", "path": "members", "value": [{"value": "a"}, {"value": "b"}]},
                    {"op": "remove", "path": 'members[value eq "c"]'},
                ]
            )
        )
    )
    assert d.add == {"a", "b"} and d.remove == {"c"} and d.replace_all is None
    assert d.display_name is None and d.external_id is False


def test_entra_remove_members_with_value_list() -> None:
    d = apply_group_patch(
        parse_patch(_p([{"op": "Remove", "path": "members", "value": [{"value": "a"}]}]))
    )
    assert d.remove == {"a"} and d.add == frozenset() and d.replace_all is None


def test_remove_all_members() -> None:
    d = apply_group_patch(parse_patch(_p([{"op": "remove", "path": "members"}])))
    assert d.replace_all == frozenset()


def test_replace_display_name() -> None:
    d = apply_group_patch(
        parse_patch(_p([{"op": "replace", "path": "displayName", "value": "Support"}]))
    )
    assert d.display_name == "Support" and d.add == frozenset()


def test_member_limit() -> None:
    ops = parse_patch(
        _p([{"op": "add", "path": "members", "value": [{"value": str(i)} for i in range(4)]}])
    )
    e = _err(lambda: apply_group_patch(ops, max_members=3))
    assert (e.status, e.scim_type) == (400, "invalidValue")
    assert len(apply_group_patch(ops, max_members=4).add) == 4


def _load_corpus() -> list[dict[str, Any]]:
    path = Path(__file__).parent / "fixtures" / "scim" / "patch_corpus.json"
    return json.loads(path.read_text())


@pytest.mark.parametrize("case", _load_corpus(), ids=lambda c: c["name"])
def test_corpus(case: dict[str, Any]) -> None:
    def run() -> Any:
        ops = parse_patch(case["patch"])
        if case["kind"] == "user":
            return apply_user_patch(case["resource"], ops)
        return apply_group_patch(ops)

    if "error" in case:
        e = _err(run)
        assert (e.status, e.scim_type) == (case["error"]["status"], case["error"]["scimType"])
        return
    result = run()
    if case["kind"] == "user":
        doc, touched = result
        assert doc == case["expect"]
        assert sorted(touched) == case["touched"]
    else:
        got = {
            "add": sorted(result.add),
            "remove": sorted(result.remove),
            "replace_all": None if result.replace_all is None else sorted(result.replace_all),
            "display_name": result.display_name,
            "external_id": result.external_id,
        }
        assert got == case["expect"]
