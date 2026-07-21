import { useTranslation } from "react-i18next";
import {
  api,
  type StandardTemplateOut,
  type StandardTemplateCreate,
  type StandardTemplateUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { insertTagAtCursor } from "@/components/admin/otrsPlaceholders";
import { VariableReference } from "@/components/admin/VariableReference";
import { Badge } from "@/components/ui/Badge";
import { formatDateTime } from "@/lib/format";

const TEMPLATE_TYPE_OPTIONS = [
  { value: "Answer", label: "Answer" },
  { value: "Create", label: "Create" },
  { value: "Note", label: "Note" },
  { value: "Email", label: "Email" },
];

export function TemplatesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<StandardTemplateOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.templates.name"), render: (r) => r.name },
    {
      key: "template_type",
      header: t("admin.templates.type"),
      render: (r) => r.template_type,
    },
    {
      key: "assigned_queue_count",
      header: t("admin.templates.usage"),
      render: (r) => {
        const n = r.assigned_queue_count ?? 0;
        return (
          <Badge
            tone={n > 0 ? "default" : "muted"}
            data-testid={`admin-template-usage-${r.id}`}
          >
            {n > 0 ? t("admin.templates.inQueues", { count: n }) : "0"}
          </Badge>
        );
      },
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.templates.name"), type: "text", required: true },
    {
      name: "template_type",
      label: t("admin.templates.type"),
      type: "select",
      options: TEMPLATE_TYPE_OPTIONS,
    },
    // Prose body — proportional UI font (not monospace).
    {
      name: "text",
      label: t("admin.templates.text"),
      type: "textarea",
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
      resourceKey="templates"
      title={t("admin.templates.title_plural")}
      newLabel={t("admin.templates.new")}
      api={api.adminTemplates}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              name: row.name,
              template_type: row.template_type,
              text: row.text ?? "",
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { template_type: "Answer", valid_id: 1 }
      }
      toCreateBody={(v: FieldValues): StandardTemplateCreate => ({
        name: v.name as string,
        template_type: (v.template_type as string) || "Answer",
        text: (v.text as string) || null,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
      toUpdateBody={(v: FieldValues): StandardTemplateUpdate => ({
        name: v.name as string,
        template_type: (v.template_type as string) || "Answer",
        text: (v.text as string) || null,
        comments: (v.comments as string) || null,
        valid_id: Number(v.valid_id) || 1,
      })}
    />
  );
}
