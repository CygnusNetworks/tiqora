"""Avatar image normalization: formats, metadata stripping, size guards."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from tiqora.avatars.normalize import (
    AVATAR_PX,
    InvalidImageError,
    normalize_image,
    sniff_upload_type,
)


def _encode(img: Image.Image, fmt: str, **kw: object) -> bytes:
    out = io.BytesIO()
    img.save(out, format=fmt, **kw)
    return out.getvalue()


def _decode(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def test_photo_is_cover_cropped_to_square_png() -> None:
    src = Image.new("RGB", (400, 200), (10, 20, 30))
    out = _decode(normalize_image(_encode(src, "JPEG"), fit="cover"))
    assert out.format == "PNG"
    assert out.size == (AVATAR_PX, AVATAR_PX)
    # Cover: no transparent letterbox anywhere.
    assert out.convert("RGBA").getpixel((0, 0))[3] == 255


def test_logo_is_contained_on_transparent_background() -> None:
    src = Image.new("RGB", (400, 100), (200, 0, 0))
    out = _decode(normalize_image(_encode(src, "PNG"), fit="contain")).convert("RGBA")
    assert out.size == (AVATAR_PX, AVATAR_PX)
    assert out.getpixel((0, 0))[3] == 0  # letterbox
    assert out.getpixel((AVATAR_PX // 2, AVATAR_PX // 2)) == (200, 0, 0, 255)


def test_exif_is_dropped_and_orientation_applied() -> None:
    exif = Image.Exif()
    exif[0x010F] = "Camera Maker"  # Make
    exif[0x0112] = 6  # rotate 90° CW on display
    src = Image.new("RGB", (300, 100), (0, 0, 255))
    data = _encode(src, "JPEG", exif=exif.tobytes())
    assert b"Camera Maker" in data
    normalized = normalize_image(data, fit="contain")
    out = _decode(normalized).convert("RGBA")
    assert b"Camera Maker" not in normalized
    assert not out.getexif()
    # Rotated: the 300x100 landscape became portrait — letterbox left/right.
    assert out.getpixel((0, AVATAR_PX // 2))[3] == 0
    assert out.getpixel((AVATAR_PX // 2, 0))[3] == 255


def test_ico_uses_largest_frame() -> None:
    src = Image.new("RGBA", (64, 64), (0, 128, 0, 255))
    data = _encode(src, "ICO", sizes=[(16, 16), (32, 32), (64, 64)])
    out = _decode(normalize_image(data, fit="contain", min_source_px=32))
    assert out.size == (AVATAR_PX, AVATAR_PX)


def test_tiny_favicon_is_rejected() -> None:
    data = _encode(Image.new("RGBA", (16, 16), (0, 0, 0, 255)), "ICO", sizes=[(16, 16)])
    with pytest.raises(InvalidImageError):
        normalize_image(data, fit="contain", min_source_px=32)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image at all",
        b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 40,  # truncated PNG
    ],
)
def test_garbage_is_rejected(data: bytes) -> None:
    with pytest.raises(InvalidImageError):
        normalize_image(data, fit="cover")


def test_decompression_bomb_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.avatars import normalize

    monkeypatch.setattr(normalize, "MAX_SOURCE_PIXELS", 100 * 100)
    data = _encode(Image.new("L", (200, 200), 0), "PNG")
    with pytest.raises(InvalidImageError):
        normalize_image(data, fit="cover")


def test_sniff_upload_type() -> None:
    img = Image.new("RGB", (8, 8))
    assert sniff_upload_type(_encode(img, "PNG")) == "image/png"
    assert sniff_upload_type(_encode(img, "JPEG")) == "image/jpeg"
    assert sniff_upload_type(_encode(img, "WEBP")) == "image/webp"
    assert sniff_upload_type(_encode(img, "GIF")) is None
    assert sniff_upload_type(b"<svg/>") is None
