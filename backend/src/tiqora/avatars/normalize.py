"""Turn any accepted raster image into a square PNG of fixed size.

Input is untrusted (uploads, vCards, third-party websites). Only a small set
of decoders is enabled, oversized canvases are refused before any pixel is
decoded, and the result is drawn onto a fresh canvas so no metadata (EXIF,
GPS, ICC, text chunks) survives.
"""

from __future__ import annotations

import io
import warnings
from typing import Literal

from PIL import Image, ImageOps

#: Edge length of every stored / served avatar.
AVATAR_PX = 256

#: Refuse canvases above this before decoding (decompression bombs). A 24 MP
#: camera photo still fits; a 1x1 px PNG claiming 50000x50000 does not.
MAX_SOURCE_PIXELS = 40_000_000

#: Decoders Pillow may use. No SVG (not raster), no exotic formats.
SOURCE_FORMATS = ("PNG", "JPEG", "WEBP", "GIF", "ICO", "BMP")

Fit = Literal["cover", "contain"]


class InvalidImageError(ValueError):
    """The bytes are not a usable image (corrupt, unsupported, too big/small)."""


def _open(data: bytes) -> Image.Image:
    with warnings.catch_warnings():
        # Pillow only warns between 1x and 2x its own limit — make that fatal.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            img = Image.open(io.BytesIO(data), formats=SOURCE_FORMATS)
            width, height = img.size
            if width * height > MAX_SOURCE_PIXELS:
                raise InvalidImageError("image dimensions too large")
            # ICO opens at its largest entry already; animations: first frame.
            img.seek(0)
            img.load()
        except InvalidImageError:
            raise
        except (
            OSError,
            ValueError,
            SyntaxError,
            EOFError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise InvalidImageError("not a readable image") from exc
    return img


def normalize_image(
    data: bytes,
    *,
    fit: Fit,
    size: int = AVATAR_PX,
    min_source_px: int = 0,
) -> bytes:
    """Return *data* as a ``size`` x ``size`` PNG.

    ``cover`` crops photos to fill the square; ``contain`` letterboxes logos on
    a transparent background. Images whose shorter edge is below
    ``min_source_px`` are rejected (16 px favicons look worse than initials).
    """
    img = _open(data)
    if min(img.size) < max(min_source_px, 1):
        raise InvalidImageError("image too small")
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGBA")
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if fit == "cover":
        canvas.paste(ImageOps.fit(img, (size, size), Image.Resampling.LANCZOS), (0, 0))
    else:
        scale = size / max(img.size)
        width = max(1, round(img.width * scale))
        height = max(1, round(img.height * scale))
        scaled = img.resize((width, height), Image.Resampling.LANCZOS)
        canvas.paste(scaled, ((size - width) // 2, (size - height) // 2))
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def sniff_upload_type(data: bytes) -> str | None:
    """MIME type of an agent upload by magic bytes; only PNG, JPEG and WebP."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
