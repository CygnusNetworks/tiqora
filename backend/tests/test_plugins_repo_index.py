"""Repository index format v1: parsing, validation, signature check, URL and version rules."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest
from packaging.version import Version

from tests.plugins.package_builder import make_keypair
from tiqora.plugins.repo_index import (
    MAX_INDEX_BYTES,
    IndexFormatError,
    newest_compatible,
    parse_index,
    publisher_keys,
    resolve_package_url,
    safe_url,
    verify_index,
)
from tiqora.plugins.signing import sign_bytes

_PRIVATE, PUBLIC, KEY_ID = make_keypair()


def _version(version: str, **kw: Any) -> dict[str, Any]:
    return {
        "version": version,
        "url": f"packages/org.example.hello-{version}.tqplugin",
        "sha256": "a" * 64,
        "size": 48213,
        "sdk": ">=0.2,<0.3",
        "tiqora": ">=0.19",
        "requires": {},
        "released_at": "2026-10-01T00:00:00Z",
        "changelog": "Neu",
        "yanked": False,
        **kw,
    }


def _index() -> dict[str, Any]:
    return {
        "index_version": 1,
        "repository": {
            "id": "org.example.plugins",
            "name": "Example Erweiterungen",
            "homepage": "https://plugins.example.org",
            "generated_at": "2026-10-12T08:00:00Z",
        },
        "publishers": [
            {
                "id": "org.example",
                "name": "Example GmbH",
                "homepage": "https://example.org",
                "support": "support@example.org",
                "keys": [{"key_id": KEY_ID, "public_key": PUBLIC, "revoked": False}],
            }
        ],
        "plugins": [
            {
                "id": "org.example.hello",
                "short_id": "hello",
                "name": "Hallo-Erweiterung",
                "publisher_id": "org.example",
                "description": "Zeigt eine Begrüßung.",
                "categories": ["tickets"],
                "license": "MIT",
                "homepage": "https://example.org/hello",
                "support": "support@example.org",
                "icon_url": None,
                "screenshots": [],
                "versions": [_version("1.1.0")],
            }
        ],
    }


def _raw(data: dict[str, Any]) -> bytes:
    return json.dumps(data).encode()


def test_parse_valid_index() -> None:
    idx = parse_index(_raw(_index()))
    assert idx.plugins[0].versions[0].version == "1.1.0"
    assert idx.repository.id == "org.example.plugins"
    assert idx.publishers[0].keys[0].key_id == KEY_ID
    assert idx.plugins[0].support == "mailto:support@example.org"


def test_unknown_fields_ignored() -> None:
    data = _index()
    data["x"] = 1
    data["plugins"][0]["future"] = {"a": 1}
    data["plugins"][0]["versions"][0]["more"] = [1]
    idx = parse_index(_raw(data))
    assert idx.plugins[0].id == "org.example.hello"
    assert not hasattr(idx, "x")


def _set(path: list[Any], value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(d: dict[str, Any]) -> None:
        node: Any = d
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value

    return mutate


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (_set(["index_version"], 2), "index_version"),
        (_set(["plugins", 0, "id"], "Org.Example"), "Kennung"),
        (_set(["plugins", 0, "short_id"], "Hello!"), "Kurzname"),
        (_set(["plugins", 0, "versions", 0, "sha256"], "xyz"), "sha256"),
        (_set(["plugins", 0, "versions", 0, "version"], "not-a-version"), "Version"),
        (_set(["plugins", 0, "versions", 0, "sdk"], "~~1"), "Versionsangabe"),
        (_set(["publishers", 0, "keys", 0, "key_id"], "0" * 16), "key_id"),
        (_set(["publishers", 0, "keys", 0, "public_key"], "ed25519:AAAA"), "Schlüssel"),
        (_set(["plugins", 0, "publisher_id"], "org.unknown"), "Herausgeber"),
        (lambda d: d["plugins"].append(copy.deepcopy(d["plugins"][0])), "doppelt"),
        (
            lambda d: d["plugins"][0]["versions"].append(_version("1.1.0")),
            "doppelt",
        ),
        (lambda d: d["publishers"].append(copy.deepcopy(d["publishers"][0])), "doppelt"),
    ],
)
def test_parse_rejects(mutate: Callable[[dict[str, Any]], None], needle: str) -> None:
    data = _index()
    mutate(data)
    with pytest.raises(IndexFormatError) as exc:
        parse_index(_raw(data))
    assert needle in str(exc.value)


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"\xff\xfe", b'{"index_version": 1}'])
def test_parse_rejects_garbage(raw: bytes) -> None:
    with pytest.raises(IndexFormatError):
        parse_index(raw)


def test_parse_rejects_oversized() -> None:
    with pytest.raises(IndexFormatError, match="zu groß"):
        parse_index(b" " * (MAX_INDEX_BYTES + 1))


def test_hostile_urls_dropped_not_rendered() -> None:
    data = _index()
    plugin = data["plugins"][0]
    plugin["homepage"] = "javascript:alert(1)"
    plugin["support"] = "javascript:alert(1)"
    plugin["icon_url"] = "http://example.org/i.png"
    plugin["screenshots"] = ["https://example.org/a.png", "data:text/html,x", "x" * 501]
    data["publishers"][0]["homepage"] = "JavaScript:alert(1)"
    data["repository"]["homepage"] = "https://x.example.org/" + "a" * 500
    idx = parse_index(_raw(data))
    assert idx.plugins[0].homepage is None
    assert idx.plugins[0].support is None
    assert idx.plugins[0].icon_url is None
    assert idx.plugins[0].screenshots == ["https://example.org/a.png"]
    assert idx.publishers[0].homepage is None
    assert idx.repository.homepage is None


def test_safe_url_rules() -> None:
    assert safe_url("https://example.org/a") == "https://example.org/a"
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("http://example.org") is None
    assert safe_url("https://example.org/a b") is None
    assert safe_url("mailto:a@example.org") is None  # only for support
    assert safe_url("mailto:a@example.org", mailto=True) == "mailto:a@example.org"
    assert safe_url("a@example.org", mailto=True) == "mailto:a@example.org"
    assert safe_url("", mailto=True) is None
    assert safe_url(None) is None


def test_verify_index_states() -> None:
    raw = _raw(_index())
    assert verify_index(raw, None, None) is False  # no key configured: usable, not verified
    sig = sign_bytes(_PRIVATE, raw)
    assert verify_index(raw, sig, PUBLIC) is True
    with pytest.raises(IndexFormatError, match="Signatur der Paketquelle ungültig"):
        verify_index(raw, None, PUBLIC)
    with pytest.raises(IndexFormatError, match="Signatur der Paketquelle ungültig"):
        verify_index(raw + b" ", sig, PUBLIC)
    other, _pub, _kid = make_keypair()
    with pytest.raises(IndexFormatError, match="Signatur der Paketquelle ungültig"):
        verify_index(raw, sign_bytes(other, raw), PUBLIC)
    with pytest.raises(IndexFormatError, match="Signatur der Paketquelle ungültig"):
        verify_index(raw, b"garbage", PUBLIC)
    with pytest.raises(IndexFormatError, match="Signatur der Paketquelle ungültig"):
        verify_index(raw, sig, "ed25519:garbage")


def test_resolve_package_url() -> None:
    idx = "https://p.example.org/r/index.json"
    assert resolve_package_url(idx, "pkgs/a.tqplugin") == "https://p.example.org/r/pkgs/a.tqplugin"
    assert (
        resolve_package_url(idx, "https://cdn.example.org/a.tqplugin")
        == "https://cdn.example.org/a.tqplugin"
    )
    for bad in ["http://p.example.org/a.tqplugin", "//evil.example.org/a", "ftp://x/a", ""]:
        with pytest.raises(IndexFormatError):
            resolve_package_url(idx, bad)


def test_resolve_package_url_file_repository() -> None:
    idx = "file:///srv/repo/index.json"
    assert resolve_package_url(idx, "pkgs/a.tqplugin") == "file:///srv/repo/pkgs/a.tqplugin"
    for bad in ["https://example.org/a", "file:///etc/passwd", "/etc/passwd", "../x.tqplugin"]:
        with pytest.raises(IndexFormatError):
            resolve_package_url(idx, bad)


def test_newest_compatible_skips_yanked_and_incompatible() -> None:
    data = _index()
    data["plugins"][0]["versions"] = [
        _version("1.0.0"),
        _version("1.1.0"),
        _version("1.2.0", yanked=True),
        _version("1.3.0", sdk=">=0.3"),
        _version("1.4.0", tiqora=">=9"),
        _version("0.9.0"),
    ]
    plugin = parse_index(_raw(data)).plugins[0]
    best = newest_compatible(plugin, core=Version("0.19.0"), sdk="0.2.0")
    assert best is not None and best.version == "1.1.0"
    # dev build without a release version ignores the "tiqora" range
    dev = newest_compatible(plugin, core=None, sdk="0.2.0")
    assert dev is not None and dev.version == "1.4.0"
    # an SDK none of the versions supports -> nothing
    only_old = plugin.model_copy(update={"versions": [plugin.versions[0]]})
    assert newest_compatible(only_old, core=Version("0.19.0"), sdk="9.0.0") is None


def test_publisher_keys_of_verified_index() -> None:
    data = _index()
    _p, pub2, kid2 = make_keypair()
    data["publishers"][0]["keys"].append({"key_id": kid2, "public_key": pub2, "revoked": True})
    keys = publisher_keys(parse_index(_raw(data)), 7)
    assert [(k.key_id, k.revoked, k.repository_id, k.publisher_id) for k in keys] == [
        (KEY_ID, False, 7, "org.example"),
        (kid2, True, 7, "org.example"),
    ]
    assert keys[0].publisher_name == "Example GmbH"
