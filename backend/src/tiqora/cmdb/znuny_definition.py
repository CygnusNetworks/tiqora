"""Import of a Znuny ITSM class definition (D2): Znuny's definition text -> ``AttributeDef``.

One parser for Mode A, the CMDB and the importer (R8): the text is read and prepared by the ITSM
core's :func:`tiqora.itsm.znuny_adapter.definitions.parse_definition` (YAML in Znuny 6.5 and
7.3, the old Perl data format through the step-0 safe parser, never evaluated; 1 MiB, depth 64,
node limit, no YAML aliases; Znuny's ``_DefinitionPrepare`` count rules and ``DefinitionCheck``
key rules). This module only maps the prepared :class:`AttrDef` tree onto Tiqora attributes.
The behaviour survey found no separate 7.x layout: both lines store the same attribute list
(``docs/itsm-import.md``, question 7).

Differences to reading a stored definition row: the text comes from an admin (pasted or copied
out of the database), so YAML without the leading ``---`` is read as YAML rather than as Znuny's
"legacy Perl" marker, and errors carry a German message for the dialog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from pydantic import ValidationError

from tiqora.cmdb.definition import KEY_RE, AttributeDef, AttributeOptions
from tiqora.itsm.znuny_adapter.definitions import AttrDef, DefinitionError, parse_definition
from tiqora.util.perl import perl_truthy
from tiqora.znuny.perl_data import PerlDataError

_KEY = re.compile(KEY_RE)

#: Znuny ``Input.Type`` -> Tiqora attribute type (D2; the 20 modules under ``XML/Type``)
_TYPE_MAP: Final[dict[str, str]] = {
    "Text": "text",
    "TextArea": "textarea",
    "Integer": "integer",
    "Date": "date",
    "DateTime": "datetime",
    "GeneralCatalog": "catalog",
    "CI": "ci_ref",
    "CISimple": "ci_ref",
    "CIClassReference": "ci_ref",  # not a Znuny 6.5/7.3 module; kept for older exports
    "Customer": "customer_user",
    "CustomerCompany": "customer_company",
    "User": "agent",
    "Service": "service",
    "SLA": "sla",
    "Queue": "queue",
    "Priority": "priority",
    "Type": "ticket_type",
    "State": "state",
    "TextLink": "url",
    "Dummy": "group",
    "CIAttachment": "attachment",
    "Attachment": "attachment",
}


class DefinitionParseError(ValueError):
    """The definition text cannot be read (syntax, size, shape)."""


@dataclass
class DefinitionImportResult:
    attributes: list[AttributeDef]
    warnings: list[str] = field(default_factory=list)
    catalog_classes_used: set[str] = field(default_factory=set)
    class_keys_used: set[str] = field(default_factory=set)


def _sanitise_key(raw: str, path: str, res: DefinitionImportResult) -> str:
    if _KEY.match(raw):
        return raw
    key = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    if not key or not key[0].isalpha():
        key = f"A_{key}"
    key = key[:64]
    res.warnings.append(f"Schlüssel {raw} bei {path} wurde zu {key} geändert")
    return key


def _decimal(value: Any) -> Decimal:
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(str(value)) from exc
    if not d.is_finite():
        raise ValueError(str(value))
    return d


def _referenced_classes(inp: dict[str, Any]) -> list[str]:
    """``CI``/``CISimple``: ``CIClassName``; older exports: ``ReferencedCIClassName`` (a list)."""
    ref = inp.get("CIClassName") or inp.get("ReferencedCIClassName")
    names = ref if isinstance(ref, list) else ([ref] if ref else [])
    return [str(n).strip().lower() for n in names if str(n).strip()]


def _convert(attr: AttrDef, prefix: str, res: DefinitionImportResult) -> AttributeDef | None:
    key = _sanitise_key(attr.key, prefix + attr.key, res)
    path = f"{prefix}{key}"
    inp = dict(attr.input)
    znuny_type = attr.input_type or "Text"
    mapped = _TYPE_MAP.get(znuny_type)
    if mapped is None:
        res.warnings.append(f"Unbekannter Typ {znuny_type} bei {path}: als Text übernommen")
        mapped = "text"
    elif mapped == "group" and not attr.sub:
        # a group without children would not validate; keep the value as text
        res.warnings.append(f"Gruppe ohne Unterattribute bei {path}: als Text übernommen")
        mapped = "text"

    options = AttributeOptions()
    if mapped == "catalog":
        cls = str(inp.get("Class", "")).strip()
        if cls:
            options.catalog_class = cls
            res.catalog_classes_used.add(cls)
        else:
            res.warnings.append(f"Katalogklasse fehlt bei {path}")
    if mapped == "ci_ref":
        keys = _referenced_classes(inp)
        options.class_keys = keys
        res.class_keys_used.update(keys)
    if mapped in ("text", "textarea", "url") and "MaxLength" in inp:
        try:
            n = int(str(inp.get("MaxLength")).strip())
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            options.max_length = n
    if mapped in ("integer", "decimal"):
        for znuny_key, field_name in (("ValueMin", "min"), ("ValueMax", "max")):
            if inp.get(znuny_key) not in (None, ""):
                try:
                    setattr(options, field_name, _decimal(inp[znuny_key]))
                except ValueError:
                    res.warnings.append(
                        f"{znuny_key} bei {path} ist keine Zahl und wurde ignoriert"
                    )

    sub: list[AttributeDef] = []
    for child in attr.sub:
        conv = _convert(child, f"{path}.", res)
        if conv is not None:
            sub.append(conv)

    required = perl_truthy(inp.get("Required"))
    try:
        return AttributeDef(
            key=key,
            name=attr.name[:200],
            type=mapped,  # type: ignore[arg-type]
            required=required,
            searchable=attr.searchable and mapped not in ("group", "attachment"),
            # Znuny's _DefinitionPrepare already clamped the counts (CountMin defaults to 1).
            # In Znuny CountMin is the number of input rows shown and Input.Required decides
            # whether a value is mandatory; Tiqora's count_min is "values required", so it
            # takes CountMin only for required inputs (ruling CMDB-PM-I1). count_default keeps
            # the rows Znuny shows.
            count_min=attr.count_min if required else 0,
            count_max=attr.count_max,
            count_default=attr.count_default,
            options=options,
            sub=sub,
        )
    except ValidationError as exc:
        res.warnings.append(
            f"Attribut {path} konnte nicht übernommen werden: {exc.errors()[0]['msg']}"
        )
        return None


def parse_znuny_definition(text: str, fmt: Literal["yaml", "perl"]) -> DefinitionImportResult:
    """Map a Znuny definition to Tiqora attributes; unknown types become warnings, not errors."""
    source = text
    if fmt == "yaml":
        stripped = text.lstrip()
        # pasted YAML without the document marker is still YAML (not Znuny's legacy-Perl marker)
        source = stripped if stripped.startswith("---") else "---\n" + text
    try:
        prepared = parse_definition(source, fmt)
    except DefinitionError as exc:
        label = "YAML" if fmt == "yaml" else "Perl"
        # chain the Perl parser's error when there is one: the API reports its ``position``
        cause = exc.__cause__ if isinstance(exc.__cause__, PerlDataError) else exc
        raise DefinitionParseError(f"{label}-Definition nicht lesbar: {exc}") from cause
    res = DefinitionImportResult(attributes=[])
    for item in prepared:
        attr = _convert(item, "", res)
        if attr is not None:
            res.attributes.append(attr)
    return res


__all__ = [
    "DefinitionImportResult",
    "DefinitionParseError",
    "parse_znuny_definition",
]
