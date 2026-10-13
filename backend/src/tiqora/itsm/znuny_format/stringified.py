"""Znuny's "stringified XMLData" (``Key::N::Sub::M`` flat keys) and its merge rules.

Ports of ``XMLData2StringifiedXMLData``, ``StringifiedXMLData2XMLData`` and the key expansion in
``ConfigItemAttributesGet`` (Znuny 6.5 ``ProcessManagement/TransitionAction/ConfigItemUpdate.pm``),
with Perl truthiness kept (``""``, ``"0"`` and ``None`` are false). Deviations, all deliberate:

* ``$Counter gt $ItemCountMin`` compares as strings in Perl, but its branch is redundant with the
  next line (both skip when neither side has a value), so it is not ported at all.
* The counter loop runs at most :data:`MAX_COUNT` times however large ``CountMax`` is.
* The ``Separator`` regex is Znuny's (``split /\\s*$Separator\\s*/``); an invalid or oversized one
  raises ``ValueError`` instead of dying.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, Final

from tiqora.itsm.znuny_format._limits import guard_xml_data
from tiqora.util.perl import perl_truthy

#: attribute iteration cap for a hostile ``CountMax``
MAX_COUNT: Final = 10_000
MAX_SEPARATOR_CHARS: Final = 64

#: the keys ``ConfigItemAttributesGet`` keeps as CI header fields (never attributes)
HEADER_KEYS: Final = (
    "ConfigItemID",
    "Name",
    "ClassName",
    "ClassID",
    "DeplStateID",
    "InciStateID",
    "DeplStateName",
    "InciStateName",
)
_NOT_ATTRIBUTES: Final = frozenset((*HEADER_KEYS, "ConfigItemNumber", "UserID"))


def _scalar(value: Any) -> str:
    return "" if value is None else str(value)


def _stringify(xml_data: Mapping[str, Any], prefix: str, out: dict[str, str]) -> None:
    for item in sorted(xml_data):
        entries = xml_data[item]
        if not isinstance(entries, list) or not entries:
            continue
        counter = 1
        for sub_item in entries:
            if not isinstance(sub_item, Mapping):
                continue
            key = f"{item}::{counter}"
            if prefix:
                key = f"{prefix}::{key}"
            content = sub_item.get("Content")
            if perl_truthy(content):
                out[key] = _scalar(content)
            rest = {k: v for k, v in sub_item.items() if k != "Content"}
            _stringify(rest, key, out)
            counter += 1


def xmldata_to_stringified(xml_data: Mapping[str, Any]) -> dict[str, str]:
    """``{"NIC": [{"Content": "a", "IP": [{"Content": "1"}]}]}`` -> ``{"NIC::1": "a", ...}``.

    Only truthy ``Content`` values are kept (Znuny drops ``""`` and ``"0"``).
    """
    guard_xml_data(xml_data)
    out: dict[str, str] = {}
    _stringify(xml_data, "", out)
    return out


def _split(value: str, separator: str) -> list[str]:
    """Perl ``split /\\s*$Separator\\s*/, $value``: trailing empty fields are removed."""
    if len(separator) > MAX_SEPARATOR_CHARS:
        raise ValueError("separator too long")
    try:
        pattern = re.compile(r"\s*" + separator + r"\s*")
    except re.error as exc:
        raise ValueError(f"separator is not a valid pattern: {exc}") from exc
    parts = pattern.split(value) if value != "" else []
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def expand_list_keys(params: Mapping[str, Any], separator: str = ",") -> dict[str, Any]:
    """Flat update parameters -> ``Key::N`` keys (the first half of ``ConfigItemAttributesGet``).

    A key without ``::`` is a list under ``Key::``; a key ending in ``::`` is split at
    ``separator`` (a regex, like Znuny) and numbered from 1. Header keys, ``ConfigItemNumber``
    and ``UserID`` are not attributes and are left out. The expanded numbers win over an explicit
    ``Key::N`` of the same name, as in Znuny (the keys are processed in sorted order).
    """
    sep = separator if perl_truthy(separator) else ","
    flat: dict[str, Any] = {}
    for name in sorted(params):
        if name in _NOT_ATTRIBUTES:
            continue
        flat[name if "::" in name else f"{name}::"] = params[name]
    for name in sorted(flat):
        if not name.endswith("::"):
            continue
        for counter, value in enumerate(_split(_scalar(flat[name]), sep), start=1):
            flat[f"{name}{counter}"] = value
        del flat[name]
    return flat


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def stringified_to_xmldata(
    stringified: Mapping[str, Any],
    definition: list[dict[str, Any]],
    original: Mapping[str, str],
    *,
    customer_lookup: Callable[[str], list[str]] | None = None,
    prefix: str = "",
) -> dict[str, Any]:
    """Flat ``Key::N`` values merged with the original ones into Znuny XMLData.

    ``definition`` is the Znuny attribute list (``Key``, ``CountMax``, ``Input``, ``Sub``).
    Keys that the definition does not name are dropped. A value of ``""`` deletes (it wins over
    the original); a missing or false value falls back to the original, then to
    ``Input.ValueDefault``. ``Customer`` inputs resolve through ``customer_lookup`` and keep the
    original value unless exactly one login matches.
    """
    result: dict[str, Any] = {}
    for item in definition:
        key_name = str(item.get("Key", ""))
        if not key_name:
            continue
        raw_input = item.get("Input")
        inp: Mapping[str, Any] = raw_input if isinstance(raw_input, Mapping) else {}
        value_default = inp.get("ValueDefault")
        try:
            count_max = min(int(item.get("CountMax") or 0), MAX_COUNT)
        except (TypeError, ValueError):
            count_max = 0
        for counter in range(1, count_max + 1):
            key = f"{key_name}::{counter}"
            if prefix:
                key = f"{prefix}::{key}"
            new = stringified.get(key)
            old = original.get(key)
            if not perl_truthy(new) and not perl_truthy(old):
                continue
            value: Any = new if perl_truthy(new) else (old if perl_truthy(old) else value_default)
            if new is not None and _scalar(new) == "":
                value = ""  # an explicit "" deletes, it wins over the original
            if isinstance(value, list):
                # list values (GI callers): the n-th entry belongs to counter n
                if counter > len(value) or not perl_truthy(value[counter - 1]):
                    continue
                value = value[counter - 1]
            elif (
                isinstance(value, str | int)
                and _scalar(value) != ""
                and inp.get("Type") == "Customer"
            ):
                found = customer_lookup(_scalar(value)) if customer_lookup else []
                fallback = old if perl_truthy(old) else value_default
                value = found[0] if len(found) == 1 else fallback
            entry: dict[str, Any] = {}
            if perl_truthy(value) and item.get("Sub"):
                entry = stringified_to_xmldata(
                    stringified, item["Sub"], original, customer_lookup=customer_lookup, prefix=key
                )
            entry["Content"] = _as_text(value)
            result.setdefault(key_name, []).append(entry)
    return result
