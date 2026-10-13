"""Runtime plugin directory (D5 filesystem part): status, materialise, activate, gc."""

from __future__ import annotations

import os
import sys
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.plugins.package_builder import build_tqplugin, build_wheel
from tiqora.config import Settings
from tiqora.plugins.discovery import discover_plugins, plugin_asset_path
from tiqora.plugins.package import PackageError, read_tqplugin, unpack_wheel
from tiqora.plugins.runtime import (
    activate_paths,
    gc_runtime_dirs,
    materialize,
    plugins_dir_status,
    reset_dir_status_cache,
    version_dir,
)

MB20 = 20 * 1024 * 1024


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    reset_dir_status_cache()
    yield
    reset_dir_status_cache()


def _settings(tmp_path: Path, *, create: bool = True, **extra: object) -> Settings:
    if create:
        (tmp_path / "plugins").mkdir(exist_ok=True)
    return Settings(_env_file=None, plugins_dir=str(tmp_path / "plugins"), **extra)  # type: ignore[arg-type]


def _package(tmp_path: Path, **kw: str) -> tuple[bytes, str, str, str]:
    data = build_tqplugin(tmp_path, build_wheel(tmp_path, **kw)).read_bytes()
    pkg = read_tqplugin(data, max_bytes=MB20)
    return data, pkg.manifest.id, pkg.manifest.version, pkg.sha256


def test_dir_status_missing_dir(tmp_path: Path) -> None:
    st = plugins_dir_status(_settings(tmp_path, create=False))
    assert not st.usable and st.reason is not None and "existiert nicht" in st.reason


