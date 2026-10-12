"""Safe Perl data-literal parser (``tiqora.znuny.perl_data``, step 0).

One grammar for itsm1 (class definitions, I4), cmdb (definition import) and itsm-import (D4):
nested ``[ ]`` / ``{ }``, ``=>`` and ``,`` (trailing commas allowed), single/double-quoted strings,
barewords as hash keys, integers/decimals, ``undef``, ``#`` comments. Everything else is rejected
with a :class:`PerlDataError` carrying the offset — the parser never evaluates anything.
"""

from __future__ import annotations

import pytest

from tiqora.znuny.perl_data import PerlDataError, parse_perl_data

# Shaped like ITSMConfigurationManagement's default "Computer" class definition (Znuny 6.5,
# configitem_definition.configitem_definition): hand-written Perl with trailing commas, nested
# Input hashes, CountMin/CountMax/CountDefault and a Sub list.
_COMPUTER_DEFINITION = r"""
[
    {
        Key        => 'Vendor',
        Name       => 'Vendor',
        Searchable => 1,
        Input      => {
            Type      => 'Text',
            Size      => 50,
            MaxLength => 50,
        },
    },
    {
        Key   => 'Description',
        Name  => 'Description',
        Searchable => 1,
        Input => {
            Type => 'TextArea',
        },
    },
    {
        Key        => 'Type',
        Name       => 'Type',
        Searchable => 1,
        Input      => {
            Type        => 'GeneralCatalog',
            Class       => 'ITSM::ConfigItem::Computer::Type',
            Translation => 1,
        },
    },
    {
        Key   => 'NIC',
        Name  => 'Network Adapter',
        Input => {
            Type      => 'Text',
            Size      => 50,
            MaxLength => 100,
            Required  => 1,
        },
        CountMin     => 0,
        CountMax     => 10,
        CountDefault => 1,
        Sub          => [
            {
                Key   => 'IPoverDHCP',
                Name  => 'IP over DHCP',
                Input => {
                    Type        => 'GeneralCatalog',
                    Class       => 'ITSM::ConfigItem::YesNo',
                    Translation => 1,
                    Required    => 1,
                },
            },
            {
                Key   => 'IPAddress',
                Name  => 'IP Address',
                Searchable => 1,
                Input => {
                    Type      => 'Text',
                    Size      => 40,
                    MaxLength => 40,
                    # IPv4 only, as in the shipped definition
                    RegEx     => '^\d{1,3}(\.\d{1,3}){3}$',
                    RegExErrorMessage => 'Value doesn\'t look like an IP address',
                },
                CountMin     => 0,
                CountMax     => 20,
                CountDefault => 0,
            },
        ],
    },
    {
        Key   => 'WarrantyExpirationDate',
        Name  => 'Warranty Expiration Date',
        Input => {
            Type            => 'Date',
            YearPeriodPast  => 20,
            YearPeriodFuture => 10,
            ValueDefault    => undef,
        },
    },
    { Key => 'Empty', Name => 'Empty', Input => {}, Sub => [], },
];
"""


def test_znuny_computer_definition() -> None:
    # a definition ends with ';' when copied out of a .pm file: not part of the literal
    data = parse_perl_data(_COMPUTER_DEFINITION.strip().removesuffix(";"))
    assert isinstance(data, list) and len(data) == 6
    assert data[0] == {
        "Key": "Vendor",
        "Name": "Vendor",
        "Searchable": 1,
        "Input": {"Type": "Text", "Size": 50, "MaxLength": 50},
    }
    assert data[2]["Input"]["Class"] == "ITSM::ConfigItem::Computer::Type"
    nic = data[3]
    assert (nic["CountMin"], nic["CountMax"], nic["CountDefault"]) == (0, 10, 1)
    assert [s["Key"] for s in nic["Sub"]] == ["IPoverDHCP", "IPAddress"]
    ip = nic["Sub"][1]["Input"]
    # single quotes keep backslashes except before \ and '
    assert ip["RegEx"] == r"^\d{1,3}(\.\d{1,3}){3}$"
    assert ip["RegExErrorMessage"] == "Value doesn't look like an IP address"
    assert data[4]["Input"]["ValueDefault"] is None
    assert data[5] == {"Key": "Empty", "Name": "Empty", "Input": {}, "Sub": []}


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        # itsm-import D4 vectors
        (
            "[ { Key => 'Vendor', Name => 'Vendor', Searchable => 1, Input => { Type => 'Text',"
            " Size => 50, MaxLength => 50, }, }, ]",
            [
                {
                    "Key": "Vendor",
                    "Name": "Vendor",
                    "Searchable": 1,
                    "Input": {"Type": "Text", "Size": 50, "MaxLength": 50},
                }
            ],
        ),
        (
            "{ a => undef, 'b c' => \"x\\ty\", d => -1.5 } # trailing comment",
            {"a": None, "b c": "x\ty", "d": -1.5},
        ),
        ("[ 'it\\'s', \"q\\\"\" ]", ["it's", 'q"']),
        # more of the grammar
        ("[]", []),
        ("{}", {}),
        ("[1,2,3,]", [1, 2, 3]),
        ("[ 0, -7, 3.25, 10 ]", [0, -7, 3.25, 10]),
        ("{ 1 => 'one', -2 => 'minus' }", {"1": "one", "-2": "minus"}),
        ("{ Foo::Bar => 1, _x9 => 2 }", {"Foo::Bar": 1, "_x9": 2}),
        ("[ \"a\\\\b\", 'a\\\\b', 'a\\nb', \"a\\nb\" ]", ["a\\b", "a\\b", "a\\nb", "a\nb"]),
        ('[ "it\\\'s" ]', ["it's"]),
        ("# leading comment\n[ 'x', # inline\n 'y' ]\n# end", ["x", "y"]),
        ("[ 'Grüße', \"ümlaut\" ]", ["Grüße", "ümlaut"]),
        ("'top-level scalar'", "top-level scalar"),
        ("undef", None),
        ("{ a => 1, a => 2 }", {"a": 2}),  # Perl: the last key wins
        ("[ [ [ ] ], { x => [ { } ] } ]", [[[]], {"x": [{}]}]),
    ],
)
def test_parses_the_subset(src: str, expected: object) -> None:
    assert parse_perl_data(src) == expected


