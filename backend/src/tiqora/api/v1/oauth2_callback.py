"""Public OAuth2 authorization-code callback for mail token configs.

Znuny equivalent: ``get-oauth2-token-by-authorization-code.pl``.

The IdP redirects here after the admin authorizes the app. No session is
required — the ``state=TokenConfigID{id}`` parameter identifies the config
(same convention as Znuny). Tokens are written to the shared legacy
``oauth2_token`` table.
"""

from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from tiqora.api.deps import DbSession
from tiqora.config import get_settings
from tiqora.domain.oauth2_mail import (
    OAuth2MailError,
    OAuth2NotAvailableError,
    handle_authorization_callback,
)

router = APIRouter(tags=["oauth2"])


def _page(title: str, body: str, *, ok: bool) -> HTMLResponse:
    color = "#0a7" if ok else "#c33"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{escape(title)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 36rem; margin: 3rem auto;
           padding: 0 1rem; color: #222; }}
    h1 {{ color: {color}; font-size: 1.25rem; }}
    a {{ color: #06c; }}
    code {{ background: #f4f4f4; padding: 0.1em 0.3em; border-radius: 3px; }}
  </style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <p>{body}</p>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200 if ok else 400)


@router.get("/oauth2/callback", response_class=HTMLResponse)
async def oauth2_authorization_callback(request: Request, session: DbSession) -> HTMLResponse:
    """Exchange ``code`` for tokens; Znuny-compatible ``state=TokenConfigID{{n}}``."""
    params: dict[str, str] = {k: str(v) for k, v in request.query_params.multi_items()}
    # Prefer last value if duplicates.
    flat: dict[str, str] = {}
    for k, v in params.items():
        flat[k] = v

    settings = get_settings()
    admin_link = "/admin/oauth2-tokens"
    try:
        token = await handle_authorization_callback(
            session, query_params=flat, user_id=1, settings=settings
        )
    except OAuth2NotAvailableError as exc:
        return _page("OAuth2 not available", escape(str(exc)), ok=False)
    except OAuth2MailError as exc:
        return _page(
            "Authorization failed",
            f'{escape(str(exc))}<br/><br/><a href="{admin_link}">Back to OAuth2 admin</a>',
            ok=False,
        )
    except Exception as exc:  # noqa: BLE001
        return _page(
            "Authorization failed",
            f'{escape(str(exc))}<br/><br/><a href="{admin_link}">Back to OAuth2 admin</a>',
            ok=False,
        )

    cfg_id = token.token_config_id
    return _page(
        "Token saved",
        (
            f"Access token for config <code>#{cfg_id}</code> was stored successfully. "
            f"You can close this window."
            f'<br/><br/><a href="{admin_link}?authorized={cfg_id}">Back to OAuth2 admin</a>'
        ),
        ok=True,
    )


# Keep typing import used if we expand later.
_Any = Any
