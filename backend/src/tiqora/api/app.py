"""FastAPI application factory with health, readiness, and Prometheus metrics."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

from tiqora import __version__
from tiqora.config import Settings, get_settings
from tiqora.db.engine import check_database
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

CallNext = Callable[[Request], Awaitable[StarletteResponse]]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown hooks."""
    settings: Settings = app.state.settings
    configure_logging(settings)
    logger.info(
        "tiqora_starting",
        version=__version__,
        environment=settings.environment,
    )
    yield
    logger.info("tiqora_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    cfg = settings or get_settings()

    app = FastAPI(
        title=cfg.app_name,
        version=__version__,
        description=("Tiqora ticket system API. Under active development — not production ready."),
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
        # Avoid high-cardinality labels for dynamic paths later; root ops only for now
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

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Liveness probe — process is up."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"])
    async def ready() -> dict[str, object]:
        """Readiness probe — critical dependencies reachable."""
        db_ok = await check_database(cfg)
        return {
            "status": "ready" if db_ok else "not_ready",
            "database": db_ok,
            "version": __version__,
        }

    @app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        """Prometheus metrics exposition."""
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", tags=["ops"])
    async def root() -> dict[str, str]:
        return {
            "name": cfg.app_name,
            "version": __version__,
            "docs": "/docs",
            "health": "/health",
        }

    return app
