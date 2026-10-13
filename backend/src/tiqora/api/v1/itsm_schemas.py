"""Schemas of ``/api/v1/itsm``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tiqora.api.v1.admin.itsm_schemas import LinkPropagation
from tiqora.domain.schemas import UtcDateTime
from tiqora.itsm.ci_provider import ServiceIncidentState
from tiqora.itsm.priority import MatrixView
from tiqora.itsm.services import ServiceItsmInfo


class LinkTypeOptionOut(BaseModel):
    """A link type offered for an object pair. Labels: override, else i18n, else Znuny's name."""

    name: str
    source_label: str | None
    target_label: str | None
    source_name: str | None
    target_name: str | None
    pointed: bool
    propagation: LinkPropagation
    origin: Literal["znuny", "tiqora"]


# --- CI search, detail and ticket links (Task 14) ------------------------------------------


class ItsmZnunyInfoOut(BaseModel):
    """What of Znuny's ITSM is installed (same facts as the admin page, without the change flag)."""

    core_installed: bool
    cmdb_installed: bool
    profile: str | None
    packages: dict[str, str]


class ItsmStatusOut(BaseModel):
    """``GET /itsm/status`` (no gate): what the UI may offer right now."""

    mode: Literal["off", "znuny_readthrough", "tiqora"]
    #: who set the mode: the automatic default, an admin or the importer
    source: Literal["auto", "admin", "importer"]
    #: a CI provider answers (mode on, its tables usable)
    ci_available: bool
    ci_read_only: bool
    #: ``znuny`` (Mode A) or ``tiqora`` (Mode B); ``None`` while no provider answers
    ci_source: Literal["znuny", "tiqora"] | None
    #: ``unsupported_znuny_itsm_version`` | ``znuny_itsm_missing`` | ``cmdb_not_installed``
    unavailable_code: str | None
    znuny: ItsmZnunyInfoOut
    #: the ticket types, the workaround state and the criticality/impact fields exist and are valid
    setup_complete: bool
    #: names of the dynamic fields the priority matrix reads (admin setting, default
    #: ``ITSMCriticality`` / ``ITSMImpact``); forms that suggest the matrix priority use them
    criticality_field: str
    impact_field: str


class CatalogValueOut(BaseModel):
    key: str
    name: str
    functionality: str | None


class CiClassOut(BaseModel):
    key: str
    name: str
    icon: str | None
    can_read: bool
    can_write: bool


class CatalogOut(BaseModel):
    classes: list[CiClassOut]
    deployment_states: list[CatalogValueOut]
    incident_states: list[CatalogValueOut]


class CiSummaryOut(BaseModel):
    #: ``"znuny:123"`` — the form every CI link in the API and the UI uses
    ref: str
    source: Literal["znuny", "tiqora"]
    id: int
    number: str
    name: str
    class_key: str
    class_name: str
    deployment_state: CatalogValueOut | None
    incident_state: CatalogValueOut | None
    changed_at: UtcDateTime | None
    edit_url: str | None


class CiPageOut(BaseModel):
    items: list[CiSummaryOut]
    #: exact unless ``truncated``
    total: int
    truncated: bool


class CiAttributeOut(BaseModel):
    key: str
    label: str
    kind: str
    display: str
    raw: str | None
    ref: str | None
    children: list[CiAttributeOut] = []


class CiVersionOut(BaseModel):
    id: int
    seq: int
    name: str
    deployment_state: CatalogValueOut | None
    incident_state: CatalogValueOut | None
    created_at: UtcDateTime
    created_by: int | None
    created_by_name: str | None
    change_note: str | None


class CiDetailOut(BaseModel):
    summary: CiSummaryOut
    version: CiVersionOut | None
    attributes: list[CiAttributeOut]
    definition_version: int | None
    read_only: bool


class CiVersionPageOut(BaseModel):
    items: list[CiVersionOut]
    total: int


class CiTicketLinkOut(BaseModel):
    ticket_id: int
    ci: CiSummaryOut
    link_type: str
    #: the ticket's side of the link; ``both`` for unpointed types
    direction: Literal["source", "target", "both"]
    created_at: UtcDateTime | None


class CiTicketLinkIn(BaseModel):
    #: ``"znuny:12"``
    ci: str = Field(min_length=3, max_length=40)
    #: default: ``ItsmSettings.ci_ticket_link_type``
    link_type: str | None = Field(None, max_length=200)


