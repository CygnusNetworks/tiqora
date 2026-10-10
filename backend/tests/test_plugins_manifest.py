from __future__ import annotations

from pathlib import Path

import pytest

from tiqora.plugins.manifest import Capabilities, ManifestError, parse_manifest, parse_web_manifest

FIX = Path(__file__).parent / "plugins" / "fixtures" / "tq_fixture_hello"


def _manifest_text(**replace: str) -> str:
    text = (FIX / "tiqora-plugin.toml").read_text()
    for old, new in replace.items():
        text = text.replace(old, new)
    return text


def test_parse_valid_manifest() -> None:
    m = parse_manifest(_manifest_text())
    assert m.id == "org.example.hello"
    assert m.short_id == "hello"
    assert m.sdk == ">=0.1,<0.2"
    assert "core.mcp.tools" in m.capabilities.extension_points
    assert m.capabilities.outbound_hosts == ["api.example.org", "*.example.net"]


@pytest.mark.parametrize("bad", ["Org.Example.Hello", "hello", "org.example-hello", "org..hello"])
def test_invalid_id_rejected(bad: str) -> None:
    with pytest.raises(ManifestError, match="id"):
        parse_manifest(_manifest_text(**{'id = "org.example.hello"': f'id = "{bad}"'}))


@pytest.mark.parametrize("bad", ["h", "Hello", "1hello", "hello_world_too_long_x"])
def test_bad_short_id_rejected(bad: str) -> None:
    with pytest.raises(ManifestError, match="short_id"):
        parse_manifest(_manifest_text(**{'short_id = "hello"': f'short_id = "{bad}"'}))


def test_unknown_key_rejected() -> None:
    with pytest.raises(ManifestError, match="colour"):
        parse_manifest(_manifest_text(**{"[capabilities]": 'colour = "blue"\n\n[capabilities]'}))


def test_bad_specifier_rejected() -> None:
    with pytest.raises(ManifestError, match="sdk"):
        parse_manifest(_manifest_text(**{'sdk = ">=0.1,<0.2"': 'sdk = "banana"'}))


def test_toml_syntax_error_is_manifest_error() -> None:
    with pytest.raises(ManifestError, match="tiqora-plugin.toml"):
        parse_manifest("id = ")


def _web(contribs: str) -> bytes:
    return ('{"manifest_version": 1, "contributions": [' + contribs + "]}").encode()


def test_web_manifest_slot_not_declared() -> None:
    caps = Capabilities(frontend_slots=["agent.route"])
    raw = _web('{"slot": "ticket.sidebar", "key": "x", "component": "X", "title_key": "t"}')
    with pytest.raises(ManifestError, match="ticket.sidebar"):
        parse_web_manifest(raw, caps)


def test_web_manifest_unknown_slot_dropped() -> None:
    caps = Capabilities(frontend_slots=["agent.route", "portal.page"])
    raw = _web(
        '{"slot": "portal.page", "key": "x"},'
        '{"slot": "agent.route", "key": "a", "path": "a", "component": "A", "title_key": "t"}'
    )
    web = parse_web_manifest(raw, caps)
    assert [c.slot for c in web.contributions] == ["agent.route"]


def test_nav_must_name_existing_route() -> None:
    caps = Capabilities(frontend_slots=["agent.nav", "agent.route"])
    raw = _web('{"slot": "agent.nav", "key": "n", "route": "missing", "label_key": "l"}')
    with pytest.raises(ManifestError, match="missing"):
        parse_web_manifest(raw, caps)


@pytest.mark.parametrize("entry", ["../evil.js", "/abs.js", "a\\\\b.js", "index.mjs.txt"])
def test_entry_path_rejected(entry: str) -> None:
    raw = ('{"manifest_version": 1, "entry": "' + entry + '"}').encode()
    with pytest.raises(ManifestError, match="entry"):
        parse_web_manifest(raw, Capabilities())


def test_fixture_web_manifest_parses() -> None:
    m = parse_manifest(_manifest_text())
    web = parse_web_manifest((FIX / "web" / "manifest.json").read_bytes(), m.capabilities)
    assert {c.slot for c in web.contributions} == {
        "agent.route",
        "agent.nav",
        "ticket.sidebar",
        "dashboard.widget",
    }
    assert web.styles == ["hello.css"]


@pytest.mark.parametrize(
    "reserved", ["ticket", "list", "kb", "customer", "get", "core", "tiqora", "plugin", "mcp"]
)
def test_reserved_short_id_rejected(reserved: str) -> None:
    with pytest.raises(ManifestError, match="reserviert"):
        parse_manifest(_manifest_text(**{'short_id = "hello"': f'short_id = "{reserved}"'}))
