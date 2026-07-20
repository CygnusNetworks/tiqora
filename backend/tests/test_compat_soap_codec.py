"""Unit tests for the SOAP envelope codec (api/compat/soap.py).

No DB/network required — pure envelope parse/serialize roundtrips, matching
Znuny's ``HTTP::SOAP`` transport wire behaviour (SOAP.pm): operation
dispatch from the Body wrapper element name, ``<OperationNameResponse>``
response wrapping, and SOAP Fault error envelopes.
"""

from __future__ import annotations

import base64

import pytest

from tiqora.api.compat.soap import (
    DEFAULT_NAMESPACE,
    SOAP11_NS,
    SOAP12_NS,
    SoapCodecError,
    SoapRequest,
    build_soap_fault,
    build_soap_response,
    content_type_for_version,
    extract_soap_action_operation,
    parse_soap_request,
    response_operation_name,
)

# ---------------------------------------------------------------------------
# Request parsing — basic shape
# ---------------------------------------------------------------------------


def test_parse_simple_scalar_fields() -> None:
    xml = b"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body>
        <SessionCreate>
          <UserLogin>agent1</UserLogin>
          <Password>secret</Password>
        </SessionCreate>
      </soapenv:Body>
    </soapenv:Envelope>"""
    req = parse_soap_request(xml)
    assert req == SoapRequest(
        version="1.1",
        operation="SessionCreate",
        data={"UserLogin": "agent1", "Password": "secret"},
    )


def test_parse_namespace_prefixed_wrapper_uses_local_name() -> None:
    """The operation wrapper is namespace-prefixed in real Znuny traffic
    (e.g. ``<tic:TicketGet>``) — the local name (sans prefix) is the
    operation, matching ``$Operation = (sort keys %{$Body})[0]``."""
    xml = b"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                                xmlns:tic="http://www.otrs.org/TicketConnector/">
      <soapenv:Body>
        <tic:TicketGet>
          <TicketID>42</TicketID>
        </tic:TicketGet>
      </soapenv:Body>
    </soapenv:Envelope>"""
    req = parse_soap_request(xml)
    assert req.operation == "TicketGet"
    assert req.data == {"TicketID": "42"}


def test_parse_soap12_envelope() -> None:
    xml = f"""<env:Envelope xmlns:env="{SOAP12_NS}">
      <env:Body>
        <TicketSearch>
          <UserLogin>agent1</UserLogin>
        </TicketSearch>
      </env:Body>
    </env:Envelope>""".encode()
    req = parse_soap_request(xml)
    assert req.version == "1.2"
    assert req.operation == "TicketSearch"


# ---------------------------------------------------------------------------
# Request parsing — arrays, nested structures, attachments
# ---------------------------------------------------------------------------


def test_parse_repeated_elements_become_a_list() -> None:
    """SOAP::Lite (and thus Znuny) arrays are repeated same-named elements;
    a single occurrence stays scalar (this ambiguity is inherent to Znuny's
    own wire format too, and already tolerated by the REST operation
    handlers via ``_to_list``)."""
    xml = b"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body>
        <TicketSearch>
          <QueueIDs>1</QueueIDs>
          <QueueIDs>2</QueueIDs>
          <QueueIDs>3</QueueIDs>
        </TicketSearch>
      </soapenv:Body>
    </soapenv:Envelope>"""
    req = parse_soap_request(xml)
    assert req.data["QueueIDs"] == ["1", "2", "3"]


def test_parse_nested_ticket_and_dynamic_field() -> None:
    xml = b"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body>
        <TicketCreate>
          <UserLogin>agent1</UserLogin>
          <Password>secret</Password>
          <Ticket>
            <Title>Broken printer</Title>
            <Queue>Raw</Queue>
            <State>new</State>
            <Priority>3 normal</Priority>
          </Ticket>
          <DynamicField>
            <Name>Foo</Name>
            <Value>bar</Value>
          </DynamicField>
          <DynamicField>
            <Name>Baz</Name>
            <Value>qux</Value>
          </DynamicField>
        </TicketCreate>
      </soapenv:Body>
    </soapenv:Envelope>"""
    req = parse_soap_request(xml)
    assert req.operation == "TicketCreate"
    assert req.data["Ticket"] == {
        "Title": "Broken printer",
        "Queue": "Raw",
        "State": "new",
        "Priority": "3 normal",
    }
    assert req.data["DynamicField"] == [
        {"Name": "Foo", "Value": "bar"},
        {"Name": "Baz", "Value": "qux"},
    ]


