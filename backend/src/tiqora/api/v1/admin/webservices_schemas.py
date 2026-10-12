"""API models of the GenericInterface webservices admin API (G15).

None of these models can carry a secret: user names, passwords, tokens and header values of the
webservice configuration are never copied into a response field.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from tiqora.domain.schemas import UtcDateTime
from tiqora.gi.ws_settings import validate_targets
from tiqora.jobs.schemas import JobAccepted


class InvokerEventOut(BaseModel):
    event: str
    asynchronous: bool
    has_condition: bool
    fired_by_tiqora: bool


class InvokerOut(BaseModel):
    name: str
    type: str
    supported: bool
    events: list[InvokerEventOut]
    mapping_outbound: str | None
    mapping_inbound: str | None
    problem: str | None


class RequesterTransportOut(BaseModel):
    type: str
    endpoint: str | None
    auth_type: str | None
    has_proxy: bool
    uses_client_certificate: bool
    insecure_tls_requested: bool


class ErrorHandlingOut(BaseModel):
    key: str
    type: str
    invoker_filter: list[str]
    error_stage_filter: list[str]
    error_message_content_filter: str | None
    stop_after_match: str | None
    retry_summary: str | None


class WebserviceEgressOut(BaseModel):
    allowed_targets: list[str]
    allow_insecure_tls: bool


class WebserviceEgressIn(BaseModel):
    allowed_targets: list[str] = Field(default_factory=list)
    allow_insecure_tls: bool = False

    @field_validator("allowed_targets")
    @classmethod
    def _valid_targets(cls, value: list[str]) -> list[str]:
        return list(validate_targets(value))


class WebserviceListItemOut(BaseModel):
    id: int
    name: str
    valid: bool
    has_provider: bool
    invoker_count: int
    event_invoker_count: int
    transport: str | None
    problem: str | None


class WebserviceListOut(BaseModel):
    webservices: list[WebserviceListItemOut]


class WebserviceDetailOut(BaseModel):
    id: int
    name: str
    valid: bool
    problem: str | None
    requester_transport: RequesterTransportOut | None
    invokers: list[InvokerOut]
    error_handling: list[ErrorHandlingOut]
    debug_threshold: str
    egress: WebserviceEgressOut
    warnings: list[str]


class DebugEntryOut(BaseModel):
    source: Literal["tiqora", "znuny"]
    id: int
    communication_id: str
    communication_type: str
    name: str | None
    remote_ip: str | None
    ticket_id: int | None
    created: UtcDateTime
    max_level: str
    can_resend: bool


class DebugEntryListOut(BaseModel):
    entries: list[DebugEntryOut]
    next_before: str | None


class DebugContentOut(BaseModel):
    id: int
    debug_level: str
    subject: str
    content: str
    created: UtcDateTime
    masked: bool  # Tiqora hid a secret in this line ("Von Tiqora maskiert")


class DebugEntryDetailOut(BaseModel):
    entry: DebugEntryOut
    contents: list[DebugContentOut]


class DryRunIn(BaseModel):
    ticket_id: int = Field(gt=0)
    article_id: int | None = Field(default=None, gt=0)
    event: str = Field(default="TicketCreate", min_length=1, max_length=200)


class JobRefOut(JobAccepted):
    """202 body of resend and dry run: poll ``GET /api/v1/jobs/{job_id}``."""