# --- priority matrix (Task 16) -------------------------------------------------------------


class MatrixCellOut(BaseModel):
    criticality: str
    impact: str
    priority_id: int


class MatrixOut(BaseModel):
    """The criticality x impact grid; source "znuny" is ``cip_allocate`` (read-only)."""

    source: Literal["znuny", "tiqora"]
    editable: bool
    #: whether the matrix is applied to tickets (setting ``priority_matrix_enabled``, mode not off)
    enabled: bool
    criticality_values: list[str]
    impact_values: list[str]
    cells: list[MatrixCellOut]


class PrioritySuggestionOut(BaseModel):
    priority_id: int | None


def matrix_out(view: MatrixView, *, enabled: bool) -> MatrixOut:
    return MatrixOut(
        source=view.source,
        editable=view.editable,
        enabled=enabled,
        criticality_values=list(view.criticality_values),
        impact_values=list(view.impact_values),
        cells=[
            MatrixCellOut(criticality=c.criticality, impact=c.impact, priority_id=c.priority_id)
            for c in view.cells
        ],
    )


# --- services (Task 17) --------------------------------------------------------------------------


class ServiceIncidentStateOut(BaseModel):
    service_id: int
    #: worst functionality over the service's CIs; None = no CI linked / no provider
    state: CatalogValueOut | None
    cis_incident: int
    cis_warning: int


class ServiceItsmOut(BaseModel):
    service_id: int
    name: str
    service_type: str | None
    criticality: str | None
    #: Mode A: type and criticality are Znuny's and cannot be changed here
    znuny_owned: bool
    owner_user_id: int | None
    owner_name: str | None
    description: str | None
    portal_visible: bool
    #: own value; None = settings default
    min_minutes_between_incidents: int | None
    #: the window actually applied (own value or default); 0 = off
    effective_min_minutes_between_incidents: int
    #: Mode A: SLA id (as string key) -> minimum minutes between incidents
    znuny_sla_min_times: dict[str, int]
    incident_state: ServiceIncidentStateOut | None


class RecentIncidentOut(BaseModel):
    ticket_id: int
    tn: str
    title: str
    type_name: str
    created_at: UtcDateTime
    minutes_ago: int
    window_minutes: int


def incident_state_out(s: ServiceIncidentState) -> ServiceIncidentStateOut:
    value = s.state
    return ServiceIncidentStateOut(
        service_id=s.service_id,
        state=None
        if value is None
        else CatalogValueOut(key=value.key, name=value.name, functionality=value.functionality),
        cis_incident=s.cis_incident,
        cis_warning=s.cis_warning,
    )


def service_out(info: ServiceItsmInfo, *, default_minutes: int) -> ServiceItsmOut:
    own = info.min_minutes_between_incidents
    return ServiceItsmOut(
        service_id=info.service_id,
        name=info.name,
        service_type=info.service_type,
        criticality=info.criticality,
        znuny_owned=info.znuny_owned,
        owner_user_id=info.owner_user_id,
        owner_name=info.owner_name,
        description=info.description,
        portal_visible=info.portal_visible,
        min_minutes_between_incidents=own,
        effective_min_minutes_between_incidents=default_minutes if own is None else own,
        znuny_sla_min_times={str(k): v for k, v in info.znuny_sla_min_times.items()},
        incident_state=None
        if info.incident_state is None
        else incident_state_out(info.incident_state),
    )


# --- note / reply to linked tickets (Task 18) ----------------------------------------------


class LinkedArticlesIn(BaseModel):
    target_ticket_ids: list[int] = Field(min_length=1)
    #: Internal notes only. A customer e-mail to every linked ticket is not offered here: the only
    #: bulk customer reply is the major-incident update, behind its own admin setting.
    mode: Literal["note"] = "note"
    subject: str = Field(min_length=1, max_length=250)
    body_html: str = Field(min_length=1, max_length=200_000)
    visible_for_customer: bool = False


# --- problem management (Task 19) ------------------------------------------------------------


class ProblemIn(BaseModel):
    root_cause: str | None = Field(None, max_length=20000)
    workaround: str | None = Field(None, max_length=20000)


class LinkedIncidentOut(BaseModel):
    ticket_id: int
    tn: str
    title: str
    type_name: str
    state: str
    state_type: str
    closed: bool


