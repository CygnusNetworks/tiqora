"""The ``ExecuteInvoker`` process transition action (port of
``ProcessManagement/TransitionAction/ExecuteInvoker.pm``).

The handler has the ``tiqora.process.engine`` ``_ActionHandler`` signature. The engine already
replaced the smart tags in *config* (Znuny ``_ReplaceTicketAttributes`` +
``_ReplaceAdditionalAttributes``). The call is always queued, in the caller's transaction (flush
only): the job exists exactly when the dialog submit commits. ``Asynchronous`` is accepted and
ignored, so a dialog submit never blocks on a remote.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.gi.jobs import GiInvokePayload, enqueue_invocation
from tiqora.gi.loader import load_gi_webservice_by_name
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

_NUMBER_RE: Final = re.compile(r"^[+-]?\d+(\.\d+)?$")  # Znuny IsNumber, integers in practice


def _override_user_id(config: dict[str, Any], user_id: int) -> int:
    """``Base.pm::_OverrideUserID``: a numeric ``UserID`` in the action config wins and is
    removed from the config."""
    raw = config.get("UserID")
    if raw is None or not _NUMBER_RE.match(str(raw).strip()):
        return user_id
    del config["UserID"]
    return int(float(str(raw).strip()))


async def execute_invoker_action(
    session: AsyncSession,
    config: dict[str, Any],
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Queue ``Webservice``/``Invoker`` for the ticket; missing parameters or an unknown
    webservice are logged and do nothing (Znuny returns without a result)."""
    config = copy.deepcopy(config)
    for key in ("Webservice", "Invoker"):
        if not str(config.get(key) or "").strip():
            logger.warning("ExecuteInvoker: missing parameter", param=key, ticket_id=ticket_id)
            return
    user_id = _override_user_id(config, user_id)
    # Znuny moves DynamicField_UserID to UserID *after* the override: the value reaches the
    # invoker data but does not change the acting user.
    if config.get("DynamicField_UserID") is not None:
        config["UserID"] = config.pop("DynamicField_UserID")
    ws = await load_gi_webservice_by_name(session, sysconfig, str(config["Webservice"]))
    if ws is None:
        logger.error(
            "ExecuteInvoker: webservice not found",
            webservice=config["Webservice"],
            ticket_id=ticket_id,
        )
        return
    await enqueue_invocation(
        session,
        payload=GiInvokePayload(
            webservice_id=ws.id,
            invoker=str(config["Invoker"]),
            data={
                "Origin": "TransitionAction",
                "TransitionActionConfig": config,
                "TicketID": ticket_id,
            },
            asynchronous=True,
            origin="transition_action",
        ),
        created_by=user_id,
    )
