"""Shared test helper: seed effective SysConfig values as Znuny rows."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from tiqora.znuny.sysconfig import yaml_encode_effective


def set_sysconfig_rows(conn: Any, base_id: int, settings: dict[str, Any]) -> None:
    """Replace the ``sysconfig_default``/``sysconfig_modified`` rows ``base_id..base_id+9`` with
    one effective value per setting (pass checkbox values as the string ``"0"``/``"1"`` to
    mimic what Znuny stores)."""
    for table in ("sysconfig_modified", "sysconfig_default"):
        conn.execute(text(f"DELETE FROM {table} WHERE id BETWEEN {base_id} AND {base_id + 9}"))
    for i, (name, value) in enumerate(settings.items()):
        enc = yaml_encode_effective(value)
        conn.execute(
            text(
                "INSERT INTO sysconfig_default (id, name, description, navigation,"
                " is_invisible, is_readonly, is_required, is_valid, has_configlevel,"
                " user_modification_possible, user_modification_active, xml_content_raw,"
                " xml_content_parsed, xml_filename, effective_value, is_dirty,"
                " exclusive_lock_guid, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :n, 'x', 'x', 0, 0, 1, 1, 0, 0, 0, 'x', 'x', 'Ticket.xml',"
                " :e, 0, '0', current_timestamp, 1, current_timestamp, 1)"
            ),
            {"id": base_id + i, "n": name, "e": enc},
        )
        conn.execute(
            text(
                "INSERT INTO sysconfig_modified (id, sysconfig_default_id, name, user_id,"
                " is_valid, user_modification_active, effective_value, is_dirty,"
                " reset_to_default, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :did, :n, NULL, 1, 0, :e, 0, 0, current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {"id": base_id + i, "did": base_id + i, "n": name, "e": enc},
        )
