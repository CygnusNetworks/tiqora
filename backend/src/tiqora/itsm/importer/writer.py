"""Dry-run writer: answers like :class:`CmdbWritePort` but records nothing in the database.

Creates return decreasing fake ids (-1, -2, ...); an unchanged version answers ``None`` like the
real writer. ``latest_version_id`` knows the fake state first and asks the real writer (given at
construction) only for real (positive) CI ids.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from tiqora.itsm.importer.ports import CmdbWritePort


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


class DryRunWriter(CmdbWritePort):
    def __init__(self, real: CmdbWritePort | None = None) -> None:
        self._real = real
        self._next = 0
        self._catalog: dict[tuple[str, str], int] = {}
        self._classes: dict[str, int] = {}
        self._definitions: dict[tuple[int, str], int] = {}
        self._last_attrs: dict[int, str] = {}
        self._last_version: dict[int, int] = {}
        self._links: set[tuple[str, str, str, str, str]] = set()

    def _fake_id(self) -> int:
        self._next -= 1
        return self._next

    async def upsert_catalog_item(
        self,
        *,
        catalog_class: str,
        item_key: str,
        name: str,
        functionality: str | None,
        valid: bool,
        position: int = 0,
    ) -> int:
        key = (catalog_class, item_key)
        if key not in self._catalog:
            self._catalog[key] = self._fake_id()
        return self._catalog[key]

    async def upsert_class(
        self,
        *,
        class_key: str,
        name: str,
        group_id: int,
        number_prefix: str,
        user_id: int,
        icon: str | None = None,
        portal_visible: bool = False,
    ) -> int:
        if class_key not in self._classes:
            self._classes[class_key] = self._fake_id()
        return self._classes[class_key]

    async def add_definition(
        self,
        *,
        class_id: int,
        attributes: Sequence[Any],
        comment: str | None,
        created_by: int,
        created_at: datetime,
        make_current: bool = True,
        skip_if_equal: bool = True,
        extra_class_keys: frozenset[str] = frozenset(),
    ) -> int:
        key = (class_id, _canon(list(attributes)))
        if skip_if_equal and key in self._definitions:
            return self._definitions[key]
        self._definitions[key] = self._fake_id()
        return self._definitions[key]

    async def create_imported_ci(
        self,
        *,
        class_id: int,
        number: str,
        name: str,
        created_by: int,
        created_at: datetime,
        deployment_state_id: int | None = None,
        incident_state_id: int | None = None,
    ) -> int:
        return self._fake_id()

    async def add_imported_version(
        self,
        ci_id: int,
        *,
        definition_id: int,
        name: str,
        deployment_state_id: int,
        incident_state_id: int,
        attributes: dict[str, Any],
        created_by: int,
        created_at: datetime,
        change_note: str | None = None,
        write_index: bool = True,
    ) -> int | None:
        canon = _canon(attributes)
        if self._last_attrs.get(ci_id) == canon:
            return None
        self._last_attrs[ci_id] = canon
        self._last_version[ci_id] = self._fake_id()
        return self._last_version[ci_id]

    async def latest_version_id(self, ci_id: int) -> int | None:
        if ci_id in self._last_version:
            return self._last_version[ci_id]
        if ci_id > 0 and self._real is not None:
            return await self._real.latest_version_id(ci_id)
        return None

    async def ensure_link_types(self, *, user_id: int) -> int:
        return 0

    async def add_link(
        self,
        *,
        source_kind: str,
        source_key: str,
        target_kind: str,
        target_key: str,
        link_type: str,
        created_by: int,
        created_at: datetime,
    ) -> bool:
        key = (source_kind, source_key, target_kind, target_key, link_type)
        if key in self._links:
            return False
        self._links.add(key)
        return True

    async def add_imported_attachment(
        self,
        ci_id: int,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        created_at: datetime,
        created_by: int,
    ) -> int:
        return self._fake_id()

    async def import_history(
        self, ci_id: int, rows: Sequence[tuple[str, str, int, datetime]]
    ) -> int:
        return len(rows)

    async def delete_ci(self, ci_id: int, *, user_id: int) -> None:
        return None

    async def finish_import(self, ci_ids: Sequence[int]) -> int:
        return 0
