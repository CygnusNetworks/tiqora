import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { GI_NOTICE_DOUBLE, GI_NOTICE_OFF, webservicesApi } from "@/lib/webservicesApi";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";

/** The Daemons notice keys, worded for the Webservices pages (they name the service). */
const BANNER_TEXT: Record<string, string> = {
  [GI_NOTICE_OFF]: "admin.webservices.notices.giRequesterOff",
  [GI_NOTICE_DOUBLE]: "admin.webservices.notices.giRequesterDouble",
};

/** Warning shown under the page title when the Daemons API flags the `gi_requester` takeover. */
export function GiNoticeBanner() {
  const { t } = useTranslation();
  const daemonsQ = useQuery({
    queryKey: ["admin", "daemons"],
    queryFn: ({ signal }) => api.getDaemons(signal),
  });
  const notices = (daemonsQ.data?.services ?? []).find((s) => s.slug === "gi_requester")?.notices ?? [];
  const shown = notices.filter((n) => n === GI_NOTICE_OFF || n === GI_NOTICE_DOUBLE);
  if (shown.length === 0) return null;
  return (
    <div
      className="rounded-xl border border-amber/30 bg-amber/10 p-3 text-sm text-ink"
      role="status"
      data-testid="webservices-notice"
    >
      {shown.map((key) => (
        <p key={key}>{t(BANNER_TEXT[key] ?? key)}</p>
      ))}
      <Link
        to="/admin/daemons"
        className="mt-1 inline-block text-xs font-medium text-accent hover:underline"
        data-testid="webservices-notice-link"
      >
        {t("admin.webservices.noticeLink")}
      </Link>
    </div>
  );
}

export function WebservicesPage() {
  const { t } = useTranslation();
  const listQ = useQuery({
    queryKey: ["admin", "webservices"],
    queryFn: ({ signal }) => webservicesApi.list(signal),
  });

  if (listQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-webservices-page">
        <Spinner />
      </div>
    );
  }
  if (listQ.isError) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-webservices-page">
        {t("admin.webservices.loadError")}
      </div>
    );
  }
  const rows = listQ.data?.webservices ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4" data-testid="admin-webservices-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.webservices.title")}
        </h1>
        <p className="mt-1 text-sm text-muted">{t("admin.webservices.description")}</p>
      </div>
      <GiNoticeBanner />
      <div className="overflow-x-auto rounded-xl border border-hairline bg-surface">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">{t("admin.webservices.columns.name")}</th>
              <th className="px-3 py-2 font-medium">{t("admin.webservices.columns.status")}</th>
              <th className="px-3 py-2 font-medium">{t("admin.webservices.columns.incoming")}</th>
              <th className="px-3 py-2 font-medium">{t("admin.webservices.columns.invokers")}</th>
              <th className="px-3 py-2 font-medium">{t("admin.webservices.columns.byEvents")}</th>
              <th className="px-3 py-2 font-medium">{t("admin.webservices.columns.note")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-muted" data-testid="webservices-empty">
                  {t("admin.webservices.empty")}
                </td>
              </tr>
            )}
            {rows.map((ws) => (
              <tr
                key={ws.id}
                className="border-b border-hairline last:border-0 hover:bg-surface-subtle/60"
                data-testid={`webservice-row-${ws.id}`}
              >
                <td className="px-3 py-2.5 font-semibold text-ink">
                  <Link
                    to="/admin/webservices/$wsId"
                    params={{ wsId: String(ws.id) }}
                    search={{ tab: "overview" }}
                    className="text-ink hover:text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                    data-testid={`webservice-link-${ws.id}`}
                  >
                    {ws.name}
                  </Link>
                </td>
                <td className="px-3 py-2.5">
                  <Badge tone={ws.valid ? "success" : "danger"}>
                    {ws.valid ? t("admin.webservices.valid") : t("admin.webservices.invalid")}
                  </Badge>
                </td>
                <td className="px-3 py-2.5 text-muted">
                  {ws.has_provider ? t("admin.webservices.yes") : t("admin.webservices.no")}
                </td>
                <td className="px-3 py-2.5 tabular-nums text-ink">{ws.invoker_count}</td>
                <td className="px-3 py-2.5 tabular-nums text-ink">{ws.event_invoker_count}</td>
                <td className="px-3 py-2.5 text-xs text-danger" data-testid={`webservice-problem-${ws.id}`}>
                  {ws.problem}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
