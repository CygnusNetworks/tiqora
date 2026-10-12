"""Import of a Znuny ITSM class definition (D2): Perl data (6.x) or YAML (7.x) -> ``AttributeDef``.

Pure module. The Perl text goes through the step-0 safe parser :mod:`tiqora.znuny.perl_data`
(never evaluated), YAML through ``yaml.safe_load`` with the same 1 MiB / depth-64 bounds. Znuny
facts: ``Kernel/System/ITSMConfigItem/Definition.pm`` (attribute hashes ``Key``, ``Name``,
``Searchable``, ``Input``, ``CountMin/Max/Default``, ``Sub``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import yaml
from pydantic import ValidationError

from tiqora.cmdb.definition import KEY_RE, AttributeDef, AttributeOptions
from tiqora.util.perl import perl_truthy
from tiqora.znuny.perl_data import MAX_TEXT_CHARS, PerlDataError, parse_perl_data

MAX_DEPTH: Final = 64
_KEY = re.compile(KEY_RE)

#: Znuny ``Input.Type`` -> Tiqora attribute type (D2)
_TYPE_MAP: Final[dict[str, str]] = {
    "Text": "text",
    "TextArea": "textarea",
    "Integer": "integer",
    "Date": "date",
    "DateTime": "datetime",
    "GeneralCatalog": "catalog",
    "CIClassReference": "ci_ref",
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


def _depth(value: Any, limit: int, level: int = 0) -> bool:
    """True when the nested structure is deeper than ``limit`` (iterative-safe bound)."""
    if level > limit:
        return True
    if isinstance(value, dict):
        return any(_depth(v, limit, level + 1) for v in value.values())
    if isinstance(value, list):
        return any(_depth(v, limit, level + 1) for v in value)
    return False


def _load(text: str, fmt: Literal["yaml", "perl"]) -> Any:
    if len(text) > MAX_TEXT_CHARS:
        raise DefinitionParseError(f"Die Definition ist größer als {MAX_TEXT_CHARS} Zeichen.")
    if fmt == "perl":
        try:
            return parse_perl_data(text, max_depth=MAX_DEPTH)
        except PerlDataError as exc:
            raise DefinitionParseError(f"Perl-Definition nicht lesbar: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DefinitionParseError(f"YAML-Definition nicht lesbar: {exc}") from exc
    # safe_load builds the tree first, so check the depth before we recurse over it
    if _depth(data, MAX_DEPTH):
        raise DefinitionParseError(f"Die Definition ist tiefer als {MAX_DEPTH} Ebenen.")
    return data


def _top_level_items(data: Any) -> list[Any]:
    """A plain attribute list, or a 7.x document: attributes of every section's ``Content``."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        sections = data.get("Sections")
        if isinstance(sections, dict):
            out: list[Any] = []
            for section in sections.values():
                content = section.get("Content") if isinstance(section, dict) else None
                if isinstance(content, list):
                    out.extend(content)
            return out
        if isinstance(sections, list):
            out = []
            for section in sections:
                content = section.get("Content") if isinstance(section, dict) else None
                if isinstance(content, list):
                    out.extend(content)
            return out
    raise DefinitionParseError("Die Definition enthält keine Attributliste.")


def _int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _sanitise_key(raw: str, path: str, res: DefinitionImportResult) -> str:
    if _KEY.match(raw):
        return raw
    key = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    if not key or not key[0].isalpha():
        key = f"A_{key}"
    key = key[:64]
    res.warnings.append(f"Schlüssel {raw} bei {path} wurde zu {key} geändert")
    return key


def _convert(item: Any, prefix: str, res: DefinitionImportResult) -> AttributeDef | None:
    if not isinstance(item, dict):
        res.warnings.append(
            f"Eintrag bei {prefix or '(Wurzel)'} ist kein Attribut und wurde übersprungen"
        )
        return None
    raw_key = str(item.get("Key", "")).strip()
    if not raw_key:
        res.warnings.append(f"Attribut ohne Key bei {prefix or '(Wurzel)'} wurde übersprungen")
        return None
    key = _sanitise_key(raw_key, prefix + raw_key, res)
    path = f"{prefix}{key}"
    inp = item.get("Input")
    inp = inp if isinstance(inp, dict) else {}
    znuny_type = str(inp.get("Type", "Text"))
    sub_raw = item.get("Sub")
    mapped = _TYPE_MAP.get(znuny_type)
    if mapped is None:
        res.warnings.append(f"Unbekannter Typ {znuny_type} bei {path}: als Text übernommen")
        mapped = "text"
    elif mapped == "group" and not isinstance(sub_raw, list):
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
        ref = inp.get("ReferencedCIClassName")
        names = ref if isinstance(ref, list) else ([ref] if ref else [])
        keys = [str(n).strip().lower() for n in names if str(n).strip()]
        options.class_keys = keys
        res.class_keys_used.update(keys)
    if mapped in ("text", "textarea", "url") and "MaxLength" in inp:
        n = _int(inp.get("MaxLength"), 0)
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

    count_min = max(0, _int(item.get("CountMin"), 0))
    count_max = max(1, _int(item.get("CountMax"), 1))
    count_default = _int(item.get("CountDefault"), 1)

    known = {"Key", "Name", "Searchable", "Input", "CountMin", "CountMax", "CountDefault", "Sub"}
    for extra in sorted(set(item) - known):
        res.warnings.append(f"Unbekannter Schlüssel {extra} bei {path} wurde ignoriert")

    sub: list[AttributeDef] = []
    if isinstance(sub_raw, list):
        for child in sub_raw:
            conv = _convert(child, f"{path}.", res)
            if conv is not None:
                sub.append(conv)

    try:
        return AttributeDef(
            key=key,
            name=str(item.get("Name") or key)[:200],
            type=mapped,  # type: ignore[arg-type]
            required=perl_truthy(inp.get("Required")),
            searchable=perl_truthy(item.get("Searchable"))
            and mapped not in ("group", "attachment"),
            count_min=count_min,
            count_max=count_max,
            count_default=min(max(count_default, count_min), count_max),
            options=options,
            sub=sub,
        )
    except ValidationError as exc:  # pragma: no cover - defensive
        res.warnings.append(
            f"Attribut {path} konnte nicht übernommen werden: {exc.errors()[0]['msg']}"
        )
        return None


def _decimal(value: Any) -> Any:
    from decimal import Decimal, InvalidOperation

    try:
        d = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(str(value)) from exc
    if not d.is_finite():
        raise ValueError(str(value))
    return d


def parse_znuny_definition(text: str, fmt: Literal["yaml", "perl"]) -> DefinitionImportResult:
    """Map a Znuny definition to Tiqora attributes; unknown parts become warnings, not errors."""
    data = _load(text, fmt)
    res = DefinitionImportResult(attributes=[])
    for item in _top_level_items(data):
        attr = _convert(item, "", res)
        if attr is not None:
            res.attributes.append(attr)
    return res


__all__ = [
    "DefinitionImportResult",
    "DefinitionParseError",
    "parse_znuny_definition",
]
