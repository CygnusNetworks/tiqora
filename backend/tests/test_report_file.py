"""Report files (JSON / YAML): round trips, alias refusal, error paths (pure, no database)."""

from __future__ import annotations

import json

import pytest

from tiqora.reporting.definition import DefinitionError, ReportDefinition
from tiqora.reporting.report_file import ReportFileOut, dump_report_file, parse_report_file

TICKETS_DEF = {
    "source": "tickets",
    "dimensions": [{"key": "queue"}],
    "measures": [{"key": "tickets"}],
}


def test_json_round_trip() -> None:
    out = ReportFileOut(name="rep48 Datei", definition=ReportDefinition.model_validate(TICKETS_DEF))
    assert parse_report_file(dump_report_file(out, "json").decode()) == out


def test_yaml_round_trip_keeps_umlauts() -> None:
    out = ReportFileOut(
        name="Größe je Queue", definition=ReportDefinition.model_validate(TICKETS_DEF)
    )
    text = dump_report_file(out, "yaml").decode()
    assert "Größe je Queue" in text
    assert parse_report_file(text) == out


def test_yaml_aliases_are_rejected() -> None:
    bomb = "a: &a [x, x]\nb: &b [*a, *a]\nc: [*b, *b]\n"
    with pytest.raises(DefinitionError) as err:
        parse_report_file(bomb)
    assert err.value.path == "content" and "Anker" in err.value.message


@pytest.mark.parametrize(
    "content",
    ["[1, 2]", "format: other\nformat_version: 1\nname: x\ndefinition: {}", "{"],
)
def test_wrong_files_are_definition_errors(content: str) -> None:
    with pytest.raises(DefinitionError) as err:
        parse_report_file(content)
    assert err.value.path == "content"


def test_pydantic_error_carries_dotted_path() -> None:
    bad = json.dumps(
        {
            "format": "tiqora-report",
            "format_version": 1,
            "name": "x",
            "definition": {"source": "tickets", "measures": []},
        }
    )
    with pytest.raises(DefinitionError) as err:
        parse_report_file(bad)
    assert err.value.path == "definition.measures"


@pytest.mark.parametrize("opener", ["[", '{"a":'])
def test_deeply_nested_files_are_definition_errors_not_recursion_errors(opener: str) -> None:
    with pytest.raises(DefinitionError) as err:
        parse_report_file(opener * 100_000)
    assert err.value.path == "content" and "verschachtelt" in err.value.message


def test_deeply_nested_yaml_is_a_definition_error() -> None:
    with pytest.raises(DefinitionError) as err:
        parse_report_file("a: " + "[" * 100_000)
    assert err.value.path == "content"