def test_dir_status_writable(tmp_path: Path) -> None:
    st = plugins_dir_status(_settings(tmp_path))
    assert st.usable and st.writable and st.reason is None
    assert [p for p in (tmp_path / "plugins").iterdir()] == []  # probe cleaned up


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the directory mode")
def test_dir_status_read_only(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    d = tmp_path / "plugins"
    d.chmod(0o555)
    try:
        st = plugins_dir_status(s)
    finally:
        d.chmod(0o755)
    assert st.usable and not st.writable
    assert st.reason is not None and "schreibgeschützt" in st.reason


def test_dir_status_switched_off(tmp_path: Path) -> None:
    st = plugins_dir_status(_settings(tmp_path, plugins_runtime_install=False))
    assert not st.usable and st.reason is not None
    assert "TIQORA_PLUGINS_RUNTIME_INSTALL" in st.reason


def test_dir_status_is_cached_until_reset(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    assert plugins_dir_status(s).usable
    (tmp_path / "plugins").rmdir()
    assert plugins_dir_status(s).usable  # cached
    reset_dir_status_cache()
    assert not plugins_dir_status(s).usable


def test_materialize_then_discover_and_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _settings(tmp_path)
    data, pid, ver, sha = _package(tmp_path)
    site = materialize(s, pid, ver, sha, data)
    assert (site.parent / "COMPLETE").read_text() == sha
    assert site == version_dir(s, pid, ver, sha) / "site"
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = set(sys.modules)
    try:
        activate_paths([site])
        found = {d.plugin_id: d for d in discover_plugins()}
        assert found["org.example.hello"].problem is None
        assert plugin_asset_path(found["org.example.hello"], "i18n/en.json") is not None
    finally:
        for name in set(sys.modules) - before:
            if name.startswith("tq_fixture_hello"):
                del sys.modules[name]


def test_materialize_is_idempotent_and_survives_concurrent_winner(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    data, pid, ver, sha = _package(tmp_path)
    first = materialize(s, pid, ver, sha, data)
    again = materialize(s, pid, ver, sha, data)
    assert first == again
    assert [p.name for p in (tmp_path / "plugins" / pid).iterdir()] == [f"{ver}-{sha[:12]}"]
    # a concurrent winner: the final dir is complete already, a bad blob is never even read
    assert materialize(s, pid, ver, sha, b"not a package") == first
    assert not [p for p in (tmp_path / "plugins").iterdir() if p.name.startswith(".tmp-")]


def test_materialize_never_trusts_existing_dir_without_complete(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    data, pid, ver, sha = _package(tmp_path)
    final = version_dir(s, pid, ver, sha)
    (final / "site").mkdir(parents=True)
    planted = final / "site" / "planted.py"
    planted.write_text("evil = 1\n")
    site = materialize(s, pid, ver, sha, data)
    assert not planted.exists()
    assert (site.parent / "COMPLETE").read_text() == sha
    assert (site / "tq_fixture_hello" / "__init__.py").is_file()
    assert not [p for p in (tmp_path / "plugins").iterdir() if p.name.startswith(".tmp-")]


def test_materialize_rejects_mismatched_sha_and_leaves_nothing(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    data, pid, ver, _sha = _package(tmp_path)
    with pytest.raises(PackageError):
        materialize(s, pid, ver, "0" * 64, data)
    assert list((tmp_path / "plugins").iterdir()) == []


def test_materialize_rejects_unsafe_plugin_id(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    with pytest.raises(PackageError):
        version_dir(s, "../x", "1.0.0", "a" * 64)


def test_unpacked_files_have_fixed_modes_and_installer(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    data, pid, ver, sha = _package(tmp_path)
    site = materialize(s, pid, ver, sha, data)
    installer = next(site.glob("*.dist-info/INSTALLER"))
    assert installer.read_text() == "tiqora\n"
    for path in site.rglob("*"):
        mode = path.stat().st_mode & 0o777
        assert mode == (0o755 if path.is_dir() else 0o644), path


def test_unpack_refuses_escape_in_wheel_names(tmp_path: Path) -> None:
    evil = tmp_path / "evil.whl"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../evil.py", "x = 1\n")
        zf.writestr("pkg-1.0.dist-info/METADATA", "Name: pkg\n")
    target = tmp_path / "out" / "site"
    with pytest.raises(PackageError):
        unpack_wheel(evil.read_bytes(), target)
    assert not (tmp_path / "out" / "evil.py").exists()
    assert not (tmp_path / "evil.py").exists()
    assert not target.exists() or list(target.rglob("*")) == []


def test_activate_paths_appends_never_inserts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", ["/a", "/b"])
    activate_paths([tmp_path / "x"])
    activate_paths([tmp_path / "x"])
    assert sys.path == ["/a", "/b", str(tmp_path / "x")]


def test_gc_keeps_current_and_previous(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    root = tmp_path / "plugins"
    shas = ["a" * 64, "b" * 64, "c" * 64]
    for i, sha in enumerate(shas):
        (root / "org.example.hello" / f"1.{i}.0-{sha[:12]}" / "site").mkdir(parents=True)
    (root / "org.example.gone" / f"1.0.0-{'d' * 12}" / "site").mkdir(parents=True)
    old_tmp = root / ".tmp-old"
    old_tmp.mkdir()
    old = time.time() - 7200
    os.utime(old_tmp, (old, old))
    fresh_tmp = root / ".tmp-fresh"
    fresh_tmp.mkdir()

    removed = gc_runtime_dirs(s, {"org.example.hello": [shas[1], shas[2]]})

    left = sorted(p.name for p in (root / "org.example.hello").iterdir())
    assert left == [f"1.1.0-{shas[1][:12]}", f"1.2.0-{shas[2][:12]}"]
    assert not (root / "org.example.gone").exists()
    assert not old_tmp.exists() and fresh_tmp.exists()
    assert removed == 3


def test_gc_does_nothing_when_not_writable(tmp_path: Path) -> None:
    s = _settings(tmp_path, plugins_runtime_install=False)
    (tmp_path / "plugins" / "org.example.gone" / f"1.0.0-{'d' * 12}").mkdir(parents=True)
    assert gc_runtime_dirs(s, {}) == 0
    assert (tmp_path / "plugins" / "org.example.gone").exists()


def test_materialize_keeps_winner_that_completes_mid_unpack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.plugins import runtime

    s = _settings(tmp_path)
    data, pid, ver, sha = _package(tmp_path)
    final = version_dir(s, pid, ver, sha)
    real = runtime.unpack_wheel

    def racing(wheel_bytes: bytes, target: Path) -> None:
        real(wheel_bytes, target)
        (final / "site").mkdir(parents=True)  # the other process finishes meanwhile
        (final / "site" / "WINNER").write_text("w")
        (final / "COMPLETE").write_text(sha)

    monkeypatch.setattr(runtime, "unpack_wheel", racing)
    assert materialize(s, pid, ver, sha, data) == final / "site"
    assert (final / "site" / "WINNER").is_file()
    assert not [p for p in (tmp_path / "plugins").iterdir() if p.name.startswith(".tmp-")]


def _raw_wheel(tmp_path: Path, members: dict[str, str]) -> bytes:
    path = tmp_path / "raw.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path.read_bytes()


def test_unpack_refuses_file_directory_collision_in_german(tmp_path: Path) -> None:
    wheel = _raw_wheel(
        tmp_path,
        {"pkg/zz": "x", "pkg/zz/b.py": "y", "pkg-1.0.dist-info/METADATA": "Name: pkg\n"},
    )
    with pytest.raises(PackageError, match="Datei und Verzeichnis") as exc:
        unpack_wheel(wheel, tmp_path / "out" / "site")
    assert str(tmp_path) not in str(exc.value)
    assert not (tmp_path / "out" / "site").exists()


def test_unpack_wraps_write_errors_without_path(tmp_path: Path) -> None:
    wheel = _raw_wheel(tmp_path, {"pkg/a.py": "x", "pkg-1.0.dist-info/METADATA": "Name: pkg\n"})
    target = tmp_path / "out" / "site"
    (target / "pkg").mkdir(parents=True)
    (target / "pkg" / "a.py").write_text("exists")  # O_EXCL -> FileExistsError
    with pytest.raises(PackageError) as exc:
        unpack_wheel(wheel, target)
    assert str(tmp_path) not in str(exc.value)


@pytest.mark.parametrize(
    ("members", "needle"),
    [
        (
            {"pkg/a.py": "x", "pkg.egg-info/PKG-INFO": "x", "pkg-1.0.dist-info/M": "x"},
            "nicht erlaubt",
        ),
        ({"pkg/a.py": "x", "pkg-1.0.data/x.py": "x", "pkg-1.0.dist-info/M": "x"}, "nicht erlaubt"),
        ({"pkg/a.so": "x", "pkg-1.0.dist-info/M": "x"}, "native"),
        ({"evil.pth": "x", "pkg-1.0.dist-info/M": "x"}, "außerhalb"),
        ({"pkg/a.py": "x"}, "dist-info"),
    ],
)
def test_unpack_wheel_own_rejections(tmp_path: Path, members: dict[str, str], needle: str) -> None:
    with pytest.raises(PackageError, match=needle):
        unpack_wheel(_raw_wheel(tmp_path, members), tmp_path / "out" / "site")
