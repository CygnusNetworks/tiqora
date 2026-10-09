"""Port of Znuny's XSLT.t (12 cases) and XSLTRegExp.t (5 cases) plus sandbox checks."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tiqora.api.compat.mapping import MappingConfigError, MappingError, build_mapper
from tiqora.api.compat.mapping import xslt as xslt_module
from tiqora.api.compat.mapping.otrs_functions import OTRS_FUNCTIONS_XSL
from tiqora.api.compat.mapping.xslt import XsltMapper, prepare_template, regex_recursion

OTRS_HREF = "/opt/otrs/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl"
_XSL = 'xmlns:xsl="http://www.w3.org/1999/XSL/Transform"'

IDENTITY = (
    '<?xml version="1.0" encoding="UTF-8"?><xsl:transform version="1.0" '
    'xmlns:xsl="http://www.w3.org/1999/XSL/Transform"><xsl:template match="@*|node()">'
    '<xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy></xsl:template></xsl:transform>'
)

CUSTOM_FUNCTIONS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
 xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
 xmlns:otrs="http://otrs.org"
 extension-element-prefixes="otrs">
<xsl:import href="__HREF__" />
<xsl:output method="xml" encoding="utf-8" indent="yes"/>
<xsl:template match="/RootElement">
<NewRootElement>
    <xsl:for-each select="/RootElement/Structure1/Array1">
    <FirstLevelArray>
        <xsl:text>Amended</xsl:text>
        <xsl:value-of select="." />
    </FirstLevelArray>
    </xsl:for-each>
    <NewStructure>
        <DateFromISO>
            <xsl:variable name="dateiso" select="/RootElement/DateISO" />
            <xsl:value-of select="otrs:date-iso-to-xsd($dateiso)" />
        </DateFromISO>
        <DateToISO>
            <xsl:variable name="datexsd" select="/RootElement/DateXSD" />
            <xsl:value-of select="otrs:date-xsd-to-iso($datexsd)" />
        </DateToISO>
        <NewKey1>
            <xsl:value-of select="/RootElement/Key1" />
        </NewKey1>
        <NewKey2>
            <xsl:value-of select="/RootElement/Structure1/Key2" />
        </NewKey2>
    </NewStructure>
</NewRootElement>
</xsl:template>
</xsl:stylesheet>"""

ARRAY_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
 xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
 xmlns:otrs="http://otrs.org"
 extension-element-prefixes="otrs">
<xsl:import href="__HREF__" />
<xsl:output method="xml" encoding="utf-8" indent="yes"/>
<xsl:template match="/RootElement">
<NewRootElement>
    <xsl:for-each select="/RootElement/Array1">
    <FirstLevelArray>
        <xsl:text>Amended</xsl:text>
        <xsl:value-of select="." />
    </FirstLevelArray>
    </xsl:for-each>
</NewRootElement>
</xsl:template>
</xsl:stylesheet>""".replace("__HREF__", OTRS_HREF)

DATA_INCLUDE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
 xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
 xmlns:otrs="http://otrs.org"
 extension-element-prefixes="otrs">
<xsl:import href="__HREF__" />
<xsl:output method="xml" encoding="utf-8" indent="yes"/>
<xsl:template match="/RootElement">
<NewRootElement>
    <DataInclude>
        <xsl:value-of select="/RootElement/DataInclude/RequesterResponseMapOutput/Value" />
    </DataInclude>
    <DataInclude>
        <xsl:value-of select="/RootElement/DataInclude/RequesterRequestInput/Value" />
    </DataInclude>
</NewRootElement>
</xsl:template>
</xsl:stylesheet>""".replace("__HREF__", OTRS_HREF)


def _simple_template(body: str, output: str = 'method="xml" encoding="utf-8" indent="yes"') -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<xsl:stylesheet version="1.0" {_XSL}>\n'
        f"<xsl:output {output}/>\n"
        f'<xsl:template match="/RootElement">\n{body}\n</xsl:template>\n</xsl:stylesheet>'
    )


def _import_template(href: str) -> str:
    """The XSLT.t "Test replacement with custom functions" template, $Home path replaced by *href*."""  # noqa: E501
    return CUSTOM_FUNCTIONS_TEMPLATE.replace("__HREF__", href)


