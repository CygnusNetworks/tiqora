import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { api, ApiError, type AclOut, type AclCreate } from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { CrudDrawer, type FieldDef, type FieldValues } from "@/components/admin/CrudDrawer";
import { Button } from "@/components/ui/Button";
import { PlusIcon } from "@/components/ui/icons";

export function AclPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["admin", "acl"],
    queryFn: ({ signal }) => api.listAcls(signal),
  });

  const createMut = useMutation({
    mutationFn: (body: AclCreate) => api.createAcl(body),
    onSuccess: async () => {
      setDrawerOpen(false);
      setSubmitError(null);
      await qc.invalidateQueries({ queryKey: ["admin", "acl"] });
    },
    onError: (err: unknown) => {
      setSubmitError(err instanceof ApiError ? err.message : String(err));
    },
  });

  const columns: DataTableColumn<AclOut>[] = [
    { key: "id", header: t("admin.table.id"), mono: true, render: (r) => r.id },
    {
      key: "name",
      header: t("admin.acl.name"),
      render: (r) => (
        <Link
          to="/admin/acl/$aclId"
          params={{ aclId: String(r.id) }}
          className="text-accent hover:underline"
          data-testid={`acl-link-${r.id}`}
        >
          {r.name}
        </Link>
      ),
    },
    { key: "description", header: t("admin.acl.description"), render: (r) => r.description ?? "—" },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.acl.name"), type: "text", required: true },
    { name: "description", label: t("admin.acl.description"), type: "text" },
    { name: "comments", label: t("admin.table.comments"), type: "textarea", rows: 2 },
    {
      name: "valid_id",
      label: t("admin.table.status"),
      type: "select",
      options: [
        { value: 1, label: t("admin.table.valid") },
        { value: 2, label: t("admin.table.invalid") },
      ],
      width: "half",
    },
    {
      name: "stop_after_match",
      label: t("admin.acl.stopAfterMatch"),
      type: "checkbox",
      help: {
        title: t("admin.acl.stopAfterMatch"),
        description: t("admin.help.acl.stopAfterMatch"),
      },
    },
    {
      name: "config_match",
      label: t("admin.acl.match"),
      type: "textarea",
      mono: true,
      rows: 10,
      help: { title: t("admin.acl.match"), description: t("admin.help.acl.match") },
    },
    {
      name: "config_change",
      label: t("admin.acl.change"),
      type: "textarea",
      mono: true,
      rows: 10,
      help: { title: t("admin.acl.change"), description: t("admin.help.acl.change") },
    },
  ];

  return (
    <div className="space-y-3 p-4" data-testid="admin-acl-page">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.acl.title_plural")}</h1>
        <Button
          type="button"
          onClick={() => {
            setSubmitError(null);
            setDrawerOpen(true);
          }}
          data-testid="admin-acl-new"
        >
          <PlusIcon className="h-4 w-4" />
          {t("admin.acl.new")}
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={listQ.data ?? []}
        rowKey={(r) => r.id}
        isLoading={listQ.isLoading}
        isRowValid={(r) => r.valid_id === 1}
        testId="admin-acl-table"
      />
      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={t("admin.acl.new")}
        fields={fields}
        initialValues={{ valid_id: 1, stop_after_match: false }}
        mode="create"
        size="xl"
        testIdPrefix="admin-acl"
        submitError={submitError}
        onSubmit={async (v: FieldValues) => {
          setSubmitError(null);
          await createMut.mutateAsync({
            name: String(v.name ?? ""),
            description: (v.description as string) || null,
            comments: (v.comments as string) || null,
            valid_id: Number(v.valid_id) || 1,
            stop_after_match: v.stop_after_match ? 1 : 0,
            config_match: (v.config_match as string) || null,
            config_change: (v.config_change as string) || null,
          });
        }}
      />
    </div>
  );
}
