"""Static web-chat widget routes: ``loader.js``, the iframe document and hashed assets (C9).

The frame document carries its own ENFORCED ``Content-Security-Policy`` (built by
``frame_csp`` from the allowed websites of the chat configuration); the global Report-Only
header of ``security_headers_middleware`` is skipped for ``/chat/v1/``. A missing widget
build never breaks app start: the routes answer 404.

Check order of ``/chat/v1/frame`` (R-PREAUTH): per-IP backstop (router dependency, before any
DB access) -> origin parameter / preview decision (pure) -> channel switch + config from the
in-process cache -> allowed origins -> Referer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import structlog
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from tiqora.api.deps import AppSettings, DbSession
from tiqora.config import Settings
from tiqora.webchat.config_cache import public_config_cache
from tiqora.webchat.origins import (
    OriginError,
    canonical_origin,
    effective_allowed_origins,
    frame_csp,
    origin_allowed,
    own_origin,
    preview_request_allowed,
    referer_origin,
)
from tiqora.webchat.public_api import public_backstop

logger = structlog.get_logger(__name__)

ASSETS_URL_PATH: Final = "/chat/v1/assets"
_ASSET_CACHE: Final = "public, max-age=31536000, immutable"
NONE_CSP: Final = "default-src 'none'; frame-ancestors 'none'"

router = APIRouter(
    prefix="/chat/v1",
    tags=["webchat-static"],
    include_in_schema=False,
    dependencies=[Depends(public_backstop)],
)

# path -> (mtime_ns, bytes); the frame document is read once per build.
_frame_cache: dict[str, tuple[int, bytes]] = {}


def _dist(settings: Settings) -> Path:
    return Path(settings.webchat_dist_dir)


def _refusal(status: int, text: str) -> Response:
    """Tiny German refusal; ``frame-ancestors 'none'`` so nobody can frame it."""
    return PlainTextResponse(
        text,
        status_code=status,
        headers={
            "Content-Security-Policy": NONE_CSP,
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex",
        },
    )


def _not_found() -> Response:
    return _refusal(404, "Chat nicht verfügbar.")


def _refused() -> Response:
    return _refusal(403, "Dieser Chat darf auf dieser Website nicht eingebunden werden.")


def _unavailable() -> Response:
    """DB outage: never cached, never framed, retry soon."""
    return PlainTextResponse(
        "Chat vorübergehend nicht verfügbar.",
        status_code=503,
        headers={
            "Content-Security-Policy": NONE_CSP,
            "Cache-Control": "no-store",
            "Retry-After": "30",
        },
    )


def _read_cached(path: Path) -> bytes | None:
    """File bytes, re-read only when the mtime changed; ``None`` when absent."""
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        _frame_cache.pop(str(path), None)
        return None
    hit = _frame_cache.get(str(path))
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        data = path.read_bytes()
    except OSError:
        return None
    _frame_cache[str(path)] = (mtime, data)
    return data


@router.api_route("/loader.js", methods=["GET", "HEAD"])
async def widget_loader(settings: AppSettings, db: DbSession) -> Response:
    """The host-page loader script (cacheable for 5 minutes)."""
    path = _dist(settings) / "loader.js"
    if not path.is_file():
        return _not_found()
    try:
        enabled = await public_config_cache.channel_enabled(db)
        await db.rollback()
    except Exception:  # noqa: BLE001 - a DB outage must not turn into a cacheable answer
        logger.warning("webchat_loader_channel_check_failed", exc_info=True)
        return _unavailable()
    if not enabled:
        return _not_found()
    try:
        data = path.read_bytes()
    except OSError:
        return _not_found()
    return Response(
        data,
        media_type="text/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=300",
            "Cross-Origin-Resource-Policy": "cross-origin",
        },
    )


async def _valid_config_allowed(
    db: AsyncSession, key: str | None, own: str, *, preview: bool = False
) -> tuple[bool, list[str]]:
    """Channel on and *key* names a valid config → (True, allowed origins incl. the portal's own
    origin when the chat is shown there).

    ``preview`` (R-PREVIEW-DRAFT, own origin + own Referer already checked): the channel switch
    is skipped and a disabled or invalid config is found too.
    """
    if not key:
        return False, []
    try:
        if preview:
            cached = await public_config_cache.by_key_any(db, key)
        else:
            if not await public_config_cache.channel_enabled(db):
                return False, []
            cached = await public_config_cache.by_key(db, key)
    finally:
        await db.rollback()
    if cached is None:
        return False, []
    return True, effective_allowed_origins(cached.values, own)


@router.api_route("/frame", methods=["GET", "HEAD"])
async def widget_frame(
    request: Request,
    settings: AppSettings,
    db: DbSession,
    k: Annotated[str | None, Query(max_length=40)] = None,
    origin: Annotated[str | None, Query(max_length=200)] = None,
    lang: Annotated[str | None, Query(max_length=20)] = None,
    preview: Annotated[str | None, Query(max_length=1)] = None,
) -> Response:
    """The chat iframe document with a per-chat, per-embedding-site CSP."""
    del lang  # read by the frame script from its own URL
    html = _read_cached(_dist(settings) / "frame.html")
    if html is None:
        return _not_found()
    # Pure decisions first (R-PREAUTH step 2, R-PREVIEW).
    try:
        own = own_origin(request, settings)
    except OriginError:
        return _refused()
    referer = request.headers.get("referer")
    is_preview = preview == "1"
    norm_origin: str | None = None
    if is_preview:
        if not preview_request_allowed(origin, referer, own):
            return _refused()
    else:
        try:
            norm_origin = canonical_origin(origin or "")
        except OriginError:
            return _refused()
    try:
        found, allowed = await _valid_config_allowed(db, k, own, preview=is_preview)
    except Exception:  # noqa: BLE001 - never cache or frame an error page
        logger.warning("webchat_frame_config_check_failed", exc_info=True)
        return _unavailable()
    if not found:
        return _not_found()
    try:
        if is_preview:
            csp = frame_csp([], own=own, preview_only=True)
        else:
            if not origin_allowed(norm_origin, allowed):
                return _refused()
            if referer is not None and referer_origin(referer) != norm_origin:
                return _refused()
            csp = frame_csp(allowed, own=own, origin=norm_origin)
    except OriginError:
        return _refused()
    return Response(
        html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": csp,
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex",
            "Cross-Origin-Resource-Policy": "cross-origin",
            "Referrer-Policy": "same-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        },
    )


class _AssetFiles(StaticFiles):
    """Content-hashed widget assets: immutable for a year.

    Assumes (as Vite does) that everything under ``assets/`` has a content-hashed name.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = _ASSET_CACHE
        return response


def mount_webchat_assets(app: FastAPI, cfg: Settings) -> bool:
    """Mount ``<dist>/assets`` at ``/chat/v1/assets``; ``False`` (no crash) without a build.

    Call before ``mount_spa`` — the SPA catch-all would otherwise win.
    """
    assets = _dist(cfg) / "assets"
    if not assets.is_dir():
        logger.info("webchat_assets_missing", dist=str(_dist(cfg)))
        return False
    app.mount(ASSETS_URL_PATH, _AssetFiles(directory=str(assets)), name="webchat-assets")
    return True
