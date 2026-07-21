import {
  AssignmentEditor,
  type AssignmentConfig,
} from "@/components/admin/AssignmentEditor";
import {
  api,
  type CustomerCompanyOut,
  type CustomerUserAdminOut,
} from "@/lib/api";

/**
 * Customer-User↔Companies assignment editor (bidirectional master-detail).
 * Both sides use string ids (login / customer_id).
 */
export function CustomerUserCustomersPage() {
  const config: AssignmentConfig<CustomerUserAdminOut, CustomerCompanyOut> = {
    testId: "admin-customer-user-customers-page",
    titleKey: "admin.customerUserCustomers.title",
    subtitleKey: "admin.customerUserCustomers.subtitle",
    sideA: {
      key: "customer-users",
      labelKey: "admin.customerUserCustomers.customerUser",
      loadItems: async (signal) => {
        const page = await api.adminCustomerUsers.list(
          { valid: "valid", pageSize: 500 },
          signal,
        );
        return page.items;
      },
      getId: (u) => u.login,
      getLabel: (u) => u.login,
      getSubLabel: (u) => `${u.first_name} ${u.last_name}`.trim() || undefined,
    },
    sideB: {
      key: "companies",
      labelKey: "admin.customerUserCustomers.companies",
      loadItems: async (signal) => {
        const page = await api.adminCustomerCompanies.list(
          { valid: "valid", pageSize: 500 },
          signal,
        );
        return page.items;
      },
      getId: (c) => c.customer_id,
      getLabel: (c) => c.name,
      getSubLabel: (c) => c.customer_id,
    },
    loadAssignedB: (login, signal) =>
      api.request<CustomerCompanyOut[]>(
        "GET",
        `/api/v1/admin/customer-users/${encodeURIComponent(login as string)}/companies`,
        { signal },
      ),
    loadAssignedA: (customerId, signal) =>
      api.listCustomerCompanyUsers(customerId as string, signal),
    assign: (login, customerId) =>
      api.assignCustomerCompany(login as string, customerId as string),
    revoke: (login, customerId) =>
      api.revokeCustomerCompany(login as string, customerId as string),
  };

  return <AssignmentEditor config={config} />;
}
