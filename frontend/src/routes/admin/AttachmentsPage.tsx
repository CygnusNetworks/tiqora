import { useTranslation } from "react-i18next";
import {
  api,
  type StandardAttachmentOut,
  type StandardAttachmentCreate,
  type StandardAttachmentUpdate,
} from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

/** Read a File as a raw base64 string (no data-URL prefix). */
function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

type ContentPayload = {
  content: string;
  filename: string;
  content_type: string;
};

function isContentPayload(v: unknown): v is ContentPayload {
  return (
    typeof v === "object" &&
    v !== null &&
    typeof (v as ContentPayload).content === "string" &&
    (v as ContentPayload).content.length > 0
  );
}

/**
 * Standard attachments master CRUD (Znuny `standard_attachment`).
 * Content is base64-encoded; the form offers a file picker that fills content,
 * filename and content_type, with optional manual overrides.
 */
export function AttachmentsPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<StandardAttachmentOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.attachments.name"), render: (r) => r.name },
    {
      key: "filename",
      header: t("admin.attachments.filename"),
      render: (r) => r.filename,
    },
    {
      key: "content_type",
      header: t("admin.attachments.contentType"),
      render: (r) => r.content_type,
    },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.change_time, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.attachments.name"), type: "text", required: true },
    {
      name: "filename",
      label: t("admin.attachments.filename"),
      type: "text",
      // Filled from the file picker when empty; optional override.
    },
    {
      name: "content_type",
      label: t("admin.attachments.contentType"),
      type: "text",
      placeholder: "application/pdf",
    },
    {
      name: "content",
      label: t("admin.attachments.content"),
      type: "custom",
      required: true,
      helpText: t("admin.attachments.contentHelp"),
      render: (value, onChange) => {
        const loaded = isContentPayload(value)
          ? value
          : typeof value === "string" && value.length > 0
            ? { content: value, filename: "", content_type: "" }
            : null;
        return (
          <div className="space-y-1">
            <input
              type="file"
              data-testid="admin-form-content-file"
              className="block w-full text-sm text-ink file:mr-3 file:rounded-md file:border-0 file:bg-surface-subtle file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-ink hover:file:bg-hairline"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                void fileToBase64(file).then((content) => {
                  onChange({
                    content,
                    filename: file.name,
                    content_type: file.type || "application/octet-stream",
                  } satisfies ContentPayload);
                });
              }}
            />
            {loaded && (
              <p className="text-xs text-muted" data-testid="admin-form-content-ready">
                {t("admin.attachments.contentReady", {
                  chars: loaded.content.length,
                })}
              </p>
            )}
          </div>
        );
      },
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
      resourceKey="attachments"
      title={t("admin.attachments.title_plural")}
      newLabel={t("admin.attachments.new")}
      api={api.adminAttachments}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      toFormValues={(row) =>
        row
          ? {
              name: row.name,
              filename: row.filename,
              content_type: row.content_type,
              // Prefill so required validation passes on edit without re-upload.
              content: {
                content: row.content,
                filename: row.filename,
                content_type: row.content_type,
              } satisfies ContentPayload,
              comments: row.comments ?? "",
              valid_id: row.valid_id,
            }
          : { valid_id: 1, content_type: "application/octet-stream" }
      }
      toCreateBody={(v: FieldValues): StandardAttachmentCreate => {
        const payload = isContentPayload(v.content)
          ? v.content
          : typeof v.content === "string"
            ? {
                content: v.content,
                filename: String(v.filename ?? ""),
                content_type: String(v.content_type ?? "application/octet-stream"),
              }
            : null;
        if (!payload) {
          throw new Error(t("admin.attachments.contentRequired"));
        }
        return {
          name: v.name as string,
          content_type: (v.content_type as string) || payload.content_type,
          content: payload.content,
          filename: (v.filename as string) || payload.filename,
          comments: (v.comments as string) || null,
          valid_id: Number(v.valid_id) || 1,
        };
      }}
      toUpdateBody={(v: FieldValues): StandardAttachmentUpdate => {
        const payload = isContentPayload(v.content)
          ? v.content
          : typeof v.content === "string" && v.content.length > 0
            ? {
                content: v.content,
                filename: String(v.filename ?? ""),
                content_type: String(v.content_type ?? ""),
              }
            : null;
        return {
          name: v.name as string,
          content_type: (v.content_type as string) || payload?.content_type || null,
          content: payload?.content ?? null,
          filename: (v.filename as string) || payload?.filename || null,
          comments: (v.comments as string) || null,
          valid_id: Number(v.valid_id) || 1,
        };
      }}
    />
  );
}
