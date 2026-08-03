"""Meilisearch integration tests (testcontainers, marker: search)."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.search import SearchIndexService
from tiqora.worker.indexer import rebuild_index
from tiqora.znuny.password import hash_password

pytestmark = [pytest.mark.db, pytest.mark.search]

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


@pytest.fixture(scope="module")
def meili_url() -> Generator[str, None, None]:
    import time
    import urllib.error
    import urllib.request

    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = (
        DockerContainer("getmeili/meilisearch:v1.11")
        .with_env("MEILI_MASTER_KEY", "test-master-key")
        .with_env("MEILI_ENV", "development")
        .with_exposed_ports(7700)
    )
    container.start()
    try:
        wait_for_logs(container, "Actix runtime found", timeout=90)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(7700)
        base = f"http://{host}:{port}"
        # Wait until /health responds (log line alone is not always enough)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=2) as resp:
                    if resp.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.5)
        else:
            raise TimeoutError(f"Meilisearch not healthy at {base}")
        yield base
    finally:
        container.stop()


def _seed_search(sync_url: str) -> dict[str, Any]:
    engine = create_engine(sync_url)
    pw = hash_password("secret")
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Idempotent cleanup of our block (shared session-scoped DB).
        conn.execute(text("DELETE FROM article_data_mime WHERE id = 900"))
        conn.execute(text("DELETE FROM article WHERE id = 900"))
        conn.execute(text("DELETE FROM ticket WHERE id IN (900, 901)"))
        conn.execute(text("DELETE FROM queue WHERE id = 300"))
        conn.execute(
            text("DELETE FROM group_user WHERE user_id IN (300, 301) OR group_id = 30"),
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = 30"))
        conn.execute(text("DELETE FROM users WHERE id IN (300, 301)"))
        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (300, 'search.agent', :pw, 'S', 'A', 1, :t, 1, :t, 1)
                """
            ),
            {"pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (301, 'search.outsider', :pw, 'O', 'U', 1, :t, 1, :t, 1)
                """
            ),
            {"pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO permission_groups
                (id, name, valid_id, create_time, create_by, change_time, change_by)
                VALUES (30, 'search-g', 1, :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO group_user
                (user_id, group_id, permission_key,
                 create_time, create_by, change_time, change_by)
                VALUES (300, 30, 'ro', :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO queue (
                    id, name, group_id, system_address_id, salutation_id, signature_id,
                    follow_up_id, follow_up_lock, valid_id,
                    create_time, create_by, change_time, change_by
                ) VALUES (300, 'SearchQ', 30, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO ticket (
                    id, tn, title, queue_id, ticket_lock_id, type_id,
                    user_id, responsible_user_id, ticket_priority_id, ticket_state_id,
                    customer_id, customer_user_id,
                    timeout, until_time, escalation_time, escalation_update_time,
                    escalation_response_time, escalation_solution_time, archive_flag,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    900, '20240601999999', 'UniqueZebraWidget', 300, 1, 1,
                    300, 1, 3, 4, 'C', 'c@x.com',
                    0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO article (
                    id, ticket_id, article_sender_type_id, communication_channel_id,
                    is_visible_for_customer, search_index_needs_rebuild,
                    create_time, create_by, change_time, change_by
                ) VALUES (900, 900, 3, 1, 1, 0, :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO article_data_mime (
                    id, article_id, a_subject, a_content_type, a_body, incoming_time,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    900, 900, 'subj', 'text/plain', 'body UniqueZebraWidget details', 0,
                    :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO ticket (
                    id, tn, title, queue_id, ticket_lock_id, type_id,
                    user_id, responsible_user_id, ticket_priority_id, ticket_state_id,
                    customer_id, customer_user_id,
                    timeout, until_time, escalation_time, escalation_update_time,
                    escalation_response_time, escalation_solution_time, archive_flag,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    901, '20240601999998', 'UniqueZebraWidget archived', 300, 1, 1,
                    300, 1, 3, 4, 'C', 'c@x.com',
                    0, 0, 0, 0, 0, 0, 1, :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )
    engine.dispose()
    return {"agent": 300, "outsider": 301, "ticket": 900, "archived_ticket": 901, "queue": 300}


@pytest.mark.asyncio
async def test_backfill_search_and_permission_filter(
    postgres_znuny_url: str,
    meili_url: str,
) -> None:
    ids = _seed_search(postgres_znuny_url)
    async_url = _to_async_url(postgres_znuny_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    settings = Settings(
        meili_url=meili_url,
        meili_master_key="test-master-key",
        meili_tickets_index="tickets_test",
        database_url=async_url,
    )

    result = await rebuild_index(
        settings=settings,
        session_factory=factory,
        batch_size=100,
        resume=False,
    )
    assert result["total_indexed"] >= 1

    async with factory() as session:
        svc = SearchIndexService(session, settings)
        try:
            hits = await svc.search(ids["agent"], "UniqueZebraWidget", limit=10)
            assert hits.estimated_total >= 1
            assert any(h.id == ids["ticket"] for h in hits.hits)
            # Archived tickets are hidden by default …
            assert all(h.id != ids["archived_ticket"] for h in hits.hits)

            # … and visible with include_archived (route gates this to admins).
            with_archived = await svc.search(
                ids["agent"], "UniqueZebraWidget", limit=10, include_archived=True
            )
            archived_hits = [h for h in with_archived.hits if h.id == ids["archived_ticket"]]
            assert len(archived_hits) == 1
            assert archived_hits[0].archive_flag == 1

            denied = await svc.search(ids["outsider"], "UniqueZebraWidget", limit=10)
            assert denied.estimated_total == 0
            assert denied.hits == []
        finally:
            await svc.close()

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_facets_and_filters(
    postgres_znuny_url: str,
    meili_url: str,
) -> None:
    ids = _seed_search(postgres_znuny_url)
    async_url = _to_async_url(postgres_znuny_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    settings = Settings(
        meili_url=meili_url,
        meili_master_key="test-master-key",
        meili_tickets_index="tickets_test",
        database_url=async_url,
    )

    result = await rebuild_index(
        settings=settings,
        session_factory=factory,
        batch_size=100,
        resume=False,
    )
    assert result["total_indexed"] >= 1

    sync_engine = create_engine(postgres_znuny_url)
    with sync_engine.connect() as conn:
        actual_state_type = conn.execute(
            text(
                """
                SELECT tst.name FROM ticket t
                JOIN ticket_state ts ON ts.id = t.ticket_state_id
                JOIN ticket_state_type tst ON tst.id = ts.type_id
                WHERE t.id = :tid
                """
            ),
            {"tid": ids["ticket"]},
        ).scalar_one()
    sync_engine.dispose()

    async with factory() as session:
        svc = SearchIndexService(session, settings)
        try:
            # Facet distribution is returned alongside hits.
            hits = await svc.search(ids["agent"], "UniqueZebraWidget", limit=10)
            assert "queue_id" in hits.facets
            assert hits.facets["queue_id"].get(str(ids["queue"])) == 1

            # A caller-supplied queue filter is intersected with, never widens,
            # the mandatory permission filter: an allowed queue still matches...
            allowed_match = await svc.search(
                ids["agent"],
                "UniqueZebraWidget",
                limit=10,
                queue_ids=[ids["queue"]],
            )
            assert allowed_match.estimated_total == 1

            # ...but a queue the agent has no permission on yields nothing, even
            # though it's a syntactically valid queue filter on its own.
            disallowed_match = await svc.search(
                ids["agent"],
                "UniqueZebraWidget",
                limit=10,
                queue_ids=[ids["queue"] + 1],
            )
            assert disallowed_match.estimated_total == 0

            # state_type filter: matching value returns the hit, mismatched
            # value excludes it.
            match_state = await svc.search(
                ids["agent"],
                "UniqueZebraWidget",
                limit=10,
                state_types=[actual_state_type],
            )
            assert match_state.estimated_total == 1

            no_match_state = await svc.search(
                ids["agent"],
                "UniqueZebraWidget",
                limit=10,
                state_types=["not-a-real-state-type"],
            )
            assert no_match_state.estimated_total == 0

            # created_ts range filter: the ticket was seeded at NOW (naive, UTC
            # per Znuny convention — see domain.search._dt_ts).
            now_ts = int(NOW.replace(tzinfo=UTC).timestamp())
            in_range_from = now_ts - 3600
            in_range_to = now_ts + 3600
            in_range = await svc.search(
                ids["agent"],
                "UniqueZebraWidget",
                limit=10,
                created_from=in_range_from,
                created_to=in_range_to,
            )
            assert in_range.estimated_total == 1

            out_of_range = await svc.search(
                ids["agent"],
                "UniqueZebraWidget",
                limit=10,
                created_from=in_range_to,
            )
            assert out_of_range.estimated_total == 0

            # owner_id / customer_id filters.
            owner_match = await svc.search(
                ids["agent"], "UniqueZebraWidget", limit=10, owner_id=ids["agent"]
            )
            assert owner_match.estimated_total == 1

            customer_match = await svc.search(
                ids["agent"], "UniqueZebraWidget", limit=10, customer_id="C"
            )
            assert customer_match.estimated_total == 1

            customer_no_match = await svc.search(
                ids["agent"], "UniqueZebraWidget", limit=10, customer_id="not-c"
            )
            assert customer_no_match.estimated_total == 0
        finally:
            await svc.close()

    await engine.dispose()


def _seed_similar(sync_url: str) -> dict[str, Any]:
    """Two closed tickets sharing a distinctive phrase + one open twin."""
    engine = create_engine(sync_url)
    pw = hash_password("secret")
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM article_data_mime WHERE id IN (910, 911, 912)"))
        conn.execute(text("DELETE FROM article WHERE id IN (910, 911, 912)"))
        conn.execute(text("DELETE FROM ticket WHERE id IN (910, 911, 912)"))
        conn.execute(text("DELETE FROM queue WHERE id = 310"))
        conn.execute(
            text("DELETE FROM group_user WHERE user_id IN (310, 311) OR group_id = 31"),
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = 31"))
        conn.execute(text("DELETE FROM users WHERE id IN (310, 311)"))
        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (310, 'similar.agent', :pw, 'S', 'A', 1, :t, 1, :t, 1)
                """
            ),
            {"pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (311, 'similar.outsider', :pw, 'O', 'U', 1, :t, 1, :t, 1)
                """
            ),
            {"pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO permission_groups
                (id, name, valid_id, create_time, create_by, change_time, change_by)
                VALUES (31, 'similar-g', 1, :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO group_user
                (user_id, group_id, permission_key,
                 create_time, create_by, change_time, change_by)
                VALUES (310, 31, 'ro', :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO queue (
                    id, name, group_id, system_address_id, salutation_id, signature_id,
                    follow_up_id, follow_up_lock, valid_id,
                    create_time, create_by, change_time, change_by
                ) VALUES (310, 'SimilarQ', 31, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        # ticket_state 2 = closed successful (type closed); 4 = open
        for tid, tn, title, state_id, art_id in (
            (910, "20240601910910", "PurpleNimbus VPN disconnect loop", 2, 910),
            (911, "20240601910911", "PurpleNimbus VPN disconnect again", 2, 911),
            (912, "20240601910912", "PurpleNimbus VPN disconnect open twin", 4, 912),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO ticket (
                        id, tn, title, queue_id, ticket_lock_id, type_id,
                        user_id, responsible_user_id, ticket_priority_id, ticket_state_id,
                        customer_id, customer_user_id,
                        timeout, until_time, escalation_time, escalation_update_time,
                        escalation_response_time, escalation_solution_time, archive_flag,
                        create_time, create_by, change_time, change_by
                    ) VALUES (
                        :tid, :tn, :title, 310, 1, 1,
                        310, 1, 3, :state_id, 'C', 'c@x.com',
                        0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1
                    )
                    """
                ),
                {"tid": tid, "tn": tn, "title": title, "state_id": state_id, "t": NOW},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO article (
                        id, ticket_id, article_sender_type_id, communication_channel_id,
                        is_visible_for_customer, search_index_needs_rebuild,
                        create_time, create_by, change_time, change_by
                    ) VALUES (:aid, :tid, 3, 1, 1, 0, :t, 1, :t, 1)
                    """
                ),
                {"aid": art_id, "tid": tid, "t": NOW},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO article_data_mime (
                        id, article_id, a_subject, a_content_type, a_body, incoming_time,
                        create_time, create_by, change_time, change_by
                    ) VALUES (
                        :aid, :aid, 'subj', 'text/plain',
                        'body PurpleNimbus VPN details', 0, :t, 1, :t, 1
                    )
                    """
                ),
                {"aid": art_id, "t": NOW},
            )
    engine.dispose()
    return {
        "agent": 310,
        "outsider": 311,
        "source": 910,
        "closed_peer": 911,
        "open_twin": 912,
        "queue": 310,
    }


