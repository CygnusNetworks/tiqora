import {
  AssignmentEditor,
  type AssignmentConfig,
} from "@/components/admin/AssignmentEditor";
import { api, type RoleOut, type UserOut } from "@/lib/api";

/**
 * Agent↔Roles assignment editor (bidirectional master-detail).
 */
export function AgentRolesPage() {
  const config: AssignmentConfig<UserOut, RoleOut> = {
    testId: "admin-agent-roles-page",
    titleKey: "admin.roleAssignments.title",
    subtitleKey: "admin.roleAssignments.subtitle",
    sideA: {
      key: "users",
      labelKey: "admin.roleAssignments.agent",
      loadItems: async (signal) => {
        const page = await api.adminUsers.list({ valid: "valid", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (u) => u.id,
      getLabel: (u) => u.login,
      getSubLabel: (u) => `${u.first_name} ${u.last_name}`.trim() || undefined,
    },
    sideB: {
      key: "roles",
      labelKey: "admin.roleAssignments.roles",
      loadItems: async (signal) => {
        const page = await api.adminRoles.list({ valid: "valid", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (r) => r.id,
      getLabel: (r) => r.name,
    },
    loadAssignedB: (uId, signal) =>
      api.request<RoleOut[]>("GET", `/api/v1/admin/users/${uId}/roles`, { signal }),
    loadAssignedA: (roleId, signal) => api.listRoleUsers(roleId as number, signal),
    loadCounts: (dir, signal) =>
      dir === "a"
        ? api.listUserAssignmentCounts("roles", signal)
        : api.listRoleAssignmentCounts("users", signal),
    assign: (uId, roleId) =>
      api.assignUserRole(uId as number, { role_id: roleId as number }),
    revoke: (uId, roleId) => api.revokeUserRole(uId as number, roleId as number),
  };

  return <AssignmentEditor config={config} />;
}
