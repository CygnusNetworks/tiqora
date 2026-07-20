import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type GroupOut } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

/**
 * Agent↔Groups assignment editor: pick an agent, then toggle their groups.
 * Reads the current set from GET /admin/users/{id}/groups and writes via the
 * existing assign/revoke endpoints. The checkbox manages the full ("rw")
 * permission only — the read endpoint filters to the same key, so the state
 * shown and the state written always agree.
 */
export function AgentGroupsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [agentId, setAgentId] = useState<number | null>(null);

  const agentsQ = useQuery({
    queryKey: ["admin", "users", "ref"],
    queryFn: () => api.adminUsers.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const groupsQ = useQuery({
    queryKey: ["admin", "groups", "ref"],
    queryFn: () => api.adminGroups.list({ valid: "valid", pageSize: 500 }),
    staleTime: 5 * 60 * 1000,
  });

  const assignedQ = useQuery({
    queryKey: ["admin", "user-groups", agentId],
    queryFn: () =>
      api.request<GroupOut[]>("GET", `/api/v1/admin/users/${agentId}/groups`),
    enabled: agentId !== null,
  });

  const assignedIds = new Set((assignedQ.data ?? []).map((g) => g.id));

  const toggleM = useMutation({
    mutationFn: ({ groupId, next }: { groupId: number; next: boolean }) =>
      next
        ? api.assignUserGroup(agentId as number, { group_id: groupId, permission_key: "rw" })
        : api.revokeUserGroup(agentId as number, groupId, "rw"),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["admin", "user-groups", agentId] }),
  });

  const groups = groupsQ.data?.items ?? [];

  return (
    <div className="space-y-4 p-4" data-testid="admin-agent-groups-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.groupAssignments.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.groupAssignments.subtitle")}</p>
      </div>

      <label className="block max-w-md space-y-1">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.groupAssignments.agent")}
        </span>
        <select
          className="w-full rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-ink"
          data-testid="admin-agent-groups-select"
          value={agentId ?? ""}
          onChange={(e) => setAgentId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">{t("admin.groupAssignments.selectAgent")}</option>
          {(agentsQ.data?.items ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.login} — {u.first_name} {u.last_name}
            </option>
          ))}
        </select>
      </label>

      {agentId === null ? (
        <p className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("admin.groupAssignments.empty")}
        </p>
      ) : (
        <div className="rounded-lg border border-hairline bg-surface">
          <div className="flex items-center justify-between border-b border-hairline px-4 py-2 text-xs uppercase tracking-wide text-muted">
            <span>{t("admin.groupAssignments.groups")}</span>
            {(assignedQ.isFetching || toggleM.isPending) && <Spinner className="size-4" />}
          </div>
          <ul className="divide-y divide-hairline">
            {groups.map((group) => {
              const checked = assignedIds.has(group.id);
              return (
                <li
                  key={group.id}
                  className="flex items-center justify-between px-4 py-2.5"
                  data-testid={`admin-group-row-${group.id}`}
                >
                  <label className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={checked}
                      disabled={toggleM.isPending}
                      data-testid={`admin-group-toggle-${group.id}`}
                      onChange={(e) =>
                        toggleM.mutate({ groupId: group.id, next: e.target.checked })
                      }
                    />
                    <span className="text-sm text-ink">{group.name}</span>
                  </label>
                  {checked && (
                    <Badge tone="success">{t("admin.groupAssignments.assigned")}</Badge>
                  )}
                </li>
              );
            })}
            {groups.length === 0 && (
              <li className="px-4 py-6 text-center text-sm text-muted">
                {t("admin.groupAssignments.noGroups")}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
