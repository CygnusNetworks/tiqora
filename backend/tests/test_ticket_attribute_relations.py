"""Unit tests for Ticket Attribute Relations CSV parse + filter."""

from __future__ import annotations

import pytest

from tiqora.domain.ticket_attribute_relations import (
    allowed_attribute2_values,
    filter_id_name_map_by_allowed_names,
    parse_attribute_relations_csv,
)


def test_parse_semicolon_csv() -> None:
    parsed = parse_attribute_relations_csv(
        "Service;Queue\nHardware;Support\nHardware;Raw\nSoftware;Dev\n"
    )
    assert parsed.attribute_1 == "Service"
    assert parsed.attribute_2 == "Queue"
    assert len(parsed.rows) == 3
    assert allowed_attribute2_values(parsed, attribute1_value="Hardware") == {
        "Support",
        "Raw",
    }
    assert allowed_attribute2_values(parsed, attribute1_value="Software") == {"Dev"}
    assert allowed_attribute2_values(parsed, attribute1_value="Other") == set()


def test_parse_comma_csv() -> None:
    parsed = parse_attribute_relations_csv('Type,Queue\n"Incident",Support\n')
    assert parsed.attribute_1 == "Type"
    assert "Support" in allowed_attribute2_values(parsed, attribute1_value="Incident")


def test_filter_id_name_map() -> None:
    items = {1: "Support", 2: "Raw", 3: "Junk"}
    out = filter_id_name_map_by_allowed_names(items, allowed_names={"Support", "Raw"})
    assert out == {1: "Support", 2: "Raw"}


def test_parse_rejects_bad_header() -> None:
    with pytest.raises(ValueError, match="two header"):
        parse_attribute_relations_csv("OnlyOne\na\n")
