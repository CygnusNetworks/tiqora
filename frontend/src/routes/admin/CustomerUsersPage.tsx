import { useTranslation } from "react-i18next";
import {
  api,
  type CustomerUserAdminOut,
  type CustomerUserAdminCreate,
  type CustomerUserAdminUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function CustomerUsersPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<CustomerUserAdminOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "login", header: t("admin.customerUsers.login"), render: (r) => r.login },
    { key: "email", header: t("admin.customerUsers.email"), render: (r) => r.email },
    {
      key: "customer_id",
      header: t("admin.customerUsers.customerId"),
      mono: true,
      render: (r) => r.customer_id,
    },
    {
      key: "name",
      header: t("admin.customerUsers.name"),
      render: (r) => `${r.first_name} ${r.last_name}`,
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "login", label: t("admin.customerUsers.login"), type: "text", required: true },
    { name: "email", label: t("admin.customerUsers.email"), type: "text", required: true },
    {
      name: "customer_id",
      label: t("admin.customerUsers.customerId"),
      type: "text",
      required: true,
    },
    {
      name: "password",
      label: t("admin.users.password"),
      type: "password",
      helpText: t("admin.users.passwordHelp"),
    },
    { name: "first_name", label: t("admin.customerUsers.firstName"), type: "text", required: true },
    { name: "last_name", label: t("admin.customerUsers.lastName"), type: "text", required: true },
    { name: "phone", label: t("admin.customerUsers.phone"), type: "text" },
    { name: "city", label: t("admin.customerUsers.city"), type: "text" },
    { name: "country", label: t("admin.customerUsers.country"), type: "text" },
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
      resourceKey="customer-users"
      title={t("admin.customerUsers.title_plural")}
      newLabel={t("admin.customerUsers.new")}
      api={api.adminCustomerUsers}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              login: row.login,
              email: row.email,
              customer_id: row.customer_id,
              password: "",
              first_name: row.first_name,
              last_name: row.last_name,
              phone: row.phone ?? "",
              city: row.city ?? "",
              country: row.country ?? "",
              valid_id: row.valid_id,
            }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): CustomerUserAdminCreate => ({
        login: v.login as string,
        email: v.email as string,
        customer_id: v.customer_id as string,
        password: (v.password as string) || null,
        first_name: v.first_name as string,
        last_name: v.last_name as string,
        phone: (v.phone as string) || null,
        city: (v.city as string) || null,
        country: (v.country as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): CustomerUserAdminUpdate => ({
        login: v.login as string,
        email: v.email as string,
        customer_id: v.customer_id as string,
        first_name: v.first_name as string,
        last_name: v.last_name as string,
        phone: (v.phone as string) || null,
        city: (v.city as string) || null,
        country: (v.country as string) || null,
        valid_id: Number(v.valid_id) || 1,
        ...(v.password ? { password: v.password as string } : {}),
      })}
    />
  );
}
