"""Unit tests for ``tiqora.api.v1.ai._map_run_error`` (Task: LLM-Budget +
Fehler-UX, part 2).

Pure unit tests — no DB, no HTTP — mirroring the structured ``detail`` code
prefix (``"<code>: <message>"``) each error class must map to, since the
frontend (``AiPanel.mapRunError``) matches on that prefix to show a specific
i18n message instead of the generic fallback.
"""

from __future__ import annotations

from fastapi import status

from tiqora.ai.llm import LlmEmptyOutputError, LlmError, LlmHttpError, LlmTimeoutError
from tiqora.ai.runtime import (
    AclDeniedError,
    AclLimitExceededError,
    LockHeldError,
    PolicyDisabledError,
)
from tiqora.api.v1.ai import _map_run_error


def test_lock_held_maps_to_423_with_ai_run_locked_code() -> None:
    exc = _map_run_error(LockHeldError("Ticket 1 run lock held by api:run-1 (0:00:01 ago)"))
    assert exc.status_code == status.HTTP_423_LOCKED
    assert isinstance(exc.detail, str) and exc.detail.startswith("ai_run_locked: ")


def test_acl_limit_exceeded_maps_to_429() -> None:
    exc = _map_run_error(AclLimitExceededError("limit reached"))
    assert exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_acl_denied_maps_to_403() -> None:
    exc = _map_run_error(AclDeniedError("not allowed"))
    assert exc.status_code == status.HTTP_403_FORBIDDEN


def test_policy_disabled_maps_to_409() -> None:
    exc = _map_run_error(PolicyDisabledError("disabled"))
    assert exc.status_code == status.HTTP_409_CONFLICT


def test_llm_empty_output_maps_to_502_with_code() -> None:
    exc = _map_run_error(LlmEmptyOutputError("length twice"))
    assert exc.status_code == status.HTTP_502_BAD_GATEWAY
    assert isinstance(exc.detail, str) and exc.detail.startswith("llm_empty_output: ")


def test_llm_timeout_maps_to_504_with_code() -> None:
    exc = _map_run_error(LlmTimeoutError("timed out"))
    assert exc.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert isinstance(exc.detail, str) and exc.detail.startswith("llm_timeout: ")


def test_llm_http_error_maps_to_502_with_code_and_status() -> None:
    exc = _map_run_error(LlmHttpError(503, "Service Unavailable"))
    assert exc.status_code == status.HTTP_502_BAD_GATEWAY
    assert isinstance(exc.detail, str)
    assert exc.detail.startswith("llm_provider_error: ")
    assert "503" in exc.detail


def test_generic_llm_error_maps_to_502_with_code() -> None:
    exc = _map_run_error(LlmError("connection reset"))
    assert exc.status_code == status.HTTP_502_BAD_GATEWAY
    assert isinstance(exc.detail, str) and exc.detail.startswith("llm_provider_error: ")
