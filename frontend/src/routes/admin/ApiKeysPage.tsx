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
import { Badge } from "@/components/ui/Badge";
import { PlusIcon, ChevronDownIcon } from "@/components/ui/icons";
import { Dialog } from "@/components/ui/Dialog";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { SelectMenu, type SelectMenuItem } from "@/components/ui/SelectMenu";
import { formatDateTime, formatDateOnly } from "@/lib/format";
import { cn } from "@/lib/cn";
import {
  type ExpiryPreset,
  presetToExpiresAt,
  dateToExpiresAt,
  tomorrowDateStr,
  isExpired,
} from "@/lib/apiKeyExpiry";

const PRESETS: { key: ExpiryPreset; labelKey: string }[] = [
  { key: "unlimited", labelKey: "expiryPresetUnlimited" },
  { key: "30", labelKey: "expiryPreset30" },
  { key: "90", labelKey: "expiryPreset90" },
  { key: "180", labelKey: "expiryPreset180" },
  { key: "365", labelKey: "expiryPreset365" },
  { key: "custom", labelKey: "expiryPresetCustom" },
];

/**
 * Segmented preset row + native date fallback for `expires_at`. Internal
 * preset/date state is derived once from `value` on mount — the field
 * remounts fresh each time the drawer (Dialog) opens, so there is no need to
 * resync on prop changes.
 */
