"""``tiqora migrate ...`` CLI: the gated Alembic migration entrypoint.

Why this exists instead of a bare ``alembic upgrade head``:

Alembic constructs its ``ScriptDirectory`` — and thus resolves ``head`` — from
the ``Config`` object *before* ``env.py`` runs. That means ``env.py`` cannot
decide which migration chains are visible; the only effective lever is the
``version_locations`` value present on the ``Config`` at command time.

So ``alembic.ini`` lists only ``versions_tiqora`` (safe default), and this
command appends ``versions_owned`` at runtime **only** when the schema
ownership gate is active (``TIQORA_SCHEMA_OWNERSHIP=1`` env flag *and* the
``tiqora_settings`` DB marker). This keeps parallel-operation deployments
from ever applying owned migrations — which alter Znuny-owned tables — by
accident.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.ownership import get_ownership_state

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
ALEMBIC_DIR = BACKEND_ROOT / "alembic"
TIQORA_LOCATION = "alembic/versions_tiqora"
OWNED_LOCATION = "alembic/versions_owned"


async def _ownership_active() -> bool:
    """True only when both gates pass (env flag + DB marker)."""
    settings = get_settings()
    if not settings.schema_ownership:
        return False
    factory = get_session_factory()
    async with factory() as session:
        state = await get_ownership_state(session, settings)
    return state.active


def build_alembic_config(*, include_owned: bool) -> Config:
    """Build an Alembic ``Config`` with the correct ``version_locations``.

    ``include_owned`` appends the owned chain; callers must have verified the
    ownership gate first. Paths are absolute so the command works regardless
    of the process working directory.
    """
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    locations = [str(BACKEND_ROOT / TIQORA_LOCATION)]
    if include_owned:
        locations.append(str(BACKEND_ROOT / OWNED_LOCATION))
    # alembic.ini sets `version_path_separator = os`, so join with os.pathsep.
    cfg.set_main_option("version_locations", os.pathsep.join(locations))
    return cfg


def add_migrate_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("migrate", help="Run Alembic migrations (ownership-gated)")
    m_sub = p.add_subparsers(dest="migrate_command")

    up = m_sub.add_parser("upgrade", help="Upgrade to head (owned chain only if gated)")
    up.add_argument("revision", nargs="?", default="head")
    up.set_defaults(func=_cmd_upgrade)

    down = m_sub.add_parser("downgrade", help="Downgrade to a revision")
    down.add_argument("revision")
    down.set_defaults(func=_cmd_downgrade)

    cur = m_sub.add_parser("current", help="Show the current revision")
    cur.set_defaults(func=_cmd_current)


def _resolve_ownership() -> bool:
    """Resolve the ownership gate in its own event loop, fully completing
    before any Alembic command runs.

    CRITICAL: Alembic's ``command.*`` are synchronous but ``alembic/env.py``
    calls ``asyncio.run()`` internally (async engine). So the ownership check
    must NOT be awaited from a loop that is still open when ``command.upgrade``
    runs — otherwise env.py hits "asyncio.run() cannot be called from a
    running event loop". We therefore run the async check to completion here
    and return a plain bool; the command call below runs with no active loop.
    """
    return asyncio.run(_ownership_active())


def _cmd_upgrade(args: argparse.Namespace) -> int:
    include_owned = _resolve_ownership()
    cfg = build_alembic_config(include_owned=include_owned)
    scope = "tiqora + owned" if include_owned else "tiqora only"
    print(f"Running migrations ({scope}) -> {args.revision}")  # noqa: T201
    command.upgrade(cfg, args.revision)
    return 0


def _cmd_downgrade(args: argparse.Namespace) -> int:
    # Downgrade must see whatever chain the target revision lives in; include
    # owned when the gate is active so an operator can roll an owned migration
    # back. Downgrading a tiqora revision never needs the owned chain.
    cfg = build_alembic_config(include_owned=_resolve_ownership())
    command.downgrade(cfg, args.revision)
    return 0


def _cmd_current(args: argparse.Namespace) -> int:
    cfg = build_alembic_config(include_owned=_resolve_ownership())
    command.current(cfg)
    return 0


def run_migrate(args: argparse.Namespace) -> int:
    if not getattr(args, "migrate_command", None):
        print("usage: tiqora migrate {upgrade|downgrade|current}")  # noqa: T201
        return 2
    # args.func is a plain sync callable: it resolves ownership (its own
    # asyncio.run) and then calls the sync Alembic command, so there is never
    # a running loop when env.py does its own asyncio.run().
    exit_code: int = args.func(args)
    return exit_code
