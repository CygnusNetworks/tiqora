"""CI attachments: staged uploads, binding, access, deletion, filename and header hygiene."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_RO,
    SERVER_ATTRS_WITH_MANUAL,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    _dep,
    _inc,
    client_for,
    make_class,
)
from tiqora.cmdb.access import CmdbAccess
from tiqora.cmdb.attachments import (
    AttachmentService,
    purge_stale_staged,
    sanitize_content_type,
    sanitize_filename,
)
from tiqora.db.tiqora.models import TiqoraCmdbAttachment, TiqoraCmdbCiHistory
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

PDF = b"%PDF-1.4 test"


def _new_ci_body(world: CmdbWorld, ids: tuple[int, int, int], **extra: Any) -> dict[str, Any]:
    cid, dep, inc = ids
    return {
        "class_id": cid,
        "name": "cmdb4-att-01",
        "deployment_state_id": dep,
        "incident_state_id": inc,
        "attributes": {"Vendor": [{"v": "Example"}]},
        **extra,
    }


async def _world(db: CmdbWorld, **kw: Any) -> tuple[int, int, int]:
    async with db.factory() as s:
        cid = await make_class(s, attributes=SERVER_ATTRS_WITH_MANUAL, **kw)
        return cid, await _dep(s, "production"), await _inc(s, "operational")


async def _upload(c: Any, name: str = "cmdb4-manual.pdf", data: bytes = PDF, **form: Any) -> Any:
    return await c.post(
        "/api/v1/cmdb/attachments",
        files={"file": (name, data, "application/pdf")},
        data={k: str(v) for k, v in form.items()},
    )


async def _history(db: CmdbWorld, ci_id: int) -> list[str]:
    async with db.factory() as s:
        rows = await s.execute(
            select(TiqoraCmdbCiHistory.event, TiqoraCmdbCiHistory.detail)
            .where(TiqoraCmdbCiHistory.ci_id == ci_id)
            .order_by(TiqoraCmdbCiHistory.id)
        )
        return [f"{e}:{d}" for e, d in rows.all()]


async def test_upload_staged_then_bind_on_create(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        r = await _upload(c)
        att = r.json()
        assert r.status_code == 201 and att["ci_id"] is None
        assert att["filename"] == "cmdb4-manual.pdf" and att["size_bytes"] == len(PDF)
        assert att["content_type"] == "application/pdf" and "content" not in att
        r = await c.post(
            "/api/v1/cmdb/cis",
            json=_new_ci_body(
                cmdb_db,
                ids,
                attachment_ids=[att["id"]],
                attributes={"Manual": [{"v": att["id"]}]},
            ),
        )
        assert r.status_code == 201, r.text
        ci = r.json()
        dl = await c.get(f"/api/v1/cmdb/attachments/{att['id']}")
        assert dl.status_code == 200 and dl.content == PDF
        assert ci["rendered"][-1]["display"] == "cmdb4-manual.pdf"
        meta = (await c.get(f"/api/v1/cmdb/attachments/{att['id']}/meta")).json()
        assert meta["ci_id"] == ci["id"]
        listed = (await c.get(f"/api/v1/cmdb/cis/{ci['id']}/attachments")).json()
        assert [a["id"] for a in listed] == [att["id"]]
    assert "AttachmentAddPost:cmdb4-manual.pdf" in await _history(cmdb_db, ci["id"])


async def test_attachment_ids_without_attribute_are_bound_too(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        att = (await _upload(c)).json()
        ci = (
            await c.post(
                "/api/v1/cmdb/cis", json=_new_ci_body(cmdb_db, ids, attachment_ids=[att["id"]])
            )
        ).json()
        meta = (await c.get(f"/api/v1/cmdb/attachments/{att['id']}/meta")).json()
    assert meta["ci_id"] == ci["id"]


async def test_other_users_staged_upload_is_not_bindable(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_B) as cb:
        staged = (await _upload(cb)).json()
        # a staged upload is private to its uploader
    async with client_for(cmdb_db, USER_A) as c:
        assert (await c.get(f"/api/v1/cmdb/attachments/{staged['id']}")).status_code == 404
        via_attr = await c.post(
            "/api/v1/cmdb/cis",
            json=_new_ci_body(cmdb_db, ids, attributes={"Manual": [{"v": staged["id"]}]}),
        )
        via_ids = await c.post(
            "/api/v1/cmdb/cis", json=_new_ci_body(cmdb_db, ids, attachment_ids=[staged["id"]])
        )
    for r in (via_attr, via_ids):
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "invalid_values"
        assert detail["issues"][0]["code"] == "unknown_reference"
    async with cmdb_db.factory() as s:
        row = await s.get(TiqoraCmdbAttachment, staged["id"])
        assert row is not None and row.ci_id is None


async def test_download_needs_class_read(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)  # GROUP_RW: A rw, B no access
    async with client_for(cmdb_db, USER_A) as c:
        ci = (await c.post("/api/v1/cmdb/cis", json=_new_ci_body(cmdb_db, ids))).json()
        att = (await _upload(c, ci_id=ci["id"])).json()
        assert att["ci_id"] == ci["id"]
        assert (await c.get(f"/api/v1/cmdb/attachments/{att['id']}")).status_code == 200
    async with client_for(cmdb_db, USER_B) as b:
        assert (await b.get(f"/api/v1/cmdb/attachments/{att['id']}")).status_code == 404
        assert (await b.get(f"/api/v1/cmdb/attachments/{att['id']}/meta")).status_code == 404
        assert (await b.get(f"/api/v1/cmdb/cis/{ci['id']}/attachments")).status_code == 404
        assert (await _upload(b, ci_id=ci["id"])).status_code == 404
        assert (await b.delete(f"/api/v1/cmdb/attachments/{att['id']}")).status_code == 404


async def test_read_only_user_can_download_but_not_upload_or_delete(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db, group_id=GROUP_RO)  # A: ro, B: ro, admin writes
    async with client_for(cmdb_db, USER_ADMIN) as adm:
        ci = (await adm.post("/api/v1/cmdb/cis", json=_new_ci_body(cmdb_db, ids))).json()
        att = (await _upload(adm, ci_id=ci["id"])).json()
    async with client_for(cmdb_db, USER_B) as b:
        assert (await b.get(f"/api/v1/cmdb/attachments/{att['id']}")).content == PDF
        assert (await _upload(b, ci_id=ci["id"])).status_code == 403
        assert (await b.delete(f"/api/v1/cmdb/attachments/{att['id']}")).status_code == 403


async def test_delete_refused_while_current_version_references_it(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        att = (await _upload(c)).json()
        ci = (
            await c.post(
                "/api/v1/cmdb/cis",
                json=_new_ci_body(cmdb_db, ids, attributes={"Manual": [{"v": att["id"]}]}),
            )
        ).json()
        r = await c.delete(f"/api/v1/cmdb/attachments/{att['id']}")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "attachment_in_use"
        assert (await c.get(f"/api/v1/cmdb/attachments/{att['id']}")).status_code == 200
        # drop the reference: the file can go
        put = await c.put(
            f"/api/v1/cmdb/cis/{ci['id']}",
            json={
                "expected_lock_version": ci["lock_version"],
                "name": ci["name"],
                "deployment_state_id": ci["deployment_state_id"],
                "incident_state_id": ci["incident_state_id"],
                "attributes": {"Vendor": [{"v": "Example"}]},
            },
        )
        assert put.status_code == 200, put.text
        assert (await c.delete(f"/api/v1/cmdb/attachments/{att['id']}")).status_code == 204
        assert (await c.get(f"/api/v1/cmdb/attachments/{att['id']}")).status_code == 404
    assert "AttachmentDeletePost:cmdb4-manual.pdf" in await _history(cmdb_db, ci["id"])


async def test_update_binds_staged_attachment_referenced_by_attribute(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        ci = (await c.post("/api/v1/cmdb/cis", json=_new_ci_body(cmdb_db, ids))).json()
        att = (await _upload(c)).json()
        put = await c.put(
            f"/api/v1/cmdb/cis/{ci['id']}",
            json={
                "expected_lock_version": ci["lock_version"],
                "name": ci["name"],
                "deployment_state_id": ci["deployment_state_id"],
                "incident_state_id": ci["incident_state_id"],
                "attributes": {"Manual": [{"v": att["id"]}]},
            },
        )
        assert put.status_code == 200, put.text
        meta = (await c.get(f"/api/v1/cmdb/attachments/{att['id']}/meta")).json()
    assert meta["ci_id"] == ci["id"]


async def test_staged_upload_can_be_deleted_by_uploader_only(cmdb_db: CmdbWorld) -> None:
    await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as a:
        att = (await _upload(a)).json()
    async with client_for(cmdb_db, USER_B) as b:
        assert (await b.delete(f"/api/v1/cmdb/attachments/{att['id']}")).status_code == 404
    async with client_for(cmdb_db, USER_A) as a:
        assert (await a.delete(f"/api/v1/cmdb/attachments/{att['id']}")).status_code == 204


async def test_upload_too_large_is_413(cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    await _world(cmdb_db)
    monkeypatch.setattr("tiqora.api.v1.cmdb.attachments.MAX_ATTACHMENT_BYTES", 10)
    async with client_for(cmdb_db, USER_A) as c:
        big = await _upload(c, data=b"x" * 11)
        ok = await _upload(c, data=b"x" * 10)
    assert big.status_code == 413
    assert ok.status_code == 201
    async with cmdb_db.factory() as s:
        rows = (await s.execute(select(TiqoraCmdbAttachment.size_bytes))).scalars().all()
    assert list(rows) == [10]


async def test_filename_is_sanitised_and_header_is_attachment(cmdb_db: CmdbWorld) -> None:
    await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.post(
            "/api/v1/cmdb/attachments",
            files={"file": ("../../cmdb4\x07evil.html", b"<script>1</script>", "text/html")},
        )
        att = r.json()
        assert r.status_code == 201 and att["filename"] == "cmdb4evil.html"
        dl = await c.get(f"/api/v1/cmdb/attachments/{att['id']}")
    assert dl.headers["content-disposition"].startswith("attachment;")
    assert 'filename="cmdb4evil.html"' in dl.headers["content-disposition"]
    assert dl.headers["x-content-type-options"] == "nosniff"


async def test_non_ascii_filename_uses_rfc5987(cmdb_db: CmdbWorld) -> None:
    await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        att = (await _upload(c, name="Bedienungsanleitung Übersicht.pdf")).json()
        dl = await c.get(f"/api/v1/cmdb/attachments/{att['id']}")
    disp = dl.headers["content-disposition"]
    assert disp.startswith("attachment;")
    assert "filename*=UTF-8''Bedienungsanleitung%20%C3%9Cbersicht.pdf" in disp


def test_sanitize_filename_edge_cases() -> None:
    assert sanitize_filename("..\\..\\evil\x00.txt") == "evil.txt"
    assert sanitize_filename("..%2F..%2fevil%07.txt") == "evil.txt"
    assert sanitize_filename("100%41.txt") == "100%41.txt"  # ordinary escapes stay literal
    assert sanitize_filename("..") == "datei"
    assert sanitize_filename(None) == "datei"
    assert sanitize_filename("   ") == "datei"
    long = sanitize_filename("a" * 400 + ".pdf")
    assert len(long) == 255 and long.endswith(".pdf")


def test_sanitize_content_type() -> None:
    assert sanitize_content_type("Application/PDF; charset=x") == "application/pdf"
    assert sanitize_content_type("not a type\r\nX: y") == "application/octet-stream"
    assert sanitize_content_type(None) == "application/octet-stream"


async def test_purge_stale_staged_removes_only_old_unbound_files(cmdb_db: CmdbWorld) -> None:
    ids = await _world(cmdb_db)
    async with client_for(cmdb_db, USER_A) as c:
        old = (await _upload(c, name="cmdb4-old.pdf")).json()
        fresh = (await _upload(c, name="cmdb4-fresh.pdf")).json()
        ci = (await c.post("/api/v1/cmdb/cis", json=_new_ci_body(cmdb_db, ids))).json()
        bound = (await _upload(c, name="cmdb4-bound.pdf", ci_id=ci["id"])).json()
    async with cmdb_db.factory() as s:
        for att_id in (old["id"], bound["id"]):
            row = await s.get(TiqoraCmdbAttachment, att_id)
            assert row is not None
            row.created_at = utc_now() - timedelta(hours=30)
        await s.commit()
    async with cmdb_db.factory() as s:
        removed = await purge_stale_staged(s, utc_now() - timedelta(hours=24))
        await s.commit()
    assert removed == 1
    async with cmdb_db.factory() as s:
        left = {int(i) for i in (await s.execute(select(TiqoraCmdbAttachment.id))).scalars().all()}
    assert left == {fresh["id"], bound["id"]}


async def test_service_get_content_round_trip(cmdb_db: CmdbWorld) -> None:
    await _world(cmdb_db)
    access = CmdbAccess(USER_A, False, frozenset(), frozenset())
    async with cmdb_db.factory() as s:
        svc = AttachmentService(s)
        rec = await svc.upload(
            access, ci_id=None, filename="cmdb4.bin", content_type=None, data=b"\x00\x01"
        )
        await s.commit()
        assert rec.content_type == "application/octet-stream"
        assert (await svc.get(access, rec.id)).sha256 == rec.sha256
        assert await svc.content(access, rec.id) == b"\x00\x01"
