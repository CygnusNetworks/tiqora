import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  ApiError,
  type QueueCustomerLinkOut,
  type QueueCustomerLinkCreate,
  type QueueCustomerLinkUpdate,
} from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { CrudDrawer, type FieldDef, type FieldValues } from "@/components/admin/CrudDrawer";
import { Button } from "@/components/ui/Button";
import { PlusIcon } from "@/components/ui/icons";

const LIST_KEY = ["admin", "queue-customer-links"];
const QUEUES_KEY = ["admin", "queues", "for-customer-links"];

export function QueueCustomerLinksPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<QueueCustomerLinkOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const linksQ = useQuery({
    queryKey: LIST_KEY,
    queryFn: ({ signal }) => api.adminQueueCustomerLinks.list(signal),
  });

  const queuesQ = useQuery({
    queryKey: QUEUES_KEY,
    queryFn: ({ signal }) => api.adminQueues.list({ valid: "valid", pageSize: 500 }, signal),
    staleTime: 5 * 60 * 1000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: LIST_KEY });

  const createM = useMutation({
    mutationFn: (body: QueueCustomerLinkCreate) => api.adminQueueCustomerLinks.create(body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const updateM = useMutation({
    mutationFn: ({ id, body }: { id: number; body: QueueCustomerLinkUpdate }) =>
      api.adminQueueCustomerLinks.update(id, body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const deleteM = useMutation({
    mutationFn: (id: number) => api.adminQueueCustomerLinks.remove(id),
    onSuccess: () => invalidate(),
  });

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };

  const openEdit = (row: QueueCustomerLinkOut) => {
    setEditing(row);
    setFormError(null);
    setDrawerOpen(true);
  };

  const rows = linksQ.data ?? [];
  const usedQueueIds = new Set(rows.map((r) => r.queue_id));
  const queues = queuesQ.data?.items ?? [];
  const availableQueues = editing
    ? queues
    : queues.filter((q) => !usedQueueIds.has(q.id));

  const handleSubmit = async (values: FieldValues) => {
    setFormError(null);
    const urlTemplate = String(values.url_template ?? "").trim();
    const adminUrlTemplate = String(values.admin_url_template ?? "").trim();
    const label = String(values.label ?? "").trim();
    const visibility = String(values.visibility ?? "all");
    try {
      if (editing) {
        await updateM.mutateAsync({
          id: editing.id,
          body: {
            url_template: urlTemplate,
            admin_url_template: adminUrlTemplate || null,
            label: label || null,
            visibility,
          },
        });
      } else {
        await createM.mutateAsync({
          queue_id: Number(values.queue_id),
          url_template: urlTemplate,
          admin_url_template: adminUrlTemplate || null,
          label: label || null,
          visibility,
        });
      }
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      throw err;
    }
  };

  const columns: DataTableColumn<QueueCustomerLinkOut>[] = [
    {
      key: "queue",
      header: t("admin.customerLinks.queue"),
      render: (r) => r.queue_name ?? `#${r.queue_id}`,
    },
    {
      key: "label",
      header: t("admin.customerLinks.label"),
      render: (r) => r.label || t("admin.customerLinks.defaultLabel"),
    },
    {
      key: "visibility",
      header: t("admin.customerLinks.visibility"),
      render: (r) =>
        r.visibility === "admins"
          ? t("admin.customerLinks.visibilityAdmins")
          : t("admin.customerLinks.visibilityAll"),
    },
    {
      key: "admin_url",
      header: t("admin.customerLinks.adminUrlTemplate"),
      mono: true,
      render: (r) => (r.admin_url_template ? "✓" : "—"),
    },
  ];

  const fields: FieldDef[] = [
    {
      name: "queue_id",
      label: t("admin.customerLinks.queue"),
      type: "select",
      required: true,
      hideOnEdit: true,
      options: availableQueues.map((q) => ({ value: q.id, label: q.name })),
    },
    {
      name: "label",
      label: t("admin.customerLinks.label"),
      type: "text",
      placeholder: t("admin.customerLinks.defaultLabel"),
      helpText: t("admin.customerLinks.labelHelp"),
    },
    {
      name: "url_template",
      label: t("admin.customerLinks.urlTemplate"),
      type: "text",
      required: true,
      mono: true,
      helpText: t("admin.customerLinks.templateHelp"),
    },
    {
      name: "admin_url_template",
      label: t("admin.customerLinks.adminUrlTemplate"),
      type: "text",
      mono: true,
      helpText: t("admin.customerLinks.adminUrlTemplateHelp"),
    },
    {
      name: "visibility",
      label: t("admin.customerLinks.visibility"),
      type: "select",
      required: true,
      options: [
        { value: "all", label: t("admin.customerLinks.visibilityAll") },
        { value: "admins", label: t("admin.customerLinks.visibilityAdmins") },
      ],
    },
  ];

  return (
    <div className="space-y-3 p-4" data-testid="admin-customer-links-page">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">
            {t("admin.customerLinks.title_plural")}
          </h1>
          <p className="mt-1 text-xs text-muted">{t("admin.customerLinks.intro")}</p>
        </div>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-customer-links-new"
          onClick={openCreate}
          aria-label={t("admin.customerLinks.new")}
          title={t("admin.customerLinks.new")}
          className="!px-2"
        >
          <PlusIcon className="text-[16px]" />
        </Button>
      </div>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        isLoading={linksQ.isLoading}
        emptyLabel={t("admin.customerLinks.empty")}
        onEdit={openEdit}
        onDelete={(row) => deleteM.mutate(row.id)}
        testId="admin-customer-links-table"
      />

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={
          editing
            ? t("admin.form.editTitle", { title: t("admin.customerLinks.title_plural") })
            : t("admin.customerLinks.new")
        }
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={
          editing
            ? {
                queue_id: editing.queue_id,
                label: editing.label ?? "",
                url_template: editing.url_template,
                admin_url_template: editing.admin_url_template ?? "",
                visibility: editing.visibility,
              }
            : {
                queue_id: availableQueues[0]?.id ?? "",
                label: "",
                url_template: "",
                admin_url_template: "",
                visibility: "all",
              }
        }
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-customer-links-form"
      />
    </div>
  );
}
