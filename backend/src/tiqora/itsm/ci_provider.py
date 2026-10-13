"""CI provider contract (cross-plan; owner: ITSM core, design I3) — pure, no DB code.

Mode A (``znuny_readthrough``) reads Znuny's ITSM tables through ``ZnunyCiProvider``; Mode B
(``tiqora``) is the Tiqora CMDB's provider. Both implement :class:`CiProvider` and pass the
contract kit ``backend/tests/itsm_provider_contract.py``. Changes are additive only (new optional
methods with defaults); a rename needs every implementing plan updated.

Rules every implementation follows:

* ``ref.source != provider.source`` → :class:`CiNotFound`.
* Methods never commit; ``link_ticket``/``unlink_ticket`` check themselves (R12, before any write)
  ``rw`` on the ticket's queue group and ``ro`` on the CI; a missing ticket is :class:`CiNotFound`,
  missing ``rw`` on the ticket :class:`CiPermissionDenied`. Callers still filter by ticket ``ro``
  when listing.
* ``link_ticket`` is idempotent (``False`` when the link exists); ``unlink_ticket`` returns the
  number of removed links (0 = nothing).
* ``search`` returns ``total`` exactly; ``limit`` > 100 is clamped to 100.
* No ``tiqora_*`` × Znuny JOIN; Znuny names (users, services) are looked up via IN lists.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol, cast, get_args, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

CiSource = Literal["znuny", "tiqora"]

_CI_SOURCES: frozenset[str] = frozenset(get_args(CiSource))
_ID_RE = re.compile(r"[0-9]+", re.ASCII)


@dataclass(frozen=True, slots=True, order=True)
class CiRef:
    """A CI in one of the two id spaces; text form ``"<source>:<id>"``."""

    source: CiSource
    id: int

    def __str__(self) -> str:
        return f"{self.source}:{self.id}"

    @classmethod
    def parse(cls, raw: str) -> CiRef:
        """Parse ``"znuny:123"`` / ``"tiqora:7"``; ``ValueError`` on anything else."""
        source, sep, ident = raw.partition(":")
        if not sep or source not in _CI_SOURCES or not _ID_RE.fullmatch(ident):
            raise ValueError(f"invalid CI reference: {raw!r}")
        return cls(cast("CiSource", source), int(ident))


@dataclass(frozen=True, slots=True)
class CatalogValue:
    key: str  # Mode A: str(general_catalog.id); Mode B: tiqora catalog key
    name: str
    # incident: operational|warning|incident; deployment: preproductive|productive|postproductive|…
    # (Znuny general-catalog preference "Functionality")
    functionality: str | None


@dataclass(frozen=True, slots=True)
class CiClassInfo:
    key: str
    name: str
    icon: str | None
    can_read: bool
    can_write: bool


@dataclass(frozen=True, slots=True)
class CiSummary:
    ref: CiRef
    number: str
    name: str
    class_key: str
    class_name: str
    deployment_state: CatalogValue | None
    incident_state: CatalogValue | None
    changed_at: datetime | None
    edit_url: str | None  # Mode A: Znuny AgentITSMConfigItemEdit; Mode B: None


@dataclass(frozen=True, slots=True)
class CiAttribute:
    key: str
    label: str
    # text|textarea|integer|date|datetime|catalog|ci|customer|customer_company|user|service|sla|
    # queue|priority|ticket_type|state|url|email|ip|other
    kind: str
    display: str  # resolved, human-readable
    raw: str | None  # stored value as text
    ref: CiRef | None  # kind == "ci"
    children: tuple[CiAttribute, ...] = ()


@dataclass(frozen=True, slots=True)
class CiVersionInfo:
    id: int
    seq: int
    name: str
    deployment_state: CatalogValue | None
    incident_state: CatalogValue | None
    created_at: datetime
    created_by: int | None
    created_by_name: str | None
    change_note: str | None


@dataclass(frozen=True, slots=True)
class CiDetail:
    summary: CiSummary
    version: CiVersionInfo | None  # the version shown (current unless a version was requested)
    attributes: tuple[CiAttribute, ...]
    definition_version: int | None
    read_only: bool  # True in Mode A


@dataclass(frozen=True, slots=True)
class CiQuery:
    text: str | None = None  # number/name prefix-or-contains; Mode A also attribute values (>= 3)
    class_keys: tuple[str, ...] = ()
    deployment_state_keys: tuple[str, ...] = ()
    incident_state_keys: tuple[str, ...] = ()
    refs: tuple[CiRef, ...] = ()
    limit: int = 25  # 1..100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class CiPage:
    items: tuple[CiSummary, ...]
    # exact unless ``truncated`` (Mode A: an attribute search hit its candidate cap)
    total: int
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class CiTicketLink:
    ticket_id: int
    ci: CiSummary
    link_type: str
    direction: Literal["source", "target", "both"]  # ticket's side; "both" for unpointed types
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class LinkTypeOption:
    name: str
    source_label: str
    target_label: str
    pointed: bool


@dataclass(frozen=True, slots=True)
class ServiceIncidentState:
    service_id: int
    state: CatalogValue | None  # worst functionality over the service's CIs; None = no CI linked
    cis_incident: int
    cis_warning: int


class CiError(Exception):
    """Base class of every CI provider error."""


class CiNotFound(CiError):
    """The CI does not exist, is not readable, or belongs to the other source."""


class CiPermissionDenied(CiError):
    """The user may not perform the operation on the CI."""


class CiReadOnly(CiError):
    """Write attempted on read-only data (never raised for links)."""


class CiSearchTooBroad(CiError):
    """The query would scan too much (statement time limit hit); the caller narrows it."""


class CiUnavailable(CiError):
    """The provider cannot serve (unsupported Znuny version, tables gone, CMDB not installed).

    ``code``: ``"unsupported_znuny_itsm_version"`` | ``"znuny_itsm_missing"`` |
    ``"cmdb_not_installed"``.
    """

    code: str

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


@runtime_checkable
class CiProvider(Protocol):
    source: CiSource
    read_only: bool

    async def is_empty(self, session: AsyncSession) -> bool: ...

    async def classes(self, session: AsyncSession, *, user_id: int) -> list[CiClassInfo]: ...

    async def deployment_states(self, session: AsyncSession) -> list[CatalogValue]: ...

    async def incident_states(self, session: AsyncSession) -> list[CatalogValue]: ...

    async def search(self, session: AsyncSession, query: CiQuery, *, user_id: int) -> CiPage: ...

    async def get(
        self,
        session: AsyncSession,
        ref: CiRef,
        *,
        user_id: int,
        version_id: int | None = None,
    ) -> CiDetail: ...

    async def get_many(
        self, session: AsyncSession, refs: Sequence[CiRef], *, user_id: int
    ) -> dict[CiRef, CiSummary]:  # unreadable/missing omitted
        ...

    async def versions(
        self, session: AsyncSession, ref: CiRef, *, user_id: int, limit: int, offset: int
    ) -> tuple[list[CiVersionInfo], int]: ...

    async def ticket_links(
        self, session: AsyncSession, ticket_ids: Sequence[int], *, user_id: int
    ) -> dict[int, list[CiTicketLink]]: ...

    async def ci_ticket_ids(
        self, session: AsyncSession, ref: CiRef, *, user_id: int
    ) -> list[int]:  # caller filters by ticket ro
        ...

    async def ticket_link_types(self, session: AsyncSession) -> list[LinkTypeOption]: ...

    async def link_ticket(
        self, session: AsyncSession, *, ticket_id: int, ref: CiRef, link_type: str, user_id: int
    ) -> bool: ...

    async def unlink_ticket(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        ref: CiRef,
        link_type: str | None,
        user_id: int,
    ) -> int: ...

    async def service_incident_states(
        self, session: AsyncSession, service_ids: Sequence[int]
    ) -> dict[int, ServiceIncidentState]: ...

    async def service_cis(
        self, session: AsyncSession, service_id: int, *, user_id: int, limit: int = 100
    ) -> list[CiSummary]: ...


__all__ = [
    "CatalogValue",
    "CiAttribute",
    "CiClassInfo",
    "CiDetail",
    "CiError",
    "CiNotFound",
    "CiPage",
    "CiPermissionDenied",
    "CiProvider",
    "CiQuery",
    "CiReadOnly",
    "CiRef",
    "CiSearchTooBroad",
    "CiSource",
    "CiSummary",
    "CiTicketLink",
    "CiUnavailable",
    "CiVersionInfo",
    "LinkTypeOption",
    "ServiceIncidentState",
]
