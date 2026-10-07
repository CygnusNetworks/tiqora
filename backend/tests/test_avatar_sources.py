"""Outbound avatar sources and the caching service — no network, no DB.

httpx runs on a ``MockTransport`` that routes on the ``Host`` header (requests
go to the pinned IP), and DNS is a stub resolver, so SSRF blocking is
exercised by mapping a hostname to a private address.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from PIL import Image
from redis.exceptions import ConnectionError as RedisConnectionError

from tiqora.avatars.freemail import is_freemail
from tiqora.avatars.service import (
    ERROR_TTL_SECONDS,
    HIT_TTL_SECONDS,
    MISS_TTL_SECONDS,
    AvatarService,
    cache_key,
    domain_from_url,
    email_key,
    normalize_domain,
)
from tiqora.avatars.sources import (
    MAX_BODY_BYTES,
    FetchError,
    fetch,
    fetch_favicon,
    fetch_gravatar,
    parse_icon_links,
)

PUBLIC_IP = "93.184.216.34"
SHA = hashlib.sha256(b"jane.doe@example.org").hexdigest()

Handler = Callable[[httpx.Request], httpx.Response]


def _png(size: int = 64, color: tuple[int, int, int] = (0, 0, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (size, size), color).save(out, format="PNG")
    return out.getvalue()


def _ico(size: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (size, size), (0, 120, 0, 255)).save(out, format="ICO", sizes=[(size, size)])
    return out.getvalue()


def _resolver(private: set[str] | None = None) -> Callable[[str, int], list[str]]:
    def resolve(host: str, port: int) -> list[str]:
        del port
        return ["10.0.0.7"] if host in (private or set()) else [PUBLIC_IP]

    return resolve


class _Recorder:
    """MockTransport handler keyed on (host, path); records what was asked."""

    def __init__(self, routes: dict[tuple[str, str], httpx.Response | Handler]) -> None:
        self.routes = routes
        self.seen: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = (request.headers["host"], request.url.path)
        self.seen.append(key)
        assert request.url.host == PUBLIC_IP, "request must go to the pinned IP"
        route = self.routes.get(key)
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route


def _client(recorder: _Recorder) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(recorder), follow_redirects=False)


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, html=body)


def _image(data: bytes, ctype: str = "image/png") -> httpx.Response:
    return httpx.Response(200, content=data, headers={"content-type": ctype})


# ---- Gravatar --------------------------------------------------------------


async def test_gravatar_hit() -> None:
    def gravatar(request: httpx.Request) -> httpx.Response:
        assert request.url.params["d"] == "404"
        assert request.url.params["s"] == "256"
        return _image(_png(256))

    rec = _Recorder({("gravatar.com", f"/avatar/{SHA}"): gravatar})
    async with _client(rec) as client:
        data = await fetch_gravatar(client, SHA, resolver=_resolver())
    assert data is not None and Image.open(io.BytesIO(data)).size == (256, 256)


async def test_gravatar_404_is_none() -> None:
    rec = _Recorder({})
    async with _client(rec) as client:
        assert await fetch_gravatar(client, SHA, resolver=_resolver()) is None
    assert rec.seen == [("gravatar.com", f"/avatar/{SHA}")]


# ---- favicon / apple-touch-icon -------------------------------------------


def test_parse_icon_links_prefers_largest_and_skips_svg() -> None:
    html = """<html><head>
      <link rel="icon" href="/fav-16.png" sizes="16x16">
      <link rel="icon" type="image/svg+xml" href="/logo.svg">
      <link rel="mask-icon" href="/mask.png">
      <link rel="Shortcut Icon" href="favicon-32.png" sizes="32x32">
      <link rel="apple-touch-icon" href="//cdn.example.com/apple.png">
      <link rel="icon" href="/big.png" sizes="96x96 192x192">
      <link rel="icon" href="data:image/png;base64,AAAA">
    </head></html>"""
    assert parse_icon_links(html, "https://www.example.com/de/") == [
        "https://www.example.com/big.png",
        "https://cdn.example.com/apple.png",
        "https://www.example.com/de/favicon-32.png",
        "https://www.example.com/fav-16.png",
    ]


async def test_favicon_uses_best_link_icon() -> None:
    home = _html(
        '<link rel="icon" href="/small.png" sizes="16x16">'
        '<link rel="apple-touch-icon" sizes="180x180" href="/apple.png">'
    )
    rec = _Recorder(
        {
            ("example.com", "/"): home,
            ("example.com", "/apple.png"): _image(_png(180)),
        }
    )
    async with _client(rec) as client:
        data = await fetch_favicon(client, "example.com", resolver=_resolver())
    assert data is not None
    assert Image.open(io.BytesIO(data)).size == (256, 256)
    assert ("example.com", "/small.png") not in rec.seen


async def test_favicon_falls_back_to_favicon_ico() -> None:
    rec = _Recorder(
        {
            ("example.com", "/"): _html("<html><head><title>x</title></head></html>"),
            ("example.com", "/favicon.ico"): _image(_ico(48), "image/x-icon"),
        }
    )
    async with _client(rec) as client:
        data = await fetch_favicon(client, "example.com", resolver=_resolver())
    assert data is not None
    assert rec.seen[-1] == ("example.com", "/favicon.ico")


async def test_tiny_favicon_counts_as_no_picture() -> None:
    rec = _Recorder(
        {
            ("example.com", "/"): _html("<p>no icons</p>"),
            ("example.com", "/favicon.ico"): _image(_ico(16), "image/x-icon"),
        }
    )
    async with _client(rec) as client:
        assert await fetch_favicon(client, "example.com", resolver=_resolver()) is None


async def test_redirects_are_followed_and_relative_hrefs_resolved() -> None:
    rec = _Recorder(
        {
            ("example.com", "/"): httpx.Response(
                301, headers={"location": "https://www.example.com/"}
            ),
            ("www.example.com", "/"): httpx.Response(302, headers={"location": "/de/start"}),
            ("www.example.com", "/de/start"): _html('<link rel="icon" sizes="64x64" href="i.png">'),
            ("www.example.com", "/de/i.png"): _image(_png(64)),
        }
    )
    async with _client(rec) as client:
        data = await fetch_favicon(client, "example.com", resolver=_resolver())
    assert data is not None
    assert ("www.example.com", "/de/i.png") in rec.seen


async def test_redirect_into_private_network_is_blocked() -> None:
    rec = _Recorder(
        {
            ("example.com", "/"): _html('<link rel="icon" sizes="64x64" href="/i.png">'),
            ("example.com", "/i.png"): httpx.Response(
                302, headers={"location": "http://intranet.example.com/secret.png"}
            ),
        }
    )
    async with _client(rec) as client:
        data = await fetch_favicon(
            client, "example.com", resolver=_resolver(private={"intranet.example.com"})
        )
    assert data is None
    assert all(host != "intranet.example.com" for host, _ in rec.seen)


async def test_private_address_is_never_contacted() -> None:
    rec = _Recorder({("intranet.example.com", "/"): _html("")})
    async with _client(rec) as client:
        data = await fetch_favicon(
            client, "intranet.example.com", resolver=_resolver(private={"intranet.example.com"})
        )
    assert data is None
    assert rec.seen == []


async def test_redirect_loop_gives_up() -> None:
    loop = httpx.Response(302, headers={"location": "/"})
    rec = _Recorder({("example.com", "/"): loop})
    async with _client(rec) as client:
        assert await fetch(client, "https://example.com/", resolver=_resolver()) is None
    assert len(rec.seen) == 4  # first request + 3 redirects


async def test_declared_oversize_body_is_refused() -> None:
    big = b"x" * (MAX_BODY_BYTES + 1)
    rec = _Recorder({("example.com", "/big"): httpx.Response(200, content=big)})
    async with _client(rec) as client:
        assert await fetch(client, "https://example.com/big", resolver=_resolver()) is None


async def test_streamed_oversize_body_is_abandoned() -> None:
    produced = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal produced
        for _ in range(100):
            produced += 64 * 1024
            yield b"x" * (64 * 1024)

    rec = _Recorder({("example.com", "/stream"): lambda _r: httpx.Response(200, content=chunks())})
    async with _client(rec) as client:
        assert await fetch(client, "https://example.com/stream", resolver=_resolver()) is None
    assert produced <= MAX_BODY_BYTES + 64 * 1024


async def test_server_error_is_transient() -> None:
    rec = _Recorder({("example.com", "/"): httpx.Response(503)})
    async with _client(rec) as client:
        with pytest.raises(FetchError):
            await fetch(client, "https://example.com/", resolver=_resolver())


# ---- domain helpers ----------------------------------------------------------


def test_freemail() -> None:
    for domain in ("gmail.com", "GMX.de", "web.de", "hotmail.co.uk", "yahoo.fr", "live.de"):
        assert is_freemail(domain), domain
    for domain in ("example.com", "cygnusnetworks.de", "mail.example.com"):
        assert not is_freemail(domain), domain


def test_domain_normalization() -> None:
    assert normalize_domain("WWW.Example.COM.") == "example.com"
    assert normalize_domain("sub.example.co.uk") == "sub.example.co.uk"
    assert normalize_domain("xn--mller-kva.de") == "xn--mller-kva.de"
    for bad in ("localhost", "10.0.0.1", "exa mple.com", "example.com/path", "-a.com", ""):
        assert normalize_domain(bad) is None, bad
    assert domain_from_url("https://www.Example.com:8443/kontakt") == "example.com"
    assert domain_from_url("example.org") == "example.org"
    assert domain_from_url("  ") is None
    assert domain_from_url(None) is None


def test_email_key_is_sha256_of_normalized_address() -> None:
    assert email_key("  Jane.Doe@Example.org ") == SHA


# ---- service: cache, single flight, freemail -------------------------------


class _FakeRedis:
    def __init__(self, *, down: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.down = down

    def _check(self) -> None:
        if self.down:
            raise RedisConnectionError("redis is down")

    async def get(self, key: str) -> str | None:
        self._check()
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._check()
        self.data[key] = value
        if ex is not None:
            self.ttl[key] = ex

    async def delete(self, *keys: str) -> None:
        self._check()
        for key in keys:
            self.data.pop(key, None)


class _NoRows:
    def first(self) -> None:
        return None

    def scalar_one_or_none(self) -> None:
        return None


class _EmptySession:
    """No uploads, no settings rows (defaults apply)."""

    async def execute(self, stmt: Any) -> _NoRows:
        del stmt
        return _NoRows()

    async def rollback(self) -> None:
        """The service ends its read transaction before fetching."""


def _service(rec: _Recorder, redis_client: _FakeRedis) -> AvatarService:
    return AvatarService(
        _EmptySession(),  # type: ignore[arg-type]
        redis_client,
        transport=httpx.MockTransport(rec),
        resolver=_resolver(),
    )


async def test_service_caches_hits() -> None:
    rec = _Recorder({("gravatar.com", f"/avatar/{SHA}"): _image(_png(256))})
    redis_client = _FakeRedis()
    service = _service(rec, redis_client)
    first = await service.person(SHA)
    second = await service.person(SHA)
    assert first is not None and second == first
    assert len(rec.seen) == 1
    key = cache_key("email", SHA)
    assert base64.b64decode(redis_client.data[key]) == first.data
    assert redis_client.ttl[key] == HIT_TTL_SECONDS


async def test_service_caches_misses() -> None:
    rec = _Recorder({})
    redis_client = _FakeRedis()
    service = _service(rec, redis_client)
    assert await service.person(SHA) is None
    assert await service.person(SHA) is None
    assert len(rec.seen) == 1
    key = cache_key("email", SHA)
    assert redis_client.data[key] == ""
    assert redis_client.ttl[key] == MISS_TTL_SECONDS


async def test_service_retries_transient_errors_sooner() -> None:
    rec = _Recorder({("gravatar.com", f"/avatar/{SHA}"): httpx.Response(502)})
    redis_client = _FakeRedis()
    assert await _service(rec, redis_client).person(SHA) is None
    assert redis_client.ttl[cache_key("email", SHA)] == ERROR_TTL_SECONDS


async def test_service_single_flight() -> None:
    rec = _Recorder({("gravatar.com", f"/avatar/{SHA}"): _image(_png(256))})
    service = _service(rec, _FakeRedis())
    results = await asyncio.gather(*(service.person(SHA) for _ in range(8)))
    assert all(r is not None for r in results)
    assert len(rec.seen) == 1


async def test_service_works_without_redis() -> None:
    rec = _Recorder({("gravatar.com", f"/avatar/{SHA}"): _image(_png(256))})
    assert await _service(rec, _FakeRedis(down=True)).person(SHA) is not None


async def test_freemail_domain_is_never_fetched() -> None:
    rec = _Recorder({("gmail.com", "/"): _html("")})
    redis_client = _FakeRedis()
    assert await _service(rec, redis_client).company("gmail.com") is None
    assert rec.seen == []
    assert redis_client.data == {}
