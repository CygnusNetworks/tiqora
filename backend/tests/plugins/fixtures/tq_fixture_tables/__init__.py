"""Fixture plugin ``org.example.tables``: one route over its own ``tqp_tables_note`` table."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from tiqora_sdk import DbSession, PluginRegistry

router = APIRouter()


@router.get("/notes")
async def notes(db: DbSession) -> list[str]:
    result = await db.execute(text("SELECT body FROM tqp_tables_note ORDER BY id"))
    return [str(row[0]) for row in result]


class TablesPlugin:
    def register(self, registry: PluginRegistry) -> None:
        registry.route(router)


plugin = TablesPlugin()
