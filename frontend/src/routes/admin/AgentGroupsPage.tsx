import {
  AssignmentEditor,
  type AssignmentConfig,
} from "@/components/admin/AssignmentEditor";
import { api, type GroupOut, type UserOut } from "@/lib/api";

/**
 * Agent↔Groups assignment editor (bidirectional master-detail).
 * Checkbox manages full ("rw") permission only.
 */
export function AgentGroupsPage() {
  const config: AssignmentConfig<UserOut, GroupOut> = {
    testId: "admin-agent-groups-page",
    titleKey: "admin.groupAssignments.title",
    subtitleKey: "admin.groupAssignments.subtitle",
    sideA: {
      key: "users",
      labelKey: "admin.groupAssignments.agent",
      loadItems: async (signal) => {
        const page = await api.adminUsers.list({ valid: "valid", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (u) => u.id,
      getLabel: (u) => u.login,
      getSubLabel: (u) => `${u.first_name} ${u.last_name}`.trim() || undefined,
    },
    sideB: {
      key: "groups",
      labelKey: "admin.groupAssignments.groups",
      loadItems: async (signal) => {
        const page = await api.adminGroups.list({ valid: "valid", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (g) => g.id,
      getLabel: (g) => g.name,
    },
    loadAssignedB: (uId, signal) =>
      api.request<GroupOut[]>("GET", `/api/v1/admin/users/${uId}/groups`, { signal }),
    loadAssignedA: (groupId, signal) => api.listGroupUsers(groupId as number, signal),
    loadCounts: (dir, signal) =>
      dir === "a"
        ? api.listUserAssignmentCounts("groups", signal)
        : api.listGroupAssignmentCounts("users", signal),
    assign: (uId, groupId) =>
      api.assignUserGroup(uId as number, {
        group_id: groupId as number,
        permission_key: "rw",
      }),
    revoke: (uId, groupId) =>
      api.revokeUserGroup(uId as number, groupId as number, "rw"),
  };

  return <AssignmentEditor config={config} />;
}