def test_parse_base64_attachment_content_preserved_verbatim() -> None:
    """Attachment Content stays a base64 *string* — decoding to bytes is the
    operation handler's job (``_build_article_in`` already does
    ``base64.b64decode``), the codec must not touch it."""
    payload = base64.b64encode(b"hello world attachment bytes").decode("ascii")
    xml = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body>
        <TicketCreate>
          <Article>
            <Subject>Hi</Subject>
            <Body>Body text</Body>
            <Attachment>
              <Filename>note.txt</Filename>
              <ContentType>text/plain</ContentType>
              <Content>{payload}</Content>
            </Attachment>
          </Article>
        </TicketCreate>
      </soapenv:Body>
    </soapenv:Envelope>""".encode()
    req = parse_soap_request(xml)
    attachment = req.data["Article"]["Attachment"]
    assert attachment["Filename"] == "note.txt"
    assert attachment["Content"] == payload
    assert base64.b64decode(attachment["Content"]) == b"hello world attachment bytes"


def test_parse_multiple_attachments_become_a_list() -> None:
    xml = b"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body>
        <TicketUpdate>
          <Article>
            <Attachment><Filename>a.txt</Filename><Content>YQ==</Content></Attachment>
            <Attachment><Filename>b.txt</Filename><Content>Yg==</Content></Attachment>
          </Article>
        </TicketUpdate>
      </soapenv:Body>
    </soapenv:Envelope>"""
    req = parse_soap_request(xml)
    attachments = req.data["Article"]["Attachment"]
    assert isinstance(attachments, list)
    assert [a["Filename"] for a in attachments] == ["a.txt", "b.txt"]


# ---------------------------------------------------------------------------
# Request parsing — malformed / XXE
# ---------------------------------------------------------------------------


def test_parse_malformed_xml_raises_codec_error() -> None:
    with pytest.raises(SoapCodecError):
        parse_soap_request(b"<not-well-formed")


def test_parse_missing_body_raises_codec_error() -> None:
    xml = b"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Header/>
    </soapenv:Envelope>"""
    with pytest.raises(SoapCodecError):
        parse_soap_request(xml)


def test_parse_empty_body_raises_codec_error() -> None:
    xml = b"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body/>
    </soapenv:Envelope>"""
    with pytest.raises(SoapCodecError):
        parse_soap_request(xml)


def test_parse_unknown_envelope_namespace_raises_codec_error() -> None:
    xml = b"""<Envelope xmlns="http://example.com/not-soap">
      <Body><Test/></Body>
    </Envelope>"""
    with pytest.raises(SoapCodecError):
        parse_soap_request(xml)


def test_parse_rejects_external_entity_xxe() -> None:
    """XXE via a SYSTEM external entity must be rejected, not silently
    resolved (would allow local file disclosure / SSRF)."""
    xml = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        b"<soapenv:Body><Test>&xxe;</Test></soapenv:Body>"
        b"</soapenv:Envelope>"
    )
    with pytest.raises(SoapCodecError):
        parse_soap_request(xml)


def test_parse_rejects_entity_expansion_bomb() -> None:
    """Billion-laughs style internal entity expansion must also be rejected."""
    xml = (
        b'<?xml version="1.0"?>'
        b"<!DOCTYPE lolz ["
        b'<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        b"]>"
        b'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        b"<soapenv:Body><Test>&lol2;</Test></soapenv:Body>"
        b"</soapenv:Envelope>"
    )
    with pytest.raises(SoapCodecError):
        parse_soap_request(xml)


