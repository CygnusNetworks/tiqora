"""KB publish -> Meilisearch indexing + agent/customer scoping (testcontainers).

Mirrors the fixture/container pattern in test_search_meili.py but targets the
``kb`` Meilisearch index and KbService.publish()/search_agent()/search_customer().
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.kb.schemas import ArticleIn, CategoryIn
from tiqora.kb.service import KbService
from tiqora.znuny.password import hash_password

pytestmark = [pytest.mark.db, pytest.mark.search]

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
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


def _seed_users(sync_url: str) -> dict[str, int]:
    """Seed two agents: one in the restricted group, one with no group memberships."""
    engine = create_engine(sync_url)
    pw = hash_password("secret")
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Idempotent cleanup of our block (shared session-scoped DB).
        conn.execute(
            text("DELETE FROM group_user WHERE user_id IN (500, 501) OR group_id = 50"),
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = 50"))
        conn.execute(text("DELETE FROM users WHERE id IN (500, 501)"))
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (500, 'kb.member', :pw, 'K', 'M', 1, :t, 1, :t, 1),"
                "        (501, 'kb.outsider', :pw, 'K', 'O', 1, :t, 1, :t, 1)"
            ),
            {"pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups"
                " (id, name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (50, 'kb-group', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user"
                " (user_id, group_id, permission_key,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES (500, 50, 'ro', :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
    engine.dispose()
    return {"member": 500, "outsider": 501, "group": 50}


@pytest.mark.asyncio
async def test_publish_indexes_chunks_with_scoping(
    postgres_znuny_url: str,
    meili_url: str,
) -> None:
    ids = _seed_users(postgres_znuny_url)
    async_url = _to_async_url(postgres_znuny_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    settings = Settings(
        database_url=async_url,
        meili_url=meili_url,
        meili_master_key="test-master-key",
        meili_kb_index="kb_test",
    )

    async with factory() as session:
        svc = KbService(session, settings)
        try:
            # Restricted category: only visible to group 50 agents, not customers.
            async with session.begin():
                restricted_cat = await svc.create_category(
                    1,
                    CategoryIn(
                        name="Internal Runbooks",
                        slug="internal-runbooks-kb1",
                        permission_group_ids=[ids["group"]],
                        customer_visible=False,
                    ),
                )
            async with session.begin():
                restricted_article = await svc.create_article(
                    1,
                    ArticleIn(
                        category_id=restricted_cat.id,
                        title="ZebraDbFailoverRunbook",
                        slug="zebra-db-failover-runbook-1",
                        content_md="## Steps\n\nRestart the ZebraDbFailoverRunbook primary.",
                    ),
                )
            async with session.begin():
                await svc.publish(1, restricted_article.id)

            # Public + customer-visible category: no group restriction.
            async with session.begin():
                public_cat = await svc.create_category(
                    1,
                    CategoryIn(
                        name="Public FAQ",
                        slug="public-faq-kb1",
                        permission_group_ids=[],
                        customer_visible=True,
                    ),
                )
            async with session.begin():
                public_article = await svc.create_article(
                    1,
                    ArticleIn(
                        category_id=public_cat.id,
                        title="ZebraPasswordResetFaq",
                        slug="zebra-password-reset-faq-1",
                        content_md="## Steps\n\nUse the ZebraPasswordResetFaq self-service link.",
                    ),
                )
            async with session.begin():
                await svc.publish(1, public_article.id)

            # Agent in the restricted group sees both.
            member_hits = await svc.search_agent(ids["member"], "Zebra", limit=10)
            member_article_ids = {h.article_id for h in member_hits.hits}
            assert restricted_article.id in member_article_ids
            assert public_article.id in member_article_ids

            # Agent outside the restricted group only sees the public article.
            outsider_hits = await svc.search_agent(ids["outsider"], "Zebra", limit=10)
            outsider_article_ids = {h.article_id for h in outsider_hits.hits}
            assert restricted_article.id not in outsider_article_ids
            assert public_article.id in outsider_article_ids

            # Customer/portal search: only customer_visible categories, regardless
            # of permission_group_id.
            customer_hits = await svc.search_customer("Zebra", limit=10)
            customer_article_ids = {h.article_id for h in customer_hits.hits}
            assert restricted_article.id not in customer_article_ids
            assert public_article.id in customer_article_ids
        finally:
            await svc.close()

    await engine.dispose()
