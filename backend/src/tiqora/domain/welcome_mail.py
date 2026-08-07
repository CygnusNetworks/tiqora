"""One-off transactional email, outside the Znuny notification-template system.

Used for the "new agent" welcome mail (auto-generated password). Reuses the
same SMTP resolution as the outbound-mail test send
(:mod:`tiqora.api.v1.admin.mail_outbound`).
"""

from __future__ import annotations

import aiosmtplib
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.email.smtp import build_message
from tiqora.domain.mail_outbound import resolve_outbound_smtp


class WelcomeMailError(RuntimeError):
    """Outbound mail is disabled/misconfigured — sending was not attempted."""


async def send_transactional_email(
    session: AsyncSession, *, to_addr: str, subject: str, body: str
) -> None:
    resolved = await resolve_outbound_smtp(session)
    if not resolved.enabled or not resolved.host:
        raise WelcomeMailError("Outbound mail is not enabled or has no host configured")

    use_tls = resolved.security == "ssl"
    start_tls = True if resolved.security == "starttls" else False if use_tls else None
    username = resolved.auth_user if resolved.auth_type in ("password", "oauth2_token") else None
    password = resolved.auth_password if resolved.auth_type == "password" else None

    send_kwargs: dict[str, object] = {
        "hostname": resolved.host,
        "port": resolved.port,
        "username": username or None,
        "timeout": float(resolved.timeout_seconds),
        "use_tls": use_tls,
        "start_tls": start_tls,
    }
    if resolved.auth_type == "oauth2_token":
        if not resolved.oauth2_token_config_id:
            raise WelcomeMailError("OAuth2 token config not found")
        from tiqora.domain.mail_outbound import make_oauth_token_generator

        send_kwargs["oauth_token_generator"] = make_oauth_token_generator(
            session, resolved.oauth2_token_config_id
        )
    else:
        send_kwargs["password"] = password or None

    from_addr = resolved.from_default.strip() or "Tiqora <noreply@localhost>"
    message = build_message(
        from_addr=from_addr,
        to_addrs=to_addr,
        cc_addrs=None,
        subject=subject,
        body=body,
        content_type="text/plain; charset=utf-8",
        in_reply_to=None,
        loop_hint=True,
    )
    await aiosmtplib.send(message, **send_kwargs)  # type: ignore[arg-type]
