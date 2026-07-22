import { useTranslation } from "react-i18next";
import {
  api,
  type CustomerCompanyOut,
  type CustomerCompanyCreate,
  type CustomerCompanyUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function CustomerCompaniesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<CustomerCompanyOut>[] = [
    {
      key: "customer_id",
      header: t("admin.customerCompanies.customerId"),
      mono: true,
      render: (r) => r.customer_id,
    },
    { key: "name", header: t("admin.customerCompanies.name"), render: (r) => r.name },
    { key: "city", header: t("admin.customerUsers.city"), render: (r) => r.city ?? "—" },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    {
      name: "customer_id",
      label: t("admin.customerCompanies.customerId"),
      type: "text",
      required: true,
      help: {
        title: t("admin.customerCompanies.customerId"),
        description: t("admin.help.customerCompanies.customerId"),
      },
    },
    { name: "name", label: t("admin.customerCompanies.name"), type: "text", required: true },
    { name: "street", label: t("admin.customerUsers.street"), type: "text" },
    { name: "city", label: t("admin.customerUsers.city"), type: "text" },
    { name: "country", label: t("admin.customerUsers.country"), type: "text" },
    { name: "url", label: t("admin.customerCompanies.url"), type: "text" },
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
      resourceKey="customer-companies"
      title={t("admin.customerCompanies.title_plural")}
      newLabel={t("admin.customerCompanies.new")}
      api={api.adminCustomerCompanies}
      idOf={(r) => r.customer_id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              customer_id: row.customer_id,
              name: row.name,
              street: row.street ?? "",
              city: row.city ?? "",
              country: row.country ?? "",
              url: row.url ?? "",
              valid_id: row.valid_id,
            }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): CustomerCompanyCreate => ({
        customer_id: v.customer_id as string,
        name: v.name as string,
        street: (v.street as string) || null,
        city: (v.city as string) || null,
        country: (v.country as string) || null,
        url: (v.url as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): CustomerCompanyUpdate => ({
        name: v.name as string,
        street: (v.street as string) || null,
        city: (v.city as string) || null,
        country: (v.country as string) || null,
        url: (v.url as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
