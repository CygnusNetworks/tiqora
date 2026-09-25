"""Encode a run's tool trace for ``tool_trace_json`` within the column limit.

``tiqora_ai_draft.tool_trace_json`` and ``tiqora_ai_article_origin.tool_trace_json``
are plain ``TEXT`` — 65,535 bytes on MariaDB, which rejects a longer value
in strict mode instead of truncating it. A run that read eleven KB articles
in production produced a trace over that limit; the INSERT
failed at commit, after the reply mail had already left, and rolled the
sent article back with it.

The trace is there so an agent can follow what the run looked at. The first
few thousand characters of each result carry that; full KB article bodies do
not need to be in it. So results are shortened until the encoded trace fits,
and only when even that is not enough are the oldest steps dropped.
"""

from __future__ import annotations

import json
from typing import Any

MAX_TOOL_TRACE_BYTES = 60_000
"""Encoded budget, below MariaDB ``TEXT`` (65,535 bytes) with headroom.

``json.dumps`` escapes non-ASCII by default, so the encoded string's length
is its byte length."""

_CONTENT_CAPS = (4_000, 1_000, 200)
"""Per-step content caps, tried in order until the trace fits."""


def _cap(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n[… {len(value) - limit} Zeichen gekürzt]"


def _capped(trace: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    out = []
    for step in trace:
        content = step.get("content")
        if isinstance(content, str):
            step = {**step, "content": _cap(content, limit)}
        out.append(step)
    return out


def encode_tool_trace(trace: list[dict[str, Any]]) -> str:
    """JSON-encode ``trace`` so it never exceeds :data:`MAX_TOOL_TRACE_BYTES`.

    An untouched trace is returned as-is. Otherwise step contents are shortened
    with a visible marker, and as a last resort the oldest steps are replaced by
    one marker step — ``parse_tool_trace`` reads every variant unchanged.
    """
    encoded = json.dumps(trace)
    if len(encoded) <= MAX_TOOL_TRACE_BYTES:
        return encoded
    for limit in _CONTENT_CAPS:
        capped = _capped(trace, limit)
        encoded = json.dumps(capped)
        if len(encoded) <= MAX_TOOL_TRACE_BYTES:
            return encoded
    # Arguments are left alone above: they are what an agent reads a trace
    # for, and normally small. Reaching this point means a huge argument or
    # hundreds of steps; keep the newest steps, arguments shortened as well.
    steps = [
        {**s, "arguments": _cap(s["arguments"], _CONTENT_CAPS[-1])}
        if isinstance(s.get("arguments"), str)
        else s
        for s in capped
    ]
    dropped = 0
    while steps:
        marker = {
            "role": "tool",
            "name": "trace",
            "content": f"[… {dropped} frühere Schritte ausgelassen]",
        }
        encoded = json.dumps([marker, *steps] if dropped else steps)
        if len(encoded) <= MAX_TOOL_TRACE_BYTES:
            return encoded
        steps = steps[1:]
        dropped += 1
    return json.dumps(
        [{"role": "tool", "name": "trace", "content": f"[… {dropped} Schritte ausgelassen]"}]
    )


__all__ = ["MAX_TOOL_TRACE_BYTES", "encode_tool_trace"]
