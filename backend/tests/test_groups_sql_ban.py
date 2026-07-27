"""CI guard: no hard-coded ``permission_groups`` in app SQL strings.

Production code must resolve the groups table via
:func:`tiqora.db.legacy.profile.groups_table_name` /
:func:`groups_table_sql` so OTRS/Znuny 6.0 (table ``groups``) works.
Comments and docstrings may still mention the name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "tiqora"

# Allowlisted files that document the historical name or implement detection.
_ALLOW = frozenset(
    {
        "db/legacy/profile.py",
        "db/legacy/user.py",
        "db/legacy/__init__.py",
        "db/tiqora/models.py",  # comment only soft-join note
        "api/v1/admin/groups.py",  # module docstring
        "api/v1/admin/auth_config.py",  # docstring
        "domain/settings_store.py",  # comment
        "ai/acl.py",  # comment
    }
)

# SQL-ish uses of the literal table name (not comments).
_SQL_USE = re.compile(
    r"""(?ix)
    (?:
        FROM\s+[`"]?permission_groups[`"]?
        | JOIN\s+[`"]?permission_groups[`"]?
        | INTO\s+[`"]?permission_groups[`"]?
        | UPDATE\s+[`"]?permission_groups[`"]?
        | TABLE\s+[`"]?permission_groups[`"]?
    )
    """
)


def test_no_hardcoded_permission_groups_sql_in_src() -> None:
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        rel = path.relative_to(_SRC).as_posix()
        if rel in _ALLOW:
            continue
        text = path.read_text(encoding="utf-8")
        # Strip comments roughly so docstrings in allow-needed files elsewhere still catch SQL
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if _SQL_USE.search(line):
                offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, (
        "Hard-coded permission_groups SQL found — use groups_table_name() / "
        "groups_table_sql() instead:\n" + "\n".join(offenders)
    )


def test_quote_ident_and_groups_sql_helpers() -> None:
    from tiqora.db.legacy.profile import (
        SchemaProfileId,
        apply_legacy_schema_profile,
        groups_table_sql,
        profile_for_id,
        quote_ident,
        reset_legacy_schema_profile,
    )

    assert quote_ident("permission_groups", dialect="mysql") == "`permission_groups`"
    assert quote_ident("groups", dialect="postgresql") == '"groups"'
    with pytest.raises(ValueError):
        quote_ident("groups; drop", dialect="mysql")

    reset_legacy_schema_profile()
    apply_legacy_schema_profile(profile_for_id(SchemaProfileId.OTRS_ZNUNY_6_0))
    assert groups_table_sql(dialect="mysql") == "`groups`"
    reset_legacy_schema_profile()
    apply_legacy_schema_profile(profile_for_id(SchemaProfileId.ZNUNY_6_5))
    assert groups_table_sql(dialect="postgresql") == '"permission_groups"'
    reset_legacy_schema_profile()
