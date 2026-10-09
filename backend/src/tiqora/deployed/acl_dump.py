"""Build Znuny's ``ACLDump`` shape (ACL/DB/ACL.pm:829-911) from the ``acl`` table."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.config import Acl
from tiqora.db.legacy.user import Users
from tiqora.process.config import _load_yaml_mapping
from tiqora.process.dump import _jsonable, format_time

# ACL/DB/ACL.pm:886 and :891-894
_NAME_UNSAFE = re.compile(r"[\r\n\\'\"]")
_LINE_BREAKS = re.compile(r"[\r\n]")


def _section(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    # ACL/DB/ACL.pm:868-882: a section only counts when it is a hash with data.
    value = cfg.get(key)
    return value if isinstance(value, dict) and value else {}


async def build_acl_dump(session: AsyncSession) -> dict[str, Any]:
    """Valid ACLs keyed by sanitised name, sorted by name.

    ``CreateBy``/``ChangeBy`` are user logins: ACLGet maps both ids through ``UserLookup``
    (ACL.pm:393-398), which does not filter on ``valid_id`` and gives ``undef`` for an
    unknown id (User.pm UserLookup). Ruling F7 said raw ids; the golden peer shows logins.
    Two ACLs whose names collapse to the same key keep the one with the higher
    id, as Znuny's hash assignment does.
    """
    rows = (
        (await session.execute(select(Acl).where(Acl.valid_id == 1).order_by(Acl.id)))
        .scalars()
        .all()
    )
    user_ids = {u for acl in rows for u in (acl.create_by, acl.change_by)}
    logins: dict[int, str] = (
        dict(
            (await session.execute(select(Users.id, Users.login).where(Users.id.in_(user_ids))))
            .tuples()
            .all()
        )
        if user_ids
        else {}
    )
    acls: dict[str, Any] = {}
    for acl in rows:
        match = _jsonable(_load_yaml_mapping(acl.config_match))
        change = _jsonable(_load_yaml_mapping(acl.config_change))
        comment = acl.comments
        if comment:
            comment = _LINE_BREAKS.sub("", comment)
        acls[_NAME_UNSAFE.sub("", acl.name)] = {
            "ID": acl.id,
            "CreateTime": format_time(acl.create_time),
            "ChangeTime": format_time(acl.change_time),
            "CreateBy": logins.get(acl.create_by),
            "ChangeBy": logins.get(acl.change_by),
            "Comment": comment,
            "Values": {
                "StopAfterMatch": acl.stop_after_match or 0,
                "Properties": _section(match, "Properties"),
                "PropertiesDatabase": _section(match, "PropertiesDatabase"),
                "Possible": _section(change, "Possible"),
                "PossibleAdd": _section(change, "PossibleAdd"),
                "PossibleNot": _section(change, "PossibleNot"),
            },
        }
    return {"ACL": {name: acls[name] for name in sorted(acls)}}
