import { useTranslation } from "react-i18next";
import { api, type WebhookOut, type WebhookCreate, type WebhookUpdate } from "@/lib/api";
import { AdminResourcePage } from "@/components/admin/AdminResourcePage";
import type { FieldDef, FieldValues } from "@/components/admin/CrudDrawer";
import type { DataTableColumn } from "@/components/admin/DataTable";
import { formatDateTime } from "@/lib/format";

function parseEvents(value: unknown): string[] {
  return String(value ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function WebhooksPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";

  const columns: DataTableColumn<WebhookOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "name", header: t("admin.webhooks.name"), render: (r) => r.name },
    { key: "url", header: t("admin.webhooks.url"), render: (r) => r.url },
    { key: "events", header: t("admin.webhooks.events"), render: (r) => r.events.join(", ") },
    {
      key: "changed",
      header: t("admin.table.changed"),
      render: (r) => formatDateTime(r.changed, locale),
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.webhooks.name"), type: "text", required: true },
    { name: "url", label: t("admin.webhooks.url"), type: "text", required: true },
    {
      name: "secret",
      label: t("admin.webhooks.secret"),
      type: "text",
      required: true,
      helpText: t("admin.webhooks.secretHelp"),
    },
    {
      name: "events",
      label: t("admin.webhooks.events"),
      type: "text",
      placeholder: t("admin.webhooks.eventsPlaceholder"),
      helpText: t("admin.webhooks.eventsHelp"),
    },
    { name: "valid", label: t("admin.table.valid"), type: "checkbox" },
  ];

  return (
    <AdminResourcePage
      resourceKey="webhooks"
      title={t("admin.webhooks.title_plural")}
      newLabel={t("admin.webhooks.new")}
      api={api.adminWebhooks}
      idOf={(r) => r.id}
      columns={columns}
      fields={fields}
      isRowValid={(r) => r.valid}
      toFormValues={(row) =>
        row
          ? { name: row.name, url: row.url, secret: "", events: row.events.join(", "), valid: row.valid }
          : { valid: true }
      }
      toCreateBody={(v: FieldValues): WebhookCreate => ({
        name: v.name as string,
        url: v.url as string,
        secret: v.secret as string,
        events: parseEvents(v.events),
        valid: Boolean(v.valid ?? true),
      })}
      toUpdateBody={(v: FieldValues): WebhookUpdate => ({
        name: v.name as string,
        url: v.url as string,
        ...(v.secret ? { secret: v.secret as string } : {}),
        events: parseEvents(v.events),
        valid: Boolean(v.valid ?? true),
      })}
    />
  );
}
