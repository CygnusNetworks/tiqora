"""Small helpers to drive the real peer container via docker exec.

Uses GOLDEN_COMPOSE_PROJECT / GOLDEN_PEER so multi-peer stacks stay isolated.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent


def _compose_project() -> str:
    peer = os.environ.get("GOLDEN_PEER", "znuny-6.5")
    if env := os.environ.get("GOLDEN_COMPOSE_PROJECT"):
        return env
    # Compose project names may not contain dots (peer ids like znuny-6.5).
    return f"tiqora-golden-{peer.lower().replace('.', '-')}"


def _compose_base() -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        _compose_project(),
        "-f",
        str(GOLDEN_DIR / "docker-compose.golden.yml"),
    ]


def znuny_console(*args: str, check: bool = True) -> str:
    """Run peer console command inside the golden container via docker exec."""
    cmd = [
        *_compose_base(),
        "exec",
        "-T",
        "znuny",
        "/usr/local/bin/znuny-entrypoint.sh",
        "console",
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"peer console command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout


def znuny_perl_eval(code: str) -> str:
    """Run an inline Perl snippet inside the golden container."""
    cmd = [
        *_compose_base(),
        "exec",
        "-T",
        "-u",
        "otrs",
        "znuny",
        "perl",
        "-I",
        "/opt/otrs",
        "-I",
        "/opt/otrs/Kernel/cpan-lib",
        "-e",
        code,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"peer perl eval failed: {result.stderr}\nstdout: {result.stdout}")
    return result.stdout


__all__ = ["znuny_console", "znuny_perl_eval"]
