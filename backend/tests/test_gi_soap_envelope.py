"""SOAP requester codec (G8): pure tests with the vectors of Znuny's Serialize.t."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any
from xml.etree.ElementTree import tostring  # noqa: S405 (serialising only)

import pytest

from tiqora.gi.soap_envelope import (
    DESERIALIZE_ERROR,
    ENVELOPE_HEAD,
    ENVELOPE_TAIL,
    SoapOutputError,
    build_request_envelope,
    parse_response,
    process_string,
    request_name,
    response_name,
    soap_action,
    soap_output,
)


def xml_of(data: dict[str, Any], sort: Any = None) -> str:
    return "".join(
        tostring(e, encoding="unicode", short_empty_elements=False)
        for e in soap_output(data, sort=sort)
    )


def test_soap_action_schemes() -> None:
    base = {"NameSpace": "http://ws.example.org/Conn"}
    assert (
        soap_action(base, operation="Inv", operation_name="Op") == "http://ws.example.org/Conn#Op"
    )
    assert soap_action({**base, "SOAPAction": "No"}, operation="Inv", operation_name="Op") == ""
    assert (
        soap_action(
            {**base, "SOAPAction": "Yes", "SOAPActionScheme": "Operation"},
            operation="Inv",
            operation_name="Op",
        )
        == "Inv"
    )
    assert (
        soap_action(
            {
                **base,
                "SOAPAction": "Yes",
                "SOAPActionScheme": "SeparatorOperation",
                "SOAPActionSeparator": "/",
            },
            operation="Inv",
            operation_name="Op",
        )
        == "/Op"
    )
    assert (
        soap_action(
            {
                **base,
                "SOAPAction": "Yes",
                "SOAPActionScheme": "FreeText",
                "SOAPActionFreeText": "urn:x",
            },
            operation="Inv",
            operation_name="Op",
        )
        == "urn:x"
    )
    # incomplete schemes fall back to the empty action
    for extra in (
        {"SOAPActionScheme": "FreeText"},
        {"SOAPActionScheme": "SeparatorOperation"},
        {"SOAPActionScheme": "NameSpaceSeparatorOperation"},
        {"SOAPActionScheme": "Nonsense", "SOAPActionSeparator": "#"},
    ):
        cfg = {**base, "SOAPAction": "Yes", **extra}
        assert soap_action(cfg, operation="Inv", operation_name="Op") == ""
    assert (
        soap_action(
            {
                **base,
                "SOAPAction": "Yes",
                "SOAPActionScheme": "NameSpaceSeparatorOperation",
                "SOAPActionSeparator": "/",
            },
            operation="Inv",
            operation_name="Op",
        )
        == "http://ws.example.org/Conn/Op"
    )


def test_request_and_response_names() -> None:
    assert request_name({}, "Op") == "Op"
    assert request_name({"RequestNameScheme": "Request"}, "Op") == "OpRequest"
    assert (
        request_name({"RequestNameScheme": "Append", "RequestNameFreeText": "Req"}, "Op") == "OpReq"
    )
    assert request_name({"RequestNameScheme": "Append"}, "Op") == "Op"
    assert request_name({"RequestNameScheme": "Plain", "RequestNameFreeText": "x"}, "Op") == "Op"
    assert response_name({}, "Op") == "OpResponse"
    assert (
        response_name({"ResponseNameScheme": "Append", "ResponseNameFreeText": "Ret"}, "Op")
        == "OpRet"
    )
    assert (
        response_name({"ResponseNameScheme": "Replace", "ResponseNameFreeText": "Answer"}, "Op")
        == "Answer"
    )
    assert response_name({"ResponseNameScheme": "Plain"}, "Op") == "Op"
    assert response_name({"ResponseNameScheme": "Plain", "ResponseTagSuffix": "_X"}, "Op") == "Op_X"
    assert response_name({"ResponseNameScheme": "Replace"}, "Op") == "Op"


def test_output_sort_order() -> None:
    # without Sort the keys are sorted
    assert xml_of({"B": "1", "A": "2"}) == "<A>2</A><B>1</B>"
    # Sort [{"B": ...}, {"A": ...}] puts B before A; unlisted keys follow sorted; missing ones skip
    data = {"A": "a", "B": "b", "C": "c", "D": "d"}
    sort = [{"D": None}, {"Missing": None}, {"B": None}]
    assert xml_of(data, sort) == "<D>d</D><B>b</B><A>a</A><C>c</C>"
    # Znuny's 'Custom sorted hash' and 'Complex sorted' vectors
    nested = {"Var": {"String": "foo", "Hash": {"Key1": 1, "Key2": 2}, "Number": 2}}
    sort = [
        {"Var": [{"Number": None}, {"String": None}, {"Hash": [{"Key2": None}, {"Key1": None}]}]}
    ]
    assert xml_of(nested, sort) == (
        "<Var><Number>2</Number><String>foo</String><Hash><Key2>2</Key2><Key1>1</Key1></Hash></Var>"
    )
    complex_data = {
        "Var": [{"Hash": {"Key1": [1, 2], "Key3": 1, "Key4": {"Key5": "Hash"}}}],
    }
    complex_sort = [
        {"Var": [{"Hash": [{"Key3": None}, {"Key4": [{"Key5": None}]}, {"Key1": None}]}]}
    ]
    assert xml_of(complex_data, complex_sort) == (
        "<Var><Hash><Key3>1</Key3><Key4><Key5>Hash</Key5></Key4>"
        "<Key1>1</Key1><Key1>2</Key1></Hash></Var>"
    )


def test_output_vectors_without_sort() -> None:
    assert xml_of({"Var": [1, 2, 3]}) == "<Var>1</Var><Var>2</Var><Var>3</Var>"
    assert xml_of({"Var": "äöüß€ис"}) == "<Var>äöüß€ис</Var>"
    complex_data = {
        "Var": [1, {"Hash": {"Key1": [1, 2], "Key3": 1, "Key4": {"Key5": "Hash"}}}],
    }
    assert xml_of(complex_data) == (
        "<Var>1</Var><Var><Hash><Key1>1</Key1><Key1>2</Key1><Key3>1</Key3>"
        "<Key4><Key5>Hash</Key5></Key4></Hash></Var>"
    )
    assert soap_output({}) == []


def test_output_errors() -> None:
    with pytest.raises(SoapOutputError, match="Array inside array"):
        soap_output({"Var": [1, [2, 3]]})
    with pytest.raises(SoapOutputError, match=r"ARRAY \(empty\)"):
        soap_output({"Var": []})
    with pytest.raises(SoapOutputError, match="don't match"):
        soap_output({"A": "x"}, sort=[{"A": [{"B": None}]}])
    with pytest.raises(SoapOutputError, match="exactly one key/value pair"):
        soap_output({"A": "x"}, sort=[{"A": None, "B": None}])
    with pytest.raises(SoapOutputError, match="not a hash reference"):
        soap_output({"A": "x"}, sort=["A"])
    with pytest.raises(SoapOutputError, match="not a valid XML name"):
        soap_output({"A><x": "1"})


def test_escaping_and_lists() -> None:
    assert process_string("a <string> & more") == "a &lt;string&gt; &amp; more"
    assert process_string("<![CDATA[Test]]>") == "&lt;![CDATA[Test]]&gt;"
    assert process_string("Test1\x03 Test2\x08 Test3\x0c") == "Test1 Test2 Test3"
    assert process_string(None) == ""
    # on the wire: once-escaped, restricted characters gone, None / {} become empty elements
    assert xml_of({"T": "a <b> & ]]>\x03c", "N": None, "E": {}}) == (
        "<E></E><N></N><T>a &lt;b&gt; &amp; ]]&gt;c</T>"
    )
    assert xml_of({"Var": [None, "x"]}) == "<Var></Var><Var>x</Var>"


def test_envelope_namespaces_no_namespace_and_fixed_prefix() -> None:
    body = soap_output({"A": "1"})
    ns = "http://ws.example.org/Conn"

    def env(**kw: Any) -> str:
        args: dict[str, Any] = {
            "operation_request": "Op",
            "namespace": ns,
            "no_namespace": False,
            "fixed_prefix": None,
            "body": soap_output({"A": "1"}),
        }
        args.update(kw)
        return build_request_envelope(**args).decode("utf-8")

    assert body  # soap_output yields fresh elements per call
    assert env() == (
        ENVELOPE_HEAD + f'<namesp1:Op xmlns:namesp1="{ns}"><A>1</A></namesp1:Op>' + ENVELOPE_TAIL
    )
    # semantics: the data element is in no namespace (as with Znuny's SOAP::Lite)
    parsed = ET.fromstring(env().encode())
    method = parsed[0][0]
    assert method.tag == f"{{{ns}}}Op" and [c.tag for c in method] == ["A"]
    assert env(fixed_prefix="ns1") == (
        ENVELOPE_HEAD + f'<ns1:Op xmlns:ns1="{ns}"><A>1</A></ns1:Op>' + ENVELOPE_TAIL
    )
    assert env(no_namespace=True) == (
        ENVELOPE_HEAD + f'<Op xmlns="{ns}"><A>1</A></Op>' + ENVELOPE_TAIL
    )
    assert env(no_namespace=True, namespace=None) == (
        ENVELOPE_HEAD + "<Op><A>1</A></Op>" + ENVELOPE_TAIL
    )
    assert env(body=[]) == (
        ENVELOPE_HEAD
        + f'<namesp1:Op xmlns:namesp1="{ns}" xsi:nil="true"></namesp1:Op>'
        + ENVELOPE_TAIL
    )
    assert ENVELOPE_HEAD.startswith('<?xml version="1.0" encoding="UTF-8"?><soap:Envelope ')
    assert 'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"' in ENVELOPE_HEAD
    with pytest.raises(SoapOutputError):
        env(operation_request="Op><evil")
    with pytest.raises(ValueError):
        env(soap_version="1.2")


def _envelope(inner: str, *, decl: str = '<?xml version="1.0" encoding="UTF-8"?>') -> bytes:
    return (
        decl
        + '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        + f"<soap:Body>{inner}</soap:Body></soap:Envelope>"
    ).encode()


def test_parse_fault_and_response() -> None:
    fault = _envelope(
        "<soap:Fault><faultstring>boom</faultstring><faultcode>Server</faultcode></soap:Fault>"
    )
    assert parse_response(fault, response_name="OpResponse") == (
        None,
        "faultcode: Server, faultstring: boom",
    )
    ok = _envelope(
        '<OpResponse xmlns="urn:x"><TicketID>7</TicketID><Tag>a</Tag><Tag>b</Tag></OpResponse>'
    )
    assert parse_response(ok, response_name="OpResponse") == (
        {"TicketID": "7", "Tag": ["a", "b"]},
        None,
    )
    # an empty / false response element is undef, like `|| undef`
    assert parse_response(_envelope("<OpResponse/>"), response_name="OpResponse") == (None, None)
    assert parse_response(_envelope("<OpResponse>0</OpResponse>"), response_name="OpResponse") == (
        None,
        None,
    )
    data, err = parse_response(
        _envelope("<Other/>"), response_name="OpResponse", operation_name="Op"
    )
    assert data is None
    assert err == "No response data found for specified operation 'Op' in soap response"
    for broken in (b"not xml", _envelope(""), b"<a/>"):
        assert parse_response(broken, response_name="OpResponse") == (None, DESERIALIZE_ERROR)


def test_parse_response_rejects_xxe() -> None:
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/hostname">]>'
        b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        b"<OpResponse><V>&x;</V></OpResponse></soap:Body></soap:Envelope>"
    )
    assert parse_response(xxe, response_name="OpResponse") == (None, DESERIALIZE_ERROR)
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        b"<OpResponse>&b;</OpResponse></soap:Body></soap:Envelope>"
    )
    assert parse_response(bomb, response_name="OpResponse") == (None, DESERIALIZE_ERROR)


def test_parse_response_converts_encoding() -> None:
    raw = (
        _envelope(
            "<OpResponse><Name>Grüße</Name></OpResponse>",
            decl='<?xml version="1.0" encoding="iso-8859-1"?>',
        )
        .decode("utf-8")
        .encode("iso-8859-1")
    )
    assert parse_response(raw, response_name="OpResponse", encoding="ISO-8859-1") == (
        {"Name": "Grüße"},
        None,
    )
    assert parse_response(raw, response_name="OpResponse", encoding="x-nonsense")[1] == (
        "SOAP Transport: Unsupported Encoding 'x-nonsense'"
    )
