"""SOAP 1.1/1.2 envelope codec for the GenericInterface compatibility layer.

Ports the wire behaviour of Znuny's ``HTTP::SOAP`` transport
(``Kernel/GenericInterface/Transport/HTTP/SOAP.pm``, backed by Perl's
``SOAP::Lite``) pragmatically:

- The operation is dispatched from the **first child element of the SOAP
  Body**, wrapper element name -> operation name (namespace-prefix agnostic,
  e.g. ``<tic:TicketGet>`` or plain ``<TicketGet>`` both resolve to
  ``TicketGet``). This mirrors ``$Operation = (sort keys %{$Body})[0]`` in
  ``SOAP.pm``.
- Nested elements decode to dicts; a repeated child tag decodes to a list
  (SOAP::Lite's array-vs-scalar ambiguity — same as Znuny, and already
  tolerated by the existing REST operation handlers via ``_to_list``).
- Leaf element text decodes to a plain string; the existing operation
  handlers already coerce to int/bool where needed (they must, since Znuny's
  own wire format is untyped strings too).
- Responses are wrapped in ``<OperationNameResponse>`` inside the SOAP Body,
  matching Znuny's default ``ResponseNameScheme: Response`` / ``Append``
  behaviour (``SOAP.pm`` lines ~468-484). Errors become a SOAP ``<Fault>``.

XXE mitigation: parsing uses ``defusedxml.ElementTree`` exclusively, which
rejects external entities, external DTDs, and entity expansion bombs. Never
use ``xml.etree.ElementTree.fromstring`` directly on request bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from xml.etree.ElementTree import (  # noqa: S405 (build-only, not parse)
    Element,
    SubElement,
    register_namespace,
    tostring,
)

from defusedxml.ElementTree import ParseError, fromstring

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOAP11_NS = "http://schemas.xmlsoap.org/soap/envelope/"
SOAP12_NS = "http://www.w3.org/2003/05/soap-envelope"

# Force conventional prefixes ("soap:Envelope" / "soap12:Envelope") instead
# of ElementTree's auto-generated ns0/ns1 — matches what SOAP clients (incl.
# Znuny's own SOAP::Lite requester) expect to see, and keeps output diffable.
register_namespace("soap", SOAP11_NS)
register_namespace("soap12", SOAP12_NS)

#: Znuny's own default for the sample GenericTicketConnectorSOAP webservice
#: (``scripts/test/.../GenericTicketConnectorSOAP.yml``): ``NameSpace:
#: http://www.otrs.org/TicketConnector/``. Used when a webservice config
#: does not set ``Provider.Transport.Config.NameSpace``.
DEFAULT_NAMESPACE = "http://www.otrs.org/TicketConnector/"

_CONTENT_TYPE_BY_VERSION = {
    "1.1": "text/xml; charset=utf-8",
    "1.2": "application/soap+xml; charset=utf-8",
}


class SoapCodecError(Exception):
    """Raised for malformed SOAP envelopes that cannot be decoded at all.

    Distinct from a Znuny operation-level error (``{"Error": {...}}``, which
    the operation handlers already return as a normal dict) — this is for
    envelopes so broken a SOAP Fault must be synthesized before any operation
    dispatch is even possible (e.g. no Body, unknown envelope namespace).
    """


@dataclass(frozen=True)
class SoapRequest:
    """A decoded SOAP request: transport version + operation + wire data."""

    version: str  # "1.1" or "1.2"
    operation: str
    data: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    """Strip a ``{namespace}`` prefix from an ElementTree tag."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def extract_soap_action_operation(soap_action: str | None) -> str | None:
    """Best-effort operation name from the ``SOAPAction`` HTTP header.

    Znuny accepts several ``SOAPActionScheme`` values (``FreeText``,
    ``Operation``, ``SeparatorOperation``, ``NameSpaceSeparatorOperation``);
    the operation name is always the trailing path/fragment segment. We use
    this only as a *fallback* when the Body wrapper element name is missing
    or ambiguous — Znuny's own primary dispatch is the Body element name
    (``SOAP.pm`` line ~280: ``$Operation = (sort keys %{$Body})[0]``).
    """
    if not soap_action:
        return None
    stripped = soap_action.strip().strip('"').strip("'")
    if not stripped:
        return None
    for sep in ("#", "/"):
        if sep in stripped:
            stripped = stripped.rsplit(sep, 1)[1]
    return stripped or None


def _element_to_value(el: Element) -> Any:
    """Decode an element to a scalar (leaf) or dict (has children)."""
    children = list(el)
    if not children:
        return el.text if el.text is not None else ""
    return _children_to_dict(children)


def _children_to_dict(children: list[Element]) -> dict[str, Any]:
    """Group child elements by local tag name; repeats become lists."""
    grouped: dict[str, list[Any]] = {}
    for child in children:
        tag = _local_name(child.tag)
        grouped.setdefault(tag, []).append(_element_to_value(child))
    return {tag: (vals[0] if len(vals) == 1 else vals) for tag, vals in grouped.items()}


def _find_body(root: Element, envelope_ns: str) -> Element:
    body = root.find(f"{{{envelope_ns}}}Body")
    if body is None:
        raise SoapCodecError("SOAP Envelope has no Body element")
    return body


# ---------------------------------------------------------------------------
# Request decoding
# ---------------------------------------------------------------------------


