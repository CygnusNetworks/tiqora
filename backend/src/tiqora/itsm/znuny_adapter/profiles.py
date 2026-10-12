"""Schema profiles of the Znuny ITSM packages (constants from the Task 2 survey).

Two profiles cover the surveyed lines: ``itsm-6`` (Znuny 6.5.x) and ``itsm-7`` (Znuny 7.3.x).
Their ITSM tables are identical except ``general_catalog_preferences`` (7.x has a column ``id``),
which no reader addresses, so every adapter query is the same for both. Other lines (6.0 - 6.4,
7.0 - 7.2) were not surveyed and are reported as unsupported, never read on a guess.
See ``docs/itsm-import.md`` ("Unterstützte Versionen", "Schema je Version").
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal


@dataclass(frozen=True, slots=True)
class SchemaProfile:
    name: str
    #: package -> version spec this profile covers, e.g. ``>=6.5,<6.6`` (all must match)
    package_versions: Mapping[str, str]
    #: ``yaml`` in both surveyed lines; ``perl`` only survives in databases from old OTRS stock
    definition_format: Literal["perl", "yaml"]
    attribute_storage: Literal["xml_storage"]
    #: ``xml_storage.xml_type`` = prefix + class id for the current version ...
    xml_type_prefix: str
    #: ... and prefix + class id for older versions (moved there when a new version is added)
    xml_archive_prefix: str
    version_has_name: bool
    version_has_states: bool
    service_itsm_columns: tuple[str, ...]
    sla_itsm_columns: tuple[str, ...]
    ci_link_object: str
    #: role -> ``general_catalog.general_catalog_class``
    catalog_classes: Mapping[str, str]


_CATALOG_CLASSES: Final[Mapping[str, str]] = {
    "ci_class": "ITSM::ConfigItem::Class",
    "deployment_state": "ITSM::ConfigItem::DeploymentState",
    "incident_state": "ITSM::Core::IncidentState",
    "service_type": "ITSM::Service::Type",
    "sla_type": "ITSM::SLA::Type",
}


def _profile(name: str, spec: str) -> SchemaProfile:
    return SchemaProfile(
        name=name,
        package_versions={"ITSMCore": spec, "ITSMConfigurationManagement": spec},
        definition_format="yaml",
        attribute_storage="xml_storage",
        xml_type_prefix="ITSM::ConfigItem::",
        xml_archive_prefix="ITSM::ConfigItem::Archiv::",
        version_has_name=True,
        version_has_states=True,
        service_itsm_columns=("type_id", "criticality"),
        sla_itsm_columns=("type_id", "min_time_bet_incidents"),
        ci_link_object="ITSMConfigItem",
        catalog_classes=_CATALOG_CLASSES,
    )


PROFILES: Final[Mapping[str, SchemaProfile]] = {
    "itsm-6": _profile("itsm-6", ">=6.5,<6.6"),
    "itsm-7": _profile("itsm-7", ">=7.3,<7.4"),
}

#: The package whose version names a CI storage layout; its absence is not an error here
#: (ITSMCore alone gives incident states and the priority matrix, but no CIs).
CMDB_PACKAGE: Final = "ITSMConfigurationManagement"
CORE_PACKAGE: Final = "ITSMCore"

_VERSION_RE: Final = re.compile(r"^\s*(\d+(?:\.\d+)*)")


def _version_tuple(raw: str) -> tuple[int, ...] | None:
    m = _VERSION_RE.match(raw)
    if m is None:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def version_matches(version: str, spec: str) -> bool:
    """``version`` satisfies a comma separated list of ``>=  >  <  <=  ==`` terms."""
    have = _version_tuple(version)
    if have is None:
        return False
    for term in spec.split(","):
        m = re.match(r"^\s*(>=|<=|==|>|<)\s*(\d+(?:\.\d+)*)\s*$", term)
        if m is None:
            raise ValueError(f"bad version spec: {spec!r}")
        op, want_raw = m.groups()
        a, b = _pad(have, tuple(int(p) for p in want_raw.split(".")))
        ok = {
            ">=": a >= b,
            "<=": a <= b,
            "==": a == b,
            ">": a > b,
            "<": a < b,
        }[op]
        if not ok:
            return False
    return True


def select_profile(packages: Mapping[str, str]) -> tuple[SchemaProfile | None, str | None]:
    """The profile that covers the installed packages, or ``(None, reason)``.

    ``ITSMCore`` must be listed; ``ITSMConfigurationManagement`` is checked when it is installed.
    Several packages installed at versions of different lines never match (no mixing).
    """
    if CORE_PACKAGE not in packages:
        return None, f"{CORE_PACKAGE} is not listed in package_repository"
    needed = [CORE_PACKAGE] + ([CMDB_PACKAGE] if CMDB_PACKAGE in packages else [])
    for profile in PROFILES.values():
        if all(version_matches(packages[p], profile.package_versions[p]) for p in needed):
            return profile, None
    for pkg in reversed(needed):
        if not any(
            version_matches(packages[pkg], pr.package_versions[pkg]) for pr in PROFILES.values()
        ):
            return None, f"{pkg} {packages[pkg]} not surveyed"
    return None, "ITSM packages of different Znuny lines are installed"
