import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { api, type SlaOut, type SlaCreate, type SlaUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

export function SlasPage() {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);

  const columns: DataTableColumn<SlaOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.slas.name"), render: (r) => r.name },
    {
      key: "solution",
      header: t("admin.slas.solutionTime"),
      mono: true,
      render: (r) => r.solution_time,
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.slas.name"), type: "text", required: true },
    {
      name: "first_response_time",
      label: t("admin.slas.firstResponseTime"),
      type: "number",
    },
    { name: "update_time", label: t("admin.slas.updateTime"), type: "number" },
    { name: "solution_time", label: t("admin.slas.solutionTime"), type: "number" },
    { name: "calendar_name", label: t("admin.slas.calendar"), type: "text" },
    { name: "comments", label: t("admin.table.comments"), type: "text" },
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
      resourceKey="slas"
      title={t("admin.slas.title_plural")}
      newLabel={t("admin.slas.new")}
      api={api.adminSlas}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              name: row.name,
              first_response_time: row.first_response_time,
              update_time: row.update_time,
              solution_time: row.solution_time,
              calendar_name: row.calendar_name ?? "",
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : {
              valid_id: 1,
              first_response_time: 0,
              update_time: 0,
              solution_time: 0,
            }
      }
      toCreateBody={(v: FieldValues): SlaCreate => ({
        name: v.name as string,
        first_response_time: Number(v.first_response_time) || 0,
        update_time: Number(v.update_time) || 0,
        solution_time: Number(v.solution_time) || 0,
        calendar_name: (v.calendar_name as string) || null,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
        service_ids: [],
      })}
      toUpdateBody={(v: FieldValues): SlaUpdate => ({
        name: v.name as string,
        first_response_time: Number(v.first_response_time) || 0,
        update_time: Number(v.update_time) || 0,
        solution_time: Number(v.solution_time) || 0,
        calendar_name: (v.calendar_name as string) || null,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
