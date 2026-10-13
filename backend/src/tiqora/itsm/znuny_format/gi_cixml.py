"""GenericInterface ``CIXMLData`` <-> Znuny ``XMLData`` (port of ``ConfigItem/Common.pm``).

Source: ITSMConfigurationManagement 6.5.4 and 7.3.5, ``Kernel/GenericInterface/Operation/ConfigItem/
Common.pm`` (``CheckXMLData``, ``ReplaceXMLData``, ``FormatXMLData``, ``InvertFormatXMLData``,
``InvertReplaceXMLData`` and the ``Validate*``/``Replace*`` helpers). The two versions are
identical for these functions (7.3 only adds ``Valid => 1`` to the catalog ``ItemList`` call,
which the caller's :class:`GiLookups` folds in), so there is one code path and no profile.

``CIXMLData`` is what a GI client sends: a single entry is a scalar or a hash, several entries an
array, a hash entry carries its own value under its own key (``{"NIC": {"NIC": "eth0",
"IPAddress": "10.0.0.1"}}``). ``XMLData`` is the shape this package uses for stored Znuny CI data
(see :mod:`~tiqora.itsm.znuny_format.xmldata`): ``{"Key": [{"Content": "x", "Sub": [...]}]}``
without Znuny's ``undef`` placeholders and ``Version`` wrapper.

Znuny behaviour kept on purpose (the golden suite compares against the real peers):

* ``Content`` of a first-level entry that is falsy in Perl (``"0"``) comes back as ``""``
  (``delete $Item->{Content} || ''``), so such vectors are one-way;
* a scalar given for a key that has a ``Sub`` definition is replaced by an empty hash in the
  replace steps;
* of several entries of a sub attribute only the last one survives the inversion, and sub
  attributes below the second level are not inverted (Znuny dies or drops them; we drop them);
* keys outside the definition are not checked and pass through unchanged;
* ``Date``/``DateTime`` values must be ``MM/DD/YYYY``, ``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM:SS``
  (``MM/DD/YYYY HH:MM:SS`` does not match Znuny's own regex either).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final, Protocol

from tiqora.itsm.znuny_format._limits import MAX_XML_DEPTH, MAX_XML_LEAVES, guard_xml_data
from tiqora.util.perl import perl_truthy

_US_DATE = re.compile(r"([0-9]{2})/([0-9]{2})/([0-9]{4})")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ISO_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")
_INTEGER = re.compile(r"-?[0-9]+")
_ZERO_TIME_SUFFIX = re.compile(r"\A([0-9]{4}-[0-9]{2}-[0-9]{2}) 00:00:00\Z")

_REPLACE_TYPES: Final = frozenset({"Date", "DateTime", "GeneralCatalog"})
_INVERT_TYPES: Final = frozenset({"Date", "GeneralCatalog"})


class CixmlError(Exception):
    """A GI parameter error; ``code`` is the suffix after ``<Operation>.`` (e.g. ``NotFound``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GiLookups(Protocol):
    """Reference data the conversion needs; the operation preloads it (sync, no I/O)."""

    def catalog_id(self, catalog_class: str, name: str) -> int | None: ...

    def catalog_name(self, catalog_id: int) -> str | None: ...

    def customer_user_exists(self, login: str) -> bool: ...

    def customer_company_exists(self, customer_id: str) -> bool: ...


# ---------------------------------------------------------------- helpers


def _is_scalar(value: Any) -> bool:
    """Perl ``ref $x eq ''`` (includes ``undef``)."""
    return value is None or isinstance(value, str | int | float | bool)


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _clean_scalar(value: str) -> str:
    return re.sub(r"\s+\Z", "", re.sub(r"\A\s+", "", value))


def clean_cixml(cixml: Any) -> Any:
    """Trim leading and trailing white space of every string (``_CleanXMLData`` of Create/Update).

    Returns a copy; the operations call it before :func:`cixml_to_xmldata`.
    """
    if isinstance(cixml, Mapping):
        return {k: clean_cixml(v) for k, v in cixml.items()}
    if isinstance(cixml, list):
        return [clean_cixml(v) for v in cixml]
    if isinstance(cixml, str):
        return _clean_scalar(cixml)
    return cixml


def _guard_cixml(cixml: Any, operation: str) -> None:
    """Bound depth and size of client-supplied data before any recursion over it."""
    leaves = 0
    stack: list[tuple[Any, int]] = [(cixml, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > 2 * MAX_XML_DEPTH + 2:
            raise CixmlError(
                "InvalidParameter",
                f"{operation}: ConfigItem->CIXMLData is nested too deeply!",
            )
        children = (
            list(node.values())
            if isinstance(node, Mapping)
            else node
            if isinstance(node, list)
            else None
        )
        if children is None:
            leaves += 1
        else:
            leaves += len(children)
            stack.extend((c, depth + 1) for c in children)
        if leaves > MAX_XML_LEAVES:
            raise CixmlError(
                "InvalidParameter",
                f"{operation}: ConfigItem->CIXMLData has too many values!",
            )


def guard_cixml(cixml: Any, operation: str) -> None:
    """Public form of the size/depth bound for operations that read ``CIXMLData`` without
    converting it (``ConfigItemSearch``); raises :class:`CixmlError`."""
    _guard_cixml(cixml, operation)


def _int_attr(item: Mapping[str, Any], key: str) -> int | None:
    raw = item.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _input(item: Mapping[str, Any]) -> Mapping[str, Any]:
    inp = item.get("Input")
    return inp if isinstance(inp, Mapping) else {}


def _sub(item: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    sub = item.get("Sub")
    return sub if isinstance(sub, list) else None


# ---------------------------------------------------------------- dates


def _normalise_date(value: str) -> str:
    """The string Znuny hands to ``Kernel::System::DateTime`` after its own regex rewrites."""
    m = _US_DATE.fullmatch(value)
    if m:
        value = f"{m.group(3)}-{m.group(1)}-{m.group(2)} 00:00:00"
    elif _ISO_DATE.fullmatch(value):
        value = f"{value} 00:00:00"
    return value


def _parse_datetime(raw: Any) -> datetime | None:
    value = _normalise_date(_str(raw))
    if not _ISO_DATETIME.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_gi_datetime(raw: Any) -> datetime | None:
    """``ValidateInputDateTime``: ``MM/DD/YYYY``, ``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM:SS``
    (the date forms mean midnight), else ``None``."""
    return _parse_datetime(raw)


# ---------------------------------------------------------------- check


def _msg(operation: str, parent: str, key: str, tail: str) -> str:
    return f"{operation}: ConfigItem->CIXMLData->{parent}{key} {tail}"


def _check_value(
    value: Any,
    inp: Mapping[str, Any],
    key: str,
    parent: str,
    lookups: GiLookups,
    operation: str,
) -> None:
    if perl_truthy(inp.get("Required")) and not perl_truthy(value):
        raise CixmlError(
            "MissingParameter",
            _msg(operation, parent, key, "parameter value is required and is missing!"),
        )
    kind = inp.get("Type")
    text = _str(value)
    bad: str | None = None
    if kind in ("Text", "TextArea"):
        max_len = _int_attr(inp, "MaxLength")
        if max_len and len(text) > max_len:
            bad = "parameter value excedes the maxium length!"
    elif kind == "Date":
        if _parse_datetime(value) is None:
            bad = "parameter value is not a valid Date format!"
    elif kind == "DateTime":
        if _parse_datetime(value) is None:
            bad = "parameter value is not a valid DateTime format!"
    elif kind == "Customer":
        if not perl_truthy(value) or not lookups.customer_user_exists(text):
            bad = "parameter value is not a valid customer!"
    elif kind == "CustomerCompany":
        if not perl_truthy(value) or not lookups.customer_company_exists(text):
            bad = "parameter value is not a valid customer company!"
    elif kind == "Integer":
        # ValueMin/ValueMax are deliberately NOT enforced: Znuny's _CheckValue
        # calls ValidateInputInteger with only Value/Input/ItemKey/Parent, so
        # the range attributes never reach it (Common.pm:314). Only IsInteger.
        if not _INTEGER.fullmatch(text):
            bad = "parameter value is not a valid Integer or out of range!"
    elif kind == "GeneralCatalog":
        found = lookups.catalog_id(_str(inp.get("Class")), text)
        if not perl_truthy(found):
            bad = f"parameter value is not a valid for General Catalog '{_str(inp.get('Class'))}'!"
    # any other type ("Dummy", ...) is not validated
    if bad is not None:
        raise CixmlError("InvalidParameter", _msg(operation, parent, key, bad))


def _check_xml(
    definition: list[dict[str, Any]],
    xml: Any,
    parent: str,
    lookups: GiLookups,
    operation: str,
) -> None:
    data: Mapping[str, Any] = xml if isinstance(xml, Mapping) else {}
    for item in definition:
        key = str(item["Key"])
        inp = _input(item)
        count_min = _int_attr(item, "CountMin")
        present = data.get(key)
        if (
            count_min is not None
            and count_min >= 1
            and perl_truthy(inp.get("Required"))
            and not perl_truthy(present)
        ):
            raise CixmlError(
                "MissingParameter", _msg(operation, parent, key, "parameter is missing!")
            )
        if isinstance(present, list):
            for entry in present:
                if isinstance(entry, Mapping):
                    _check_value(entry.get(key), inp, key, parent, lookups, operation)
                elif _is_scalar(entry):
                    _check_value(entry, inp, key, parent, lookups, operation)
                else:
                    raise CixmlError(
                        "InvalidParameter", _msg(operation, parent, key, "parameter is invalid!")
                    )
        elif isinstance(present, Mapping):
            _check_value(present.get(key), inp, key, parent, lookups, operation)
        elif perl_truthy(present):
            _check_value(present, inp, key, parent, lookups, operation)
        count_max = _int_attr(item, "CountMax")
        if count_max is not None and isinstance(present, list) and len(present) > count_max:
            raise CixmlError(
                "InvalidParameter",
                _msg(
                    operation, parent, key, "parameter repetitions is higher than the maxium value!"
                ),
            )
        sub = _sub(item)
        if sub is not None:
            if isinstance(present, list):
                for idx, entry in enumerate(present):
                    _check_xml(sub, entry, f"{parent}{key}[{idx}]->", lookups, operation)
            elif isinstance(present, Mapping):
                _check_xml(sub, present, f"{parent}{key}->", lookups, operation)
            else:
                _check_xml(sub, {}, f"{parent}{key}->", lookups, operation)


# ---------------------------------------------------------------- replace / invert replace


def _replace_value(value: Any, inp: Mapping[str, Any], lookups: GiLookups) -> Any:
    kind = inp.get("Type")
    if kind not in _REPLACE_TYPES:
        return value
    if kind == "GeneralCatalog":
        return lookups.catalog_id(_str(inp.get("Class")), _str(value))
    parsed = _parse_datetime(value)
    if parsed is None:
        return value
    return parsed.strftime("%Y-%m-%d" if kind == "Date" else "%Y-%m-%d %H:%M:%S")


def _invert_replace_value(value: Any, inp: Mapping[str, Any], lookups: GiLookups) -> Any:
    kind = inp.get("Type")
    if kind not in _INVERT_TYPES:
        return value
    if kind == "Date":
        return _ZERO_TIME_SUFFIX.sub(r"\1", _str(value)) if value is not None else value
    try:
        return lookups.catalog_name(int(_str(value)))
    except ValueError:
        return None


def _walk_values(
    definition: list[dict[str, Any]],
    xml: Any,
    convert: Any,
    lookups: GiLookups,
    *,
    require_dict: bool,
) -> Any:
    """Shared body of ``ReplaceXMLData`` / ``InvertReplaceXMLData`` (mutates ``xml``)."""
    if require_dict and not (isinstance(xml, dict) and xml):
        # Znuny: IsHashRefWithData() false -> nothing to replace (a string here would die in Perl)
        return xml
    if not isinstance(xml, dict):
        return xml
    for item in definition:
        key = str(item["Key"])
        inp = _input(item)
        present = xml.get(key)
        if isinstance(present, list):
            for idx, entry in enumerate(present):
                if isinstance(entry, dict):
                    if key in entry:
                        entry[key] = convert(entry[key], inp, lookups)
                elif _is_scalar(entry):
                    present[idx] = convert(entry, inp, lookups)
        elif isinstance(present, dict):
            if key in present:
                present[key] = convert(present[key], inp, lookups)
        elif perl_truthy(present):
            xml[key] = convert(present, inp, lookups)
        sub = _sub(item)
        if sub is None:
            continue
        present = xml.get(key)
        if isinstance(present, list):
            for idx, entry in enumerate(present):
                present[idx] = _walk_values(sub, entry, convert, lookups, require_dict=require_dict)
        elif isinstance(present, dict):
            xml[key] = _walk_values(sub, present, convert, lookups, require_dict=require_dict)
        elif perl_truthy(present):
            xml[key] = _walk_values(sub, {}, convert, lookups, require_dict=require_dict)
    return xml


# ---------------------------------------------------------------- format / invert format


def _content(value: Any) -> str | None:
    return None if value is None else str(value)


def _format(cixml: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """``FormatXMLData``: scalars/hashes/arrays -> ``{Key: [{"Content": ..., <sub keys>}]}``."""
    out: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(cixml):
        value = cixml[key]
        entries: list[dict[str, Any]] = []
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, Mapping):
                    rest = {k: v for k, v in entry.items() if k != key}
                    entries.append({"Content": _content(entry.get(key)), **_format(rest)})
                elif _is_scalar(entry):
                    entries.append({"Content": _content(entry)})
        elif isinstance(value, Mapping):
            rest = {k: v for k, v in value.items() if k != key}
            entries.append({"Content": _content(value.get(key)), **_format(rest)})
        elif _is_scalar(value):
            entries.append({"Content": _content(value)})
        else:
            continue
        out[key] = entries
    return out


def _normalise_content(value: Any) -> Any:
    return "" if value is None else value if isinstance(value, str) else str(value)


def _item_value(key: str, item: Mapping[str, Any]) -> Any:
    """One first-level entry: its content, or a hash with the sub attributes beside it."""
    content = item.get("Content")
    content = _normalise_content(content) if perl_truthy(content) else ""
    rest = {k: v for k, v in item.items() if k not in ("Content", "TagKey")}
    if not rest:
        return content
    out: dict[str, Any] = {key: content}
    for sub_key in sorted(rest):
        sub_entries = rest[sub_key]
        out[sub_key] = _invert_nested(sub_entries if isinstance(sub_entries, list) else [])
    return out


def _invert_nested(entries: list[Any]) -> Any:
    """The recursive ``InvertFormatXMLData`` call on a sub attribute (last entry wins)."""
    new: Any = None
    for root in entries:
        if not isinstance(root, Mapping):
            continue
        for key in sorted(root):
            if key == "TagKey":
                continue
            value = root[key]
            if key == "Content" and not isinstance(value, list):
                new = _normalise_content(value)
            elif isinstance(value, list):
                # attributes below the second level: Znuny dies or drops them, so do we
                continue
    return new


def _invert_format(xml_data: Mapping[str, Any]) -> dict[str, Any]:
    """The top level of ``InvertFormatXMLData``: ``{Key: [entries]}`` -> GI shape."""
    out: dict[str, Any] = {}
    for key in sorted(xml_data):
        if key == "TagKey":
            continue
        raw = xml_data[key]
        if not isinstance(raw, list):
            continue
        entries = [e for e in raw if isinstance(e, Mapping)]
        if len(entries) > 1:
            out[key] = [_item_value(key, e) for e in entries]
        elif entries:
            out[key] = _item_value(key, entries[0])
    return out


# ---------------------------------------------------------------- public API


def cixml_to_xmldata(
    cixml: Mapping[str, Any],
    definition: list[dict[str, Any]],
    *,
    lookups: GiLookups,
    operation: str = "ConfigItemCreate",
) -> dict[str, Any]:
    """Validate GI ``CIXMLData`` against a Znuny class definition and convert it to ``XMLData``.

    ``definition`` is the Znuny definition list (``Key``, ``Input``, ``CountMin``, ``CountMax``,
    ``Sub``). Catalog names become ids, dates are normalised. Raises :class:`CixmlError` with
    Znuny's error code and message (``operation`` is the GI operation named in the message).
    """
    _guard_cixml(cixml, operation)
    _check_xml(definition, cixml, "", lookups, operation)
    replaced = _walk_values(
        definition, copy.deepcopy(dict(cixml)), _replace_value, lookups, require_dict=False
    )
    return _format(replaced)


def xmldata_to_cixml(
    xml_data: Mapping[str, Any],
    definition: list[dict[str, Any]],
    *,
    lookups: GiLookups,
) -> dict[str, Any]:
    """Stored ``XMLData`` -> the ``CIXMLData`` a ``ConfigItemGet`` answers with.

    Raises ``ValueError`` for data nested deeper than 16 levels or with over 50 000 values.
    """
    guard_xml_data(xml_data)
    formatted = _invert_format(xml_data)
    result = _walk_values(definition, formatted, _invert_replace_value, lookups, require_dict=True)
    return result if isinstance(result, dict) else {}
