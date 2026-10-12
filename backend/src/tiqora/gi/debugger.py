"""GenericInterface debugger - port of ``Kernel/GenericInterface/Debugger.pm``.

One :class:`GiDebugger` belongs to one communication (one request/response pair). Lines below
the configured threshold are dropped, ``TestMode`` writes nothing, and ``error`` additionally
goes to the application log (Znuny: ``Log``). Everything written is masked first
(:mod:`tiqora.gi.redact`) and cut at ``TIQORA_GI_DEBUG_MAX_CONTENT_BYTES``.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable, Mapping
from typing import Any, Literal, Protocol

import structlog

from tiqora.config import get_settings
from tiqora.gi.config import DEBUG_LEVELS, DebuggerConfig, DebugLevel
from tiqora.gi.redact import redact_config, redact_text

logger = structlog.get_logger(__name__)

SUMMARY_MAX_LENGTH = 255
NO_DATA = "No data provided"


class DebugSink(Protocol):
    """Where debugger lines go. ``replay`` is stored only with the first line of a
    communication (the call that creates the entry row)."""

    async def add(
        self,
        *,
        communication_id: str,
        communication_type: str,
        webservice_id: int,
        remote_ip: str | None,
        name: str | None,
        ticket_id: int | None,
        debug_level: str,
        summary: str,
        content: str,
        replay: Mapping[str, Any] | None = None,
    ) -> None: ...


class GiDebugger:
    def __init__(
        self,
        *,
        sink: DebugSink,
        config: DebuggerConfig,
        webservice_id: int,
        communication_type: Literal["Provider", "Requester"],
        remote_ip: str | None = None,
        communication_id: str | None = None,
        name: str | None = None,
        secret_values: Iterable[str] = (),
        replay: Mapping[str, Any] | None = None,
        max_content_bytes: int | None = None,
    ) -> None:
        self._sink = sink
        self._config = config
        self._webservice_id = webservice_id
        self._communication_type = communication_type
        self._remote_ip = remote_ip
        self._name = name
        self._secrets = tuple(secret_values)
        self._replay = replay
        self._max_content_bytes = max_content_bytes
        # Znuny: md5 hex (32 characters).
        self.communication_id: str = communication_id or secrets.token_hex(16)
        self.ticket_id: int | None = None

    def add_secrets(self, *values: str) -> None:
        """Mask *values* in every later line (secrets only known during a call, e.g. a token)."""
        self._secrets = (*self._secrets, *(v for v in values if isinstance(v, str)))

    def _limit(self) -> int:
        if self._max_content_bytes is not None:
            return self._max_content_bytes
        return get_settings().gi_debug_max_content_bytes

    def _mask_leaves(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            return {key: self._mask_leaves(item) for key, item in data.items()}
        if isinstance(data, list | tuple):
            return [self._mask_leaves(item) for item in data]
        if isinstance(data, str):
            return redact_text(data, self._secrets)
        return data

    def _render(self, data: Any) -> str:
        if data is None or data == "" or data == {} or data == []:
            return NO_DATA
        if isinstance(data, str):
            text = redact_text(data, self._secrets)
        elif isinstance(data, Mapping | list | tuple):
            # Mask the string leaves first: after json.dumps a secret with a quote or
            # backslash only exists in its escaped form (redact_text covers that as well).
            dumped = json.dumps(
                self._mask_leaves(redact_config(data)),
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            text = redact_text(dumped, self._secrets)
        else:
            text = redact_text(str(data), self._secrets)
        raw = text.encode("utf-8")
        limit = self._limit()
        if len(raw) > limit:
            cut = raw[:limit].decode("utf-8", errors="ignore")
            text = f"{cut}\n[… gekürzt, {len(raw)} Bytes]"
        return text

    async def log(self, level: DebugLevel, summary: str, data: Any = None) -> None:
        safe_summary = redact_text(summary, self._secrets)
        if level == "error":
            logger.error(
                "gi_debugger_error",
                webservice_id=self._webservice_id,
                communication_id=self.communication_id,
                summary=safe_summary[:SUMMARY_MAX_LENGTH],
            )
        if self._config.test_mode:
            return
        if DEBUG_LEVELS[level] < DEBUG_LEVELS[self._config.threshold]:
            return
        await self._sink.add(
            communication_id=self.communication_id,
            communication_type=self._communication_type,
            webservice_id=self._webservice_id,
            remote_ip=self._remote_ip,
            name=self._name,
            ticket_id=self.ticket_id,
            debug_level=level,
            summary=safe_summary[:SUMMARY_MAX_LENGTH],
            content=self._render(data),
            replay=self._replay,
        )

    async def debug(self, summary: str, data: Any = None) -> None:
        await self.log("debug", summary, data)

    async def info(self, summary: str, data: Any = None) -> None:
        await self.log("info", summary, data)

    async def notice(self, summary: str, data: Any = None) -> None:
        await self.log("notice", summary, data)

    async def error(self, summary: str, data: Any = None) -> dict[str, Any]:
        """Log at ``error`` and return Znuny's error shape."""
        await self.log("error", summary, data)
        return {"Success": 0, "ErrorMessage": redact_text(summary, self._secrets)}
