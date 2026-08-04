import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";

type Row = {
  id: number;
  filename: string;
  attribute_1: string;
  attribute_2: string;
  acl_data: string;
  priority: number;
};

export function TicketAttributeRelationsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [filename, setFilename] = useState("relations.csv");
  const [priority, setPriority] = useState(1);
  const [csv, setCsv] = useState("Service;Queue\nHardware;Support\n");
  const [error, setError] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["admin", "ticket-attribute-relations"],
    queryFn: ({ signal }) => api.listTicketAttributeRelations(signal),
  });

  const createM = useMutation({
    mutationFn: () =>
      api.createTicketAttributeRelation({
        filename,
        acl_data: csv,
        priority,
      }),
    onSuccess: async () => {
      setOpen(false);
      setError(null);
      await qc.invalidateQueries({ queryKey: ["admin", "ticket-attribute-relations"] });
    },
    onError: (e: unknown) => {
      setError(e instanceof ApiError ? String(e.message) : "Failed");
    },
  });

  const deleteM = useMutation({
    mutationFn: (id: number) => api.deleteTicketAttributeRelation(id),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["admin", "ticket-attribute-relations"] });
    },
  });

  const columns: DataTableColumn<Row>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    { key: "filename", header: t("admin.tar.filename"), render: (r) => r.filename },
    {
      key: "attrs",
      header: t("admin.tar.attributes"),
      render: (r) => (
        <span className="font-mono text-xs">
          {r.attribute_1} → {r.attribute_2}
        </span>
      ),
    },
    { key: "priority", header: t("admin.tar.priority"), render: (r) => r.priority },
    {
      key: "actions",
      header: "",
      render: (r) => (
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            if (confirm(t("admin.tar.confirmDelete"))) deleteM.mutate(r.id);
          }}
        >
          {t("common.delete")}
        </Button>
      ),
    },
  ];

  return (
    <div className="space-y-3 p-4" data-testid="admin-tar-page">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.tar.title")}
        </h1>
        <Button size="sm" onClick={() => setOpen(true)} data-testid="admin-tar-new">
          {t("admin.tar.new")}
        </Button>
      </div>
      <p className="max-w-2xl text-sm text-muted">{t("admin.tar.help")}</p>
      <DataTable
        columns={columns}
        rows={(listQ.data as Row[] | undefined) ?? []}
        rowKey={(r) => r.id}
        isLoading={listQ.isLoading}
        testId="admin-tar-table"
      />

      <Dialog open={open} onClose={() => setOpen(false)} title={t("admin.tar.new")}>
        <div className="space-y-3">
          {error && <p className="text-sm text-danger">{error}</p>}
          <label className="block text-sm">
            <span className="mb-1 block text-xs font-medium uppercase text-muted">
              {t("admin.tar.filename")}
            </span>
            <input
              className="w-full rounded border border-hairline px-2 py-1.5 text-sm"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-xs font-medium uppercase text-muted">
              {t("admin.tar.priority")}
            </span>
            <input
              type="number"
              min={1}
              className="w-full rounded border border-hairline px-2 py-1.5 text-sm"
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value) || 1)}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-xs font-medium uppercase text-muted">
              {t("admin.tar.csv")}
            </span>
            <textarea
              className="h-40 w-full rounded border border-hairline px-2 py-1.5 font-mono text-xs"
              value={csv}
              onChange={(e) => setCsv(e.target.value)}
            />
          </label>
          <Button size="sm" onClick={() => createM.mutate()} disabled={createM.isPending}>
            {t("common.save")}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