def parse_soap_request(xml_bytes: bytes, soap_action: str | None = None) -> SoapRequest:
    """Parse a raw SOAP 1.1 or 1.2 envelope into operation name + data dict.

    Raises ``SoapCodecError`` for structurally broken envelopes (not
    well-formed XML, unknown envelope namespace, empty/missing Body). XXE is
    prevented by parsing exclusively via ``defusedxml``.
    """
    try:
        root = fromstring(xml_bytes)
    except ParseError as exc:
        raise SoapCodecError(f"Malformed XML: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - defusedxml raises varied guard exceptions
        raise SoapCodecError(f"Rejected XML (possible XXE/entity attack): {exc}") from exc

    tag = root.tag
    if tag == f"{{{SOAP11_NS}}}Envelope":
        version = "1.1"
        envelope_ns = SOAP11_NS
    elif tag == f"{{{SOAP12_NS}}}Envelope":
        version = "1.2"
        envelope_ns = SOAP12_NS
    else:
        raise SoapCodecError(f"Unknown or missing SOAP envelope namespace (root tag {tag!r})")

    body = _find_body(root, envelope_ns)
    children = list(body)
    if not children:
        raise SoapCodecError("SOAP Body is empty — no operation wrapper element found")

    # Znuny: $Operation = (sort keys %{$Body})[0] — the (only) wrapper
    # element inside Body names the operation. SOAPAction is a fallback hint
    # only, used when the wrapper's local name looks unusable.
    wrapper = children[0]
    operation = _local_name(wrapper.tag)
    if not operation:
        hinted = extract_soap_action_operation(soap_action)
        if not hinted:
            raise SoapCodecError("Could not determine operation from Body or SOAPAction")
        operation = hinted

    data = _children_to_dict(list(wrapper))
    return SoapRequest(version=version, operation=operation, data=data)


# ---------------------------------------------------------------------------
# Response encoding
# ---------------------------------------------------------------------------


def _append_value(parent: Element, tag: str, value: Any) -> None:
    """Append ``tag`` child element(s) representing ``value`` to ``parent``."""
    if isinstance(value, dict):
        el = SubElement(parent, tag)
        for key, sub_value in value.items():
            _append_value(el, key, sub_value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _append_value(parent, tag, item)
    elif isinstance(value, bool):
        el = SubElement(parent, tag)
        el.text = "1" if value else "0"
    elif value is None:
        SubElement(parent, tag)
    else:
        el = SubElement(parent, tag)
        el.text = str(value)


def response_operation_name(operation: str) -> str:
    """Znuny's default ``ResponseNameScheme: Response`` (== ``Append`` +
    ``Response``): ``TicketGet`` -> ``TicketGetResponse``."""
    return f"{operation}Response"


def build_soap_response(
    operation: str,
    result: dict[str, Any],
    *,
    namespace: str = DEFAULT_NAMESPACE,
    version: str = "1.1",
) -> bytes:
    """Serialize an operation result dict into a SOAP response envelope.

    ``result`` must not contain an ``Error`` key — use
    :func:`build_soap_fault` for error responses (Znuny always emits a SOAP
    Fault, never a normal envelope, for GenericInterface errors).
    """
    envelope_ns = SOAP11_NS if version == "1.1" else SOAP12_NS
    envelope = Element(f"{{{envelope_ns}}}Envelope")
    body = SubElement(envelope, f"{{{envelope_ns}}}Body")

    wrapper_tag = response_operation_name(operation)
    wrapper = SubElement(body, wrapper_tag)
    wrapper.set("xmlns", namespace)
    for key, value in result.items():
        _append_value(wrapper, key, value)

    return _serialize(envelope)


def build_soap_fault(
    fault_string: str,
    *,
    fault_code: str = "Server",
    version: str = "1.1",
) -> bytes:
    """Serialize a GenericInterface error as a SOAP Fault envelope.

    Matches ``SOAP.pm`` ``ProviderGenerateResponse`` when ``ErrorMessage`` is
    set: ``OperationResponse = 'Fault'`` with ``faultcode``/``faultstring``.
    """
    envelope_ns = SOAP11_NS if version == "1.1" else SOAP12_NS
    envelope = Element(f"{{{envelope_ns}}}Envelope")
    body = SubElement(envelope, f"{{{envelope_ns}}}Body")

    if version == "1.1":
        fault = SubElement(body, "Fault")
        SubElement(fault, "faultcode").text = fault_code
        SubElement(fault, "faultstring").text = fault_string
    else:
        fault = SubElement(body, f"{{{envelope_ns}}}Fault")
        code_el = SubElement(fault, f"{{{envelope_ns}}}Code")
        SubElement(code_el, f"{{{envelope_ns}}}Value").text = f"soap12:{fault_code}"
        reason_el = SubElement(fault, f"{{{envelope_ns}}}Reason")
        SubElement(reason_el, f"{{{envelope_ns}}}Text").text = fault_string

    return _serialize(envelope)


def content_type_for_version(version: str) -> str:
    return _CONTENT_TYPE_BY_VERSION.get(version, _CONTENT_TYPE_BY_VERSION["1.1"])


def _serialize(envelope: Element) -> bytes:
    xml_body: bytes = tostring(envelope, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body


__all__ = [
    "DEFAULT_NAMESPACE",
    "SOAP11_NS",
    "SOAP12_NS",
    "SoapCodecError",
    "SoapRequest",
    "build_soap_fault",
    "build_soap_response",
    "content_type_for_version",
    "extract_soap_action_operation",
    "parse_soap_request",
    "response_operation_name",
]
