"""Worker-side snapshot of valid GenericInterface webservices with a requester section."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.compat.mapping.perl import perl_truthy
from tiqora.api.compat.webservices import (
    _PARSE_ERRORS,
    Fingerprint,
    _load_yaml_rows,
    load_fingerprint,
    replace_config_placeholders,
)
from tiqora.db.legacy.config import GiWebserviceConfig
from tiqora.gi.config import DebuggerConfig, RequesterConfig, parse_requester
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GiWebservice:
    id: int
    name: str
    requester: RequesterConfig
    debugger: DebuggerConfig
    config: dict[str, Any]  # full parsed config (secrets inside)


async def _build(
    ws_id: int, name: str, parsed: dict[str, Any] | None, sysconfig: SysConfig, huge_xml: bool
) -> GiWebservice | None:
    """Parse one row; None when it is broken or has no Requester section."""
    if parsed is None:
        logger.warning("gi_webservice_invalid", ws_id=ws_id, name=name, error="invalid YAML")
        return None
    try:
        config = await replace_config_placeholders(parsed, sysconfig)
        if not isinstance(config.get("Requester"), dict) or not config["Requester"]:
            return None
        requester = await asyncio.to_thread(parse_requester, config, huge_xml=huge_xml)
    except _PARSE_ERRORS as exc:
        logger.warning("gi_webservice_invalid", ws_id=ws_id, name=name, error=str(exc))
        return None
    except SQLAlchemyError:
        raise
    except Exception:
        logger.exception("gi_webservice_unexpected_error", ws_id=ws_id, name=name)
        return None
    return GiWebservice(
        id=ws_id, name=name, requester=requester, debugger=requester.debugger, config=config
    )


async def _huge_xml(sysconfig: SysConfig) -> bool:
    return perl_truthy(await sysconfig.get("LibXML::EnableHugeXMLDataSupport"))


class GiWebservices:
    """Snapshot of all VALID webservices with a Requester section; swapped atomically."""

    def __init__(self) -> None:
        self._by_id: dict[int, GiWebservice] = {}
        self._by_name: dict[str, GiWebservice] = {}
        self._fingerprint: Fingerprint | None = None
        self._loaded = False
        self._lock = asyncio.Lock()

    def by_id(self, ws_id: int) -> GiWebservice | None:
        return self._by_id.get(ws_id)

    def by_name(self, name: str) -> GiWebservice | None:
        return self._by_name.get(name)

    def all(self) -> tuple[GiWebservice, ...]:
        return tuple(self._by_id[i] for i in sorted(self._by_id))

    async def refresh(
        self, session: AsyncSession, sysconfig: SysConfig, *, force: bool = False
    ) -> bool:
        """Reload when the fingerprint changed. DB errors propagate and keep the old snapshot."""
        async with self._lock:
            fingerprint = await load_fingerprint(session)
            if not force and self._loaded and fingerprint == self._fingerprint:
                return False
            huge_xml = await _huge_xml(sysconfig)
            rows = (
                await session.execute(
                    select(
                        GiWebserviceConfig.id, GiWebserviceConfig.name, GiWebserviceConfig.config
                    )
                    .where(GiWebserviceConfig.valid_id == 1)
                    .order_by(GiWebserviceConfig.id)
                )
            ).all()
            parsed_rows = await asyncio.to_thread(_load_yaml_rows, rows)
            by_id: dict[int, GiWebservice] = {}
            for ws_id, ws_name, parsed in parsed_rows:
                ws = await _build(ws_id, ws_name, parsed, sysconfig, huge_xml)
                if ws is not None:
                    by_id[ws.id] = ws
            self._by_id = by_id
            self._by_name = {ws.name: ws for ws in by_id.values()}
            self._fingerprint = fingerprint
            self._loaded = True
            return True


async def load_gi_webservice(
    session: AsyncSession, sysconfig: SysConfig, ws_id: int
) -> GiWebservice | None:
    """One valid webservice with a requester section; None when missing or invalid."""
    row = (
        await session.execute(
            select(GiWebserviceConfig.id, GiWebserviceConfig.name, GiWebserviceConfig.config).where(
                GiWebserviceConfig.id == ws_id, GiWebserviceConfig.valid_id == 1
            )
        )
    ).all()
    if not row:
        return None
    parsed_rows = await asyncio.to_thread(_load_yaml_rows, row)
    ws_id_, name, parsed = parsed_rows[0]
    return await _build(ws_id_, name, parsed, sysconfig, await _huge_xml(sysconfig))
