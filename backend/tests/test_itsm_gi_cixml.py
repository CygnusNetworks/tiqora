"""GI ``CIXMLData`` conversion (port of ``ConfigItem/Common.pm``, 6.5.4 and 7.3.5 behave alike)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tiqora.itsm.znuny_format.gi_cixml import (
    CixmlError,
    cixml_to_xmldata,
    clean_cixml,
    xmldata_to_cixml,
)

VECTORS = json.loads((Path(__file__).parent / "fixtures/itsm/gi_vectors.json").read_text())


class _Lookups:
    def __init__(
        self,
        catalog: dict[str, dict[str, int]] | None = None,
        customers: list[str] | None = None,
        companies: list[str] | None = None,
    ) -> None:
        self._catalog = catalog or {}
        self._customers = set(customers or [])
        self._companies = set(companies or [])

    def catalog_id(self, catalog_class: str, name: str) -> int | None:
        return self._catalog.get(catalog_class, {}).get(name)

    def catalog_name(self, catalog_id: int) -> str | None:
        for items in self._catalog.values():
            for name, ident in items.items():
                if ident == catalog_id:
                    return name
        return None

    def customer_user_exists(self, login: str) -> bool:
        return login in self._customers

    def customer_company_exists(self, customer_id: str) -> bool:
        return customer_id in self._companies


def _lookups(v: dict[str, Any]) -> _Lookups:
    return _Lookups(v.get("catalog", {}), v.get("customers", []), v.get("companies", []))


@pytest.mark.parametrize("v", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(v: dict[str, Any]) -> None:
    lookups = _lookups(v)
    operation = v.get("operation", "ConfigItemCreate")
    if "error" in v:
        with pytest.raises(CixmlError) as exc:
            cixml_to_xmldata(v["cixml"], v["definition"], lookups=lookups, operation=operation)
        assert (exc.value.code, exc.value.message) == (v["error"]["code"], v["error"]["message"])
    else:
        assert cixml_to_xmldata(v["cixml"], v["definition"], lookups=lookups) == v["xml_data"]
        back = v.get("cixml_back", v["cixml"])
        assert xmldata_to_cixml(v["xml_data"], v["definition"], lookups=lookups) == back


def test_vectors_cover_both_outcomes() -> None:
    assert sum("error" in v for v in VECTORS) >= 10
    assert sum("xml_data" in v for v in VECTORS) >= 10


def test_conversion_does_not_mutate_its_input() -> None:
    v = next(x for x in VECTORS if x["name"] == "catalog_name_to_id")
    before = json.dumps(v["cixml"], sort_keys=True)
    cixml_to_xmldata(v["cixml"], v["definition"], lookups=_lookups(v))
    assert json.dumps(v["cixml"], sort_keys=True) == before


def test_xmldata_with_raw_none_placeholders_and_tagkey_is_accepted() -> None:
    definition = [{"Key": "Vendor", "Input": {"Type": "Text"}}]
    xml = {"Vendor": [None, {"TagKey": "[1]{'Version'}[1]{'Vendor'}[1]", "Content": "Dell"}]}
    assert xmldata_to_cixml(xml, definition, lookups=_Lookups()) == {"Vendor": "Dell"}


def test_unresolvable_catalog_id_comes_back_as_none() -> None:
    definition = [
        {"Key": "Type", "Input": {"Type": "GeneralCatalog", "Class": "ITSM::ConfigItem::X"}}
    ]
    out = xmldata_to_cixml({"Type": [{"Content": "99"}]}, definition, lookups=_Lookups())
    assert out == {"Type": None}


def test_xmldata_nested_too_deep_is_refused() -> None:
    node: dict[str, Any] = {"Content": "x"}
    for _ in range(20):
        node = {"K": [node]}
    with pytest.raises(ValueError):
        xmldata_to_cixml(node, [], lookups=_Lookups())


def test_cixml_nested_too_deep_is_refused_before_recursion() -> None:
    deep: Any = "x"
    for _ in range(80):
        deep = {"K": deep}
    with pytest.raises(CixmlError) as exc:
        cixml_to_xmldata({"K": deep}, [], lookups=_Lookups())
    assert exc.value.code == "InvalidParameter"
    assert "nested too deeply" in exc.value.message


def test_cixml_with_too_many_values_is_refused() -> None:
    with pytest.raises(CixmlError) as exc:
        cixml_to_xmldata({"K": ["x"] * 50_001}, [], lookups=_Lookups())
    assert exc.value.code == "InvalidParameter"
    assert "too many values" in exc.value.message


def test_clean_cixml_trims_strings_at_every_level_and_copies() -> None:
    raw = {
        "Vendor": "  Dell \n",
        "NIC": [{"NIC": " eth0", "IPAddress": "1.2.3.4 "}, " eth1 "],
        "N": 3,
    }
    assert clean_cixml(raw) == {
        "Vendor": "Dell",
        "NIC": [{"NIC": "eth0", "IPAddress": "1.2.3.4"}, "eth1"],
        "N": 3,
    }
    assert raw["Vendor"] == "  Dell \n"
