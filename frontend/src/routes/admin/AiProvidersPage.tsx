import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import {
  aiApi,
  type LlmProviderCreate,
  type LlmProviderOut,
  type LlmProviderTestOut,
  type LlmProviderUpdate,
  type ProviderKind,
} from "@/lib/aiApi";
import {
  CrudDrawer,
  type FieldDef,
  type FieldValues,
} from "@/components/admin/CrudDrawer";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { PlusIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import {
  MODELS_KEY,
  PROVIDERS_KEY,
  numberOrNull,
} from "@/components/admin/ai-models/shared";

/** Only OpenAI-compatible providers can be created; a legacy Anthropic row
 * keeps its kind (the select offers it only while editing that row). */
const CREATABLE_KIND: ProviderKind = "openai_compat";

function toFormValues(row: LlmProviderOut | null): FieldValues {
  return row
    ? {
        name: row.name,
        kind: row.kind,
        base_url: row.base_url,
        api_key: "",
        eu_hosted: row.eu_hosted,
        price_currency: row.price_currency ?? "",
        budget_cost_day: row.budget_cost_day ?? "",
        budget_cost_week: row.budget_cost_week ?? "",
        budget_cost_month: row.budget_cost_month ?? "",
      }
    : {
        name: "",
        kind: CREATABLE_KIND,
        base_url: "",
        api_key: "",
        eu_hosted: false,
        price_currency: "",
        budget_cost_day: "",
        budget_cost_week: "",
        budget_cost_month: "",
      };
}

export function AiProvidersPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<LlmProviderOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<
    Record<number, LlmProviderTestOut>
  >({});
  const [testingId, setTestingId] = useState<number | null>(null);
  const [duplicatingId, setDuplicatingId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: ({ signal }) => aiApi.listProviders(signal),
  });

  // Models live on their own page; here only "N Modelle" per provider.
  const modelsQ = useQuery({
    queryKey: MODELS_KEY,
    queryFn: ({ signal }) => aiApi.listModels(signal),
  });
  const modelCount = useMemo(() => {
    const counts = new Map<number, number>();
    for (const m of modelsQ.data ?? []) {
      counts.set(m.provider_id, (counts.get(m.provider_id) ?? 0) + 1);
    }
    return counts;
  }, [modelsQ.data]);

  const invalidate = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: PROVIDERS_KEY }),
      // Deleting a provider deletes its models; names show on the models page.
      qc.invalidateQueries({ queryKey: MODELS_KEY }),
    ]);

  const createM = useMutation({
    mutationFn: (body: LlmProviderCreate) => aiApi.createProvider(body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const updateM = useMutation({
    mutationFn: ({ id, body }: { id: number; body: LlmProviderUpdate }) =>
      aiApi.updateProvider(id, body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidate();
    },
  });

  const deleteM = useMutation({
    mutationFn: (id: number) => aiApi.deleteProvider(id),
    onMutate: () => setActionError(null),
    onSuccess: () => invalidate(),
    // 409 when its models are still in profiles — the detail names them.
    onError: (err) =>
      setActionError(err instanceof ApiError ? err.message : String(err)),
  });

  const testM = useMutation({
    mutationFn: (id: number) => aiApi.testProvider(id),
    onMutate: (id) => setTestingId(id),
    onSuccess: (result, id) => setTestResults((r) => ({ ...r, [id]: result })),
    onError: (err, id) =>
      setTestResults((r) => ({
        ...r,
        [id]: {
          ok: false,
          detail: err instanceof ApiError ? err.message : String(err),
          model_count: null,
        },
      })),
    onSettled: () => setTestingId(null),
  });

  const duplicateM = useMutation({
    mutationFn: (id: number) => aiApi.duplicateProvider(id),
    onMutate: (id) => {
      setActionError(null);
      setDuplicatingId(id);
    },
    onSuccess: async (copy) => {
      await invalidate();
      // Open the copy's edit dialog directly so the operator can rename it
      // and change what differs (typically the key or the budget).
      openEdit(copy);
    },
    onError: (err) =>
      setActionError(err instanceof ApiError ? err.message : String(err)),
    onSettled: () => setDuplicatingId(null),
  });

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };
  const openEdit = (row: LlmProviderOut) => {
    setEditing(row);
    setFormError(null);
    setDrawerOpen(true);
  };

  const handleSubmit = async (values: FieldValues) => {
    setFormError(null);
    const kind = values.kind as ProviderKind;
    const base = {
      name: String(values.name ?? ""),
      base_url: String(values.base_url ?? ""),
      eu_hosted: Boolean(values.eu_hosted),
      price_currency:
        String(values.price_currency ?? "")
          .trim()
          .toUpperCase() || null,
      budget_cost_day: numberOrNull(values.budget_cost_day),
      budget_cost_week: numberOrNull(values.budget_cost_week),
      budget_cost_month: numberOrNull(values.budget_cost_month),
    };
    const apiKey =
      typeof values.api_key === "string" ? values.api_key.trim() : "";
    try {
      if (editing) {
        const body: LlmProviderUpdate = { ...base };
        // Sending an unchanged legacy "anthropic" kind would be rejected;
        // the backend keeps the stored kind when the field is omitted.
        if (kind !== editing.kind) body.kind = kind;
        if (apiKey) body.api_key = apiKey;
        await updateM.mutateAsync({ id: editing.id, body });
      } else {
        const body: LlmProviderCreate = {
          ...base,
          kind: CREATABLE_KIND,
          api_key: apiKey || null,
        };
        await createM.mutateAsync(body);
      }
    } catch (err) {
      setFormError(
        err instanceof ApiError ? err.message : t("admin.form.genericError"),
      );
      throw err;
    }
  };

  const handleDelete = async (row: LlmProviderOut) => {
    const ok = await confirm({
      title: t("admin.ai.providers.title"),
      message: t("admin.ai.providers.deleteConfirmWithModels", {
        name: row.name,
      }),
      variant: "danger",
    });
    if (ok) deleteM.mutate(row.id);
  };

  // Two-line row instead of a wide table: name/kind + status dot on top,
  // "N Modelle" (link to the models page) + currency below; chips right;
  // every action in the ⋯-menu (row click = edit).
  const renderRow = (r: LlmProviderOut) => {
    const result = testResults[r.id];
    return (
      <div key={r.id} className="border-t border-hairline first:border-t-0">
        <div
          role="button"
          tabIndex={0}
          data-testid={`admin-ai-provider-row-${r.id}`}
          onClick={() => openEdit(r)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              openEdit(r);
            }
          }}
          className="grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 px-3.5 py-2.5 transition-colors hover:bg-surface-subtle md:grid-cols-[minmax(0,1fr)_auto_auto]"
        >
          <div className="flex min-w-0 items-center gap-2.5">
            <span
              className={cn(
                "h-1.5 w-1.5 shrink-0 rounded-full",
                r.valid_id === 1 ? "bg-green" : "bg-muted",
              )}
              title={
                r.valid_id === 1
                  ? t("admin.table.valid")
                  : t("admin.table.invalid")
              }
            />
            <span className="truncate text-sm font-medium text-ink">
              {r.name}
            </span>
            <span className="shrink-0 text-xs text-muted">
              {t(`admin.ai.providers.kindLabel.${r.kind}`)}
            </span>
          </div>
          <div className="col-start-1 row-start-2 flex min-w-0 items-baseline gap-3 pl-4">
            <Link
              to="/admin/ai/models"
              onClick={(e) => e.stopPropagation()}
              className="text-xs text-accent hover:underline"
              data-testid={`admin-ai-provider-models-${r.id}`}
            >
              {t("admin.ai.providers.modelCount", {
                count: modelCount.get(r.id) ?? 0,
              })}
            </Link>
            {r.price_currency && (
              <span
                className="shrink-0 font-mono text-[11px] text-muted"
                title={t("admin.ai.providers.priceCurrency")}
              >
                {r.price_currency}
              </span>
            )}
          </div>
          <div className="row-span-2 hidden flex-wrap items-center justify-end gap-1 md:flex">
            {r.eu_hosted && (
              <Badge tone="success">{t("admin.ai.providers.flagEu")}</Badge>
            )}
            {r.has_api_key && (
              <Badge tone="muted">{t("admin.ai.providers.hasKey")}</Badge>
            )}
          </div>
          <div
            className="col-start-2 row-span-2 md:col-start-3"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <Menu
              panelTestId={`admin-ai-provider-menu-${r.id}`}
              trigger={({ ref, toggleProps }) => (
                <button
                  type="button"
                  ref={ref}
                  {...toggleProps}
                  data-testid={`admin-ai-provider-menu-trigger-${r.id}`}
                  title={t("admin.table.actions")}
                  className="rounded-md px-1.5 py-1 text-sm leading-none text-muted transition-colors hover:bg-surface-subtle hover:text-ink"
                >
                  ⋯
                </button>
              )}
            >
              <MenuItem
                testId={`admin-ai-provider-edit-${r.id}`}
                onSelect={() => openEdit(r)}
              >
                {t("admin.table.edit")}
              </MenuItem>
              <MenuItem
                testId={`admin-ai-provider-test-${r.id}`}
                onSelect={() => testM.mutate(r.id)}
              >
                {t("admin.ai.providers.testAction")}
              </MenuItem>
              <MenuItem
                testId={`admin-ai-provider-duplicate-${r.id}`}
                onSelect={() => duplicateM.mutate(r.id)}
              >
                {t("admin.ai.providers.duplicateAction")}
              </MenuItem>
              <MenuSeparator />
              <MenuItem
                danger
                testId={`admin-row-delete-${r.id}`}
                onSelect={() => void handleDelete(r)}
              >
                {t("admin.table.delete")}
              </MenuItem>
            </Menu>
          </div>
        </div>
        {(testingId === r.id || duplicatingId === r.id || result) && (
          <div className="flex items-baseline gap-2 border-t border-dashed border-hairline bg-surface-subtle/60 px-3.5 py-2 pl-8 text-xs">
            {testingId === r.id || duplicatingId === r.id ? (
              <Spinner className="h-3 w-3 self-center" />
            ) : result ? (
              <span
                className={result.ok ? "text-green" : "text-danger"}
                data-testid={`admin-ai-provider-test-result-${r.id}`}
              >
                {result.ok
                  ? t("admin.ai.providers.testOkModels", {
                      count: result.model_count ?? 0,
                    })
                  : `${t("admin.ai.providers.testFail")}: ${result.detail ?? ""}`}
              </span>
            ) : null}
          </div>
        )}
      </div>
    );
  };

  const fields: FieldDef[] = [
    {
      name: "name",
      label: t("admin.ai.providers.name"),
      type: "text",
      required: true,
    },
    {
      name: "kind",
      label: t("admin.ai.providers.kind"),
      type: "select",
      required: true,
      options: [
        CREATABLE_KIND,
        ...(editing && editing.kind !== CREATABLE_KIND ? [editing.kind] : []),
      ].map((k) => ({
        value: k,
        label: t(`admin.ai.providers.kindLabel.${k}`),
      })),
      helpText: (v) =>
        v.kind !== CREATABLE_KIND
          ? t("admin.ai.providers.kindLegacyHelp")
          : undefined,
    },
    {
      name: "base_url",
      label: t("admin.ai.providers.baseUrl"),
      type: "text",
      required: true,
    },
    {
      name: "api_key",
      label: t("admin.ai.providers.apiKey"),
      type: "password",
      helpText: editing?.has_api_key
        ? t("admin.ai.providers.apiKeySetHelp")
        : t("admin.ai.providers.apiKeyHelp"),
    },
    {
      name: "price_currency",
      label: t("admin.ai.providers.priceCurrency"),
      helpText: t("admin.ai.providers.priceCurrencyHelp"),
      type: "select",
      options: [
        { value: "", label: t("admin.ai.providers.priceCurrencyNone") },
        { value: "EUR", label: "EUR" },
        { value: "USD", label: "USD" },
      ],
    },
    {
      name: "budget_cost_day",
      label: t("admin.ai.providers.budgetCostDay"),
      type: "number",
      helpText: t("admin.ai.providers.budgetCostHelp"),
    },
    {
      name: "budget_cost_week",
      label: t("admin.ai.providers.budgetCostWeek"),
      type: "number",
    },
    {
      name: "budget_cost_month",
      label: t("admin.ai.providers.budgetCostMonth"),
      type: "number",
    },
    {
      name: "eu_hosted",
      label: t("admin.ai.providers.flagEu"),
      type: "checkbox",
    },
  ];

  return (
    <div className="space-y-3 p-4" data-testid="admin-ai-providers-page">
      <div className="flex items-center justify-between gap-3">
        <h1 className="font-display text-xl font-semibold text-ink">
          {t("admin.ai.providers.title")}
        </h1>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-ai-providers-new"
          onClick={openCreate}
          aria-label={t("admin.ai.providers.new")}
          title={t("admin.ai.providers.new")}
          className="!px-2"
        >
          <PlusIcon className="text-[16px]" />
        </Button>
      </div>
      <p className="text-xs text-muted">
        {t("admin.ai.providers.description")}
      </p>
      {actionError && (
        <p
          className="text-sm text-danger"
          data-testid="admin-ai-providers-action-error"
        >
          {actionError}
        </p>
      )}

      <div
        className="rounded-xl border border-hairline bg-surface"
        data-testid="admin-ai-providers-table"
      >
        {listQ.isLoading ? (
          <div className="flex justify-center p-6">
            <Spinner />
          </div>
        ) : (listQ.data?.items.length ?? 0) === 0 ? (
          <p className="p-4 text-sm text-muted">{t("admin.table.empty")}</p>
        ) : (
          listQ.data?.items.map(renderRow)
        )}
      </div>

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={
          editing
            ? t("admin.form.editTitle", {
                title: t("admin.ai.providers.title"),
              })
            : t("admin.ai.providers.new")
        }
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={toFormValues(editing)}
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-ai-provider-form"
      />

      {confirmDialog}
    </div>
  );
}