# ---------------------------------------------------------------------------
# SOAPAction header
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ('"http://www.otrs.org/TicketConnector/#TicketGet"', "TicketGet"),
        ("http://www.otrs.org/TicketConnector/TicketGet", "TicketGet"),
        ("'TicketCreate'", "TicketCreate"),
        (None, None),
        ("", None),
    ],
)
def test_extract_soap_action_operation(header: str | None, expected: str | None) -> None:
    assert extract_soap_action_operation(header) == expected


# ---------------------------------------------------------------------------
# Response serialization
# ---------------------------------------------------------------------------


def test_response_operation_name() -> None:
    assert response_operation_name("TicketGet") == "TicketGetResponse"


def test_build_response_wraps_in_operation_response_element() -> None:
    xml = build_soap_response("TicketGet", {"TicketID": 42, "TicketNumber": "2026070100001"})
    text = xml.decode()
    assert "<TicketGetResponse" in text
    assert f'xmlns="{DEFAULT_NAMESPACE}"' in text
    assert "<TicketID>42</TicketID>" in text
    assert "<TicketNumber>2026070100001</TicketNumber>" in text
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')


def test_build_response_honours_custom_namespace() -> None:
    xml = build_soap_response("SessionCreate", {"SessionID": "abc"}, namespace="urn:custom-ns")
    assert 'xmlns="urn:custom-ns"' in xml.decode()


def test_build_response_soap12_envelope() -> None:
    xml = build_soap_response("TicketGet", {"TicketID": 1}, version="1.2")
    text = xml.decode()
    assert f'xmlns:soap12="{SOAP12_NS}"' in text
    assert "<soap12:Envelope" in text
    assert "<soap12:Body>" in text


def test_build_response_encodes_nested_and_repeated_values() -> None:
    result = {
        "Ticket": [
            {"TicketID": 1, "Title": "A"},
            {"TicketID": 2, "Title": "B"},
        ]
    }
    xml = build_soap_response("TicketGet", result).decode()
    assert xml.count("<Ticket>") == 2
    assert "<TicketID>1</TicketID>" in xml
    assert "<TicketID>2</TicketID>" in xml


def test_build_fault_soap11() -> None:
    xml = build_soap_fault("TicketGet.AuthFail: bad session").decode()
    assert "<Fault>" in xml
    assert "<faultcode>Server</faultcode>" in xml
    assert "<faultstring>TicketGet.AuthFail: bad session</faultstring>" in xml


def test_build_fault_soap12() -> None:
    xml = build_soap_fault("boom", version="1.2").decode()
    assert "soap12:Fault" in xml
    assert "<soap12:Text>boom</soap12:Text>" in xml


def test_content_type_for_version() -> None:
    assert content_type_for_version("1.1") == "text/xml; charset=utf-8"
    assert content_type_for_version("1.2") == "application/soap+xml; charset=utf-8"
    assert content_type_for_version(SOAP11_NS) == content_type_for_version("1.1")  # unknown -> 1.1


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


def test_request_response_roundtrip_preserves_operation_and_data_shape() -> None:
    request_xml = b"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
      <soapenv:Body>
        <TicketGet>
          <SessionID>abc123</SessionID>
          <TicketID>7</TicketID>
        </TicketGet>
      </soapenv:Body>
    </soapenv:Envelope>"""
    req = parse_soap_request(request_xml)

    # Simulate what the shared operation handler would return.
    fake_result = {"Ticket": [{"TicketID": 7, "Title": "Roundtrip"}]}
    response_xml = build_soap_response(req.operation, fake_result, version=req.version)

    # The response must itself be a parseable envelope whose Body wrapper is
    # named "<Operation>Response".
    reparsed_root_check = parse_soap_request(
        response_xml.replace(b"TicketGetResponse", b"TicketGet")
    )
    assert reparsed_root_check.data["Ticket"]["TicketID"] == "7"
    assert b"<TicketGetResponse" in response_xml
