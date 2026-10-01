"""Agent article view: attached PGP public keys and the PGPexch.htm preview.

Route functions are called directly with the ticket/permission/config lookups
stubbed; gpg is real (throwaway homes directly under /tmp — gpg-agent socket
path limit on macOS, see test_crypto_pgp.py).
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import HTTPException

from tiqora.api.v1 import tickets as tickets_api
from tiqora.api.v1 import tickets_crypto as tc
from tiqora.crypto.config import CryptoConfig, PgpConfig, SmimeConfig
from tiqora.domain.ticket_service import TicketAccessDenied
from tiqora.storage.backend import AttachmentContent, AttachmentMeta

TICKET, ARTICLE, ATT = 10, 20, 30


@dataclass
class _User:
    id: int = 7


@dataclass
class _Session:
    added: list[Any] = field(default_factory=list)
    commits: int = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


def _stub_attachment(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    content: bytes,
    *,
    filename: str = "public_key.asc",
    content_type: str = "application/pgp-keys",
    deny: bool = False,
) -> None:
    meta = AttachmentMeta(
        id=ATT,
        article_id=ARTICLE,
        filename=filename,
        content_type=content_type,
        content_size=str(len(content)),
        content_id=None,
        content_alternative=None,
        disposition="attachment",
    )

    class _Svc:
        def __init__(self, _session: Any) -> None:
            pass

        async def get_attachment(self, *_args: Any) -> AttachmentContent:
            if deny:
                raise TicketAccessDenied(TICKET)
            return AttachmentContent(meta=meta, content=content)

    monkeypatch.setattr(module, "TicketService", _Svc)


# ------------------------------------------------------------------ PGP key


gnupg = pytest.importorskip("gnupg")
needs_gpg = pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH")


@pytest.fixture(scope="module")
def armored_key() -> Iterator[tuple[str, str]]:
    if shutil.which("gpg") is None:
        pytest.skip("gpg binary not on PATH")
    with tempfile.TemporaryDirectory(dir="/tmp") as home:  # noqa: S108
        gpg = gnupg.GPG(gnupghome=home)
        key = gpg.gen_key(
            gpg.gen_key_input(
                name_real="Erika Beispiel",
                name_email="erika@example.org",
                key_type="RSA",
                key_length=2048,
                no_protection=True,
                expire_date="2y",
            )
        )
        assert key.fingerprint, key.stderr
        yield str(key.fingerprint), str(gpg.export_keys(str(key.fingerprint)))


@pytest.fixture
def keyring() -> Iterator[str]:
    with tempfile.TemporaryDirectory(dir="/tmp") as home:  # noqa: S108
        yield home


def _stub_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    homedir: str,
    enabled: bool = True,
    may_edit: bool = True,
    gpg_bin: str = "gpg",
) -> None:
    config = CryptoConfig(
        pgp=PgpConfig(enabled=enabled, homedir=homedir, gpg_bin=gpg_bin), smime=SmimeConfig()
    )

    async def _load(_session: Any, _settings: Any = None) -> CryptoConfig:
        return config

    class _Perm:
        def __init__(self, _session: Any) -> None:
            pass

        async def has_rw_in_any_group(self, _uid: int, groups: tuple[str, ...]) -> bool:
            assert groups == ("admin", "users")
            return may_edit

    monkeypatch.setattr(tc, "load_crypto_config", _load)
    monkeypatch.setattr(tc, "PermissionEngine", _Perm)


@needs_gpg
async def test_key_info_parses_without_importing(
    monkeypatch: pytest.MonkeyPatch, armored_key: tuple[str, str], keyring: str
) -> None:
    fp, armored = armored_key
    _stub_attachment(monkeypatch, tc, armored.encode())
    _stub_env(monkeypatch, homedir=keyring)

    out = await tc.attachment_pgp_key(TICKET, ARTICLE, ATT, _User(), _Session())  # type: ignore[arg-type]

    assert out.available is True
    assert out.can_import is True
    assert len(out.keys) == 1
    key = out.keys[0]
    assert key.fingerprint == fp
    assert key.uids == ["Erika Beispiel <erika@example.org>"]
    assert key.emails == ["erika@example.org"]
    assert key.algorithm == "RSA"
    assert key.bits == 2048
    assert key.status == "good"
    assert key.created is not None and key.expires is not None
    assert key.in_keyring is False
    # Scanning never writes to the shared keyring.
    assert gnupg.GPG(gnupghome=keyring).list_keys() == []


@needs_gpg
async def test_key_info_without_rights_or_keyring_cannot_import(
    monkeypatch: pytest.MonkeyPatch, armored_key: tuple[str, str], keyring: str
) -> None:
    _fp, armored = armored_key
    _stub_attachment(monkeypatch, tc, armored.encode())

    _stub_env(monkeypatch, homedir=keyring, may_edit=False)
    out = await tc.attachment_pgp_key(TICKET, ARTICLE, ATT, _User(), _Session())  # type: ignore[arg-type]
    assert out.available is True and out.can_import is False

    _stub_env(monkeypatch, homedir=keyring, enabled=False)
    out = await tc.attachment_pgp_key(TICKET, ARTICLE, ATT, _User(), _Session())  # type: ignore[arg-type]
    assert out.available is True and out.can_import is False

    # No keyring configured at all: details still shown.
    _stub_env(monkeypatch, homedir="")
    out = await tc.attachment_pgp_key(TICKET, ARTICLE, ATT, _User(), _Session())  # type: ignore[arg-type]
    assert out.available is True and out.can_import is False
    assert out.keys[0].in_keyring is False


async def test_key_info_degrades_without_gpg(
    monkeypatch: pytest.MonkeyPatch, armored_key: tuple[str, str], keyring: str
) -> None:
    _fp, armored = armored_key
    _stub_attachment(monkeypatch, tc, armored.encode())
    _stub_env(monkeypatch, homedir=keyring, gpg_bin="/nonexistent/gpg")

    out = await tc.attachment_pgp_key(TICKET, ARTICLE, ATT, _User(), _Session())  # type: ignore[arg-type]
    assert out.available is False
    assert out.keys == []
    assert out.problem


async def test_key_info_for_something_else(monkeypatch: pytest.MonkeyPatch, keyring: str) -> None:
    _stub_attachment(monkeypatch, tc, b"-----BEGIN PGP SIGNATURE-----\n\niQ==\n")
    _stub_env(monkeypatch, homedir=keyring)
    out = await tc.attachment_pgp_key(TICKET, ARTICLE, ATT, _User(), _Session())  # type: ignore[arg-type]
    assert out.available is False

    _stub_attachment(monkeypatch, tc, b"x", deny=True)
    with pytest.raises(HTTPException) as exc:
        await tc.attachment_pgp_key(TICKET, ARTICLE, ATT, _User(), _Session())  # type: ignore[arg-type]
    assert exc.value.status_code == 403


@needs_gpg
async def test_import_adds_key_and_audit_row(
    monkeypatch: pytest.MonkeyPatch, armored_key: tuple[str, str], keyring: str
) -> None:
    fp, armored = armored_key
    _stub_attachment(monkeypatch, tc, armored.encode())
    _stub_env(monkeypatch, homedir=keyring)
    session = _Session()

    out = await tc.import_attachment_pgp_key(TICKET, ARTICLE, ATT, _User(), session)  # type: ignore[arg-type]

    assert out.keys[0].in_keyring is True
    assert [k["fingerprint"] for k in gnupg.GPG(gnupghome=keyring).list_keys()] == [fp]
    assert session.commits == 1
    (audit,) = session.added
    assert audit.key_type == "pgp"
    assert audit.identifier == fp
    assert audit.action == "import"
    assert audit.user_id == 7
    assert audit.email == "erika@example.org"


@needs_gpg
@pytest.mark.parametrize(
    ("kwargs", "content", "status"),
    [
        ({"may_edit": False}, None, 403),
        ({"enabled": False}, None, 409),
        ({}, b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n\nlQ==\n", 422),
        ({}, b"hello", 422),
    ],
)
async def test_import_guards(
    monkeypatch: pytest.MonkeyPatch,
    armored_key: tuple[str, str],
    keyring: str,
    kwargs: dict[str, bool],
    content: bytes | None,
    status: int,
) -> None:
    _fp, armored = armored_key
    _stub_attachment(monkeypatch, tc, content if content is not None else armored.encode())
    _stub_env(monkeypatch, homedir=keyring, **kwargs)
    with pytest.raises(HTTPException) as exc:
        await tc.import_attachment_pgp_key(TICKET, ARTICLE, ATT, _User(), _Session())  # type: ignore[arg-type]
    assert exc.value.status_code == status
    assert gnupg.GPG(gnupghome=keyring).list_keys() == []


# ------------------------------------------------------------- PGPexch.htm


async def test_html_preview_is_sanitised_and_charset_aware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
        "</head><body><p>Gr\xfc\xdfe</p><script>alert(1)</script>"
        '<img src="https://tracker.example/p.gif"></body></html>'
    ).encode("cp1252")
    _stub_attachment(
        monkeypatch, tickets_api, html, filename="PGPexch.htm", content_type="text/html"
    )

    out = await tickets_api.attachment_html_preview(TICKET, ARTICLE, ATT, _User(), _Session())  # type: ignore[arg-type]

    assert out.is_html is True
    assert "Grüße" in out.body
    assert "<script" not in out.body
    assert "data-external-src" in out.body  # external images gated like the body


async def test_html_preview_rejects_non_html(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_attachment(
        monkeypatch, tickets_api, b"%PDF-1.4", filename="a.pdf", content_type="application/pdf"
    )
    with pytest.raises(HTTPException) as exc:
        await tickets_api.attachment_html_preview(TICKET, ARTICLE, ATT, _User(), _Session())  # type: ignore[arg-type]
    assert exc.value.status_code == 415


def test_decode_prefers_mime_charset_then_meta() -> None:
    raw = "é".encode("latin-1")
    assert tickets_api._decode_html_attachment(raw, "text/html; charset=iso-8859-1") == "é"
    assert tickets_api._decode_html_attachment("é".encode(), None) == "é"
    assert tickets_api._decode_html_attachment(raw, "text/html; charset=bogus") == "�"
