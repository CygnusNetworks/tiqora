import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

/**
 * Customer-User↔Groups assignment editor: pick a customer user (by login), then
 * toggle their groups. Reads GET /admin/customer-users/{login}/groups (rw only)
 * and writes via assign/revoke — same UX as Agent↔Groups.
 */
export function CustomerUserGroupsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [login, setLogin] = useState<string | null>(null);

  const usersQ = useQuery({
    queryKey: ["admin", "customer-users", "ref"],
    queryFn: () => api.adminCustomerUsers.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const groupsQ = useQuery({
    queryKey: ["admin", "groups", "ref"],
    queryFn: () => api.adminGroups.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const assignedQ = useQuery({
    queryKey: ["admin", "customer-user-groups", login],
    queryFn: () => api.listCustomerUserGroups(login as string),
    enabled: login !== null,
  });

  const assignedIds = new Set((assignedQ.data ?? []).map((g) => g.id));

  const toggleM = useMutation({
    mutationFn: ({ groupId, next }: { groupId: number; next: boolean }) =>
      next
        ? api.assignCustomerUserGroup(login as string, {
            group_id: groupId,
            permission_key: "rw",
            permission_value: 1,
          })
        : api.revokeCustomerUserGroup(login as string, groupId, "rw"),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["admin", "customer-user-groups", login],
      }),
  });

  const groups = groupsQ.data?.items ?? [];

  return (
    <div className="space-y-4 p-4" data-testid="admin-customer-user-groups-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.customerUserGroups.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.customerUserGroups.subtitle")}</p>
      </div>

      <label className="block max-w-md space-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.customerUserGroups.customerUser")}
        </span>
        <select
          className="w-full rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-ink"
          data-testid="admin-customer-user-groups-select"
          value={login ?? ""}
          onChange={(e) => {
            const v = e.currentTarget.value;
            setLogin(v ? v : null);
          }}
        >
          <option value="">{t("admin.customerUserGroups.selectCustomerUser")}</option>
          {(usersQ.data?.items ?? []).map((u) => (
            <option key={u.id} value={u.login}>
              {u.login} — {u.first_name} {u.last_name}
            </option>
          ))}
        </select>
      </label>

      {login === null ? (
        <p className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("admin.customerUserGroups.empty")}
        </p>
      ) : (
        <div className="rounded-lg border border-hairline bg-surface">
          <div className="flex items-center justify-between border-b border-hairline px-4 py-2 text-xs uppercase tracking-wide text-muted">
            <span>{t("admin.customerUserGroups.groups")}</span>
            {(assignedQ.isFetching || toggleM.isPending) && <Spinner className="size-4" />}
          </div>
          <ul className="divide-y divide-hairline">
            {groups.map((group) => {
              const checked = assignedIds.has(group.id);
              return (
                <li
                  key={group.id}
                  className="flex items-center justify-between px-4 py-2.5"
                  data-testid={`admin-customer-user-group-row-${group.id}`}
                >
                  <label className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={checked}
                      disabled={toggleM.isPending}
                      data-testid={`admin-customer-user-group-toggle-${group.id}`}
                      onChange={(e) =>
                        toggleM.mutate({ groupId: group.id, next: e.target.checked })
                      }
                    />
                    <span className="text-sm text-ink">{group.name}</span>
                  </label>
                  {checked && (
                    <Badge tone="success">{t("admin.customerUserGroups.assigned")}</Badge>
                  )}
                </li>
              );
            })}
            {groups.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted">
                {t("admin.customerUserGroups.noGroups")}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
