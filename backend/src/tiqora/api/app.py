"""FastAPI application factory with health, readiness, and Prometheus metrics."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import redis.asyncio as redis
import structlog
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

from tiqora import __version__
from tiqora.api.compat.router import compat_router
from tiqora.api.portal import portal_router
from tiqora.api.portal.deps import require_portal_enabled
from tiqora.api.scim.router import SCIM_PREFIX, scim_router
from tiqora.api.spa import mount_spa, spa_is_available
from tiqora.api.v1 import api_v1_router
from tiqora.api.v1.admin.chat_configs import chat_config_validation_handler
from tiqora.config import Settings, get_settings
from tiqora.db.engine import check_database, get_session_factory
from tiqora.identity.env_providers import ensure_env_providers, env_ldap_tls_warnings
from tiqora.logging_setup import configure_logging
from tiqora.webchat.public_api import CHAT_PUBLIC_PREFIX, chat_body_limit_middleware
from tiqora.webchat.public_api import router as webchat_public_router
from tiqora.webchat.static import NONE_CSP, mount_webchat_assets
from tiqora.webchat.static import router as webchat_static_router

logger = structlog.get_logger(__name__)

REQUEST_COUNT = Counter(
    "tiqora_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "tiqora_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
)
INDEX_DOCS = Counter(
    "tiqora_index_documents_total",
    "Documents sent to Meilisearch",
)

CallNext = Callable[[Request], Awaitable[StarletteResponse]]

# Conservative SPA CSP. Inline styles are required (many React style= props);
# scripts stay 'self' only (vite production build has no inline scripts).
# Article HTML is rendered in a sandboxed iframe with its own CSP meta tag —
# that nested document is not governed by this parent policy for its own
# content, but frame-src must still allow the iframe itself.
_SPA_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src 'self' blob:;"
)


async def _crypto_self_check(app: FastAPI, settings: Settings) -> None:
    """Log whether gpg / openssl are usable for every *enabled* crypto backend."""
    try:
        import asyncio

        from tiqora.crypto.config import backend_status_sync, load_crypto_config

        async with app.state.session_factory() as _crypto_session:
            crypto_cfg = await load_crypto_config(_crypto_session, settings)
        for st in await asyncio.to_thread(backend_status_sync, crypto_cfg):
            if not st.enabled:
                continue
            if st.available:
                logger.info(
                    "crypto_backend_ready",
                    backend=st.backend,
                    binary=st.binary.path,
                    version=st.binary.version,
                )
            else:
                logger.warning("crypto_backend_unusable", backend=st.backend, problems=st.problems)
    except Exception as _exc:  # noqa: BLE001 — never block startup on the self-check
        logger.warning("crypto_self_check_skipped", error=str(_exc))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown hooks."""
    settings: Settings = app.state.settings
    configure_logging(settings)
    app.state.session_factory = get_session_factory()
    app.state.redis = redis.from_url(settings.redis_url, decode_responses=True)
    app.state.chat_hub = None
    logger.info(
        "tiqora_starting",
        version=__version__,
        environment=settings.environment,
    )

    # Build + freeze the extension registry now so a broken registration fails at boot.
    from tiqora.plugins.bootstrap import init_registry

    registry = await init_registry(
        role="api", settings=settings, session_factory=app.state.session_factory
    )
    from tiqora.plugins.routes import mount_plugin_routes, record_refused_plugin_routes

    mount_plugin_routes(app, registry)
    await record_refused_plugin_routes(app, app.state.session_factory)

    # Detect OTRS/Znuny schema tier and refuse unknown shapes (with override).
    # Soft-skip only when the DB is unreachable (dev/tests without a peer DB);
    # an *unknown* schema on a reachable DB hard-fails unless overridden.
    if settings.legacy_schema_check_enabled:
        try:
            from tiqora.db.legacy.profile import (
                UnsupportedLegacySchemaError,
                ensure_legacy_schema_supported,
            )

            dialect_hint = (
                "postgresql"
                if settings.is_postgres
                else "mysql"
                if settings.is_mysql
                else "unknown"
            )
            async with app.state.session_factory() as _schema_session:
                profile = await ensure_legacy_schema_supported(
                    _schema_session,
                    allow_unknown=settings.allow_unknown_legacy_schema,
                    profile_override=settings.legacy_schema_profile,
                    dialect_hint=dialect_hint,
                )
            app.state.legacy_schema_profile = profile
            logger.info(
                "legacy_schema_ready",
                profile_id=profile.profile_id.value,
                label=profile.label,
                groups_table=profile.groups_table,
            )
        except UnsupportedLegacySchemaError:
            raise
        except Exception as _exc:  # noqa: BLE001 — classified below
            from tiqora.db.legacy.profile import is_db_unavailable

            if is_db_unavailable(_exc):
                # Peer DB not reachable (local dev without stack). Soft-skip.
                logger.warning(
                    "legacy_schema_check_skipped",
                    error=str(_exc),
                    reason="db_unavailable",
                )
                app.state.legacy_schema_profile = None
            else:
                # Detection/adapter bug on a reachable DB must not boot silently
                # with profile=None (wrong groups table / missing color default).
                logger.error(
                    "legacy_schema_detection_failed",
                    error=str(_exc),
                    error_type=type(_exc).__name__,
                    exc_info=True,
                )
                raise
    else:
        app.state.legacy_schema_profile = None

    from tiqora.itsm.mode import init_itsm_mode_at_startup

    await init_itsm_mode_at_startup(app.state.session_factory)

    try:
        async with app.state.session_factory() as _identity_session:
            await ensure_env_providers(_identity_session, settings)
    except Exception as _exc:  # noqa: BLE001
        logger.warning("identity_env_sync_skipped", error=str(_exc))
    for _flag in env_ldap_tls_warnings(settings):
        logger.warning(
            "ldap_tls_verification_off",
            setting=_flag,
            hint="the directory certificate is not checked; set it to true",
        )
    await _crypto_self_check(app, settings)

    # Compat webservices: initial load (never fatal) + background hot reload (D12).
    from tiqora.api.compat.reload import reload_tick, run_webservice_reloader

    try:
        await reload_tick(app, force=True)
    except Exception as _exc:  # noqa: BLE001 - requests lazy-load and the poller retries
        logger.warning("compat_webservice_initial_load_failed", error=str(_exc))
    reload_stop = asyncio.Event()
    reload_task = asyncio.create_task(
        run_webservice_reloader(
            app,
            interval_seconds=settings.compat_webservice_reload_seconds,
            stop=reload_stop,
        )
    )
    try:
        yield
    finally:
        reload_stop.set()
        try:
            await asyncio.wait_for(reload_task, 5)
        except Exception:  # noqa: BLE001 - timeout (task already cancelled) or task failure
            logger.warning("compat_webservice_reloader_stop_failed", exc_info=True)
    chat_hub = getattr(app.state, "chat_hub", None)
    if chat_hub is not None:
        await chat_hub.aclose()
    redis_client = getattr(app.state, "redis", None)
    if redis_client is not None:
        await redis_client.aclose()
    logger.info("tiqora_stopped")


