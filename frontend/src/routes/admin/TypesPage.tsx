import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api, type TicketTypeOut, type TicketTypeCreate, type TicketTypeUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function TypesPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);

  const columns: DataTableColumn<TicketTypeOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.types.name"), render: (r) => r.name },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    {
      name: "name",
      label: t("admin.types.name"),
      type: "text",
      required: true,
    },
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
      resourceKey="types"
      title={t("admin.types.title_plural")}
      newLabel={t("admin.types.new")}
      api={api.adminTypes}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) => (row ? { name: row.name, valid_id: row.valid_id } : { valid_id: 1 })}
      toCreateBody={(v: FieldValues): TicketTypeCreate => ({
        name: v.name as string,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): TicketTypeUpdate => ({
        name: v.name as string,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
