import { useTranslation } from "react-i18next";
import {
  api,
  type SalutationOut,
  type SalutationWrite,
  type SalutationUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function SalutationsPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<SalutationOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.salutations.name"), render: (r) => r.name },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.salutations.name"), type: "text", required: true },
    { name: "text", label: t("admin.salutations.text"), type: "textarea", required: true },
    { name: "comments", label: t("admin.table.comments"), type: "textarea" },
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
      resourceKey="salutations"
      title={t("admin.salutations.title_plural")}
      newLabel={t("admin.salutations.new")}
      api={api.adminSalutations}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              name: row.name,
              text: row.text,
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): SalutationWrite => ({
        name: v.name as string,
        text: v.text as string,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): SalutationUpdate => ({
        name: v.name as string,
        text: v.text as string,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
