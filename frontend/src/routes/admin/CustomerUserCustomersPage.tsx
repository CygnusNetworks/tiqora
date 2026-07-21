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
 * Both sides use string ids (login / customer_id) and server-side search
 * (customer_user is 100k+ rows; companies can also be large).
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
          { valid: "all", pageSize: 50, search: "" },
          signal,
        );
        return page.items;
      },
      searchItems: async (q, signal) => {
        const page = await api.adminCustomerUsers.list(
          { valid: "all", pageSize: 50, search: q },
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
      isValid: (u) => u.valid_id === 1,
    },
    sideB: {
      key: "companies",
      labelKey: "admin.customerUserCustomers.companies",
      loadItems: async (signal) => {
        const page = await api.adminCustomerCompanies.list(
          { valid: "all", pageSize: 50, search: "" },
          signal,
        );
        return page.items;
      },
      searchItems: async (q, signal) => {
        const page = await api.adminCustomerCompanies.list(
          { valid: "all", pageSize: 50, search: q },
          signal,
        );
        return page.items;
      },
      getId: (c) => c.customer_id,
      getLabel: (c) => c.name,
      getSubLabel: (c) => c.customer_id,
      isValid: (c) => c.valid_id === 1,
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
