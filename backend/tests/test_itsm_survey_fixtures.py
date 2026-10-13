"""Checks on the generated Znuny ITSM schema fixtures (wave-4 itsm-core, Task 2).

The fixtures in ``tests/fixtures/znuny-itsm/<profile>/`` are produced by
``tests/golden/itsm_survey.py`` from a real Znuny ITSM peer; this module needs neither Docker nor a
database. Profiles: ``itsm-6`` (Znuny 6.5, ITSM bundle 6.5.x) and ``itsm-7`` (Znuny 7.3).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tiqora.itsm import seed_definitions

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "znuny-itsm"
PROFILES = ["itsm-6", "itsm-7"]
FILES = (
    "schema.mysql.sql",
    "schema.postgresql.sql",
    "schema-post.mysql.sql",
    "schema-post.postgresql.sql",
    "initial_insert.sql",
    "sysconfig.json",
    "packages.json",
)
CORE_PACKAGES = (
    "GeneralCatalog",
    "ITSMCore",
    "ITSMIncidentProblemManagement",
    "ITSMConfigurationManagement",
    "ImportExport",
    "ITSMChangeManagement",
    "ITSMServiceLevelManagement",
)
# Tables the importer and the Mode-A adapter rely on (I4); each must be created by the DDL.
REQUIRED_TABLES = (
    "general_catalog",
    "general_catalog_preferences",
    "cip_allocate",
    "configitem",
    "configitem_version",
    "configitem_definition",
    "configitem_history",
    "configitem_history_type",
    "configitem_counter",
)


class _ZnunyYamlLoader(yaml.SafeLoader):
    """SafeLoader that keeps ``No``/``Yes`` strings: Znuny writes them as plain scalars."""


_ZnunyYamlLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_ZnunyYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf")
)


def _znuny_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_ZnunyYamlLoader)  # noqa: S506 - SafeLoader subclass


_INSERT = re.compile(
    r"INSERT INTO (?P<table>\w+) \((?P<columns>[^)]*)\) VALUES \((?P<values>.*)\);\n", re.S
)
_VALUE = re.compile(r"\s*(?:'((?:[^']|'')*)'|(NULL)|(-?\d+(?:\.\d+)?))\s*(?:,|$)", re.S)


def _statements(text: str) -> list[str]:
    """Split on ``);`` + newline that is outside of a quoted string."""
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        char = text[i]
        current.append(char)
        if char == "'":
            if in_string and text[i + 1 : i + 2] == "'":
                current.append("'")
                i += 1
            else:
                in_string = not in_string
        elif char == "\n" and not in_string and "".join(current[-3:]) == ");\n":
            statements.append("".join(current))
            current = []
        i += 1
    return statements


def rows_of(text: str) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for statement in _statements(text):
        start = statement.find("INSERT INTO ")
        if start < 0:
            continue
        match = _INSERT.fullmatch(statement[start:])
        assert match, statement[:200]
        columns = [c.strip() for c in match["columns"].split(",")]
        values: list[Any] = []
        position = 0
        body = match["values"]
        while position < len(body):
            token = _VALUE.match(body, position)
            assert token, f"cannot parse values near {body[position : position + 60]!r}"
            if token.group(1) is not None:
                values.append(token.group(1).replace("''", "'"))
            elif token.group(2):
                values.append(None)
            else:
                number = token.group(3)
                values.append(float(number) if "." in number else int(number))
            position = token.end()
        assert len(values) == len(columns), statement[:200]
        tables.setdefault(match["table"], []).append(dict(zip(columns, values, strict=True)))
    return tables


@pytest.mark.parametrize("profile", PROFILES)
def test_fixture_files_complete(profile: str) -> None:
    folder = FIXTURES / profile
    for name in FILES:
        path = folder / name
        assert path.is_file(), f"missing {path}"
        assert path.stat().st_size > 0, f"empty {path}"
    packages = json.loads((folder / "packages.json").read_text("utf-8"))
    by_name = {p["name"]: p["version"] for p in packages["packages"]}
    for name in CORE_PACKAGES:
        assert re.fullmatch(r"\d+\.\d+\.\d+", by_name.get(name, "")), (name, by_name)
    created = set(packages["tables"])
    for table in REQUIRED_TABLES:
        assert table in created, table
        for dialect in ("mysql", "postgresql"):
            ddl = (folder / f"schema.{dialect}.sql").read_text("utf-8")
            assert re.search(rf"CREATE TABLE {table} \(", ddl), (dialect, table)
    sysconfig = json.loads((folder / "sysconfig.json").read_text("utf-8"))
    assert any(name.startswith("ITSM") for name in sysconfig)
    assert any(name.startswith("LinkObject::") for name in sysconfig)
    assert (FIXTURES / "README.md").is_file()


@pytest.mark.parametrize("profile", PROFILES)
def test_fixtures_have_no_real_data(profile: str) -> None:
    folder = FIXTURES / profile
    insert = (folder / "initial_insert.sql").read_text("utf-8")
    sysconfig = (folder / "sysconfig.json").read_text("utf-8")
    emails = re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", insert + sysconfig)
    assert all(host.endswith(("example.org", "example.com", ".test")) for host in emails), emails
    hosts = re.findall(r"https?://([\w.-]+)", insert)
    allowed = ("example.org", "example.com", ".test")
    assert all(h.endswith(allowed) or h in {"localhost"} for h in hosts), sorted(set(hosts))
    ips = re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", insert)
    assert all(
        ip.startswith(("192.0.2.", "198.51.100.", "203.0.113.", "127.", "255.")) for ip in ips
    ), ips


@pytest.mark.parametrize("profile", PROFILES)
def test_seed_definitions_match_fixture(profile: str) -> None:
    rows = rows_of((FIXTURES / profile / "initial_insert.sql").read_text("utf-8"))
    seed = seed_definitions.for_profile(profile)

    types = {row["name"] for row in rows["ticket_type"]}
    assert {t.name for t in seed.ticket_types} == types

    states = {row["name"]: row for row in rows["ticket_state"]}
    assert {s.name for s in seed.ticket_states} == set(states)
    core_state_types = {"closed": 3}  # ticket_state_type ids of the Znuny core initial_insert
    for state in seed.ticket_states:
        assert states[state.name]["type_id"] == core_state_types[state.type_name]

    fields = {row["name"]: row for row in rows["dynamic_field"]}
    assert {f.name for f in seed.dynamic_fields} == set(fields)
    for field in seed.dynamic_fields:
        row = fields[field.name]
        assert row["field_type"] == field.field_type
        assert row["object_type"] == field.object_type
        assert _znuny_yaml(row["config"]) == field.config
    assert {t.name for t in seed.ticket_history_types} == {
        r["name"] for r in rows["ticket_history_type"]
    }
    groups = {r["name"]: r["comments"] for r in rows["permission_groups"]}
    assert {g.name: g.comments for g in seed.groups} == groups


def test_parse_inserts_handles_quotes_and_newlines() -> None:
    text = "INSERT INTO t (a, b, c) VALUES (1, 'it''s\nok', NULL);\nINSERT INTO t (a) VALUES (2);\n"
    assert rows_of(text) == {"t": [{"a": 1, "b": "it's\nok", "c": None}, {"a": 2}]}
