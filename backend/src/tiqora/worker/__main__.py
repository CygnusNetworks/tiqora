"""Worker process entrypoint (taskiq placeholder)."""

import time

import structlog

from tiqora.config import get_settings
from tiqora.logging_setup import configure_logging

logger = structlog.get_logger(__name__)


def run_worker() -> None:
    """Start the background worker.

    Full taskiq broker wiring lands in Phase 0/1. This scaffold only boots
    logging so the process role is usable in Docker entrypoints.
    """
    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "tiqora_worker_placeholder",
        redis_url=settings.redis_url,
        message="taskiq consumer not yet implemented",
    )
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    run_worker()
