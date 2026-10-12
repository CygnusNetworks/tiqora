"""Event conditions of a Generic Interface invoker (G4).

Line-by-line port of ``Kernel/GenericInterface/Event/Handler.pm`` of Znuny 6.5:
``_SerializeConfig`` (lines 318-394) and ``_ConditionCheck`` (lines 451-1056), including the
early-return structure and the quirks the Znuny tests pin down.  Pure functions, no I/O.

Deliberate differences (each pinned by a test):

* ``Module`` conditions never match (Tiqora cannot load Perl validation modules) and ``jq#``
  field names never match (Znuny without ``Jq`` behaves the same); both add a warning.
* A dict/list inside an array is skipped by :func:`serialize_config` (Perl would emit the
  unmatchable key ``HASH(0x...)_...``); the array counter still advances.
* Malformed configuration returns ``False`` plus a :class:`ConditionWarning` where Znuny
  logs an error and returns ``undef``/dies - the invoker simply does not fire.
* Regex matching runs under ``REGEX_TIMEOUT_SECONDS``; a timeout is a non-match plus a warning.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tiqora.api.compat.mapping.perl import perl_str
from tiqora.util.perl import perl_truthy
from tiqora.util.perl_regex import REGEX_TIMEOUT_SECONDS, PerlRegexError, compile_perl_regex

__all__ = ["ConditionWarning", "condition_check", "serialize_config"]


@dataclass(frozen=True, slots=True)
class ConditionWarning:
    invoker: str
    event: str
    message: str


def _is_container(value: Any) -> bool:
    return isinstance(value, (Mapping, list, tuple))


def _has_data(value: Any) -> bool:
    """``IsHashRefWithData`` / ``IsArrayRefWithData``."""
    return _is_container(value) and len(value) > 0


def _scalar(value: Any) -> str | None:
    """Perl string of a plain scalar; ``None`` for references (never equal to anything)."""
    return None if _is_container(value) else perl_str(value)


# --------------------------------------------------------------------------- _SerializeConfig


def serialize_config(data: Mapping[str, Any] | Sequence[Any]) -> dict[str, Any]:
    """``_SerializeConfig`` (Handler.pm 318-394): flatten nested data to ``A_B_0``-style keys."""
    out: dict[str, Any] = {}
    _serialize(data, out, "")
    return out


def _serialize(data: Any, out: dict[str, Any], prefix: str) -> None:
    if isinstance(data, Mapping):
        for key in sorted(data, key=str):  # Perl: sort keys
            if not perl_truthy(key):  # next CONFIGITEM if !$ConfigItem (skips "" and "0")
                continue
            value = data[key]
            if _has_data(value):
                _serialize(value, out, f"{prefix}{key}_")
            else:
                out[f"{prefix}{key}"] = value  # scalar or EMPTY container
        return
    count = 0
    for item in data:
        if not perl_truthy(item):
            continue  # skipped before the counter increments
        if _has_data(item):
            pass  # Perl would recurse with prefix "HASH(0x...)_": unmatchable, so skipped
        else:
            out[f"{prefix}{count}"] = item
        count += 1


# --------------------------------------------------------------------------- _ConditionCheck

_LINKINGS = ("and", "or", "xor")
_FIELD_TYPES = ("String", "Hash", "Array", "Regexp", "Module")
_JQ_FIELD = re.compile(r"\Ajq#(.+)", re.IGNORECASE | re.DOTALL)

_MATCH = "match"
_FAIL = "fail"
_FAIL_SOFT = "fail_soft"  # Hash type with non-dict data: counted, but never an early return
_NONE = "none"  # no branch ran (Type missing in a hash spec): neither success nor fail
_ERROR = "error"


def condition_check(
    condition: Mapping[str, Any],
    data: Mapping[str, Any],
    *,
    invoker: str = "",
    event: str = "",
    warnings: list[ConditionWarning] | None = None,
) -> bool:
    """``_ConditionCheck`` (Handler.pm 451-1056).

    ``condition`` is ``InvokerEvent.Condition``: ``{"ConditionLinking": ..., "Condition": {...}}``
    and ``data`` the serialized event data.  Problems are appended to ``warnings``.
    """

    def warn(message: str) -> None:
        if warnings is not None:
            warnings.append(ConditionWarning(invoker, event, message))

    if not isinstance(condition, Mapping) or condition.get("Condition") is None:
        warn("Bedingung fehlt")
        return False
    if not isinstance(data, Mapping) or not data:
        warn("Ereignisdaten sind leer")
        return False
    conditions = condition["Condition"]
    if not isinstance(conditions, Mapping) or not conditions:
        warn("Bedingung enthält keine Werte")
        return False

    raw_linking = condition.get("ConditionLinking")
    linking = perl_str(raw_linking) if perl_truthy(raw_linking) else "and"
    if linking not in _LINKINGS:
        warn("Ungültiges ConditionLinking")
        return False

    cond_success = cond_fail = 0

    for cond_name in sorted(conditions, key=str):
        if cond_name == "ConditionLinking":
            continue
        actual = conditions[cond_name]
        fields = actual.get("Fields") if isinstance(actual, Mapping) else None
        if not isinstance(fields, Mapping) or not fields:
            warn(f"Condition->{cond_name}: keine Fields vorhanden")
            return False

        raw_type = actual.get("Type")
        cond_type = perl_str(raw_type) if perl_truthy(raw_type) else "and"
        if cond_type not in _LINKINGS:
            warn(f"Condition->{cond_name}->Type ist ungültig")
            return False

        field_success = field_fail = 0
        skip_tail = False  # Perl `next CONDITIONNAME`

        for field_name in sorted(fields, key=str):
            spec = fields[field_name]
            if isinstance(spec, (list, tuple)):
                spec = {"Type": "Array", "Match": list(spec)}
            elif not isinstance(spec, Mapping):
                spec = {"Type": "String", "Match": spec}  # plain scalar -> String check

            field_type = spec.get("Type")
            if (perl_str(field_type) if perl_truthy(field_type) else "String") not in _FIELD_TYPES:
                warn(f"Condition->{cond_name}->Fields->{field_name}: ungültiger Typ")
                return False

            if _JQ_FIELD.match(str(field_name)):
                warn(
                    f"Feld {field_name}: jq-Ausdrücke werden in Tiqora nicht ausgewertet"
                    " (Bedingung trifft nie zu)"
                )

            # Znuny dispatches on the raw ->{Type}; an unset Type matches no branch.
            outcome = _check_field(
                str(field_name),
                spec,
                spec.get("Type"),
                data,
                f"Condition->{cond_name}->Fields->{field_name}",
                warn,
            )
            if outcome == _ERROR:
                return False
            if outcome == _NONE:
                continue
            if outcome == _MATCH:
                field_success += 1
                if linking == "or" and cond_type == "or":
                    return True
                if linking != "or" and cond_type == "or":
                    skip_tail = True
                    break
            else:
                field_fail += 1
                if outcome == _FAIL:
                    if linking == "and" and cond_type == "and":
                        return False
                    if cond_type == "and":
                        skip_tail = True
                        break

        if skip_tail:
            continue

        if cond_type == "and":
            if not field_fail:
                if linking == "or":
                    return True
                cond_success += 1
            else:
                cond_fail += 1
                if linking == "and":
                    return False
        elif cond_type == "or":
            if field_success > 0:
                if linking == "or":
                    return True
                cond_success += 1
            else:
                cond_fail += 1
                if linking == "and":
                    return False
        elif cond_type == "xor":
            if field_success == 1:
                if linking == "or":
                    return True
                cond_success += 1
            else:
                cond_fail += 1

    if linking == "and":
        return not cond_fail
    if linking == "or":
        return cond_success > 0
    return cond_success == 1  # xor


def _check_field(
    name: str,
    spec: Mapping[str, Any],
    raw_type: Any,
    data: Mapping[str, Any],
    label: str,
    warn: Any,
) -> str:
    value = data.get(name)
    has_match = "Match" in spec
    match = spec.get("Match")

    if raw_type == "String":
        if (not perl_truthy(match) and perl_str(match) != "0") or _is_container(match):
            warn(f"{label}: Match muss ein String sein, wenn Type String ist")
            return _ERROR
        wanted = perl_str(match)
        if _JQ_FIELD.match(name) or (
            value is not None and (perl_truthy(value) or perl_str(value) == "0")
        ):
            found = False
            if not _is_container(value):
                found = wanted == perl_str(value)
            elif isinstance(value, (list, tuple)):
                found = any(wanted == _scalar(item) for item in value)
            return _MATCH if found else _FAIL
        for key in _array_fields(name, data):
            item = _scalar(data[key])
            if item is not None and item == wanted:
                return _MATCH
        return _FAIL

    if raw_type == "Array":
        if not isinstance(match, (list, tuple)):
            warn(f"{label}: Match muss eine Liste sein, wenn Type Array ist")
            return _ERROR
        present = [_scalar(item) for item in value] if isinstance(value, (list, tuple)) else []
        hits = sum(1 for wanted in match for item in present if _scalar(wanted) == item)
        return _MATCH if hits == len(match) else _FAIL

    if raw_type == "Hash":
        if not isinstance(match, Mapping):
            warn(f"{label}: Match muss ein Hash sein")
            return _ERROR
        if not perl_truthy(value) or not isinstance(value, Mapping):
            return _FAIL_SOFT  # Znuny: `next FIELDLNAME` without the early return
        hits = sum(1 for key, wanted in match.items() if _scalar(value.get(key)) == _scalar(wanted))
        return _MATCH if hits == len(match) else _FAIL

    if raw_type == "Regexp":
        if not has_match or not perl_truthy(match) or _is_container(match):
            warn(f"{label}: Match muss ein regulärer Ausdruck sein, wenn Type Regexp ist")
            return _ERROR
        try:
            pattern = compile_perl_regex(perl_str(match), verbose=False)  # qr{$Match}: no /x
        except PerlRegexError as exc:
            warn(f"{label}: {exc}")
            return _ERROR

        def hit(text: Any) -> bool:
            plain = _scalar(text)
            if plain is None:
                return False
            try:
                return pattern.search(plain, timeout=REGEX_TIMEOUT_SECONDS) is not None
            except TimeoutError:
                warn(f"{label}: regulärer Ausdruck überschritt {REGEX_TIMEOUT_SECONDS}s")
                return False

        if perl_truthy(value):
            if not _is_container(value):
                found = hit(value)
            elif isinstance(value, (list, tuple)):
                found = any(hit(item) for item in value)
            else:
                found = False
            return _MATCH if found else _FAIL
        for key in _array_fields(name, data):
            if hit(data[key]):
                return _MATCH
        return _FAIL

    if raw_type == "Module":
        warn("Bedingung vom Typ Module wird in Tiqora nicht ausgewertet")
        return _FAIL

    return _NONE


def _array_fields(name: str, data: Mapping[str, Any]) -> list[str]:
    """Serialized array keys ``<name>_<n>`` (Perl ``m{ \\A \\Q$FieldName\\E _ \\d+ \\z }xms``)."""
    pattern = re.compile(re.escape(name) + r"_\d+")
    return [key for key in data if isinstance(key, str) and pattern.fullmatch(key)]
