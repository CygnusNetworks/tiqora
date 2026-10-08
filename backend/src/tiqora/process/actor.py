"""Who performs a process action: the interface it came through and the user id writes are
attributed to (Znuny ``AgentTicketProcess`` vs ``CustomerTicketProcess``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Znuny CustomerPanelUserID: the customer interface writes as this user
# (CustomerTicketProcess.pm uses ``CustomerPanelUserID`` for every ticket write).
from tiqora.domain.portal_ticket_service import PORTAL_SYSTEM_USER_ID

Interface = Literal["AgentInterface", "CustomerInterface"]

__all__ = ["Interface", "ProcessActor"]


@dataclass(frozen=True, slots=True)
class ProcessActor:
    interface: Interface
    user_id: int  # whose id writes are attributed to
    customer_user_login: str | None = None
    customer_id: str | None = None

    @classmethod
    def agent(cls, user_id: int) -> ProcessActor:
        return cls(interface="AgentInterface", user_id=user_id)

    @classmethod
    def customer(cls, *, login: str, customer_id: str) -> ProcessActor:
        return cls(
            interface="CustomerInterface",
            user_id=PORTAL_SYSTEM_USER_ID,
            customer_user_login=login,
            customer_id=customer_id,
        )

    @property
    def frontend_action(self) -> str:
        return (
            "AgentTicketProcess" if self.interface == "AgentInterface" else "CustomerTicketProcess"
        )
