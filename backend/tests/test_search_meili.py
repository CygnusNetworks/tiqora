"""Meilisearch integration tests (testcontainers, marker: search)."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
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
        conn.execute(text("DELETE FROM ticket WHERE id = 900"))
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
    engine.dispose()
    return {"agent": 300, "outsider": 301, "ticket": 900, "queue": 300}


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

            denied = await svc.search(ids["outsider"], "UniqueZebraWidget", limit=10)
            assert denied.estimated_total == 0
            assert denied.hits == []
        finally:
            await svc.close()

    await engine.dispose()
