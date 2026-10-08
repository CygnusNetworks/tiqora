---
name: alembic-env-cannot-gate-version-locations
description: |
  Fix for an Alembic migration gate that silently does nothing — env.py sets
  version_locations (or otherwise tries to decide which migration chains are
  visible) but `alembic upgrade head` still reaches migrations it should have
  excluded. Use when: (1) you have multiple version_locations directories (e.g.
  a "safe" chain and a gated/"owned" chain) and want head to stop at one of
  them conditionally, (2) a conditional in env.py that appends/removes a
  version location has no effect, (3) `alembic upgrade head` applied a
  migration you expected to be gated off (e.g. one that alters tables it
  shouldn't), (4) building a two-chain Alembic layout for parallel-operation /
  schema-ownership scenarios. Root cause: Alembic builds ScriptDirectory and
  resolves "head" from the Config BEFORE env.py runs, so env.py is too late.
author: Claude Code
version: 1.1.0
date: 2026-07-20
---

# Alembic env.py cannot gate version_locations / head

## Problem

You split migrations into two directories via `version_locations`, e.g. a
default chain and a gated chain that should only apply under some condition
(feature flag, DB marker, "schema ownership"). You put the gating logic in
`alembic/env.py` — computing `version_locations` dynamically and calling
`config.set_main_option("version_locations", ...)`. It looks correct and even
has passing unit tests. But a plain `alembic upgrade head` still walks into the
gated chain and applies migrations you intended to keep invisible.

## Context / Trigger Conditions

- `alembic.ini` lists two dirs, e.g.
  `version_locations = alembic/versions_a:alembic/versions_b`
- `env.py` recomputes and `set_main_option("version_locations", ...)` based on
  a flag/marker
- The gated migration chains linearly off the base head
  (`down_revision = <base head>`), so the whole thing is one linear graph and
  `head` = the gated tip
- Symptom: `alembic upgrade head` applies the gated migration regardless of the
  flag; your env.py gate is effectively dead code
- Especially dangerous in parallel-operation setups where the gated chain
  alters tables owned by another live system

## Root cause

Alembic constructs its `ScriptDirectory` — and therefore resolves `head` and
the whole revision graph — **from the `Config` object before `env.py` is
executed**. `command.upgrade(cfg, rev)` does, in order:

1. `script = ScriptDirectory.from_config(cfg)`  ← reads `version_locations`
   from alembic.ini here
2. `with EnvironmentContext(cfg, script, ...): script.run_env()`  ← runs env.py

So anything env.py sets via `set_main_option("version_locations", ...)` happens
*after* the directories were already scanned and `head` already computed. The
env.py "gate" never affects which revisions exist.

## Solution

The only effective lever is the `version_locations` present on the `Config`
**before** the command runs. Two parts:

1. **Make the default safe.** In `alembic.ini`, list ONLY the base chain:
   ```ini
   path_separator = os          # (was: version_path_separator, now deprecated)
   version_locations = alembic/versions_base
   ```
   Now a bare `alembic upgrade head` can never see the gated chain.

2. **Gate at the command layer, not in env.py.** Provide your own entrypoint
   (a CLI command) that builds the Config, decides whether to append the gated
   directory, and only then calls the alembic command API:
   ```python
   import os
   from alembic import command
   from alembic.config import Config

   def build_config(*, include_gated: bool) -> Config:
       cfg = Config("alembic.ini")
       cfg.set_main_option("script_location", "<abs>/alembic")
       locs = ["<abs>/alembic/versions_base"]
       if include_gated:
           locs.append("<abs>/alembic/versions_gated")
       # join with the SAME separator alembic.ini declares (path_separator=os)
       cfg.set_main_option("version_locations", os.pathsep.join(locs))
       return cfg

   def upgrade(gate_active: bool) -> None:
       command.upgrade(build_config(include_gated=gate_active), "head")
   ```
   Run migrations through this command (e.g. in your container entrypoint), not
   through raw `alembic upgrade head`.

3. **Delete the env.py gate** — it is misleading dead code. Leave a comment
   explaining why the gate lives at the Config layer.

### Gotcha: the separator

If `alembic.ini` has `path_separator = os` (or the deprecated
`version_path_separator = os`), you MUST join multiple locations with
`os.pathsep` (":" on POSIX). Joining with a space yields one bogus path and
alembic silently finds zero revisions (`get_heads()` returns `[]`).

## Verification

Regression test with no DB needed — inspect the ScriptDirectory the Config
produces:

```python
from alembic.config import Config
from alembic.script import ScriptDirectory

def test_default_config_excludes_gated_chain():
    s = ScriptDirectory.from_config(Config("alembic.ini"))
    assert s.get_heads() == {"<base_head_rev>"}
    all_revs = {r.revision for r in s.walk_revisions()}
    assert "<gated_head_rev>" not in all_revs   # not even walkable

def test_builder_includes_gated_when_asked():
    s = ScriptDirectory.from_config(build_config(include_gated=True))
    assert s.get_heads() == {"<gated_head_rev>"}
```

This is the test that would have caught it: the original suite only unit-tested
the (dead) env.py helper, never asserted the real `head` the default config
resolves to.

## Notes

- Testing the gate function in isolation is not enough — assert the behavior of
  the actual `ScriptDirectory.from_config(cfg)`, because that's the layer that
  matters.
- Same reasoning applies to anything you might want env.py to influence about
  the script graph (version_locations, script_location): configure it on the
  Config before invoking the command, or in alembic.ini.
- `version_path_separator` is deprecated in recent Alembic; use
  `path_separator`.

## See also

- `alembic-two-chain-new-head-forks-graph` — the standing maintenance cost of
  this layout: because the gated chain rebases its `down_revision` onto the
  base head, every *new* base migration forks the graph into two heads
  (`MultipleHeads`) unless the gated chain is rebased in the same commit.

## References

- Alembic Config / ScriptDirectory: https://alembic.sqlalchemy.org/en/latest/api/config.html
- Alembic multiple bases / branches: https://alembic.sqlalchemy.org/en/latest/branches.html
