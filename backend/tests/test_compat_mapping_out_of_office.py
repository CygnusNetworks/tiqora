"""Mapping::OutOfOffice - CSV fixtures ported from Znuny's OutOfOffice.t plus edge cases."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.api.compat.mapping import MAPPER_FACTORIES, MappingError, build_mapper
from tiqora.api.compat.mapping.out_of_office import OutOfOfficeMapper

CSV = (
    "# Kommentar\r\nUserLogin,EMail,StartTime,EndTime,Enabled\r\n"
    "agent.one,,01.03.2026 00:00:00,15.03.2026 23:59:59,yes\r\n"
    ",agent.two@example.org,2026-04-01,2026-04-02,no\r\n\r\n"
)


def _entry(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "UserLogin": None,
        "UserSearch": None,
        "UserEmail": None,
        "StartDate": None,
        "EndDate": None,
        "OutOfOffice": None,
    }
    base.update(kw)
    return base


# (csv string, expected entries) - the fixtures of scripts/test/.../User/OutOfOffice.t
CASES = [
    (
        "UserEmail,StartDate,EndDate,OutOfOffice\nagent@example.org,2017-10-20,2017-10-27,yes",
        [
            _entry(
                UserEmail="agent@example.org",
                StartDate="2017-10-20",
                EndDate="2017-10-27",
                OutOfOffice=1,
            )
        ],
    ),
    (
        "UserSearch,StartDate,EndDate,OutOfOffice\nJane,2017-10-20,2017-10-30,yes",
        [_entry(UserSearch="Jane", StartDate="2017-10-20", EndDate="2017-10-30", OutOfOffice=1)],
    ),
    (
        "UserLogin,StartDate,EndDate,OutOfOffice\njdoe,2017-10-20,2017-10-27,yes",
        [_entry(UserLogin="jdoe", StartDate="2017-10-20", EndDate="2017-10-27", OutOfOffice=1)],
    ),
    (
        "UserLogin,OutOfOffice\njdoe,no",
        [_entry(UserLogin="jdoe", OutOfOffice=0)],
    ),
]


@pytest.mark.parametrize(("csv_string", "expected"), CASES)
def test_znuny_fixtures(csv_string: str, expected: list[dict[str, Any]]) -> None:
    out = OutOfOfficeMapper({}).map({"OutOfOfficeEntriesCSVString": csv_string})
    assert out["OutOfOfficeEntries"] == expected


def test_csv_maps_to_entries_and_keeps_credentials() -> None:
    out = OutOfOfficeMapper({}).map(
        {"UserLogin": "admin", "Password": "x", "OutOfOfficeEntriesCSVString": CSV}
    )
    assert out["UserLogin"] == "admin" and out["Password"] == "x"
    assert out["OutOfOfficeEntriesCSVString"] == CSV
    assert out["OutOfOfficeEntries"] == [
        {
            "UserLogin": "agent.one",
            "UserSearch": None,
            "UserEmail": "",
            "StartDate": "2026-03-01",
            "EndDate": "2026-03-15",
            "OutOfOffice": 1,
        },
        {
            "UserLogin": "",
            "UserSearch": None,
            "UserEmail": "agent.two@example.org",
            "StartDate": "2026-04-01",
            "EndDate": "2026-04-02",
            "OutOfOffice": 0,
        },
    ]


def test_does_not_mutate_input() -> None:
    data = {"UserLogin": "admin", "OutOfOfficeEntriesCSVString": CSV}
    OutOfOfficeMapper({}).map(data)
    assert set(data) == {"UserLogin", "OutOfOfficeEntriesCSVString"}


@pytest.mark.parametrize("data", [{"UserLogin": "admin"}, {"OutOfOfficeEntriesCSVString": ""}])
def test_missing_csv_is_error(data: dict[str, Any]) -> None:
    with pytest.raises(MappingError, match="Parameter OutOfOfficeEntriesCSVString is missing."):
        OutOfOfficeMapper({}).map(data)


@pytest.mark.parametrize("csv_string", ["UserLogin,Enabled\n", "INVALID"])
def test_header_only_is_error(csv_string: str) -> None:
    with pytest.raises(MappingError) as err:
        OutOfOfficeMapper({}).map({"OutOfOfficeEntriesCSVString": csv_string})
    assert str(err.value) == (
        "CSV in parameter OutOfOfficeEntriesCSVString does not contain any entries."
    )


def test_only_comments_is_parse_error() -> None:
    with pytest.raises(MappingError, match="Error while parsing CSV string"):
        OutOfOfficeMapper({}).map({"OutOfOfficeEntriesCSVString": "# nothing\n# here\n"})


def test_malformed_quote_is_parse_error() -> None:
    with pytest.raises(MappingError, match="Error while parsing CSV string"):
        OutOfOfficeMapper({}).map({"OutOfOfficeEntriesCSVString": 'UserLogin\nab"cd\n'})


def test_quoted_comma() -> None:
    out = OutOfOfficeMapper({}).map(
        {"OutOfOfficeEntriesCSVString": 'UserSearch,Enabled\n"Doe, Jane",yes\n'}
    )
    assert out["OutOfOfficeEntries"][0]["UserSearch"] == "Doe, Jane"
    assert out["OutOfOfficeEntries"][0]["OutOfOffice"] == 1


def test_doubled_quote_and_unknown_flag_and_short_row() -> None:
    out = OutOfOfficeMapper({}).map(
        {"OutOfOfficeEntriesCSVString": 'UserSearch,Enabled,EndTime\n"say ""hi""",maybe\n'}
    )
    assert out["OutOfOfficeEntries"] == [_entry(UserSearch='say "hi"', OutOfOffice="maybe")]


