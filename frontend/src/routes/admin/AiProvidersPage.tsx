import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { CrudDrawer, type FieldDef, type FieldValues } from "@/components/admin/CrudDrawer";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { PlusIcon } from "@/components/ui/icons";

const QUERY_KEY = ["admin", "ai", "providers"] as const;
const PROVIDER_KINDS: ProviderKind[] = ["openai_compat", "anthropic"];

function toFormValues(row: LlmProviderOut | null): FieldValues {
  return row
    ? {
        name: row.name,
        kind: row.kind,
        base_url: row.base_url,
        default_model: row.default_model,
        api_key: "",
        supports_tools: row.supports_tools,
        supports_streaming: row.supports_streaming,
        eu_hosted: row.eu_hosted,
      }
    : {
        name: "",
        kind: "openai_compat",
        base_url: "",
        default_model: "",
        api_key: "",
        supports_tools: true,
        supports_streaming: true,
        eu_hosted: false,
      };
}

export function AiProvidersPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<LlmProviderOut | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<number, LlmProviderTestOut>>({});
  const [testingId, setTestingId] = useState<number | null>(null);

  const listQ = useQuery({
    queryKey: QUERY_KEY,
    queryFn: ({ signal }) => aiApi.listProviders(signal),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: QUERY_KEY });

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
    onSuccess: () => invalidate(),
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
          model: null,
          tool_calling_ok: false,
          error: err instanceof ApiError ? err.message : String(err),
        },
      })),
    onSettled: () => setTestingId(null),
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
    const base: LlmProviderCreate = {
      name: String(values.name ?? ""),
      kind: values.kind as ProviderKind,
      base_url: String(values.base_url ?? ""),
      default_model: String(values.default_model ?? ""),
      supports_tools: Boolean(values.supports_tools),
      supports_streaming: Boolean(values.supports_streaming),
      eu_hosted: Boolean(values.eu_hosted),
    };
    const apiKey = typeof values.api_key === "string" ? values.api_key.trim() : "";
    try {
      if (editing) {
        const body: LlmProviderUpdate = { ...base };
        if (apiKey) body.api_key = apiKey;
        await updateM.mutateAsync({ id: editing.id, body });
      } else {
        await createM.mutateAsync({ ...base, api_key: apiKey || null });
      }
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("admin.form.genericError"));
      throw err;
    }
  };

  const columns: DataTableColumn<LlmProviderOut>[] = [
    { key: "name", header: t("admin.ai.providers.name"), render: (r) => r.name },
    {
      key: "kind",
      header: t("admin.ai.providers.kind"),
      render: (r) => t(`admin.ai.providers.kindLabel.${r.kind}`),
    },
    {
      key: "base_url",
      header: t("admin.ai.providers.baseUrl"),
      mono: true,
      render: (r) => r.base_url,
    },
    {
      key: "default_model",
      header: t("admin.ai.providers.defaultModel"),
      mono: true,
      render: (r) => r.default_model,
    },
    {
      key: "flags",
      header: t("admin.ai.providers.flags"),
      render: (r) => (
        <div className="flex flex-wrap gap-1">
          {r.supports_tools && <Badge tone="accent">{t("admin.ai.providers.flagTools")}</Badge>}
          {r.eu_hosted && <Badge tone="success">{t("admin.ai.providers.flagEu")}</Badge>}
          {r.has_api_key && <Badge tone="muted">{t("admin.ai.providers.hasKey")}</Badge>}
        </div>
      ),
    },
    {
      key: "test",
      header: t("admin.ai.providers.test"),
      render: (r) => {
        const result = testResults[r.id];
        return (
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={testingId === r.id}
              data-testid={`admin-ai-provider-test-${r.id}`}
              onClick={() => testM.mutate(r.id)}
            >
              {testingId === r.id ? <Spinner className="h-3 w-3" /> : t("admin.ai.providers.testAction")}
            </Button>
            {result && (
              <span
                className={`text-xs ${result.ok ? "text-green" : "text-danger"}`}
                data-testid={`admin-ai-provider-test-result-${r.id}`}
              >
                {result.ok
                  ? `${t("admin.ai.providers.testOk")} (${result.model ?? "?"})`
                  : `${t("admin.ai.providers.testFail")}: ${result.error ?? ""}`}
              </span>
            )}
          </div>
        );
      },
    },
  ];

  const fields: FieldDef[] = [
    { name: "name", label: t("admin.ai.providers.name"), type: "text", required: true },
    {
      name: "kind",
      label: t("admin.ai.providers.kind"),
      type: "select",
      required: true,
      options: PROVIDER_KINDS.map((k) => ({ value: k, label: t(`admin.ai.providers.kindLabel.${k}`) })),
    },
    { name: "base_url", label: t("admin.ai.providers.baseUrl"), type: "text", required: true },
    {
      name: "default_model",
      label: t("admin.ai.providers.defaultModel"),
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
    { name: "supports_tools", label: t("admin.ai.providers.flagTools"), type: "checkbox" },
    { name: "supports_streaming", label: t("admin.ai.providers.flagStreaming"), type: "checkbox" },
    { name: "eu_hosted", label: t("admin.ai.providers.flagEu"), type: "checkbox" },
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
      <p className="text-xs text-muted">{t("admin.ai.providers.description")}</p>

      <DataTable
        columns={columns}
        rows={listQ.data?.items ?? []}
        rowKey={(r) => r.id}
        isLoading={listQ.isLoading}
        isRowValid={(r) => r.valid_id === 1}
        onEdit={openEdit}
        onDelete={(row) => {
          if (window.confirm(t("admin.ai.providers.deleteConfirm", { name: row.name }))) {
            deleteM.mutate(row.id);
          }
        }}
        testId="admin-ai-providers-table"
      />

      <CrudDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={
          editing
            ? t("admin.form.editTitle", { title: t("admin.ai.providers.title") })
            : t("admin.ai.providers.new")
        }
        fields={fields}
        mode={editing ? "edit" : "create"}
        initialValues={toFormValues(editing)}
        onSubmit={handleSubmit}
        submitError={formError}
        testIdPrefix="admin-ai-provider-form"
      />
    </div>
  );
}
