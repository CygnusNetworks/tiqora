"""CMDB import as a job: dry run, abort/skip, idempotency, updates, XLSX, templates, limits.

Both dialects (``cmdb_db``); seed band 744000-744999 (logins and keys contain ``cmdb4``).
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

import pytest
from openpyxl import Workbook

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    CUSTOMER_A,
    GROUP_RO,
    SERVER_ATTRS,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    client_for,
    make_ci,
    make_class,
    parse_attrs,
    run_jobs_until_idle,
)
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.transfer.import_job import INPUT_PREFIX, CmdbImportPayload
from tiqora.cmdb.transfer.mapping import ImportOptions
from tiqora.db.tiqora.models import TiqoraJobFile
from tiqora.jobs.service import enqueue_job, utc_now

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

MAPPING = [
    {"column": "number", "target": "number"},
    {"column": "name", "target": "name"},
    {"column": "state", "target": "deployment_state"},
    {"column": "vendor", "target": "attr:Vendor"},
    {"column": "ip", "target": "attr:NIC[1].IPAddress[1]"},
    {"column": "nic", "target": "attr:NIC[1]"},
]
HEADER = ["number", "name", "state", "vendor", "nic", "ip"]


def _csv(rows: list[list[str]], header: list[str] | None = None) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(header or HEADER)
    w.writerows(rows)
    return out.getvalue().encode()


async def _class(db: CmdbWorld, **kw: Any) -> int:
    async with db.factory() as s:
        return await make_class(s, **kw)


async def _upload(
    db: CmdbWorld,
    user: int,
    cid: int | None,
    data: bytes,
    mapping: list[dict[str, Any]] | None,
    *,
    options: dict[str, Any] | None = None,
    dry_run: bool = False,
    filename: str = "cmdb4.csv",
    extra: dict[str, Any] | None = None,
) -> int:
    req: dict[str, Any] = {"dry_run": dry_run, "options": options or {}}
    if cid is not None:
        req["class_id"] = cid
    if mapping is not None:
        req["mapping"] = mapping
    req.update(extra or {})
    async with client_for(db, user) as c:
        r = await c.post(
            "/api/v1/cmdb/import-jobs",
            files={"file": (filename, data, "application/octet-stream")},
            data={"request": json.dumps(req)},
        )
    assert r.status_code == 202, r.text
    return int(r.json()["job_id"])


async def _run_and_get(db: CmdbWorld, job_id: int, user: int = USER_A) -> dict[str, Any]:
    await run_jobs_until_idle(db)
    async with client_for(db, user) as c:
        r = await c.get(f"/api/v1/jobs/{job_id}")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def _report_rows(db: CmdbWorld, job_id: int) -> list[dict[str, str]]:
    rows = db.sql(
        "SELECT data FROM tiqora_job_file WHERE job_id = :j AND name = 'report.csv'", j=job_id
    )
    assert len(rows) == 1
    text = bytes(rows[0][0]).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


async def _ci_count(db: CmdbWorld, cid: int) -> int:
    return int(db.sql("SELECT COUNT(*) FROM tiqora_cmdb_ci WHERE class_id = :c", c=cid)[0][0])


def _ci_by_name(db: CmdbWorld, name: str) -> tuple[int, str, int]:
    row = db.sql("SELECT id, number, version_count FROM tiqora_cmdb_ci WHERE name = :n", n=name)[0]
    return int(row[0]), str(row[1]), int(row[2])


async def _ci_values(db: CmdbWorld, ci_id: int) -> dict[str, Any]:
    async with client_for(db, USER_A) as c:
        r = await c.get(f"/api/v1/cmdb/cis/{ci_id}")
    assert r.status_code == 200, r.text
    values: dict[str, Any] = r.json()["current_version"]["attributes"]
    return values


async def test_dry_run_reports_and_writes_nothing(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        _csv([["", "cmdb4-i1", "Production", "Example", "eth0", "192.0.2.1"]]),
        MAPPING,
        options={"identifiers": ["name"]},
        dry_run=True,
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["status"] == "succeeded" and res["result"]["created"] == 1
    assert res["result"]["dry_run"] is True and res["result"]["errors"] == 0
    assert await _ci_count(cmdb_db, cid) == 0
    report = await _report_rows(cmdb_db, job)
    assert [(r["row"], r["status"]) for r in report] == [("2", "created")]


async def test_real_run_creates_cis_with_defaults_and_values(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        _csv(
            [
                ["", "cmdb4-i1", "Production", "Example", "eth0", "192.0.2.1"],
                ["", "cmdb4-i2", "", "Other", "", ""],
            ]
        ),
        MAPPING,
        options={"identifiers": ["name"]},
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["status"] == "succeeded", res
    assert res["result"] == {
        "created": 2,
        "updated": 0,
        "unchanged": 0,
        "errors": 0,
        "skipped": 0,
        "dry_run": False,
    }
    i1, number, versions = _ci_by_name(cmdb_db, "cmdb4-i1")
    assert number.startswith("CMDB4SRV-") and versions == 1
    assert await _ci_values(cmdb_db, i1) == {
        "Vendor": [{"v": "Example"}],
        "NIC": [{"v": "eth0", "s": {"IPAddress": [{"v": "192.0.2.1"}]}}],
    }
    # F27: an empty state cell gets the defaults production / operational
    i2, _, _ = _ci_by_name(cmdb_db, "cmdb4-i2")
    async with client_for(cmdb_db, USER_A) as c:
        ci = (await c.get(f"/api/v1/cmdb/cis/{i2}")).json()
    assert ci["states"]["deployment"]["item_key"] == "production"
    assert ci["states"]["incident_own"]["item_key"] == "operational"
    history = cmdb_db.sql(
        "SELECT COUNT(*) FROM tiqora_cmdb_ci_version WHERE change_note = 'Import cmdb4.csv'"
    )
    assert int(history[0][0]) == 2


async def test_abort_writes_nothing_on_bad_row(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    rows = [
        ["", "cmdb4-ok1", "Production", "A", "eth0", "192.0.2.1"],
        ["", "cmdb4-bad", "Nonexistent", "B", "eth0", "not-an-ip"],
        ["", "cmdb4-ok2", "Production", "C", "eth0", "192.0.2.3"],
    ]
    job = await _upload(
        cmdb_db, USER_A, cid, _csv(rows), MAPPING, options={"identifiers": ["name"]}
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["status"] == "failed" and await _ci_count(cmdb_db, cid) == 0
    assert "nichts geschrieben" in res["error"]
    report = await _report_rows(cmdb_db, job)
    assert [r["row"] for r in report if r["status"] == "error"] == ["3"]  # header is row 1
    assert "Nonexistent" in report[1]["message"] and "not-an-ip" in report[1]["message"]
    assert "Unbekannter Verwendungsstatus" in report[1]["message"]
    assert "Ungültige IP-Adresse" in report[1]["message"]
    assert [r["status"] for r in report if r["status"] != "error"] == ["skipped", "skipped"]


async def test_skip_writes_only_valid_rows(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    rows = [
        ["", "cmdb4-ok1", "Production", "A", "eth0", "192.0.2.1"],
        ["", "cmdb4-bad", "Nonexistent", "B", "eth0", "192.0.2.2"],
        ["", "cmdb4-ok2", "Production", "C", "eth0", "192.0.2.3"],
    ]
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        _csv(rows),
        MAPPING,
        options={"identifiers": ["name"], "on_error": "skip"},
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["status"] == "succeeded" and res["result"]["created"] == 2
    assert res["result"]["errors"] == 1
    assert await _ci_count(cmdb_db, cid) == 2
    report = await _report_rows(cmdb_db, job)
    assert [(r["row"], r["status"]) for r in report] == [
        ("2", "created"),
        ("3", "error"),
        ("4", "created"),
    ]
    assert report[0]["ci_number"].startswith("CMDB4SRV-")


async def test_rerun_is_idempotent(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    data = _csv(
        [
            ["", "cmdb4-r1", "Production", "A", "eth0", "192.0.2.1"],
            ["", "cmdb4-r2", "Pilot", "B", "eth1", "192.0.2.2"],
        ]
    )
    opts = {"identifiers": ["name"]}
    first = await _run_and_get(
        cmdb_db, await _upload(cmdb_db, USER_A, cid, data, MAPPING, options=opts)
    )
    assert first["result"]["created"] == 2
    second = await _run_and_get(
        cmdb_db, await _upload(cmdb_db, USER_A, cid, data, MAPPING, options=opts)
    )
    assert second["status"] == "succeeded", second
    assert second["result"]["unchanged"] == 2 and second["result"]["created"] == 0
    assert _ci_by_name(cmdb_db, "cmdb4-r1")[2] == 1 and _ci_by_name(cmdb_db, "cmdb4-r2")[2] == 1
    assert await _ci_count(cmdb_db, cid) == 2


async def test_rerun_matches_names_and_references_ignoring_case(cmdb_db: CmdbWorld) -> None:
    # both dialects: PostgreSQL compares case-sensitively unless the query lowers both sides
    cid = await _class(cmdb_db)
    mapping = [
        {"column": "name", "target": "name"},
        {"column": "owner", "target": "attr:Owner"},
    ]
    opts = {"identifiers": ["name"]}
    first = await _run_and_get(
        cmdb_db,
        await _upload(
            cmdb_db,
            USER_A,
            cid,
            _csv([["cmdb4-Case", CUSTOMER_A]], ["name", "owner"]),
            mapping,
            options=opts,
        ),
    )
    assert first["result"]["created"] == 1, first
    rerun = _csv([["CMDB4-CASE", CUSTOMER_A.upper()]], ["name", "owner"])
    second = await _run_and_get(
        cmdb_db, await _upload(cmdb_db, USER_A, cid, rerun, mapping, options=opts)
    )
    assert second["status"] == "succeeded", second
    assert second["result"]["created"] == 0 and second["result"]["errors"] == 0, second
    assert await _ci_count(cmdb_db, cid) == 1


async def test_update_by_number_with_empty_cells_keep_and_clear(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    async with cmdb_db.factory() as s:
        ci = await make_ci(
            s,
            cid,
            "cmdb4-u1",
            attributes={"Vendor": [{"v": "Keep Corp"}], "Model": [{"v": "M1"}]},
        )
    _, number, _ = _ci_by_name(cmdb_db, "cmdb4-u1")
    mapping = [
        {"column": "number", "target": "number"},
        {"column": "vendor", "target": "attr:Vendor"},
        {"column": "model", "target": "attr:Model"},
    ]
    header = ["number", "vendor", "model"]
    opts = {"identifiers": ["number"], "create_missing": False}

    keep = await _run_and_get(
        cmdb_db,
        await _upload(
            cmdb_db, USER_A, cid, _csv([[number, "", "M2"]], header), mapping, options=opts
        ),
    )
    assert keep["result"]["updated"] == 1
    assert await _ci_values(cmdb_db, ci) == {
        "Vendor": [{"v": "Keep Corp"}],
        "Model": [{"v": "M2"}],
    }

    clear = await _run_and_get(
        cmdb_db,
        await _upload(
            cmdb_db,
            USER_A,
            cid,
            _csv([[number, "", "M2"]], header),
            mapping,
            options={**opts, "empty_cells": "clear"},
        ),
    )
    assert clear["result"]["updated"] == 1
    assert await _ci_values(cmdb_db, ci) == {"Model": [{"v": "M2"}]}
    assert _ci_by_name(cmdb_db, "cmdb4-u1")[2] == 3


async def test_unknown_number_is_a_row_error_not_a_new_ci(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        _csv([["CMDB4SRV-999999", "cmdb4-x", "", "", "", ""]]),
        MAPPING,
        options={"on_error": "skip"},
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["result"]["errors"] == 1 and await _ci_count(cmdb_db, cid) == 0
    report = await _report_rows(cmdb_db, job)
    assert "CMDB4SRV-999999" in report[0]["message"]


async def test_duplicate_identifier_in_file_is_an_error(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    rows = [
        ["", "cmdb4-dup", "Production", "A", "", ""],
        ["", "cmdb4-dup", "Production", "B", "", ""],
    ]
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        _csv(rows),
        MAPPING,
        options={"identifiers": ["name"], "on_error": "skip"},
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["result"]["created"] == 1 and res["result"]["errors"] == 1
    report = await _report_rows(cmdb_db, job)
    assert (report[1]["row"], report[1]["status"]) == ("3", "error")
    assert "mehrfach" in report[1]["message"] and "Zeile 2" in report[1]["message"]


async def test_attribute_identifier_matches_the_search_index(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    async with cmdb_db.factory() as s:
        ci = await make_ci(s, cid, "cmdb4-s1", attributes={"SerialNumber": [{"v": "SN-Ä-1"}]})
    mapping = [
        {"column": "serial", "target": "attr:SerialNumber"},
        {"column": "vendor", "target": "attr:Vendor"},
    ]
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        _csv([["sn-ä-1", "Example"]], ["serial", "vendor"]),
        mapping,
        options={"identifiers": ["attr:SerialNumber"], "create_missing": False},
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["status"] == "succeeded" and res["result"]["updated"] == 1, res
    assert (await _ci_values(cmdb_db, ci))["Vendor"] == [{"v": "Example"}]


async def test_references_are_checked_once_per_value(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    mapping = [
        {"column": "name", "target": "name"},
        {"column": "owner", "target": "attr:Owner"},
    ]
    rows = [
        ["cmdb4-o1", CUSTOMER_A],
        ["cmdb4-o2", "cmdb4-nobody@example.org"],
    ]
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        _csv(rows, ["name", "owner"]),
        mapping,
        options={"identifiers": ["name"], "on_error": "skip"},
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["result"]["created"] == 1 and res["result"]["errors"] == 1
    report = await _report_rows(cmdb_db, job)
    assert report[0]["status"] == "created"
    assert "Owner" in report[1]["message"] and "cmdb4-nobody@example.org" in report[1]["message"]


async def test_stored_keys_the_definition_lost_are_carried_forward(cmdb_db: CmdbWorld) -> None:
    """F11: an import is not an interactive full save, so it never trips on legacy keys."""
    cid = await _class(cmdb_db)
    async with cmdb_db.factory() as s:
        ci = await make_ci(
            s, cid, "cmdb4-legacy", attributes={"Vendor": [{"v": "Old"}], "Model": [{"v": "M"}]}
        )
        slim = [a for a in SERVER_ATTRS if a["key"] != "Model"]
        await ClassService(s).add_definition(
            cid, parse_attrs(slim), comment=None, user_id=USER_ADMIN
        )
        await s.commit()
    mapping = [
        {"column": "name", "target": "name"},
        {"column": "vendor", "target": "attr:Vendor"},
    ]
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        _csv([["cmdb4-legacy", "New"]], ["name", "vendor"]),
        mapping,
        options={"identifiers": ["name"]},
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["status"] == "succeeded" and res["result"]["updated"] == 1, res
    values = await _ci_values(cmdb_db, ci)
    assert values["Vendor"] == [{"v": "New"}] and values["Model"] == [{"v": "M"}]


async def test_import_needs_rw_on_class_at_run_time(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db, group_id=GROUP_RO)  # USER_B has only ro here
    async with client_for(cmdb_db, USER_B) as c:
        r = await c.post(
            "/api/v1/cmdb/import-jobs",
            files={"file": ("cmdb4.csv", _csv([]), "text/csv")},
            data={"request": json.dumps({"class_id": cid, "mapping": MAPPING, "dry_run": True})},
        )
    assert r.status_code == 403, r.text
    jobs = cmdb_db.sql("SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.cmdb_import'")
    assert int(jobs[0][0]) == 0

    # the worker checks again: a job queued for somebody who has no write access fails
    payload = CmdbImportPayload(
        user_id=USER_B,
        class_id=cid,
        file_format="csv",
        mapping=[],  # never reached
        options=ImportOptions.model_validate({}),
        dry_run=False,
        filename="cmdb4.csv",
    )
    async with cmdb_db.factory() as s:
        job = await enqueue_job(s, kind="core.cmdb_import", payload=payload, created_by=USER_B)
        s.add(
            TiqoraJobFile(
                job_id=job.id,
                name=f"{INPUT_PREFIX}cmdb4.csv",
                content_type="text/csv",
                size_bytes=1,
                data=b"x",
                created_at=utc_now(),
            )
        )
        await s.commit()
        job_id = int(job.id)
    res = await _run_and_get(cmdb_db, job_id, user=USER_B)
    assert res["status"] == "failed" and "Berechtigung" in res["error"]


async def test_invalid_mapping_is_422_before_enqueue(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    bad = [
        {"column": "name", "target": "name"},
        {"column": "vendor", "target": "attr:DoesNotExist"},
        {"column": "ghost", "target": "attr:Vendor"},
        {"column": "state", "target": "attr:NIC["},
    ]
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.post(
            "/api/v1/cmdb/import-jobs",
            files={"file": ("cmdb4.csv", _csv([]), "text/csv")},
            data={
                "request": json.dumps(
                    {"class_id": cid, "mapping": bad, "options": {"identifiers": ["name"]}}
                )
            },
        )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_mapping"
    assert sorted(row["row"] for row in detail["rows"]) == [2, 3, 4]
    jobs = cmdb_db.sql("SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.cmdb_import'")
    assert int(jobs[0][0]) == 0


async def test_without_mapping_the_header_texts_are_the_targets(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    header = ["name", "attr:Vendor", "attr:NIC[1].IPAddress[2]", "Kommentar"]
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        _csv([["cmdb4-d1", "Example", "192.0.2.9", "wird ignoriert"]], header),
        None,
        options={"identifiers": ["name"]},
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["status"] == "succeeded" and res["result"]["created"] == 1, res
    ci, _, _ = _ci_by_name(cmdb_db, "cmdb4-d1")
    assert (await _ci_values(cmdb_db, ci))["Vendor"] == [{"v": "Example"}]


async def test_xlsx_import(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append([*HEADER, "bought", "cpu"])
    ws.append(
        ["", "cmdb4-x1", "Production", "Example", "eth0", "192.0.2.5", datetime(2026, 3, 4), 8.0]
    )
    buf = io.BytesIO()
    wb.save(buf)
    mapping = [
        *MAPPING,
        {"column": "bought", "target": "attr:Purchase"},
        {"column": "cpu", "target": "attr:CPU"},
    ]
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        buf.getvalue(),
        mapping,
        options={"identifiers": ["name"]},
        filename="cmdb4.xlsx",
    )
    res = await _run_and_get(cmdb_db, job)
    assert res["status"] == "succeeded" and res["result"]["created"] == 1, res
    ci, _, _ = _ci_by_name(cmdb_db, "cmdb4-x1")
    values = await _ci_values(cmdb_db, ci)
    assert values["Purchase"] == [{"v": "2026-03-04"}] and values["CPU"] == [{"v": 8}]


async def test_input_file_is_attached_to_job_and_only_creator_sees_result(
    cmdb_db: CmdbWorld,
) -> None:
    cid = await _class(cmdb_db)
    job = await _upload(
        cmdb_db,
        USER_A,
        cid,
        _csv([["", "cmdb4-p1", "", "", "", ""]]),
        MAPPING,
        options={"identifiers": ["name"]},
    )
    res = await _run_and_get(cmdb_db, job)
    assert sorted(f["name"] for f in res["files"]) == ["input-cmdb4.csv", "report.csv"]
    assert res["result"]["created"] == 1
    report_id = next(f["id"] for f in res["files"] if f["name"] == "report.csv")
    # an admin sees status and progress, but neither the result nor the files' content
    seen = await _run_and_get(cmdb_db, job, user=USER_ADMIN)
    assert seen["status"] == "succeeded" and seen["result"] is None
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.get(f"/api/v1/jobs/{job}/files/{report_id}")
    assert r.status_code == 404
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.get(f"/api/v1/jobs/{job}/files/{report_id}")
    assert r.status_code == 200
    assert r.content.startswith(b"\xef\xbb\xbfrow;status;ci_number;message")
    assert b";created;CMDB4SRV-" in r.content


async def test_upload_over_limit_is_413(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    body = b"name\n" + b"x" * (25 * 1024 * 1024)
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.post(
            "/api/v1/cmdb/import-jobs",
            files={"file": ("cmdb4.csv", body, "text/csv")},
            data={"request": json.dumps({"class_id": cid, "mapping": []})},
        )
    assert r.status_code == 413, r.text
    assert "25" in str(r.json()["detail"])
    jobs = cmdb_db.sql("SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.cmdb_import'")
    assert int(jobs[0][0]) == 0


async def test_template_crud_owner_only_and_stale_409(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    body = {
        "name": "cmdb4 vorlage",
        "class_id": cid,
        "file_format": "csv",
        "options": {"identifiers": ["name"]},
        "mapping": [{"column": "name", "target": "name"}],
        "shared": False,
    }
    async with client_for(cmdb_db, USER_A) as a, client_for(cmdb_db, USER_ADMIN) as admin:
        r = await a.post("/api/v1/cmdb/import-templates", json=body)
        assert r.status_code == 201, r.text
        tpl = r.json()
        assert tpl["owned"] is True and tpl["lock_version"] == 1
        assert tpl["options"]["identifiers"] == ["name"]
        tid = tpl["id"]

        # private: invisible to somebody else
        assert (await admin.get(f"/api/v1/cmdb/import-templates/{tid}")).status_code == 404
        assert (await admin.get("/api/v1/cmdb/import-templates")).json() == []
        assert [t["id"] for t in (await a.get("/api/v1/cmdb/import-templates")).json()] == [tid]

        # a bad target is rejected on save
        bad = {**body, "mapping": [{"column": "x", "target": "attr:"}]}
        assert (await a.post("/api/v1/cmdb/import-templates", json=bad)).status_code == 422

        # update needs the current lock_version
        shared = {**body, "shared": True, "name": "cmdb4 geteilt", "lock_version": 1}
        r = await a.put(f"/api/v1/cmdb/import-templates/{tid}", json=shared)
        assert r.status_code == 200, r.text
        assert r.json()["lock_version"] == 2 and r.json()["shared"] is True
        stale = await a.put(f"/api/v1/cmdb/import-templates/{tid}", json=shared)
        assert stale.status_code == 409
        missing = await a.put(f"/api/v1/cmdb/import-templates/{tid}", json=body)
        assert missing.status_code == 409  # no lock_version at all

        # shared: visible, but only the owner changes or deletes it
        got = await admin.get(f"/api/v1/cmdb/import-templates/{tid}")
        assert got.status_code == 200 and got.json()["owned"] is False
        other = await admin.put(
            f"/api/v1/cmdb/import-templates/{tid}", json={**shared, "lock_version": 2}
        )
        assert other.status_code == 403
        assert (await admin.delete(f"/api/v1/cmdb/import-templates/{tid}")).status_code == 403

        assert (await a.delete(f"/api/v1/cmdb/import-templates/{tid}")).status_code == 204
        assert (await a.get(f"/api/v1/cmdb/import-templates/{tid}")).status_code == 404


async def test_import_with_a_saved_template(cmdb_db: CmdbWorld) -> None:
    cid = await _class(cmdb_db)
    async with client_for(cmdb_db, USER_A) as a:
        r = await a.post(
            "/api/v1/cmdb/import-templates",
            json={
                "name": "cmdb4 vorlage",
                "class_id": cid,
                "file_format": "csv",
                "options": {"identifiers": ["name"], "delimiter": ","},
                "mapping": [
                    {"column": "Bezeichnung", "target": "name"},
                    {"column": "Hersteller", "target": "attr:Vendor"},
                ],
            },
        )
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        up = await a.post(
            "/api/v1/cmdb/import-jobs",
            files={
                "file": ("cmdb4.csv", b"Bezeichnung,Hersteller\ncmdb4-t1,Example\n", "text/csv")
            },
            data={"request": json.dumps({"template_id": tid, "dry_run": False})},
        )
        assert up.status_code == 202, up.text
    res = await _run_and_get(cmdb_db, int(up.json()["job_id"]))
    assert res["status"] == "succeeded" and res["result"]["created"] == 1, res
    ci, _, _ = _ci_by_name(cmdb_db, "cmdb4-t1")
    assert (await _ci_values(cmdb_db, ci))["Vendor"] == [{"v": "Example"}]


async def test_import_quota_is_429(cmdb_db: CmdbWorld) -> None:
    from tiqora.api.v1.cmdb.transfer import MAX_ACTIVE_IMPORTS_PER_USER

    cid = await _class(cmdb_db)
    data = _csv([["", "cmdb4-q", "Production", "A", "eth0", "192.0.2.1"]])
    opts = {"identifiers": ["name"]}
    for _ in range(MAX_ACTIVE_IMPORTS_PER_USER):
        await _upload(cmdb_db, USER_A, cid, data, MAPPING, options=opts, dry_run=True)
    req = {"dry_run": True, "options": opts, "class_id": cid, "mapping": MAPPING}
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.post(
            "/api/v1/cmdb/import-jobs",
            files={"file": ("cmdb4.csv", data, "application/octet-stream")},
            data={"request": json.dumps(req)},
        )
    assert r.status_code == 429, r.text
    assert r.json()["detail"]["code"] == "too_many_imports"
