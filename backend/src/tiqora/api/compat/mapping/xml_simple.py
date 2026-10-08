"""Port of the parts of ``XML::Simple`` that ``Mapping::XSLT`` uses.

``xml_out`` mirrors ``XMLout(AttrIndent => 1, ContentKey => '-content', NoAttr => 1,
KeyAttr => [], RootName => 'RootElement')`` and ``xml_in`` mirrors ``XMLin(ForceArray,
ContentKey => '-content', NoAttr => !keep, ForceContent => keep, KeyAttr => [])``
(``znuny-6.5.22/Kernel/cpan-lib/XML/Simple.pm``: ``value_to_xml`` / ``collapse``).

Quirks kept on purpose (Znuny behaviour, clients depend on it): a list with a single
element comes back as the element itself, and an empty string comes back as ``{}``.

Parsing is XXE-safe: no entity resolution, no network, no DTD loading.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from tiqora.api.compat.mapping import MappingError

_OUT_ERROR = "Could not convert data from Perl to XML before mapping"
_IN_ERROR = "Could not convert data from XML to Perl after mapping"
_CONTENT = "content"


class XmlSimpleError(MappingError):
    """Conversion failed (str = Znuny's Summary)."""


# --------------------------------------------------------------------- XMLout


def _escape(value: Any) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float))


def _value_to_xml(value: Any, name: str, indent: str) -> str:
    if _is_scalar(value):
        return f"{indent}<{name}>{_escape(value)}</{name}>\n"
    if isinstance(value, dict):
        nested: list[str] = []
        text_empty = not value  # empty hash -> text_content = ''
        for key in sorted(value, key=str):
            skey = str(key)
            if skey.startswith("-"):
                continue
            nested.append(_value_to_xml(value[key], skey, indent + "  "))
        if not nested and not text_empty:
            return f"{indent}<{name} />\n"
        if text_empty:
            return f"{indent}<{name}></{name}>\n"
        return f"{indent}<{name}>\n{''.join(nested)}{indent}</{name}>\n"
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for item in value:
            if _is_scalar(item):
                parts.append(f"{indent}<{name}>{_escape(item)}</{name}>\n")
            elif isinstance(item, dict):
                parts.append(_value_to_xml(item, name, indent))
            else:
                inner = _value_to_xml(item, "anon", indent + "  ")
                parts.append(f"{indent}<{name}>\n{inner}{indent}</{name}>\n")
        return "".join(parts)
    raise XmlSimpleError(f"Can't encode a value of type: {type(value).__name__}")


def xml_out(data: Any, *, root_name: str = "RootElement") -> str:
    if not isinstance(data, dict):
        raise XmlSimpleError(_OUT_ERROR)
    try:
        return _value_to_xml(data, root_name, "")
    except RecursionError as exc:
        raise XmlSimpleError(_OUT_ERROR) from exc


# ---------------------------------------------------------------------- XMLin


def _qname(prefix: str | None, local: str) -> str:
    return f"{prefix}:{local}" if prefix else local


def _element_name(el: Any) -> str:
    return _qname(el.prefix, etree.QName(el).localname)


def _attr_items(el: Any) -> list[tuple[str, str]]:
    reverse = {uri: prefix for prefix, uri in el.nsmap.items() if prefix}
    items: list[tuple[str, str]] = []
    for name, value in el.attrib.items():
        qn = etree.QName(name)
        prefix = reverse.get(qn.namespace) if qn.namespace else None
        items.append((_qname(prefix, qn.localname), str(value)))
    return items


def _add(attr: dict[str, Any], key: str, val: Any, force_array: frozenset[str] | bool) -> None:
    if key in attr:
        if isinstance(attr[key], list):
            attr[key].append(val)
        else:
            attr[key] = [attr[key], val]
    elif (
        isinstance(val, list)
        or key != _CONTENT
        and (force_array is True or (isinstance(force_array, frozenset) and key in force_array))
    ):
        attr[key] = [val]
    else:
        attr[key] = val


def _collapse(el: Any, force_array: frozenset[str] | bool, keep_attributes: bool) -> Any:
    attr: dict[str, Any] = {}
    if keep_attributes:
        for key, value in _attr_items(el):
            attr[key] = value

    # items in document order: ("t", text) | ("e", name, collapsed)
    items: list[tuple[str, Any, Any]] = []
    buf = el.text or ""
    for child in el:
        if isinstance(child.tag, str):
            if buf:
                items.append(("t", buf, None))
                buf = ""
            items.append(
                ("e", _element_name(child), _collapse(child, force_array, keep_attributes))
            )
        # comments / PIs / entity references vanish; surrounding text merges (SAX behaviour)
        buf += child.tail or ""
    if buf:
        items.append(("t", buf, None))

    for index, (kind, a, b) in enumerate(items):
        if kind == "t":
            if a.strip() == "":
                continue
            if not attr and index == len(items) - 1:
                return {_CONTENT: a} if keep_attributes else a
            _add(attr, _CONTENT, a, force_array)
        else:
            _add(attr, a, b, force_array)

    if len(attr) == 1 and isinstance(attr.get("anon"), list):
        return attr["anon"]
    return attr


def xml_in(
    xml: bytes,
    *,
    force_array: frozenset[str] | bool = False,
    keep_attributes: bool = False,
    huge_tree: bool = False,
) -> Any:
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=huge_tree,
    )
    try:
        root = etree.fromstring(xml, parser)
        result = _collapse(root, force_array, keep_attributes)
    except (etree.XMLSyntaxError, ValueError, RecursionError) as exc:
        raise XmlSimpleError(_IN_ERROR) from exc
    if isinstance(result, str) and result in ("", "0"):  # Znuny: `if !$ReturnData`
        raise XmlSimpleError(_IN_ERROR)
    return result
