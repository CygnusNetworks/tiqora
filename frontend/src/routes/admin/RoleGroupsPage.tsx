import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type GroupOut } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

/**
 * Role↔Groups assignment editor: pick a role, then toggle its groups.
 * Reads the current set from GET /admin/roles/{id}/groups and writes via the
 * existing assign/revoke endpoints (group_role rows). The checkbox manages the
 * full ("rw") permission only — the read endpoint filters to the same key so
 * the displayed and written state always agree.
 */
export function RoleGroupsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [roleId, setRoleId] = useState<number | null>(null);

  const rolesQ = useQuery({
    queryKey: ["admin", "roles", "ref"],
    queryFn: () => api.adminRoles.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const groupsQ = useQuery({
    queryKey: ["admin", "groups", "ref"],
    queryFn: () => api.adminGroups.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const assignedQ = useQuery({
    queryKey: ["admin", "role-groups", roleId],
    queryFn: () =>
      api.request<GroupOut[]>("GET", `/api/v1/admin/roles/${roleId}/groups`),
    enabled: roleId !== null,
  });

  const assignedIds = new Set((assignedQ.data ?? []).map((g) => g.id));

  const toggleM = useMutation({
    mutationFn: ({ groupId, next }: { groupId: number; next: boolean }) =>
      next
        ? api.assignRoleGroup(roleId as number, {
            group_id: groupId,
            permission_key: "rw",
            permission_value: 1,
          })
        : api.revokeRoleGroup(roleId as number, groupId, "rw"),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["admin", "role-groups", roleId] }),
  });

  const groups = groupsQ.data?.items ?? [];

  return (
    <div className="space-y-4 p-4" data-testid="admin-role-groups-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.roleGroupAssignments.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.roleGroupAssignments.subtitle")}</p>
      </div>

      <label className="block max-w-md space-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.roleGroupAssignments.role")}
        </span>
        <select
          className="w-full rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-ink"
          data-testid="admin-role-groups-select"
          value={roleId ?? ""}
          onChange={(e) => setRoleId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">{t("admin.roleGroupAssignments.selectRole")}</option>
          {(rolesQ.data?.items ?? []).map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </label>

      {roleId === null ? (
        <p className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("admin.roleGroupAssignments.empty")}
        </p>
      ) : (
        <div className="rounded-lg border border-hairline bg-surface">
          <div className="flex items-center justify-between border-b border-hairline px-4 py-2 text-xs uppercase tracking-wide text-muted">
            <span>{t("admin.roleGroupAssignments.groups")}</span>
            {(assignedQ.isFetching || toggleM.isPending) && <Spinner className="size-4" />}
          </div>
          <ul className="divide-y divide-hairline">
            {groups.map((group) => {
              const checked = assignedIds.has(group.id);
              return (
                <li
                  key={group.id}
                  className="flex items-center justify-between px-4 py-2.5"
                  data-testid={`admin-role-group-row-${group.id}`}
                >
                  <label className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={checked}
                      disabled={toggleM.isPending}
                      data-testid={`admin-role-group-toggle-${group.id}`}
                      onChange={(e) =>
                        toggleM.mutate({ groupId: group.id, next: e.target.checked })
                      }
                    />
                    <span className="text-sm text-ink">{group.name}</span>
                  </label>
                  {checked && (
                    <Badge tone="success">{t("admin.roleGroupAssignments.assigned")}</Badge>
                  )}
                </li>
              );
            })}
            {groups.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted">
                {t("admin.roleGroupAssignments.noGroups")}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
