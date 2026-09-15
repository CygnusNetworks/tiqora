"""Unit tests for the ``/api/v1/ai/refine`` error mapping and request schema.

Pure unit tests — no DB, no HTTP. The frontend matches on the structured
``detail`` code prefix, the same convention ``_map_run_error`` already uses.
"""

from __future__ import annotations

import pytest
from fastapi import status
from pydantic import ValidationError

from tiqora.ai.refine import (
    MAX_TOTAL_CHARS,
    RefineAclDeniedError,
    RefineAclLimitExceededError,
    RefineEmptyOutputError,
    RefineError,
    RefinePolicyDisabledError,
)
from tiqora.api.v1.ai import AiRefineIn, AiRefineSegmentIn, _map_refine_error


def test_acl_limit_exceeded_maps_to_429() -> None:
    exc = _map_refine_error(RefineAclLimitExceededError("limit reached"))
    assert exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_acl_denied_maps_to_403() -> None:
    exc = _map_refine_error(RefineAclDeniedError("not allowed"))
    assert exc.status_code == status.HTTP_403_FORBIDDEN


def test_policy_disabled_maps_to_409_with_code() -> None:
    exc = _map_refine_error(RefinePolicyDisabledError("disabled"))
    assert exc.status_code == status.HTTP_409_CONFLICT
    assert isinstance(exc.detail, str) and exc.detail.startswith("refine_disabled: ")


def test_empty_output_maps_to_502_with_code() -> None:
    exc = _map_refine_error(RefineEmptyOutputError("nothing usable"))
    assert exc.status_code == status.HTTP_502_BAD_GATEWAY
    assert isinstance(exc.detail, str) and exc.detail.startswith("refine_empty_output: ")


def test_other_refine_errors_map_to_409() -> None:
    assert _map_refine_error(RefineError("too long")).status_code == status.HTTP_409_CONFLICT


def _segments(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"kind": kind, "text": text} for kind, text in pairs]


def test_request_rejects_an_unknown_segment_kind() -> None:
    with pytest.raises(ValidationError):
        AiRefineIn(queue_id=1, segments=_segments(("footer", "x")))


def test_request_accepts_a_ticket_instead_of_a_queue() -> None:
    body = AiRefineIn(ticket_id=42, segments=_segments(("own", "hallo")))
    assert body.queue_id is None and body.ticket_id == 42


def test_request_rejects_naming_neither_a_ticket_nor_a_queue() -> None:
    # Without one of them there is no queue policy to consult.
    with pytest.raises(ValidationError):
        AiRefineIn(segments=_segments(("own", "hallo")))


def test_request_rejects_naming_both_a_ticket_and_a_queue() -> None:
    # The ticket's own queue is authoritative; a second one could disagree.
    with pytest.raises(ValidationError):
        AiRefineIn(ticket_id=42, queue_id=1, segments=_segments(("own", "hallo")))


def test_request_accepts_a_customer_for_the_new_ticket_form() -> None:
    # Seeds PII name masking when there is no ticket to read names from.
    body = AiRefineIn(queue_id=1, customer_user_id="jane.doe", segments=_segments(("own", "x")))
    assert body.customer_user_id == "jane.doe"


def test_request_rejects_a_customer_alongside_a_ticket() -> None:
    # With a ticket the names come from the ticket itself; a client-supplied
    # customer could disagree with it.
    with pytest.raises(ValidationError):
        AiRefineIn(ticket_id=42, customer_user_id="jane.doe", segments=_segments(("own", "x")))


def test_request_rejects_an_unknown_tone() -> None:
    with pytest.raises(ValidationError):
        AiRefineIn(queue_id=1, tone="shakespearean", segments=_segments(("own", "x")))


def test_request_rejects_an_empty_segment_list() -> None:
    with pytest.raises(ValidationError):
        AiRefineIn(queue_id=1, segments=[])


def test_request_rejects_a_body_over_the_size_cap() -> None:
    with pytest.raises(ValidationError):
        AiRefineIn(queue_id=1, segments=_segments(("own", "x" * (MAX_TOTAL_CHARS + 1))))


def test_request_defaults_to_the_standard_tone() -> None:
    body = AiRefineIn(queue_id=1, segments=_segments(("own", "hallo")))
    assert body.tone == "standard"
    assert body.ticket_id is None
    assert body.segments == [AiRefineSegmentIn(kind="own", text="hallo")]
