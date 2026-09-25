"""Log hygiene of :func:`tiqora.logging_setup.configure_logging`."""

from __future__ import annotations

import logging

import pytest

from tiqora.config import get_settings
from tiqora.logging_setup import configure_logging


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_httpx_request_log_hides_the_telegram_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_logging(get_settings())
    configure_logging(get_settings())  # idempotent: one filter, not two
    httpx_logger = logging.getLogger("httpx")
    assert len(httpx_logger.filters) == 1

    # Other tests (uvicorn's dictConfig) may have disabled the logger or raised
    # its level; this one only cares about what the filter does to a record.
    monkeypatch.setattr(httpx_logger, "disabled", False)
    monkeypatch.setattr(httpx_logger, "level", logging.INFO)
    collect = _Collect()
    httpx_logger.addHandler(collect)
    try:
        # The exact call httpx makes for every request.
        httpx_logger.info(
            'HTTP Request: %s %s "%s %d %s"',
            "POST",
            "https://api.telegram.org/bot123456:TEST-token_value/getUpdates",
            "HTTP/1.1",
            200,
            "OK",
        )
        httpx_logger.info("HTTP Request: POST https://llm.example/v1/chat/completions")
    finally:
        httpx_logger.removeHandler(collect)

    first, second = collect.messages
    assert "TEST-token_value" not in first
    assert "https://api.telegram.org/bot***/getUpdates" in first
    assert second == "HTTP Request: POST https://llm.example/v1/chat/completions"
