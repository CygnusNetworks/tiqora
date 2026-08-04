import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api, type ServiceOut, type ServiceCreate, type ServiceUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function ServicesPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);

  const columns: DataTableColumn<ServiceOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.services.name"), render: (r) => r.name },
    {
      key: "slas",
      header: t("admin.services.slaCount"),
      render: (r) => r.sla_ids?.length ?? 0,
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.services.name"), type: "text", required: true },
    { name: "comments", label: t("admin.table.comments"), type: "text" },
    {
      name: "valid_id",
      label: t("admin.table.status"),
      type: "select",
      options: [
        { value: 1, label: t("admin.table.valid") },
        { value: 2, label: t("admin.table.invalid") },
      ],
    },
  ];

  return (
    <AdminResourcePage
      resourceKey="services"
      title={t("admin.services.title_plural")}
      newLabel={t("admin.services.new")}
      api={api.adminServices}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? { name: row.name, comments: row.comments ?? "", valid_id: row.valid_id }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): ServiceCreate => ({
        name: v.name as string,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
        sla_ids: [],
      })}
      toUpdateBody={(v: FieldValues): ServiceUpdate => ({
        name: v.name as string,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
