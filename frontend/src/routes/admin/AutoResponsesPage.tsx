import { useTranslation } from "react-i18next";
import {
  api,
  type AutoResponseOut,
  type AutoResponseCreate,
  type AutoResponseUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function AutoResponsesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<AutoResponseOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.autoResponses.name"), render: (r) => r.name },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.autoResponses.name"), type: "text", required: true },
    { name: "type_id", label: t("admin.autoResponses.typeId"), type: "number", required: true },
    {
      name: "system_address_id",
      label: t("admin.queues.systemAddressId"),
      type: "number",
      required: true,
    },
    { name: "text0", label: t("admin.autoResponses.subject"), type: "text" },
    { name: "text1", label: t("admin.autoResponses.body"), type: "textarea" },
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
      resourceKey="auto-responses"
      title={t("admin.autoResponses.title_plural")}
      newLabel={t("admin.autoResponses.new")}
      api={api.adminAutoResponses}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              name: row.name,
              type_id: row.type_id,
              system_address_id: row.system_address_id,
              text0: row.text0 ?? "",
              text1: row.text1 ?? "",
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): AutoResponseCreate => ({
        name: v.name as string,
        type_id: Number(v.type_id),
        system_address_id: Number(v.system_address_id),
        text0: (v.text0 as string) || null,
        text1: (v.text1 as string) || null,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): AutoResponseUpdate => ({
        name: v.name as string,
        type_id: Number(v.type_id),
        system_address_id: Number(v.system_address_id),
        text0: (v.text0 as string) || null,
        text1: (v.text1 as string) || null,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
