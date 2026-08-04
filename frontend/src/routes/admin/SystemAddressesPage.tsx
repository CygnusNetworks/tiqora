import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import {
  api,
  type SystemAddressOut,
  type SystemAddressCreate,
  type SystemAddressUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function SystemAddressesPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);

  const columns: DataTableColumn<SystemAddressOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "email", header: t("admin.systemAddresses.email"), render: (r) => r.value0 },
    { key: "name", header: t("admin.systemAddresses.name"), render: (r) => r.value1 },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    {
      name: "value0",
      label: t("admin.systemAddresses.email"),
      type: "text",
      required: true,
      help: {
        title: t("admin.systemAddresses.email"),
        description: t("admin.help.systemAddresses.email"),
      },
    },
    {
      name: "value1",
      label: t("admin.systemAddresses.name"),
      type: "text",
      required: true,
      help: {
        title: t("admin.systemAddresses.name"),
        description: t("admin.help.systemAddresses.name"),
      },
    },
    {
      name: "comments",
      label: t("admin.table.comments"),
      type: "textarea",
    },
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
      resourceKey="system-addresses"
      title={t("admin.systemAddresses.title_plural")}
      newLabel={t("admin.systemAddresses.new")}
      api={api.adminSystemAddresses}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              value0: row.value0,
              value1: row.value1,
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): SystemAddressCreate => ({
        value0: (v.value0 as string).trim(),
        value1: ((v.value1 as string) || (v.value0 as string)).trim(),
        comments: (v.comments as string) || null,
        queue_id: 1,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): SystemAddressUpdate => ({
        value0: (v.value0 as string).trim(),
        value1: ((v.value1 as string) || (v.value0 as string)).trim(),
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
