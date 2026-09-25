"""structlog JSON logging configuration."""

import logging
import re
import sys

import structlog

from tiqora.config import Settings

# Telegram puts the bot token into the URL path (``/bot<id>:<secret>/getUpdates``)
# and httpx logs every request URL at INFO, so the worker wrote the token into
# its log every poll. The request lines themselves are worth keeping.
_TELEGRAM_TOKEN_RE = re.compile(r"/bot\d+:[\w-]+")


class _RedactTelegramToken(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _TELEGRAM_TOKEN_RE.sub("/bot***", message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def configure_logging(settings: Settings) -> None:
    """Configure stdlib logging and structlog for JSON output."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactTelegramToken) for f in httpx_logger.filters):
        httpx_logger.addFilter(_RedactTelegramToken())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
