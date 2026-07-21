import {
  AssignmentEditor,
  type AssignmentConfig,
} from "@/components/admin/AssignmentEditor";
import { api, type CustomerUserAdminOut, type GroupOut } from "@/lib/api";

/**
 * Customer-User↔Groups assignment editor (bidirectional master-detail).
 * Anchor id is the customer-user *login* string.
 *
 * Customer users are server-searched (PROD has 100k+ rows); groups stay
 * client-filtered (small table).
 */
export function CustomerUserGroupsPage() {
  const config: AssignmentConfig<CustomerUserAdminOut, GroupOut> = {
    testId: "admin-customer-user-groups-page",
    titleKey: "admin.customerUserGroups.title",
    subtitleKey: "admin.customerUserGroups.subtitle",
    sideA: {
      key: "customer-users",
      labelKey: "admin.customerUserGroups.customerUser",
      loadItems: async (signal) => {
        const page = await api.adminCustomerUsers.list(
          { valid: "valid", pageSize: 50, search: "" },
          signal,
        );
        return page.items;
      },
      searchItems: async (q, signal) => {
        const page = await api.adminCustomerUsers.list(
          { valid: "valid", pageSize: 50, search: q },
          signal,
        );
        return page.items;
      },
      getId: (u) => u.login,
      getLabel: (u) => u.login,
      getSubLabel: (u) => {
        const name = `${u.first_name} ${u.last_name}`.trim();
        if (name && u.email) return `${name} · ${u.email}`;
        return name || u.email || undefined;
      },
    },
    sideB: {
      key: "groups",
      labelKey: "admin.customerUserGroups.groups",
      loadItems: async (signal) => {
        const page = await api.adminGroups.list({ valid: "valid", pageSize: 500 }, signal);
        return page.items;
      },
      getId: (g) => g.id,
      getLabel: (g) => g.name,
    },
    loadAssignedB: (login, signal) => api.listCustomerUserGroups(login as string, signal),
    loadAssignedA: (groupId, signal) => api.listGroupCustomerUsers(groupId as number, signal),
    assign: (login, groupId) =>
      api.assignCustomerUserGroup(login as string, {
        group_id: groupId as number,
        permission_key: "rw",
        permission_value: 1,
      }),
    revoke: (login, groupId) =>
      api.revokeCustomerUserGroup(login as string, groupId as number, "rw"),
  };

  return <AssignmentEditor config={config} />;
}