class ProblemOut(BaseModel):
    ticket_id: int
    root_cause: str | None
    workaround: str | None
    known_error: bool
    known_error_article_id: int | None
    #: state of the KB article (``review`` until a reviewer publishes it)
    known_error_article_state: str | None
    known_error_since: UtcDateTime | None
    #: linked incidents the caller may read
    incidents: list[LinkedIncidentOut]
    change_time: UtcDateTime | None
    change_by: int | None


class ProblemIncidentsIn(BaseModel):
    incident_ticket_ids: list[int] = Field(min_length=1, max_length=50)


class CloseIncidentsIn(BaseModel):
    #: the note written on every incident; a default text when omitted
    note_html: str | None = Field(None, max_length=200_000)


# --- major incident (Task 20) ---------------------------------------------------------------


class MajorIncidentDeclareIn(BaseModel):
    summary: str | None = Field(None, max_length=500)


class MajorIncidentUpdateIn(BaseModel):
    #: internal = note on the major ticket only; note = + note on linked tickets;
    #: reply = + e-mail reply to the customers of linked tickets (needs the admin setting)
    mode: Literal["internal", "note", "reply"]
    body_html: str = Field(min_length=1, max_length=200_000)


class MajorTimelineEntryOut(BaseModel):
    kind: Literal["declared", "update", "ended"]
    at: UtcDateTime
    by: int
    update_id: int | None = None
    mode: Literal["internal", "note", "reply"] | None = None
    #: sanitised HTML (updates only)
    body: str | None = None
    target_count: int = 0
    job_id: int | None = None
    summary: str | None = None


class MajorIncidentOut(BaseModel):
    ticket_id: int
    active: bool
    summary: str | None
    declared_at: UtcDateTime
    declared_by: int
    ended_at: UtcDateTime | None
    ended_by: int | None
    timeline: list[MajorTimelineEntryOut]


class MajorIncidentUpdateOut(BaseModel):
    update: MajorTimelineEntryOut
    job_id: int | None = None


# --- ticket ITSM summary and link graph (Task 21) ------------------------------------------


class ProblemRefOut(BaseModel):
    ticket_id: int
    tn: str
    title: str
    known_error: bool


class MajorBannerOut(BaseModel):
    """An active major incident the ticket is linked to (and the caller may read)."""

    ticket_id: int
    tn: str
    title: str
    summary: str | None
    declared_at: UtcDateTime


class TicketItsmDialogsOut(BaseModel):
    #: ``AgentTicketAddtlITSMField`` has at least one configured, usable field
    itsm_fields: bool
    #: ``AgentTicketDecision`` has at least one configured, usable field
    decision: bool


class TicketItsmOut(BaseModel):
    """``GET /tickets/{id}/itsm``: ``enabled=false`` while ITSM is off or not set up."""

    enabled: bool
    mode: Literal["off", "znuny_readthrough", "tiqora"]
    role: (
        Literal["incident", "major_incident", "service_request", "problem", "change", "change_task"]
        | None
    )
    criticality: str | None
    impact: str | None
    matrix_priority_id: int | None
    service: ServiceItsmOut | None
    major_incident: MajorIncidentOut | None
    major_banner: MajorBannerOut | None
    problem: ProblemRefOut | None
    #: ``None`` while no CI provider serves
    ci_count: int | None
    recent_incident: RecentIncidentOut | None
    dialogs: TicketItsmDialogsOut
    #: ``known_error_kb_category_id`` is set (the known-error button is offered)
    known_error_category_configured: bool
    #: ``major_incident_customer_reply`` is on (the "Antwort an Kunden" update mode is offered)
    major_incident_customer_reply: bool


class LinkGraphNodeOut(BaseModel):
    #: ``ticket:123`` or the CI reference form ``znuny:45``
    key: str
    kind: Literal["ticket", "ci"]
    depth: int
    number: str
    title: str
    state: str | None
    state_type: str | None
    class_name: str | None
    incident_state: str | None
    incident_functionality: str | None


class LinkGraphEdgeOut(BaseModel):
    source: str
    target: str
    link_type: str
    kind: Literal["ticket_ticket", "ticket_ci"]


class LinkGraphOut(BaseModel):
    root: str
    nodes: list[LinkGraphNodeOut]
    edges: list[LinkGraphEdgeOut]
    #: the node cap was hit; some linked tickets or CIs are not shown
    truncated: bool
