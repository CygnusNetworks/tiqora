"""Safe attachment delivery (shared by agent + portal endpoints).

Attachments are attacker-controlled bytes (inbound email MIME parts, portal
uploads) served from an authenticated same-origin context. Serving them
``inline`` with their stored ``Content-Type`` is a stored-XSS vector: an
``text/html`` / ``image/svg+xml`` part runs script on the session origin. This
helper neutralizes that:

- **Active types** (html/xhtml/xml/svg) are downgraded to ``text/plain`` so the
  browser never executes them.
- ``inline`` is honored **only** for a small allowlist of raster image types;
  everything else is forced to ``attachment`` (download, not render).
- Every response carries ``Content-Security-Policy: sandbox`` (unique opaque
  origin, scripts disabled) and ``X-Content-Type-Options: nosniff`` as
  defense-in-depth, and the ``filename`` is sanitized before it goes into the
  quoted ``Content-Disposition`` parameter.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi.responses import Response

# Raster images that are safe to render inline (embedded CID images etc.).
# SVG is deliberately excluded — it can carry script.
_INLINE_SAFE_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }
)

# Types the browser would treat as active content — never serve with their own
# Content-Type; downgrade to text/plain.
_ACTIVE_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "image/svg+xml",
        "text/xml",
        "application/xml",
        "text/xsl",
        "application/mathml+xml",
    }
)


def _neutralize_type(content_type: str | None) -> str:
    ct = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if ct in _ACTIVE_TYPES or ct.endswith("+xml"):
        return "text/plain"
    return ct or "application/octet-stream"


def _sanitize_filename(filename: str) -> str:
    """Strip characters that would break the quoted Content-Disposition param
    (`"` / `\\`) or attempt header injection (CR/LF)."""
    return filename.replace("\\", "").replace('"', "").replace("\r", "").replace("\n", "")


def safe_attachment_response(
    content: bytes,
    content_type: str | None,
    filename: str | None,
    disposition: str | None,
    *,
    force_download: bool = False,
) -> Response:
    ct = _neutralize_type(content_type)
    want_inline = (not force_download) and (disposition or "").lower() == "inline"
    inline = want_inline and ct in _INLINE_SAFE_TYPES
    disp_kind = "inline" if inline else "attachment"

    headers: dict[str, str] = {
        # Sandboxed opaque origin, scripts disabled — even a mislabeled body
        # cannot execute. setdefault in the global middleware won't override it.
        "Content-Security-Policy": "sandbox",
        "X-Content-Type-Options": "nosniff",
    }
    if filename:
        safe = _sanitize_filename(filename)
        headers["Content-Disposition"] = (
            f"{disp_kind}; filename=\"{safe}\"; filename*=UTF-8''{quote(safe)}"
        )
    else:
        headers["Content-Disposition"] = disp_kind

    return Response(content=content, media_type=ct, headers=headers)


__all__ = ["safe_attachment_response"]
