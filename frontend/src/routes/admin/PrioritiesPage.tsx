import { useTranslation } from "react-i18next";
import { api, type PriorityOut, type PriorityCreate, type PriorityUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function PrioritiesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<PriorityOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.priorities.name"), render: (r) => r.name },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.priorities.name"), type: "text", required: true },
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
      resourceKey="priorities"
      title={t("admin.priorities.title_plural")}
      newLabel={t("admin.priorities.new")}
      api={api.adminPriorities}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) => (row ? { name: row.name, valid_id: row.valid_id } : { valid_id: 1 })}
      toCreateBody={(v: FieldValues): PriorityCreate => ({
        name: v.name as string,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): PriorityUpdate => ({
        name: v.name as string,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
