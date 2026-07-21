import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  api,
  ApiError,
  type ApiKeyOut,
  type ApiKeyCreate,
  type ApiKeyUpdate,
  type ApiKeyCreated,
  type AdminValidFilter,
} from "@/lib/api";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { CrudDrawer, type FieldDef, type FieldValues } from "@/components/admin/CrudDrawer";
import { Button } from "@/components/ui/Button";
import { PlusIcon } from "@/components/ui/icons";
import { Dialog } from "@/components/ui/Dialog";
import { formatDateTime } from "@/lib/format";

export function ApiKeysPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const queryClient = useQueryClient();

  const [validFilter, setValidFilter] = useState<AdminValidFilter>("valid");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<ApiKeyOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const keysQ = useQuery({
    queryKey: ["admin", "api-keys", validFilter],
    queryFn: ({ signal }) =>
      api.adminApiKeys.list({ valid: validFilter, pageSize: 500 }, signal),
  });

  const usersQ = useQuery({
    queryKey: ["admin", "users", "for-api-keys"],
    queryFn: ({ signal }) => api.adminUsers.list({ valid: "valid", pageSize: 500 }, signal),
    staleTime: 5 * 60 * 1000,
  });

  const userLabel = useMemo(() => {
    const map = new Map<number, string>();
    for (const u of usersQ.data?.items ?? []) {
      map.set(u.id, u.login ? `${u.login} (#${u.id})` : `#${u.id}`);
    }
    return (id: number) => map.get(id) ?? String(id);
  }, [usersQ.data]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin", "api-keys"] });

  const createM = useMutation({
    mutationFn: (body: ApiKeyCreate) => api.adminApiKeys.create(body),
    onSuccess: async (created) => {
      setDrawerOpen(false);
      setCreatedKey(created);
      setCopied(false);
      await invalidate();
    },
  });

  const updateM = useMutation({
    mutationFn: ({ id, body }: { id: number; body: ApiKeyUpdate }) =>
      api.adminApiKeys.update(id, body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const revokeM = useMutation({
    mutationFn: (id: number) => api.adminApiKeys.update(id, { valid: false }),
    onSuccess: () => invalidate(),
  });

  const deleteM = useMutation({
    mutationFn: (id: number) => api.adminApiKeys.remove(id),
    onSuccess: () => invalidate(),
  });

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };

  const openEdit = (row: ApiKeyOut) => {
    setEditing(row);
    setFormError(null);
    setDrawerOpen(true);
  };

  const handleSubmit = async (values: FieldValues) => {
    setFormError(null);
    const name = String(values.name ?? "").trim();
    const expiresRaw = String(values.expires_at ?? "").trim();
    let expires_at: string | null = null;
    if (expiresRaw) {
      const d = new Date(expiresRaw);
      if (Number.isNaN(d.getTime())) {
        setFormError(t("admin.apiKeys.expiresInvalid"));
        throw new Error("invalid expires");
      }
      expires_at = d.toISOString();
    }
    try {
      if (editing) {
        await updateM.mutateAsync({
          id: editing.id,
          body: {
            name,
            expires_at,
            valid: Boolean(values.valid ?? editing.valid),
          },
        });
      } else {
        const user_id = Number(values.user_id);
        if (!Number.isFinite(user_id) || user_id <= 0) {
          setFormError(t("admin.apiKeys.userRequired"));
          throw new Error("user required");
        }
        await createM.mutateAsync({
          name,
          user_id,
          expires_at,
        });
      }
    } catch (err) {
      if (!(err instanceof Error && (err.message === "user required" || err.message === "invalid expires"))) {
        setFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      }
      throw err;
    }
  };

  const copyKey = async () => {
    if (!createdKey?.key) return;
    try {
      await navigator.clipboard.writeText(createdKey.key);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const columns: DataTableColumn<ApiKeyOut>[] = [
    {
      key: "name",
      header: t("admin.apiKeys.name"),
      render: (r) => r.name,
    },
    {
      key: "user",
      header: t("admin.apiKeys.user"),
      mono: true,
      render: (r) => userLabel(r.user_id),
    },
    {
      key: "created",
      header: t("admin.apiKeys.created"),
      render: (r) => formatDateTime(r.created, locale),
    },
    {
      key: "last_used_at",
      header: t("admin.apiKeys.lastUsed"),
      render: (r) => formatDateTime(r.last_used_at, locale),
    },
    {
      key: "expires_at",
      header: t("admin.apiKeys.expires"),
      render: (r) => formatDateTime(r.expires_at, locale),
    },
    {
      key: "delete",
      header: t("admin.apiKeys.delete"),
      render: (r) => (
        <Button
          variant="ghost"
          size="sm"
          data-testid={`admin-api-keys-delete-${r.id}`}
          onClick={(e) => {
            e.stopPropagation();
            if (window.confirm(t("admin.apiKeys.deleteConfirm", { name: r.name }))) {
              deleteM.mutate(r.id);
            }
          }}
        >
          {t("admin.apiKeys.delete")}
        </Button>
      ),
    },
  ];

  const userOptions = (usersQ.data?.items ?? []).map((u) => ({
    value: u.id,
    label: u.login ? `${u.login} (#${u.id})` : `#${u.id}`,
  }));

  const fields: FieldDef[] = editing
    ? [
        {
          name: "name",
          label: t("admin.apiKeys.name"),
          type: "text",
          required: true,
        },
        {
          name: "user_id",
          label: t("admin.apiKeys.user"),
          type: "custom",
          render: () => (
            <span className="text-sm text-ink" data-testid="admin-api-keys-user-ro">
              {userLabel(editing.user_id)}
            </span>
          ),
        },
        {
          name: "expires_at",
          label: t("admin.apiKeys.expires"),
          type: "text",
          placeholder: "2027-01-01T00:00:00Z",
          helpText: t("admin.apiKeys.expiresHelp"),
        },
        {
          name: "valid",
          label: t("admin.table.valid"),
          type: "checkbox",
        },
      ]
    : [
        {
          name: "name",
          label: t("admin.apiKeys.name"),
          type: "text",
          required: true,
        },
        {
          name: "user_id",
          label: t("admin.apiKeys.user"),
          type: "select",
          required: true,
          options: userOptions,
        },
        {
          name: "expires_at",
          label: t("admin.apiKeys.expires"),
          type: "text",
          placeholder: "2027-01-01T00:00:00Z",
          helpText: t("admin.apiKeys.expiresHelp"),
        },
      ];

  const rows = keysQ.data?.items ?? [];

  return (
    <div className="space-y-3 p-4" data-testid="admin-api-keys-page">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.apiKeys.title_plural")}
        </h1>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-api-keys-new"
          onClick={openCreate}
          aria-label={t("admin.apiKeys.new")}
          title={t("admin.apiKeys.new")}
          className="!px-2"
        >
          <PlusIcon className="text-[16px]" />
        </Button>
      </div>

      <label className="flex flex-wrap items-center gap-2 text-sm text-ink">
        <span className="text-xs font-medium uppercase tracking-wide text-muted">
          {t("admin.filter.label")}
        </span>
        <select
          data-testid="admin-api-keys-valid-filter"
          className="min-w-[10rem] rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          value={validFilter}
          onChange={(e) => setValidFilter(e.target.value as AdminValidFilter)}
        >
          <option value="valid">{t("admin.filter.valid")}</option>
          <option value="invalid">{t("admin.filter.invalid")}</option>
          <option value="all">{t("admin.filter.all")}</option>
        </select>
      </label>

      <p className="text-xs text-muted">{t("admin.apiKeys.hint")}</p>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        isLoading={keysQ.isLoading}
        emptyLabel={t("admin.apiKeys.empty")}
        isRowValid={(r) => r.valid}
        onEdit={openEdit}
        onDeactivate={(row) => {
          if (row.valid && window.confirm(t("admin.apiKeys.revokeConfirm", { name: row.name }))) {
            revokeM.mutate(row.id);
          }
        }}
        testId="admin-api-keys-table"
      />

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={
          editing
            ? t("admin.form.editTitle", { title: t("admin.apiKeys.title_plural") })
            : t("admin.apiKeys.new")
        }
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={
          editing
            ? {
                name: editing.name,
                user_id: editing.user_id,
                expires_at: editing.expires_at
                  ? new Date(editing.expires_at).toISOString()
                  : "",
                valid: editing.valid,
              }
            : {
                name: "",
                user_id: userOptions[0]?.value ?? "",
                expires_at: "",
              }
        }
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-api-keys-form"
      />

      <Dialog
        open={createdKey != null}
        onClose={() => setCreatedKey(null)}
        title={t("admin.apiKeys.createdTitle")}
      >
        <div className="space-y-3" data-testid="admin-api-keys-once-dialog">
          <p className="text-sm text-danger font-medium">{t("admin.apiKeys.onceWarning")}</p>
          <p className="text-xs text-muted">{t("admin.apiKeys.onceHint")}</p>
          <div className="flex flex-wrap items-center gap-2">
            <code
              className="block max-w-full flex-1 break-all rounded-md border border-hairline bg-surface-subtle px-2 py-1.5 font-mono text-[12.5px] text-ink"
              data-testid="admin-api-keys-plaintext"
            >
              {createdKey?.key}
            </code>
            <Button variant="secondary" size="sm" onClick={copyKey} data-testid="admin-api-keys-copy">
              {copied ? t("admin.apiKeys.copied") : t("admin.apiKeys.copy")}
            </Button>
          </div>
          <div className="flex justify-end">
            <Button variant="primary" size="sm" onClick={() => setCreatedKey(null)}>
              {t("common.close")}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