function ExpiryField({
  value,
  onChange,
  locale,
}: {
  value: string | null;
  onChange: (v: string | null) => void;
  locale: string;
}) {
  const { t } = useTranslation();
  const [preset, setPreset] = useState<ExpiryPreset>(value ? "custom" : "unlimited");
  const [customDate, setCustomDate] = useState<string>(
    value ? new Date(value).toISOString().slice(0, 10) : "",
  );

  const choosePreset = (p: ExpiryPreset) => {
    setPreset(p);
    if (p === "unlimited") onChange(null);
    else if (p === "custom") onChange(customDate ? dateToExpiresAt(customDate) : null);
    else onChange(presetToExpiresAt(p));
  };

  const chooseDate = (d: string) => {
    setCustomDate(d);
    onChange(d ? dateToExpiresAt(d) : null);
  };

  return (
    <div className="space-y-2">
      <div
        className="flex flex-wrap gap-1.5"
        role="group"
        data-testid="admin-api-keys-form-expiry-presets"
      >
        {PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            data-testid={`admin-api-keys-form-expiry-preset-${p.key}`}
            aria-pressed={preset === p.key}
            onClick={() => choosePreset(p.key)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors duration-100",
              preset === p.key
                ? "border-accent bg-accent-dim text-accent"
                : "border-hairline bg-surface-subtle text-muted hover:text-ink",
            )}
          >
            {t(`admin.apiKeys.${p.labelKey}`)}
          </button>
        ))}
      </div>
      {preset === "custom" && (
        <input
          type="date"
          data-testid="admin-api-keys-form-expiry-date"
          value={customDate}
          min={tomorrowDateStr()}
          onChange={(e) => chooseDate(e.target.value)}
          className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        />
      )}
      <p className="text-xs text-muted" data-testid="admin-api-keys-form-expiry-preview">
        {value
          ? t("admin.apiKeys.expiryPreviewLabel", { date: formatDateOnly(value, locale) })
          : t("admin.apiKeys.expiryPreviewUnlimited")}
      </p>
    </div>
  );
}

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
  const { confirm, dialog: confirmDialog } = useConfirm();

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

  const userItems: SelectMenuItem<number>[] = useMemo(
    () =>
      (usersQ.data?.items ?? []).map((u) => ({
        value: u.id,
        label: [u.first_name, u.last_name].filter(Boolean).join(" ") || u.login,
        hint: u.login,
      })),
    [usersQ.data],
  );

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
    const expires_at = typeof values.expires_at === "string" && values.expires_at ? values.expires_at : null;
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
      if (!(err instanceof Error && err.message === "user required")) {
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
      render: (r) =>
        r.expires_at ? (
          <span className="inline-flex items-center gap-1.5">
            <span className={isExpired(r.expires_at) ? "text-danger" : undefined}>
              {formatDateOnly(r.expires_at, locale)}
            </span>
            {isExpired(r.expires_at) && (
              <Badge tone="danger" data-testid={`admin-api-keys-expired-${r.id}`}>
                {t("admin.apiKeys.expired")}
              </Badge>
            )}
          </span>
        ) : (
          <span className="text-muted">{t("admin.apiKeys.unbounded")}</span>
        ),
    },
  ];

  const fields: FieldDef[] = editing
    ? [
        {
          name: "name",
          label: t("admin.apiKeys.name"),
          type: "text",
          required: true,
          help: {
            title: t("admin.apiKeys.name"),
            description: t("admin.help.apiKeys.name"),
          },
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
          type: "custom",
          help: {
            title: t("admin.apiKeys.expires"),
            description: t("admin.help.apiKeys.expires"),
          },
          render: (value, onChange) => (
            <ExpiryField
              value={typeof value === "string" ? value : null}
              onChange={onChange}
              locale={locale}
            />
          ),
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
          help: {
            title: t("admin.apiKeys.name"),
            description: t("admin.help.apiKeys.name"),
          },
        },
        {
          name: "user_id",
          label: t("admin.apiKeys.user"),
          type: "custom",
          required: true,
          render: (value, onChange) => (
            <SelectMenu
              items={userItems}
              value={typeof value === "number" ? value : undefined}
              onSelect={onChange}
              loading={usersQ.isLoading}
              placeholder={t("admin.form.selectPlaceholder")}
              panelTestId="admin-api-keys-form-user-panel"
              trigger={({ open, ref, toggleProps }) => (
                <button
                  ref={ref}
                  type="button"
                  data-testid="admin-api-keys-form-user_id"
                  {...toggleProps}
                  className="flex w-full items-center justify-between gap-2 rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-left text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                >
                  <span className="min-w-0 flex-1 truncate">
                    {userItems.find((i) => i.value === value)?.label ??
                      t("admin.form.selectPlaceholder")}
                  </span>
                  <ChevronDownIcon
                    className={cn(
                      "shrink-0 text-muted transition-transform duration-150",
                      open && "rotate-180",
                    )}
                  />
                </button>
              )}
            />
          ),
        },
        {
          name: "expires_at",
          label: t("admin.apiKeys.expires"),
          type: "custom",
          help: {
            title: t("admin.apiKeys.expires"),
            description: t("admin.help.apiKeys.expires"),
          },
          render: (value, onChange) => (
            <ExpiryField
              value={typeof value === "string" ? value : null}
              onChange={onChange}
              locale={locale}
            />
          ),
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
        <SelectMenu
          items={[
            { value: "valid" as AdminValidFilter, label: t("admin.filter.valid") },
            { value: "invalid" as AdminValidFilter, label: t("admin.filter.invalid") },
            { value: "all" as AdminValidFilter, label: t("admin.filter.all") },
          ]}
          value={validFilter}
          onSelect={setValidFilter}
          panelTestId="admin-api-keys-filter-panel"
          trigger={({ open, ref, toggleProps }) => (
            <button
              ref={ref}
              type="button"
              data-testid="admin-api-keys-filter"
              {...toggleProps}
              className="flex min-w-[10rem] items-center justify-between gap-2 rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
            >
              <span>
                {validFilter === "valid"
                  ? t("admin.filter.valid")
                  : validFilter === "invalid"
                    ? t("admin.filter.invalid")
                    : t("admin.filter.all")}
              </span>
              <ChevronDownIcon
                className={cn("text-muted transition-transform duration-150", open && "rotate-180")}
              />
            </button>
          )}
        />
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
        onDeactivate={async (row) => {
          if (!row.valid) return;
          const ok = await confirm({
            title: t("common.confirm"),
            message: t("admin.apiKeys.revokeConfirm", { name: row.name }),
          });
          if (ok) revokeM.mutate(row.id);
        }}
        onDelete={async (row) => {
          const ok = await confirm({
            title: t("admin.apiKeys.delete"),
            message: t("admin.apiKeys.deleteConfirm", { name: row.name }),
            variant: "danger",
          });
          if (ok) deleteM.mutate(row.id);
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
                expires_at: editing.expires_at ?? null,
                valid: editing.valid,
              }
            : {
                name: "",
                user_id: userItems[0]?.value ?? "",
                expires_at: null,
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

      {confirmDialog}
    </div>
  );
}
