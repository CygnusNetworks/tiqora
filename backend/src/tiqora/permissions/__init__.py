"""Permission engine (group/role + later ACL) shared by UI, REST, and MCP."""

from tiqora.permissions.engine import PERMISSION_KEYS, PermissionEngine

__all__ = ["PERMISSION_KEYS", "PermissionEngine"]
