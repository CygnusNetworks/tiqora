import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/components/ui/Dialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { api, type AdminValidFilter, type UserOut } from "@/lib/api";

const VALID_FILTERS: AdminValidFilter[] = ["valid", "invalid", "all"];

/** A row survives the filter when its own validity matches the selection. */
function matches(filter: AdminValidFilter, valid: boolean): boolean {
  if (filter === "all") return true;
  return filter === "valid" ? valid : !valid;
}

/**
 * Read-only breakdown of an agent's resolved group/queue permissions —
 * the union of direct group assignment and role-derived grants, each key
 * annotated with where it came from.
 *
 * Rows attached to an invalid group, queue or role are listed but grant
 * nothing (see the backend `PermissionEngine`), so they are marked and — by
 * default — filtered out, mirroring the valid/invalid/all switch on the
 * admin tables.
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
  const [filter, setFilter] = useState<AdminValidFilter>("valid");

  const permsQ = useQuery({
    queryKey: ["admin", "users", user?.id, "effective-permissions"],
    queryFn: ({ signal }) => api.getUserEffectivePermissions(user!.id, signal),
    enabled: open,
  });

  const name = user ? `${user.first_name} ${user.last_name}`.trim() || user.login : "";

  const data = permsQ.data;
  const roles = (data?.roles ?? []).filter((r) => matches(filter, r.valid_id === 1));
  const groups = (data?.groups ?? []).filter((g) => matches(filter, g.valid_id === 1));
  // A queue permission is only effective when the queue *and* the group
  // carrying the permission are valid.
  const queues = (data?.queues ?? []).filter((q) =>
    matches(filter, q.valid_id === 1 && q.group_valid_id === 1),
  );

  const invalidBadge = (
    <Badge tone="warn" data-testid="effective-permissions-invalid">
      {t("admin.filter.invalid")}
    </Badge>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("admin.users.effectivePermissionsTitle", { name })}
      description={t("admin.users.effectivePermissionsDescription")}
      size="xl"
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
      ) : data ? (
        <div className="flex flex-col gap-4" data-testid="effective-permissions-content">
          <div
            className="inline-flex self-start rounded-lg border border-hairline bg-surface p-0.5"
            role="group"
            aria-label={t("admin.filter.label")}
            data-testid="effective-permissions-valid-filter"
          >
            {VALID_FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                aria-pressed={filter === f}
                data-testid={`effective-permissions-valid-${f}`}
                onClick={() => setFilter(f)}
                className={cn(
                  "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                  filter === f
                    ? "bg-accent text-white"
                    : "text-muted hover:bg-surface-subtle hover:text-ink",
                )}
              >
                {t(`admin.filter.${f}`)}
              </button>
            ))}
          </div>

          <section>
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.users.rolesHeading")}
            </h3>
            {roles.length === 0 ? (
              <p className="text-sm text-muted">{t("admin.users.noRoles")}</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {roles.map((r) => (
                  <span
                    key={r.id}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border border-hairline bg-surface-subtle px-2 py-0.5 text-xs",
                      r.valid_id === 1 ? "text-ink" : "text-muted",
                    )}
                  >
                    {r.name}
                    {r.valid_id !== 1 && invalidBadge}
                  </span>
                ))}
              </div>
            )}
          </section>

          <section>
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              {t("admin.users.groupsHeading")}
            </h3>
            {groups.length === 0 ? (
              <p className="text-sm text-muted">{t("admin.users.noGroups")}</p>
            ) : (
              <div
                className="overflow-x-auto rounded-md border border-hairline"
                data-testid="effective-permissions-groups"
              >
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface-subtle text-xs uppercase tracking-wide text-muted">
                    <tr>
                      <th className="px-3 py-1.5 font-medium">{t("admin.groups.title_plural")}</th>
                      <th className="px-3 py-1.5 font-medium">
                        {t("admin.users.permissionColumn")}
                      </th>
                      <th className="px-3 py-1.5 font-medium">{t("admin.users.sourceColumn")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groups.map((g) => (
                      <tr
                        key={g.group_id}
                        className={cn("border-t border-hairline", g.valid_id !== 1 && "opacity-70")}
                      >
                        <td className="px-3 py-1.5">
                          <span className="inline-flex items-center gap-1.5">
                            <span className="font-medium text-ink">{g.group_name}</span>
                            {g.valid_id !== 1 && invalidBadge}
                          </span>
                        </td>
                        <td className="px-3 py-1.5">
                          {/* One chip per key rather than a joined string: a
                              key granted only through an invalid role is
                              configured but not in force, and that difference
                              is the whole point of this view. */}
                          <div className="flex flex-wrap gap-1">
                            {g.keys.map((key) => {
                              const inForce =
                                g.valid_id === 1 &&
                                g.sources.some((s) => s.key === key && s.valid_id === 1);
                              return (
                                <span
                                  key={key}
                                  title={inForce ? undefined : t("admin.users.keyInactiveHint")}
                                  className={cn(
                                    "rounded border px-1.5 py-0.5 font-mono text-[11px]",
                                    inForce
                                      ? "border-hairline bg-surface-subtle text-ink"
                                      : "border-escalation/30 bg-escalation/10 text-escalation line-through",
                                  )}
                                >
                                  {key}
                                </span>
                              );
                            })}
                          </div>
                        </td>
                        <td className="px-3 py-1.5">
                          <div className="flex flex-wrap gap-1">
                            {g.sources.map((s, idx) => (
                              <span
                                key={`${s.key}-${s.via}-${idx}`}
                                title={
                                  s.valid_id === 1 ? undefined : t("admin.users.sourceInvalidHint")
                                }
                                className={cn(
                                  "rounded border px-1.5 py-0.5 text-[11px]",
                                  s.valid_id === 1
                                    ? "border-hairline bg-surface-subtle text-muted"
                                    : "border-escalation/30 bg-escalation/10 text-escalation line-through",
                                )}
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
            {queues.length === 0 ? (
              <p className="text-sm text-muted">{t("admin.users.noQueues")}</p>
            ) : (
              <div
                className="overflow-x-auto rounded-md border border-hairline"
                data-testid="effective-permissions-queues"
              >
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface-subtle text-xs uppercase tracking-wide text-muted">
                    <tr>
                      <th className="px-3 py-1.5 font-medium">{t("admin.queues.title_plural")}</th>
                      <th className="px-3 py-1.5 font-medium">{t("admin.groups.title_plural")}</th>
                      <th className="px-3 py-1.5 font-medium">
                        {t("admin.users.permissionColumn")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {queues.map((q) => {
                      const effective = q.valid_id === 1 && q.group_valid_id === 1;
                      return (
                        <tr
                          key={q.queue_id}
                          className={cn("border-t border-hairline", !effective && "opacity-70")}
                        >
                          <td className="px-3 py-1.5">
                            <span className="inline-flex items-center gap-1.5">
                              <span className="font-medium text-ink">{q.queue_name}</span>
                              {q.valid_id !== 1 && invalidBadge}
                            </span>
                          </td>
                          <td className="px-3 py-1.5">
                            <span className="inline-flex items-center gap-1.5 text-muted">
                              {q.group_name}
                              {q.group_valid_id !== 1 && invalidBadge}
                            </span>
                          </td>
                          <td className="px-3 py-1.5 font-mono text-xs">{q.keys.join(", ")}</td>
                        </tr>
                      );
                    })}
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