def test_explicit_columns_win_over_aliases() -> None:
    csv_string = (
        "UserEmail,EMail,StartDate,StartTime,OutOfOffice,Enabled\n"
        "a@example.org,b@example.org,2026-01-01,02.02.2026 00:00:00,yes,no\n"
    )
    entry = OutOfOfficeMapper({}).map({"OutOfOfficeEntriesCSVString": csv_string})[
        "OutOfOfficeEntries"
    ][0]
    assert entry["UserEmail"] == "a@example.org"
    assert entry["StartDate"] == "2026-01-01"
    assert entry["OutOfOffice"] == 1


def test_german_date_is_unanchored_like_perl() -> None:
    # Perl's regex is unanchored and the replacement swaps the whole value.
    csv_string = "UserLogin,StartDate\njdoe,ab01.03.2026 10:00:00xy\n"
    entry = OutOfOfficeMapper({}).map({"OutOfOfficeEntriesCSVString": csv_string})[
        "OutOfOfficeEntries"
    ][0]
    assert entry["StartDate"] == "2026-03-01"


def test_date_without_time_is_left_alone() -> None:
    csv_string = "UserLogin,StartDate\njdoe,01.03.2026\n"
    entry = OutOfOfficeMapper({}).map({"OutOfOfficeEntriesCSVString": csv_string})[
        "OutOfOfficeEntries"
    ][0]
    assert entry["StartDate"] == "01.03.2026"


def test_blank_rows_skipped_and_quoted_newline_kept() -> None:
    csv_string = 'UserSearch,Enabled\n\n"a\nb",yes\n'
    out = OutOfOfficeMapper({}).map({"OutOfOfficeEntriesCSVString": csv_string})
    assert [e["UserSearch"] for e in out["OutOfOfficeEntries"]] == ["a\nb"]


def test_registered_in_factories() -> None:
    assert set(MAPPER_FACTORIES) == {"Simple", "XSLT", "OutOfOffice"}
    mapper = build_mapper({"Type": "OutOfOffice"})
    out = mapper.map({"OutOfOfficeEntriesCSVString": "UserLogin,Enabled\njdoe,yes\n"})
    assert out["OutOfOfficeEntries"] == [_entry(UserLogin="jdoe", OutOfOffice=1)]
