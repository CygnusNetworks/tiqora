import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type CustomerCompanyOut } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

/**
 * Customer-User↔Customers assignment editor: pick a customer user, then toggle
 * which companies they can also see tickets for.
 *
 * This edits the Znuny `customer_user_customer` M2M (extra ticket visibility),
 * keyed by the customer-user *login*. It is separate from the user's single
 * primary company (`customer_user.customer_id`), which lives on the Customer
 * Users page. Reads from GET /admin/customer-users/{login}/companies and writes
 * via the existing assign/revoke endpoints.
 */
export function CustomerUserCustomersPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [login, setLogin] = useState<string | null>(null);

  const usersQ = useQuery({
    queryKey: ["admin", "customer-users", "ref"],
    queryFn: () => api.adminCustomerUsers.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const companiesQ = useQuery({
    queryKey: ["admin", "customer-companies", "ref"],
    queryFn: () => api.adminCustomerCompanies.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const assignedQ = useQuery({
    queryKey: ["admin", "customer-user-companies", login],
    queryFn: () =>
      api.request<CustomerCompanyOut[]>(
        "GET",
        `/api/v1/admin/customer-users/${encodeURIComponent(login as string)}/companies`,
      ),
    enabled: login !== null,
  });

  const assignedIds = new Set((assignedQ.data ?? []).map((c) => c.customer_id));

  const toggleM = useMutation({
    mutationFn: ({ customerId, next }: { customerId: string; next: boolean }) =>
      next
        ? api.assignCustomerCompany(login as string, customerId)
        : api.revokeCustomerCompany(login as string, customerId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["admin", "customer-user-companies", login],
      }),
  });

  const companies = companiesQ.data?.items ?? [];

  return (
    <div className="space-y-4 p-4" data-testid="admin-customer-user-customers-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.customerUserCustomers.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.customerUserCustomers.subtitle")}</p>
      </div>

      <label className="block max-w-md space-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.customerUserCustomers.customerUser")}
        </span>
        <select
          className="w-full rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-ink"
          data-testid="admin-customer-user-customers-select"
          value={login ?? ""}
          onChange={(e) => setLogin(e.target.value ? e.target.value : null)}
        >
          <option value="">{t("admin.customerUserCustomers.selectCustomerUser")}</option>
          {(usersQ.data?.items ?? []).map((u) => (
            <option key={u.id} value={u.login}>
              {u.login} — {u.first_name} {u.last_name}
            </option>
          ))}
        </select>
      </label>

      {login === null ? (
        <p className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("admin.customerUserCustomers.empty")}
        </p>
      ) : (
        <div className="rounded-lg border border-hairline bg-surface">
          <div className="flex items-center justify-between border-b border-hairline px-4 py-2 text-xs uppercase tracking-wide text-muted">
            <span>{t("admin.customerUserCustomers.companies")}</span>
            {(assignedQ.isFetching || toggleM.isPending) && <Spinner className="size-4" />}
          </div>
          <ul className="divide-y divide-hairline">
            {companies.map((company) => {
              const checked = assignedIds.has(company.customer_id);
              return (
                <li
                  key={company.customer_id}
                  className="flex items-center justify-between px-4 py-2.5"
                  data-testid={`admin-customer-company-row-${company.customer_id}`}
                >
                  <label className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={checked}
                      disabled={toggleM.isPending}
                      data-testid={`admin-customer-company-toggle-${company.customer_id}`}
                      onChange={(e) =>
                        toggleM.mutate({
                          customerId: company.customer_id,
                          next: e.target.checked,
                        })
                      }
                    />
                    <span className="text-sm text-ink">{company.name}</span>
                  </label>
                  {checked && (
                    <Badge tone="success">{t("admin.customerUserCustomers.assigned")}</Badge>
                  )}
                </li>
              );
            })}
            {companies.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted">
                {t("admin.customerUserCustomers.noCompanies")}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
