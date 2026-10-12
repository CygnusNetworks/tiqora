"""Pure tests for the process designer core (ids, YAML codec, versions)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

from tiqora.process.designer.ids import (
    ENTITY_ID_RE,
    ENTITY_TYPES,
    SUB_ENTITY_TYPES,
    generate_entity_id,
)
from tiqora.process.designer.versioning import entity_version
from tiqora.process.designer.yaml_codec import (
    dump_znuny_yaml,
    load_znuny_yaml,
    stringify_scalars,
)

_SAMPLE_REL = "scripts/test/sample/ProcessManagement"
_SAMPLE_DIRS = [
    Path(os.environ["TIQORA_ZNUNY_SOURCE"]) / _SAMPLE_REL
    for _ in [0]
    if os.environ.get("TIQORA_ZNUNY_SOURCE")
] + [Path.home() / "git/tiqora-backup-after-smime/tiqora/znuny-6.5.22" / _SAMPLE_REL]


def test_entity_id_shape() -> None:
    ids = {generate_entity_id("Activity") for _ in range(1000)}
    assert len(ids) == 1000
    for eid in ids:
        assert re.fullmatch(r"Activity-[0-9a-f]{32}", eid)
        assert ENTITY_ID_RE.match(eid)
    assert tuple(t for t in ENTITY_TYPES if t != "Process") == SUB_ENTITY_TYPES
    assert "Process" in ENTITY_TYPES and "Process" not in SUB_ENTITY_TYPES


def test_entity_id_total_length_capped_at_50() -> None:
    assert ENTITY_ID_RE.match("TransitionAction-" + "a" * 33)  # 50 chars
    assert ENTITY_ID_RE.match("TransitionAction-" + "a" * 34) is None  # 51 chars
    assert ENTITY_ID_RE.match("Foo-abc") is None
    assert ENTITY_ID_RE.match("Activity-abc\n") is None


def test_yaml_dump_starts_with_document_marker_and_strings() -> None:
    out = dump_znuny_yaml({"Display": 2, "Flag": True, "None": None, "List": [1, "a"]})
    assert out.startswith("---\n")
    assert load_znuny_yaml(out) == {"Display": "2", "Flag": "1", "None": "", "List": ["1", "a"]}


def test_stringify_scalars_does_not_mutate_input() -> None:
    src = {"a": [1, {"b": False}]}
    assert stringify_scalars(src) == {"a": ["1", {"b": "0"}]}
    assert src == {"a": [1, {"b": False}]}


def test_load_empty_and_aliases() -> None:
    assert load_znuny_yaml(None) == {}
    assert load_znuny_yaml("") == {}
    assert load_znuny_yaml(b"") == {}
    with pytest.raises(yaml.YAMLError):
        load_znuny_yaml("a: &x 1\nb: *x\n")
    with pytest.raises(yaml.YAMLError):
        load_znuny_yaml("a: &x 1\n")


def test_reads_znuny_sample_files_unchanged() -> None:
    files = [f for d in _SAMPLE_DIRS if d.is_dir() for f in sorted(d.glob("*.yml"))]
    if not files:
        pytest.skip("Znuny sample files not available")
    for f in files:
        data = load_znuny_yaml(f.read_bytes())
        assert isinstance(data, dict) and data, f.name
        # dump -> load is stable after stringification
        assert load_znuny_yaml(dump_znuny_yaml(data)) == stringify_scalars(data)


def test_version_changes_with_config_and_name_only() -> None:
    base = entity_version(name="n", config="c", layout="l", state_entity_id="S1")
    assert len(base) == 16 and re.fullmatch(r"[0-9a-f]{16}", base)
    assert entity_version(name="n", config="c", layout="l", state_entity_id="S1") == base
    assert entity_version(name="n2", config="c", layout="l", state_entity_id="S1") != base
    assert entity_version(name="n", config="c2", layout="l", state_entity_id="S1") != base
    # field boundaries matter
    assert entity_version(name="nc", config="") != entity_version(name="n", config="c")


def test_umlauts_round_trip() -> None:
    data = {"Name": "Größe äöü ß €", "Liste": ["Prüfung"]}
    out = dump_znuny_yaml(data)
    assert "Größe" in out  # allow_unicode, no escapes
    assert load_znuny_yaml(out.encode("utf-8")) == data
