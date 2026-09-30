import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  aiApi,
  type LlmModelIn,
  type LlmModelOut,
  type LlmModelTestOut,
} from "@/lib/aiApi";
import {
  CrudDrawer,
  type FieldDef,
  type FieldValues,
} from "@/components/admin/CrudDrawer";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { PlusIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import {
  MODELS_KEY,
  PROVIDERS_KEY,
  SETTINGS_KEY,
  errorText,
  formatPrice,
  invalidateCatalog,
  numberOrNull,
} from "./shared";

const DATALIST_ID = "admin-ai-model-remote-list";

type RemoteState = {
  providerId: number;
  loading: boolean;
  error: string | null;
  models: string[];
};

function toFormValues(row: LlmModelOut | null, providerIds: number[]): FieldValues {
  return row
    ? {
        provider_id: row.provider_id,
        model_id: row.model_id,
        display_name: row.display_name ?? "",
        supports_tools: row.supports_tools,
        supports_vision: row.supports_vision,
        active: row.valid_id === 1,
        context_tokens: row.context_tokens ?? "",
        max_tool_rounds: row.max_tool_rounds ?? "",
        price_input_per_1m: row.price_input_per_1m ?? "",
        price_output_per_1m: row.price_output_per_1m ?? "",
      }
    : {
        // One provider is the common setup: preselect it.
        provider_id: providerIds.length === 1 ? providerIds[0] : "",
        model_id: "",
        display_name: "",
        supports_tools: true,
        supports_vision: false,
        active: true,
        context_tokens: "",
        max_tool_rounds: "",
        price_input_per_1m: "",
        price_output_per_1m: "",
      };
}

export function ModelsTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { confirm, dialog: confirmDialog } = useConfirm();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<LlmModelOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<number, LlmModelTestOut>>({});
  const [testingId, setTestingId] = useState<number | null>(null);
  const [remote, setRemote] = useState<RemoteState | null>(null);

  const modelsQ = useQuery({
    queryKey: MODELS_KEY,
    queryFn: ({ signal }) => aiApi.listModels(signal),
  });
  const providersQ = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: ({ signal }) => aiApi.listProviders(signal),
  });
  // Only for the tool-rounds placeholder (the built-in default).
  const settingsQ = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: ({ signal }) => aiApi.getSettings(signal),
  });
  const providers = providersQ.data?.items ?? [];

  const saveM = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: LlmModelIn }) =>
      id == null ? aiApi.createModel(body) : aiApi.updateModel(id, body),
    onSuccess: async () => {
      setDrawerOpen(false);
      await invalidateCatalog(qc);
    },
  });

  const deleteM = useMutation({
    mutationFn: (id: number) => aiApi.deleteModel(id),
    onMutate: () => setActionError(null),
    onSuccess: () => invalidateCatalog(qc),
    onError: (err) => setActionError(errorText(err, t("admin.form.genericError"))),
  });

  const testM = useMutation({
    mutationFn: (id: number) => aiApi.testModel(id),
    onMutate: (id) => setTestingId(id),
    onSuccess: (result, id) => setTestResults((r) => ({ ...r, [id]: result })),
    onError: (err, id) =>
      setTestResults((r) => ({
        ...r,
        [id]: {
          ok: false,
          model: null,
          tool_calling_ok: false,
          error: errorText(err, t("admin.form.genericError")),
        },
      })),
    onSettled: () => setTestingId(null),
  });

  const remoteM = useMutation({
    mutationFn: (providerId: number) => aiApi.listProviderRemoteModels(providerId),
    onMutate: (providerId) =>
      setRemote({ providerId, loading: true, error: null, models: [] }),
    onSuccess: (out, providerId) =>
      setRemote({ providerId, loading: false, error: null, models: out.models }),
    onError: (err, providerId) =>
      setRemote({
        providerId,
        loading: false,
        error: errorText(err, t("admin.form.genericError")),
        models: [],
      }),
  });

  const openCreate = () => {
    setEditing(null);
    setFormError(null);
    setDrawerOpen(true);
  };
  const openEdit = (row: LlmModelOut) => {
    setEditing(row);
    setFormError(null);
    setDrawerOpen(true);
  };

  const handleSubmit = async (values: FieldValues) => {
    setFormError(null);
    const body: LlmModelIn = {
      provider_id: Number(values.provider_id),
      model_id: String(values.model_id ?? "").trim(),
      display_name: String(values.display_name ?? "").trim() || null,
      supports_tools: Boolean(values.supports_tools),
      supports_vision: Boolean(values.supports_vision),
      valid_id: values.active ? 1 : 2,
      context_tokens: numberOrNull(values.context_tokens),
      max_tool_rounds: numberOrNull(values.max_tool_rounds),
      price_input_per_1m: numberOrNull(values.price_input_per_1m),
      price_output_per_1m: numberOrNull(values.price_output_per_1m),
    };
    try {
      await saveM.mutateAsync({ id: editing?.id ?? null, body });
    } catch (err) {
      // Drawer stays open (it only closes on success) with the server's
      // message; no rethrow — CrudDrawer calls onSubmit fire-and-forget.
      setFormError(errorText(err, t("admin.form.genericError")));
    }
  };

  const handleDelete = async (row: LlmModelOut) => {
    const ok = await confirm({
      title: t("admin.ai.models.title"),
      message: t("admin.ai.models.deleteConfirm", { name: row.label }),
      variant: "danger",
    });
    if (ok) deleteM.mutate(row.id);
  };

  const currencyOf = (providerId: unknown) =>
    providers.find((p) => p.id === Number(providerId))?.price_currency ?? null;

  const renderModelIdField = (
    value: unknown,
    onChange: (v: unknown) => void,
    values: FieldValues,
  ) => {
    const providerId = Number(values.provider_id) || null;
    const loaded = remote && remote.providerId === providerId ? remote : null;
    return (
      <div className="space-y-1">
        <div className="flex gap-2">
          <input
            id="admin-ai-model-form-model_id"
            data-testid="admin-ai-model-form-model_id"
            type="text"
            list={DATALIST_ID}
            autoComplete="off"
            value={typeof value === "string" ? value : ""}
            onChange={(e) => onChange(e.target.value)}
            className="min-w-0 flex-1 rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 font-mono text-sm text-ink placeholder:text-muted focus:border-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          />
          <Button
            size="sm"
            variant="secondary"
            data-testid="admin-ai-model-load-remote"
            disabled={providerId == null || Boolean(loaded?.loading)}
            onClick={() => providerId != null && remoteM.mutate(providerId)}
            className="shrink-0"
          >
            {loaded?.loading ? <Spinner className="h-3 w-3" /> : null}
            {t("admin.ai.models.loadRemote")}
          </Button>
        </div>
        <datalist id={DATALIST_ID} data-testid={DATALIST_ID}>
          {(loaded?.models ?? []).map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        {loaded && !loaded.loading && (
          <p
            className={cn("text-xs", loaded.error ? "text-danger" : "text-muted")}
            data-testid="admin-ai-model-remote-status"
          >
            {loaded.error
              ? `${t("admin.ai.models.loadRemoteFail")}: ${loaded.error}`
              : t("admin.ai.models.loadRemoteDone", { count: loaded.models.length })}
          </p>
        )}
      </div>
    );
  };

  const usedIn = editing?.used_in_profiles ?? [];

  const fields: FieldDef[] = [
    {
      name: "provider_id",
      label: t("admin.ai.models.provider"),
      type: "select",
      required: true,
      options: providers.map((p) => ({ value: p.id, label: p.name })),
    },
    {
      name: "model_id",
      label: t("admin.ai.models.modelId"),
      type: "custom",
      required: true,
      helpText: t("admin.ai.models.modelIdHelp"),
      render: renderModelIdField,
    },
    {
      name: "display_name",
      label: t("admin.ai.models.displayName"),
      type: "text",
      helpText: t("admin.ai.models.displayNameHelp"),
    },
    {
      name: "supports_tools",
      label: t("admin.ai.models.supportsTools"),
      type: "checkbox",
    },
    {
      name: "supports_vision",
      label: t("admin.ai.models.supportsVision"),
      type: "checkbox",
    },
    {
      name: "active",
      label: t("admin.ai.models.active"),
      type: "checkbox",
    },
    {
      name: "disable_warning",
      label: "",
      type: "custom",
      showIf: (v) => !v.active && usedIn.length > 0,
      render: () => (
        <p
          className="rounded-md border border-escalation/30 bg-escalation/10 px-3 py-2 text-xs text-ink"
          data-testid="admin-ai-model-disable-warning"
        >
          {t("admin.ai.models.disableWarning", {
            profiles: usedIn.map((n) => `„${n}“`).join(", "),
          })}
        </p>
      ),
    },
    {
      name: "price_input_per_1m",
      label: t("admin.ai.models.priceInput"),
      type: "number",
      helpText: (v) => {
        const cur = currencyOf(v.provider_id);
        return cur
          ? t("admin.ai.models.priceHelp", { currency: cur })
          : t("admin.ai.models.priceHelpNoCurrency");
      },
    },
    {
      name: "price_output_per_1m",
      label: t("admin.ai.models.priceOutput"),
      type: "number",
    },
    {
      name: "context_tokens",
      label: t("admin.ai.models.contextTokens"),
      type: "number",
      helpText: t("admin.ai.models.contextTokensHelp"),
    },
    {
      name: "max_tool_rounds",
      label: t("admin.ai.models.maxToolRounds"),
      type: "number",
      placeholder: settingsQ.data
        ? String(settingsQ.data.default_max_tool_rounds)
        : undefined,
      helpText: t("admin.ai.models.maxToolRoundsHelp"),
    },
  ];

  const models = modelsQ.data ?? [];

  const renderTestResult = (r: LlmModelOut) => {
    const result = testResults[r.id];
    if (testingId !== r.id && !result) return null;
    return (
      <tr className="border-t border-dashed border-hairline bg-surface-subtle/60">
        <td colSpan={6} className="px-3.5 py-2 text-xs">
          {testingId === r.id ? (
            <Spinner className="h-3 w-3" />
          ) : result ? (
            <span
              className={result.ok ? "text-green" : "text-danger"}
              data-testid={`admin-ai-model-test-result-${r.id}`}
            >
              {result.ok
                ? [
                    `${t("admin.ai.models.testOk")} (${result.model ?? "?"})`,
                    r.supports_tools
                      ? result.tool_calling_ok
                        ? t("admin.ai.models.testToolsOk")
                        : t("admin.ai.models.testToolsFail")
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")
                : `${t("admin.ai.models.testFail")}: ${result.error ?? ""}`}
            </span>
          ) : null}
        </td>
      </tr>
    );
  };

  const renderRow = (r: LlmModelOut) => {
    const price = formatPrice(r.price_input_per_1m, r.price_output_per_1m, r.price_currency);
    return (
      <Fragment key={r.id}>
        <tr
          data-testid={`admin-ai-model-row-${r.id}`}
          onClick={() => openEdit(r)}
          className="cursor-pointer border-t border-hairline transition-colors first:border-t-0 hover:bg-surface-subtle"
        >
          <td className="px-3.5 py-2.5">
            <div className="flex min-w-0 items-center gap-2">
              <span
                className={cn(
                  "h-1.5 w-1.5 shrink-0 rounded-full",
                  r.valid_id === 1 ? "bg-green" : "bg-muted",
                )}
                title={r.valid_id === 1 ? t("admin.table.valid") : t("admin.ai.models.inactive")}
              />
              <span className="truncate font-medium text-ink">{r.label}</span>
            </div>
          </td>
          <td className="px-3.5 py-2.5 text-muted">{r.provider_name}</td>
          <td className="px-3.5 py-2.5">
            <span
              className="break-all font-mono text-xs text-ink"
              data-testid={`admin-ai-model-id-${r.id}`}
            >
              {r.model_id}
            </span>
          </td>
          <td className="px-3.5 py-2.5">
            <div className="flex flex-wrap gap-1">
              {r.supports_tools && <Badge tone="accent">{t("admin.ai.models.cap.tools")}</Badge>}
              {r.supports_vision && <Badge tone="accent">{t("admin.ai.models.cap.vision")}</Badge>}
              {r.valid_id !== 1 && <Badge tone="muted">{t("admin.ai.models.inactive")}</Badge>}
            </div>
          </td>
          <td className="whitespace-nowrap px-3.5 py-2.5 font-mono text-[11px] text-muted">
            {price ?? "—"}
          </td>
          <td
            className="px-2 py-2.5 text-right"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <Menu
              panelTestId={`admin-ai-model-menu-${r.id}`}
              trigger={({ ref, toggleProps }) => (
                <button
                  type="button"
                  ref={ref}
                  {...toggleProps}
                  data-testid={`admin-ai-model-menu-trigger-${r.id}`}
                  title={t("admin.table.actions")}
                  className="rounded-md px-1.5 py-1 text-sm leading-none text-muted transition-colors hover:bg-surface-subtle hover:text-ink"
                >
                  ⋯
                </button>
              )}
            >
              <MenuItem testId={`admin-ai-model-test-${r.id}`} onSelect={() => testM.mutate(r.id)}>
                {t("admin.ai.models.testAction")}
              </MenuItem>
              <MenuItem testId={`admin-ai-model-edit-${r.id}`} onSelect={() => openEdit(r)}>
                {t("admin.table.edit")}
              </MenuItem>
              <MenuSeparator />
              <MenuItem
                danger
                testId={`admin-ai-model-delete-${r.id}`}
                onSelect={() => void handleDelete(r)}
              >
                {t("admin.table.delete")}
              </MenuItem>
            </Menu>
          </td>
        </tr>
        {renderTestResult(r)}
      </Fragment>
    );
  };

  return (
    <div className="space-y-3" data-testid="admin-ai-models-tab">
      <div className="flex items-start justify-between gap-3">
        <p className="max-w-3xl text-xs text-muted">{t("admin.ai.models.intro")}</p>
        <Button
          variant="primary"
          size="sm"
          data-testid="admin-ai-models-new"
          onClick={openCreate}
          disabled={providers.length === 0}
          className="shrink-0"
        >
          <PlusIcon className="text-[16px]" />
          {t("admin.ai.models.new")}
        </Button>
      </div>
      {actionError && (
        <p className="text-sm text-danger" data-testid="admin-ai-models-error">
          {actionError}
        </p>
      )}
      <div className="overflow-x-auto rounded-xl border border-hairline bg-surface">
        {modelsQ.isLoading ? (
          <div className="flex justify-center p-6">
            <Spinner />
          </div>
        ) : models.length === 0 ? (
          <p className="p-4 text-sm text-muted">{t("admin.ai.models.empty")}</p>
        ) : (
          <table className="w-full text-sm" data-testid="admin-ai-models-table">
            <thead>
              <tr className="border-b border-hairline bg-surface-subtle text-left text-[11px] uppercase tracking-wide text-muted">
                <th className="px-3.5 py-2 font-semibold">{t("admin.ai.models.colName")}</th>
                <th className="px-3.5 py-2 font-semibold">{t("admin.ai.models.colProvider")}</th>
                <th className="px-3.5 py-2 font-semibold">{t("admin.ai.models.colModelId")}</th>
                <th className="px-3.5 py-2 font-semibold">{t("admin.ai.models.colCaps")}</th>
                <th className="whitespace-nowrap px-3.5 py-2 font-semibold">
                  {t("admin.ai.models.colPrice")}
                </th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>{models.map(renderRow)}</tbody>
          </table>
        )}
      </div>

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={
          editing
            ? t("admin.form.editTitle", { title: editing.label })
            : t("admin.ai.models.new")
        }
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={toFormValues(
          editing,
          providers.map((p) => p.id),
        )}
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-ai-model-form"
      />
      {confirmDialog}
    </div>
  );
}