_CHAT_LABEL_WORDS = frozenset(
    {"api", "chat", "v1", "config", "sessions", "messages", "stream", "typing"}
    | {"attachments", "contact", "end", "offline"}
)


def _chat_label_path(path: str) -> str:
    """Metrics label of a public chat path: session ids → ``{id}``, numbers → ``{n}``.

    The API is anonymous, so any other segment collapses to ``{x}`` (no label per URL).
    """
    out: list[str] = []
    for part in path.split("/"):
        if len(part) == 32 and all(c in "0123456789abcdef" for c in part):
            out.append("{id}")
        elif part.isdigit():
            out.append("{n}")
        elif part == "" or part in _CHAT_LABEL_WORDS:
            out.append(part)
        else:
            out.append("{x}")
    return "/".join(out)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    cfg = settings or get_settings()
    # Production-only hard fail on weak secrets / debug / wildcard CORS / etc.
    # No-op in development and test so defaults remain usable.
    cfg.validate_production()
    if cfg.is_production and not cfg.password_reject_weak_hashes:
        # Parallel-op with Znuny may still need weak verifiers until a rehash
        # campaign; refuse-weak is opt-in so we only warn at boot.
        logger.warning(
            "password_reject_weak_hashes_disabled",
            hint=(
                "Set TIQORA_PASSWORD_REJECT_WEAK_HASHES=1 after migrating agents "
                "to BCRYPT (rehash-on-login is already on by default)."
            ),
        )

    is_prod = cfg.is_production
    app = FastAPI(
        title=cfg.app_name,
        version=__version__,
        description="Tiqora ticket system API.",
        lifespan=lifespan,
        # H-05: do not expose OpenAPI/Swagger on production deployments.
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.state.settings = cfg

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def upload_size_limit_middleware(
        request: Request, call_next: CallNext
    ) -> StarletteResponse:
        """M-4: refuse oversized bodies before Starlette spools them to disk."""
        from tiqora.api.uploads import body_size_limit_middleware

        return await body_size_limit_middleware(request, call_next)

    @app.middleware("http")
    async def chat_body_limit(request: Request, call_next: CallNext) -> StarletteResponse:
        """R-UPLOAD: bound public chat bodies before FastAPI reads them."""
        return await chat_body_limit_middleware(request, call_next)

    @app.middleware("http")
    async def csrf_origin_check_middleware(
        request: Request, call_next: CallNext
    ) -> StarletteResponse:
        """M-02: Origin/Referer check for cookie-authenticated unsafe API requests.

        API-key Authorization, safe methods, and requests without a session
        cookie are exempt — SPA same-origin and non-browser clients keep working.
        """
        from tiqora.security.csrf import csrf_origin_middleware

        return await csrf_origin_middleware(request, call_next)

    @app.middleware("http")
    async def security_headers_middleware(
        request: Request, call_next: CallNext
    ) -> StarletteResponse:
        """M-03: defense-in-depth security headers (proxy may also set some)."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # HSTS in production (harmless if a TLS-terminating proxy also sets it;
        # omitted in dev so plain-HTTP local runs aren't pinned to HTTPS).
        if cfg.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), camera=(), microphone=()",
        )
        # CSP: report-only by default so a policy drift cannot brick the SPA;
        # set TIQORA_CSP_ENFORCE=1 after verifying reports are clean.
        csp_header = (
            "Content-Security-Policy" if cfg.csp_enforce else "Content-Security-Policy-Report-Only"
        )
        # /chat/v1/ (widget frame) sets its own enforced CSP; a Report-Only
        # frame-ancestors on top would only confuse (C9). Every other answer there
        # (errors, assets) must not be frameable.
        if request.url.path.startswith("/chat/v1/"):
            response.headers.setdefault("Content-Security-Policy", NONE_CSP)
        else:
            response.headers.setdefault(csp_header, _SPA_CSP)
        return response

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next: CallNext) -> StarletteResponse:
        path = request.url.path
        if path.startswith(CHAT_PUBLIC_PREFIX + "/"):
            label_path = _chat_label_path(path)
        elif path.startswith(cfg.api_prefix):
            # Collapse dynamic segments for low-cardinality labels
            parts = path.split("/")
            label_parts: list[str] = []
            for p in parts:
                if p.isdigit():
                    label_parts.append("{id}")
                else:
                    label_parts.append(p)
            label_path = "/".join(label_parts)
        else:
            label_path = path if path in {"/health", "/ready", "/metrics", "/"} else "other"
        method = request.method
        with REQUEST_LATENCY.labels(method=method, path=label_path).time():
            response = await call_next(request)
        REQUEST_COUNT.labels(
            method=method,
            path=label_path,
            status=str(response.status_code),
        ).inc()
        return response

    app.add_exception_handler(RequestValidationError, chat_config_validation_handler)
    app.include_router(api_v1_router, prefix=cfg.api_prefix)
    app.include_router(compat_router)
    app.include_router(webchat_public_router, prefix=CHAT_PUBLIC_PREFIX)
    app.include_router(
        portal_router,
        prefix="/api/portal",
        dependencies=[Depends(require_portal_enabled)],
    )
    # SCIM 2.0 provisioning (bearer token only, outside /api so the CSRF check never applies).
    # Must be registered before mount_spa: the SPA fallback matches any remaining GET path.
    app.include_router(scim_router, prefix=SCIM_PREFIX)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Liveness probe — process is up."""
        return {"status": "ok", "version": __version__}

    @app.get("/health/ai", tags=["ops"])
    async def health_ai() -> dict[str, object]:
        """AI budget probe for external monitoring (Zabbix, Checkmk, …).

        ``status`` is ``ok`` or ``limit_reached``; the list names each
        exhausted cap by kind, id and window only — no queue/provider names or
        amounts, since this endpoint is unauthenticated like ``/health``.
        Always HTTP 200: a spent budget is a state to alert on, not an outage.
        """
        from tiqora.ai.limits import exhausted_limits

        async with get_session_factory()() as session:
            reached = await exhausted_limits(session)
        return {
            "status": "limit_reached" if reached else "ok",
            "limits_reached": [
                {
                    "kind": s.kind,
                    "subject_id": s.subject_id,
                    "window": s.window,
                    "resets_at": s.resets_at.isoformat() + "Z",
                }
                for s in reached
            ],
        }

    @app.get("/ready", tags=["ops"])
    async def ready() -> dict[str, object]:
        """Readiness probe — critical dependencies reachable."""
        db_ok = await check_database(cfg)
        redis_ok = False
        try:
            client = getattr(app.state, "redis", None)
            if client is not None:
                redis_ok = bool(await client.ping())
        except Exception:  # noqa: BLE001
            redis_ok = False
        ready_ok = db_ok  # Redis optional for readiness in dev
        return {
            "status": "ready" if ready_ok else "not_ready",
            "database": db_ok,
            "redis": redis_ok,
            "version": __version__,
        }

    # H-04: keep /metrics registered when enabled (default) so internal scrapes
    # and tests keep working; operators can disable with TIQORA_METRICS_ENABLED=0
    # when the public app port must not expose metrics (nginx deny is still the
    # primary control in prod).
    if cfg.metrics_enabled:

        @app.get("/metrics", tags=["ops"])
        async def metrics() -> Response:
            """Prometheus metrics exposition."""
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
    else:

        @app.get("/metrics", tags=["ops"], include_in_schema=False)
        async def metrics_disabled() -> Response:
            raise HTTPException(status_code=404, detail="Not found")

    # When the api serves the built SPA, "/" must return index.html — so only
    # register the JSON root when the SPA is not mounted (dev/tests, or
    # TIQORA_SERVE_FRONTEND=0).
    if not spa_is_available(cfg):

        @app.get("/", tags=["ops"])
        async def root() -> dict[str, str]:
            return {
                "name": cfg.app_name,
                "version": __version__,
                "docs": "/docs",
                "health": "/health",
            }

    # Plugin routes (/api/plugins/<id>/…): sub-apps are mounted by the lifespan once the
    # registry is built. Must come before the SPA fallback.
    from tiqora.plugins.routes import create_plugin_router

    app.state.plugin_router = create_plugin_router()
    app.mount("/api/plugins", app.state.plugin_router)

    app.include_router(webchat_static_router)
    mount_webchat_assets(app, cfg)

    # Must be last: the SPA fallback matches any remaining GET path.
    mount_spa(app, cfg)

    return app
