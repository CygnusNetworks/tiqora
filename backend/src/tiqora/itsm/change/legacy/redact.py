"""Erased customers in archived Znuny changes (GDPR; pure, no database).

An erased customer user leaves a tombstone (``tiqora_itsm_legacy_erased``): the keyed hash
:func:`erased_hash` of the lowercased login or address, never the identifier itself. The erasure
pseudonymises the rows already archived (:class:`MappingRedactor`: login -> the token the
tickets got, address -> :data:`REDACTED`); every later archive write redacts against all
tombstones (:class:`TombstoneRedactor`: -> :data:`REDACTED`), so the person never comes back, also
not in a change archived for the first time after the erasure.

:func:`redact_data` only touches person-bearing values of the stored JSON (allowlist):

* CAB entries of kind ``customer_user`` (the whole login);
* history values (``old``/``new``/``old_display``/``new_display``): ``CABCustomers`` lists entry
  by entry, the recipient of ``*NotificationSent`` rows (``<address>%%<event>``, ruling S5), and
  the values of free-text fields (:data:`TEXT_HISTORY_FIELDS`, ``DynamicField_*``) word by word;
* free text word by word: the change's title, description and justification, the work orders'
  title, instruction and report, dynamic field values.

Links, ids, numbers, catalog names, history types and field names and agent logins are never
touched. A word matches only as a whole (``erika`` does not match ``erika.beispiel``).
``data["redacted"]`` lists the hashes a row was redacted for, so the archive can tell a row that
must be written again (a tombstone added or rolled back) from an unchanged one.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import re
from collections.abc import Collection, Mapping
from typing import Any, Final

#: what an erased customer becomes where no pseudonym is known
REDACTED: Final = "[gelöscht]"
#: ``data`` key with the sorted tombstone hashes the row was redacted for
REDACTED_KEY: Final = "redacted"
#: ``change_history.fieldname`` values that hold free text (survey Q3)
TEXT_HISTORY_FIELDS: Final = frozenset(
    {"ChangeTitle", "Description", "Justification", "WorkOrderTitle", "Instruction", "Report"}
)
_CAB_CUSTOMERS: Final = "CABCustomers"
_NOTIFICATION_SUFFIX: Final = "NotificationSent"
_HISTORY_VALUES: Final = ("old", "new", "old_display", "new_display")
_WORKORDER_TEXTS: Final = ("title", "instruction", "report")
_CHANGE_TEXTS: Final = ("title", "description", "justification")
#: a word: everything up to white space or a separator (an address or a dotted login is one)
_WORD: Final = re.compile(r"[^\s,;:<>()\[\]{}\"'|/\\%]+")
_TRAILING: Final = ".!?"
_KEY_LABEL: Final = b"itsm-legacy-erased-v1"


def erased_hash(secret_key: str, identifier: str) -> str:
    """HMAC-SHA256 (hex) of the lowercased identifier under a key derived from the site secret."""
    key = hmac.new(secret_key.encode("utf-8"), _KEY_LABEL, hashlib.sha256).digest()
    return hmac.new(key, identifier.strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()


class Redactor:
    """Decides per identifier; remembers the tombstone hashes it matched (``hits``)."""

    def __init__(self) -> None:
        self.hits: set[str] = set()

    def lookup(self, identifier: str) -> str | None:  # pragma: no cover - interface
        raise NotImplementedError


class MappingRedactor(Redactor):
    """The erasure: each login -> its token, each address -> :data:`REDACTED`."""

    def __init__(
        self, secret_key: str, replacements: Mapping[str, str], emails: Collection[str]
    ) -> None:
        super().__init__()
        self._secret = secret_key
        self._map = {e.strip().lower(): REDACTED for e in emails if e and e.strip()}
        self._map.update({k.strip().lower(): v for k, v in replacements.items() if k.strip()})

    def lookup(self, identifier: str) -> str | None:
        found = self._map.get(identifier.strip().lower())
        if found is not None:
            self.hits.add(erased_hash(self._secret, identifier))
        return found


class TombstoneRedactor(Redactor):
    """Every archive write: an identifier with a tombstone -> :data:`REDACTED`."""

    def __init__(self, secret_key: str, hashes: Collection[str]) -> None:
        super().__init__()
        self._secret = secret_key
        self._hashes = frozenset(hashes)

    def lookup(self, identifier: str) -> str | None:
        if not identifier.strip():
            return None
        digest = erased_hash(self._secret, identifier)
        if digest not in self._hashes:
            return None
        self.hits.add(digest)
        return REDACTED


def _word(word: str, redactor: Redactor) -> str:
    found = redactor.lookup(word)
    if found is not None:
        return found
    core = word.rstrip(_TRAILING)
    if core and core != word:
        found = redactor.lookup(core)
        if found is not None:
            return found + word[len(core) :]
    return word


def _text(value: Any, redactor: Redactor) -> Any:
    if not isinstance(value, str) or not value:
        return value
    return _WORD.sub(lambda m: _word(m.group(0), redactor), value)


def _entries(value: Any, separator: str, redactor: Redactor) -> Any:
    """A list value (``%%`` or ``, `` joined): each whole entry."""
    if not isinstance(value, str) or not value:
        return value
    parts = value.split(separator)
    return separator.join(p if not p.strip() else (redactor.lookup(p) or p) for p in parts)


def _recipient(value: Any, redactor: Redactor) -> Any:
    """``<address>%%<event>``: the address."""
    if not isinstance(value, str) or not value:
        return value
    address, sep, rest = value.partition("%%")
    return (redactor.lookup(address) or address) + sep + rest


def _fields(values: Any, redactor: Redactor) -> Any:
    if not isinstance(values, dict):
        return values
    return {
        name: [_text(v, redactor) for v in items] if isinstance(items, list) else items
        for name, items in values.items()
    }


def _history(entry: dict[str, Any], redactor: Redactor) -> None:
    field = entry.get("field") or ""
    kind = str(entry.get("history_type") or "")
    for key in _HISTORY_VALUES:
        value = entry.get(key)
        if field == _CAB_CUSTOMERS:
            entry[key] = _entries(value, ", " if key.endswith("_display") else "%%", redactor)
        elif kind.endswith(_NOTIFICATION_SUFFIX):
            entry[key] = _recipient(value, redactor)
        elif field in TEXT_HISTORY_FIELDS or str(field).startswith("DynamicField_"):
            entry[key] = _text(value, redactor)


def redact_data(data: Mapping[str, Any], redactor: Redactor) -> dict[str, Any]:
    """A copy of the stored JSON with the person-bearing values redacted (see the module)."""
    out = copy.deepcopy(dict(data))
    for key in _CHANGE_TEXTS:
        out[key] = _text(out.get(key), redactor)
    out["cab"] = [
        [kind, redactor.lookup(login) or login]
        if kind == "customer_user" and isinstance(login, str)
        else [kind, login]
        for kind, login in out.get("cab") or ()
    ]
    for workorder in out.get("workorders") or ():
        for key in _WORKORDER_TEXTS:
            workorder[key] = _text(workorder.get(key), redactor)
        workorder["dynamic_fields"] = _fields(workorder.get("dynamic_fields"), redactor)
    out["dynamic_fields"] = _fields(out.get("dynamic_fields"), redactor)
    for entry in out.get("history") or ():
        if isinstance(entry, dict):
            _history(entry, redactor)
    return out


def redacted_hashes(data: Mapping[str, Any]) -> set[str]:
    return {str(h) for h in data.get(REDACTED_KEY) or ()}
