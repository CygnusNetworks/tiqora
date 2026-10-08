"""``Mapping::OutOfOffice`` - CSV bulk entries (port of ``Kernel/GenericInterface/Mapping/OutOfOffice.pm``)."""

from __future__ import annotations

from typing import Any

from tiqora.api.compat.mapping import DataInclude, MappingError
from tiqora.api.compat.mapping.perl import (
    REGEX_TIMEOUT_SECONDS,
    compile_perl_regex,
    perl_is_string,
    perl_str,
    perl_truthy,
)

_CSV_PARAM = "OutOfOfficeEntriesCSVString"

# Perl: m{(\d{2}).(\d{2}).(\d{4}) \s \d{2}:\d{2}:\d{2}}x - unanchored, `.` is any char but "\n"
# (the shared helper compiles with DOTALL, so spell the dot as a negated class).
_DATE_RE = r"(\d{2})[^\n](\d{2})[^\n](\d{4}) \s \d{2}:\d{2}:\d{2}"
_YES_NO = {"yes": 1, "no": 0}
_ENTRY_KEYS = ("UserLogin", "UserSearch", "UserEmail", "StartDate", "EndDate", "OutOfOffice")


def _csv_rows(text: str) -> list[list[str]]:
    """Text::CSV (binary, sep ",", quote/escape ``"``, no loose quotes/escapes) over ``text``.

    A bare quote inside an unquoted field, junk after a closing quote and an unterminated quote
    are errors (Text::CSV returns false, CSV2Array undef). Empty lines yield an empty row.
    """
    rows: list[list[str]] = []
    row: list[str] = []
    field: list[str] = []
    i, n = 0, len(text)
    in_quotes = False
    # True once the current field was opened with a quote and closed again.
    closed = False
    started = False  # any char of the current record seen

    def fail() -> MappingError:
        return MappingError(f"Error while parsing CSV string of parameter {_CSV_PARAM}.")

    while i < n:
        ch = text[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    field.append('"')
                    i += 2
                    continue
                in_quotes, closed = False, True
            else:
                field.append(ch)
            i += 1
            continue
        if ch == '"':
            if field or closed:
                raise fail()
            in_quotes = True
            started = True
        elif ch == ",":
            row.append("".join(field))
            field, closed, started = [], False, True
        elif ch == "\n":
            if started:
                row.append("".join(field))
            rows.append(row)
            row, field, closed, started = [], [], False, False
        elif closed:
            raise fail()
        else:
            field.append(ch)
            started = True
        i += 1
    if in_quotes:
        raise fail()
    if started:
        row.append("".join(field))
        rows.append(row)
    return rows


class OutOfOfficeMapper:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config

    def map(self, data: Any, data_include: DataInclude | None = None) -> Any:
        raw = data.get(_CSV_PARAM) if isinstance(data, dict) else None
        if not perl_is_string(raw) or perl_str(raw) == "":
            raise MappingError(f"Parameter {_CSV_PARAM} is missing.")
        text = perl_str(raw).replace("\r\n", "\n").replace("\r", "\n")
        text = text.rstrip("\n") + "\n"
        # Remove comment lines (Perl: s{^#.*\n}{}gm)
        text = "".join(line for line in text.splitlines(keepends=True) if not line.startswith("#"))

        rows = _csv_rows(text)
        if not rows:
            raise MappingError(f"Error while parsing CSV string of parameter {_CSV_PARAM}.")
        header = rows[0]
        csv_entries: list[dict[str, str | None]] = []
        for row in rows[1:]:
            if not row:
                continue
            csv_entries.append(
                {col: (row[i] if i < len(row) else None) for i, col in enumerate(header)}
            )
        if not csv_entries:
            raise MappingError(f"CSV in parameter {_CSV_PARAM} does not contain any entries.")

        date_re = compile_perl_regex(_DATE_RE, verbose=True)
        entries: list[dict[str, Any]] = []
        for csv_entry in csv_entries:
            entry: dict[str, Any] = dict(csv_entry)
            for key, fallback in (
                ("UserEmail", "EMail"),
                ("EndDate", "EndTime"),
                ("StartDate", "StartTime"),
                ("OutOfOffice", "Enabled"),
            ):
                if entry.get(key) is None:
                    entry[key] = entry.get(fallback)
            for part in ("EndDate", "StartDate"):
                value = entry.get(part)
                if not perl_truthy(value):
                    continue
                try:
                    match = date_re.search(value, timeout=REGEX_TIMEOUT_SECONDS)
                except TimeoutError as exc:
                    raise MappingError("regex timed out in OutOfOffice date parsing") from exc
                if match is not None:
                    entry[part] = f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
            flag = entry.get("OutOfOffice")
            if flag in _YES_NO:
                entry["OutOfOffice"] = _YES_NO[flag]
            entries.append({key: entry.get(key) for key in _ENTRY_KEYS})

        # Preserve the original data: it also carries the login credentials.
        return {**data, "OutOfOfficeEntries": entries}
