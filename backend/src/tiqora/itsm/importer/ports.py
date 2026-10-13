"""Ports of the ITSM importer (design D3): what the importer needs from the world.

Pure contract module: dataclasses and ``runtime_checkable`` protocols, no database access. The
implementations are in :mod:`tiqora.itsm.importer.facades`.

Controller rulings applied to the plan's D3 text: ``CmdbWritePort`` mirrors cmdb's
``importer_api`` one to one (R9: ``create_imported_ci``, ``add_imported_version`` with ``None`` =
unchanged, ``add_imported_attachment`` with time and author, ``add_definition(skip_if_equal=True)``,
``finish_import`` instead of ``recompute_incident_states``); link types come from itsm1's
``sync_link_type_rows`` (``ensure_link_types``); ``ItsmModePort`` carries ``switch_to_tiqora`` and
``rollback_to_znuny`` (R6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from tiqora.itsm.znuny_adapter.reader import CipRow, ServiceItsmRow

#: placeholder for a Znuny timestamp that is NULL
EPOCH = datetime(1970, 1, 1)

ItsmModeName = Literal["off", "znuny_readthrough", "tiqora"]


@dataclass(frozen=True, slots=True)
class ZnunySourceInfo:
    """What of Znuny's ITSM the source database holds."""

    core_installed: bool  # general_catalog + cip_allocate
    cmdb_installed: bool  # configitem, configitem_version, configitem_definition
    profile: str | None  # None: unknown package versions (see ``unsupported_reason``)
    unsupported_reason: str | None


@dataclass(frozen=True, slots=True)
class ZnunyCatalogItem:
    id: int
    catalog_class: str
    name: str
    valid: bool
    comment: str | None
    functionality: str | None
    #: ``Permission`` preference of a CI class (Znuny group id); None: none or not a group id
    permission_group: int | None = None


@dataclass(frozen=True, slots=True)
class ZnunyClassDefinition:
    id: int
    class_id: int
    version: int
    raw: str
    created_at: datetime
    created_by: int


@dataclass(frozen=True, slots=True)
class ZnunyCi:
    id: int
    number: str
    class_id: int
    last_version_id: int | None
    deployment_state_id: int | None
    incident_state_id: int | None
    created_at: datetime
    created_by: int
    changed_at: datetime
    changed_by: int


@dataclass(frozen=True, slots=True)
class ZnunyCiVersion:
    id: int
    ci_id: int
    name: str
    definition_id: int
    deployment_state_id: int | None
    incident_state_id: int | None
    created_at: datetime
    created_by: int
    xml_data: Mapping[str, Any]  # Znuny XMLData: {Key: [{"Content": v, Sub: [...]}, ...]}


@dataclass(frozen=True, slots=True)
class ZnunyLink:
    source_object: str
    source_key: str
    target_object: str
    target_key: str
    type_name: str
    state_name: str
    created_at: datetime
    created_by: int

    @property
    def map_key(self) -> str:
        return (
            f"{self.source_object}:{self.source_key}>"
            f"{self.target_object}:{self.target_key}:{self.type_name}"
        )


@dataclass(frozen=True, slots=True)
class ZnunyAttachment:
    ci_id: int
    filename: str
    content_type: str
    size: int
    data: bytes | None  # None: FS backend or not requested
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CiView:
    id: int
    number: str
    name: str
    class_id: int
    class_key: str
    class_name: str
    deployment_state: str
    deployment_state_id: int
    incident_state: str
    incident_state_id: int
    version_id: int | None
    definition_version: int
    attributes: dict[str, Any]  # cmdb D4: {"Key": [{"v": ..., "s": {...}}]}
    schema: dict[str, Any]
    created_at: datetime
    created_by: int
    changed_at: datetime
    #: (attachment id, filename, content type, size in bytes)
    attachments: list[tuple[int, str, str, int]]


@dataclass(frozen=True, slots=True)
class CiQuery:
    """Znuny-style CI search (``*`` wildcards)."""

    number: str | None = None
    name: str | None = None
    class_keys: list[str] | None = None
    deployment_states: list[str] | None = None
    incident_states: list[str] | None = None
    attributes: dict[str, str] | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    changed_after: datetime | None = None
    changed_before: datetime | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class CmdbClassView:
    id: int
    key: str
    name: str
    group_id: int
    schema: dict[str, Any]
    definition_version: int


@dataclass(frozen=True, slots=True)
class CatalogView:
    id: int
    catalog_class: str
    key: str
    name: str
    functionality: str | None


