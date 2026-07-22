import { useTranslation } from "react-i18next";
import { api, type UserOut, type UserCreate, type UserUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function UsersPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<UserOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "login", header: t("admin.users.login"), render: (r) => r.login },
    {
      key: "name",
      header: t("admin.users.name"),
      render: (r) => `${r.first_name} ${r.last_name}`,
    },
    { key: "title", header: t("admin.users.title"), render: (r) => r.title ?? "—" },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "login", label: t("admin.users.login"), type: "text", required: true },
    {
      name: "password",
      label: t("admin.users.password"),
      type: "password",
      helpText: t("admin.users.passwordHelp"),
    },
    { name: "title", label: t("admin.users.title"), type: "text" },
    { name: "first_name", label: t("admin.users.firstName"), type: "text", required: true },
    { name: "last_name", label: t("admin.users.lastName"), type: "text", required: true },
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
      resourceKey="users"
      title={t("admin.users.title_plural")}
      newLabel={t("admin.users.new")}
      api={api.adminUsers}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              login: row.login,
              password: "",
              title: row.title ?? "",
              first_name: row.first_name,
              last_name: row.last_name,
              valid_id: row.valid_id,
            }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): UserCreate => ({
        login: v.login as string,
        password: v.password as string,
        title: (v.title as string) || null,
        first_name: v.first_name as string,
        last_name: v.last_name as string,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): UserUpdate => ({
        login: v.login as string,
        title: (v.title as string) || null,
        first_name: v.first_name as string,
        last_name: v.last_name as string,
        valid_id: Number(v.valid_id) || 1,
        ...(v.password ? { password: v.password as string } : {}),
      })}
    />
  );
}
