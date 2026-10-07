"""Outbound picture sources: Gravatar (people) and website icons (companies).

Every request goes through :func:`tiqora.security.outbound.pin_outbound_url`
— connect to the checked IP, never to whatever the hostname resolves to a
moment later — with ``follow_redirects=False``. Redirects are followed here by
hand (at most :data:`MAX_REDIRECTS`), each hop validated again, so a public
site cannot bounce the fetch into the internal network. Bodies are streamed
and abandoned past :data:`MAX_BODY_BYTES`.

Result contract of the ``fetch_*`` functions: normalized PNG bytes, ``None``
for a definite "no picture" (404, blocked address, no usable icon), or
:class:`FetchError` for a transient failure worth retrying sooner.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from tiqora.avatars.normalize import AVATAR_PX, InvalidImageError, normalize_image
from tiqora.security.outbound import HostResolver, OutboundURLError, pin_outbound_url

FETCH_TIMEOUT_SECONDS = 3.0
MAX_BODY_BYTES = 1024 * 1024
MAX_REDIRECTS = 3
#: Favicons below this edge length count as "no picture".
MIN_FAVICON_PX = 32
#: ``<link>`` icons tried per domain (best first) before ``/favicon.ico``.
MAX_LINK_ICONS = 3

_USER_AGENT = "Mozilla/5.0 (compatible; Tiqora avatar fetcher)"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ICON_RELS = frozenset({"icon", "apple-touch-icon", "apple-touch-icon-precomposed"})


class FetchError(Exception):
    """Transient outbound failure (timeout, connection, 5xx, 429)."""


@dataclass(frozen=True)
class Fetched:
    url: str  # final URL after redirects (hostname form, not the pinned IP)
    content_type: str
    body: bytes


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    resolver: HostResolver | None = None,
    accept: str = "*/*",
    max_bytes: int = MAX_BODY_BYTES,
) -> Fetched | None:
    """GET *url* safely; ``None`` for a definite miss, :class:`FetchError` if transient."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        try:
            pinned = pin_outbound_url(current, resolver=resolver)
        except OutboundURLError:
            return None
        request = client.build_request(
            "GET",
            pinned.request_url,
            headers=pinned.request_headers({"User-Agent": _USER_AGENT, "Accept": accept}),
            extensions=pinned.request_extensions(),
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise FetchError(str(exc)) from exc
        try:
            status = response.status_code
            location = response.headers.get("location")
            if status in _REDIRECT_STATUSES and location:
                # Relative Location resolves against the hostname URL, never
                # against the pinned-IP request URL.
                current = urljoin(pinned.original_url, location)
                continue
            if status == 429 or status >= 500:
                raise FetchError(f"HTTP {status}")
            if status != 200:
                return None
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                return None
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    return None
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            return Fetched(
                url=pinned.original_url, content_type=content_type.strip().lower(), body=bytes(body)
            )
        except httpx.HTTPError as exc:
            raise FetchError(str(exc)) from exc
        finally:
            await response.aclose()
    return None  # too many redirects


async def fetch_gravatar(
    client: httpx.AsyncClient, email_sha256: str, *, resolver: HostResolver | None = None
) -> bytes | None:
    """Gravatar by SHA-256 of the normalized address; ``d=404`` means "none"."""
    url = f"https://gravatar.com/avatar/{email_sha256}?d=404&s={AVATAR_PX}"
    fetched = await fetch(client, url, resolver=resolver, accept="image/*")
    if fetched is None:
        return None
    try:
        return normalize_image(fetched.body, fit="cover")
    except InvalidImageError:
        return None


@dataclass(frozen=True)
class IconLink:
    href: str
    score: int  # bigger is better (largest declared edge length)


def _icon_size(rel: set[str], sizes: str) -> int:
    best = 0
    for token in sizes.lower().split():
        width, _, height = token.partition("x")
        if width.isdigit() and height.isdigit():
            best = max(best, min(int(width), int(height)))
    if best == 0 and rel & {"apple-touch-icon", "apple-touch-icon-precomposed"}:
        return 180  # Apple's default size when none is declared
    return best


class _IconLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[IconLink] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        values = {name.lower(): (value or "") for name, value in attrs}
        rel = set(values.get("rel", "").lower().split())
        href = values.get("href", "").strip()
        if not href or not rel & _ICON_RELS:
            return
        if "svg" in values.get("type", "").lower():
            return
        path = urlparse(href).path.lower()
        if href.lower().startswith("data:") or path.endswith(".svg"):
            return
        self.links.append(IconLink(href=href, score=_icon_size(rel, values.get("sizes", ""))))


def parse_icon_links(html: str, base_url: str) -> list[str]:
    """Absolute icon URLs from ``<link rel=icon|apple-touch-icon>``, best first."""
    parser = _IconLinkParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — html.parser is lenient; never fail a lookup on markup
        pass
    ordered = sorted(parser.links, key=lambda link: link.score, reverse=True)
    urls: list[str] = []
    for link in ordered:
        absolute = urljoin(base_url, link.href)
        if urlparse(absolute).scheme in ("http", "https") and absolute not in urls:
            urls.append(absolute)
    return urls


def _decode_html(fetched: Fetched) -> str:
    return fetched.body.decode("utf-8", errors="replace")


async def fetch_favicon(
    client: httpx.AsyncClient, domain: str, *, resolver: HostResolver | None = None
) -> bytes | None:
    """Best icon of ``https://{domain}/``: largest ``<link>`` icon, then ``/favicon.ico``."""
    home = f"https://{domain}/"
    transient = False
    base = home
    candidates: list[str] = []
    try:
        page = await fetch(
            client, home, resolver=resolver, accept="text/html,application/xhtml+xml"
        )
    except FetchError:
        page = None
        transient = True
    if page is not None:
        base = page.url
        if "html" in page.content_type or not page.content_type:
            candidates = parse_icon_links(_decode_html(page), base)[:MAX_LINK_ICONS]
    for fallback in (urljoin(base, "/favicon.ico"), urljoin(home, "/favicon.ico")):
        if fallback not in candidates:
            candidates.append(fallback)
    for url in candidates:
        try:
            fetched = await fetch(client, url, resolver=resolver, accept="image/*")
        except FetchError:
            transient = True
            continue
        if fetched is None:
            continue
        try:
            return normalize_image(fetched.body, fit="contain", min_source_px=MIN_FAVICON_PX)
        except InvalidImageError:
            continue
    if transient:
        raise FetchError(f"icon lookup for {domain} incomplete")
    return None
