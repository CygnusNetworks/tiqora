"""Public wallboard API (E9): the only unauthenticated part of the dashboards.

``/api/v1/kiosk`` has no ``CurrentUser`` and ignores cookies and bearer tokens: the link's
secret travels in the ``X-Tiqora-Kiosk`` header (the SPA reads it from the URL fragment, so it
never reaches an access log). Order per request: per-IP backstop (Redis, fail closed), link
resolution (identical 404 for every invalid state, misses are rate-limited per IP hash), the
``last_used_at`` stamp, then the board or the data. The data cache is consulted only after
resolution, so a revocation applies on the next poll.
"""

from __future__ import annotations

from typing import Annotated, Final, NoReturn

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.api.deps import AppSettings, DbSession, RedisDep, get_session_factory_dep
from tiqora.api.v1.dashboards import DashboardRegistry
from tiqora.dashboard import kiosk, store
from tiqora.dashboard.schemas import DashboardDataOut, DashboardOut, KioskBoardOut
from tiqora.jobs.service import utc_now
from tiqora.security.ratelimit import client_ip
from tiqora.webchat.ratelimit import ChatBackendUnavailable, ChatRateLimiter, RateLimited
from tiqora.webchat.tokens import hash_ip

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/kiosk", tags=["kiosk"])

KIOSK_HEADER: Final = "X-Tiqora-Kiosk"
IP_SCOPE: Final = "kiosk_ip"
IP_LIMIT: Final = 240
IP_WINDOW: Final = 60
MISS_SCOPE: Final = "kiosk_miss"
MISS_LIMIT: Final = 30
MISS_WINDOW: Final = 600
MAX_TOKEN_LENGTH: Final = 200
MSG_RATE_LIMITED: Final = "Zu viele Anfragen. Bitte versuchen Sie es später erneut."
MSG_UNAVAILABLE: Final = "Der Dienst ist vorübergehend nicht verfügbar."
#: every response, errors included
HEADERS: Final = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory_dep)]
DashboardId = Annotated[int, Path(ge=1, le=2**63 - 1)]
KioskToken = Annotated[str | None, Header(alias=KIOSK_HEADER, max_length=1000)]


def _fail(code: int, message: str, **extra: str) -> NoReturn:
    raise HTTPException(code, {"message": message}, headers={**HEADERS, **extra})


async def _limit(
    limiter: ChatRateLimiter, scope: str, subject: str, limit: int, window: int
) -> None:
    try:
        await limiter.hit(scope, subject, limit=limit, window_seconds=window)
    except RateLimited as exc:
        _fail(
            status.HTTP_429_TOO_MANY_REQUESTS,
            MSG_RATE_LIMITED,
            **{"Retry-After": str(exc.retry_after)},
        )
    except ChatBackendUnavailable:
        _fail(status.HTTP_503_SERVICE_UNAVAILABLE, MSG_UNAVAILABLE, **{"Retry-After": "30"})


async def _gate(
    request: Request,
    response: Response,
    session: AsyncSession,
    redis: Redis,
    settings: AppSettings,
    token: str | None,
) -> kiosk.ResolvedLink:
    """Backstop, resolution and ``last_used_at``; every failure is the same 404."""
    for k, v in HEADERS.items():
        response.headers[k] = v
    limiter = ChatRateLimiter(redis)
    ip_hash = hash_ip(client_ip(request, settings.trusted_proxies), settings.secret_key)
    await _limit(limiter, IP_SCOPE, ip_hash, IP_LIMIT, IP_WINDOW)
    now = utc_now()
    link = None
    if token and len(token) <= MAX_TOKEN_LENGTH:
        link = await kiosk.resolve_link(session, token, now)
    if link is None:
        await session.rollback()
        await _limit(limiter, MISS_SCOPE, ip_hash, MISS_LIMIT, MISS_WINDOW)
        logger.info("dashboard_kiosk_miss")
        _fail(status.HTTP_404_NOT_FOUND, kiosk.MSG_INVALID_LINK)
    await kiosk.touch_link(session, link.id, now)
    await session.commit()
    return link


@router.get("/board", response_model=KioskBoardOut)
async def kiosk_board(
    request: Request,
    response: Response,
    session: DbSession,
    redis: RedisDep,
    settings: AppSettings,
    token: KioskToken = None,
) -> KioskBoardOut:
    """The link's dashboards in rotation order, widgets reduced to what rendering needs."""
    link = await _gate(request, response, session, redis, settings, token)
    boards: list[DashboardOut] = []
    try:
        for dashboard_id in link.dashboard_ids:
            try:
                boards.append(
                    await store.get_dashboard(session, dashboard_id, link.created_by, admin=True)
                )
            except store.DashboardNotFound:  # lost between resolution and now
                continue
    finally:
        await session.rollback()
    if not boards:
        _fail(status.HTTP_404_NOT_FOUND, kiosk.MSG_INVALID_LINK)
    return kiosk.board_out(link, boards, utc_now())


@router.get("/dashboards/{dashboard_id}/data", response_model=DashboardDataOut)
async def kiosk_dashboard_data(
    dashboard_id: DashboardId,
    request: Request,
    response: Response,
    session: DbSession,
    redis: RedisDep,
    settings: AppSettings,
    factory: SessionFactory,
    registry: DashboardRegistry,
    token: KioskToken = None,
) -> DashboardDataOut:
    """Redacted widget data, computed as the link's creator (cached for 20 s)."""
    link = await _gate(request, response, session, redis, settings, token)
    if dashboard_id not in link.dashboard_ids:
        await session.rollback()
        _fail(status.HTTP_404_NOT_FOUND, kiosk.MSG_INVALID_LINK)
    try:
        board = await store.get_dashboard(session, dashboard_id, link.created_by, admin=True)
    except store.DashboardNotFound:
        _fail(status.HTTP_404_NOT_FOUND, kiosk.MSG_INVALID_LINK)
    finally:
        await session.rollback()  # providers open their own sessions
    return await kiosk.kiosk_data(factory, registry, redis, link, board, settings)
