import { useTranslation } from "react-i18next";
import { api, type StateOut, type StateCreate, type StateUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

// Znuny ticket_state_type ids: 1 new, 2 open, 3 closed, 4 pending reminder,
// 5 pending auto, 6 removed, 7 merged.
const STATE_TYPE_OPTIONS = [
  { value: 1, label: "new" },
  { value: 2, label: "open" },
  { value: 3, label: "closed" },
  { value: 4, label: "pending reminder" },
  { value: 5, label: "pending auto" },
  { value: 7, label: "merged" },
];

export function StatesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<StateOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.states.name"), render: (r) => r.name },
    { key: "type_id", header: t("admin.states.typeId"), mono: true, render: (r) => r.type_id },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.states.name"), type: "text", required: true },
    {
      name: "type_id",
      label: t("admin.states.typeId"),
      type: "select",
      required: true,
      options: STATE_TYPE_OPTIONS,
    },
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
      resourceKey="states"
      title={t("admin.states.title_plural")}
      newLabel={t("admin.states.new")}
      api={api.adminStates}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              name: row.name,
              type_id: row.type_id,
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): StateCreate => ({
        name: v.name as string,
        type_id: Number(v.type_id),
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): StateUpdate => ({
        name: v.name as string,
        type_id: Number(v.type_id),
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