@pytest.mark.asyncio
async def test_find_similar_closed_keyword(
    postgres_znuny_url: str,
    meili_url: str,
) -> None:
    ids = _seed_similar(postgres_znuny_url)
    async_url = _to_async_url(postgres_znuny_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    settings = Settings(
        meili_url=meili_url,
        meili_master_key="test-master-key",
        meili_tickets_index="tickets_similar_test",
        database_url=async_url,
    )

    result = await rebuild_index(
        settings=settings,
        session_factory=factory,
        batch_size=100,
        resume=False,
    )
    assert result["total_indexed"] >= 3

    async with factory() as session:
        svc = SearchIndexService(session, settings)
        try:
            similar = await svc.find_similar(
                ids["agent"],
                ids["source"],
                title="PurpleNimbus VPN disconnect loop",
                excerpt="body PurpleNimbus VPN details",
            )
            peer_ids = {item.id for item in similar.items}
            assert ids["closed_peer"] in peer_ids
            # Source excluded.
            assert ids["source"] not in peer_ids
            # Open twin must not appear (state_type = closed only).
            assert ids["open_twin"] not in peer_ids

            denied = await svc.find_similar(
                ids["outsider"],
                ids["source"],
                title="PurpleNimbus VPN disconnect loop",
            )
            assert denied.items == []
        finally:
            await svc.close()

    await engine.dispose()
