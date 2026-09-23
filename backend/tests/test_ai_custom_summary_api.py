"""Unit tests for ``POST /tickets/{id}/ai/summarize/custom`` — request schema
and error mapping. Pure unit tests, no DB and no HTTP; the service itself is
covered in ``test_ai_summary.py``."""

from __future__ import annotations

import pytest
from fastapi import status
from pydantic import ValidationError

from tiqora.ai.llm import LlmHttpError, LlmTimeoutError
from tiqora.ai.summary import (
    CUSTOM_INSTRUCTION_MAX_CHARS,
    SummaryAclDeniedError,
    SummaryAclLimitExceededError,
    SummaryEmptyOutputError,
    SummaryPolicyDisabledError,
)
from tiqora.api.v1.ai import AiCustomSummaryIn, _map_custom_summary_error


def test_instruction_is_trimmed_and_required() -> None:
    assert AiCustomSummaryIn(instruction="  Kurz, mit Timeline  ").instruction == (
        "Kurz, mit Timeline"
    )
    with pytest.raises(ValidationError):
        AiCustomSummaryIn(instruction="   ")


def test_instruction_length_is_capped() -> None:
    AiCustomSummaryIn(instruction="x" * CUSTOM_INSTRUCTION_MAX_CHARS)
    with pytest.raises(ValidationError):
        AiCustomSummaryIn(instruction="x" * (CUSTOM_INSTRUCTION_MAX_CHARS + 1))


def test_acl_errors_keep_the_stored_summary_mapping() -> None:
    assert (
        _map_custom_summary_error(SummaryAclDeniedError("no")).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        _map_custom_summary_error(SummaryAclLimitExceededError("limit")).status_code
        == status.HTTP_429_TOO_MANY_REQUESTS
    )
    assert (
        _map_custom_summary_error(SummaryPolicyDisabledError("off")).status_code
        == status.HTTP_409_CONFLICT
    )


def test_empty_output_maps_to_the_shared_llm_code() -> None:
    exc = _map_custom_summary_error(SummaryEmptyOutputError("empty"))
    assert exc.status_code == status.HTTP_502_BAD_GATEWAY
    assert isinstance(exc.detail, str) and exc.detail.startswith("llm_empty_output: ")


def test_llm_failures_map_to_the_shared_codes() -> None:
    timeout = _map_custom_summary_error(LlmTimeoutError("slow"))
    assert timeout.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert isinstance(timeout.detail, str) and timeout.detail.startswith("llm_timeout: ")
    provider = _map_custom_summary_error(LlmHttpError(500, "boom"))
    assert provider.status_code == status.HTTP_502_BAD_GATEWAY
    assert isinstance(provider.detail, str) and provider.detail.startswith("llm_provider_error: ")
