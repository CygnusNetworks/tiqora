#!/usr/bin/env python3
"""Hold N web-chat SSE streams open for M minutes against a TEST instance.

Sizing tool for the chat fan-out (docs/deployment.md). It creates N visitor sessions
(``POST /api/chat/v1/sessions``, one first message each), opens one
``GET .../sessions/{id}/stream`` per session and keeps them open, then prints the number
of streams held, errors, and the RSS of the API process if ``--pid`` is given.

NEVER point this at a production host: every session creates a real ticket.

Prerequisites on the test instance (the defaults would throttle one load generator):
  * the web chat channel is on and a chat config with ``--key`` allows ``--website-origin``;
  * the config's rate limits are raised (``sessions_per_config_minute`` 300,
    ``sessions_per_ip_10min`` 100) or ``--rate`` is kept below them;
  * ``TIQORA_CHAT_MAX_STREAMS`` >= N;
  * for the per-IP stream cap (6) the script sends a distinct ``X-Forwarded-For`` per
    session, which only counts when the peer is trusted: set
    ``TIQORA_TRUSTED_PROXIES=0.0.0.0/0`` (test instance only).

Example::

    uv run python scripts/webchat_stream_load.py --base-url http://localhost:8000 \\
        --key <public key> --website-origin https://www.example.org \\
        --streams 200 --minutes 5 --pid 1234
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

PREFIX = "/api/chat/v1"


@dataclass
class Stats:
    created: int = 0
    held: int = 0  # streams currently open
    peak_held: int = 0
    opened: int = 0
    events: int = 0
    closed_early: int = 0
    errors: dict[str, int] = field(default_factory=dict)

    def error(self, kind: str) -> None:
        self.errors[kind] = self.errors.get(kind, 0) + 1


def fake_ip(n: int) -> str:
    """A distinct private address per session (10.0.1.0 + n)."""
    return str(ipaddress.IPv4Address(0x0A000000 + 256 + n))


async def create_session(
    client: httpx.AsyncClient, args: argparse.Namespace, n: int, stats: Stats
) -> tuple[str, str] | None:
    body = {
        "key": args.key,
        "origin": args.website_origin,
        "visitor_id": secrets.token_urlsafe(18),
        "message": f"load test {n}",
        "client_id": secrets.token_hex(8),
        "language": "en",
    }
    headers = {"Origin": args.own_origin, "X-Forwarded-For": fake_ip(n)}
    for _ in range(5):
        try:
            resp = await client.post(PREFIX + "/sessions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            stats.error(f"create:{type(exc).__name__}")
            return None
        if resp.status_code == 201:
            data = resp.json()
            stats.created += 1
            return data["session_id"], data["token"]
        if resp.status_code in (429, 503):
            await asyncio.sleep(min(float(resp.headers.get("Retry-After", "5")), 30))
            continue
        stats.error(f"create:{resp.status_code}:{resp.text[:60]}")
        return None
    stats.error("create:retries_exhausted")
    return None


async def hold_stream(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    n: int,
    session: tuple[str, str],
    deadline: float,
    stats: Stats,
) -> None:
    session_id, token = session
    headers = {
        "Origin": args.own_origin,
        "X-Chat-Token": token,
        "X-Forwarded-For": fake_ip(n),
        "Accept": "text/event-stream",
    }
    url = f"{PREFIX}/sessions/{session_id}/stream"
    try:
        async with client.stream("GET", url, headers=headers, timeout=None) as resp:
            if resp.status_code != 200:
                stats.error(f"stream:{resp.status_code}")
                return
            stats.opened += 1
            stats.held += 1
            stats.peak_held = max(stats.peak_held, stats.held)
            try:
                async for line in resp.aiter_lines():
                    if line.startswith(("event:", "data:")):
                        stats.events += 1
                    if time.monotonic() >= deadline:
                        return
                stats.closed_early += 1  # server ended the stream before the deadline
            finally:
                stats.held -= 1
    except httpx.HTTPError as exc:
        stats.error(f"stream:{type(exc).__name__}")


def rss_mib(pid: int) -> float | None:
    """RSS of *pid* in MiB via ``ps`` (works on Linux and macOS)."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, integer pid
            ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=True
        ).stdout.strip()
        return int(out) / 1024
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


async def run(args: argparse.Namespace) -> int:
    stats = Stats()
    limits = httpx.Limits(max_connections=args.streams + 20, max_keepalive_connections=20)
    deadline = time.monotonic() + args.minutes * 60 + args.streams / args.rate
    tasks: list[asyncio.Task[None]] = []
    started = time.monotonic()
    rss_before = rss_mib(args.pid) if args.pid else None

    async with httpx.AsyncClient(base_url=args.base_url, limits=limits, timeout=30) as client:

        async def one(n: int) -> None:
            session = await create_session(client, args, n, stats)
            if session is not None:
                await hold_stream(client, args, n, session, deadline, stats)

        for n in range(args.streams):
            tasks.append(asyncio.create_task(one(n)))
            await asyncio.sleep(1 / args.rate)
        print(f"ramp-up done in {time.monotonic() - started:.0f}s, holding ...", flush=True)
        hold_until = time.monotonic() + args.minutes * 60
        while time.monotonic() < hold_until:
            await asyncio.sleep(min(30, max(1, hold_until - time.monotonic())))
            rss = rss_mib(args.pid) if args.pid else None
            print(
                f"t+{time.monotonic() - started:.0f}s held={stats.held} "
                f"errors={sum(stats.errors.values())}"
                + (f" rss={rss:.0f}MiB" if rss is not None else ""),
                flush=True,
            )
        deadline = 0.0  # release every stream
        await asyncio.gather(*tasks, return_exceptions=True)

    rss_after = rss_mib(args.pid) if args.pid else None
    print("--- result ---")
    print(f"streams requested : {args.streams}")
    print(f"sessions created  : {stats.created}")
    print(f"streams opened    : {stats.opened} (peak concurrently held: {stats.peak_held})")
    print(f"closed early      : {stats.closed_early}")
    print(f"SSE lines received: {stats.events}")
    print(f"errors            : {sum(stats.errors.values())} {dict(stats.errors) or ''}")
    if rss_before is not None and rss_after is not None:
        print(f"API RSS           : {rss_before:.0f} MiB before, {rss_after:.0f} MiB after")
    return 0 if stats.peak_held >= args.streams and not stats.errors else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--base-url", required=True, help="API origin of the TEST instance")
    p.add_argument("--key", required=True, help="public key of a chat config")
    p.add_argument("--website-origin", required=True, help="an origin allowed by that config")
    p.add_argument("--streams", "-n", type=int, default=200, help="number of streams (N)")
    p.add_argument("--minutes", "-m", type=float, default=5.0, help="hold time (M)")
    p.add_argument("--rate", type=float, default=4.0, help="new sessions per second")
    p.add_argument("--pid", type=int, help="PID of the API process, to report its RSS")
    p.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow a non-local --base-url (never use against production)",
    )
    args = p.parse_args(argv)
    host = urlsplit(args.base_url).hostname or ""
    if host not in {"localhost", "127.0.0.1", "::1"} and not args.allow_remote:
        p.error("refusing a non-local --base-url (every session creates a ticket)")
    parts = urlsplit(args.base_url)
    args.own_origin = f"{parts.scheme}://{parts.netloc}"
    return args


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_args())))
