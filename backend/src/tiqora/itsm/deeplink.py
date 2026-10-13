"""Links into Znuny's agent interface ("In Znuny bearbeiten"), Mode A.

The base is ``ItsmSettings.znuny_agent_url`` when set (the agent interface root including the
script alias, e.g. ``https://znuny.example.org/znuny/``), else ``HttpType``, ``FQDN`` and
``ScriptAlias`` from SysConfig. ``FQDN`` still at Znuny's placeholder (or empty) counts as unset:
a link to ``yourhost.example.com`` helps nobody, so the caller shows no link. The action names
are the same on 6.5 and 7.3 (behaviour survey, question 10).
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import quote

from tiqora.itsm.settings import ItsmSettings
from tiqora.znuny.sysconfig import ZNUNY_SETTING_DEFAULTS, SysConfig

_ACTION_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_PLACEHOLDER_FQDN: Final = str(ZNUNY_SETTING_DEFAULTS["FQDN"])

CI_ZOOM: Final = "AgentITSMConfigItemZoom"
CI_EDIT: Final = "AgentITSMConfigItemEdit"
CI_HISTORY: Final = "AgentITSMConfigItemHistory"
SERVICE_ZOOM: Final = "AgentITSMServiceZoom"


async def agent_base_url(sysconfig: SysConfig, settings: ItsmSettings) -> str | None:
    """``…/index.pl`` of the Znuny agent interface, or ``None`` while it cannot be derived."""
    override = settings.znuny_agent_url.strip()
    if override:
        if override.lower().endswith("index.pl"):
            return override
        return override.rstrip("/") + "/index.pl"
    fqdn = str(await sysconfig.get("FQDN", "") or "").strip()
    if not fqdn or fqdn == _PLACEHOLDER_FQDN:
        return None
    http_type = str(await sysconfig.get("HttpType", "http") or "http").strip()
    alias = str(await sysconfig.get("ScriptAlias", "otrs/") or "").strip().lstrip("/")
    if alias and not alias.endswith("/"):
        alias += "/"
    return f"{http_type}://{fqdn}/{alias}index.pl"


async def znuny_agent_url(
    sysconfig: SysConfig, settings: ItsmSettings, action: str, **params: str | int
) -> str | None:
    """``<base>?Action=<action>;<key>=<value>;…`` (Znuny's own separator is ``;``)."""
    if not _ACTION_RE.fullmatch(action):
        raise ValueError(f"invalid Znuny action name: {action!r}")
    base = await agent_base_url(sysconfig, settings)
    if base is None:
        return None
    parts = [f"Action={action}"]
    for key, value in params.items():
        if not _ACTION_RE.fullmatch(key):
            raise ValueError(f"invalid Znuny parameter name: {key!r}")
        parts.append(f"{key}={quote(str(value), safe='')}")
    return f"{base}?{';'.join(parts)}"


__all__ = [
    "CI_EDIT",
    "CI_HISTORY",
    "CI_ZOOM",
    "SERVICE_ZOOM",
    "agent_base_url",
    "znuny_agent_url",
]
