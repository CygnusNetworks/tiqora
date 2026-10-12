"""CI attribute decoding and display resolution (pure) plus the batched name lookups (DB)."""

from __future__ import annotations

import pytest

from tests._itsm_adapter_helpers import (
    count_statements,
    itsm_session,
    seed_company,
    seed_customer,
    seed_service,
    seed_user,
)
from tests._itsm_db import ITSM_PARAMS
from tests._itsm_fixtures import CLASS_CATALOG, catalog_id, seed_ci
from tiqora.itsm.ci_provider import CiRef
from tiqora.itsm.znuny_adapter import attributes as attrs
from tiqora.itsm.znuny_adapter.definitions import AttrDef, parse_definition
from tiqora.znuny.xml_hash import build_xml_hash

# Seed ids 940200-940399: users 940201.., services 940251..; logins/names contain itsm94.

DEFINITION = """---
- Key: Vendor
  Name: Vendor
  Searchable: 1
  Input:
    Type: Text
- Key: NIC
  Name: Network Adapter
  Input:
    Type: Text
  CountMin: 0
  CountMax: 3
  Sub:
  - Key: IPAddress
    Name: IP Address
    Searchable: 1
    Input:
      Type: Text
    CountMin: 0
    CountMax: 20
- Key: Rack
  Name: Rack
  Input:
    Type: Weird
"""


def _defs() -> tuple[AttrDef, ...]:
    return parse_definition(DEFINITION, "yaml")


def _xml(rows: list[tuple[str, str]]) -> list[object]:
    return build_xml_hash(rows)


def test_attributes_repeatable_and_nested_decoding() -> None:
    xml = _xml(
        [
            ("[1]{'Version'}[1]{'Vendor'}[1]{'Content'}", "Example Corp"),
            ("[1]{'Version'}[1]{'NIC'}[1]{'Content'}", "eth0"),
            ("[1]{'Version'}[1]{'NIC'}[1]{'IPAddress'}[1]{'Content'}", "192.0.2.10"),
            ("[1]{'Version'}[1]{'NIC'}[1]{'IPAddress'}[2]{'Content'}", "192.0.2.11"),
            ("[1]{'Version'}[1]{'NIC'}[2]{'Content'}", "eth1"),
            ("[1]{'Version'}[1]{'NIC'}[2]{'IPAddress'}[1]{'Content'}", "198.51.100.7"),
            ("[1]{'Version'}[1]{'NIC'}[3]{'Content'}", ""),  # empty instance is dropped
            ("[1]{'Version'}[1]{'Unknown'}[1]{'Content'}", "ignored"),
        ]
    )
    values = attrs.values_from_xml_data(_defs(), xml)
    assert [v.key for v in values] == ["Vendor", "NIC", "NIC"]
    first, second = values[1], values[2]
    assert first.content == "eth0"
    assert [s.content for s in first.subs] == ["192.0.2.10", "192.0.2.11"]
    assert (second.content, [s.content for s in second.subs]) == ("eth1", ["198.51.100.7"])


def test_decoding_follows_definition_order_not_storage_order() -> None:
    xml = _xml(
        [
            ("[1]{'Version'}[1]{'NIC'}[1]{'Content'}", "eth0"),
            ("[1]{'Version'}[1]{'Vendor'}[1]{'Content'}", "Example Corp"),
        ]
    )
    assert [v.key for v in attrs.values_from_xml_data(_defs(), xml)] == ["Vendor", "NIC"]


def test_decoding_nothing_stored() -> None:
    assert attrs.values_from_xml_data(_defs(), _xml([])) == []
    assert attrs.values_from_xml_data(_defs(), [None]) == []


def test_unknown_input_type_kept_as_other() -> None:
    xml = _xml([("[1]{'Version'}[1]{'Rack'}[1]{'Content'}", "R-12")])
    values = attrs.values_from_xml_data(_defs(), xml)
    (rack,) = attrs.resolve_display(values, _defs())
    assert (rack.kind, rack.display, rack.raw, rack.label) == ("other", "R-12", "R-12", "Rack")
    assert attrs.kind_for("Dummy") == "other"
    assert {attrs.kind_for(t) for t in ("Text", "TextArea", "TextLink")} == {"text", "textarea"}


def test_resolve_display_nests_children_and_keeps_labels() -> None:
    xml = _xml(
        [
            ("[1]{'Version'}[1]{'NIC'}[1]{'Content'}", "eth0"),
            ("[1]{'Version'}[1]{'NIC'}[1]{'IPAddress'}[1]{'Content'}", "192.0.2.10"),
        ]
    )
    (nic,) = attrs.resolve_display(attrs.values_from_xml_data(_defs(), xml), _defs())
    assert nic.label == "Network Adapter"
    assert [(c.key, c.label, c.display) for c in nic.children] == [
        ("IPAddress", "IP Address", "192.0.2.10")
    ]


