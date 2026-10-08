from __future__ import annotations

from dataclasses import dataclass

import pytest

from tiqora.extensions import (
    DuplicateExtensionError,
    ExtensionPoint,
    Registry,
    RegistryFrozenError,
    get_registry,
    register_core,
    set_registry_for_tests,
)


@dataclass(frozen=True)
class _Item:
    key: str
    value: int = 0


POINT: ExtensionPoint[_Item] = ExtensionPoint(
    key="test.items", item_key=lambda i: i.key, description="test point"
)


def test_add_get_items_keep_registration_order() -> None:
    reg = Registry()
    reg.add(POINT, _Item("b"))
    reg.add(POINT, _Item("a"), owner="com.example.plugin")
    assert [i.key for i in reg.items(POINT)] == ["b", "a"]
    assert reg.get(POINT, "a") == _Item("a")
    assert reg.get(POINT, "missing") is None
    owners = {r.key: r.owner for r in reg.registrations(POINT)}
    assert owners == {"b": "core", "a": "com.example.plugin"}


def test_duplicate_key_is_rejected_even_from_another_owner() -> None:
    reg = Registry()
    reg.add(POINT, _Item("a"))
    with pytest.raises(DuplicateExtensionError, match="test.items"):
        reg.add(POINT, _Item("a", 1), owner="com.example.plugin")


def test_require_names_point_and_key() -> None:
    with pytest.raises(KeyError, match="test.items: nope"):
        Registry().require(POINT, "nope")


def test_frozen_registry_refuses_additions() -> None:
    reg = Registry()
    reg.freeze()
    with pytest.raises(RegistryFrozenError):
        reg.add(POINT, _Item("a"))


def test_core_registry_has_channels_and_is_frozen() -> None:
    from tiqora.api.v1.admin.channels import CHANNEL_CONFIG_KEYS
    from tiqora.channels.registry import CHANNELS

    set_registry_for_tests(None)
    reg = get_registry()
    assert reg.frozen
    assert {c.key for c in reg.items(CHANNELS)} == set(CHANNEL_CONFIG_KEYS)
    assert get_registry() is reg  # cached


def test_register_core_twice_on_one_registry_fails_loudly() -> None:
    reg = Registry()
    register_core(reg)
    with pytest.raises(DuplicateExtensionError):
        register_core(reg)
