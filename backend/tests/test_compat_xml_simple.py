"""XML::Simple data<->XML conversion (port of the options Mapping::XSLT uses)."""

from __future__ import annotations

from typing import Any

import pytest
from lxml import etree

from tiqora.api.compat.mapping import MappingError
from tiqora.api.compat.mapping.xml_simple import XmlSimpleError, xml_in, xml_out


def test_xml_out_sorted_nested_lists() -> None:
    out = xml_out({"b": "2", "a": {"y": ["1", "2"], "x": None}, "-skip": "z", "e": {}})
    tree = etree.fromstring(out.encode())
    assert tree.tag == "RootElement"
    assert [c.tag for c in tree] == ["a", "b", "e"]
    a = tree.find("a")
    assert a is not None
    assert [c.tag for c in a] == ["x", "y", "y"]
    x, e = tree.find("a/x"), tree.find("e")
    assert x is not None and x.text is None
    assert e is not None and e.text is None


def test_xml_out_escapes_only_four_chars() -> None:
    assert "<k>a &amp; &lt;b&gt; &quot;c&quot; 'd'</k>" in xml_out({"k": "a & <b> \"c\" 'd'"})


def test_xml_out_list_in_list_uses_anon() -> None:
    out = xml_out({"k": [["1", "2"]]}).replace("\n", "").replace(" ", "")
    assert "<k><anon>1</anon><anon>2</anon></k>" in out


def test_xml_out_matches_perl_layout() -> None:
    assert xml_out({"a": {"b": "1"}, "c": ""}) == (
        "<RootElement>\n  <a>\n    <b>1</b>\n  </a>\n  <c></c>\n</RootElement>\n"
    )


def test_xml_out_empty_list_emits_nothing_and_ints_stringify() -> None:
    out = xml_out({"a": [], "n": 5})
    assert [c.tag for c in etree.fromstring(out.encode())] == ["n"]
    assert "<n>5</n>" in out


@pytest.mark.parametrize("bad", [["a", "b"], "text", None])
def test_xml_out_rejects_non_dict_root(bad: Any) -> None:
    with pytest.raises(XmlSimpleError, match="Could not convert data from Perl to XML"):
        xml_out(bad)


def test_xml_simple_error_is_a_mapping_error() -> None:
    assert issubclass(XmlSimpleError, MappingError)


@pytest.mark.parametrize(
    ("xml", "force", "expected"),
    [
        (b"<R><a>1</a></R>", False, {"a": "1"}),
        (b"<R><a>1</a><a>2</a></R>", False, {"a": ["1", "2"]}),
        (b"<R><a>1</a></R>", frozenset({"a"}), {"a": ["1"]}),
        (b"<R><a>1</a></R>", True, {"a": ["1"]}),
        (b"<R><a></a></R>", False, {"a": {}}),
        (b"<R><a>  </a></R>", False, {"a": {}}),
        (b"<R><a> x </a></R>", False, {"a": " x "}),  # not trimmed
        (
            b"<R><a><b>1</b></a><a><b>2</b></a></R>",
            False,
            {"a": [{"b": "1"}, {"b": "2"}]},
        ),
        (b"<R><a>t<b>1</b></a></R>", False, {"a": {"content": "t", "b": "1"}}),
        (b"<R><anon>1</anon><anon>2</anon></R>", False, ["1", "2"]),
        (b"<R><anon>1</anon></R>", False, {"anon": "1"}),
        (b"<R>only text</R>", False, "only text"),
        (b"<R><a>x<!-- c -->y</a></R>", False, {"a": "xy"}),
    ],
)
def test_xml_in(xml: bytes, force: Any, expected: Any) -> None:
    assert xml_in(xml, force_array=force) == expected


def test_xml_in_keep_attributes() -> None:
    assert xml_in(b'<R><a id="7">x</a></R>', keep_attributes=True) == {
        "a": {"id": "7", "content": "x"}
    }
    assert xml_in(b"<R><a>x</a></R>", keep_attributes=True) == {"a": {"content": "x"}}
    assert xml_in(b'<R><a id="7"/></R>', keep_attributes=True) == {"a": {"id": "7"}}


def test_xml_in_ignores_attributes_by_default() -> None:
    assert xml_in(b'<R><a id="7">x</a></R>') == {"a": "x"}


def test_round_trip_single_element_list_collapses() -> None:  # the classic trap, Znuny behaviour
    assert xml_in(xml_out({"L": [{"k": "v"}]}).encode()) == {"L": {"k": "v"}}
    assert xml_in(xml_out({"L": [{"k": "v"}, {"k": "w"}]}).encode()) == {
        "L": [{"k": "v"}, {"k": "w"}]
    }


def test_round_trip_empty_string_becomes_empty_dict() -> None:
    assert xml_in(xml_out({"Key": ""}).encode()) == {"Key": {}}


def test_round_trip_list_in_list() -> None:
    assert xml_in(xml_out({"k": [["1", "2"], ["3", "4"]]}).encode()) == {
        "k": [["1", "2"], ["3", "4"]]
    }


def test_round_trip_special_chars_and_umlauts() -> None:
    value = 'Grüße & <€> "q"'
    assert xml_in(xml_out({"k": value}).encode()) == {"k": value}


def test_xml_in_rejects_malformed_xml() -> None:
    with pytest.raises(XmlSimpleError, match="Could not convert data from XML to Perl"):
        xml_in(b"<R><a></R>")


def test_xml_in_rejects_entities() -> None:
    evil = b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><R><a>&x;</a></R>'
    result = xml_in(evil)
    assert "root:" not in str(result)


def test_xml_in_does_not_expand_internal_entities() -> None:
    xml = b'<!DOCTYPE r [<!ENTITY y "EXPANDED">]><R><a>&y;</a></R>'
    assert "EXPANDED" not in str(xml_in(xml))
