import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type RoleOut } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

/**
 * Agent↔Roles assignment editor: pick an agent, then toggle their roles.
 * Reads the current set from GET /admin/users/{id}/roles and writes via the
 * existing assign/revoke endpoints (role_user rows).
 */
export function AgentRolesPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [agentId, setAgentId] = useState<number | null>(null);

  const agentsQ = useQuery({
    queryKey: ["admin", "users", "ref"],
    queryFn: () => api.adminUsers.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const rolesQ = useQuery({
    queryKey: ["admin", "roles", "ref"],
    queryFn: () => api.adminRoles.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const assignedQ = useQuery({
    queryKey: ["admin", "user-roles", agentId],
    queryFn: () =>
      api.request<RoleOut[]>("GET", `/api/v1/admin/users/${agentId}/roles`),
    enabled: agentId !== null,
  });

  const assignedIds = new Set((assignedQ.data ?? []).map((r) => r.id));

  const toggleM = useMutation({
    mutationFn: ({ roleId, next }: { roleId: number; next: boolean }) =>
      next
        ? api.assignUserRole(agentId as number, { role_id: roleId })
        : api.revokeUserRole(agentId as number, roleId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["admin", "user-roles", agentId] }),
  });

  const roles = rolesQ.data?.items ?? [];

  return (
    <div className="space-y-4 p-4" data-testid="admin-agent-roles-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.roleAssignments.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.roleAssignments.subtitle")}</p>
      </div>

      <label className="block max-w-md space-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.roleAssignments.agent")}
        </span>
        <select
          className="w-full rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-ink"
          data-testid="admin-agent-roles-select"
          value={agentId ?? ""}
          onChange={(e) => setAgentId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">{t("admin.roleAssignments.selectAgent")}</option>
          {(agentsQ.data?.items ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.login} — {u.first_name} {u.last_name}
            </option>
          ))}
        </select>
      </label>

      {agentId === null ? (
        <p className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("admin.roleAssignments.empty")}
        </p>
      ) : (
        <div className="rounded-lg border border-hairline bg-surface">
          <div className="flex items-center justify-between border-b border-hairline px-4 py-2 text-xs uppercase tracking-wide text-muted">
            <span>{t("admin.roleAssignments.roles")}</span>
            {(assignedQ.isFetching || toggleM.isPending) && <Spinner className="size-4" />}
          </div>
          <ul className="divide-y divide-hairline">
            {roles.map((role) => {
              const checked = assignedIds.has(role.id);
              return (
                <li
                  key={role.id}
                  className="flex items-center justify-between px-4 py-2.5"
                  data-testid={`admin-role-row-${role.id}`}
                >
                  <label className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={checked}
                      disabled={toggleM.isPending}
                      data-testid={`admin-role-toggle-${role.id}`}
                      onChange={(e) =>
                        toggleM.mutate({ roleId: role.id, next: e.target.checked })
                      }
                    />
                    <span className="text-sm text-ink">{role.name}</span>
                  </label>
                  {checked && <Badge tone="success">{t("admin.roleAssignments.assigned")}</Badge>}
                </li>
              );
            })}
            {roles.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted">
                {t("admin.roleAssignments.noRoles")}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
