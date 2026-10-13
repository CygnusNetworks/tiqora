"""``tiqora.znuny.link_config``: the one reader of ``LinkObject::*`` (final review M3).

Pure: a stand-in SysConfig answers ``get_prefix`` / ``get``. The write path
(``link_objects``) and the ITSM catalogue (``znuny_link_types``) both read through it, so the
two can no longer drift; this module checks the reader and that the catalogue follows it.
"""

from __future__ import annotations

from typing import Any

from tiqora.itsm.link_types import znuny_link_types
from tiqora.znuny.link_config import DEFAULT_LINK_CONFIG, read_link_config


class _SysConfig:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    async def get_prefix(self, prefix: str) -> dict[str, Any]:
        return {k: v for k, v in self.values.items() if k.startswith(prefix)}

    async def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)


CONFIG: dict[str, Any] = {
    "LinkObject::Type###Normal": {"SourceName": "Normal", "TargetName": "Normal"},
    "LinkObject::Type###ParentChild": {"SourceName": "Parent", "TargetName": " Child "},
    "LinkObject::Type###DependsOn": {"SourceName": "Depends on", "TargetName": "Required for"},
    "LinkObject::Type###Broken": {"SourceName": "only one"},
    "LinkObject::PossibleLink###0200": {"Object1": "Ticket", "Object2": "Ticket", "Type": "Normal"},
    "LinkObject::PossibleLink###0201": {
        "Object1": "Ticket",
        "Object2": "Ticket",
        "Type": "ParentChild",
    },
    "LinkObject::PossibleLink###3220": {
        "Object1": "ITSMConfigItem",
        "Object2": "Ticket",
        "Type": "DependsOn",
    },
    "LinkObject::PossibleLink###9999": {"Object1": "Ticket", "Object2": "Ticket", "Type": "Gone"},
    "LinkObject::TypeGroup###0001": ["Normal", "ParentChild"],
    "LinkObject::TypeGroup###0002": ["Normal", "Gone"],
    "ITSM::Core::IncidentLinkTypeDirection": {"DependsOn": "Both"},
}


async def test_reader_ports_type_possible_and_group_lists() -> None:
    cfg = await read_link_config(_SysConfig(CONFIG))  # type: ignore[arg-type]
    assert list(cfg.names) == ["DependsOn", "Normal", "ParentChild"]  # Broken lacks a name
    assert cfg.pointed("ParentChild") and not cfg.pointed("Normal")
    assert not cfg.pointed("Unknown")
    # either order counts; a PossibleLink of an unknown type is dropped
    assert cfg.allows("DependsOn", "Ticket", "ITSMConfigItem")
    assert not cfg.allows("DependsOn", "Ticket", "Ticket")
    assert "Gone" not in cfg.possible
    # a group with an unknown member does not count
    assert cfg.groups == (("Normal", "ParentChild"),)
    assert cfg.same_group("ParentChild", "Normal") and not cfg.same_group("Normal", "DependsOn")


async def test_reader_falls_back_to_framework_defaults() -> None:
    cfg = await read_link_config(_SysConfig({}))  # type: ignore[arg-type]
    assert cfg is DEFAULT_LINK_CONFIG
    assert cfg.allows("ParentChild", "Ticket", "Ticket") and cfg.pointed("ParentChild")
    assert cfg.same_group("Normal", "ParentChild")


async def test_itsm_catalogue_follows_the_reader() -> None:
    sysconfig = _SysConfig(CONFIG)
    cfg = await read_link_config(sysconfig)  # type: ignore[arg-type]
    catalogue = await znuny_link_types(sysconfig)  # type: ignore[arg-type]
    assert list(catalogue) == list(cfg.names)
    for name, znuny in catalogue.items():
        assert (znuny.source_name, znuny.target_name) == cfg.names[name]
        assert znuny.pointed == cfg.pointed(name) and znuny.possible == cfg.possible[name]
    assert catalogue["DependsOn"].propagation == "both"
    fallback = await znuny_link_types(_SysConfig({}))  # type: ignore[arg-type]
    assert {n: (t.source_name, t.target_name) for n, t in fallback.items()} == dict(
        DEFAULT_LINK_CONFIG.names
    )
