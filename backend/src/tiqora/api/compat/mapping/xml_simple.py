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


def _value_to_xml(value: Any, name: str, indent: str, no_attr: bool = True) -> str:
    """``value_to_xml``; *name* ``""`` means "no root element" (``RootName => undef``).

    With ``no_attr`` false (``NoAttr => 0``) the scalar members of a hash become attributes,
    one per line after the first (``AttrIndent => 1``); the key ``content`` is the text content.
    """
    named = name != ""
    if _is_scalar(value):
        if named:
            return f"{indent}<{name}>{_escape(value)}</{name}>\n"
        return f"{_escape(value)}\n"
    if isinstance(value, dict):
        head: list[str] = [f"{indent}<{name}"] if named else []
        nested: list[str] = []
        text_content: str | None = None
        if value:
            first_arg = True
            for key in sorted(value, key=str):
                skey = str(key)
                item = value[key]
                if skey.startswith("-"):
                    continue
                if item is None:
                    if skey == _CONTENT:
                        text_content = ""
                    item = ""
                if isinstance(item, (dict, list, tuple)) or no_attr:
                    nested.append(_value_to_xml(item, skey, indent + "  ", no_attr))
                elif skey == _CONTENT:
                    text_content = _escape(item)
                else:
                    if not first_arg:
                        head.append("\n" + indent + " " + " " * len(name))
                    head.append(f' {skey}="{_escape(item)}"')
                    first_arg = False
        else:
            text_content = ""
        if nested or text_content is not None:
            if not named:
                return "".join(nested)  # special case: no root element
            head.append(">")
            if text_content is not None:
                head.append(text_content)
                if nested:
                    nested[0] = nested[0].lstrip()
            else:
                head.append("\n")
            if nested:
                head.extend(nested)
                head.append(indent)
            head.append(f"</{name}>\n")
        else:
            head.append(" />\n")
        return "".join(head)
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for item in value:
            if _is_scalar(item):
                parts.append(f"{indent}<{name}>{_escape(item)}</{name}>\n")
            elif isinstance(item, dict):
                parts.append(_value_to_xml(item, name, indent, no_attr))
            else:
                inner = _value_to_xml(item, "anon", indent + "  ", no_attr)
                parts.append(f"{indent}<{name}>\n{inner}{indent}</{name}>\n")
        return "".join(parts)
    raise XmlSimpleError(f"Can't encode a value of type: {type(value).__name__}")


def xml_out(data: Any, *, no_attr: bool = True, root_name: str | None = "RootElement") -> str:
    """``XMLout(..., NoAttr => no_attr, RootName => root_name)``; ``root_name=None`` drops the root.

    Without a root element Perl first wraps every top-level scalar into a one-element list, so
    even with ``no_attr=False`` the top level never produces attributes.
    """
    if not isinstance(data, dict):
        raise XmlSimpleError(_OUT_ERROR)
    name = root_name or ""
    if not name:
        data = {k: (v if isinstance(v, (dict, list, tuple)) else [v]) for k, v in data.items()}
    try:
        return _value_to_xml(data, name, "", no_attr)
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
