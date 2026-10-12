from __future__ import annotations

from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.plugins.naming import (
    CAMEL_NAME_RE,
    TAG_NAME_RE,
    channel_key,
    is_plugin_name,
    qualified,
)


def test_qualified_names() -> None:
    assert qualified("points", "SetContract") == "Plugin_points_SetContract"
    assert channel_key("points", "Chat") == "plugin_points_chat"
    assert is_plugin_name("Kernel::System::ProcessManagement::TransitionAction::Plugin_points_X")
    assert is_plugin_name("Plugin_points::ContractGet") and not is_plugin_name(
        "Ticket::TicketCreate"
    )
    for bad in ("setContract", "Set_Contract", "S", "X" * 42, "Set-Contract"):
        assert not CAMEL_NAME_RE.fullmatch(bad)
    for good in ("SetContract", "Ab", "X" * 41):
        assert CAMEL_NAME_RE.fullmatch(good)


def test_tag_names() -> None:
    assert TAG_NAME_RE.fullmatch("GREETING_TEXT")
    for bad in ("greeting", "G", "1GREETING", "GREETING-X"):
        assert not TAG_NAME_RE.fullmatch(bad)


def test_core_never_uses_plugin_prefix() -> None:
    registry = Registry()
    register_core(registry)
    regs = registry.registrations()
    assert regs
    for reg in regs:
        # core keys stay outside the plugin namespace
        assert not reg.key.lower().startswith(("plugin_", "plugin.")), reg
