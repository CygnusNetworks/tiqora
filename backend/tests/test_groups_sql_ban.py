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
# Broader than bare FROM/JOIN so aliases, multi-table FROM lists, and
# ``permission_groups.col`` qualification also fail the guard.
_SQL_USE = re.compile(
    r"""(?ix)
    (?:
        (?:FROM|JOIN|INTO|UPDATE|TABLE|USING|DELETE\s+FROM)\s+[`"]?permission_groups[`"]?
        | [`"]?permission_groups[`"]?\s*\.
        | [`"]?permission_groups[`"]?\s+AS\s+
        | ,\s*[`"]?permission_groups[`"]?
    )
    """
)


def _code_portion(line: str) -> str:
    """Strip full-line and trailing ``#`` comments (outside simple quotes)."""
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return ""
    # Drop trailing # comment when # is not inside a single/double-quoted span.
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "#" and not in_single and not in_double:
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def test_no_hardcoded_permission_groups_sql_in_src() -> None:
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        rel = path.relative_to(_SRC).as_posix()
        if rel in _ALLOW:
            continue
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            code = _code_portion(line)
            if not code.strip():
                continue
            if _SQL_USE.search(code):
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


def test_ban_regex_catches_alias_and_qualified_forms() -> None:
    assert _SQL_USE.search("SELECT * FROM permission_groups AS g")
    assert _SQL_USE.search("JOIN permission_groups ON g.id = u.group_id")
    assert _SQL_USE.search("WHERE permission_groups.id = 1")
    assert _SQL_USE.search("FROM users, permission_groups")
    # Full-line / trailing comments are stripped before the regex runs.
    assert _code_portion("# FROM permission_groups") == ""
    assert not _SQL_USE.search(_code_portion("# FROM permission_groups"))
    assert _code_portion('x = "permission_groups"  # FROM permission_groups') == (
        'x = "permission_groups"  '
    )
    assert not _SQL_USE.search(
        _code_portion('x = "permission_groups"  # FROM permission_groups')
    )
