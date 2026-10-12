import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "@tanstack/react-router";
import { processDesignerApi, type ProcessDraftSummary, type ProcessImportOut } from "@/lib/processDesignerApi";
import { apiErrorMessage } from "@/lib/avatarApi";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { DeployStatusCard } from "@/components/process/DeployStatusCard";
import { ImportDialog } from "@/components/process-designer/ImportDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useConfirm, usePrompt } from "@/components/ui/ConfirmDialog";

const STATE_TONE: Record<string, "success" | "muted" | "warn"> = {
  Active: "success",
  Inactive: "muted",
  FadeAway: "warn",
};

/**
 * Process list of the designer: ``GET /api/v1/admin/process-designer/processes`` lists every
 * process including drafts (``sync_state != "synced"``), so a freshly created, imported or copied
 * process shows up right away. Row actions: edit, export (plain download link), copy, delete
 * (only while the process is switched off).
 */
export function ProcessesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { prompt, dialog: promptDialog } = usePrompt();
  const [importOpen, setImportOpen] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);

  const listQ = useQuery({
    queryKey: ["admin", "process-designer", "processes"],
    queryFn: ({ signal }) => processDesignerApi.listProcesses(signal),
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "process-designer", "processes"] });
    void qc.invalidateQueries({ queryKey: ["admin", "processes"] });
  };
  const open = (processEntityId: string) =>
    void navigate({ to: "/admin/processes/$processEntityId", params: { processEntityId } });

  const createM = useMutation({
    mutationFn: (name: string) => processDesignerApi.createProcess({ name, description: "" }),
    onSuccess: (draft) => {
      refresh();
      open(draft.process.entity_id);
    },
    onError: (err) =>
      setNotice({ text: apiErrorMessage(err, t("admin.processes.createError")), error: true }),
  });
  const copyM = useMutation({
    mutationFn: (id: string) => processDesignerApi.copyProcess(id),
    onSuccess: (draft) => {
      refresh();
      setNotice({ text: t("admin.processes.copied", { name: draft.process.name }), error: false });
    },
    onError: (err) =>
      setNotice({ text: apiErrorMessage(err, t("admin.processes.copyError")), error: true }),
  });
  const deleteM = useMutation({
    mutationFn: (id: string) => processDesignerApi.deleteProcess(id),
    onSuccess: refresh,
    onError: (err) =>
      setNotice({ text: apiErrorMessage(err, t("admin.processes.deleteError")), error: true }),
  });

  const onNew = async () => {
    const name = await prompt({
      title: t("admin.processes.newTitle"),
      message: t("admin.processes.newMessage"),
      placeholder: t("admin.processes.name"),
      confirmLabel: t("admin.processes.create"),
    });
    if (name) createM.mutate(name);
  };
  const onDelete = async (r: ProcessDraftSummary) => {
    const ok = await confirm({
      title: t("admin.processes.deleteTitle"),
      message: t("admin.processes.deleteMessage", { name: r.name }),
      confirmLabel: t("admin.processes.delete"),
      variant: "danger",
    });
    if (ok) deleteM.mutate(r.entity_id);
  };
  const onImported = (out: ProcessImportOut) => {
    setImportOpen(false);
    refresh();
    setNotice({ text: t("admin.processDesigner.import.success", { name: out.name }), error: false });
  };

  const columns: DataTableColumn<ProcessDraftSummary>[] = [
    {
      key: "name",
      header: t("admin.processes.name"),
      render: (r) => (
        <Link
          to="/admin/processes/$processEntityId"
          params={{ processEntityId: r.entity_id }}
          className="text-accent hover:underline"
          data-testid={`process-link-${r.entity_id}`}
        >
          {r.name}
        </Link>
      ),
    },
    { key: "entity_id", header: t("admin.processes.entityId"), mono: true, render: (r) => r.entity_id },
    {
      key: "state",
      header: t("admin.processes.stateLabel"),
      render: (r) => (
        <span className="flex flex-wrap items-center gap-1">
          <Badge tone={STATE_TONE[r.state_name] ?? "muted"} data-testid={`process-state-${r.entity_id}`}>
            {r.state_name
              ? t(`admin.processes.states.${r.state_name}`, { defaultValue: r.state_name })
              : "—"}
          </Badge>
          {r.sync_state !== "synced" ? (
            <Badge tone="warn" data-testid={`process-draft-${r.entity_id}`}>
              {t("admin.processDesigner.draft")}
            </Badge>
          ) : null}
        </span>
      ),
    },
    {
      key: "actions",
      header: t("admin.processes.actions"),
      render: (r) => (
        <span className="flex flex-wrap items-center gap-1">
          <Button
            size="sm"
            onClick={() => open(r.entity_id)}
            data-testid={`process-edit-${r.entity_id}`}
          >
            {t("admin.processes.edit")}
          </Button>
          <a
            href={processDesignerApi.exportUrl(r.entity_id)}
            download
            data-testid={`process-export-${r.entity_id}`}
            className="rounded-md border border-hairline bg-surface px-2 py-1 text-xs text-ink hover:bg-surface-subtle"
          >
            {t("admin.processes.export")}
          </a>
          <Button
            size="sm"
            onClick={() => copyM.mutate(r.entity_id)}
            disabled={copyM.isPending}
            data-testid={`process-copy-${r.entity_id}`}
          >
            {t("admin.processes.copy")}
          </Button>
          {r.state_name === "Inactive" ? (
            <Button
              size="sm"
              variant="danger"
              onClick={() => void onDelete(r)}
              data-testid={`process-delete-${r.entity_id}`}
            >
              {t("admin.processes.delete")}
            </Button>
          ) : null}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-3 p-4" data-testid="admin-processes-page">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.processes.title_plural")}
        </h1>
        <div className="flex gap-2">
          <Button onClick={() => setImportOpen(true)} data-testid="process-import">
            {t("admin.processes.import")}
          </Button>
          <Button variant="primary" onClick={() => void onNew()} data-testid="process-new">
            {t("admin.processes.new")}
          </Button>
        </div>
      </div>
      {notice ? (
        <p
          role={notice.error ? "alert" : "status"}
          data-testid="processes-notice"
          className={notice.error ? "text-sm text-danger" : "text-sm text-ink"}
        >
          {notice.text}
        </p>
      ) : null}
      <DeployStatusCard kind="process" />
      <DataTable
        columns={columns}
        rows={listQ.data ?? []}
        rowKey={(r) => r.entity_id}
        isLoading={listQ.isLoading}
        testId="admin-processes-table"
      />
      <ImportDialog open={importOpen} onClose={() => setImportOpen(false)} onImported={onImported} />
      {confirmDialog}
      {promptDialog}
    </div>
  );
}