def test_integers_are_int_and_decimals_float() -> None:
    data = parse_perl_data("[ 5, 5.0 ]")
    assert type(data[0]) is int and type(data[1]) is float


@pytest.mark.parametrize(
    "snippet",
    [
        # itsm1 I4 vectors
        "sub { 1 }",
        "qw(a b)",
        "do { }",
        "eval '1'",
        "$x",
        "@a",
        "%h",
        "\\'x'",
        "`ls`",
        # itsm-import D4 vectors
        "system('id')",
        '[ "$ENV{HOME}" ]',
        "[ @list ]",
        "{ a => sub { 1 } }",
        "[ 1, 2",
        # more rejections
        '[ "user@example.org" ]',  # array interpolation inside double quotes
        '[ "a\\x41" ]',  # escapes outside the documented set
        "{ a => bareword }",  # only undef is a bareword value
        "[ foo ]",
        "{ a => 1 b => 2 }",  # missing separator
        "{ a, 1 }",  # hash pairs need =>
        "[ 1 => 2 ]",  # => only inside hashes
        "[ 'a' . 'b' ]",
        "[ 1 ] [ 2 ]",  # trailing garbage
        "[ 1 ];",
        "[ 007 ]",  # Perl octal literal
        "[ 1e5 ]",
        "[ 0x10 ]",
        "{ a => 1",
        "'unterminated",
        '"unterminated',
        "",
        "   # only a comment",
        "{ [1] => 2 }",
        "[ ,1 ]",
        "[ 1,, 2 ]",
        "q{x}",
        "<STDIN>",
        "[ -'x' ]",
    ],
)
def test_code_and_unknown_syntax_rejected(snippet: str) -> None:
    with pytest.raises(PerlDataError):
        parse_perl_data(snippet)


def test_error_carries_position_and_message() -> None:
    with pytest.raises(PerlDataError) as exc:
        parse_perl_data("[ 1, $x ]")
    assert exc.value.position == 5
    assert exc.value.message
    assert "5" in str(exc.value)
    assert isinstance(exc.value, ValueError)


def test_depth_limit() -> None:
    with pytest.raises(PerlDataError, match="depth"):
        parse_perl_data("[" * 100 + "]" * 100)
    assert parse_perl_data("[" * 64 + "]" * 64) is not None
    with pytest.raises(PerlDataError, match="depth"):
        parse_perl_data("[" * 65 + "]" * 65)
    assert parse_perl_data("[[[]]]", max_depth=3) == [[[]]]
    with pytest.raises(PerlDataError, match="depth"):
        parse_perl_data("[[[[]]]]", max_depth=3)


def test_node_limit() -> None:
    src = "[" + ",".join("1" for _ in range(50)) + "]"
    assert len(parse_perl_data(src)) == 50
    with pytest.raises(PerlDataError, match="nodes"):
        parse_perl_data(src, max_nodes=20)


def test_oversized_input_rejected() -> None:
    with pytest.raises(PerlDataError):
        parse_perl_data("'" + "x" * (1024 * 1024 + 1) + "'")


def test_large_flat_input_is_linear() -> None:
    # 100k elements parse without recursion problems or quadratic behaviour
    src = "[" + ",".join(str(i) for i in range(100_000)) + "]"
    assert parse_perl_data(src)[-1] == 99_999
