"""Avatar endpoints: read proxy, uploads, company logos, admin switches,
vCard import and GDPR erasure. Own prefix ``avatar.`` / ``AVATAR-``; every
test cleans up what it commits.

Outbound HTTP runs on a ``MockTransport`` injected through the
``get_avatar_service`` dependency, Redis is an in-memory fake.
"""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from PIL import Image
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.avatars.normalize import AVATAR_PX
from tiqora.avatars.service import (
    MAX_UPLOAD_BYTES,
    AvatarService,
    cache_key,
    email_key,
)
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraAvatar, TiqoraSettings
from tiqora.domain.settings_store import (
    KEY_AVATARS_FAVICON_ENABLED,
    KEY_AVATARS_GRAVATAR_ENABLED,
)

pytestmark = pytest.mark.db

ROOT = 1  # admin: has every feature grant
OUTSIDER = 9875  # no users row, no grant
PREFIX = "avatar."
ANNA = PREFIX + "anna"
BERT = PREFIX + "bert"
NOMAIL = PREFIX + "nomail"
ANNA_EMAIL = "Anna.Avatar@avatar-test.example"
BERT_EMAIL = "bert@avatar-test.example"
COMPANY = "AVATAR-CO"
COMPANY_NO_URL = "AVATAR-NOURL"
DOMAIN = "avatar-test.example"
PUBLIC_IP = "93.184.216.34"
_SETTING_KEYS = (KEY_AVATARS_GRAVATAR_ENABLED, KEY_AVATARS_FAVICON_ENABLED)


def _async_url(url: str) -> str:
    return (
        url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _cleanup(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(delete(TiqoraAvatar))
        conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.in_(_SETTING_KEYS)))
        # Written by customer edits / GDPR jobs in this module only.
        for table in (
            "tiqora_cache_invalidation",
            "tiqora_gdpr_backup",
            "tiqora_gdpr_job",
            "tiqora_gdpr_audit",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("DELETE FROM customer_user WHERE login LIKE :p"), {"p": PREFIX + "%"})
        conn.execute(
            text("DELETE FROM customer_user WHERE customer_id LIKE 'AVATAR-%'"),
        )
        conn.execute(text("DELETE FROM customer_company WHERE customer_id LIKE 'AVATAR-%'"))
    engine.dispose()


def _seed(url: str) -> None:
    _cleanup(url)
    engine = create_engine(url)
    t = "2026-01-01 12:00:00"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO customer_company (customer_id, name, url, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:a, 'Avatar Test GmbH', 'https://www.avatar-test.example/', 1,"
                " :t, 1, :t, 1), (:b, 'Avatar Ohne Web', NULL, 1, :t, 1, :t, 1)"
            ),
            {"a": COMPANY, "b": COMPANY_NO_URL, "t": t},
        )
        for login, email, first, last in (
            (ANNA, ANNA_EMAIL, "", "Avatar"),
            (BERT, BERT_EMAIL, "Bert", "Beispiel"),
            (NOMAIL, "", "Nora", "Ohnemail"),
        ):
            conn.execute(
                text(
                    "INSERT INTO customer_user (login, email, customer_id, first_name,"
                    " last_name, valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:login, :email, :cid, :first, :last, 1, :t, 1, :t, 1)"
                ),
                {
                    "login": login,
                    "email": email,
                    "cid": COMPANY,
                    "first": first,
                    "last": last,
                    "t": t,
                },
            )
    engine.dispose()


@pytest.fixture
def seeded(mariadb_znuny_url: str) -> Iterator[str]:
    _seed(mariadb_znuny_url)
    yield mariadb_znuny_url
    _cleanup(mariadb_znuny_url)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def seeded_any(request: pytest.FixtureRequest) -> Iterator[str]:
    url = request.getfixturevalue(request.param)
    _seed(url)
    yield url
    _cleanup(url)


def _jpeg(size: tuple[int, int] = (300, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (30, 90, 160)).save(out, format="JPEG")
    return out.getvalue()


def _png(size: int = 256) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (size, size), (200, 10, 10)).save(out, format="PNG")
    return out.getvalue()


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del ex
        self.data[key] = value

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.data.pop(key, None)


