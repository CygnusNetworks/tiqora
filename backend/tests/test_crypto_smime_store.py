"""Znuny-layout S/MIME store (tiqora/crypto/smime_store.py) against real openssl.

Filenames must be exactly what Znuny's SMIME.pm writes — ``openssl x509
-subject_hash`` + ``.n`` collision index, private key under the same name in
the private dir with the secret in ``.P`` — so the directories can be shared.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests._smime_fixtures import make_ca, make_leaf
from tiqora.crypto import CryptoUnavailableError
from tiqora.crypto.smime_store import SmimeStore, SmimeStoreError, validate_filename

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl binary not on PATH"
)


@pytest.fixture
def store(tmp_path: Path) -> SmimeStore:
    return SmimeStore(str(tmp_path / "certs"), str(tmp_path / "private"))


def _openssl_hash(pem: bytes) -> str:
    out = subprocess.run(
        ["openssl", "x509", "-noout", "-subject_hash"], input=pem, capture_output=True, check=True
    )
    return out.stdout.decode().strip()


def test_add_certificate_uses_openssl_subject_hash(store: SmimeStore) -> None:
    leaf = make_leaf("agent@example.org")
    entry = store.add_certificate(leaf.cert_pem)
    assert entry.filename == f"{_openssl_hash(leaf.cert_pem)}.0"
    assert (store.cert_dir / entry.filename).read_bytes() == leaf.cert_pem
    info = entry.info
    assert info is not None
    assert info.emails == ["agent@example.org"]
    assert info.email_joined == "agent@example.org"
    assert len(info.fingerprint) == 59 and info.fingerprint.count(":") == 19
    assert info.is_ca is False
    assert info.is_expired() is False
    assert info.subject == "CN=agent@example.org, emailAddress=agent@example.org"
    # Znuny's smime_keys.subject formatting ("=" → "= ").
    assert info.znuny_subject.startswith("CN= agent@example.org")


def test_der_certificate_is_stored_as_pem(store: SmimeStore) -> None:
    from cryptography.hazmat.primitives import serialization

    leaf = make_leaf("der@example.org")
    der = leaf.cert.public_bytes(serialization.Encoding.DER)
    entry = store.add_certificate(der)
    assert (store.cert_dir / entry.filename).read_bytes() == leaf.cert_pem


def test_same_subject_gets_collision_index(store: SmimeStore) -> None:
    first = make_leaf("same@example.org")
    second = make_leaf("same@example.org")  # same DN, different key
    a = store.add_certificate(first.cert_pem)
    b = store.add_certificate(second.cert_pem)
    h = _openssl_hash(first.cert_pem)
    assert (a.filename, b.filename) == (f"{h}.0", f"{h}.1")


def test_duplicate_certificate_is_refused(store: SmimeStore) -> None:
    leaf = make_leaf("dup@example.org")
    store.add_certificate(leaf.cert_pem)
    with pytest.raises(SmimeStoreError, match="already installed"):
        store.add_certificate(leaf.cert_pem)


def test_invalid_certificate_is_refused(store: SmimeStore) -> None:
    with pytest.raises(SmimeStoreError):
        store.add_certificate(b"-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----\n")


def test_private_key_with_secret_lands_next_to_cert(store: SmimeStore) -> None:
    leaf = make_leaf("key@example.org")
    cert = store.add_certificate(leaf.cert_pem)
    entry, generated = store.add_private_key(leaf.encrypted_key("geheim"), "geheim")
    assert generated is False
    assert entry.filename == cert.filename
    assert entry.has_private is True
    key_file = store.private_dir / cert.filename  # type: ignore[operator]
    secret_file = key_file.with_name(key_file.name + ".P")
    assert secret_file.read_text() == "geheim"
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(secret_file).st_mode) == 0o600
    got = store.get_private(cert.filename)
    assert got is not None and got[1] == "geheim"
    assert store.list_entries()[0].has_private is True


def test_unencrypted_key_without_secret_gets_generated_secret(store: SmimeStore) -> None:
    leaf = make_leaf("plain@example.org")
    cert = store.add_certificate(leaf.cert_pem)
    entry, generated = store.add_private_key(leaf.key_pem, "")
    assert generated is True
    key_pem, secret = store.get_private(cert.filename) or (b"", "")
    assert secret and b"ENCRYPTED PRIVATE KEY" in key_pem
    # The stored key opens with the stored secret (what Znuny will do).
    proc = subprocess.run(
        ["openssl", "pkey", "-noout", "-passin", f"pass:{secret}"],
        input=key_pem,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert entry.has_private


def test_encrypted_key_without_secret_is_refused(store: SmimeStore) -> None:
    leaf = make_leaf("enc@example.org")
    store.add_certificate(leaf.cert_pem)
    with pytest.raises(SmimeStoreError, match="secret is required"):
        store.add_private_key(leaf.encrypted_key("x"), "")


def test_wrong_secret_is_refused(store: SmimeStore) -> None:
    leaf = make_leaf("wrong@example.org")
    store.add_certificate(leaf.cert_pem)
    with pytest.raises(SmimeStoreError, match="wrong secret"):
        store.add_private_key(leaf.encrypted_key("right"), "wrong")


def test_private_key_without_certificate_is_refused(store: SmimeStore) -> None:
    leaf = make_leaf("orphan@example.org")
    with pytest.raises(SmimeStoreError, match="upload the certificate first"):
        store.add_private_key(leaf.key_pem, "s")


def test_remove_certificate_removes_private_and_compacts(store: SmimeStore) -> None:
    first = make_leaf("c@example.org")
    second = make_leaf("c@example.org")
    third = make_leaf("c@example.org")
    a = store.add_certificate(first.cert_pem)
    store.add_certificate(second.cert_pem)
    c = store.add_certificate(third.cert_pem)
    store.add_private_key(third.key_pem, "")
    store.add_private_key(first.key_pem, "")
    h = a.filename.split(".")[0]

    renames = store.remove_certificate(a.filename)
    # .1 → .0 and .2 → .1: no gap left for OpenSSL's -CApath lookup.
    assert renames == {f"{h}.1": f"{h}.0", f"{h}.2": f"{h}.1"}
    names = sorted(e.filename for e in store.list_entries())
    assert names == [f"{h}.0", f"{h}.1"]
    assert c.filename == f"{h}.2"
    moved = store.entry(f"{h}.1")
    assert moved.info is not None and moved.info.public_key_der == c.info.public_key_der  # type: ignore[union-attr]
    assert moved.has_private is True
    assert (store.private_dir / f"{h}.1.P").is_file()  # type: ignore[operator]
    assert not (store.private_dir / f"{h}.2").exists()  # type: ignore[operator]


def test_remove_private_only(store: SmimeStore) -> None:
    leaf = make_leaf("rp@example.org")
    cert = store.add_certificate(leaf.cert_pem)
    store.add_private_key(leaf.key_pem, "")
    assert store.remove_private(cert.filename) is True
    assert store.remove_private(cert.filename) is False
    assert store.entry(cert.filename).has_private is False


def test_search_by_email_private_and_valid(store: SmimeStore) -> None:
    now = datetime.now(UTC)
    valid = make_leaf("q@example.org")
    expired = make_leaf(
        "q@example.org",
        common_name="old q",
        not_before=now - timedelta(days=60),
        not_after=now - timedelta(days=1),
    )
    v = store.add_certificate(valid.cert_pem)
    e = store.add_certificate(expired.cert_pem)
    store.add_private_key(valid.key_pem, "")
    assert {x.filename for x in store.search("Q@example.org")} == {v.filename, e.filename}
    assert [x.filename for x in store.search("q@example.org", private=True)] == [v.filename]
    assert [x.filename for x in store.search("q@example.org", valid_only=True)] == [v.filename]
    assert store.entry(e.filename).info.is_expired()  # type: ignore[union-attr]


def test_find_by_fingerprint_and_ca_flag(store: SmimeStore) -> None:
    ca = make_ca()
    leaf = make_leaf("signed@example.org", ca=ca)
    ca_entry = store.add_certificate(ca.cert_pem)
    store.add_certificate(leaf.cert_pem)
    assert ca_entry.info is not None and ca_entry.info.is_ca is True
    found = store.find_by_fingerprint(ca_entry.info.fingerprint.replace(":", "").lower())
    assert found is not None and found.filename == ca_entry.filename


def test_invalid_file_is_listed_without_info(store: SmimeStore) -> None:
    store.cert_dir.mkdir(parents=True)
    (store.cert_dir / "deadbeef.0").write_text("garbage")
    (store.cert_dir / "README").write_text("ignored")
    (entry,) = store.list_entries()
    assert entry.filename == "deadbeef.0"
    assert entry.info is None


def test_rehash_renames_misnamed_files(store: SmimeStore) -> None:
    leaf = make_leaf("hash@example.org")
    store.cert_dir.mkdir(parents=True)
    store.private_dir.mkdir(parents=True)  # type: ignore[union-attr]
    (store.cert_dir / "00000000.0").write_bytes(leaf.cert_pem)
    (store.private_dir / "00000000.0").write_bytes(leaf.key_pem)  # type: ignore[operator]
    (store.private_dir / "00000000.0.P").write_text("x")  # type: ignore[operator]
    renames = store.rehash()
    h = _openssl_hash(leaf.cert_pem)
    assert renames == {"00000000.0": f"{h}.0"}
    assert (store.private_dir / f"{h}.0.P").read_text() == "x"  # type: ignore[operator]


@pytest.mark.parametrize("bad", ["../etc/passwd", "abc.0", "deadbeef.100", "deadbeef.0.P", ""])
def test_filename_validation_blocks_traversal(bad: str) -> None:
    with pytest.raises(SmimeStoreError):
        validate_filename(bad)


def test_unconfigured_store_is_unavailable() -> None:
    with pytest.raises(CryptoUnavailableError):
        SmimeStore("", "")
