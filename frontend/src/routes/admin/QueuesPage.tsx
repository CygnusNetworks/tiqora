import { useTranslation } from "react-i18next";
import { api, type QueueOut, type QueueCreate, type QueueUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function QueuesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<QueueOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.queues.name"), render: (r) => r.name },
    { key: "group_id", header: t("admin.queues.groupId"), mono: true, render: (r) => r.group_id },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.queues.name"), type: "text", required: true },
    { name: "group_id", label: t("admin.queues.groupId"), type: "number", required: true },
    {
      name: "system_address_id",
      label: t("admin.queues.systemAddressId"),
      type: "number",
      required: true,
    },
    { name: "salutation_id", label: t("admin.queues.salutationId"), type: "number", required: true },
    { name: "signature_id", label: t("admin.queues.signatureId"), type: "number", required: true },
    { name: "follow_up_id", label: t("admin.queues.followUpId"), type: "number", required: true },
    {
      name: "follow_up_lock",
      label: t("admin.queues.followUpLock"),
      type: "checkbox",
    },
    { name: "unlock_timeout", label: t("admin.queues.unlockTimeout"), type: "number" },
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
      resourceKey="queues"
      title={t("admin.queues.title_plural")}
      newLabel={t("admin.queues.new")}
      api={api.adminQueues}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              name: row.name,
              group_id: row.group_id,
              system_address_id: row.system_address_id,
              salutation_id: row.salutation_id,
              signature_id: row.signature_id,
              follow_up_id: row.follow_up_id,
              follow_up_lock: Boolean(row.follow_up_lock),
              unlock_timeout: row.unlock_timeout ?? "",
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { follow_up_lock: false, valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): QueueCreate => ({
        name: v.name as string,
        group_id: Number(v.group_id),
        system_address_id: Number(v.system_address_id),
        salutation_id: Number(v.salutation_id),
        signature_id: Number(v.signature_id),
        follow_up_id: Number(v.follow_up_id),
        follow_up_lock: v.follow_up_lock ? 1 : 0,
        unlock_timeout: v.unlock_timeout === "" ? null : Number(v.unlock_timeout),
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): QueueUpdate => ({
        name: v.name as string,
        group_id: Number(v.group_id),
        system_address_id: Number(v.system_address_id),
        salutation_id: Number(v.salutation_id),
        signature_id: Number(v.signature_id),
        follow_up_id: Number(v.follow_up_id),
        follow_up_lock: v.follow_up_lock ? 1 : 0,
        unlock_timeout: v.unlock_timeout === "" ? null : Number(v.unlock_timeout),
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
