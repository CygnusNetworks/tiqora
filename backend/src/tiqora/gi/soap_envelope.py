"""SOAP requester envelope codec (G8, pure: no DB, network or file system).

Port of the data-shaping half of ``Kernel/GenericInterface/Transport/HTTP/SOAP.pm``:
``_SOAPOutputRecursion`` / ``_SOAPOutputHashRecursion`` / ``_SOAPOutputProcessString`` /
``_SOAPOutputTypesGet`` (lines 1345-1696), the request/response name schemes and SOAPAction
schemes of ``RequesterPerformRequest`` (570-1106), and a parser for the answer.

Envelope layout: the header is the one ``SOAP::Serializer->autotype(0)`` writes (taken from
Znuny's ``Serialize.t``); the byte-exact comparison with a real Znuny peer is pinned by the
golden test of the plan (Task 19).

Deliberate differences (safe side):

* Element text is written once-escaped (``&``, ``<``, ``>``). Znuny escapes first and hands the
  result to SOAP::Lite; the receiver must see the original text either way.
* Element names that are not valid XML names are refused instead of producing broken XML.
* The response is parsed with ``defusedxml`` (no DTD, no entities, no network).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final
from xml.etree.ElementTree import Element, tostring  # noqa: S405 (build-only)

from defusedxml.ElementTree import fromstring

from tiqora.api.compat.mapping.perl import perl_str, perl_truthy
from tiqora.api.compat.soap import SOAP11_NS, SOAP12_NS, element_to_value

ENVELOPE_HEAD: Final = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soap:Envelope soap:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" '
    'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:soapenc="http://schemas.xmlsoap.org/soap/encoding/" '
    'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<soap:Body>"
)
ENVELOPE_TAIL: Final = "</soap:Body></soap:Envelope>"

DESERIALIZE_ERROR: Final = "SOAP Transport: Could not deserialize received XML data"
_NOT_FOUND_PREFIX: Final = "No response data found for specified operation"

_RESTRICTED_RE: Final = re.compile("[\x01-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f]")
_NAME_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")
_XML_DECL_RE: Final = re.compile(r"\A\s*<\?xml[^>]*\?>")


class SoapOutputError(Exception):
    """Data or sort structure that has no XML representation (message as in Znuny)."""


# ---------------------------------------------------------------------------- strings


def process_string(text: Any) -> str:
    """``_SOAPOutputProcessString``: escape ``& < >`` and drop restricted characters."""
    if text is None:
        return ""
    value = perl_str(text)
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _RESTRICTED_RE.sub("", value)


def _clean_text(value: Any) -> str:
    """Element text before serialisation (ElementTree does the escaping)."""
    return _RESTRICTED_RE.sub("", perl_str(value))


# ---------------------------------------------------------------------------- types


def _types_get(data: Any, sort: Any) -> tuple[str, str, Any, Any]:
    """``_SOAPOutputTypesGet``: returns ``(data_type, sort_type, data, sort)``."""
    types: dict[str, str] = {}
    values = {"Data": data, "Sort": sort}
    for key in ("Data", "Sort"):
        value = values[key]
        if value is None:
            types[key] = "UNDEFINED"
        elif isinstance(value, list) and value:
            types[key] = "ARRAYREF"
        elif isinstance(value, dict) and value:
            types[key] = "HASHREF"
        elif (key == "Data" and isinstance(value, dict)) or (
            key == "Sort" and isinstance(value, list)
        ):
            values[key] = None
            types[key] = "UNDEFINED"
        elif isinstance(value, (dict, list)):
            kind = "HASH" if isinstance(value, dict) else "ARRAY"
            raise SoapOutputError(f"{key} type '{kind} (empty)' is invalid")
        elif isinstance(value, (str, int, float, bool)):
            types[key] = "STRING"
        else:
            raise SoapOutputError(f"{key} type '{type(value).__name__}' is invalid")

    if types["Data"] == "UNDEFINED" and types["Sort"] != "UNDEFINED":
        types["Sort"] = "UNDEFINED"

    if (
        types["Sort"] != "UNDEFINED"
        and types["Data"] != types["Sort"]
        and not (types["Data"] == "HASHREF" and types["Sort"] == "ARRAYREF")
    ):
        raise SoapOutputError(
            f"Types of Data '{types['Data']}' and Sort '{types['Sort']}' don't match"
        )

    if types["Sort"] == "ARRAYREF":
        for element in values["Sort"]:
            if not isinstance(element, dict) or not element:
                raise SoapOutputError("Element of sort array is not a hash reference")
            keys = sorted(element)
            if len(keys) != 1:
                raise SoapOutputError(
                    "Sort array element hash reference must contain exactly one key/value pair"
                )
            if not str(keys[0]):
                raise SoapOutputError(
                    "Key of sort array element hash reference must be a non zero-length string"
                )
    return types["Data"], types["Sort"], values["Data"], values["Sort"]


# ---------------------------------------------------------------------------- output


def soap_output(data: Mapping[str, Any], *, sort: Any = None) -> list[Element]:
    """``_SOAPOutputRecursion`` for a (non-empty) top-level hash: the body elements.

    Element order: keys listed in *sort* first (a list of one-key hashes, recursive, values
    describe the sub-structure), then the remaining keys sorted. Lists repeat the element;
    ``None`` and an empty hash become an empty element.
    """
    out: list[Element] = []
    dtype, stype, cdata, csort = _types_get(dict(data), sort)
    if dtype == "UNDEFINED":
        return out
    _hash_children(out, cdata, csort if stype == "ARRAYREF" else None)
    return out


def _hash_children(out: list[Element] | Element, data: dict[str, Any], sort: Any) -> None:
    unsorted = dict(data)
    ordered: list[tuple[str, Any, Any]] = []
    if isinstance(sort, list):
        for entry in sort:
            key = sorted(entry)[0]
            if key not in unsorted:
                continue
            ordered.append((key, unsorted.pop(key), entry[key]))
    for key in sorted(unsorted, key=str):
        ordered.append((key, unsorted[key], None))
    for key, value, sub_sort in ordered:
        _append(out, str(key), value, sub_sort)


def _append(out: list[Element] | Element, key: str, value: Any, sort: Any) -> None:
    if not _NAME_RE.fullmatch(key):
        raise SoapOutputError(f"Element name '{key}' is not a valid XML name")
    dtype, stype, value, sort = _types_get(value, sort)
    if dtype == "ARRAYREF":
        for item in value:
            if isinstance(item, list):
                raise SoapOutputError("Array inside array is not permitted")
            _append(out, key, item, sort)
        return
    element = Element(key)
    out.append(element)
    if dtype == "STRING":
        element.text = _clean_text(value)
    elif dtype == "HASHREF":
        _hash_children(element, value, sort if stype == "ARRAYREF" else None)
    else:
        element.text = ""


# ---------------------------------------------------------------------------- names


def soap_action(config: Mapping[str, Any], *, operation: str, operation_name: str) -> str:
    """The SOAPAction value *without* the surrounding double quotes."""
    action = config.get("SOAPAction")
    scheme = config.get("SOAPActionScheme")
    separator = config.get("SOAPActionSeparator")
    namespace = perl_str(config.get("NameSpace"))
    if action is None or perl_str(action) == "":
        action, scheme, separator = "Yes", "NameSpaceSeparatorOperation", "#"
    if perl_str(action) == "No":
        return ""
    scheme_text = perl_str(scheme) or "NameSpaceSeparatorOperation"
    sep = perl_str(separator)
    free_text = perl_str(config.get("SOAPActionFreeText"))
    if scheme_text == "FreeText" and free_text:
        return free_text
    if scheme_text == "Operation":
        return operation
    if scheme_text == "SeparatorOperation" and sep:
        return sep + operation_name
    if scheme_text == "NameSpaceSeparatorOperation" and sep:
        return namespace + sep + operation_name
    return ""


def request_name(config: Mapping[str, Any], operation_name: str) -> str:
    scheme = perl_str(config.get("RequestNameScheme")) or "Plain"
    free_text = config.get("RequestNameFreeText")
    if scheme == "Request":
        scheme, free_text = "Append", "Request"
    if scheme == "Append" and perl_truthy(free_text):
        return operation_name + perl_str(free_text)
    return operation_name


def response_name(config: Mapping[str, Any], operation_name: str) -> str:
    scheme = perl_str(config.get("ResponseNameScheme")) or "Response"
    free_text = config.get("ResponseNameFreeText")
    if scheme == "Response":
        scheme, free_text = "Append", "Response"
    name = operation_name
    if perl_truthy(free_text):
        if scheme == "Append":
            name += perl_str(free_text)
        elif scheme == "Replace":
            name = perl_str(free_text)
    suffix = config.get("ResponseTagSuffix")
    if suffix is not None and perl_str(suffix) != "":
        name += perl_str(suffix)
    return name


# ---------------------------------------------------------------------------- envelope


def build_request_envelope(
    *,
    operation_request: str,
    namespace: str | None,
    no_namespace: bool,
    fixed_prefix: str | None,
    body: list[Element],
    soap_version: str = "1.1",
) -> bytes:
    """The request envelope (UTF-8). The method element is *operation_request*:

    * ``no_namespace`` and a namespace: ``<Op xmlns="ns">``; without one: ``<Op>``;
    * otherwise ``<Op xmlns="ns">`` or, with *fixed_prefix*, ``<prefix:Op xmlns:prefix="ns">``.
    """
    if soap_version != "1.1":
        raise ValueError("only SOAP 1.1 is supported for requests")
    if not _NAME_RE.fullmatch(operation_request):
        raise SoapOutputError(f"Element name '{operation_request}' is not a valid XML name")
    ns = namespace or ""
    prefix = fixed_prefix or ""
    if prefix and not _NAME_RE.fullmatch(prefix):
        raise SoapOutputError(f"Namespace prefix '{prefix}' is not a valid XML name")
    if no_namespace:
        method = Element(operation_request)
        if ns:
            method.set("xmlns", ns)
    elif prefix:
        method = Element(f"{prefix}:{operation_request}")
        method.set(f"xmlns:{prefix}", ns)
    else:
        method = Element(operation_request)
        method.set("xmlns", ns)
    for child in body:
        method.append(child)
    xml = tostring(method, encoding="unicode", short_empty_elements=False)
    return (ENVELOPE_HEAD + xml + ENVELOPE_TAIL).encode("utf-8")


# ---------------------------------------------------------------------------- response


def _fault_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_fault_text(v) for _, v in sorted(value.items()))
    if isinstance(value, list):
        return " ".join(_fault_text(v) for v in value)
    return perl_str(value)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[1] if tag.startswith("{") else tag


def parse_response(
    xml: bytes,
    *,
    response_name: str,
    encoding: str | None = None,
    operation_name: str | None = None,
) -> tuple[Any, str | None]:
    """Parse the answer: ``(data, None)`` or ``(None, error message)``.

    *encoding* (Znuny ``Encoding``) converts a non-UTF-8 answer first. A Fault gives
    ``"faultcode: Server, faultstring: boom"`` (keys sorted); the response element must be
    named *response_name*; its content is decoded like the provider decodes requests.
    """
    if encoding and not re.fullmatch(r"utf-?8", encoding, re.IGNORECASE):
        try:
            text = xml.decode(encoding, errors="replace")
        except LookupError:
            return None, f"SOAP Transport: Unsupported Encoding '{encoding}'"
        xml = _XML_DECL_RE.sub("", text, count=1).encode("utf-8")
    try:
        root = fromstring(xml)
    except Exception:  # noqa: BLE001 - ParseError and every defusedxml guard exception
        return None, DESERIALIZE_ERROR
    if root.tag not in (f"{{{SOAP11_NS}}}Envelope", f"{{{SOAP12_NS}}}Envelope"):
        return None, DESERIALIZE_ERROR
    body = next((c for c in root if _local(c.tag) == "Body"), None)
    if body is None or len(body) == 0:
        return None, DESERIALIZE_ERROR
    body_value = element_to_value(body)
    if not isinstance(body_value, dict):
        return None, DESERIALIZE_ERROR

    if "Fault" in body_value:
        fault = body_value["Fault"]
        if not isinstance(fault, dict):
            return None, f"Fault: {_fault_text(fault)}"
        return None, ", ".join(f"{k}: {_fault_text(fault[k])}" for k in sorted(fault))

    if response_name not in body_value:
        shown = operation_name if operation_name is not None else response_name
        return None, f"{_NOT_FOUND_PREFIX} '{shown}' in soap response"
    value = body_value[response_name]
    truthy = bool(value) if isinstance(value, (dict, list)) else perl_truthy(value)
    return (value if truthy else None), None


def is_fault_error(error: str) -> bool:
    """True when :func:`parse_response` reported a remote Fault (not a parse/shape problem)."""
    return error != DESERIALIZE_ERROR and not error.startswith(
        (_NOT_FOUND_PREFIX, "SOAP Transport: Unsupported Encoding")
    )
