import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";

const FEATURE = "customer_directory" as const;

/**
 * Who may use the agent customer directory ("Kunden"): single agents, every
 * member of a group, every holder of a role. Admins always may.
 */
export function CustomerDirectoryAccessDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [userIds, setUserIds] = useState<Set<number>>(new Set());
  const [groupIds, setGroupIds] = useState<Set<number>>(new Set());
  const [roleIds, setRoleIds] = useState<Set<number>>(new Set());
  const [userFilter, setUserFilter] = useState("");

  const [seeded, setSeeded] = useState(false);

  const grantsQ = useQuery({
    queryKey: ["admin", "feature-grants", FEATURE],
    queryFn: ({ signal }) => api.getFeatureGrants(FEATURE, signal),
  });
  // Seed the checkboxes once from fresh server data (not from a stale cache entry).
  useEffect(() => {
    if (seeded || !grantsQ.data || grantsQ.isFetching) return;
    setUserIds(new Set(grantsQ.data.user_ids));
    setGroupIds(new Set(grantsQ.data.group_ids));
    setRoleIds(new Set(grantsQ.data.role_ids));
    setSeeded(true);
  }, [seeded, grantsQ.data, grantsQ.isFetching]);
  const groupsQ = useQuery({
    queryKey: ["admin", "groups", "ref"],
    queryFn: ({ signal }) => api.adminGroups.list({ valid: "valid", pageSize: 500 }, signal),
  });
  const rolesQ = useQuery({
    queryKey: ["admin", "roles", "ref"],
    queryFn: ({ signal }) => api.adminRoles.list({ valid: "valid", pageSize: 500 }, signal),
  });
  const usersQ = useQuery({
    queryKey: ["admin", "users", "ref"],
    queryFn: ({ signal }) => api.adminUsers.list({ valid: "valid", pageSize: 500 }, signal),
  });

  const saveM = useMutation({
    mutationFn: () =>
      api.setFeatureGrants(FEATURE, {
        user_ids: Array.from(userIds),
        group_ids: Array.from(groupIds),
        role_ids: Array.from(roleIds),
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["admin", "feature-grants", FEATURE] });
      onClose();
    },
  });

  const toggle = (set: Set<number>, setter: (s: Set<number>) => void, id: number) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setter(next);
  };

  const users = useMemo(() => {
    const all = usersQ.data?.items ?? [];
    const q = userFilter.trim().toLowerCase();
    if (!q) return all;
    return all.filter(
      (u) =>
        u.login.toLowerCase().includes(q) ||
        `${u.first_name} ${u.last_name}`.toLowerCase().includes(q),
    );
  }, [usersQ.data, userFilter]);

  const loading = !seeded || groupsQ.isLoading || rolesQ.isLoading || usersQ.isLoading;
  const boxClass =
    "max-h-56 space-y-1 overflow-auto rounded-md border border-hairline bg-surface-subtle p-2";

  const checkList = (
    items: Array<{ id: number; name: string }>,
    selected: Set<number>,
    setter: (s: Set<number>) => void,
    testPrefix: string,
  ) => (
    <div className={boxClass}>
      {items.map((item) => (
        <label key={item.id} className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            data-testid={`${testPrefix}-${item.id}`}
            checked={selected.has(item.id)}
            onChange={() => toggle(selected, setter, item.id)}
          />
          <span className="truncate">{item.name}</span>
        </label>
      ))}
    </div>
  );

  return (
    <Dialog
      open
      onClose={onClose}
      title={t("admin.customerUsers.accessTitle")}
      description={t("admin.customerUsers.accessHint")}
      size="lg"
      footer={
        <div className="flex items-center justify-end gap-2">
          {saveM.isError && (
            <p className="mr-auto text-xs text-danger" data-testid="directory-access-error">
              {saveM.error instanceof Error ? saveM.error.message : t("admin.form.genericError")}
            </p>
          )}
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saveM.isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            data-testid="directory-access-save"
            disabled={saveM.isPending || loading}
            onClick={() => saveM.mutate()}
          >
            {saveM.isPending ? <Spinner className="h-4 w-4" /> : t("common.save")}
          </Button>
        </div>
      }
    >
      {grantsQ.isError ? (
        <p className="py-6 text-sm text-danger" data-testid="directory-access-load-error">
          {t("admin.customerUsers.accessLoadError")}
        </p>
      ) : loading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-3" data-testid="directory-access-form">
          <div className="min-w-0">
            <p className="mb-1 text-sm font-medium text-ink">{t("admin.customerUsers.accessUsers")}</p>
            <input
              value={userFilter}
              onChange={(e) => setUserFilter(e.target.value)}
              placeholder={t("admin.templates.editorUsersFilter")}
              className="mb-2 w-full rounded-md border border-hairline bg-surface-subtle px-2 py-1 text-sm text-ink"
            />
            {checkList(
              users.map((u) => ({ id: u.id, name: `${u.first_name} ${u.last_name} (${u.login})` })),
              userIds,
              setUserIds,
              "directory-access-user",
            )}
          </div>
          <div className="min-w-0">
            <p className="mb-1 text-sm font-medium text-ink">{t("admin.customerUsers.accessGroups")}</p>
            <p className="mb-2 text-xs text-muted">{t("admin.customerUsers.accessGroupsHint")}</p>
            {checkList(groupsQ.data?.items ?? [], groupIds, setGroupIds, "directory-access-group")}
          </div>
          <div className="min-w-0">
            <p className="mb-1 text-sm font-medium text-ink">{t("admin.customerUsers.accessRoles")}</p>
            <p className="mb-2 text-xs text-muted">{t("admin.customerUsers.accessRolesHint")}</p>
            {checkList(rolesQ.data?.items ?? [], roleIds, setRoleIds, "directory-access-role")}
          </div>
        </div>
      )}
    </Dialog>
  );
}
