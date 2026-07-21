import { useTranslation } from "react-i18next";
import { api, type SignatureOut, type SignatureWrite, type SignatureUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { insertTagAtCursor } from "@/components/admin/otrsPlaceholders";
import { VariableReference } from "@/components/admin/VariableReference";
import { formatDateTime } from "@/lib/format";

export function SignaturesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<SignatureOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.signatures.name"), render: (r) => r.name },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.signatures.name"), type: "text", required: true },
    // Prose body — proportional UI font (not monospace).
    {
      name: "text",
      label: t("admin.signatures.text"),
      type: "textarea",
      required: true,
      mono: false,
      rows: 10,
      afterControl: ({ value, onChange, controlId }) => (
        <VariableReference
          onInsert={(tag) => {
            const el = document.getElementById(controlId) as HTMLTextAreaElement | null;
            const text = typeof value === "string" ? value : "";
            insertTagAtCursor(el, text, tag, (next) => onChange(next));
          }}
        />
      ),
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
      resourceKey="signatures"
      title={t("admin.signatures.title_plural")}
      newLabel={t("admin.signatures.new")}
      api={api.adminSignatures}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              name: row.name,
              text: row.text,
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): SignatureWrite => ({
        name: v.name as string,
        text: v.text as string,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): SignatureUpdate => ({
        name: v.name as string,
        text: v.text as string,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
