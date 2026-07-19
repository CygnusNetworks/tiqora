"""Small helpers to drive the real Znuny container via docker exec.

Kept out of conftest.py so test modules can import them directly
(``from _helpers import znuny_console, znuny_perl_eval``) without relying on
package-relative conftest imports.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent


def znuny_console(*args: str, check: bool = True) -> str:
    """Run a Znuny console command inside the golden container via docker exec.

    Equivalent to ``bin/otrs.Console.pl <args>`` run as the ``otrs`` user.
    """
    cmd = [
        "docker",
        "compose",
        "-f",
        str(GOLDEN_DIR / "docker-compose.golden.yml"),
        "exec",
        "-T",
        "-u",
        "otrs",
        "znuny",
        "perl",
        "/opt/otrs/bin/otrs.Console.pl",
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"znuny console command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout


def znuny_perl_eval(code: str) -> str:
    """Run an inline Perl snippet inside the golden container.

    ``code`` typically pulls Kernel::System::ObjectManager and prints a
    result to stdout, which is returned here.
    """
    cmd = [
        "docker",
        "compose",
        "-f",
        str(GOLDEN_DIR / "docker-compose.golden.yml"),
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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"znuny perl eval failed: {result.stderr}\nstdout: {result.stdout}")
    return result.stdout


__all__ = ["znuny_console", "znuny_perl_eval"]
