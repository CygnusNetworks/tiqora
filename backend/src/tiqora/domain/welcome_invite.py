"""The onboarding mail: one link, no credential.

Kept apart from :mod:`tiqora.domain.password_setup` (which owns the token)
and :mod:`tiqora.domain.welcome_mail` (which owns SMTP) so the wording lives
in exactly one place — it is sent both when an agent is created and when an
admin re-issues the link from the user list.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.domain.password_policy import MIN_PASSWORD_LENGTH
from tiqora.domain.password_setup import TOKEN_TTL, issue_token, setup_url
from tiqora.domain.welcome_mail import send_transactional_email


async def send_setup_invite(
    session: AsyncSession,
    *,
    settings: Settings,
    user_id: int,
    login: str,
    first_name: str,
    to_addr: str,
) -> None:
    """Issue a fresh setup link and mail it. Caller owns the transaction —
    the token row must not be committed if the send fails."""
    token = await issue_token(session, user_id)
    url = setup_url(settings, token)
    days = TOKEN_TTL.days
    await send_transactional_email(
        session,
        to_addr=to_addr,
        subject="Ihr Tiqora-Zugang: in zwei Schritten startklar",
        body=(
            f"Hallo {first_name},\n\n"
            "für Sie wurde ein Tiqora-Zugang angelegt. Zwei Schritte, dann "
            "können Sie loslegen:\n\n"
            "1. Passwort festlegen\n"
            f"   {url}\n"
            f"   Mindestens {MIN_PASSWORD_LENGTH} Zeichen. Der Link gilt "
            f"{days} Tage und nur einmal.\n\n"
            "2. Anmelden mit Ihrem Login\n"
            f"   {login}\n\n"
            "Falls der Link nicht mehr funktioniert, fordern Sie bitte einen "
            "neuen bei Ihrer Administration an.\n\n"
            "Sie haben keinen Zugang erwartet? Diese E-Mail können Sie "
            "ignorieren — ohne den Link lässt sich das Konto nicht verwenden."
        ),
    )
