"""The parsed Znuny statistic (``StatsGet`` view) built from an XMLHash."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from tiqora.reporting.znuny_stats.xml_hash import XmlHashError
from tiqora.util.perl import perl_truthy

ElementUse = Literal["x", "series", "restriction"]
TIME_KEYS: Final = (
    "TimeStart",
    "TimeStop",
    "TimeRelativeUnit",
    "TimeRelativeCount",
    "TimeRelativeUpcomingCount",
    "TimeScaleCount",
)
_USES: Final = (
    ("x", "UseAsXvalue"),
    ("series", "UseAsValueSeries"),
    ("restriction", "UseAsRestriction"),
)


@dataclass(frozen=True, slots=True)
class StatElement:
    use: ElementUse
    element: str  # "QueueIDs", "CreateTime", "KindsOfReporting", "DynamicField_X", ...
    fixed: bool  # Perl-truthy "Fixed"; False = changeable at runtime
    selected: tuple[str, ...]  # SelectedValues contents in order ('' skipped)
    time: Mapping[str, str]  # only the TIME_KEYS present


@dataclass(frozen=True, slots=True)
class ZnunyStat:
    origin: Literal["db", "file"]
    stat_id: int | None  # xml_key for db; None for a file
    stat_number: str | None
    title: str
    description: str
    stat_type: str  # "dynamic" | "static" (anything else kept, mapped to "none")
    object_module: str  # "Kernel::System::Stats::Dynamic::Ticket"
    static_file: str | None  # static stats: "StateAction"
    valid: bool
    permission: tuple[str, ...]  # group ids (db) or group names (file), raw
    formats: tuple[str, ...]  # "Print", "CSV", "Excel", "D3::BarChart", ...
    sum_row: bool
    sum_col: bool
    time_zone: str | None
    show_as_dashboard_widget: bool
    elements: tuple[StatElement, ...]

    @property
    def import_key(self) -> str:
        if self.origin == "db":
            return f"znuny.stat.{self.stat_id}"
        digest = hashlib.sha1(f"{self.title}|{self.object_module}".encode(), usedforsecurity=False)
        return f"znuny.file.{digest.hexdigest()[:12]}"


def _items(node: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    lst = node.get(key)
    if not isinstance(lst, list):
        return []
    return [item for item in lst[1:] if isinstance(item, dict)]


def _text(node: Mapping[str, Any], key: str) -> str | None:
    items = _items(node, key)
    if not items:
        return None
    content = items[0].get("Content")
    return content if isinstance(content, str) else None


def _contents(node: Mapping[str, Any], key: str) -> tuple[str, ...]:
    out = []
    for item in _items(node, key):
        content = item.get("Content")
        if isinstance(content, str) and content != "":
            out.append(content)
    return tuple(out)


def _flag(node: Mapping[str, Any], key: str, *, default: bool = False) -> bool:
    value = _text(node, key)
    return default if value is None else perl_truthy(value)


def stat_from_xml_hash(
    xml_hash: list[Any], *, origin: Literal["db", "file"], stat_id: int | None
) -> ZnunyStat:
    """Read a statistic like ``StatsGet`` does; missing otrs_stats/Title/ObjectModule raise."""
    root = xml_hash[1] if len(xml_hash) > 1 and isinstance(xml_hash[1], dict) else None
    items = _items(root, "otrs_stats") if root is not None else []
    if not items:
        raise XmlHashError("Die Statistik ist beschädigt (otrs_stats fehlt).")
    stat = items[0]
    title = _text(stat, "Title")
    object_module = _text(stat, "ObjectModule")
    if not title:
        raise XmlHashError("Die Statistik hat keinen Titel.")
    if not object_module:
        raise XmlHashError("Die Statistik nennt kein Statistikmodul.")
    stat_type = _text(stat, "StatType") or "dynamic"
    static_file = _text(stat, "File") or None
    if static_file is None and "::Static::" in object_module:
        static_file = object_module.rsplit("::", 1)[-1]
    elements: list[StatElement] = []
    for use, tag in _USES:
        for item in _items(stat, tag):
            element = item.get("Element")
            if not isinstance(element, str) or not element:
                continue
            time = {k: item[k] for k in TIME_KEYS if isinstance(item.get(k), str)}
            elements.append(
                StatElement(
                    use=use,  # type: ignore[arg-type]
                    element=element,
                    fixed=perl_truthy(item.get("Fixed")),
                    selected=_contents(item, "SelectedValues"),
                    time=time,
                )
            )
    return ZnunyStat(
        origin=origin,
        stat_id=stat_id,
        stat_number=_text(stat, "StatNumber") or None,
        title=title,
        description=_text(stat, "Description") or "",
        stat_type=stat_type,
        object_module=object_module,
        static_file=static_file,
        valid=_flag(stat, "Valid", default=True),
        permission=_contents(stat, "Permission"),
        formats=_contents(stat, "Format"),
        sum_row=_flag(stat, "SumRow"),
        sum_col=_flag(stat, "SumCol"),
        time_zone=_text(stat, "TimeZone") or None,
        show_as_dashboard_widget=_flag(stat, "ShowAsDashboardWidget"),
        elements=tuple(elements),
    )
