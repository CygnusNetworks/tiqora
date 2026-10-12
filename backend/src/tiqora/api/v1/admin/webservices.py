"""Admin API for GenericInterface webservices - ``/api/v1/admin/webservices`` (G15).

Read-only view of the Znuny webservice configuration, the Tiqora-side egress settings (which
internal targets may be reached, whether TLS checks may be relaxed), the debugger (Tiqora and
Znuny rows), "resend" and "dry run". No handler here performs an outbound call: resend and dry
run queue a ``core.gi_invoke`` job and answer ``202``.

Secrets never leave this module: the detail view copies no user name, password, token or header
value, and every debugger line is masked on read with the secrets of the webservice's own
configuration (Znuny's REST requester logs the raw ``Authorization`` header).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal

import structlog
import yaml
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.mapping.perl import perl_str, perl_truthy
from tiqora.api.compat.webservices import _PARSE_ERRORS, replace_config_placeholders
from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.webservices_schemas import (
    DebugContentOut,
    DebugEntryDetailOut,
    DebugEntryListOut,
    DebugEntryOut,
    DryRunIn,
    ErrorHandlingOut,
    InvokerEventOut,
    InvokerOut,
    JobRefOut,
    RequesterTransportOut,
    WebserviceDetailOut,
    WebserviceEgressIn,
    WebserviceEgressOut,
    WebserviceListItemOut,
    WebserviceListOut,
)
from tiqora.config import get_settings
from tiqora.db.legacy.config import GiWebserviceConfig
from tiqora.gi.config import InvokerConfig, RequesterConfig, parse_requester
from tiqora.gi.debug_store import (
    DebugContentRow,
    DebugEntryRow,
    get_entry,
    get_replay,
    search_entries,
)
from tiqora.gi.events import invoker_can_fire
from tiqora.gi.jobs import (
    GI_INVOKE_KIND,
    GI_INVOKE_SYNC_KIND,
    GiInvokePayload,
    enqueue_invocation,
)
from tiqora.gi.loader import load_gi_webservice
from tiqora.gi.redact import MASK, MIN_SECRET_LENGTH, SECRET_KEY_RE, redact_text, redact_url
from tiqora.gi.ws_settings import WebserviceEgressSettings, get_egress_settings, set_egress_settings
from tiqora.jobs.service import count_active_jobs, lock_user_job_quota
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webservices", tags=["admin:webservices"])

MAX_ACTIVE_GI_JOBS_PER_ADMIN: Final = 10
DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200

PROBLEM_INVALID_YAML: Final = "Die Konfiguration ist kein gültiges YAML."
PROBLEM_UNREADABLE: Final = "Die Konfiguration kann nicht gelesen werden."
PROBLEM_UNSUPPORTED_TYPE: Final = "Der Typ {type} wird von Tiqora nicht unterstützt."
WARNING_CERT_DIRS: Final = (
    "Die Konfiguration verwendet ein Clientzertifikat, aber TIQORA_GI_CERT_DIRS ist nicht "
    "gesetzt. Aufrufe mit Zertifikat schlagen fehl."
)
WARNING_UNSUPPORTED_AUTH: Final = "Die Anmeldung per {auth} wird von Tiqora nicht unterstützt."
WARNING_INSECURE_TLS: Final = (
    "Die Konfiguration verlangt, die Prüfung des Servernamens abzuschalten. Das geht in Tiqora "
    "nur, wenn Sie in den Einstellungen die unsichere TLS-Verbindung erlauben."
)
TOO_MANY_JOBS: Final = "Es laufen bereits zu viele Aufrufe. Bitte warten Sie, bis sie fertig sind."

_AUTH_LINE_RE: Final = re.compile(
    r"""^(?P<head>[ \t]*['"]?(?:proxy-)?authorization['"]?[ \t]*(?:=>|:|=)[ \t]*)(?P<value>.+)$""",
    re.IGNORECASE | re.MULTILINE,
)
_RETRY_KEYS: Final = (
    ("RetryIntervalStart", "Start"),
    ("RetryIntervalFactor", "Faktor"),
    ("RetryIntervalMax", "Höchstabstand"),
    ("RetryCountMax", "Höchstzahl"),
    ("RetryPeriodMax", "Höchstdauer"),
)


# ------------------------------------------------------------------------------ parsing


class _Parsed:
    """One webservice row: raw config (placeholders replaced) and its requester model."""

    __slots__ = ("config", "id", "name", "problem", "requester", "valid")

    def __init__(self, ws_id: int, name: str, valid: bool) -> None:
        self.id = ws_id
        self.name = name
        self.valid = valid
        self.config: dict[str, Any] | None = None
        self.requester: RequesterConfig | None = None
        self.problem: str | None = None


def _text(blob: bytes | memoryview | str) -> str:
    return blob if isinstance(blob, str) else bytes(blob).decode("utf-8")


def _load_yaml(blob: Any) -> dict[str, Any] | None:
    try:
        parsed = yaml.safe_load(_text(blob))
    except _PARSE_ERRORS:
        return None
    return parsed if isinstance(parsed, dict) else None


def _invoker_problem(invoker: InvokerConfig) -> str | None:
    if not invoker.supported:
        return PROBLEM_UNSUPPORTED_TYPE.format(type=invoker.type or "?")
    if invoker.mapping_error:
        return f"Fehler in der Abbildung: {invoker.mapping_error}"
    return None


async def _parse_row(row: Any, sysconfig: SysConfig, huge_xml: bool) -> _Parsed:
    parsed = _Parsed(int(row.id), row.name, int(row.valid_id) == 1)
    raw = await asyncio.to_thread(_load_yaml, row.config)
    if raw is None:
        parsed.problem = PROBLEM_INVALID_YAML
        return parsed
    try:
        config = await replace_config_placeholders(raw, sysconfig)
        parsed.config = config
        if isinstance(config.get("Requester"), dict) and config["Requester"]:
            parsed.requester = await asyncio.to_thread(parse_requester, config, huge_xml=huge_xml)
    except _PARSE_ERRORS:
        parsed.problem = PROBLEM_UNREADABLE
        parsed.config = None
        return parsed
    if parsed.requester is not None:
        problems = [
            f"{name}: {p}"
            for name, inv in parsed.requester.invokers.items()
            if (p := _invoker_problem(inv))
        ]
        if problems:
            parsed.problem = "; ".join(problems)
    return parsed


async def _huge_xml(sysconfig: SysConfig) -> bool:
    return perl_truthy(await sysconfig.get("LibXML::EnableHugeXMLDataSupport"))


_COLUMNS = (
    GiWebserviceConfig.id,
    GiWebserviceConfig.name,
    GiWebserviceConfig.config,
    GiWebserviceConfig.valid_id,
)


async def _load_one(session: AsyncSession, ws_id: int) -> _Parsed:
    row = (await session.execute(select(*_COLUMNS).where(GiWebserviceConfig.id == ws_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webservice nicht gefunden")
    sysconfig = SysConfig(session)
    return await _parse_row(row, sysconfig, await _huge_xml(sysconfig))


# ------------------------------------------------------------------------------ secrets


def _collect_secrets(value: Any, out: set[str], *, secret_context: bool = False) -> None:
    """Every string below a secret-looking key, and every ``AdditionalHeaders`` value."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            if name == "AuthType":
                continue  # names the scheme (BasicAuth, OAuth2Token), not a secret
            secret = (
                secret_context or bool(SECRET_KEY_RE.search(name)) or name == "AdditionalHeaders"
            )
            _collect_secrets(item, out, secret_context=secret)
    elif isinstance(value, list | tuple):
        for item in value:
            _collect_secrets(item, out, secret_context=secret_context)
    elif secret_context and isinstance(value, str | int | float) and not isinstance(value, bool):
        text = perl_str(value)
        if len(text) >= MIN_SECRET_LENGTH:
            out.add(text)


def _secrets_of(config: dict[str, Any] | None) -> set[str]:
    found: set[str] = set()
    if config is None:
        return found
    requester = config.get("Requester")
    transport = requester.get("Transport") if isinstance(requester, dict) else None
    provider = config.get("Provider")
    p_transport = provider.get("Transport") if isinstance(provider, dict) else None
    _collect_secrets(transport, found)
    _collect_secrets(p_transport, found)
    for secret in list(found):
        found.add(base64.b64encode(secret.encode()).decode("ascii"))
    # Znuny logs "Basic " + base64("user:password") (REST.pm:705 proxy, :787 basic auth).
    _collect_credential_tokens(transport, found)
    _collect_credential_tokens(p_transport, found)
    return found


_CREDENTIAL_PAIRS: Final = (("BasicAuthUser", "BasicAuthPassword"), ("ProxyUser", "ProxyPassword"))


def _collect_credential_tokens(value: Any, out: set[str]) -> None:
    """Add base64("user:password") for every user/password pair Znuny turns into a token."""
    if isinstance(value, Mapping):
        for user_key, password_key in _CREDENTIAL_PAIRS:
            if user_key in value and password_key in value:
                pair = f"{perl_str(value[user_key])}:{perl_str(value[password_key])}"
                out.add(base64.b64encode(pair.encode()).decode("ascii"))
        for item in value.values():
            _collect_credential_tokens(item, out)
    elif isinstance(value, list | tuple):
        for item in value:
            _collect_credential_tokens(item, out)


def _mask_line(text: str, secrets: set[str]) -> tuple[str, bool]:
    masked = redact_text(text, secrets)
    masked = _AUTH_LINE_RE.sub(lambda m: f"{m.group('head')}{MASK}", masked)
    return masked, masked != text


# ------------------------------------------------------------------------------ outputs


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mapping_type(config: Any) -> str | None:
    if not isinstance(config, dict) or not config:
        return None
    kind = config.get("Type")
    return perl_str(kind) if kind is not None and not isinstance(kind, dict | list) else None


def _transport_out(requester: RequesterConfig) -> RequesterTransportOut | None:
    transport = requester.transport
    if transport is None:
        return None
    config = transport.config
    auth = _dict(config.get("Authentication"))
    auth_type = perl_str(auth.get("AuthType")) if auth.get("AuthType") else None
    proxy = _dict(config.get("Proxy"))
    x509 = _dict(config.get("X509"))
    ssl_cfg = _dict(config.get("SSL"))
    endpoint_raw = config.get("Host") if transport.type == "HTTP::REST" else config.get("Endpoint")
    endpoint = (
        redact_url(perl_str(endpoint_raw))
        if endpoint_raw is not None and not isinstance(endpoint_raw, dict | list)
        else None
    )
    return RequesterTransportOut(
        type=transport.type,
        endpoint=endpoint,
        auth_type=auth_type,
        has_proxy=perl_str(proxy.get("UseProxy")) == "Yes" and bool(proxy.get("ProxyHost")),
        uses_client_certificate=perl_str(x509.get("UseX509")) == "Yes"
        or perl_str(ssl_cfg.get("UseSSL")) == "Yes",
        insecure_tls_requested=perl_truthy(config.get("SSLNoHostnameVerification")),
    )


def _invoker_out(invoker: InvokerConfig) -> InvokerOut:
    fires = invoker_can_fire(invoker)
    return InvokerOut(
        name=invoker.name,
        type=invoker.type,
        supported=invoker.supported,
        events=[
            InvokerEventOut(
                event=e.event,
                asynchronous=e.asynchronous,
                has_condition=e.condition is not None,
                fired_by_tiqora=fires,
            )
            for e in invoker.events
        ],
        mapping_outbound=_mapping_type(invoker.raw.get("MappingOutbound")),
        mapping_inbound=_mapping_type(invoker.raw.get("MappingInbound")),
        problem=_invoker_problem(invoker),
    )


def _retry_summary(config: dict[str, Any]) -> str | None:
    if perl_str(config.get("Type")) != "RequestRetry":
        return None
    parts = [
        f"{label} {perl_str(config[key])}"
        for key, label in _RETRY_KEYS
        if config.get(key) not in (None, "") and not isinstance(config[key], dict | list)
    ]
    if not perl_truthy(config.get("ScheduleRetry")):
        parts.insert(0, "keine Wiederholung")
    return ", ".join(parts) or None


def _warnings(requester: RequesterConfig | None, egress: WebserviceEgressSettings) -> list[str]:
    if requester is None or requester.transport is None:
        return []
    out: list[str] = []
    transport = _transport_out(requester)
    assert transport is not None
    if transport.uses_client_certificate and not get_settings().gi_cert_dirs:
        out.append(WARNING_CERT_DIRS)
    auth = transport.auth_type or ""
    if auth in ("JWT", "NTLM"):
        out.append(WARNING_UNSUPPORTED_AUTH.format(auth=auth))
    if transport.insecure_tls_requested and not egress.allow_insecure_tls:
        out.append(WARNING_INSECURE_TLS)
    return out


def _egress_out(value: WebserviceEgressSettings) -> WebserviceEgressOut:
    return WebserviceEgressOut(
        allowed_targets=list(value.allowed_targets), allow_insecure_tls=value.allow_insecure_tls
    )


# ------------------------------------------------------------------------------ list / detail


@router.get("", response_model=WebserviceListOut)
async def list_webservices(admin: AdminUser, session: DbSession) -> WebserviceListOut:
    """All webservices, including invalid ones (``valid`` false) and broken configurations."""
    rows = (await session.execute(select(*_COLUMNS).order_by(GiWebserviceConfig.id))).all()
    sysconfig = SysConfig(session)
    huge_xml = await _huge_xml(sysconfig)
    items: list[WebserviceListItemOut] = []
    for row in rows:
        parsed = await _parse_row(row, sysconfig, huge_xml)
        provider = _dict(_dict(parsed.config).get("Provider"))
        requester = parsed.requester
        items.append(
            WebserviceListItemOut(
                id=parsed.id,
                name=parsed.name,
                valid=parsed.valid,
                has_provider=bool(_dict(provider.get("Operation"))),
                invoker_count=len(requester.invokers) if requester else 0,
                event_invoker_count=sum(
                    1 for i in requester.invokers.values() if i.events and invoker_can_fire(i)
                )
                if requester
                else 0,
                transport=(requester.transport.type or None)
                if requester and requester.transport
                else None,
                problem=parsed.problem,
            )
        )
    return WebserviceListOut(webservices=items)


@router.get("/{ws_id}", response_model=WebserviceDetailOut)
async def get_webservice(ws_id: int, admin: AdminUser, session: DbSession) -> WebserviceDetailOut:
    parsed = await _load_one(session, ws_id)
    egress = await get_egress_settings(session, ws_id)
    requester = parsed.requester
    return WebserviceDetailOut(
        id=parsed.id,
        name=parsed.name,
        valid=parsed.valid,
        problem=parsed.problem,
        requester_transport=_transport_out(requester) if requester else None,
        invokers=[_invoker_out(i) for i in requester.invokers.values()] if requester else [],
        error_handling=[
            ErrorHandlingOut(
                key=m.key,
                type=m.type,
                invoker_filter=list(m.invoker_filter),
                error_stage_filter=list(m.error_stage_filter),
                error_message_content_filter=m.error_message_content_filter,
                stop_after_match=m.stop_after_match,
                retry_summary=_retry_summary(m.config),
            )
            for m in requester.error_handling
        ]
        if requester
        else [],
        debug_threshold=requester.debugger.threshold if requester else "error",
        egress=_egress_out(egress),
        warnings=_warnings(requester, egress),
    )


@router.put("/{ws_id}/egress", response_model=WebserviceEgressOut)
async def put_egress(
    ws_id: int, body: WebserviceEgressIn, admin: AdminUser, session: DbSession
) -> WebserviceEgressOut:
    await _require_exists(session, ws_id)
    value = WebserviceEgressSettings(
        allowed_targets=tuple(body.allowed_targets), allow_insecure_tls=body.allow_insecure_tls
    )
    await set_egress_settings(session, ws_id, value)  # commits
    logger.info("gi_egress_settings_changed", webservice_id=ws_id, admin_id=admin.id)
    return _egress_out(await get_egress_settings(session, ws_id))


# ------------------------------------------------------------------------------ debugger


def _entry_out(row: DebugEntryRow) -> DebugEntryOut:
    return DebugEntryOut(
        source=row.source,
        id=row.id,
        communication_id=row.communication_id,
        communication_type=row.communication_type,
        name=row.name,
        remote_ip=row.remote_ip,
        ticket_id=row.ticket_id,
        created=row.create_time,
        max_level=row.max_level,
        can_resend=row.can_resend,
    )


def _encode_cursor(row: DebugEntryRow) -> str:
    raw = json.dumps([row.create_time.isoformat(), row.source, row.id])
    return base64.urlsafe_b64encode(raw.encode()).decode("ascii").rstrip("=")


def _decode_cursor(raw: str) -> tuple[datetime, str, int]:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        when, source, entry_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if source not in ("tiqora", "znuny"):
            raise ValueError(source)
        moment = datetime.fromisoformat(when)
        if moment.tzinfo is not None:
            # create_time columns are naive UTC; an aware value fails to bind on PostgreSQL
            moment = moment.astimezone(UTC).replace(tzinfo=None)
        return moment, str(source), int(entry_id)
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Ungültige Seitenangabe"
        ) from exc


def _naive_utc(moment: datetime | None) -> datetime | None:
    if moment is None or moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


async def _require_exists(session: AsyncSession, ws_id: int) -> None:
    found = (
        await session.execute(select(GiWebserviceConfig.id).where(GiWebserviceConfig.id == ws_id))
    ).first()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webservice nicht gefunden")


@router.get("/{ws_id}/debugger", response_model=DebugEntryListOut)
async def list_debugger(
    ws_id: int,
    admin: AdminUser,
    session: DbSession,
    type_: Annotated[Literal["Provider", "Requester"] | None, Query(alias="type")] = None,
    source: Literal["tiqora", "znuny"] | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    remote_ip: Annotated[str | None, Query(max_length=50)] = None,
    before: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> DebugEntryListOut:
    await _require_exists(session, ws_id)
    cursor = _decode_cursor(before) if before else None
    rows = await search_entries(
        session,
        webservice_id=ws_id,
        communication_type=type_,
        created_after=_naive_utc(from_),
        created_before=_naive_utc(to),
        remote_ip=remote_ip,
        source=source,
        limit=limit + 1,
        before=cursor,
    )
    page = rows[:limit]
    return DebugEntryListOut(
        entries=[_entry_out(r) for r in page],
        next_before=_encode_cursor(page[-1]) if len(rows) > limit and page else None,
    )


@router.get("/{ws_id}/debugger/{source}/{entry_id}", response_model=DebugEntryDetailOut)
async def get_debugger_entry(
    ws_id: int,
    source: Literal["tiqora", "znuny"],
    entry_id: int,
    admin: AdminUser,
    session: DbSession,
) -> DebugEntryDetailOut:
    parsed = await _load_one(session, ws_id)
    found = await get_entry(session, source=source, entry_id=entry_id, webservice_id=ws_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Protokolleintrag nicht gefunden")
    row, lines = found
    secrets = _secrets_of(parsed.config)
    return DebugEntryDetailOut(
        entry=_entry_out(row), contents=[_content_out(line, secrets) for line in lines]
    )


def _content_out(line: DebugContentRow, secrets: set[str]) -> DebugContentOut:
    content, masked_content = _mask_line(line.content or "", secrets)
    subject, masked_subject = _mask_line(line.subject, secrets)
    return DebugContentOut(
        id=line.id,
        debug_level=line.debug_level,
        subject=subject,
        content=content,
        created=line.create_time,
        masked=masked_content or masked_subject,
    )


# ------------------------------------------------------------------------------ jobs


async def _enqueue_limited(
    session: AsyncSession, admin_id: int, payload: GiInvokePayload
) -> JobRefOut:
    """Quota check and enqueue in one transaction (the user row lock serialises the cap)."""
    await session.rollback()  # the quota lock must be the first statement of its transaction
    await lock_user_job_quota(session, admin_id)
    active = 0
    for kind in (GI_INVOKE_KIND, GI_INVOKE_SYNC_KIND):
        active += await count_active_jobs(session, kind=kind, created_by=admin_id)
    if active >= MAX_ACTIVE_GI_JOBS_PER_ADMIN:
        await session.rollback()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY_JOBS)
    job = await enqueue_invocation(session, payload=payload, created_by=admin_id)
    job_id, job_status = int(job.id), job.status
    await session.commit()
    return JobRefOut(job_id=job_id, status="running" if job_status == "running" else "queued")


async def _require_valid_invoker(session: AsyncSession, ws_id: int, invoker: str) -> None:
    ws = await load_gi_webservice(session, SysConfig(session), ws_id)
    if ws is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Der Webservice ist nicht gültig.")
    if invoker not in ws.requester.invokers:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aufruf (Invoker) nicht gefunden")


@router.post(
    "/{ws_id}/debugger/tiqora/{entry_id}/resend",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobRefOut,
)
async def resend(ws_id: int, entry_id: int, admin: AdminUser, session: DbSession) -> JobRefOut:
    """Run the recorded call again with the stored event data and the CURRENT ticket data."""
    await _require_exists(session, ws_id)
    found = await get_entry(session, source="tiqora", entry_id=entry_id, webservice_id=ws_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Protokolleintrag nicht gefunden")
    if not found[0].can_resend:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Dieser Eintrag kann nicht erneut gesendet werden."
        )
    replay = await get_replay(session, entry_id=entry_id, webservice_id=ws_id)
    invoker = replay.get("invoker") if replay else None
    data = replay.get("data") if replay else None
    if not isinstance(invoker, str) or not isinstance(data, dict):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Dieser Eintrag kann nicht erneut gesendet werden."
        )
    await _require_valid_invoker(session, ws_id, invoker)
    payload = GiInvokePayload(
        webservice_id=ws_id, invoker=invoker, data=data, asynchronous=True, origin="resend"
    )
    return await _enqueue_limited(session, admin.id, payload)


@router.post(
    "/{ws_id}/invokers/{invoker}/dry-run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobRefOut,
)
async def dry_run(
    ws_id: int, invoker: str, body: DryRunIn, admin: AdminUser, session: DbSession
) -> JobRefOut:
    """Build the request exactly as a real call would and show it masked; nothing is sent."""
    await _require_exists(session, ws_id)
    await _require_valid_invoker(session, ws_id, invoker)
    data: dict[str, Any] = {"TicketID": body.ticket_id, "Event": body.event}
    if body.article_id is not None:
        data["ArticleID"] = body.article_id
    payload = GiInvokePayload(
        webservice_id=ws_id,
        invoker=invoker,
        data=data,
        asynchronous=True,
        origin="dry_run",
        dry_run=True,
    )
    return await _enqueue_limited(session, admin.id, payload)
