"""Regression tests for the schema-ownership migration gate.

These would have caught the production incident where a bare
``alembic upgrade head`` reached the owned chain (which alters Znuny tables)
because ``alembic.ini`` listed ``versions_owned`` and Alembic resolves
``head`` from the Config before ``env.py`` runs. No database required — we
inspect the ``ScriptDirectory`` the Config produces.
"""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory

from tiqora.cli.migrate import ALEMBIC_INI, build_alembic_config

# The current head of the tiqora-only chain and of the owned chain. Update
# these when adding migrations; the assertions below encode the invariant,
# not the exact ids.
TIQORA_HEAD = "20260723_0020"
OWNED_HEAD = "20260719_0006"


def _heads(cfg: Config) -> set[str]:
    return set(ScriptDirectory.from_config(cfg).get_heads())


def test_default_config_stops_at_tiqora_head() -> None:
    """A bare `alembic upgrade head` (default alembic.ini) must NOT see the
    owned chain — head is the tiqora head, and the owned revision is not even
    discoverable."""
    cfg = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())
    assert heads == {TIQORA_HEAD}, heads
    # The owned revision must be absent from the walkable graph entirely.
    all_revs = {rev.revision for rev in script.walk_revisions()}
    assert OWNED_HEAD not in all_revs


def test_migrate_config_without_gate_excludes_owned() -> None:
    """The CLI config builder without owned inclusion matches the safe default."""
    cfg = build_alembic_config(include_owned=False)
    assert _heads(cfg) == {TIQORA_HEAD}


def test_migrate_config_with_gate_includes_owned() -> None:
    """When the gate is active the builder appends versions_owned and head
    advances to the owned head."""
    cfg = build_alembic_config(include_owned=True)
    heads = _heads(cfg)
    assert heads == {OWNED_HEAD}, heads
    all_revs = {rev.revision for rev in ScriptDirectory.from_config(cfg).walk_revisions()}
    assert TIQORA_HEAD in all_revs and OWNED_HEAD in all_revs
