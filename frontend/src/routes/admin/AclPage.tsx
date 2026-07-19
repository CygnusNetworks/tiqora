import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { api, type AclOut } from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { Badge } from "@/components/ui/Badge";

export function AclPage() {
  const { t } = useTranslation();
  const listQ = useQuery({
    queryKey: ["admin", "acl"],
    queryFn: ({ signal }) => api.listAcls(signal),
  });

  const columns: DataTableColumn<AclOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    {
      key: "name",
      header: t("admin.acl.name"),
      render: (r) => (
        <Link
          to="/admin/acl/$aclId"
          params={{ aclId: String(r.id) }}
          className="text-accent hover:underline"
          data-testid={`acl-link-${r.id}`}
        >
          {r.name}
        </Link>
      ),
    },
    { key: "description", header: t("admin.acl.description"), render: (r) => r.description ?? "—" },
  ];

  return (
    <div className="space-y-3 p-4" data-testid="admin-acl-page">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.acl.title_plural")}</h1>
        <Badge tone="muted">{t("admin.readOnly")}</Badge>
      </div>
      <DataTable
        columns={columns}
        rows={listQ.data ?? []}
        rowKey={(r) => r.id}
        isLoading={listQ.isLoading}
        isRowValid={(r) => r.valid_id === 1}
        testId="admin-acl-table"
      />
    </div>
  );
}
