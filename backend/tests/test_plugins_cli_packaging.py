"""``tiqora plugins keygen|pack|sign|inspect|index`` (D14): no database, German errors."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path

import pytest

from tests.plugins.package_builder import build_wheel
from tiqora.config import get_settings
from tiqora.main import main
from tiqora.plugins.package import read_tqplugin
from tiqora.plugins.repo_index import parse_index, verify_index
from tiqora.plugins.signing import parse_public_key


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TIQORA_PLUGINS_TRUSTED_KEYS", raising=False)
    monkeypatch.delenv("TIQORA_PLUGINS_ALLOW_UNSIGNED", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    code = exc.value.code
    return 0 if code is None else int(code)


def _pub_from(capsys: pytest.CaptureFixture[str]) -> str:
    found = re.search(r"ed25519:[A-Za-z0-9+/=]+", capsys.readouterr().out)
    assert found, "keygen must print the public key"
    return found.group(0)


def _keygen(tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str = "k.pem") -> str:
    assert _run(["plugins", "keygen", "--out", str(tmp_path / name)]) == 0
    return _pub_from(capsys)


def test_keygen_writes_0600_and_prints_public_key(tmp_path: Path, capsys) -> None:
    code = _run(["plugins", "keygen", "--out", str(tmp_path / "k.pem")])
    assert code == 0
    assert stat.S_IMODE((tmp_path / "k.pem").stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert "ed25519:" in out and "key_id:" in out
    assert (tmp_path / "k.pem").read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")


def test_keygen_refuses_overwrite(tmp_path: Path, capsys) -> None:
    key = tmp_path / "k.pem"
    key.write_bytes(b"keep me")
    assert _run(["plugins", "keygen", "--out", str(key)]) == 2
    assert key.read_bytes() == b"keep me"
    assert "existiert bereits" in capsys.readouterr().err


def test_pack_sign_inspect_roundtrip(tmp_path: Path, monkeypatch, capsys) -> None:
    wheel = build_wheel(tmp_path)
    pub = _keygen(tmp_path, capsys)
    assert _run(["plugins", "pack", str(wheel), "--output", str(tmp_path / "a.tqplugin")]) == 0
    assert (
        _run(
            [
                "plugins",
                "sign",
                str(tmp_path / "a.tqplugin"),
                "--key",
                str(tmp_path / "k.pem"),
                "--output",
                str(tmp_path / "b.tqplugin"),
            ]
        )
        == 0
    )
    with zipfile.ZipFile(tmp_path / "a.tqplugin") as zf:
        assert "SIGNATURE" not in zf.namelist()
    with zipfile.ZipFile(tmp_path / "b.tqplugin") as zf:
        assert "SIGNATURE" in zf.namelist()
    monkeypatch.setenv("TIQORA_PLUGINS_TRUSTED_KEYS", pub)
    get_settings.cache_clear()
    capsys.readouterr()
    assert _run(["plugins", "inspect", str(tmp_path / "b.tqplugin"), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["trust"] == "local"
    assert report["plugin_id"] == "org.example.hello"
    assert report["installable"] is True


def test_pack_includes_changelog_and_refuses_overwrite(tmp_path: Path, capsys) -> None:
    wheel = build_wheel(tmp_path)
    log = tmp_path / "CHANGELOG.md"
    log.write_text("Neu: alles", encoding="utf-8")
    out = tmp_path / "a.tqplugin"
    assert _run(["plugins", "pack", str(wheel), "--changelog", str(log), "--output", str(out)]) == 0
    assert read_tqplugin(out.read_bytes(), max_bytes=10**7).changelog == "Neu: alles"
    assert _run(["plugins", "pack", str(wheel), "--output", str(out)]) == 2
    assert _run(["plugins", "pack", str(wheel), "--output", str(out), "--force"]) == 0


def test_inspect_unsigned_exits_1(tmp_path: Path, capsys) -> None:
    out = tmp_path / "a.tqplugin"
    assert _run(["plugins", "pack", str(build_wheel(tmp_path)), "--output", str(out)]) == 0
    capsys.readouterr()
    assert _run(["plugins", "inspect", str(out), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["trust"] == "unsigned"
    assert report["installable"] is False
    assert any("nicht signiert" in p for p in report["problems"])


def test_inspect_unreadable_package_exits_1(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.tqplugin"
    bad.write_bytes(b"not a zip")
    assert _run(["plugins", "inspect", str(bad)]) == 1
    assert "Fehler" in capsys.readouterr().err


def test_sign_refuses_in_place_without_force(tmp_path: Path, capsys) -> None:
    pub = _keygen(tmp_path, capsys)
    pkg = tmp_path / "a.tqplugin"
    assert _run(["plugins", "pack", str(build_wheel(tmp_path)), "--output", str(pkg)]) == 0
    before = pkg.read_bytes()
    argv = ["plugins", "sign", str(pkg), "--key", str(tmp_path / "k.pem")]
    assert _run(argv) == 2
    assert pkg.read_bytes() == before
    assert _run([*argv, "--force"]) == 0
    assert pkg.read_bytes() != before
    assert read_tqplugin(pkg.read_bytes(), max_bytes=10**7).signature is not None
    assert verify_package_sig(pkg, pub)


def test_index_build_and_sign(tmp_path: Path, capsys) -> None:
    pub = _keygen(tmp_path, capsys)
    pkgs = tmp_path / "repo"
    pkgs.mkdir()
    for version in ("1.0.0", "1.10.0"):
        wheel = build_wheel(tmp_path, version=version)
        out = pkgs / f"org.example.hello-{version}.tqplugin"
        assert _run(["plugins", "pack", str(wheel), "--output", str(out)]) == 0
    index_path = tmp_path / "index.json"
    assert (
        _run(
            [
                "plugins",
                "index",
                "build",
                str(pkgs),
                "--repository-id",
                "org.example.plugins",
                "--name",
                "Example Erweiterungen",
                "--publisher-key",
                pub,
                "--publisher-id",
                "org.example",
                "--publisher-name",
                "Example GmbH",
                "--output",
                str(index_path),
            ]
        )
        == 0
    )
    raw = index_path.read_bytes()
    index = parse_index(raw)
    assert index.repository.id == "org.example.plugins"
    assert index.publishers[0].id == "org.example"
    (plugin,) = index.plugins
    assert plugin.id == "org.example.hello" and plugin.publisher_id == "org.example"
    # newest first, numeric (1.10.0 > 1.0.0), relative urls, real file hash
    assert [v.version for v in plugin.versions] == ["1.10.0", "1.0.0"]
    first = plugin.versions[0]
    assert first.url == "org.example.hello-1.10.0.tqplugin"
    assert first.sha256 == hashlib.sha256((pkgs / first.url).read_bytes()).hexdigest()
    assert first.size > 0 and first.sdk

    assert (
        _run(["plugins", "index", "sign", str(index_path), "--key", str(tmp_path / "k.pem")]) == 0
    )
    sig = (tmp_path / "index.json.sig").read_bytes()
    assert verify_index(raw, sig, pub) is True


def test_index_build_publisher_flags_must_come_together(tmp_path: Path, capsys) -> None:
    (tmp_path / "repo").mkdir()
    code = _run(
        [
            "plugins",
            "index",
            "build",
            str(tmp_path / "repo"),
            "--repository-id",
            "org.example.plugins",
            "--name",
            "X",
            "--publisher-id",
            "org.example",
            "--output",
            str(tmp_path / "index.json"),
        ]
    )
    assert code == 2
    assert not (tmp_path / "index.json").exists()
    assert "zusammen" in capsys.readouterr().err


def verify_package_sig(pkg: Path, pub: str) -> bool:
    from tiqora.plugins.signing import verify_signature

    with zipfile.ZipFile(pkg) as zf:
        checksums = zf.read("CHECKSUMS")
        sig = zf.read("SIGNATURE")
    verify_signature(sig, checksums, parse_public_key(pub))  # raises when invalid
    return True


def test_pack_rejects_package_the_reader_rejects(tmp_path: Path, capsys) -> None:
    log = tmp_path / "CHANGELOG.md"
    log.write_text("x" * 200_000, encoding="utf-8")
    out = tmp_path / "a.tqplugin"
    argv = ["plugins", "pack", str(build_wheel(tmp_path)), "--changelog", str(log), "-o", str(out)]
    assert _run(argv) == 1
    assert "Fehler" in capsys.readouterr().err
    assert not out.exists()
    assert not (tmp_path / "a.tqplugin.tmp").exists()


def test_sign_rejects_corrupt_package(tmp_path: Path, capsys) -> None:
    _keygen(tmp_path, capsys)
    bad = tmp_path / "bad.tqplugin"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("CHECKSUMS", "garbage")
    before = bad.read_bytes()
    argv = ["plugins", "sign", str(bad), "--key", str(tmp_path / "k.pem"), "--force"]
    assert _run(argv) == 1
    assert bad.read_bytes() == before


def test_sign_alias_path_still_needs_force(tmp_path: Path, capsys, monkeypatch) -> None:
    _keygen(tmp_path, capsys)
    pkg = tmp_path / "a.tqplugin"
    assert _run(["plugins", "pack", str(build_wheel(tmp_path)), "--output", str(pkg)]) == 0
    monkeypatch.chdir(tmp_path)
    before = pkg.read_bytes()
    argv = ["plugins", "sign", "a.tqplugin", "--key", "k.pem", "-o", str(pkg)]
    assert _run(argv) == 2
    assert pkg.read_bytes() == before


def test_index_sign_with_non_ed25519_key_exits_2(tmp_path: Path, capsys) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = tmp_path / "ec.pem"
    key.write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    sig_dir = tmp_path / "index.json"
    sig_dir.write_text("{}", encoding="utf-8")
    assert _run(["plugins", "index", "sign", str(sig_dir), "--key", str(key)]) == 2
    assert "Fehler" in capsys.readouterr().err


def test_index_sign_unwritable_sig_exits_2(tmp_path: Path, capsys) -> None:
    _keygen(tmp_path, capsys)
    idx = tmp_path / "index.json"
    idx.write_text("{}", encoding="utf-8")
    (tmp_path / "index.json.sig").mkdir()
    assert _run(["plugins", "index", "sign", str(idx), "--key", str(tmp_path / "k.pem")]) == 2
    assert "Fehler" in capsys.readouterr().err
