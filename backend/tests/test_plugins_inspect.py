"""The package report: trust, compatibility, installability and capabilities of a .tqplugin."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.plugins.package_builder import build_tqplugin, build_wheel, make_keypair
from tiqora.config import Settings
from tiqora.plugins.inspect import (
    CapabilityLine,
    InstalledPlugin,
    PackageReport,
    inspect_package,
)
from tiqora.plugins.package import PackageError
from tiqora.plugins.signing import KeyRing, PublisherKey, TrustLevel, parse_public_key

S = Settings()
S_UNSIGNED = Settings(plugins_allow_unsigned=True)
NO_RING = KeyRing(official={}, local={}, publishers=[])
HELLO = "org.example.hello"


def _ring(*, local: str | None = None, publisher: PublisherKey | None = None) -> KeyRing:
    return KeyRing(
        official={},
        local={} if local is None else {local[0]: parse_public_key(local[1])},
        publishers=[] if publisher is None else [publisher],
    )


def _installed(
    version: str, *, source: str = "upload", top: tuple[str, ...] = ("tq_fixture_hello",)
) -> dict[str, InstalledPlugin]:
    return {HELLO: InstalledPlugin(HELLO, version, source, "b" * 64, top)}


def _package(
    tmp_path: Path, package: str = "tq_fixture_hello", *, sign: bool = False, **wheel_kw: Any
) -> tuple[bytes, tuple[str, str]]:
    private, pub, kid = make_keypair()
    wheel = build_wheel(tmp_path, package, **wheel_kw)
    pkg = build_tqplugin(tmp_path, wheel, sign_with=private if sign else None)
    return pkg.read_bytes(), (kid, pub)


def _inspect(data: bytes, **kw: Any) -> PackageReport:
    args: dict[str, Any] = {
        "settings": S,
        "ring": NO_RING,
        "installed": {},
        "image_plugin_ids": frozenset(),
    }
    args.update(kw)
    return inspect_package(data, **args)


def test_report_install_signed_local(tmp_path: Path) -> None:
    data, (kid, pub) = _package(tmp_path, sign=True)
    r = _inspect(data, ring=_ring(local=(kid, pub)))
    assert (r.action, r.installable, r.trust) == ("install", True, TrustLevel.LOCAL)
    assert r.problems == [] and r.key_id == kid
    assert r.signer is not None and kid in r.signer
    assert CapabilityLine(kind="extension_point", value="core.http.routes") in r.capabilities
    assert CapabilityLine(kind="outbound_host", value="api.example.org") in r.capabilities
    assert CapabilityLine(kind="frontend_slot", value="agent.nav") in r.capabilities
    assert (r.plugin_id, r.short_id, r.version, r.installed_version) == (
        HELLO,
        "hello",
        "1.0.0",
        None,
    )
    assert r.homepage == "https://example.org/hello"
    assert r.support == "mailto:support@example.org"
    assert len(r.sha256) == 64 and r.size_bytes == len(data)


def test_report_tables_listed(tmp_path: Path) -> None:
    data, _ = _package(
        tmp_path, "tq_fixture_tables", plugin_id="org.example.tables", dist_name="tq-fixture-tables"
    )
    r = _inspect(data, settings=S_UNSIGNED)
    assert CapabilityLine(kind="table", value="tqp_tables_note") in r.capabilities
    assert r.problems == [] and r.installable
    assert r.short_id == "tables"


def test_report_unsigned_blocked_unless_allowed(tmp_path: Path) -> None:
    data, _ = _package(tmp_path)
    blocked = _inspect(data)
    assert blocked.trust is TrustLevel.UNSIGNED and not blocked.installable
    assert any("TIQORA_PLUGINS_ALLOW_UNSIGNED" in p for p in blocked.problems)
    allowed = _inspect(data, settings=S_UNSIGNED)
    assert allowed.installable and allowed.problems == []


def test_report_unknown_key_is_a_note_when_allowed(tmp_path: Path) -> None:
    data, _ = _package(tmp_path, sign=True)  # signed by a key nobody knows
    blocked = _inspect(data)
    assert not blocked.installable
    allowed = _inspect(data, settings=S_UNSIGNED)
    assert allowed.installable and allowed.trust is TrustLevel.UNSIGNED
    assert "Signiert mit einem unbekannten Schlüssel" in allowed.warnings


def test_report_invalid_signature_never_installable(tmp_path: Path) -> None:
    data, (kid, pub) = _package(tmp_path, sign=True)
    # same key id in the ring but a different public key: verification must fail
    _p, other_pub, _k = make_keypair()
    ring = KeyRing(official={}, local={kid: parse_public_key(other_pub)}, publishers=[])
    r = _inspect(data, settings=S_UNSIGNED, ring=ring)
    assert r.trust is TrustLevel.INVALID and not r.installable
    assert "Signatur ungültig" in r.problems


def test_trust_publisher_key_from_verified_index_only(tmp_path: Path) -> None:
    private, pub, kid = make_keypair()
    wheel = build_wheel(tmp_path)
    data = build_tqplugin(tmp_path, wheel, sign_with=private).read_bytes()
    key = PublisherKey(
        key_id=kid,
        public_key=parse_public_key(pub),
        publisher_id="org.example",
        publisher_name="Example GmbH",
        repository_id=1,
        revoked=False,
    )
    verified = _inspect(data, ring=_ring(publisher=key))
    assert verified.trust is TrustLevel.VERIFIED_PUBLISHER
    assert verified.signer == "Example GmbH" and verified.installable
    unverified = _inspect(data, ring=NO_RING)  # index not verified -> ring without its keys
    assert unverified.trust is TrustLevel.UNSIGNED and not unverified.installable


def test_revoked_publisher_key_is_invalid(tmp_path: Path) -> None:
    private, pub, kid = make_keypair()
    data = build_tqplugin(tmp_path, build_wheel(tmp_path), sign_with=private).read_bytes()
    key = PublisherKey(kid, parse_public_key(pub), "org.example", "Example GmbH", 1, True)
    r = _inspect(data, settings=S_UNSIGNED, ring=_ring(publisher=key))
    assert r.trust is TrustLevel.INVALID and not r.installable
    assert any("zurückgezogen" in p for p in r.problems)


def test_image_plugin_not_installable(tmp_path: Path) -> None:
    data, _ = _package(tmp_path)
    r = _inspect(
        data,
        settings=S_UNSIGNED,
        installed=_installed("1.0.0", source="image"),
        image_plugin_ids=frozenset({HELLO}),
    )
    assert r.action == "blocked_image" and not r.installable
    assert any("ist im Image eingebaut" in p for p in r.problems)


def test_update_reinstall_downgrade(tmp_path: Path) -> None:
    data, _ = _package(tmp_path)
    update = _inspect(data, settings=S_UNSIGNED, installed=_installed("0.9.0"))
    assert (update.action, update.installed_version, update.installable) == (
        "update",
        "0.9.0",
        True,
    )
    re = _inspect(data, settings=S_UNSIGNED, installed=_installed("1.0.0"))
    assert (re.action, re.installable) == ("reinstall", True)
    down = _inspect(data, settings=S_UNSIGNED, installed=_installed("1.1.0"))
    assert down.action == "downgrade" and not down.installable
    assert any("älter" in p for p in down.problems)


def test_own_dist_only_from_the_installed_runtime_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tiqora.plugins.inspect as mod

    seen: list[str | None] = []
    real = mod.collision_problems

    def spy(top: list[str], taken: Any = None, *, own_dist: str | None = None) -> list[str]:
        seen.append(own_dist)
        return real(top, taken, own_dist=own_dist)

    monkeypatch.setattr(mod, "collision_problems", spy)
    data, _ = _package(tmp_path)
    _inspect(data, settings=S_UNSIGNED, installed=_installed("0.9.0"))
    _inspect(data, settings=S_UNSIGNED)  # not installed
    _inspect(data, settings=S_UNSIGNED, installed=_installed("0.9.0", source="image"))
    _inspect(data, settings=S_UNSIGNED, installed=_installed("0.9.0", top=("other_pkg",)))
    assert seen == ["tq_fixture_hello", None, None, None]


def test_module_name_collision_is_blocking(tmp_path: Path) -> None:
    data, _ = _package(tmp_path, "tq_fixture_hello", extra_members={"json/__init__.py": b""})
    r = _inspect(data, settings=S_UNSIGNED)
    assert not r.installable
    assert any("„json“" in p for p in r.problems)


def test_report_requirements_problem_and_hint(tmp_path: Path) -> None:
    data, _ = _package(tmp_path, requires_dist=["tq-surely-not-installed>=2"])
    r = _inspect(data, settings=S_UNSIGNED)
    assert not r.installable
    assert any("„tq-surely-not-installed“" in p for p in r.problems)
    assert any("tiqora plugins bake" in p for p in r.problems)


def test_report_incompatible_sdk(tmp_path: Path) -> None:
    data, _ = _package(tmp_path, manifest_overrides={"sdk": ">=9"})
    r = _inspect(data, settings=S_UNSIGNED)
    assert not r.installable
    assert any("SDK >=9" in p for p in r.problems)


def test_report_warnings_not_blocking(tmp_path: Path) -> None:
    sql = b'SQL = "UPDATE ticket SET title = 1"\n'
    data, _ = _package(tmp_path, extra_members={"tq_fixture_hello/raw_sql.py": sql})
    r = _inspect(data, settings=S_UNSIGNED)
    assert r.installable and r.problems == []
    assert any("Znuny-Tabelle „ticket“" in w for w in r.warnings)


def test_unprefixed_migration_table_blocks(tmp_path: Path) -> None:
    migration = (
        b"import sqlalchemy as sa\nfrom alembic import op\nrevision = 'x1'\ndown_revision = None\n"
        b"def upgrade():\n    op.create_table('evil', sa.Column('id', sa.Integer()))\n"
    )
    data, _ = _package(
        tmp_path,
        "tq_fixture_tables",
        plugin_id="org.example.tables",
        dist_name="tq-fixture-tables",
        extra_members={"tq_fixture_tables/migrations/0001_init.py": migration},
    )
    r = _inspect(data, settings=S_UNSIGNED)
    assert not r.installable
    assert any("„evil“" in p and "tqp_tables_" in p for p in r.problems)


def test_short_id_taken_by_another_plugin_blocks(tmp_path: Path) -> None:
    data, _ = _package(tmp_path)
    other = _inspect(data, settings=S_UNSIGNED, short_id_owners={"hello": "org.other.hello"})
    assert not other.installable
    assert "Kurzname „hello“ wird schon von org.other.hello verwendet" in other.problems
    same = _inspect(data, settings=S_UNSIGNED, short_id_owners={"hello": HELLO})
    assert same.installable


def test_unmet_plugin_requirement_blocks(tmp_path: Path) -> None:
    data, _ = _package(tmp_path, manifest_overrides={"requires": {"org.example.base": ">=2"}})
    missing = _inspect(data, settings=S_UNSIGNED)
    assert any("„org.example.base“" in p and "nicht installiert" in p for p in missing.problems)
    old = {"org.example.base": InstalledPlugin("org.example.base", "1.0.0", "upload", None, ())}
    wrong = _inspect(data, settings=S_UNSIGNED, installed=old)
    assert any("„org.example.base“" in p and "1.0.0" in p for p in wrong.problems)
    new = {"org.example.base": InstalledPlugin("org.example.base", "2.1.0", "upload", None, ())}
    assert _inspect(data, settings=S_UNSIGNED, installed=new).installable


def test_report_is_json_roundtrippable(tmp_path: Path) -> None:
    data, _ = _package(tmp_path, sign=True)
    r = _inspect(data, settings=S_UNSIGNED)
    assert PackageReport.model_validate_json(r.model_dump_json()) == r


def test_unreadable_package_raises(tmp_path: Path) -> None:
    with pytest.raises(PackageError):
        _inspect(b"not a zip")
