"""Znuny stats parsing: XMLHash tokenizer, export files, ``xml_storage`` reader (plan Task 2).

Pure tests plus one DB test on both dialects. Band 52020-52039: ``xml_storage`` rows with
``xml_type = 'Stats'`` and keys 52022 / 52023 / 52024 (broken) / ``rep52x`` (non-numeric),
deleted again by explicit key list before and after.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.reporting.znuny_stats.model import stat_from_xml_hash
from tiqora.reporting.znuny_stats.reader import list_stat_ids, load_stats
from tiqora.reporting.znuny_stats.xml_hash import (
    MAX_FILE_BYTES,
    MAX_STAT_ROWS,
    XmlHashError,
    build_xml_hash,
    parse_content_key,
    xml_file_to_hash,
)

FIX = Path(__file__).parent / "fixtures" / "znuny_stats"
BASE = "[1]{'otrs_stats'}[1]"


def _file_stat(name: str):
    return stat_from_xml_hash(
        xml_file_to_hash((FIX / name).read_bytes()), origin="file", stat_id=None
    )


def test_parse_content_key() -> None:
    assert parse_content_key("[1]{'otrs_stats'}[1]{'Title'}[1]{'Content'}") == [
        1,
        "otrs_stats",
        1,
        "Title",
        1,
        "Content",
    ]
    assert parse_content_key("[1]{'a\\'b'}[2]{'c\\\\d'}") == [1, "a'b", 2, "c\\d"]
    for bad in ("", "[x]", "{'a'}[1", "[1]{a}", "[1]{'a'}junk", "[-1]", "[1]{'a\\x'}"):
        with pytest.raises(XmlHashError):
            parse_content_key(bad)


def test_values_with_quotes_backslashes_newlines() -> None:
    rows = [
        ("[1]{'otrs_stats'}[1]{'Title'}[1]{'Content'}", 'It\'s a \\ "test"\nline 2'),
        (
            "[1]{'otrs_stats'}[1]{'ObjectModule'}[1]{'Content'}",
            "Kernel::System::Stats::Dynamic::Ticket",
        ),
        ("[1]{'otrs_stats'}[1]{'StatType'}[1]{'Content'}", "dynamic"),
        ("[1]{'otrs_stats'}[1]{'Description'}[1]{'Content'}", None),
    ]
    stat = stat_from_xml_hash(build_xml_hash(rows), origin="db", stat_id=52020)
    assert stat.title == 'It\'s a \\ "test"\nline 2'
    assert stat.description == ""
    assert stat.import_key == "znuny.stat.52020"


def test_elements_from_db_rows() -> None:
    def k(tag: str, n: int, leaf: str) -> str:
        return f"{BASE}{{'{tag}'}}[{n}]{{'{leaf}'}}"

    rows = [
        (f"{BASE}{{'Title'}}[1]{{'Content'}}", "rep52 Matrix"),
        (f"{BASE}{{'ObjectModule'}}[1]{{'Content'}}", "Kernel::System::Stats::Dynamic::Ticket"),
        (f"{BASE}{{'StatType'}}[1]{{'Content'}}", "dynamic"),
        (f"{BASE}{{'Permission'}}[1]{{'Content'}}", "3"),
        (f"{BASE}{{'Permission'}}[2]{{'Content'}}", "7"),
        # rows arrive unordered: the restriction first, the x axis last
        (k("UseAsRestriction", 1, "Element"), "QueueIDs"),
        (k("UseAsRestriction", 1, "Fixed"), "0"),
        (
            f"{BASE}{{'UseAsRestriction'}}[1]{{'SelectedValues'}}[2]{{'Content'}}",
            "5",
        ),
        (
            f"{BASE}{{'UseAsRestriction'}}[1]{{'SelectedValues'}}[1]{{'Content'}}",
            "4",
        ),
        (k("UseAsXvalue", 1, "Element"), "CreateTime"),
        (k("UseAsXvalue", 1, "Fixed"), "1"),
        (k("UseAsXvalue", 1, "TimeRelativeUnit"), "Month"),
        (k("UseAsXvalue", 1, "TimeRelativeCount"), "12"),
        (
            f"{BASE}{{'UseAsXvalue'}}[1]{{'SelectedValues'}}[1]{{'Content'}}",
            "Month",
        ),
    ]
    stat = stat_from_xml_hash(build_xml_hash(rows), origin="db", stat_id=52021)
    assert stat.permission == ("3", "7")
    x, r = stat.elements
    assert (x.use, x.element, x.fixed, x.selected) == ("x", "CreateTime", True, ("Month",))
    assert dict(x.time) == {"TimeRelativeUnit": "Month", "TimeRelativeCount": "12"}
    assert (r.use, r.element, r.fixed, r.selected) == ("restriction", "QueueIDs", False, ("4", "5"))


def test_row_cap_and_non_ascii_digit_key() -> None:
    rows = [(f"{BASE}{{'Foo'}}[{i}]{{'Content'}}", "x") for i in range(1, MAX_STAT_ROWS + 2)]
    with pytest.raises(XmlHashError):
        build_xml_hash(rows)
    with pytest.raises(XmlHashError):
        parse_content_key("[1]{'a'}[\u00b2]{'b'}")


def test_conflicting_paths_raise() -> None:
    with pytest.raises(XmlHashError):
        build_xml_hash([("[1]{'a'}[1]{'b'}", "x"), ("[1]{'a'}{'b'}", "y")])
    with pytest.raises(XmlHashError):
        build_xml_hash([("[1]{'a'}[1]{'b'}", "x"), ("[1]{'a'}[1]{'b'}[1]{'c'}", "y")])
    with pytest.raises(XmlHashError):
        build_xml_hash([("[1]{'a'}[1]{'b'}[1]{'c'}", "y"), ("[1]{'a'}[1]{'b'}", "x")])


def test_missing_required_fields_raise() -> None:
    with pytest.raises(XmlHashError):
        stat_from_xml_hash([None], origin="db", stat_id=1)
    only_title = build_xml_hash([(f"{BASE}{{'Title'}}[1]{{'Content'}}", "x")])
    with pytest.raises(XmlHashError):
        stat_from_xml_hash(only_title, origin="db", stat_id=1)


def test_export_files_parse() -> None:
    overview = _file_stat("Stats.TicketOverview.de.xml")
    assert overview.title.startswith("Überblick")
    assert overview.sum_row and overview.sum_col and overview.permission == ("stats",)
    assert [(e.use, e.element) for e in overview.elements] == [
        ("x", "StateIDs"),
        ("series", "QueueIDs"),
    ]
    assert overview.formats == ("Print", "D3::BarChart")
    lst = _file_stat("Stats.TestTicketList.en.xml")
    assert lst.object_module.endswith("::TicketList") and lst.import_key.startswith("znuny.file.")
    assert lst.import_key == _file_stat("Stats.TestTicketList.en.xml").import_key
    assert [e.selected for e in lst.elements if e.use == "x"] == [
        ("TicketNumber", "Title", "Queue")
    ]


def test_made_up_fixtures() -> None:
    m = _file_stat("matrix_time_axis.xml")
    x, s, r = m.elements
    assert (x.element, x.fixed, x.selected) == ("CreateTime", True, ("Month",))
    assert dict(x.time) == {
        "TimeRelativeCount": "12",
        "TimeRelativeUnit": "Month",
        "TimeRelativeUpcomingCount": "1",
        "TimeScaleCount": "1",
    }
    assert (s.use, s.element, s.fixed) == ("series", "QueueIDs", False)
    assert (r.use, r.element, r.selected) == ("restriction", "StateTypeIDs", ("1", "2"))
    assert m.sum_col and not m.sum_row and m.time_zone == "Europe/Berlin"
    assert m.formats == ("Print", "D3::LineChart")

    a = _file_stat("accounted_time.xml")
    assert a.object_module.endswith("::TicketAccountedTime")
    ax = next(e for e in a.elements if e.use == "x")
    assert ax.selected == ("TotalTime", "NumberOfTickets", "TicketMinTime")
    ar = next(e for e in a.elements if e.use == "restriction")
    assert dict(ar.time) == {"TimeStart": "2026-09-01 00:00:00", "TimeStop": "2026-09-30 23:59:59"}

    sr = _file_stat("solution_response.xml")
    assert sr.object_module.endswith("::TicketSolutionResponseTime")
    assert next(e for e in sr.elements if e.element == "CloseTime2").use == "restriction"
    assert len(next(e for e in sr.elements if e.use == "x").selected) == 6

    st = _file_stat("static_state_action.xml")
    assert (st.stat_type, st.static_file, st.elements) == ("static", "StateAction", ())

    un = _file_stat("unsupported.xml")
    assert not un.valid and un.show_as_dashboard_widget
    assert [e.element for e in un.elements if e.use == "restriction"] == [
        "DynamicField_Kundenwert",
        "EscalationTime",
    ]
    assert next(e for e in un.elements if e.use == "x").selected == ("HalfYear",)
    assert next(e for e in un.elements if e.use == "x").time["TimeRelativeUpcomingCount"] == "3"


def test_file_rejects_entities_and_wrong_root() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
        b"<otrs_stats><Title>&a;</Title></otrs_stats>"
    )
    with pytest.raises(XmlHashError):
        xml_file_to_hash(bomb)
    with pytest.raises(XmlHashError):
        xml_file_to_hash(b"<foo/>")
    with pytest.raises(XmlHashError):
        xml_file_to_hash(b"not xml at all")
    with pytest.raises(XmlHashError):
        xml_file_to_hash(b"<otrs_stats>" + b"x" * (MAX_FILE_BYTES + 1) + b"</otrs_stats>")
    deep = b"<otrs_stats>" + b"<a>" * 40 + b"</a>" * 40 + b"</otrs_stats>"
    with pytest.raises(XmlHashError):
        xml_file_to_hash(deep)


# ------------------------------------------------------------------ DB reader

LO_KEYS = ("52022", "52023", "52024", "52025", "rep52x")


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _delete(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM xml_storage WHERE xml_type = 'Stats' AND xml_key IN :k").bindparams(
                bindparam("k", expanding=True)
            ),
            {"k": list(LO_KEYS)},
        )
    engine.dispose()


def _seed(sync_url: str) -> None:
    ins = text(
        "INSERT INTO xml_storage (xml_type, xml_key, xml_content_key, xml_content_value) "
        "VALUES ('Stats', :k, :ck, :v)"
    )
    mod = "Kernel::System::Stats::Dynamic::Ticket"
    rows: list[dict[str, str | None]] = []
    for key, title in (("52022", "rep52 It's one"), ("52023", "rep52 two")):
        rows += [
            {"k": key, "ck": f"{BASE}{{'Title'}}[1]{{'Content'}}", "v": title},
            {"k": key, "ck": f"{BASE}{{'ObjectModule'}}[1]{{'Content'}}", "v": mod},
            {"k": key, "ck": f"{BASE}{{'UseAsXvalue'}}[1]{{'Element'}}", "v": "StateIDs"},
            {"k": key, "ck": f"{BASE}{{'Description'}}[1]{{'Content'}}", "v": None},
        ]
    rows.append({"k": "52022", "ck": f"{BASE}{{'Permission'}}[1]{{'Content'}}", "v": "7"})
    rows.append({"k": "52023", "ck": f"{BASE}{{'Permission'}}[1]{{'Content'}}", "v": "8"})
    rows += [
        {"k": "52025", "ck": f"{BASE}{{'Foo'}}[{i}]{{'Content'}}", "v": "x"}
        for i in range(1, MAX_STAT_ROWS + 2)
    ]
    rows += [
        {"k": "52024", "ck": f"{BASE}{{'Title'}}[1]{{'Content'}}", "v": "rep52 broken"},
        {"k": "52024", "ck": "[1]{'otrs_stats'", "v": "x"},
        {"k": "rep52x", "ck": f"{BASE}{{'Title'}}[1]{{'Content'}}", "v": "rep52 non-numeric"},
    ]
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for row in rows:
            conn.execute(ins, row)
    engine.dispose()


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_load_stats_from_xml_storage(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _delete(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        _seed(sync_url)
        async with factory() as session:
            ids = await list_stat_ids(session)
            assert {52022, 52023, 52024} <= set(ids)
            assert ids == sorted(ids)
            stats, errors, truncated = await load_stats(session, [52022, 52023, 52024])
            assert truncated is False
            only_one = await list_stat_ids(session, permission_group_ids=[7])
            assert 52022 in only_one and 52023 not in only_one and "rep52x" not in only_one
            assert await list_stat_ids(session, permission_group_ids=[]) == []
            _s, err_budget, trunc = await load_stats(session, [52022, 52023], row_budget=4)
            assert trunc is True and _s == [] and err_budget == []
            part, _e, trunc = await load_stats(session, [52022, 52023], row_budget=5)
            assert [s.stat_id for s in part] == [52022] and trunc is True
            _s, err_big, _t = await load_stats(session, [52025])
            assert [e.message for e in err_big] == ["Die Statistik ist zu groß, um sie zu lesen."]
            _s, err_missing, _t = await load_stats(session, [52029])
            assert "nicht gefunden" in err_missing[0].message
        assert [(s.stat_id, s.origin, s.import_key) for s in stats] == [
            (52022, "db", "znuny.stat.52022"),
            (52023, "db", "znuny.stat.52023"),
        ]
        assert stats[0].title == "rep52 It's one" and stats[0].description == ""
        assert [(e.stat_id, bool(e.message)) for e in errors] == [(52024, True)]
        assert "beschädigt" in errors[0].message
    finally:
        _delete(sync_url)
        await engine.dispose()
