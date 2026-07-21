import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  type PlaceholderFieldOut,
  type PlaceholderFieldCreate,
  type PlaceholderFieldUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";

type SourceTable = "customer_user" | "customer_company";

/** Dependent column picker: loads available columns for the chosen source table. */
function ColumnNameInput({
  value,
  onChange,
  source,
  listId,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  source: string;
  listId: string;
}) {
  const src = (source === "customer_company" ? "customer_company" : "customer_user") as SourceTable;
  const columnsQ = useQuery({
    queryKey: ["admin", "customer-fields", "available-columns", src],
    queryFn: ({ signal }) => api.listAvailableCustomerColumns(src, signal),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const options = columnsQ.data ?? [];
  const str = typeof value === "string" ? value : "";

  return (
    <>
      <input
        id="admin-form-column_name"
        data-testid="admin-form-column_name"
        list={listId}
        type="text"
        value={str}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 font-mono text-sm text-ink placeholder:text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
        autoComplete="off"
      />
      <datalist id={listId} data-testid="admin-form-column_name-datalist">
        {options.map((col) => (
          <option key={col} value={col} />
        ))}
      </datalist>
    </>
  );
}

export function CustomerFieldsPage() {
  const { t } = useTranslation();

  const columns: DataTableColumn<PlaceholderFieldOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    {
      key: "source_table",
      header: t("admin.customerFields.sourceTable"),
      mono: true,
      render: (r) => r.source_table,
    },
    {
      key: "column_name",
      header: t("admin.customerFields.columnName"),
      mono: true,
      render: (r) => r.column_name,
    },
    {
      key: "tag_name",
      header: t("admin.customerFields.tagName"),
      mono: true,
      render: (r) => r.tag_name,
    },
    {
      key: "label",
      header: t("admin.customerFields.label"),
      render: (r) => r.label ?? "—",
    },
    {
      key: "enabled",
      header: t("admin.customerFields.enabled"),
      render: (r) => (r.enabled ? t("admin.table.valid") : t("admin.table.invalid")),
    },
  ];

  const fields: FieldDef[] = [
    {
      name: "source_table",
      label: t("admin.customerFields.sourceTable"),
      type: "select",
      required: true,
      options: [
        { value: "customer_user", label: "customer_user" },
        { value: "customer_company", label: "customer_company" },
      ],
    },
    {
      name: "column_name",
      label: t("admin.customerFields.columnName"),
      type: "custom",
      required: true,
      helpText: t("admin.customerFields.columnNameHelp"),
      render: (value, onChange, values) => (
        <ColumnNameInput
          value={value}
          onChange={onChange}
          source={String(values.source_table ?? "customer_user")}
          listId="customer-field-column-options"
        />
      ),
    },
    {
      name: "tag_name",
      label: t("admin.customerFields.tagName"),
      type: "text",
      required: true,
      mono: true,
      helpText: t("admin.customerFields.tagNameHelp"),
    },
    {
      name: "label",
      label: t("admin.customerFields.label"),
      type: "text",
    },
    {
      name: "enabled",
      label: t("admin.customerFields.enabled"),
      type: "checkbox",
    },
  ];

  return (
    <AdminResourcePage
      resourceKey="customer-fields"
      title={t("admin.customerFields.title_plural")}
      newLabel={t("admin.customerFields.new")}
      api={api.adminCustomerFields}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              source_table: row.source_table,
              column_name: row.column_name,
              tag_name: row.tag_name,
              label: row.label ?? "",
              enabled: row.enabled,
            }
          : {
              source_table: "customer_user",
              column_name: "",
              tag_name: "",
              label: "",
              enabled: true,
            }
      }
      toCreateBody={(v: FieldValues): PlaceholderFieldCreate => ({
        source_table: String(v.source_table ?? "customer_user"),
        column_name: String(v.column_name ?? ""),
        tag_name: String(v.tag_name ?? ""),
        label: v.label ? String(v.label) : null,
        enabled: Boolean(v.enabled ?? true),
      })}
      toUpdateBody={(v: FieldValues): PlaceholderFieldUpdate => ({
        source_table: String(v.source_table ?? "customer_user"),
        column_name: String(v.column_name ?? ""),
        tag_name: String(v.tag_name ?? ""),
        label: v.label ? String(v.label) : null,
        enabled: Boolean(v.enabled ?? true),
      })}
    />
  );
}
