import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { copyText } from "@/components/admin/identity/copyText";
import { apiErrorMessage } from "@/lib/avatarApi";
import { dashboardsApi, type DashboardTab, type KioskLinkCreated, type KioskLinkIn } from "@/lib/dashboardsApi";
import { KioskLinkForm } from "./KioskLinkForm";
import { kioskLinkKeys, wallboardUrl } from "./kioskLinks";

/**
 * "Wallboard-Link erstellen": the form first, then the created link. The secret is in the create answer
 * only, so the second step shows the address once. "Kopieren" uses the clipboard API; on plain-HTTP
 * pages (no `navigator.clipboard`) and whenever copying fails, the read-only field is selected for a
 * manual copy instead.
 */
export function KioskLinksDialog({
  dashboards,
  initialDashboardId,
  onClose,
}: {
  dashboards: DashboardTab[];
  initialDashboardId: number | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const ids = useId();
  const [created, setCreated] = useState<KioskLinkCreated | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copy, setCopy] = useState<"idle" | "done" | "manual">("idle");
  const field = useRef<HTMLInputElement | null>(null);

  const create = useMutation({
    mutationFn: (body: KioskLinkIn) => dashboardsApi.kioskLinks.create(body),
    onSuccess: (link) => {
      setCreated(link);
      void qc.invalidateQueries({ queryKey: kioskLinkKeys.all });
    },
    onError: (err) => setError(apiErrorMessage(err, t("dashboards.kiosk.createFailed"))),
  });

  const url = created ? wallboardUrl(created.token) : "";

  useEffect(() => {
    if (created) field.current?.select();
  }, [created]);

  const doCopy = async () => {
    const ok = await copyText(url);
    setCopy(ok ? "done" : "manual");
    if (!ok) field.current?.select();
  };

  return (
    <Dialog open onClose={onClose} title={created ? t("dashboards.kiosk.createdTitle") : t("dashboards.kiosk.title")} size="lg">
      {created ? (
        <div className="space-y-4" data-testid="kiosk-link-created">
          <p className="text-sm font-medium text-ink">{t("dashboards.kiosk.oneTime")}</p>
          <div className="space-y-1.5">
            <label htmlFor={`${ids}-url`} className="text-[13px] font-medium text-ink">
              {t("dashboards.kiosk.linkLabel")}
            </label>
            <div className="flex gap-2">
              <input
                id={`${ids}-url`}
                ref={field}
                readOnly
                value={url}
                onFocus={(e) => e.currentTarget.select()}
                className="min-w-0 flex-1 rounded-md border border-hairline bg-surface-subtle px-3 py-2 font-mono text-xs text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
              />
              <Button variant="primary" onClick={() => void doCopy()}>
                {copy === "done" ? t("dashboards.kiosk.copied") : t("dashboards.kiosk.copy")}
              </Button>
            </div>
            {copy === "manual" && (
              <p role="status" className="text-xs text-muted">
                {t("dashboards.kiosk.copyHint")}
              </p>
            )}
          </div>
          <p className="rounded-md bg-surface-subtle px-3 py-2 text-xs text-muted">{t("dashboards.kiosk.rights")}</p>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Link to="/admin/dashboard-kiosk-links" onClick={onClose} className="text-sm text-accent hover:underline">
              {t("dashboards.kiosk.manageAll")}
            </Link>
            <Button variant="secondary" onClick={onClose}>
              {t("dashboards.kiosk.close")}
            </Button>
          </div>
        </div>
      ) : (
        <KioskLinkForm
          dashboards={dashboards}
          initialDashboardId={initialDashboardId}
          pending={create.isPending}
          error={error}
          onSubmit={(body) => {
            setError(null);
            create.mutate(body);
          }}
          onCancel={onClose}
        />
      )}
    </Dialog>
  );
}
