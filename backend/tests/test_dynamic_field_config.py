"""``config_from_yaml``: PossibleValues keys/labels stay strings; other keys keep types."""

from __future__ import annotations

from tiqora.api.v1.admin.dynamic_fields import config_from_yaml as api_config_from_yaml
from tiqora.domain.dynamic_field_config import config_from_yaml


def test_possible_values_keys_and_labels_stay_strings() -> None:
    raw = (
        "---\nPossibleValues:\n  yes: Ja\n  no: Nein\n  01: x\n  on: y\n  '02': z\n"
        "  7: true\n  null: Leer\n"
    )
    assert config_from_yaml(raw)["PossibleValues"] == {
        "yes": "Ja",
        "no": "Nein",
        "01": "x",
        "on": "y",
        "02": "z",
        "7": "true",
        "null": "Leer",
    }


def test_other_keys_keep_their_native_types() -> None:
    cfg = config_from_yaml("---\nTreeView: 1\nDefaultValue: ''\nRows: 5\nPossibleNone: 0\n")
    assert cfg == {"TreeView": 1, "DefaultValue": "", "Rows": 5, "PossibleNone": 0}


def test_bytes_empty_and_invalid_input() -> None:
    assert config_from_yaml(b"---\nPossibleValues:\n  yes: Ja\n") == {
        "PossibleValues": {"yes": "Ja"}
    }
    assert config_from_yaml(None) == {}
    assert config_from_yaml("") == {}
    assert config_from_yaml(": : [") == {}
    assert config_from_yaml("- a\n- b\n") == {}


def test_api_module_reexports_same_loader() -> None:
    assert api_config_from_yaml is config_from_yaml
