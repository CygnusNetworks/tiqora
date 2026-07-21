import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  ApiError,
  type AdminPage,
  type QueueVariableOut,
  type QueueVariableCreate,
  type QueueVariableUpdate,
} from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { CrudDrawer, type FieldDef, type FieldValues } from "@/components/admin/CrudDrawer";
import { Button } from "@/components/ui/Button";

/** Sentinel for the "Global (all queues)" option → queue_id = null / global_only. */
const GLOBAL_SENTINEL = "global";

type QueueScope = typeof GLOBAL_SENTINEL | number;

/**
 * List queue variables with server-side filter.
 * adminCrud.list does not forward queue_id/global_only, so we call request directly.
 */
function listScopedVariables(
  scope: QueueScope,
  signal?: AbortSignal,
): Promise<AdminPage<QueueVariableOut>> {
  const query =
    scope === GLOBAL_SENTINEL
      ? { global_only: true, page_size: 500 }
      : { queue_id: scope, page_size: 500 };
  return api.request<AdminPage<QueueVariableOut>>("GET", "/api/v1/admin/queue-variables", {
    query,
    signal,
  });
}

export function QueueVariablesPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [scope, setScope] = useState<QueueScope>(GLOBAL_SENTINEL);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<QueueVariableOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const queuesQ = useQuery({
    queryKey: ["admin", "queues", "for-queue-variables"],
    queryFn: ({ signal }) => api.adminQueues.list({ valid: "valid", pageSize: 500 }, signal),
    staleTime: 5 * 60 * 1000,
  });

  const varsQ = useQuery({
    queryKey: ["admin", "queue-variables", scope],
    queryFn: ({ signal }) => listScopedVariables(scope, signal),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["admin", "queue-variables"] });

  const createM = useMutation({
    mutationFn: (body: QueueVariableCreate) => api.adminQueueVariables.create(body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const updateM = useMutation({
    mutationFn: ({ id, body }: { id: number; body: QueueVariableUpdate }) =>
      api.adminQueueVariables.update(id, body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  // Hard DELETE (no soft-valid flag on this table); adminCrud.deactivate maps to DELETE.
  const deleteM = useMutation({
    mutationFn: (id: number) => api.adminQueueVariables.deactivate(id),
    onSuccess: () => invalidate(),
  });

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };

  const openEdit = (row: QueueVariableOut) => {
    setEditing(row);
    setFormError(null);
    setDrawerOpen(true);
  };

  const queueIdForCreate = scope === GLOBAL_SENTINEL ? null : scope;

  const handleSubmit = async (values: FieldValues) => {
    setFormError(null);
    const name = String(values.name ?? "").trim();
    const value = String(values.value ?? "");
    try {
      if (editing) {
        await updateM.mutateAsync({
          id: editing.id,
          body: { name, value },
        });
      } else {
        await createM.mutateAsync({
          name,
          value,
          queue_id: queueIdForCreate,
        });
      }
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      throw err;
    }
  };

  const columns: DataTableColumn<QueueVariableOut>[] = [
    {
      key: "name",
      header: t("admin.queueVariables.name"),
      mono: true,
      render: (r) => r.name,
    },
    {
      key: "value",
      header: t("admin.queueVariables.value"),
      render: (r) => r.value ?? "",
    },
  ];

  const fields: FieldDef[] = [
    {
      name: "name",
      label: t("admin.queueVariables.name"),
      type: "text",
      required: true,
      mono: true,
      helpText: t("admin.queueVariables.nameHelp"),
    },
    {
      name: "value",
      label: t("admin.queueVariables.value"),
      type: "textarea",
      rows: 3,
    },
  ];

  const rows = varsQ.data?.items ?? [];
  const queues = queuesQ.data?.items ?? [];

  return (
    <div className="space-y-3 p-4" data-testid="admin-queue-variables-page">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.queueVariables.title_plural")}
        </h1>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-queue-variables-new"
          onClick={openCreate}
        >
          {t("admin.queueVariables.new")}
        </Button>
      </div>

      <label className="flex flex-wrap items-center gap-2 text-sm text-ink">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.queueVariables.queue")}
        </span>
        <select
          data-testid="admin-queue-variables-queue-select"
          className="min-w-[16rem] rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          value={scope === GLOBAL_SENTINEL ? GLOBAL_SENTINEL : String(scope)}
          onChange={(e) => {
            const v = e.target.value;
            setScope(v === GLOBAL_SENTINEL ? GLOBAL_SENTINEL : Number(v));
          }}
        >
          <option value={GLOBAL_SENTINEL}>{t("admin.queueVariables.global")}</option>
          {queues.map((q) => (
            <option key={q.id} value={q.id}>
              {q.name}
            </option>
          ))}
        </select>
      </label>

      <p className="text-xs text-muted" data-testid="admin-queue-variables-hint">
        {scope === GLOBAL_SENTINEL
          ? t("admin.queueVariables.globalHint")
          : t("admin.queueVariables.queueHint")}
      </p>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        isLoading={varsQ.isLoading}
        emptyLabel={t("admin.queueVariables.empty")}
        onEdit={openEdit}
        onDeactivate={(row) => deleteM.mutate(row.id)}
        testId="admin-queue-variables-table"
      />

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={
          editing
            ? t("admin.form.editTitle", { title: t("admin.queueVariables.title_plural") })
            : t("admin.queueVariables.new")
        }
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={
          editing
            ? { name: editing.name, value: editing.value ?? "" }
            : { name: "", value: "" }
        }
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-queue-variables-form"
      />
    </div>
  );
}
