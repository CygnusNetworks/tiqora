"""FastAPI application factory with health, readiness, and Prometheus metrics."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import redis.asyncio as redis
import structlog
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

from tiqora import __version__
from tiqora.api.compat.router import compat_router, mount_dynamic_compat_routes
from tiqora.api.portal import portal_router
from tiqora.api.spa import mount_spa, spa_is_available
from tiqora.api.v1 import api_v1_router
from tiqora.config import Settings, get_settings
from tiqora.db.engine import check_database, get_session_factory
from tiqora.logging_setup import configure_logging

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
POLLER_HISTORY_LAG = Gauge(
    "tiqora_poller_history_lag",
    "Rows behind max ticket_history.id watermark",
)
POLLER_ARTICLE_LAG = Gauge(
    "tiqora_poller_article_lag",
    "Rows behind max article.id watermark",
)
POLLER_RUNS = Counter(
    "tiqora_poller_runs_total",
    "Znuny-write poller runs",
    ["status"],
)
INDEX_DOCS = Counter(
    "tiqora_index_documents_total",
    "Documents sent to Meilisearch",
)

CallNext = Callable[[Request], Awaitable[StarletteResponse]]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown hooks."""
    settings: Settings = app.state.settings
    configure_logging(settings)
    app.state.session_factory = get_session_factory()
    app.state.redis = redis.from_url(settings.redis_url, decode_responses=True)
    logger.info(
        "tiqora_starting",
        version=__version__,
        environment=settings.environment,
    )
    # Mount dynamic GenericInterface compat routes from webservice config
    try:
        async with app.state.session_factory() as _compat_session:
            await mount_dynamic_compat_routes(app, _compat_session)
    except Exception as _exc:  # noqa: BLE001
        logger.warning("compat_mount_skipped", error=str(_exc))
    yield
    redis_client = getattr(app.state, "redis", None)
    if redis_client is not None:
        await redis_client.aclose()
    logger.info("tiqora_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    cfg = settings or get_settings()

    app = FastAPI(
        title=cfg.app_name,
        version=__version__,
        description="Tiqora ticket system API.",
        lifespan=lifespan,
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
    async def metrics_middleware(request: Request, call_next: CallNext) -> StarletteResponse:
        path = request.url.path
        if path.startswith(cfg.api_prefix):
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

    app.include_router(api_v1_router, prefix=cfg.api_prefix)
    app.include_router(compat_router)
    app.include_router(portal_router, prefix="/api/portal")

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Liveness probe — process is up."""
        return {"status": "ok", "version": __version__}

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

    @app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        """Prometheus metrics exposition."""
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

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

    # Must be last: the SPA fallback matches any remaining GET path.
    mount_spa(app, cfg)

    return app
