import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Spinner } from "@/components/ui/Spinner";
import { KioskLinksDialog } from "@/dashboard/kiosk/KioskLinksDialog";
import { kioskLinkKeys, sortLinks } from "@/dashboard/kiosk/kioskLinks";
import { apiErrorMessage } from "@/lib/avatarApi";
import { dashboardKeys, dashboardsApi, type KioskLink } from "@/lib/dashboardsApi";
import { formatDateTime } from "@/lib/format";

const LINK_BUTTON =
  "rounded px-1.5 py-1 text-xs text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

type Status = "active" | "revoked" | "expired";

function statusOf(link: KioskLink): Status {
  if (link.revoked_at) return "revoked";
  return link.active ? "active" : "expired";
}

/** Admin: every wallboard link with its state; active ones can be revoked, inactive ones deleted. */
export function DashboardKioskLinksPage() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<KioskLink | null>(null);
  const [deleting, setDeleting] = useState<KioskLink | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const linksQ = useQuery({
    queryKey: kioskLinkKeys.all,
    queryFn: ({ signal }) => dashboardsApi.kioskLinks.list(signal),
  });
  const tabsQ = useQuery({
    queryKey: dashboardKeys.list(),
    queryFn: ({ signal }) => dashboardsApi.list(signal),
    enabled: creating,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: kioskLinkKeys.all });

  const fail = (err: unknown) => {
    setRevoking(null);
    setDeleting(null);
    setProblem(apiErrorMessage(err, t("admin.kioskLinks.actionFailed")));
  };
  const revoke = useMutation({
    mutationFn: (id: number) => dashboardsApi.kioskLinks.revoke(id),
    onSuccess: async () => {
      await refresh();
      setRevoking(null);
    },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => dashboardsApi.kioskLinks.remove(id),
    onSuccess: async () => {
      await refresh();
      setDeleting(null);
    },
    onError: fail,
  });

  const links = sortLinks(linksQ.data ?? []);
  const when = (v: string | null) => formatDateTime(v, i18n.language);
  const COL = "px-3 py-2 text-left text-[12px] font-semibold text-muted";

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-dashboard-kiosk-links-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">{t("admin.kioskLinks.title")}</h1>
          <p className="mt-1 text-sm text-muted">{t("admin.kioskLinks.subtitle")}</p>
        </div>
        <Button
          variant="primary"
          onClick={() => {
            setProblem(null);
            setCreating(true);
          }}
        >
          {t("admin.kioskLinks.new")}
        </Button>
      </div>

      {problem && (
        <p role="alert" className="text-sm text-danger">
          {problem}
        </p>
      )}
      {linksQ.isLoading && <Spinner />}
      {linksQ.isError && (
        <p role="alert" className="text-sm text-danger">
          {t("admin.kioskLinks.loadFailed")}
        </p>
      )}
      {linksQ.isSuccess && links.length === 0 && (
        <p className="rounded-[14px] border border-dashed border-hairline bg-surface px-4 py-8 text-center text-sm text-muted">
          {t("admin.kioskLinks.empty")}
        </p>
      )}
      {links.length > 0 && (
        <div className="overflow-x-auto rounded-[14px] border border-hairline bg-surface">
          <table className="w-full text-sm">
            <thead className="border-b border-hairline">
              <tr>
                <th scope="col" className={COL}>{t("admin.kioskLinks.columns.name")}</th>
                <th scope="col" className={COL}>{t("admin.kioskLinks.columns.dashboards")}</th>
                <th scope="col" className={COL}>{t("admin.kioskLinks.columns.status")}</th>
                <th scope="col" className={COL}>{t("admin.kioskLinks.columns.usage")}</th>
                <th scope="col" className={COL}>
                  <span className="sr-only">{t("admin.kioskLinks.columns.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {links.map((link) => {
                const status = statusOf(link);
                const nameId = `kiosk-link-${link.id}-name`;
                return (
                  <tr key={link.id} className="border-b border-hairline align-top last:border-0">
                    <th scope="row" id={nameId} className="px-3 py-2 text-left font-medium text-ink">
                      {link.name}
                      <div className="text-xs font-normal text-muted">
                        {link.created_by_name ? t("admin.kioskLinks.createdBy", { name: link.created_by_name }) : null}
                        {link.show_details && (
                          <span className="ml-1">· {t("admin.kioskLinks.withDetails")}</span>
                        )}
                      </div>
                    </th>
                    <td className="px-3 py-2 text-ink">
                      {link.dashboards.map((d, i) => (
                        <span key={d.id}>
                          {i > 0 && ", "}
                          {d.name}
                          {!d.visible && <span className="text-muted"> ({t("admin.kioskLinks.hidden")})</span>}
                        </span>
                      ))}
                    </td>
                    <td className="px-3 py-2">
                      <span className={status === "active" ? "font-medium text-ink" : "text-muted"}>
                        {t(`admin.kioskLinks.${status}`)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-xs text-muted">
                      <div>
                        {link.last_used_at
                          ? t("admin.kioskLinks.lastUsed", { when: when(link.last_used_at) })
                          : t("admin.kioskLinks.neverUsed")}
                      </div>
                      <div>
                        {status === "revoked"
                          ? t("admin.kioskLinks.revokedAt", { when: when(link.revoked_at) })
                          : status === "expired"
                            ? t("admin.kioskLinks.expiredAt", { when: when(link.expires_at) })
                            : t("admin.kioskLinks.expiresAt", { when: when(link.expires_at) })}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap justify-end gap-1">
                        {link.active ? (
                          <button
                            type="button"
                            aria-describedby={nameId}
                            className={LINK_BUTTON}
                            onClick={() => {
                              setProblem(null);
                              setRevoking(link);
                            }}
                          >
                            {t("admin.kioskLinks.revoke")}
                          </button>
                        ) : (
                          <button
                            type="button"
                            aria-describedby={nameId}
                            className={`${LINK_BUTTON} !text-danger`}
                            onClick={() => {
                              setProblem(null);
                              setDeleting(link);
                            }}
                          >
                            {t("admin.kioskLinks.delete")}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {creating && tabsQ.data && (
        <KioskLinksDialog dashboards={tabsQ.data} initialDashboardId={tabsQ.data[0]?.id ?? null} onClose={() => setCreating(false)} />
      )}
      <ConfirmDialog
        open={revoking !== null}
        role="alertdialog"
        variant="danger"
        title={t("admin.kioskLinks.revokeTitle", { name: revoking?.name ?? "" })}
        message={t("admin.kioskLinks.revokeMessage")}
        confirmLabel={t("admin.kioskLinks.revoke")}
        pending={revoke.isPending}
        onConfirm={() => revoking && revoke.mutate(revoking.id)}
        onCancel={() => setRevoking(null)}
      />
      <ConfirmDialog
        open={deleting !== null}
        role="alertdialog"
        variant="danger"
        title={t("admin.kioskLinks.deleteTitle", { name: deleting?.name ?? "" })}
        message={t("admin.kioskLinks.deleteMessage")}
        confirmLabel={t("admin.kioskLinks.delete")}
        pending={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
