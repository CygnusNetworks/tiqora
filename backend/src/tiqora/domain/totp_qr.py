"""SVG QR-code rendering for the TOTP `otpauth://` provisioning URI.

Kept in its own module so ``qrcode`` stays an isolated dependency and the
rendering (pure CPU, sub-millisecond for a short URI) is trivially unit
testable without a DB/Redis fixture.
"""

from __future__ import annotations

import io

import qrcode
import qrcode.image.svg


def totp_qr_svg(otpauth_uri: str) -> str:
    """Render *otpauth_uri* as a standalone SVG document (string)."""
    img = qrcode.make(otpauth_uri, image_factory=qrcode.image.svg.SvgImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")
