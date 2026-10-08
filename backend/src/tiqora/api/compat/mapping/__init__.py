"""GenericInterface data mapping (port of ``Kernel::GenericInterface::Mapping``).

Mappers are pure and synchronous: they never touch the DB, the network or the file system.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class MappingError(Exception):
    """Map() returned Success => 0 (Znuny DebuggerObject->Error). str(exc) = Znuny's Summary."""


class MappingConfigError(MappingError):
    """Mapping->new() failed (bad MappingConfig) - Znuny returns the error before Map runs."""


DataInclude = dict[str, Any]


class Mapper(Protocol):
    def map(self, data: Any, data_include: DataInclude | None = None) -> Any: ...


MapperFactory = Callable[[dict[str, Any] | None, bool], Mapper]  # (Config, huge_xml) -> mapper


def _simple_factory(config: dict[str, Any] | None, huge_xml: bool) -> Mapper:
    from tiqora.api.compat.mapping.simple import SimpleMapper

    return SimpleMapper(config)


def _xslt_factory(config: dict[str, Any] | None, huge_xml: bool) -> Mapper:
    from tiqora.api.compat.mapping.xslt import XsltMapper

    return XsltMapper(config, huge_xml=huge_xml)


def _out_of_office_factory(config: dict[str, Any] | None, huge_xml: bool) -> Mapper:
    from tiqora.api.compat.mapping.out_of_office import OutOfOfficeMapper

    return OutOfOfficeMapper(config)


MAPPER_FACTORIES: dict[str, MapperFactory] = {
    "Simple": _simple_factory,
    "XSLT": _xslt_factory,
    "OutOfOffice": _out_of_office_factory,
}


def build_mapper(mapping_config: Any, *, huge_xml: bool = False) -> Mapper:
    """Port of ``Kernel::GenericInterface::Mapping->new``.

    Raises MappingConfigError for a config Znuny would reject before Map() runs.
    """
    if not isinstance(mapping_config, dict) or not mapping_config:
        raise MappingConfigError("Got no MappingConfig as hash ref with content!")
    mapping_type = mapping_config.get("Type")
    if not isinstance(mapping_type, str) or mapping_type == "":
        raise MappingConfigError("Got no MappingConfig with Type as string with value!")
    config = mapping_config.get("Config")
    if config is not None and (not isinstance(config, dict) or not config):
        raise MappingConfigError(
            "Got MappingConfig with Data, but Data is no hash ref with content!"
        )
    factory = MAPPER_FACTORIES.get(mapping_type)
    if factory is None:
        raise MappingConfigError(f"Mapping type '{mapping_type}' is not supported by Tiqora")
    return factory(config, huge_xml)
