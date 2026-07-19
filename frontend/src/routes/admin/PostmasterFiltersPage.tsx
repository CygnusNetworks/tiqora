import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { api } from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { Badge } from "@/components/ui/Badge";

type Row = { name: string; ruleCount: number };

export function PostmasterFiltersPage() {
  const { t } = useTranslation();
  const listQ = useQuery({
    queryKey: ["admin", "postmaster-filters"],
    queryFn: ({ signal }) => api.listPostmasterFilters(signal),
  });

  const rows: Row[] = (listQ.data ?? []).map((f) => ({ name: f.name, ruleCount: f.rules.length }));

  const columns: DataTableColumn<Row>[] = [
    {
      key: "name",
      header: t("admin.postmasterFilters.name"),
      render: (r) => (
        <Link
          to="/admin/postmaster-filters/$name"
          params={{ name: r.name }}
          className="text-accent hover:underline"
          data-testid={`postmaster-filter-link-${r.name}`}
        >
          {r.name}
        </Link>
      ),
    },
    {
      key: "rules",
      header: t("admin.postmasterFilters.rules"),
      render: (r) => <Badge tone="muted">{r.ruleCount}</Badge>,
    },
  ];

  return (
    <div className="space-y-3 p-4" data-testid="admin-postmaster-filters-page">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.postmasterFilters.title_plural")}
        </h1>
        <Badge tone="muted">{t("admin.readOnly")}</Badge>
      </div>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.name}
        isLoading={listQ.isLoading}
        testId="admin-postmaster-filters-table"
      />
    </div>
  );
}
