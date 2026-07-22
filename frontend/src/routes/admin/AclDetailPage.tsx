import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "@tanstack/react-router";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";
import { HelpPopover } from "@/components/ui/HelpPopover";

export function AclDetailPage() {
  const { t } = useTranslation();
  const { aclId } = useParams({ from: "/admin/acl/$aclId" });
  const detailQ = useQuery({
    queryKey: ["admin", "acl", aclId],
    queryFn: ({ signal }) => api.getAcl(Number(aclId), signal),
  });

  return (
    <div className="space-y-3 p-4" data-testid="admin-acl-detail-page">
      <Link to="/admin/acl" className="text-sm text-accent hover:underline">
        {t("common.back")}
      </Link>
      {detailQ.isLoading ? (
        <Spinner />
      ) : detailQ.data ? (
        <>
          <h1 className="font-display text-xl font-semibold text-ink">{detailQ.data.name}</h1>
          {detailQ.data.description && <p className="text-sm text-muted">{detailQ.data.description}</p>}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="overflow-x-auto rounded-lg border border-hairline bg-surface p-3">
              <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
                {t("admin.acl.match")}
                <HelpPopover title={t("admin.acl.match")} testId="acl-detail-help-match">
                  {t("admin.help.acl.match")}
                </HelpPopover>
              </h2>
              <pre className="whitespace-pre-wrap break-words font-mono text-xs text-ink">
                {detailQ.data.config_match ?? "—"}
              </pre>
            </div>
            <div className="overflow-x-auto rounded-lg border border-hairline bg-surface p-3">
              <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
                {t("admin.acl.change")}
                <HelpPopover title={t("admin.acl.change")} testId="acl-detail-help-change">
                  {t("admin.help.acl.change")}
                </HelpPopover>
              </h2>
              <pre className="whitespace-pre-wrap break-words font-mono text-xs text-ink">
                {detailQ.data.config_change ?? "—"}
              </pre>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