def test_resolve_display_uses_lookups_and_falls_back_to_the_stored_value() -> None:
    defs = parse_definition(
        "---\n- Key: Svc\n  Name: Service\n  Input:\n    Type: Service\n"
        "- Key: Ref\n  Name: Related\n  Input:\n    Type: CI\n"
        "- Key: Gone\n  Name: Gone\n  Input:\n    Type: Service\n",
        "yaml",
    )
    values = [
        attrs.AttrValue("Svc", "7"),
        attrs.AttrValue("Ref", "42"),
        attrs.AttrValue("Gone", "99"),
    ]
    lookups = attrs.Lookups(
        names={"service": {"7": "Mail"}},
        cis={42: attrs.CiLookup(42, "1022000042", "srv-db-01.example.org", 22)},
    )
    svc, ref, gone = attrs.resolve_display(values, defs, lookups)
    assert (svc.kind, svc.display, svc.raw) == ("service", "Mail", "7")
    assert ref.ref == CiRef("znuny", 42)
    assert ref.display == "srv-db-01.example.org (1022000042)"
    assert (gone.display, gone.ref) == ("99", None)


def test_collect_lookup_keys_groups_values_per_kind() -> None:
    defs = parse_definition(
        "---\n- Key: A\n  Name: A\n  Input:\n    Type: User\n"
        "- Key: B\n  Name: B\n  Input:\n    Type: Text\n"
        "- Key: C\n  Name: C\n  Input:\n    Type: Customer\n  CountMax: 3\n"
        "  Sub:\n  - Key: D\n    Name: D\n    Input:\n      Type: GeneralCatalog\n",
        "yaml",
    )
    values = [
        attrs.AttrValue("A", "3"),
        attrs.AttrValue("B", "plain"),
        attrs.AttrValue("C", "login-a@example.org", (attrs.AttrValue("D", "11"),)),
        attrs.AttrValue("C", "login-b@example.org"),
    ]
    assert attrs.collect_lookup_keys(values, defs) == {
        "user": {"3"},
        "customer": {"login-a@example.org", "login-b@example.org"},
        "catalog": {"11"},
    }


@pytest.mark.db
@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_display_resolution_batched(dialect, profile, request) -> None:
    """Every input type costs at most one statement, however many values there are."""
    async with itsm_session(request, dialect, profile) as (session, reader, engine):
        for i in range(30):
            await seed_user(session, 940201 + i, f"itsm94-u{i}@example.org", "Anna", f"Agent{i}")
            await seed_service(session, 940251 + i, f"itsm94 service {i}")
        await seed_customer(session, "itsm94-c@example.org", "Carl", "Customer", "itsm94-co")
        await seed_company(session, "itsm94-co", "Example Org GmbH")
        class_id = await catalog_id(session, CLASS_CATALOG, "Computer")
        other = await seed_ci(
            session, profile, number="94020001", class_name="Computer", name="itsm94 other"
        )
        yes = await catalog_id(session, "ITSM::ConfigItem::YesNo", "Yes")

        keys = {
            "catalog": {str(yes), str(class_id)},
            "ci": {str(other), "999999999"},
            "user": {str(940201 + i) for i in range(30)},
            "customer": {"itsm94-c@example.org", "nobody@example.org"},
            "customer_company": {"itsm94-co"},
            "service": {str(940251 + i) for i in range(30)},
        }
        with count_statements(engine) as statements:
            lookups = await attrs.load_lookups(session, keys)
        assert len(statements) <= len(keys)  # one per input type
        assert lookups.names["catalog"][str(yes)] == "Yes"
        assert lookups.names["catalog"][str(class_id)] == "Computer"
        assert lookups.cis[other] == attrs.CiLookup(other, "94020001", "itsm94 other", class_id)
        assert 999999999 not in lookups.cis
        assert lookups.names["user"]["940205"] == '"Anna Agent4" <itsm94-u4@example.org>'
        assert lookups.names["customer"] == {
            "itsm94-c@example.org": '"Carl Customer" <itsm94-c@example.org>'
        }
        assert lookups.names["customer_company"] == {"itsm94-co": "Example Org GmbH"}
        assert lookups.names["service"]["940270"] == "itsm94 service 19"
        assert len(lookups.names["service"]) == 30

        # an unreadable class leaves the CI unresolved: no name leaks
        hidden = await attrs.load_lookups(session, {"ci": {str(other)}}, readable_class_ids=[1])
        assert hidden.cis == {}


@pytest.mark.db
@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_user_full_names(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        await seed_user(session, 940290, "itsm94-x@example.org", "Berta", "Beispiel")
        await seed_user(session, 940291, "itsm94-y@example.org", "", "")
        names = await attrs.user_full_names(session, [940290, 940291, 940299])
        assert names == {940290: "Berta Beispiel", 940291: "itsm94-y@example.org"}
