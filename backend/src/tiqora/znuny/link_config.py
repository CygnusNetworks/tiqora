"""Znuny's link-type configuration from SysConfig: the one reader of ``LinkObject::*``.

Port of ``LinkObject.pm`` ``TypeList`` / ``PossibleLinkList`` / ``TypeGroupList``:

* ``LinkObject::Type###<Name>``: a type needs both ``SourceName`` and ``TargetName``; it is
  *pointed* when the two differ (``ParentChild``), unpointed otherwise (``Normal``).
* ``LinkObject::PossibleLink###<nnnn>``: the object pairs per type; counts only for a known type.
* ``LinkObject::TypeGroup###<nnnn>``: types that cancel each other between the same two objects;
  a group counts only when all members are known types.

When SysConfig carries no link type at all (a database whose configuration was never rebuilt),
the Framework.xml / Ticket.xml defaults apply: ``Normal`` and ``ParentChild`` for Ticket-Ticket,
in one type group.

Used by the link write path (``domain.ticket_write_service.link_objects``) and by the ITSM link
type catalogue (``itsm.link_types``), so both see the same types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tiqora.znuny.sysconfig import SysConfig

TYPE_PREFIX: Final = "LinkObject::Type###"
POSSIBLE_PREFIX: Final = "LinkObject::PossibleLink###"
GROUP_PREFIX: Final = "LinkObject::TypeGroup###"


@dataclass(frozen=True, slots=True)
class LinkConfig:
    #: type name -> (SourceName, TargetName), whitespace collapsed; name order
    names: dict[str, tuple[str, str]]
    #: type name -> (Object1, Object2) pairs from ``PossibleLink``
    possible: dict[str, frozenset[tuple[str, str]]]
    groups: tuple[tuple[str, ...], ...]

    def pointed(self, link_type: str) -> bool:
        """``SourceName ne TargetName`` (False for an unknown type)."""
        names = self.names.get(link_type)
        return names is not None and names[0] != names[1]

    def allows(self, link_type: str, object1: str, object2: str) -> bool:
        """``PossibleTypesList``: the pair is listed for that type in either order."""
        pairs = self.possible.get(link_type, frozenset())
        return (object1, object2) in pairs or (object2, object1) in pairs

    def same_group(self, type1: str, type2: str) -> bool:
        """``LinkObject.pm::PossibleType`` is the negation: two types in one group cancel out."""
        return any(type1 in g and type2 in g for g in self.groups)


_TICKET_PAIR: Final = frozenset({("Ticket", "Ticket")})
DEFAULT_LINK_CONFIG: Final = LinkConfig(
    names={"Normal": ("Normal", "Normal"), "ParentChild": ("Parent", "Child")},
    possible={"Normal": _TICKET_PAIR, "ParentChild": _TICKET_PAIR},
    groups=(("Normal", "ParentChild"),),
)


def _squash(value: object) -> str:
    return " ".join(str(value or "").split())


async def read_link_config(sysconfig: SysConfig) -> LinkConfig:
    """The effective link-type configuration (see the module docstring)."""
    raw_types = await sysconfig.get_prefix(TYPE_PREFIX)
    if not raw_types:
        return DEFAULT_LINK_CONFIG
    names: dict[str, tuple[str, str]] = {}
    for key, value in raw_types.items():
        if not isinstance(value, dict):
            continue
        src, tgt = _squash(value.get("SourceName")), _squash(value.get("TargetName"))
        if src and tgt:  # TypeList drops a type without both names
            names[key.removeprefix(TYPE_PREFIX)] = (src, tgt)
    possible: dict[str, set[tuple[str, str]]] = {name: set() for name in names}
    for _key, value in sorted((await sysconfig.get_prefix(POSSIBLE_PREFIX)).items()):
        if not isinstance(value, dict):
            continue
        o1, o2, ty = (str(value.get(k) or "").strip() for k in ("Object1", "Object2", "Type"))
        if o1 and o2 and ty in possible:
            possible[ty].add((o1, o2))
    groups: list[tuple[str, ...]] = []
    for _key, value in sorted((await sysconfig.get_prefix(GROUP_PREFIX)).items()):
        if isinstance(value, list):
            members = tuple(str(t).strip() for t in value)
            if members and all(m in names for m in members):
                groups.append(members)
    return LinkConfig(
        names={name: names[name] for name in sorted(names)},
        possible={name: frozenset(pairs) for name, pairs in possible.items()},
        groups=tuple(groups),
    )


__all__ = ["DEFAULT_LINK_CONFIG", "LinkConfig", "read_link_config"]
