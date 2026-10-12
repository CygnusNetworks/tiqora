"""GDPR erasure of the Tiqora CMDB: links, CI versions, search index and history (both dialects)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    CUSTOMER_A,
    CUSTOMER_B,
    SERVER_ATTRS,
    TICKET_OPEN,
    USER_A,
    CmdbWorld,
    make_ci,
    make_class,
)
from tiqora.cmdb.definition import parse_attrs
from tiqora.cmdb.erasure import scrub_values
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.service import CiService, system_access
from tiqora.config import Settings
from tiqora.gdpr.erasure import rollback_job, run_erasure

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

_GDPR_TABLES = (
    "tiqora_gdpr_backup",
    "tiqora_gdpr_job",
    "tiqora_gdpr_audit",
    "tiqora_cache_invalidation",
    "tiqora_event_outbox",
    "tiqora_report_dirty_ticket",
)
_ATTRS = [{**a, "searchable": True} if a["key"] == "Owner" else a for a in SERVER_ATTRS]


@pytest.fixture(autouse=True)
def _clean_gdpr_rows(cmdb_db: CmdbWorld) -> Any:
    def wipe() -> None:
        for table in _GDPR_TABLES:
            cmdb_db.sql(f"DELETE FROM {table}")  # noqa: S608

    wipe()
    yield
    wipe()


def _count(db: CmdbWorld, sql: str, **p: Any) -> int:
    return int(db.sql(sql, **p)[0][0])


async def _setup(db: CmdbWorld) -> tuple[int, int]:
    """CI A: Owner = customer A in v1, customer B in v2, customer A again in v3 (current),
    links to customer A and ticket 740300. CI B: Owner = customer B."""
    async with db.factory() as s:
        cid = await make_class(s, attributes=_ATTRS)
        a = await make_ci(s, cid, "cmdb4-a", attributes={"Owner": [{"v": CUSTOMER_A}]})
        b = await make_ci(s, cid, "cmdb4-b", attributes={"Owner": [{"v": CUSTOMER_B}]})
        svc = CiService(s)
        for owner in (CUSTOMER_B, CUSTOMER_A):
            await svc.update(
                system_access(USER_A),
                a,
                expected_lock_version=None,
                attributes_patch={"Owner": [{"v": owner}]},
                enforce_permissions=False,
            )
        links = LinkService(s)
        for end in (LinkEnd("customer_user", CUSTOMER_A), LinkEnd("ticket", str(TICKET_OPEN))):
            await links.add(
                system_access(USER_A),
                source=LinkEnd("ci", str(a)),
                target=end,
                link_type_key="RelevantTo",
                enforce_permissions=False,
            )
        await s.commit()
    return a, b


def _owner_index(db: CmdbWorld, ci_id: int) -> list[str]:
    rows = db.sql(
        "SELECT value_key FROM tiqora_cmdb_ci_attr WHERE ci_id = :c AND attr_path = 'Owner'",
        c=ci_id,
    )
    return [str(r[0]) for r in rows]


def _owner_values(db: CmdbWorld, ci_id: int) -> list[str]:
    rows = db.sql(
        "SELECT attributes FROM tiqora_cmdb_ci_version WHERE ci_id = :c ORDER BY id", c=ci_id
    )
    return [json.loads(r[0])["Owner"][0]["v"] for r in rows]


def test_scrub_values_walks_sub_attributes_and_keeps_the_rest() -> None:
    attrs = parse_attrs(
        [
            {
                "key": "Contacts",
                "name": "Contacts",
                "type": "text",
                "count_max": 3,
                "sub": [{"key": "Person", "name": "Person", "type": "customer_user"}],
            },
            {"key": "Owner", "name": "Owner", "type": "customer_user"},
            {"key": "Note", "name": "Note", "type": "text"},
        ]
    )
    values = {
        "Contacts": [{"v": "x", "s": {"Person": [{"v": "a@example.org"}, {"v": "z@example.org"}]}}],
        "Owner": [{"v": "a@example.org"}],
        "Note": [{"v": "a@example.org"}],  # free text is not a customer reference
        "Legacy": [{"v": "a@example.org"}],  # key the definition no longer knows
    }
    new, changed = scrub_values(attrs, values, {"a@example.org": "tok-1"})
    assert changed is True
    assert new["Contacts"][0]["s"]["Person"] == [{"v": "tok-1"}, {"v": "z@example.org"}]
    assert new["Owner"] == [{"v": "tok-1"}]
    assert new["Note"] == values["Note"]
    assert new["Legacy"] == values["Legacy"]
    assert scrub_values(attrs, values, {"nobody@example.org": "t"})[1] is False
    assert values["Owner"] == [{"v": "a@example.org"}]  # the input is not mutated


@pytest.mark.parametrize("delete_mode", [False, True])
async def test_erasure_scrubs_cmdb_data_and_rollback_restores_it(
    cmdb_db: CmdbWorld, delete_mode: bool
) -> None:
    a, b = await _setup(cmdb_db)
    cuid = _count(cmdb_db, "SELECT id FROM customer_user WHERE login = :l", l=CUSTOMER_A)
    like = {"p": "%cmdb4-cust-a%"}
    assert _owner_values(cmdb_db, a) == [CUSTOMER_A, CUSTOMER_B, CUSTOMER_A]
    assert _count(
        cmdb_db, "SELECT COUNT(*) FROM tiqora_cmdb_ci_history WHERE detail LIKE :p", **like
    )
    assert _owner_index(cmdb_db, a) == [CUSTOMER_A]
    assert _count(cmdb_db, "SELECT COUNT(*) FROM tiqora_itsm_link") == 2
    before_b = cmdb_db.sql(
        "SELECT id, attributes FROM tiqora_cmdb_ci_version WHERE ci_id = :c", c=b
    )

    result = await run_erasure(
        cmdb_db.factory,
        Settings(),
        customer_user_ids=[cuid],
        mode="delete" if delete_mode else "anonymize",
        delete_tickets=delete_mode,
        seed=7,
        force_parallel=True,
        actor="test",
    )

    token = _owner_values(cmdb_db, a)[-1]
    assert token != CUSTOMER_A and token
    assert _owner_values(cmdb_db, a) == [token, CUSTOMER_B, token, token]  # + system version
    assert (
        _count(
            cmdb_db, "SELECT COUNT(*) FROM tiqora_cmdb_ci_version WHERE attributes LIKE :p", **like
        )
        == 0
    )
    assert (
        _count(cmdb_db, "SELECT COUNT(*) FROM tiqora_cmdb_ci_history WHERE detail LIKE :p", **like)
        == 0
    )
    note, creator = cmdb_db.sql(
        "SELECT change_note, created_by FROM tiqora_cmdb_ci_version WHERE ci_id = :c"
        " ORDER BY id DESC LIMIT 1",
        c=a,
    )[0]
    assert (note, creator) == ("Datenschutz", 1)
    ci = cmdb_db.sql(
        "SELECT version_count, current_version_id, lock_version FROM tiqora_cmdb_ci WHERE id = :c",
        c=a,
    )[0]
    newest = _count(cmdb_db, "SELECT MAX(id) FROM tiqora_cmdb_ci_version WHERE ci_id = :c", c=a)
    assert (ci[0], ci[1]) == (4, newest)
    assert _owner_index(cmdb_db, a) == [token.casefold()]
    # the other customer's CI is untouched
    assert (
        cmdb_db.sql("SELECT id, attributes FROM tiqora_cmdb_ci_version WHERE ci_id = :c", c=b)
        == before_b
    )
    # links: the customer link is gone; the ticket link only goes with a hard-deleted ticket
    remaining = cmdb_db.sql("SELECT target_kind FROM tiqora_itsm_link")
    assert [r[0] for r in remaining] == ([] if delete_mode else ["ticket"])
    redacted = {
        r[0]
        for r in cmdb_db.sql(
            "SELECT detail FROM tiqora_cmdb_ci_history WHERE ci_id = :c AND event = 'LinkAdd'", c=a
        )
    }
    assert "RelevantTo: customer_user [gelöscht]" in redacted
    assert ("RelevantTo: ticket [gelöscht]" in redacted) is delete_mode

    await rollback_job(
        cmdb_db.factory, Settings(), result.job_id, force_parallel=True, actor="test"
    )

    assert _owner_values(cmdb_db, a) == [CUSTOMER_A, CUSTOMER_B, CUSTOMER_A]
    assert _owner_index(cmdb_db, a) == [CUSTOMER_A]
    ci = cmdb_db.sql(
        "SELECT version_count, current_version_id, lock_version FROM tiqora_cmdb_ci WHERE id = :c",
        c=a,
    )[0]
    assert ci[0] == 3
    assert _count(cmdb_db, "SELECT COUNT(*) FROM tiqora_cmdb_ci_version WHERE ci_id = :c", c=a) == 3
    assert _count(
        cmdb_db, "SELECT COUNT(*) FROM tiqora_cmdb_ci_history WHERE detail LIKE :p", **like
    )
    assert sorted(r[0] for r in cmdb_db.sql("SELECT target_kind FROM tiqora_itsm_link")) == [
        "customer_user",
        "ticket",
    ]
    assert (
        _count(
            cmdb_db,
            "SELECT COUNT(*) FROM tiqora_cmdb_ci_history"
            " WHERE event = 'VersionCreate' AND ci_id = :c",
            c=a,
        )
        == 3
    )
