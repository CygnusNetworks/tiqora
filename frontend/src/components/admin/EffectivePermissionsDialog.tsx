import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { api, type UserOut } from "@/lib/api";

/**
 * Read-only breakdown of an agent's resolved group/queue permissions —
 * union of direct group assignment and role-derived grants, each key
 * annotated with where it came from (mirrors the admin API's
 * `EffectivePermissionsOut`).
 */
export function EffectivePermissionsDialog({
  user,
  onClose,
}: {
  user: UserOut | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const open = user !== null;

  const permsQ = useQuery({
    queryKey: ["admin", "users", user?.id, "effective-permissions"],
    queryFn: ({ signal }) => api.getUserEffectivePermissions(user!.id, signal),
    enabled: open,
  });

  const name = user ? `${user.first_name} ${user.last_name}`.trim() || user.login : "";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("admin.users.effectivePermissionsTitle", { name })}
      description={t("admin.users.effectivePermissionsDescription")}
      size="lg"
      footer={
        <Button variant="secondary" onClick={onClose} data-testid="effective-permissions-close">
          {t("common.close")}
        </Button>
      }
    >
      {permsQ.isLoading ? (
        <div className="flex justify-center py-6">
          <Spinner />
        </div>
      ) : permsQ.data ? (
        <div className="flex flex-col gap-4" data-testid="effective-permissions-content">
          <section>
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.users.rolesHeading")}
            </h3>
            {permsQ.data.roles.length === 0 ? (
              <p className="text-sm text-muted">{t("admin.users.noRoles")}</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {permsQ.data.roles.map((r) => (
                  <span
                    key={r.id}
                    className="rounded-full border border-hairline bg-surface-subtle px-2 py-0.5 text-xs text-ink"
                  >
                    {r.name}
                  </span>
                ))}
              </div>
            )}
          </section>

          <section>
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.users.groupsHeading")}
            </h3>
            {permsQ.data.groups.length === 0 ? (
              <p className="text-sm text-muted">{t("admin.users.noGroups")}</p>
            ) : (
              <div className="overflow-x-auto rounded-md border border-hairline">
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface-subtle text-xs uppercase tracking-wide text-muted">
                    <tr>
                      <th className="px-3 py-1.5 font-medium">{t("admin.groups.title_plural")}</th>
                      <th className="px-3 py-1.5 font-medium">{t("admin.users.permissionColumn")}</th>
                      <th className="px-3 py-1.5 font-medium">{t("admin.users.sourceColumn")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {permsQ.data.groups.map((g) => (
                      <tr key={g.group_id} className="border-t border-hairline">
                        <td className="px-3 py-1.5 font-medium text-ink">{g.group_name}</td>
                        <td className="px-3 py-1.5 font-mono text-xs">{g.keys.join(", ")}</td>
                        <td className="px-3 py-1.5">
                          <div className="flex flex-wrap gap-1">
                            {g.sources.map((s, idx) => (
                              <span
                                key={`${s.key}-${s.via}-${idx}`}
                                className="rounded border border-hairline bg-surface-subtle px-1.5 py-0.5 text-[11px] text-muted"
                              >
                                {s.key}: {s.via === "direct" ? t("admin.users.viaDirect") : s.via}
                              </span>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section>
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.users.queuesHeading")}
            </h3>
            {permsQ.data.queues.length === 0 ? (
              <p className="text-sm text-muted">{t("admin.users.noQueues")}</p>
            ) : (
              <div className="overflow-x-auto rounded-md border border-hairline">
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface-subtle text-xs uppercase tracking-wide text-muted">
                    <tr>
                      <th className="px-3 py-1.5 font-medium">{t("admin.queues.title_plural")}</th>
                      <th className="px-3 py-1.5 font-medium">{t("admin.groups.title_plural")}</th>
                      <th className="px-3 py-1.5 font-medium">{t("admin.users.permissionColumn")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {permsQ.data.queues.map((q) => (
                      <tr key={q.queue_id} className="border-t border-hairline">
                        <td className="px-3 py-1.5 font-medium text-ink">{q.queue_name}</td>
                        <td className="px-3 py-1.5 text-muted">{q.group_name}</td>
                        <td className="px-3 py-1.5 font-mono text-xs">{q.keys.join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      ) : null}
    </Dialog>
  );
}
