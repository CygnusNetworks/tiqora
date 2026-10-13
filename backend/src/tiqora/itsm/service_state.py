"""Service incident state from linked CIs: one rule for Mode A and Mode B (R12, pure).

Behaviour survey, question 2 (``docs/itsm-import.md``; ``Kernel/System/Service.pm``
``_ServiceGetCurrentIncidentState`` and ``ITSMConfigItem::CurInciStateRecalc``):

* only links of a type with a propagation setting count, in that direction (reversed for the
  service, see :func:`counts_for_service`);
* only CIs whose deployment state has the functionality ``productive`` count;
* the worst incident functionality of the counted CIs wins: ``incident`` before ``warning`` before
  ``operational`` (a CI's current state already carries the transitive ``warning``);
* only while the service stays ``operational`` that way, a valid direct child service
  (``Parent::Child`` by name) that is not operational raises it to ``warning``, never to
  ``incident``;
* a service without any linked CI and without such a child has no state.

The Mode A provider feeds Znuny's stored states, the Tiqora CMDB its own; both fold here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

ServiceState = Literal["operational", "warning", "incident"]

_REVERSED: Final[Mapping[str, str]] = {
    "source": "target",
    "target": "source",
    "both": "both",
    "none": "none",
}


def counts_for_service(propagation: str, pointed: bool, service_is_source: bool) -> bool:
    """``LinkKeyListWithData(Object1 => Service, Direction => reverse(propagation))``.

    ``propagation`` is the direction from the CI's point of view; Znuny reads it from the
    service, so it is reversed. ``Source``: the CI is the link source (an unpointed type is
    stored either way and counts either way); ``Target``: the service is the source of a pointed
    link; unpointed types never match ``Target``.
    """
    wanted = _REVERSED.get(propagation, "none")
    if wanted == "both":
        return True
    if wanted == "source":
        return (not pointed) or (not service_is_source)
    if wanted == "target":
        return pointed and service_is_source
    return False


@dataclass(frozen=True, slots=True)
class ServiceFold:
    state: ServiceState
    has_cis: bool  # any CI linked at all (any type, any deployment state)
    cis_incident: int
    cis_warning: int

    @property
    def shown(self) -> ServiceState | None:
        """``None`` for a service that is operational only because nothing is linked to it."""
        return None if self.state == "operational" and not self.has_cis else self.state


def service_closure(
    services: Mapping[int, tuple[str, bool]], wanted: Iterable[int]
) -> tuple[set[int], dict[str, list[int]]]:
    """``wanted`` plus all valid descendants (``Parent::Child`` names); ``services`` maps every
    service id to ``(name, valid)``. Returns the closure and the valid children per parent name."""
    children_of: dict[str, list[int]] = defaultdict(list)
    for sid, (name, valid) in services.items():
        if valid and "::" in name:
            children_of[name.rpartition("::")[0]].append(sid)
    closure: set[int] = set()
    pending = [s for s in wanted if s in services]
    while pending:
        sid = pending.pop()
        if sid in closure:
            continue
        closure.add(sid)
        pending.extend(children_of.get(services[sid][0], ()))
    return closure, dict(children_of)


def fold_service_states(
    services: Mapping[int, tuple[str, bool]],
    children_of: Mapping[str, list[int]],
    closure: Iterable[int],
    counted: Mapping[int, Iterable[str | None]],
    linked: Mapping[int, bool],
) -> dict[int, ServiceFold]:
    """Fold the counted CIs' incident functionalities per service, deepest service first.

    ``counted``: per service the incident functionality of each counted CI (productive,
    propagating link); ``linked``: whether the service has any linked CI at all.
    """
    final: dict[int, ServiceFold] = {}
    for sid in sorted(closure, key=lambda s: -services[s][0].count("::")):
        kinds = list(counted.get(sid, ()))
        n_incident = sum(1 for k in kinds if k == "incident")
        n_warning = sum(1 for k in kinds if k == "warning")
        state: ServiceState = (
            "incident" if n_incident else "warning" if n_warning else "operational"
        )
        if state == "operational":
            for child in children_of.get(services[sid][0], ()):
                child_fold = final.get(child)
                if child_fold is not None and child_fold.state != "operational":
                    state = "warning"
                    break
        final[sid] = ServiceFold(state, bool(linked.get(sid)), n_incident, n_warning)
    return final


__all__ = [
    "ServiceFold",
    "ServiceState",
    "counts_for_service",
    "fold_service_states",
    "service_closure",
]
