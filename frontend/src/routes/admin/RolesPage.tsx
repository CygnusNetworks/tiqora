import { useTranslation } from "react-i18next";
import { api, type RoleOut, type RoleCreate, type RoleUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function RolesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<RoleOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.roles.name"), render: (r) => r.name },
    { key: "comments", header: t("admin.table.comments"), render: (r) => r.comments ?? "—" },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    {
      name: "name",
      label: t("admin.roles.name"),
      type: "text",
      required: true,
      help: { title: t("admin.roles.name"), description: t("admin.help.roles.name") },
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
      help: { title: t("admin.table.status"), description: t("admin.help.common.validId") },
    },
  ];

  return (
    <AdminResourcePage
      resourceKey="roles"
      title={t("admin.roles.title_plural")}
      newLabel={t("admin.roles.new")}
      api={api.adminRoles}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? { name: row.name, comments: row.comments ?? "", valid_id: row.valid_id }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): RoleCreate => ({
        name: v.name as string,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): RoleUpdate => ({
        name: v.name as string,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
