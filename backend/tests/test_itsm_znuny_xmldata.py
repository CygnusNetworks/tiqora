"""XMLData <-> Tiqora attributes and Znuny's stringified form (vectors from ConfigItemUpdate.t)."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.cmdb.definition import parse_attrs
from tiqora.cmdb.values import normalize_values
from tiqora.itsm.znuny_format.definition import (
    REVERSE_TYPE_MAP,
    to_tiqora_schema,
    to_znuny_definition,
)
from tiqora.itsm.znuny_format.stringified import (
    expand_list_keys,
    stringified_to_xmldata,
    xmldata_to_stringified,
)
from tiqora.itsm.znuny_format.xmldata import attributes_to_xmldata, xmldata_to_attributes

NIC_DEF: list[dict[str, Any]] = [
    {
        "Key": "NIC",
        "Name": "Network Adapter",
        "CountMin": 0,
        "CountMax": 10,
        "CountDefault": 1,
        "Input": {"Type": "Text", "Required": 1},
        "Sub": [
            {
                "Key": "IPoverDHCP",
                "Name": "IP over DHCP",
                "CountMin": 1,
                "CountMax": 1,
                "CountDefault": 1,
                "Input": {"Type": "GeneralCatalog", "Class": "ITSM::ConfigItem::YesNo"},
            },
            {
                "Key": "IPAddress",
                "Name": "IP Address",
                "CountMin": 0,
                "CountMax": 20,
                "CountDefault": 0,
                "Input": {"Type": "Text"},
            },
        ],
    },
    {"Key": "CPU", "Name": "CPU", "CountMin": 0, "CountMax": 16, "Input": {"Type": "Text"}},
    {"Key": "Vendor", "Name": "Vendor", "CountMin": 0, "CountMax": 1, "Input": {"Type": "Text"}},
]
NIC_XML = {
    "NIC": [
        {
            "Content": "NicName1",
            "IPoverDHCP": [{"Content": "37"}],
            "IPAddress": [{"Content": "123"}],
        },
        {
            "Content": "NicName2",
            "IPoverDHCP": [{"Content": "38"}],
            "IPAddress": [{"Content": "456"}],
        },
    ]
}

# ConfigItemUpdate.t lines ~95-160 (HardDisk with a Sub attribute)
HARDDISK_XML = {
    "CPU": [{"Content": "CPU-Test"}],
    "Vendor": [{"Content": "Vendor-Test"}],
    "HardDisk": [
        {"Content": "HardDisk1-Test", "Capacity": [{"Content": "12"}]},
        {"Content": "HardDisk2-Test", "Capacity": [{"Content": "34"}]},
    ],
}
HARDDISK_DEF: list[dict[str, Any]] = [
    {"Key": "CPU", "CountMin": 0, "CountMax": 16, "Input": {"Type": "Text"}},
    {"Key": "Vendor", "CountMin": 0, "CountMax": 1, "Input": {"Type": "Text"}},
    {
        "Key": "HardDisk",
        "CountMin": 0,
        "CountMax": 10,
        "Input": {"Type": "Text"},
        "Sub": [{"Key": "Capacity", "CountMin": 0, "CountMax": 1, "Input": {"Type": "Text"}}],
    },
]


class _Resolver:
    def __init__(self, catalog: dict[str, int] | None = None, ci: dict[str, int] | None = None):
        self._catalog = catalog or {}
        self._ci = ci or {}

    def ci(self, znuny_ci_id: str) -> int | None:
        return self._ci.get(znuny_ci_id)

    def catalog(self, znuny_gc_id: str) -> int | None:
        return self._catalog.get(znuny_gc_id)


def _schema(defn: list[dict[str, Any]]) -> dict[str, Any]:
    schema, _warnings = to_tiqora_schema(defn)
    return schema


@pytest.fixture
def schema_for_nic() -> dict[str, Any]:
    return _schema(NIC_DEF)


@pytest.fixture
def schema_with_ci_ref() -> dict[str, Any]:
    return _schema(
        [
            {
                "Key": "DependsOn",
                "Name": "Depends on",
                "CountMin": 0,
                "CountMax": 5,
                "Input": {"Type": "CI", "CIClassName": "Computer"},
            }
        ]
    )


def test_xmldata_to_stringified_pod_example() -> None:
    assert xmldata_to_stringified(NIC_XML) == {
        "NIC::1": "NicName1",
        "NIC::1::IPAddress::1": "123",
        "NIC::1::IPoverDHCP::1": "37",
        "NIC::2": "NicName2",
        "NIC::2::IPAddress::1": "456",
        "NIC::2::IPoverDHCP::1": "38",
    }


def test_xmldata_to_stringified_znuny_vector_and_perl_truthiness() -> None:
    assert xmldata_to_stringified(HARDDISK_XML) == {
        "CPU::1": "CPU-Test",
        "HardDisk::1": "HardDisk1-Test",
        "HardDisk::1::Capacity::1": "12",
        "HardDisk::2": "HardDisk2-Test",
        "HardDisk::2::Capacity::1": "34",
        "Vendor::1": "Vendor-Test",
    }
    # Content "0" and "" are false in Perl and therefore not stringified (the sub entry stays)
    zero = {"A": [{"Content": "0", "B": [{"Content": "x"}]}, {"Content": ""}]}
    assert xmldata_to_stringified(zero) == {"A::1::B::1": "x"}


def test_expand_list_keys_splits_and_trims() -> None:
    assert expand_list_keys({"CPU::": "NEW-2-CPU, NEW-2-CPU2", "Vendor": "V", "Ram::1": "R"}) == {
        "CPU::1": "NEW-2-CPU",
        "CPU::2": "NEW-2-CPU2",
        "Vendor::1": "V",
        "Ram::1": "R",
    }


def test_expand_list_keys_header_keys_separator_and_perl_split() -> None:
    out = expand_list_keys(
        {
            "ConfigItemID": "5",
            "Name": "n",
            "DeplStateName": "Planned",
            "UserID": "1",
            "ConfigItemNumber": "x",
            "CPU::": "a ; b;;",
        },
        separator=";",
    )
    assert out == {"CPU::1": "a", "CPU::2": "b"}  # headers out, trailing empty field removed
    assert expand_list_keys({"CPU::": "a,,b"}) == {"CPU::1": "a", "CPU::2": "", "CPU::3": "b"}
    assert expand_list_keys({"CPU::": ""}) == {}
    assert expand_list_keys({"CPU::": "a,b"}, separator="") == {"CPU::1": "a", "CPU::2": "b"}
    # the separator is a regex (Znuny interpolates it)
    assert expand_list_keys({"CPU::": "a|b"}, separator=r"\|") == {"CPU::1": "a", "CPU::2": "b"}
    # an explicit Key::N is overwritten by the expansion of Key:: (sorted order, as in Perl)
    assert expand_list_keys({"CPU::": "x", "CPU::1": "explicit"}) == {"CPU::1": "x"}


@pytest.mark.parametrize("separator", ["(", "x" * 65])
def test_expand_list_keys_refuses_bad_separator(separator: str) -> None:
    with pytest.raises(ValueError):
        expand_list_keys({"CPU::": "a,b"}, separator=separator)


def test_stringified_empty_string_deletes_and_original_fills() -> None:
    original = xmldata_to_stringified({"Vendor": [{"Content": "Old"}], "CPU": [{"Content": "c1"}]})
    out = stringified_to_xmldata({"Vendor::1": ""}, NIC_DEF, original)
    assert out["Vendor"] == [
        {"Content": ""}
    ]  # '' wins over the original (ConfigItemUpdate.pm ~680)
    assert out["CPU"] == [{"Content": "c1"}]  # untouched keys come from the original
    assert "NIC" not in out


def test_stringified_znuny_update_vector() -> None:
    """ConfigItemUpdate.t ~287-420: new values replace, the rest comes from the original."""
    original = xmldata_to_stringified(HARDDISK_XML)
    new = {
        "Vendor::1": "NEW-Vendor",
        "CPU::1": "NEW-CPU",
        "CPU::2": "NEW-CPU2",
        "HardDisk::1": "NEW-HardDisk1",
        "HardDisk::1::Capacity::1": "12",
        "HardDisk::2": "NEW-HardDisk2",
        "HardDisk::2::Capacity::1": "44",
        "NotInDefinition::1": "dropped",
    }
    assert stringified_to_xmldata(new, HARDDISK_DEF, original) == {
        "CPU": [{"Content": "NEW-CPU"}, {"Content": "NEW-CPU2"}],
        "Vendor": [{"Content": "NEW-Vendor"}],
        "HardDisk": [
            {"Capacity": [{"Content": "12"}], "Content": "NEW-HardDisk1"},
            {"Capacity": [{"Content": "44"}], "Content": "NEW-HardDisk2"},
        ],
    }


def test_stringified_sub_is_skipped_under_a_false_parent_and_default_applies() -> None:
    defn = [
        {
            "Key": "P",
            "CountMax": 1,
            "Input": {"Type": "Text"},
            "Sub": [{"Key": "C", "CountMax": 1, "Input": {"Type": "Text"}}],
        }
    ]
    assert stringified_to_xmldata({"P::1": "", "P::1::C::1": "x"}, defn, {"P::1": "old"}) == {
        "P": [{"Content": ""}]
    }
    assert stringified_to_xmldata({"P::1": "v", "P::1::C::1": "x"}, defn, {}) == {
        "P": [{"C": [{"Content": "x"}], "Content": "v"}]
    }


def test_stringified_counter_loop_is_bounded() -> None:
    defn = [{"Key": "A", "CountMax": 10**12, "Input": {"Type": "Text"}}]
    assert stringified_to_xmldata({"A::1": "x"}, defn, {}) == {"A": [{"Content": "x"}]}


def test_stringified_customer_lookup_unique_hit_only() -> None:
    defn = [{"Key": "Owner", "CountMin": 0, "CountMax": 1, "Input": {"Type": "Customer"}}]
    hit = stringified_to_xmldata(
        {"Owner::1": "kunde"}, defn, {}, customer_lookup=lambda s: ["itim56.kunde"]
    )
    assert hit["Owner"] == [{"Content": "itim56.kunde"}]
    many = stringified_to_xmldata(
        {"Owner::1": "k"}, defn, {"Owner::1": "alt"}, customer_lookup=lambda s: ["a", "b"]
    )
    assert many["Owner"] == [{"Content": "alt"}]
    none = stringified_to_xmldata(
        {"Owner::1": "k"}, defn, {"Owner::1": "alt"}, customer_lookup=lambda s: []
    )
    assert none["Owner"] == [{"Content": "alt"}]


def test_round_trip_through_tiqora_attributes(schema_for_nic: dict[str, Any]) -> None:
    resolver = _Resolver({"37": 9037, "38": 9038})
    attrs, issues = xmldata_to_attributes(NIC_XML, schema_for_nic, resolver)
    assert issues == []
    assert attrs["NIC"][0] == {
        "v": "NicName1",
        "s": {"IPoverDHCP": [{"v": 9037}], "IPAddress": [{"v": "123"}]},
    }
    assert attributes_to_xmldata(attrs, schema_for_nic) == {
        "NIC": [
            {
                "Content": "NicName1",
                "IPoverDHCP": [{"Content": "9037"}],
                "IPAddress": [{"Content": "123"}],
            },
            {
                "Content": "NicName2",
                "IPoverDHCP": [{"Content": "9038"}],
                "IPAddress": [{"Content": "456"}],
            },
        ]
    }


def test_attributes_pass_cmdb_value_validation(schema_for_nic: dict[str, Any]) -> None:
    attrs, _ = xmldata_to_attributes(NIC_XML, schema_for_nic, _Resolver({"37": 9037, "38": 9038}))
    values, issues = normalize_values(parse_attrs(schema_for_nic["attributes"]), attrs)
    assert issues == []
    assert values == attrs


def test_unresolved_ci_reference_kept_as_text_with_issue(
    schema_with_ci_ref: dict[str, Any],
) -> None:
    attrs, issues = xmldata_to_attributes(
        {"DependsOn": [{"Content": "999"}]}, schema_with_ci_ref, _Resolver({})
    )
    assert attrs["DependsOn"] == [{"v": "999"}]
    assert [i.code for i in issues] == ["unresolved_reference"]
    assert (issues[0].path, issues[0].value) == ("DependsOn[1]", "999")


def test_resolved_ci_reference_becomes_tiqora_id(schema_with_ci_ref: dict[str, Any]) -> None:
    attrs, issues = xmldata_to_attributes(
        {"DependsOn": [{"Content": "7"}, {"Content": "8"}]},
        schema_with_ci_ref,
        _Resolver(ci={"7": 107, "8": 108}),
    )
    assert issues == []
    assert attrs["DependsOn"] == [{"v": 107}, {"v": 108}]


def test_unknown_attribute_is_kept_and_reported(schema_for_nic: dict[str, Any]) -> None:
    attrs, issues = xmldata_to_attributes(
        {"Legacy": [{"Content": "x"}], "TagKey": "ignored", "CPU": [{"Content": "c"}]},
        schema_for_nic,
        _Resolver(),
    )
    assert attrs == {"Legacy": [{"v": "x"}], "CPU": [{"v": "c"}]}
    assert [(i.code, i.path) for i in issues] == [("unknown_attribute", "Legacy")]


def test_date_datetime_integer_and_bad_values() -> None:
    schema = _schema(
        [
            {"Key": "D", "Name": "D", "CountMax": 1, "Input": {"Type": "Date"}},
            {"Key": "T", "Name": "T", "CountMax": 1, "Input": {"Type": "DateTime"}},
            {"Key": "N", "Name": "N", "CountMax": 1, "Input": {"Type": "Integer"}},
            {"Key": "E", "Name": "E", "CountMax": 1, "Input": {"Type": "Date"}},
            {"Key": "M", "Name": "M", "CountMax": 1, "Input": {"Type": "Integer"}},
        ]
    )
    xml = {
        "D": [{"Content": "2018-10-04"}],
        "T": [{"Content": "2018-10-04 13:05:09"}],
        "N": [{"Content": "42"}],
        "E": [{"Content": "2018-13-45"}],
        "M": [{"Content": "forty"}],
    }
    attrs, issues = xmldata_to_attributes(xml, schema, _Resolver())
    assert attrs["D"] == [{"v": "2018-10-04"}]
    assert attrs["T"] == [{"v": "2018-10-04T13:05:09"}]
    assert attrs["N"] == [{"v": 42}]
    assert attrs["E"] == [{"v": "2018-13-45"}]
    assert sorted((i.code, i.path, i.value) for i in issues) == [
        ("bad_value", "E[1]", "2018-13-45"),
        ("bad_value", "M[1]", "forty"),
    ]
    back = attributes_to_xmldata(attrs, schema)
    assert back["T"] == [{"Content": "2018-10-04 13:05:09"}]
    assert back["N"] == [{"Content": "42"}]


def test_xmldata_size_guard() -> None:
    deep: dict[str, Any] = {"A": [{"Content": "x"}]}
    node = deep["A"][0]
    for _ in range(20):
        node["A"] = [{"Content": "x"}]
        node = node["A"][0]
    with pytest.raises(ValueError, match="deeper"):
        xmldata_to_stringified(deep)
    with pytest.raises(ValueError, match="deeper"):
        xmldata_to_attributes(deep, {"attributes": []}, _Resolver())
    wide = {"A": [{"Content": str(i)} for i in range(50_001)]}
    with pytest.raises(ValueError, match="more than"):
        xmldata_to_stringified(wide)
    ok = {"A": [{"Content": str(i)} for i in range(1000)]}
    assert len(xmldata_to_stringified(ok)) == 999  # "0" is false


def test_to_znuny_definition_inverts_the_schema(schema_for_nic: dict[str, Any]) -> None:
    back = to_znuny_definition(schema_for_nic)
    nic = back[0]
    assert (nic["Key"], nic["Name"], nic["CountMin"], nic["CountMax"], nic["CountDefault"]) == (
        "NIC",
        "Network Adapter",
        0,
        10,
        1,
    )
    assert nic["Input"] == {"Type": "Text", "Required": 1}
    dhcp = nic["Sub"][0]
    assert dhcp["Input"] == {"Type": "GeneralCatalog", "Class": "ITSM::ConfigItem::YesNo"}
    # CMDB-PM-I1: IPoverDHCP has CountMin 1 but no Input.Required, so the stored schema has
    # count_min 0 (the row is still rendered: CountDefault 1) and the inverse says CountMin 0.
    assert (dhcp["CountMin"], dhcp["CountMax"], dhcp["CountDefault"]) == (0, 1, 1)
    # parsing the inverse gives the same schema again
    assert _schema(back) == schema_for_nic


@pytest.mark.parametrize(
    ("count_min", "required", "expected"),
    [(None, 1, 1), (None, 0, 0), (1, 0, 0), (2, 1, 2), (0, 1, 0)],
    ids=["required-default", "optional-default", "optional-explicit", "required-2", "required-0"],
)
def test_count_min_survives_the_round_trip_only_for_required_inputs(
    count_min: int | None, required: int, expected: int
) -> None:
    """The importer's schema and its Znuny inverse follow the CMDB rule: a CountMin (explicit or
    Znuny's default 1) binds only with ``Input.Required``; an optional input gets 0."""
    attr: dict[str, Any] = {"Key": "Vendor", "Name": "Vendor", "CountMax": 3}
    attr["Input"] = {"Type": "Text", "Required": 1} if required else {"Type": "Text"}
    if count_min is not None:
        attr["CountMin"] = count_min
    schema = _schema([attr])
    assert schema["attributes"][0]["count_min"] == expected
    [back] = to_znuny_definition(schema)
    assert back["CountMin"] == expected
    assert _schema([back]) == schema


def test_reverse_type_map_covers_every_attribute_type() -> None:
    from typing import get_args

    from tiqora.cmdb.definition import AttributeType

    assert set(REVERSE_TYPE_MAP) == set(get_args(AttributeType))
