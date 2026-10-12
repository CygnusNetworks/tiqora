"""Built-in CMDB class templates stay valid."""

from __future__ import annotations

from tiqora.cmdb.definition import iter_attribute_paths, validate_definition
from tiqora.cmdb.seed import DEFAULT_CATALOG_ROWS
from tiqora.cmdb.templates import CLASS_TEMPLATES


def test_every_template_validates() -> None:
    for key, tpl in CLASS_TEMPLATES.items():
        known = {c.catalog_class for c in tpl.catalog_items} | {
            c.catalog_class for c in DEFAULT_CATALOG_ROWS
        }
        assert (
            validate_definition(
                tpl.attributes, known_catalog_classes=known, known_class_keys=set(CLASS_TEMPLATES)
            )
            == []
        ), key
        assert tpl.key == key
    assert set(CLASS_TEMPLATES) == {"computer", "hardware", "location", "network", "software"}


def test_computer_template_shape() -> None:
    paths = dict(iter_attribute_paths(CLASS_TEMPLATES["computer"].attributes))
    assert paths["CPU"].count_max == 16
    assert paths["HardDisk"].count_max == 10 and "HardDisk.Capacity" in paths
    assert paths["NIC.IPAddress"].type == "ip" and paths["NIC.IPAddress"].searchable
    assert paths["NIC.IPAddress"].count_max == 20
    assert paths["Owner"].type == "customer_user" and paths["Owner"].portal
    assert paths["Location"].options.class_keys == ["location"]
    assert paths["Type"].options.catalog_class == "ITSM::ConfigItem::Computer::Type"


def test_software_template_shape() -> None:
    tpl = CLASS_TEMPLATES["software"]
    paths = dict(iter_attribute_paths(tpl.attributes))
    assert paths["LicenceKey"].count_max == 25
    assert paths["LicenceKey.Quantity"].type == "integer"
    assert paths["LicenceKey.ExpirationDate"].type == "date"
    classes = {c.catalog_class for c in tpl.catalog_items}
    assert classes == {
        "ITSM::ConfigItem::Software::Type",
        "ITSM::ConfigItem::Software::LicenceType",
    }


def test_catalog_items_have_unique_keys_per_class() -> None:
    for tpl in CLASS_TEMPLATES.values():
        keys = [(c.catalog_class, c.item_key) for c in tpl.catalog_items]
        assert len(keys) == len(set(keys)) and keys, tpl.key