_PASS_THROUGH = IDENTITY
_RANDOM = "4711"


@dataclass(frozen=True)
class Case:
    name: str
    config: dict[str, Any]
    data: Any
    result: Any
    result_success: bool
    config_success: bool = True
    data_include: dict[str, Any] | None = None
    marks: tuple[Any, ...] = field(default=())


CASES = [
    # ---- XSLT.t ----
    Case("invalid xml", {"Template": "this is no xml"}, {"Key": "Value"}, None, False),
    Case(
        "no xslt",
        {"Template": '<?xml version="1.0" encoding="UTF-8"?>\n<valid-xml-but-no-xslt/>'},
        {"Key": "Value"},
        None,
        False,
    ),
    Case(
        "invalid xslt",
        {"Template": _simple_template("", 'method="html" encoding="utf-8" indent="yes"')},
        {"Key": "Value"},
        None,
        False,
    ),
    Case("empty data", {"Template": ""}, None, {}, True),
    Case("empty config", {}, {"Key": "Value"}, {"Key": "Value"}, True, config_success=False),
    Case(
        "invalid hash key name",
        {"Template": _simple_template("<NewKey>NewValue</NewKey>")},
        {"<Key>": "Value"},
        None,
        False,
    ),
    Case(
        "invalid replacement",
        {
            "Template": _simple_template(
                "<Wrapper><Level1><Level2><Level3>Value</Level1></Level2></Level3></Wrapper>"
            )
        },
        {"Key": "Value"},
        None,
        False,
    ),
    Case(
        "array as data",  # Perl flattened the list into the scalar "ArrayPart1"
        {"Template": ARRAY_TEMPLATE},
        "ArrayPart1",
        None,
        False,
    ),
    Case(
        "simple overwrite",
        {
            "Template": _simple_template(
                "<NewRootElement><NewKey>NewValue</NewKey></NewRootElement>"
            )
        },
        {"Key": "Value"},
        {"NewKey": "NewValue"},
        True,
    ),
    Case(
        "simple overwrite (whitespaces trimmed)",
        {
            "Template": _simple_template(
                "<NewRootElement><NewKey>\n        NewValue\n</NewKey></NewRootElement>"
            )
        },
        {"Key": "Value"},
        {"NewKey": "NewValue"},
        True,
    ),
    Case(
        "replacement with custom functions",
        {"Template": _import_template(OTRS_HREF)},
        {
            "DateISO": "2010-12-31 23:58:59",
            "DateXSD": "2011-11-30T22:57:58Z",
            "Key1": "Value1",
            "Structure1": {"Array1": ["ArrayPart1", "ArrayPart2", "ArrayPart3"], "Key2": "Value2"},
        },
        {
            "FirstLevelArray": ["AmendedArrayPart1", "AmendedArrayPart2", "AmendedArrayPart3"],
            "NewStructure": {
                "DateFromISO": "2010-12-31T23:58:59Z",
                "DateToISO": "2011-11-30 22:57:58",
                "NewKey1": "Value1",
                "NewKey2": "Value2",
            },
        },
        True,
    ),
    Case(
        "DataInclude functionality",
        {
            "DataInclude": ["RequesterRequestInput", "RequesterResponseMapOutput"],
            "Template": DATA_INCLUDE_TEMPLATE,
        },
        {"Key": "Value"},
        {"DataInclude": ["3", "1"]},
        True,
        data_include={
            "RequesterRequestInput": {"Value": 1},
            "RequesterRequestPrepareOutput": {"Value": 2},
            "RequesterResponseMapOutput": {"Value": 3},
        },
    ),
    # ---- XSLTRegExp.t ----
    Case(
        "RegExp (Pre RegExp)",
        {
            "PreRegExFilter": [{"Search": r"^(\D*)ID$", "Replace": "$1Number"}],
            "Template": _PASS_THROUGH,
        },
        {"TicketID": _RANDOM},
        {"TicketNumber": _RANDOM},
        True,
    ),
    Case(
        "RegExp (Post RegExp)",
        {
            "PostRegExFilter": [{"Search": r"^(\D*)ID$", "Replace": "$1Number"}],
            "Template": _PASS_THROUGH,
        },
        {"TicketID": _RANDOM},
        {"TicketNumber": _RANDOM},
        True,
    ),
    Case(
        "RegExp (Pre RegExp + Post RegExp)",
        {
            "PreRegExFilter": [{"Search": r"^(\D*)_(\D*)$", "Replace": "$1$2"}],
            "PostRegExFilter": [{"Search": r"^(Queue)Name$", "Replace": "$1ID"}],
            "Template": _PASS_THROUGH,
        },
        {"Ticket_Number": _RANDOM, "QueueName": _RANDOM},
        {"TicketNumber": _RANDOM, "QueueID": _RANDOM},
        True,
    ),
    Case(
        "RegExp complex (Post RegExp)",
        {
            "PostRegExFilter": [
                {"Search": r"^(\D*)(\d)$", "Replace": "$1_$2"},
                {"Search": r"^_(\d*).(\d*)$", "Replace": "$1x$2"},
                {"Search": r"^(some)(K\D*)$", "Replace": "$2$1"},
            ],
            "Template": _PASS_THROUGH,
        },
        {
            "UnitData": {
                "UnitTestKey1": "someValue",
                "UnitTestKey2": "someValue",
                "Data": {"_10x10": "someValue", "_20x20": "someValue"},
            },
            "OtherData": {
                "OtherData1": "someValue",
                "OtherData2": "someValue",
                "IssueData": [{"someKey": "someValue", "_20x20": "someValue"}],
            },
        },
        {
            "UnitData": {
                "UnitTestKey_1": "someValue",
                "UnitTestKey_2": "someValue",
                "Data": {"10x10": "someValue", "20x20": "someValue"},
            },
            "OtherData": {
                "OtherData_1": "someValue",
                "OtherData_2": "someValue",
                "IssueData": {"Keysome": "someValue", "20x20": "someValue"},
            },
        },
        True,
    ),
    Case(
        "RegExp complex (Post RegExp + Pre RegExp)",
        {
            "PostRegExFilter": [
                {"Search": r"^(\D*)(\d)$", "Replace": "$1_$2"},
                {"Search": r"^_(\d*).(\d*)$", "Replace": "$1x$2"},
                {"Search": r"^(some)(K\D*)$", "Replace": "$2$1"},
            ],
            "PreRegExFilter": [
                {"Search": r"^(\D*)(ZZ)$", "Replace": "$1_$2"},
                {"Search": r"^[QQQ]+(Key)[QQQ]+$", "Replace": "Q$1Q"},
            ],
            "Template": _PASS_THROUGH,
        },
        {
            "UnitTestKeyZZ": "someValue",
            "PreData": {"QQQKeyQQQ": "someValue"},
            "UnitData": {
                "UnitTestKey1": "someValue",
                "UnitTestKey2": "someValue",
                "Data": {"_10x10": "someValue", "_20x20": "someValue"},
            },
            "OtherData": {
                "OtherData1": "someValue",
                "OtherData2": "someValue",
                "IssueData": [
                    {"someKey": "someValue", "_10x10": "someValue"},
                    {"someKey": "someValue", "_20x20": "someValue"},
                ],
            },
        },
        {
            "UnitTestKey_ZZ": "someValue",
            "PreData": {"QKeyQ": "someValue"},
            "UnitData": {
                "UnitTestKey_1": "someValue",
                "UnitTestKey_2": "someValue",
                "Data": {"10x10": "someValue", "20x20": "someValue"},
            },
            "OtherData": {
                "OtherData_1": "someValue",
                "OtherData_2": "someValue",
                "IssueData": [
                    {"Keysome": "someValue", "10x10": "someValue"},
                    {"Keysome": "someValue", "20x20": "someValue"},
                ],
            },
        },
        True,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_znuny_vectors(case: Case) -> None:
    mapping_config = {"Type": "XSLT", "Config": case.config}
    if not case.config_success:
        with pytest.raises(MappingConfigError):
            build_mapper(mapping_config)
        return
    mapper = build_mapper(mapping_config)
    # a second instance from the same config must work too (XSLT.t instantiates twice)
    assert build_mapper(mapping_config) is not mapper
    if not case.result_success:
        with pytest.raises(MappingError) as excinfo:
            mapper.map(case.data, case.data_include)
        assert str(excinfo.value)  # "error message found"
        return
    assert mapper.map(case.data, case.data_include) == case.result


def test_error_summaries_follow_znuny() -> None:
    def summary(case_name: str) -> str:
        case = next(c for c in CASES if c.name == case_name)
        with pytest.raises(MappingError) as excinfo:
            XsltMapper(case.config).map(case.data)
        return str(excinfo.value)

    assert summary("invalid xml") == "Could not load configured XSLT template"
    assert summary("no xslt") == "Could not parse configured XSLT template"
    assert (
        summary("invalid hash key name") == "Could not load data after conversion from Perl to XML"
    )
    assert (
        summary("array as data")
        == "Got Data but it is not a hash or array ref in Mapping XSLT backend!"
    )


def test_no_template_returns_data_unchanged() -> None:
    data = {"a": "b"}
    assert XsltMapper({"ForceArray": "x"}).map(data) is data
    assert XsltMapper(None).map(data) is data


def test_bad_data_include_is_error() -> None:
    with pytest.raises(MappingError, match="DataInclude"):
        XsltMapper({"Template": IDENTITY}).map({"a": "b"}, {})


def test_list_data_with_data_include_config_is_error() -> None:
    with pytest.raises(MappingError):
        XsltMapper({"Template": IDENTITY, "DataInclude": ["X"]}).map(["a"], {"X": {"v": 1}})


# ------------------------------------------------------------- Tiqora additions


@pytest.mark.parametrize(
    "href",
    [
        "/opt/otrs/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl",
        "/opt/znuny/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl",
        "Kernel/GenericInterface/Mapping/OTRSFunctions.xsl",
        "file:///opt/otrs/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl",
    ],
)
def test_otrs_functions_import_any_path(href: str) -> None:
    out = XsltMapper({"Template": _import_template(href)}).map(
        {"DateISO": "2010-12-31 23:58:59", "DateXSD": "2011-11-30T22:57:58Z"}
    )
    assert out["NewStructure"]["DateFromISO"] == "2010-12-31T23:58:59Z"
    assert out["NewStructure"]["DateToISO"] == "2011-11-30 22:57:58"


@pytest.mark.parametrize(
    "href", ["/etc/passwd", "file:///etc/hosts", "https://example.org/x.xsl", "other.xsl"]
)
def test_foreign_import_is_rejected(href: str) -> None:
    tmpl = IDENTITY.replace("<xsl:template", f'<xsl:import href="{href}"/><xsl:template', 1)
    with pytest.raises(MappingError):
        XsltMapper({"Template": tmpl}).map({"a": "b"})


def test_foreign_include_is_rejected() -> None:
    tmpl = IDENTITY.replace("<xsl:template", '<xsl:include href="/etc/hosts"/><xsl:template', 1)
    with pytest.raises(MappingError):
        XsltMapper({"Template": tmpl}).map({"a": "b"})


def test_document_function_is_denied(tmp_path: Path) -> None:
    secret = tmp_path / "secret.xml"
    secret.write_text("<s>TOPSECRET</s>")
    tmpl = (
        f'<xsl:stylesheet version="1.0" {_XSL}>'
        f'<xsl:template match="/"><R><X><xsl:value-of select="document(\'{secret}\')"/></X></R>'
        "</xsl:template></xsl:stylesheet>"
    )
    with pytest.raises(MappingError):
        XsltMapper({"Template": tmpl}).map({"a": "b"})


def test_exsl_document_write_is_denied(tmp_path: Path) -> None:
    target = tmp_path / "x.xml"
    tmpl = (
        f'<xsl:stylesheet version="1.0" {_XSL} '
        'xmlns:exsl="http://exslt.org/common" extension-element-prefixes="exsl">'
        f'<xsl:template match="/"><exsl:document href="{target}"><a/></exsl:document><R/>'
        "</xsl:template></xsl:stylesheet>"
    )
    with pytest.raises(MappingError):
        XsltMapper({"Template": tmpl}).map({"a": "b"})
    assert not target.exists()


def test_external_entity_in_template_is_not_resolved(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    tmpl = (
        f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file://{secret}">]>'
        f'<xsl:stylesheet version="1.0" {_XSL}><xsl:template match="/RootElement">'
        "<R><V>&e;</V></R></xsl:template></xsl:stylesheet>"
    )
    try:
        out = XsltMapper({"Template": tmpl}).map({"a": "b"})
    except MappingError:
        return  # rejecting the template is fine too
    assert "TOPSECRET" not in repr(out)


@pytest.mark.parametrize("encoding", ["utf-8", "iso-8859-1"])
def test_umlauts_round_trip_any_output_encoding(encoding: str) -> None:
    tmpl = IDENTITY.replace(
        "<xsl:template", f'<xsl:output method="xml" encoding="{encoding}"/><xsl:template', 1
    )
    assert XsltMapper({"Template": tmpl}).map({"Text": "Grüße € ✓"}) == {"Text": "Grüße € ✓"}


def test_huge_text_node_needs_huge_flag() -> None:
    big = {"Content": "A" * (11 * 1024 * 1024)}
    with pytest.raises(MappingError):
        XsltMapper({"Template": IDENTITY}).map(big)
    assert XsltMapper({"Template": IDENTITY}, huge_xml=True).map(big) == big


def test_build_mapper_passes_huge_flag() -> None:
    big = {"Content": "A" * (11 * 1024 * 1024)}
    mapper = build_mapper({"Type": "XSLT", "Config": {"Template": IDENTITY}}, huge_xml=True)
    assert mapper.map(big) == big


def test_template_whitespace_trim() -> None:
    assert prepare_template("<a>\n   x\n</a>") == "<a>x</a>"


def test_post_regex_second_match_quirk() -> (
    None
):  # Perl: second filter deletes an already-renamed key
    data: dict[str, Any] = {"ab": "v"}
    regex_recursion(data, [{"Search": "^a", "Replace": "x"}, {"Search": "b$", "Replace": "y"}])
    assert data == {"xb": "v", "ay": None}


def test_force_array_and_keep_attributes() -> None:
    assert XsltMapper({"Template": IDENTITY, "ForceArray": "L"}).map({"L": {"k": "v"}}) == {
        "L": [{"k": "v"}]
    }
    assert XsltMapper({"Template": IDENTITY, "KeepAttributes": 1}).map({"k": "v"}) == {
        "k": {"content": "v"}
    }


def test_data_include_only_configured_modules() -> None:
    template = _simple_template(
        "<NewRootElement>"
        '<A><xsl:value-of select="/RootElement/DataInclude/RequesterRequestInput/Value"/></A>'
        '<B><xsl:value-of select="count(/RootElement/DataInclude/RequesterRequestPrepareOutput)"/></B>'  # noqa: E501
        "</NewRootElement>"
    )
    out = XsltMapper({"Template": template, "DataInclude": ["RequesterRequestInput"]}).map(
        {"Key": "Value"},
        {"RequesterRequestInput": {"Value": 1}, "RequesterRequestPrepareOutput": {"Value": 2}},
    )
    assert out == {"A": "1", "B": "0"}


def test_data_include_does_not_mutate_caller_data() -> None:
    data = {"Key": "Value"}
    XsltMapper({"Template": IDENTITY, "DataInclude": ["X"]}).map(data, {"X": {"v": 1}})
    assert data == {"Key": "Value"}


def test_otrs_functions_matches_znuny_source() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "znuny-6.5.22/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl"
    )
    if not source.exists():
        pytest.skip("Znuny source tree not available")
    assert source.read_bytes() == OTRS_FUNCTIONS_XSL


# ------------------------------------------------------ regex safety (ruling)


def test_filter_pattern_that_does_not_compile_fails_at_build() -> None:
    with pytest.raises(MappingConfigError):
        XsltMapper({"Template": IDENTITY, "PreRegExFilter": [{"Search": "(", "Replace": "x"}]})
    with pytest.raises(MappingConfigError):
        XsltMapper({"Template": IDENTITY, "PostRegExFilter": [{"Search": "[", "Replace": "x"}]})


def test_malformed_filter_entry_fails_at_build() -> None:
    with pytest.raises(MappingConfigError):
        XsltMapper({"Template": IDENTITY, "PreRegExFilter": ["not-a-hash"]})


def test_catastrophic_filter_pattern_times_out() -> None:
    mapper = XsltMapper(
        {"Template": IDENTITY, "PreRegExFilter": [{"Search": r"^(a|aa)+$", "Replace": "x"}]}
    )
    started = time.monotonic()
    with pytest.raises(MappingError, match="timed out"):
        mapper.map({"a" * 60 + "b": "v"})
    assert time.monotonic() - started < 5


def test_filters_run_on_regex_package_with_timeout() -> None:
    import regex

    from tiqora.api.compat.mapping.perl import compile_perl_regex

    assert isinstance(compile_perl_regex("a", verbose=False), regex.Pattern)
    assert not hasattr(xslt_module, "re")  # plain `re` is never imported into the XSLT module


# ------------------------------------------------------------ resource limits


def test_infinite_template_recursion_is_an_error_not_a_hang() -> None:
    tmpl = (
        f'<xsl:stylesheet version="1.0" {_XSL}>'
        '<xsl:template match="/RootElement"><R><xsl:call-template name="loop"/></R></xsl:template>'
        '<xsl:template name="loop"><x/><xsl:call-template name="loop"/></xsl:template>'
        "</xsl:stylesheet>"
    )
    started = time.monotonic()
    with pytest.raises(MappingError, match="Could not map data"):
        XsltMapper({"Template": tmpl}).map({"a": "b"})
    assert time.monotonic() - started < 10


def test_output_size_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xslt_module, "MAX_OUTPUT_BYTES", 100)
    with pytest.raises(MappingError, match="Could not map data"):
        XsltMapper({"Template": IDENTITY}).map({"k": "v" * 1000})


def test_data_include_uses_perl_truthiness() -> None:
    # XSLT.pm:211 `next if !$Param{DataInclude}->{$Module}`: {} is true, "0" is false
    template = _simple_template(
        "<NewRootElement>"
        '<E><xsl:value-of select="count(/RootElement/DataInclude/Empty)"/></E>'
        '<Z><xsl:value-of select="count(/RootElement/DataInclude/Zero)"/></Z>'
        "</NewRootElement>"
    )
    out = XsltMapper({"Template": template, "DataInclude": ["Empty", "Zero"]}).map(
        {"Key": "Value"}, {"Empty": {}, "Zero": "0"}
    )
    assert out == {"E": "1", "Z": "0"}


@pytest.mark.parametrize(
    "href",
    [
        "/opt/otrs/OTRSFunctions.xsl/../../etc/evil.xsl",
        "/opt/otrs/OTRSFunctions.xsl.bak",
        "https://example.org/OTRSFunctions.xsl?x=/etc/passwd&y=1.xsl",
    ],
)
def test_href_containing_otrs_functions_not_at_the_end_is_refused(href: str) -> None:
    tmpl = IDENTITY.replace("<xsl:template", f'<xsl:import href="{href}"/><xsl:template', 1)
    with pytest.raises(MappingError) as exc:
        XsltMapper({"Template": tmpl}).map({"a": "b"})
    assert "not allowed" in str(exc.value) or "Could not" in str(exc.value)


def test_foreign_import_message_is_english() -> None:
    tmpl = IDENTITY.replace("<xsl:template", '<xsl:import href="/etc/hosts.xsl"/><xsl:template', 1)
    with pytest.raises(MappingError) as exc:
        XsltMapper({"Template": tmpl}).map({"a": "b"})
    assert "is not allowed in Tiqora" in str(exc.value)
    assert "nicht" not in str(exc.value)


def test_text_output_that_is_not_xml_is_an_error() -> None:
    # Znuny parses the transform result with XMLin: plain text is no XML document
    tmpl = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
        '<xsl:output method="text"/>'
        '<xsl:template match="/">plain <xsl:value-of select="/RootElement/a"/></xsl:template>'
        "</xsl:stylesheet>"
    )
    with pytest.raises(MappingError):
        XsltMapper({"Template": tmpl}).map({"a": "b"})
