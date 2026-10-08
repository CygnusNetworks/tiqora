"""Port of ``Kernel/GenericInterface/Mapping/Simple.pm`` (``new``/``_ConfigCheck`` and ``Map``)."""

from __future__ import annotations

from typing import Any

from tiqora.api.compat.mapping import DataInclude, MappingConfigError, MappingError
from tiqora.api.compat.mapping.perl import (
    perl_fullmatch_x,
    perl_is_string,
    perl_str,
    perl_truthy,
)

_STRING_ONLY = ("KeyMapExact", "KeyMapRegEx", "KeyMapDefault", "ValueMapDefault")
_VALID_MAP_TYPES = ("Keep", "Ignore", "MapTo")


def _hash_with_data(value: Any) -> bool:
    return isinstance(value, dict) and bool(value)


def _string_with_data(value: Any) -> bool:
    return perl_is_string(value) and perl_str(value) != ""


def _str_dict(value: dict[Any, Any]) -> dict[str, Any]:
    """Perl hash keys are always strings (YAML may hand us ints)."""
    return {perl_str(k): v for k, v in value.items()}


def _config_check(config: Any) -> None:
    """Port of ``_ConfigCheck`` (Config already known to be None or a non-empty dict)."""
    if config is None:
        return
    if not isinstance(config, dict):
        raise MappingConfigError("Config is defined but not a hash reference!")
    if not config:
        return

    for config_type in (
        "KeyMapExact",
        "KeyMapRegEx",
        "KeyMapDefault",
        "ValueMap",
        "ValueMapDefault",
    ):
        section = config.get(config_type)
        if section is None:
            if config_type in ("KeyMapDefault", "ValueMapDefault"):
                raise MappingConfigError(f"Got no {config_type}, but it is required!")
            continue
        if not _hash_with_data(section):
            raise MappingConfigError(
                f"Got {config_type} with Data, but Data is no hash ref with content!"
            )
        if config_type not in _STRING_ONLY:
            continue
        for key, val in section.items():
            if not perl_is_string(key):
                raise MappingConfigError(f"Got key in {config_type} which is not a string!")
            if not perl_is_string(val):
                raise MappingConfigError(
                    f"Got value for {perl_str(key)} in {config_type} which is not a string!"
                )

    for config_type in ("KeyMapDefault", "ValueMapDefault"):
        section = config[config_type]
        map_type = section.get("MapType")
        if not _string_with_data(map_type) or map_type not in _VALID_MAP_TYPES:
            raise MappingConfigError(f"Got no valid MapType in {config_type}!")
        if map_type == "MapTo" and not _string_with_data(section.get("MapTo")):
            raise MappingConfigError(
                f"Got MapType 'MapTo', but MapTo value is not valid in {config_type}!"
            )

    value_map = config.get("ValueMap")
    if isinstance(value_map, dict) and value_map:
        for key_name, entry in value_map.items():
            if not _hash_with_data(entry):
                raise MappingConfigError(
                    f"Got {perl_str(key_name)} in ValueMap, but it is no hash ref with content!"
                )
            for sub_name in ("ValueMapExact", "ValueMapRegEx"):
                sub = entry.get(sub_name)
                if sub is None:
                    continue
                if not _hash_with_data(sub):
                    raise MappingConfigError(
                        f"Got {sub_name} in {perl_str(key_name)} in ValueMap,"
                        " but it is no hash ref with content!"
                    )
                for sub_key, sub_val in sub.items():
                    if not perl_is_string(sub_key):
                        raise MappingConfigError(
                            f"Got key in {sub_name} in {perl_str(key_name)} in ValueMap"
                            " which is not a string!"
                        )
                    if not perl_is_string(sub_val):
                        raise MappingConfigError(
                            f"Got value for {perl_str(sub_key)} in {sub_name} in"
                            f" {perl_str(key_name)} in ValueMap which is not a string!"
                        )


class SimpleMapper:
    """``Mapping::Simple``: 1:1 and regex mapping of top-level keys and values."""

    def __init__(self, config: dict[str, Any] | None) -> None:
        _config_check(config)
        self._config: dict[str, Any] | None = None
        if not config:
            return
        normalised: dict[str, Any] = {}
        for name, section in config.items():
            normalised[name] = _str_dict(section) if isinstance(section, dict) else section
        value_map = normalised.get("ValueMap")
        if isinstance(value_map, dict):
            normalised["ValueMap"] = {
                key: {sub: (_str_dict(v) if isinstance(v, dict) else v) for sub, v in entry.items()}
                for key, entry in value_map.items()
            }
        self._config = normalised

    def map(self, data: Any, data_include: DataInclude | None = None) -> Any:
        if data is not None and not isinstance(data, dict):
            raise MappingError("Got Data but it is not a hash ref in Mapping Simple backend!")
        if not data:
            return {}
        config = self._config
        if not config:
            return data

        result: dict[str, Any] = {}
        for old_key, old_value in sorted(data.items(), key=lambda item: perl_str(item[0])):
            old_key = perl_str(old_key)
            if old_key == "":
                continue  # "Got an original key that is not valid!"
            new_key = self._map_key(config, old_key, result)
            if new_key is None:
                continue
            self._map_value(config, new_key, old_value, result)
        return result

    @staticmethod
    def _map_key(config: dict[str, Any], old_key: str, result: dict[str, Any]) -> str | None:
        """Return the new key, or None when the pair is dropped (ignored or collided)."""
        new_key = ""
        exact = (config.get("KeyMapExact") or {}).get(old_key)
        if perl_truthy(exact):
            new_key = perl_str(exact)

        key_regex: dict[str, Any] = config.get("KeyMapRegEx") or {}
        if not perl_truthy(new_key):
            for pattern in sorted(key_regex):
                if not perl_fullmatch_x(pattern, old_key):
                    continue
                target = perl_str(key_regex[pattern])
                if perl_truthy(result.get(target)):
                    return None  # "The data key '...' already exists!"
                new_key = target
                break

        if not perl_truthy(new_key):
            default = config["KeyMapDefault"]
            if default["MapType"] == "Keep":
                new_key = old_key
            elif default["MapType"] == "Ignore":
                return None
            else:  # MapTo
                target = perl_str(default["MapTo"])
                if perl_truthy(result.get(target)):
                    return None
                new_key = target

        if not perl_truthy(new_key):
            raise MappingError(f"Could not map data key {new_key}!")
        return new_key

    @staticmethod
    def _map_value(
        config: dict[str, Any], new_key: str, old_value: Any, result: dict[str, Any]
    ) -> None:
        if not perl_is_string(old_value):
            result[new_key] = old_value  # nested structures / booleans / null pass through
            return
        text = perl_str(old_value)

        entry = (config.get("ValueMap") or {}).get(new_key)
        if entry:
            exact_map = entry.get("ValueMapExact")
            if exact_map and text in exact_map:  # Perl `defined`: an empty target still counts
                result[new_key] = exact_map[text]
                return
            regex_map = entry.get("ValueMapRegEx") or {}
            for pattern in sorted(regex_map):
                if perl_fullmatch_x(pattern, text):
                    result[new_key] = regex_map[pattern]
                    return

        default = config["ValueMapDefault"]
        if default["MapType"] == "Keep":
            result[new_key] = old_value
        elif default["MapType"] == "MapTo":
            result[new_key] = default["MapTo"]
        # Ignore: dropped
