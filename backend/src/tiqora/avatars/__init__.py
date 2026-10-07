"""Customer avatars and company logos.

Pictures reach the browser only through ``/api/v1/avatars/*``: an image the
agent uploaded (``tiqora_avatar``) wins, otherwise Gravatar (people) or the
website's favicon / apple-touch-icon (companies) is fetched server-side,
normalized to a 256 px PNG and cached in Redis. The page CSP stays
``img-src 'self'`` and no agent browser talks to a third party.
"""