@dataclass(frozen=True, slots=True)
class ImpactView:
    #: (ci id, number, name)
    cis: list[tuple[int, str, str]]
    #: (service id, name)
    services: list[tuple[int, str]]
    #: (customer id, label)
    customers: list[tuple[str, str]]
    #: (ticket id, number, title)
    open_tickets: list[tuple[int, str, str]]
    truncated: bool


@runtime_checkable
class ImportSource(Protocol):
    """Read-only view of Znuny's ITSM tables (itsm1's adapter and the importer's extras)."""

    async def info(self) -> ZnunySourceInfo: ...
    async def profile(self) -> str | None: ...  # None = Znuny ITSM missing or unsupported
    async def catalog_items(self) -> list[ZnunyCatalogItem]: ...
    async def class_definitions(self) -> list[ZnunyClassDefinition]: ...
    async def ci_count(self) -> int: ...
    async def ci_ids_page(self, after_id: int, limit: int) -> list[int]: ...
    async def cis(self, ids: Sequence[int]) -> list[ZnunyCi]: ...
    async def versions(self, ci_ids: Sequence[int]) -> dict[int, list[ZnunyCiVersion]]: ...
    async def links_for_cis(self, ci_ids: Sequence[int]) -> list[ZnunyLink]: ...
    async def attachments(
        self, ci_ids: Sequence[int], *, with_data: bool
    ) -> list[ZnunyAttachment]: ...
    async def change_counts(self) -> dict[str, int]: ...
    async def max_ci_change_time(self) -> datetime | None: ...
    async def count_cis_changed_since(self, since: datetime) -> int: ...
    async def cip_rows(self) -> list[CipRow]: ...  # Znuny's criticality x impact -> priority
    async def service_itsm_rows(self) -> list[ServiceItsmRow]: ...  # ITSM columns of ``service``


@runtime_checkable
class CmdbWritePort(Protocol):
    """cmdb's ``importer_api``, bound to a session. Every method flushes, none commits."""

    async def upsert_catalog_item(
        self,
        *,
        catalog_class: str,
        item_key: str,
        name: str,
        functionality: str | None,
        valid: bool,
        position: int = 0,
    ) -> int: ...
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
    ) -> int: ...
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
    ) -> int: ...
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
    ) -> int: ...
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
    ) -> int | None: ...  # None = unchanged
    async def latest_version_id(self, ci_id: int) -> int | None: ...
    async def ensure_link_types(self, *, user_id: int) -> int: ...
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
    ) -> bool: ...  # False = existed
    async def add_imported_attachment(
        self,
        ci_id: int,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        created_at: datetime,
        created_by: int,
    ) -> int: ...
    async def import_history(
        self, ci_id: int, rows: Sequence[tuple[str, str, int, datetime]]
    ) -> int: ...
    async def delete_ci(self, ci_id: int, *, user_id: int) -> None: ...
    async def finish_import(self, ci_ids: Sequence[int]) -> int: ...  # CIs whose state changed


@runtime_checkable
class CmdbReadPort(Protocol):
    async def visible_class_ids(self, user_id: int, perm: Literal["ro", "rw"]) -> set[int]: ...
    async def get_ci(self, ci_id: int, *, version_id: int | None = None) -> CiView | None: ...
    async def ci_id_by_number(self, number: str) -> int | None: ...
    async def search_cis(
        self, query: CiQuery, *, class_ids: set[int], limit: int, offset: int
    ) -> list[int]: ...
    async def class_by_key_or_name(self, value: str) -> CmdbClassView | None: ...
    async def catalog_item(
        self, catalog_class: str, name_or_id: str | int
    ) -> CatalogView | None: ...
    async def impact(self, ci_id: int, *, max_depth: int, user_id: int) -> ImpactView: ...


@runtime_checkable
class ItsmModePort(Protocol):
    async def effective_mode(self) -> ItsmModeName: ...
    async def znuny_itsm_present(self) -> bool: ...
    async def is_switched(self) -> bool: ...
    async def switch_to_tiqora(self, *, user_id: int, import_run_id: int) -> ItsmModeName: ...
    async def rollback_to_znuny(self, *, user_id: int, import_run_id: int) -> ItsmModeName: ...


@dataclass(frozen=True)
class Ports:
    source: ImportSource
    writer: CmdbWritePort
    reader: CmdbReadPort
    mode: ItsmModePort