class _Outbound:
    """Pretend internet: Gravatar knows BERT; nothing else answers."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        # Request session of the avatar being resolved; outbound calls record
        # whether it still holds a DB transaction (= a pooled connection).
        self.session: AsyncSession | None = None
        self.in_transaction: list[bool] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        self.seen.append(f"{host}{request.url.path}")
        if self.session is not None:
            self.in_transaction.append(self.session.in_transaction())
        if host == "gravatar.com" and request.url.path == f"/avatar/{email_key(BERT_EMAIL)}":
            return httpx.Response(200, content=_png(), headers={"content-type": "image/png"})
        return httpx.Response(404)


class _Harness:
    def __init__(self, url: str, user_id: int) -> None:
        from fastapi import Depends
        from httpx import ASGITransport, AsyncClient

        from tiqora.api.app import create_app
        from tiqora.api.deps import get_current_user, get_db
        from tiqora.api.v1.avatars import get_avatar_service
        from tiqora.domain.auth import AuthenticatedUser

        self.engine = create_async_engine(_async_url(url))
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.redis = _FakeRedis()
        self.outbound = _Outbound()
        factory = self.factory

        async def _db() -> Any:
            async with factory() as session:
                yield session

        async def _service(session: AsyncSession = Depends(get_db)) -> AvatarService:  # noqa: B008
            self.outbound.session = session
            return AvatarService(
                session,
                self.redis,
                transport=httpx.MockTransport(self.outbound),
                resolver=lambda _host, _port: [PUBLIC_IP],
            )

        user = AuthenticatedUser(
            id=user_id, login=f"u{user_id}", first_name="A", last_name="B", auth_method="session"
        )
        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_avatar_service] = _service
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def __aenter__(self) -> _Harness:
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.client.__aexit__(*exc)
        await self.engine.dispose()

    async def upload(self, path: str, data: bytes, name: str = "a.jpg") -> httpx.Response:
        return await self.client.put(path, files={"file": (name, data, "image/jpeg")})

    async def avatar_rows(self) -> list[tuple[str, str, str | None]]:
        async with self.factory() as session:
            rows = await session.execute(
                select(TiqoraAvatar.kind, TiqoraAvatar.subject_key, TiqoraAvatar.owner_ref)
            )
            return [tuple(r) for r in rows.all()]  # type: ignore[misc]


async def test_upload_serve_delete_roundtrip(seeded_any: str) -> None:
    sha = email_key(ANNA_EMAIL)
    async with _Harness(seeded_any, ROOT) as h:
        h.redis.data[cache_key("email", sha)] = ""  # a cached "no picture"
        first = await h.upload(f"/api/v1/customers/{ANNA}/avatar", _jpeg())
        second = await h.upload(f"/api/v1/customers/{ANNA}/avatar", _jpeg((100, 400)))
        hit = await h.client.get(f"/api/v1/avatars/email/{sha}", params={"v": "1"})
        rows = await h.avatar_rows()
        deleted = await h.client.delete(f"/api/v1/customers/{ANNA}/avatar")
        miss = await h.client.get(f"/api/v1/avatars/email/{sha}")
        rows_after = await h.avatar_rows()
    assert first.status_code == 200, first.text
    assert first.json()["version"].isdigit()
    assert second.json()["version"] != first.json()["version"]
    # Upload invalidated the cached miss.
    assert hit.status_code == 200
    assert hit.headers["content-type"] == "image/png"
    assert hit.headers["content-security-policy"] == "sandbox"
    assert hit.headers["x-content-type-options"] == "nosniff"
    assert hit.headers["cache-control"] == "private, max-age=3600"
    assert Image.open(io.BytesIO(hit.content)).size == (AVATAR_PX, AVATAR_PX)
    assert rows == [("email", sha, ANNA)]
    assert deleted.status_code == 204
    # No upload any more → Gravatar (404 for Anna) → 404 with the short cache.
    assert miss.status_code == 404
    assert miss.headers["cache-control"] == "private, max-age=600"
    assert miss.headers["content-security-policy"] == "sandbox"
    assert rows_after == []


async def test_gravatar_through_proxy_and_admin_switch(seeded: str) -> None:
    sha = email_key(BERT_EMAIL)
    async with _Harness(seeded, ROOT) as h:
        defaults = await h.client.get("/api/v1/admin/avatars")
        hit = await h.client.get(f"/api/v1/avatars/email/{sha}")
        cached = await h.client.get(f"/api/v1/avatars/email/{sha}")
        fetches_before = len(h.outbound.seen)
        off = await h.client.put(
            "/api/v1/admin/avatars", json={"gravatar_enabled": False, "favicon_enabled": True}
        )
        h.redis.data.clear()
        disabled = await h.client.get(f"/api/v1/avatars/email/{sha}")
        fetches_after = len(h.outbound.seen)
    async with _Harness(seeded, OUTSIDER) as h:
        forbidden = await h.client.get("/api/v1/admin/avatars")
    assert defaults.json() == {"gravatar_enabled": True, "favicon_enabled": True}
    assert hit.status_code == 200 and cached.status_code == 200
    assert fetches_before == 1  # second read came from the cache
    assert off.json() == {"gravatar_enabled": False, "favicon_enabled": True}
    assert disabled.status_code == 404
    assert fetches_after == fetches_before
    assert forbidden.status_code == 403


async def test_outbound_fetch_releases_db_connection(seeded: str) -> None:
    # A list of uncached avatars must not hold one pooled connection per fetch.
    async with _Harness(seeded, ROOT) as h:
        hit = await h.client.get(f"/api/v1/avatars/email/{email_key(BERT_EMAIL)}")
        logo = await h.client.get("/api/v1/avatars/domain/example.org")
        in_transaction = list(h.outbound.in_transaction)
    assert hit.status_code == 200
    assert logo.status_code == 404
    assert in_transaction and not any(in_transaction)


async def test_read_validation_and_freemail(seeded: str) -> None:
    async with _Harness(seeded, ROOT) as h:
        bad_hash = await h.client.get("/api/v1/avatars/email/not-a-hash")
        upper = await h.client.get(f"/api/v1/avatars/email/{email_key(ANNA_EMAIL).upper()}")
        bad_domain = await h.client.get("/api/v1/avatars/domain/localhost")
        ip_domain = await h.client.get("/api/v1/avatars/domain/10.0.0.1")
        freemail = await h.client.get("/api/v1/avatars/domain/gmail.com")
        seen = list(h.outbound.seen)
    assert [r.status_code for r in (bad_hash, upper, bad_domain, ip_domain)] == [422] * 4
    assert freemail.status_code == 404
    assert freemail.headers["cache-control"] == "private, max-age=600"
    assert seen == []


async def test_upload_rejections(seeded: str) -> None:
    gif = io.BytesIO()
    Image.new("RGB", (64, 64)).save(gif, format="GIF")
    async with _Harness(seeded, ROOT) as h:
        bad_format = await h.upload(f"/api/v1/customers/{ANNA}/avatar", gif.getvalue(), "a.gif")
        svg = await h.upload(f"/api/v1/customers/{ANNA}/avatar", b"<svg/>", "a.svg")
        too_large = await h.upload(
            f"/api/v1/customers/{ANNA}/avatar", b"\xff\xd8\xff" + b"0" * MAX_UPLOAD_BYTES
        )
        no_email = await h.upload(f"/api/v1/customers/{NOMAIL}/avatar", _jpeg())
        unknown = await h.upload(f"/api/v1/customers/{PREFIX}nobody/avatar", _jpeg())
        rows = await h.avatar_rows()
    async with _Harness(seeded, OUTSIDER) as h:
        denied = [
            await h.upload(f"/api/v1/customers/{ANNA}/avatar", _jpeg()),
            await h.client.delete(f"/api/v1/customers/{ANNA}/avatar"),
            await h.upload(f"/api/v1/customers/companies/{COMPANY}/logo", _jpeg()),
            await h.client.delete(f"/api/v1/customers/companies/{COMPANY}/logo"),
            await h.client.post(
                "/api/v1/customers/vcard-import/preview",
                files={"file": ("a.vcf", b"BEGIN:VCARD\nEND:VCARD\n", "text/vcard")},
            ),
        ]
        # Reading stays open to every agent.
        read = await h.client.get(f"/api/v1/avatars/email/{email_key(ANNA_EMAIL)}")
    assert bad_format.status_code == 422
    assert svg.status_code == 422
    assert too_large.status_code == 413
    assert no_email.status_code == 422
    assert unknown.status_code == 404
    assert rows == []
    assert [r.status_code for r in denied] == [403] * 5
    assert read.status_code == 404


async def test_company_logo(seeded: str) -> None:
    async with _Harness(seeded, ROOT) as h:
        uploaded = await h.upload(f"/api/v1/customers/companies/{COMPANY}/logo", _jpeg())
        bare = await h.client.get(f"/api/v1/avatars/domain/{DOMAIN}")
        www = await h.client.get(f"/api/v1/avatars/domain/www.{DOMAIN}")
        fetched = list(h.outbound.seen)
        rows = await h.avatar_rows()
        no_url = await h.upload(f"/api/v1/customers/companies/{COMPANY_NO_URL}/logo", _jpeg())
        missing = await h.upload("/api/v1/customers/companies/AVATAR-NONE/logo", _jpeg())
        deleted = await h.client.delete(f"/api/v1/customers/companies/{COMPANY}/logo")
        after = await h.client.get(f"/api/v1/avatars/domain/{DOMAIN}")
    assert uploaded.status_code == 200, uploaded.text
    assert bare.status_code == 200 and www.status_code == 200
    assert bare.content == www.content
    assert fetched == []  # the upload wins, no website lookup
    assert rows == [("domain", DOMAIN, COMPANY)]
    assert no_url.status_code == 422
    assert no_url.json()["detail"] == "company has no website URL"
    assert missing.status_code == 404
    assert deleted.status_code == 204
    # Without the upload the website is asked (and has nothing here).
    assert after.status_code == 404
    assert h.outbound.seen and h.outbound.seen[0] == f"{DOMAIN}/"


def _vcf(photo: bytes) -> bytes:
    b64 = base64.b64encode(photo).decode("ascii")
    folded = "\r\n ".join(b64[i : i + 74] for i in range(0, len(b64), 74))
    return (
        "BEGIN:VCARD\r\nVERSION:3.0\r\n"
        "N:Neuname;Anna;;;\r\nFN:Anna Neuname\r\n"
        "item1.EMAIL;type=INTERNET;type=pref:anna.privat@elsewhere.example\r\n"
        f"EMAIL;type=INTERNET;type=WORK:{ANNA_EMAIL.lower()}\r\n"
        "TEL;type=CELL:+49 170 0000011\r\nTEL;type=WORK:+49 228 0000012\r\n"
        f"PHOTO;ENCODING=b;TYPE=JPEG:{folded}\r\n"
        "END:VCARD\r\n"
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Unbekannt Person\r\n"
        "EMAIL:unknown@nowhere.example\r\nEND:VCARD\r\n"
    ).encode()


async def _anna_fields(h: _Harness) -> tuple[str, str, str | None, str | None]:
    async with h.factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT first_name, last_name, phone, mobile FROM customer_user"
                    " WHERE login = :l"
                ),
                {"l": ANNA},
            )
        ).one()
    return row[0], row[1], row[2], row[3]


async def test_vcard_preview_writes_nothing(seeded: str) -> None:
    vcf = _vcf(_jpeg())
    async with _Harness(seeded, ROOT) as h:
        before = await _anna_fields(h)
        resp = await h.client.post(
            "/api/v1/customers/vcard-import/preview",
            files={"file": ("kontakte.vcf", vcf, "text/vcard")},
        )
        after = await _anna_fields(h)
        rows = await h.avatar_rows()
        empty = await h.client.post(
            "/api/v1/customers/vcard-import/preview",
            files={"file": ("x.vcf", b"no cards here", "text/vcard")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["truncated"] is False
    anna, unknown = body["cards"]
    # Matched through the card's *second* address, case-insensitively.
    assert anna["match_login"] == ANNA
    assert anna["match_name"] == "Avatar"
    assert anna["fill_fields"] == ["first_name", "phone", "mobile"]
    assert anna["has_photo"] is True
    assert anna["photo_preview"].startswith("data:image/png;base64,")
    assert anna["has_existing_avatar"] is False
    assert anna["emails"] == ["anna.privat@elsewhere.example", ANNA_EMAIL.lower()]
    assert unknown["match_login"] is None and unknown["fill_fields"] == []
    assert before == after
    assert rows == []
    assert empty.status_code == 422


async def test_vcard_apply_fills_blanks_and_stores_photo(seeded: str) -> None:
    vcf = _vcf(_jpeg())
    selections = [
        {"index": 0, "login": ANNA, "photo": True, "fields": True},
        {"index": 1, "login": PREFIX + "nobody", "photo": False, "fields": True},
    ]
    async with _Harness(seeded, ROOT) as h:
        resp = await h.client.post(
            "/api/v1/customers/vcard-import/apply",
            files={"file": ("kontakte.vcf", vcf, "text/vcard")},
            data={"selections": json.dumps(selections)},
        )
        fields = await _anna_fields(h)
        rows = await h.avatar_rows()
        photo = await h.client.get(f"/api/v1/avatars/email/{email_key(ANNA_EMAIL)}")
        preview = await h.client.post(
            "/api/v1/customers/vcard-import/preview",
            files={"file": ("kontakte.vcf", vcf, "text/vcard")},
        )
        bad = await h.client.post(
            "/api/v1/customers/vcard-import/apply",
            files={"file": ("kontakte.vcf", vcf, "text/vcard")},
            data={"selections": "{not json"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "applied": [{"login": ANNA, "photo": True, "filled": ["first_name", "phone", "mobile"]}]
    }
    # Blank fields filled, the existing last name kept.
    assert fields == ("Anna", "Avatar", "+49 228 0000012", "+49 170 0000011")
    assert rows == [("email", email_key(ANNA_EMAIL), ANNA)]
    assert photo.status_code == 200
    card = preview.json()["cards"][0]
    assert card["fill_fields"] == [] and card["has_existing_avatar"] is True
    assert bad.status_code == 422


async def test_gdpr_erasure_removes_upload_and_rollback_restores(seeded_any: str) -> None:
    from tiqora.gdpr.erasure import rollback_job, run_erasure

    async with _Harness(seeded_any, ROOT) as h:
        uploaded = await h.upload(f"/api/v1/customers/{BERT}/avatar", _jpeg())
        async with h.factory() as session:
            bert_id = (
                await session.execute(
                    text("SELECT id FROM customer_user WHERE login = :l"), {"l": BERT}
                )
            ).scalar_one()
        result = await run_erasure(
            h.factory,
            Settings(),
            customer_user_ids=[int(bert_id)],
            mode="anonymize",
            seed=7,
            force_parallel=True,
            actor="test",
        )
        erased = await h.avatar_rows()
        await rollback_job(h.factory, Settings(), result.job_id, actor="test")
        restored = await h.avatar_rows()
    assert uploaded.status_code == 200
    assert result.counts["tiqora_avatar"] == 1
    assert erased == []
    assert restored == [("email", email_key(BERT_EMAIL), BERT)]


async def test_gdpr_anonymize_customer_removes_upload(seeded: str) -> None:
    from tiqora.gdpr.anonymize import anonymize_customer

    async with _Harness(seeded, ROOT) as h:
        await h.upload(f"/api/v1/customers/{ANNA}/avatar", _jpeg())
        await h.upload(f"/api/v1/customers/companies/{COMPANY}/logo", _jpeg())
        result = await anonymize_customer(
            h.factory, Settings(), login=ANNA, seed=3, force_parallel=True, actor="test"
        )
        rows = await h.avatar_rows()
    assert result.avatars == 1
    # The person's photo is gone; the company logo is not personal data.
    assert rows == [("domain", DOMAIN, COMPANY)]
