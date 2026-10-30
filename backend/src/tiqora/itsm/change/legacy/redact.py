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
touched. In free text an identifier matches as a whole run of identifier characters (letters,
digits, ``_ . @ + -``; leading/trailing dots and dashes are tried off): ``erika`` does not match
``erika.beispiel``, but ``&lt;erika@example.org&gt;`` and ``chg94.kunde&nbsp;`` (raw RichText
history values) do. A value with HTML entities is also matched after decoding them (an
identifier written as ``erika&#64;example.org``); such a value is then stored decoded.
Identifiers with other characters are matched only in the exact-entry fields (CAB, lists,
recipients).
``data["redacted"]`` lists the hashes a row was redacted for, so the archive can tell a row that
must be written again (a tombstone added or rolled back) from an unchanged one.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import html
import re
from collections.abc import Collection, Mapping
from typing import Any, Final

from tiqora.config import Settings

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
#: a run of identifier characters (an address or a dotted login is one run; ``&``, ``;``,
#: ``<``, white space ... end it)
_WORD: Final = re.compile(r"[\w.@+-]+")
_EDGES: Final = ".-"
_KEY_LABEL: Final = b"itsm-legacy-erased-v1"
#: upper case: :func:`erased_hash` lowercases every identifier, so no identifier hashes to it
_CHECK_LABEL: Final = b"ITSM-LEGACY-ERASED-KEY-CHECK-V1"


def erased_key(settings: Settings) -> bytes:
    """The tombstone HMAC key, derived from ``TIQORA_ITSM_LEGACY_ERASED_KEY``, else from the
    site secret (``TIQORA_SECRET_KEY``).

    The one source of the key: the erasure, the archive job and the importer step all pass
    their settings here, so their hashes agree. Derive it once per run, not per word.
    """
    secret = settings.itsm_legacy_erased_key or settings.secret_key
    return hmac.new(secret.encode("utf-8"), _KEY_LABEL, hashlib.sha256).digest()


def key_check(key: bytes) -> str:
    """The check value of a tombstone key (HMAC-SHA256 hex of a fixed label), stored once next
    to the tombstones: a run under another key finds a different value and refuses."""
    return hmac.new(key, _CHECK_LABEL, hashlib.sha256).hexdigest()


def erased_hash(key: bytes, identifier: str) -> str:
    """HMAC-SHA256 (hex) of the stripped, lowercased identifier under :func:`erased_key`."""
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
        self, key: bytes, replacements: Mapping[str, str], emails: Collection[str]
    ) -> None:
        super().__init__()
        self._key = key
        self._map = {e.strip().lower(): REDACTED for e in emails if e and e.strip()}
        self._map.update({k.strip().lower(): v for k, v in replacements.items() if k.strip()})

    def lookup(self, identifier: str) -> str | None:
        found = self._map.get(identifier.strip().lower())
        if found is not None:
            self.hits.add(erased_hash(self._key, identifier))
        return found


class TombstoneRedactor(Redactor):
    """Every archive write: an identifier with a tombstone -> :data:`REDACTED`."""

    def __init__(self, key: bytes, hashes: Collection[str]) -> None:
        super().__init__()
        self._key = key
        self._hashes = frozenset(hashes)

    def lookup(self, identifier: str) -> str | None:
        if not identifier.strip():
            return None
        digest = erased_hash(self._key, identifier)
        if digest not in self._hashes:
            return None
        self.hits.add(digest)
        return REDACTED


def _word(word: str, redactor: Redactor) -> str:
    found = redactor.lookup(word)
    if found is not None:
        return found
    core = word.strip(_EDGES)
    if core and core != word:
        found = redactor.lookup(core)
        if found is not None:
            start = word.index(core)
            return word[:start] + found + word[start + len(core) :]
    return word


def _words(value: str, redactor: Redactor) -> str:
    return _WORD.sub(lambda m: _word(m.group(0), redactor), value)


def _text(value: Any, redactor: Redactor) -> Any:
    if not isinstance(value, str) or not value:
        return value
    out = _words(value, redactor)
    if "&" in out:
        decoded = html.unescape(out)
        if decoded != out:
            redacted = _words(decoded, redactor)
            if redacted != decoded:  # only visible with the entities decoded
                return redacted
    return out


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
