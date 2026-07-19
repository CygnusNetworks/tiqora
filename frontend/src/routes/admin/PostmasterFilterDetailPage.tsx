import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "@tanstack/react-router";
import { api } from "@/lib/api";
import { Spinner } from "@/components/ui/Spinner";

export function PostmasterFilterDetailPage() {
  const { t } = useTranslation();
  const { name } = useParams({ from: "/admin/postmaster-filters/$name" });
  const detailQ = useQuery({
    queryKey: ["admin", "postmaster-filters", name],
    queryFn: ({ signal }) => api.getPostmasterFilter(name, signal),
  });

  return (
    <div className="space-y-3 p-4" data-testid="admin-postmaster-filter-detail-page">
      <Link to="/admin/postmaster-filters" className="text-sm text-accent hover:underline">
        {t("common.back")}
      </Link>
      <h1 className="font-display text-xl font-semibold text-ink">{name}</h1>
      {detailQ.isLoading ? (
        <Spinner />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
          <table className="w-full min-w-[560px] border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-hairline bg-surface-subtle text-xs uppercase tracking-wide text-muted">
                <th className="py-1.5 pl-4 pr-2 font-medium">{t("admin.postmasterFilters.type")}</th>
                <th className="py-1.5 pr-2 font-medium">{t("admin.postmasterFilters.key")}</th>
                <th className="py-1.5 pr-2 font-medium">{t("admin.postmasterFilters.value")}</th>
                <th className="py-1.5 pr-4 font-medium">{t("admin.postmasterFilters.stop")}</th>
              </tr>
            </thead>
            <tbody>
              {detailQ.data?.rules.map((rule, idx) => (
                <tr key={idx} className="border-b border-hairline last:border-b-0">
                  <td className="py-1 pl-4 pr-2 font-mono text-xs">{rule.f_type}</td>
                  <td className="py-1 pr-2 text-xs">{rule.f_key}</td>
                  <td className="py-1 pr-2 text-xs">
                    {rule.f_not ? "≠ " : ""}
                    {rule.f_value}
                  </td>
                  <td className="py-1 pr-4 text-xs">{rule.f_stop ? "✓" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
