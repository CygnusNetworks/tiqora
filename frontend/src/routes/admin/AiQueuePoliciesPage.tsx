import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { aiApi, type AclFeature, type AiQueuePolicyOut, type AiUsageOut } from "@/lib/aiApi";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { PickerField } from "@/components/admin/PickerField";
import { Tabs } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { Spinner } from "@/components/ui/Spinner";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { PlusIcon } from "@/components/ui/icons";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/cn";

const POLICIES_KEY = ["admin", "ai", "queue-policies"] as const;
const QUEUES_KEY = ["admin", "ai", "reference-queues"] as const;
const PROVIDERS_KEY = ["admin", "ai", "providers"] as const;
const USAGE_KEY = ["admin", "ai", "usage"] as const;

const NONE = 0;
const FEATURES: AclFeature[] = ["summary", "auto_reply", "manual_assist"];

type ListTab = "policies" | "usage";

export function AiQueuePoliciesPage() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language?.startsWith("de") ? "de" : "en";
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { confirm, dialog: confirmDialog } = useConfirm();

  const [listTab, setListTab] = useState<ListTab>("policies");
  const [usageQueueFilter, setUsageQueueFilter] = useState<number>(NONE);
  const [usageFeatureFilter, setUsageFeatureFilter] = useState<AclFeature | "">("");
  const [usagePage, setUsagePage] = useState(1);

  const policiesQ = useQuery({
    queryKey: POLICIES_KEY,
    queryFn: ({ signal }) => aiApi.listQueuePolicies(signal),
  });
  const queuesQ = useQuery({
    queryKey: QUEUES_KEY,
    queryFn: ({ signal }) => api.listReferenceQueues({}, signal),
  });
  const providersQ = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: ({ signal }) => aiApi.listProviders(signal),
  });

  const usageQ = useQuery({
    queryKey: [...USAGE_KEY, usageQueueFilter, usageFeatureFilter, usagePage],
    queryFn: ({ signal }) =>
      aiApi.listUsage(
        {
          queue_id: usageQueueFilter !== NONE ? usageQueueFilter : undefined,
          feature: usageFeatureFilter || undefined,
          page: usagePage,
          page_size: 25,
        },
        signal,
      ),
    enabled: listTab === "usage",
  });

  const queueNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const q of queuesQ.data ?? []) map.set(q.id, q.name);
    return map;
  }, [queuesQ.data]);

  const availableQueues = useMemo(() => {
    const used = new Set((policiesQ.data?.items ?? []).map((p) => p.queue_id));
    return (queuesQ.data ?? []).filter((q) => !used.has(q.id));
  }, [queuesQ.data, policiesQ.data]);

  const deleteM = useMutation({
    mutationFn: (id: number) => aiApi.deleteQueuePolicy(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: POLICIES_KEY }),
  });

  const providerName = (id: number | null) => {
    if (id == null) return "—";
    return providersQ.data?.items.find((p) => p.id === id)?.name ?? `#${id}`;
  };

  const goToEditor = (row: AiQueuePolicyOut) =>
    void navigate({ to: "/admin/ai/queues/$policyId", params: { policyId: String(row.id) } });

  const handleDelete = async (row: AiQueuePolicyOut) => {
    const ok = await confirm({
      title: t("admin.ai.queues.title"),
      message: t("admin.ai.queues.deleteConfirm"),
      variant: "danger",
    });
    if (ok) deleteM.mutate(row.id);
  };

  // Two-line row matching the provider list: status dot + queue + autonomy on
  // top, provider (mono) below, feature chips right, actions in the ⋯-menu.
  const renderPolicyRow = (r: AiQueuePolicyOut) => {
    const hasFeature = r.enabled_manual_assist || r.enabled_summary || r.enabled_auto_reply;
    return (
      <div key={r.id} className="border-t border-hairline first:border-t-0">
        <div
          role="button"
          tabIndex={0}
          data-testid={`admin-ai-queue-row-${r.id}`}
          onClick={() => goToEditor(r)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              goToEditor(r);
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
              title={r.valid_id === 1 ? t("admin.table.valid") : t("admin.table.invalid")}
            />
            <span className="truncate text-sm font-medium text-ink">
              {queueNameById.get(r.queue_id) ?? `#${r.queue_id}`}
            </span>
            <span className="shrink-0 text-xs text-muted">
              {t("admin.ai.queues.autonomy.label")}: {t(`admin.ai.queues.autonomy.${r.autonomy}`)}
            </span>
          </div>
          <div className="col-start-1 row-start-2 flex min-w-0 items-baseline gap-3 pl-4">
            <span className="truncate font-mono text-xs text-muted">
              {providerName(r.llm_provider_id)}
            </span>
          </div>
          <div className="row-span-2 hidden flex-wrap items-center justify-end gap-1 md:flex">
            {r.enabled_manual_assist && (
              <Badge tone="accent">{t("admin.ai.feature.manual_assist")}</Badge>
            )}
            {r.enabled_summary && <Badge tone="accent">{t("admin.ai.feature.summary")}</Badge>}
            {r.enabled_auto_reply && <Badge tone="warn">{t("admin.ai.feature.auto_reply")}</Badge>}
            {!hasFeature && <Badge tone="muted">{t("admin.ai.queues.noFeatures")}</Badge>}
          </div>
          <div
            className="col-start-2 row-span-2 md:col-start-3"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <Menu
              panelTestId={`admin-ai-queue-menu-${r.id}`}
              trigger={({ ref, toggleProps }) => (
                <button
                  type="button"
                  ref={ref}
                  {...toggleProps}
                  data-testid={`admin-ai-queue-menu-trigger-${r.id}`}
                  title={t("admin.table.actions")}
                  className="rounded-md px-1.5 py-1 text-sm leading-none text-muted transition-colors hover:bg-surface-subtle hover:text-ink"
                >
                  ⋯
                </button>
              )}
            >
              <MenuItem testId={`admin-ai-queue-edit-${r.id}`} onSelect={() => goToEditor(r)}>
                {t("admin.table.edit")}
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
      </div>
    );
  };

  const usageQueueItems = useMemo(
    () => [
      { value: NONE, label: t("admin.ai.usage.allQueues") },
      ...(queuesQ.data ?? []).map((q) => ({ value: q.id, label: q.name })),
    ],
    [queuesQ.data, t],
  );
  const usageFeatureItems = useMemo(
    () => [
      { value: "" as const, label: t("admin.ai.usage.allFeatures") },
      ...[...FEATURES, "mcp" as AclFeature].map((f) => ({ value: f, label: t(`admin.ai.feature.${f}`) })),
    ],
    [t],
  );

  const usageColumns: DataTableColumn<AiUsageOut>[] = [
    { key: "ts", header: t("admin.ai.usage.ts"), render: (u) => formatDateTime(u.ts, locale) },
    {
      key: "queue",
      header: t("admin.ai.usage.queue"),
      render: (u) => (u.queue_id != null ? queueNameById.get(u.queue_id) ?? u.queue_id : "—"),
    },
    { key: "feature", header: t("admin.ai.usage.feature"), render: (u) => t(`admin.ai.feature.${u.feature}`) },
    { key: "model", header: t("admin.ai.usage.model"), mono: true, render: (u) => u.model ?? "—" },
    {
      key: "tokens",
      header: t("admin.ai.usage.tokens"),
      mono: true,
      render: (u) => `${u.prompt_tokens}/${u.completion_tokens}`,
    },
    {
      key: "success",
      header: t("admin.ai.usage.success"),
      render: (u) => (
        <Badge tone={u.success ? "success" : "danger"}>
          {u.success ? t("admin.ai.usage.ok") : t("admin.ai.usage.failed")}
        </Badge>
      ),
    },
  ];

  return (
    <div className="space-y-4 p-4" data-testid="admin-ai-queues-page">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">{t("admin.ai.queues.title")}</h1>
          <p className="mt-1 text-xs text-muted">{t("admin.ai.queues.description")}</p>
        </div>
        {listTab === "policies" && (
          <Button
            variant="primary"
            size="sm"
            data-testid="admin-ai-queues-new"
            disabled={availableQueues.length === 0}
            onClick={() => void navigate({ to: "/admin/ai/queues/new" })}
            aria-label={t("admin.ai.queues.new")}
            title={t("admin.ai.queues.new")}
            className="!px-2"
          >
            <PlusIcon className="text-[16px]" />
          </Button>
        )}
      </div>

      <Tabs
        items={[
          { id: "policies", label: t("admin.ai.queues.list.tabPolicies") },
          { id: "usage", label: t("admin.ai.queues.list.tabUsage") },
        ]}
        value={listTab}
        onChange={(id) => setListTab(id as ListTab)}
      />

      {listTab === "policies" ? (
        <div
          className="rounded-xl border border-hairline bg-surface"
          data-testid="admin-ai-queues-table"
        >
          {policiesQ.isLoading ? (
            <div className="flex justify-center p-6">
              <Spinner />
            </div>
          ) : (policiesQ.data?.items.length ?? 0) === 0 ? (
            <p className="p-4 text-sm text-muted">{t("admin.table.empty")}</p>
          ) : (
            (policiesQ.data?.items ?? []).map(renderPolicyRow)
          )}
        </div>
      ) : (
        <div className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="w-44">
              <PickerField
                testId="admin-ai-usage-queue-filter"
                value={usageQueueFilter}
                items={usageQueueItems}
                placeholder={t("admin.ai.usage.allQueues")}
                onSelect={(v) => {
                  setUsageQueueFilter(v);
                  setUsagePage(1);
                }}
              />
            </div>
            <div className="w-44">
              <PickerField
                testId="admin-ai-usage-feature-filter"
                value={usageFeatureFilter}
                items={usageFeatureItems}
                placeholder={t("admin.ai.usage.allFeatures")}
                onSelect={(v) => {
                  setUsageFeatureFilter(v);
                  setUsagePage(1);
                }}
              />
            </div>
            {usageQ.data && (
              <span className="text-xs text-muted" data-testid="admin-ai-usage-totals">
                {t("admin.ai.usage.totals", {
                  prompt: usageQ.data.total_prompt_tokens,
                  completion: usageQ.data.total_completion_tokens,
                })}
              </span>
            )}
          </div>
          <DataTable
            columns={usageColumns}
            rows={usageQ.data?.items ?? []}
            rowKey={(u) => u.id}
            isLoading={usageQ.isLoading}
            emptyLabel={t("admin.table.empty")}
            testId="admin-ai-usage-table"
          />
        </div>
      )}

      {confirmDialog}
    </div>
  );
}
