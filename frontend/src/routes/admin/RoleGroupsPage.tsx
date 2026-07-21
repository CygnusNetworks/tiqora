import {
  AssignmentEditor,
  type AssignmentConfig,
} from "@/components/admin/AssignmentEditor";
import { api, type GroupOut, type RoleOut } from "@/lib/api";

/**
 * Role↔Groups assignment editor (bidirectional master-detail).
 * Checkbox manages full ("rw") permission only.
 */
export function RoleGroupsPage() {
  const config: AssignmentConfig<RoleOut, GroupOut> = {
    testId: "admin-role-groups-page",
    titleKey: "admin.roleGroupAssignments.title",
    subtitleKey: "admin.roleGroupAssignments.subtitle",
    sideA: {
      key: "roles",
      labelKey: "admin.roleGroupAssignments.role",
      loadItems: async (signal) => {
        const page = await api.adminRoles.list({ valid: "valid", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (r) => r.id,
      getLabel: (r) => r.name,
    },
    sideB: {
      key: "groups",
      labelKey: "admin.roleGroupAssignments.groups",
      loadItems: async (signal) => {
        const page = await api.adminGroups.list({ valid: "valid", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (g) => g.id,
      getLabel: (g) => g.name,
    },
    loadAssignedB: (roleId, signal) =>
      api.request<GroupOut[]>("GET", `/api/v1/admin/roles/${roleId}/groups`, { signal }),
    loadAssignedA: (groupId, signal) => api.listGroupRoles(groupId as number, signal),
    loadCounts: (dir, signal) =>
      dir === "a"
        ? api.listRoleAssignmentCounts("groups", signal)
        : api.listGroupAssignmentCounts("roles", signal),
    assign: (roleId, groupId) =>
      api.assignRoleGroup(roleId as number, {
        group_id: groupId as number,
        permission_key: "rw",
        permission_value: 1,
      }),
    revoke: (roleId, groupId) =>
      api.revokeRoleGroup(roleId as number, groupId as number, "rw"),
  };

  return <AssignmentEditor config={config} />;
}
