"""Step-0 ITSM contract modules (pure): CI provider types, provider extension point, mode keys."""

from __future__ import annotations

import dataclasses
import typing

import pytest

from tiqora.extensions import Registry
from tiqora.itsm import ci_provider, mode, providers
from tiqora.itsm.ci_provider import (
    CiError,
    CiNotFound,
    CiPermissionDenied,
    CiProvider,
    CiReadOnly,
    CiRef,
    CiUnavailable,
)
from tiqora.itsm.providers import CI_PROVIDERS, CiProviderFactory


def test_ci_ref_str_and_parse_round_trip() -> None:
    ref = CiRef("znuny", 12)
    assert str(ref) == "znuny:12"
    assert CiRef.parse("znuny:12") == ref
    assert CiRef.parse("tiqora:7") == CiRef("tiqora", 7)
    assert CiRef.parse(str(CiRef("tiqora", 0))) == CiRef("tiqora", 0)


@pytest.mark.parametrize(
    "raw",
    [
        "x:1",
        "znuny:-1",
        "znuny:",
        ":1",
        "znuny",
        "znuny:1:2",
        "znuny:1.5",
        "Znuny:1",
        " znuny:1",
        "znuny: 1",
        "znuny:+1",
        "znuny:١",
        "",
    ],
)
def test_ci_ref_parse_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        CiRef.parse(raw)


def test_ci_ref_is_ordered_and_hashable() -> None:
    refs = [CiRef("znuny", 2), CiRef("tiqora", 5), CiRef("znuny", 1)]
    assert sorted(refs) == [CiRef("tiqora", 5), CiRef("znuny", 1), CiRef("znuny", 2)]
    assert len({CiRef("znuny", 1), CiRef("znuny", 1)}) == 1


def test_errors_hierarchy_and_unavailable_code() -> None:
    for cls in (CiNotFound, CiPermissionDenied, CiReadOnly, CiUnavailable):
        assert issubclass(cls, CiError)
    err = CiUnavailable("cmdb_not_installed")
    assert err.code == "cmdb_not_installed"
    assert "cmdb_not_installed" in str(err)
    assert CiUnavailable("znuny_itsm_missing", "no tables").code == "znuny_itsm_missing"


def test_dataclasses_are_frozen_with_i3_fields() -> None:
    expected = {
        "CiRef": ["source", "id"],
        "CatalogValue": ["key", "name", "functionality"],
        "CiClassInfo": ["key", "name", "icon", "can_read", "can_write"],
        "CiSummary": [
            "ref",
            "number",
            "name",
            "class_key",
            "class_name",
            "deployment_state",
            "incident_state",
            "changed_at",
            "edit_url",
        ],
        "CiAttribute": ["key", "label", "kind", "display", "raw", "ref", "children"],
        "CiVersionInfo": [
            "id",
            "seq",
            "name",
            "deployment_state",
            "incident_state",
            "created_at",
            "created_by",
            "created_by_name",
            "change_note",
        ],
        "CiDetail": ["summary", "version", "attributes", "definition_version", "read_only"],
        "CiQuery": [
            "text",
            "class_keys",
            "deployment_state_keys",
            "incident_state_keys",
            "refs",
            "limit",
            "offset",
        ],
        "CiPage": ["items", "total", "truncated"],
        "CiTicketLink": ["ticket_id", "ci", "link_type", "direction", "created_at"],
        "LinkTypeOption": ["name", "source_label", "target_label", "pointed"],
        "ServiceIncidentState": ["service_id", "state", "cis_incident", "cis_warning"],
    }
    for name, fields in expected.items():
        cls = getattr(ci_provider, name)
        assert [f.name for f in dataclasses.fields(cls)] == fields, name
        params = cls.__dataclass_params__  # type: ignore[attr-defined]
        assert params.frozen, name
    query = ci_provider.CiQuery()
    assert (query.text, query.limit, query.offset, query.refs) == (None, 25, 0, ())


def test_provider_protocol_methods() -> None:
    methods = {
        "is_empty",
        "classes",
        "deployment_states",
        "incident_states",
        "search",
        "get",
        "get_many",
        "versions",
        "ticket_links",
        "ci_ticket_ids",
        "ticket_link_types",
        "link_ticket",
        "unlink_ticket",
        "service_incident_states",
        "service_cis",
    }
    for name in methods:
        assert callable(getattr(CiProvider, name)), name
    hints = typing.get_type_hints(CiProvider)
    assert {"source", "read_only"} <= set(hints)

    class _Partial:
        source = "tiqora"
        read_only = False

    assert not isinstance(_Partial(), CiProvider)


def test_ci_providers_extension_point() -> None:
    assert CI_PROVIDERS.key == "itsm.ci_providers"
    assert CI_PROVIDERS.sdk_stable is False

    def _create(_sysconfig: object) -> CiProvider:
        raise AssertionError("not called")

    reg = Registry()
    factory = CiProviderFactory(mode="tiqora", create=_create)  # type: ignore[arg-type]
    reg.add(CI_PROVIDERS, factory)
    assert reg.get(CI_PROVIDERS, "tiqora") is factory
    assert reg.get(CI_PROVIDERS, "znuny_readthrough") is None
    # the lookups moved here from Task 7 (ruling F6): both are defined in providers.py
    assert callable(providers.get_ci_provider) and callable(providers.tiqora_cmdb_empty)


def test_mode_keys() -> None:
    assert typing.get_args(mode.ItsmMode) == ("off", "znuny_readthrough", "tiqora")
    assert typing.get_args(mode.ModeSource) == ("auto", "admin", "importer")
    assert mode.SETTING_MODE == "itsm.mode"
    assert mode.SETTING_MODE_META == "itsm.mode_meta"
    assert mode.SETTING_SWITCH == "itsm.switch"
    assert mode.SETTING_ZNUNY_READONLY == "itsm.znuny_readonly"
    assert mode.ZNUNY_ITSM_MARKER_TABLES == ("general_catalog", "cip_allocate")
    assert {"mode", "mode_meta", "switch", "znuny_readonly"} == mode.RESERVED_ITSM_SECTION_FIELDS
    # every reserved field is the suffix of one of the guarded keys
    keys = {
        mode.SETTING_MODE,
        mode.SETTING_MODE_META,
        mode.SETTING_SWITCH,
        mode.SETTING_ZNUNY_READONLY,
    }
    assert {f"itsm.{f}" for f in mode.RESERVED_ITSM_SECTION_FIELDS} == keys
