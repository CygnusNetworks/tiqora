"""Znuny's XMLHash: content-key tokenizer, hash rebuild and export-file parser (pure).

Shared by the statistics reader and the ITSM adapter (CI attributes live in ``xml_storage`` too);
``tiqora.reporting.znuny_stats.xml_hash`` re-exports every public name.

Storage format (``Kernel/System/XML.pm``)
-----------------------------------------
``XMLHashGet`` reads ``xml_content_key`` / ``xml_content_value`` per leaf and evaluates
``$XMLHash<content_key> = '<value>';`` as Perl. Only the **value** is escaped (``\\`` -> ``\\\\``,
``'`` -> ``\\'``) and the database holds it unescaped; NULL is read as ``''``. The **key** is
written verbatim by ``_XMLHash2D`` (``{'$tag'}``, tags are XML names and never contain quotes);
the tokenizer still accepts ``\\'`` and ``\\\\`` inside a key. Nothing is ever evaluated here.

The shape is ``@XMLHash``. A parsed export file has ``undef`` at index 0 and the root hash
(``{'otrs_stats'}``) at ``[1]``; a statistic stored by Znuny (``StatsAdd``/``Import``) has the root
at ``[0]`` (``[0]{'otrs_stats'}[1]{...}``, ``StatsGet`` reads ``$XMLHash[0]``), and
``build_xml_hash`` keeps whatever index the keys name. Every tag maps to a list (index 0 ``undef``)
of hashes with the key ``Content`` plus the XML attributes of that occurrence plus child tags.
``Kernel/System/Stats.pm`` ``StatsGet`` reads the string keys ``Title Object File Description
SumRow SumCol StatNumber Cache StatType Valid ObjectModule`` and ``TimeZone`` from
``[1]{key}[1]{Content}``, the list keys ``Permission`` / ``Format`` from every ``[n]{Content}``,
and the element lists ``UseAsXvalue``, ``UseAsValueSeries`` and ``UseAsRestriction`` (attributes
``Element``, ``Fixed`` and the time keys, ``SelectedValues[n]{Content}``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

MAX_STAT_ROWS: Final = 20_000  # leaves per stat; more -> XmlHashError
MAX_DEPTH: Final = 12
#: Real Znuny exports are a few KB; the cap must hold behind the proxy (base64 ~1.37 MB).
MAX_FILE_BYTES: Final = 1_000_000
_MAX_INDEX: Final = MAX_STAT_ROWS

Path_ = list[int | str]

_BAD_FILE: Final = "Die Datei ist keine gültige Znuny-Statistik (XML)."
_BAD_STAT: Final = "Die gespeicherte Statistik ist beschädigt."


class XmlHashError(ValueError):
    """Unreadable Znuny statistic; the message is German and fit for the import report."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse_content_key(key: str) -> Path_:
    """``"[1]{'a'}[2]{'b'}"`` -> ``[1, "a", 2, "b"]``. Hand-written scanner, linear time."""
    n = len(key)
    if n == 0 or n > 2_000:
        raise XmlHashError(_BAD_STAT)
    out: Path_ = []
    i = 0
    while i < n:
        ch = key[i]
        if ch == "[":
            j = i + 1
            while j < n and "0" <= key[j] <= "9":
                j += 1
            if j == i + 1 or j >= n or key[j] != "]" or j - i - 1 > 6:
                raise XmlHashError(_BAD_STAT)
            index = int(key[i + 1 : j])
            if index > _MAX_INDEX:
                raise XmlHashError(_BAD_STAT)
            out.append(index)
            i = j + 1
        elif ch == "{" and key.startswith("{'", i):
            j = i + 2
            chars: list[str] = []
            while True:
                if j >= n:
                    raise XmlHashError(_BAD_STAT)
                c = key[j]
                if c == "\\":
                    if j + 1 >= n or key[j + 1] not in ("'", "\\"):
                        raise XmlHashError(_BAD_STAT)
                    chars.append(key[j + 1])
                    j += 2
                elif c == "'":
                    break
                else:
                    chars.append(c)
                    j += 1
            if j + 1 >= n or key[j + 1] != "}":
                raise XmlHashError(_BAD_STAT)
            out.append("".join(chars))
            i = j + 2
        else:
            raise XmlHashError(_BAD_STAT)
    return out


def build_xml_hash(rows: Iterable[tuple[str, str | None]]) -> list[Any]:
    """Rebuild Znuny's ``@XMLHash`` from ``(xml_content_key, xml_content_value)`` rows."""
    root: list[Any] = [None]
    for count, (content_key, value) in enumerate(rows, start=1):
        if count > MAX_STAT_ROWS:
            raise XmlHashError("Die Statistik ist zu groß, um sie zu lesen.")
        path = parse_content_key(content_key)
        if len(path) < 2 or len(path) % 2 or len(path) // 2 > MAX_DEPTH:
            raise XmlHashError(_BAD_STAT)
        pairs = [(path[i], path[i + 1]) for i in range(0, len(path), 2)]
        if any(not isinstance(a, int) or not isinstance(b, str) for a, b in pairs):
            raise XmlHashError(_BAD_STAT)
        cur: list[Any] = root
        for depth, (idx, tag) in enumerate(pairs):
            assert isinstance(idx, int) and isinstance(tag, str)
            while len(cur) <= idx:
                cur.append(None)
            node = cur[idx]
            if node is None:
                node = {}
                cur[idx] = node
            elif not isinstance(node, dict):
                raise XmlHashError(_BAD_STAT)
            if depth == len(pairs) - 1:
                if tag in node:
                    raise XmlHashError(_BAD_STAT)
                node[tag] = "" if value is None else value
            else:
                child = node.get(tag)
                if child is None:
                    child = [None]
                    node[tag] = child
                elif not isinstance(child, list):
                    raise XmlHashError(_BAD_STAT)
                cur = child
    return root


def _element_to_node(elem: Element, depth: int, budget: list[int]) -> dict[str, Any]:
    if depth > MAX_DEPTH:
        raise XmlHashError(_BAD_FILE)
    budget[0] -= 1
    if budget[0] < 0:
        raise XmlHashError("Die Statistik ist zu groß, um sie zu lesen.")
    node: dict[str, Any] = {k: v for k, v in elem.attrib.items()}
    node["Content"] = (elem.text or "").strip()
    for child in elem:
        if not isinstance(child.tag, str):  # comments / processing instructions
            continue
        lst = node.get(child.tag)
        if not isinstance(lst, list):
            lst = [None]
            node[child.tag] = lst
        lst.append(_element_to_node(child, depth + 1, budget))
    return node


def xml_file_to_hash(content: bytes) -> list[Any]:
    """Parse a Znuny stats export (``<otrs_stats>``) into the same shape as ``build_xml_hash``.

    The XML declaration decides the encoding (the Znuny samples are ISO-8859-1); DTDs and
    entities are refused (defusedxml).
    """
    if len(content) > MAX_FILE_BYTES:
        raise XmlHashError(f"Die Datei ist zu groß (höchstens {MAX_FILE_BYTES // 1_000_000} MB).")
    try:
        root = SafeET.fromstring(content, forbid_dtd=True)
    except (DefusedXmlException, ParseError, ValueError, LookupError, RecursionError) as exc:
        raise XmlHashError(_BAD_FILE) from exc
    if root.tag != "otrs_stats":
        raise XmlHashError("Die Datei ist keine Znuny-Statistik.")
    node = _element_to_node(root, 1, [MAX_STAT_ROWS])
    return [None, {"otrs_stats": [None, node]}]
