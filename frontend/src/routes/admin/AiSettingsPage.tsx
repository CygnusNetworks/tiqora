import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { aiApi, type AiSettingsUpdate, type OperationMode } from "@/lib/aiApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";

const SETTINGS_KEY = ["admin", "ai", "settings"] as const;
const PROVIDERS_KEY = ["admin", "ai", "providers"] as const;
const MCP_KEY = ["admin", "ai", "mcp-clients"] as const;
const POLICIES_KEY = ["admin", "ai", "queue-policies"] as const;

export function AiSettingsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [disclosureText, setDisclosureText] = useState("");
  const [globalCap, setGlobalCap] = useState<string>("");
  const [pendingMode, setPendingMode] = useState<OperationMode | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const settingsQ = useQuery({
    queryKey: SETTINGS_KEY,
    queryFn: ({ signal }) => aiApi.getSettings(signal),
  });
  const providersQ = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: ({ signal }) => aiApi.listProviders(signal),
  });
  const mcpQ = useQuery({
    queryKey: MCP_KEY,
    queryFn: ({ signal }) => aiApi.listMcpClients(signal),
  });
  const policiesQ = useQuery({
    queryKey: POLICIES_KEY,
    queryFn: ({ signal }) => aiApi.listQueuePolicies(signal),
  });
  useEffect(() => {
    if (settingsQ.data) {
      setDisclosureText(settingsQ.data.disclosure_default_text);
      setGlobalCap(
        settingsQ.data.global_max_replies_per_hour != null
          ? String(settingsQ.data.global_max_replies_per_hour)
          : "",
      );
    }
  }, [settingsQ.data]);

  const saveM = useMutation({
    mutationFn: (body: AiSettingsUpdate) => aiApi.putSettings(body),
    onSuccess: (data) => {
      qc.setQueryData(SETTINGS_KEY, data);
      setStatusMsg(t("admin.ai.settings.saved"));
    },
    onError: () => setStatusMsg(t("admin.ai.settings.saveError")),
  });

  const saveTextAndCap = () => {
    setStatusMsg(null);
    const capNum = globalCap.trim() ? Number(globalCap) : null;
    saveM.mutate({
      disclosure_default_text: disclosureText,
      global_max_replies_per_hour: capNum != null && Number.isFinite(capNum) ? capNum : null,
    });
  };

  const confirmModeChange = () => {
    if (!pendingMode) return;
    saveM.mutate({ operation_mode: pendingMode });
    setPendingMode(null);
  };

  if (settingsQ.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4" data-testid="admin-ai-settings-page">
        <Spinner />
      </div>
    );
  }

  if (settingsQ.isError || !settingsQ.data) {
    return (
      <div className="p-4 text-sm text-danger" data-testid="admin-ai-settings-page">
        {t("admin.ai.settings.loadError")}
      </div>
    );
  }

  const mode = settingsQ.data.operation_mode;
  const activePolicyCount = (policiesQ.data?.items ?? []).filter(
    (p) => p.enabled_auto_reply || p.enabled_summary || p.enabled_manual_assist,
  ).length;

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4" data-testid="admin-ai-settings-page">
      <div>
        <h1 className="font-display text-xl font-semibold text-ink">{t("admin.ai.settings.title")}</h1>
        <p className="mt-1 text-sm text-muted">{t("admin.ai.settings.description")}</p>
      </div>

      {mode === "parallel" && (
        <div
          className="rounded-lg border border-escalation/40 bg-escalation/10 p-3 text-sm text-escalation"
          data-testid="admin-ai-parallel-banner"
        >
          {t("admin.ai.settings.parallelBanner")}
        </div>
      )}

      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-hairline bg-surface p-3">
          <p className="text-xs uppercase tracking-wide text-muted">
            {t("admin.ai.settings.statProviders")}
          </p>
          <p className="mt-1 font-mono text-2xl text-ink" data-testid="admin-ai-stat-providers">
            {providersQ.data?.total ?? "—"}
          </p>
        </div>
        <div className="rounded-lg border border-hairline bg-surface p-3">
          <p className="text-xs uppercase tracking-wide text-muted">{t("admin.ai.settings.statMcp")}</p>
          <p className="mt-1 font-mono text-2xl text-ink" data-testid="admin-ai-stat-mcp">
            {mcpQ.data?.total ?? "—"}
          </p>
        </div>
        <div className="rounded-lg border border-hairline bg-surface p-3">
          <p className="text-xs uppercase tracking-wide text-muted">
            {t("admin.ai.settings.statActivePolicies")}
          </p>
          <p className="mt-1 font-mono text-2xl text-ink" data-testid="admin-ai-stat-policies">
            {policiesQ.data ? activePolicyCount : "—"}
          </p>
        </div>
      </div>

      <div className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="font-display text-sm font-semibold text-ink">
          {t("admin.ai.settings.operationMode")}
        </h2>
        <p className="text-xs text-muted">{t("admin.ai.settings.operationModeHint")}</p>
        <div
          className="inline-flex rounded-lg border border-hairline bg-surface p-0.5"
          role="group"
          aria-label={t("admin.ai.settings.operationMode")}
        >
          {(["parallel", "tiqora_primary"] as OperationMode[]).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              data-testid={`admin-ai-mode-${m}`}
              disabled={saveM.isPending}
              onClick={() => {
                if (m !== mode) setPendingMode(m);
              }}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                mode === m
                  ? "bg-accent text-white"
                  : "text-muted hover:bg-surface-subtle hover:text-ink",
              )}
            >
              {t(`admin.ai.settings.mode.${m}`)}
            </button>
          ))}
        </div>

        {pendingMode && (
          <div
            className="space-y-2 rounded-md border border-escalation/40 bg-escalation/10 p-3"
            data-testid="admin-ai-mode-confirm"
          >
            <p className="text-sm text-ink">
              {t("admin.ai.settings.modeConfirm", { mode: t(`admin.ai.settings.mode.${pendingMode}`) })}
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setPendingMode(null)}>
                {t("admin.form.cancel")}
              </Button>
              <Button
                variant="primary"
                size="sm"
                data-testid="admin-ai-mode-confirm-apply"
                onClick={confirmModeChange}
              >
                {t("admin.ai.settings.modeConfirmApply")}
              </Button>
            </div>
          </div>
        )}
      </div>

      <div className="space-y-3 rounded-lg border border-hairline bg-surface p-4">
        <h2 className="font-display text-sm font-semibold text-ink">
          {t("admin.ai.settings.disclosure")}
        </h2>
        <label className="block text-sm">
          <span className="mb-1 block text-muted">{t("admin.ai.settings.disclosureText")}</span>
          <textarea
            data-testid="admin-ai-disclosure-text"
            value={disclosureText}
            onChange={(e) => setDisclosureText(e.target.value)}
            rows={3}
            className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
          />
        </label>
        <label className="block text-sm sm:w-64">
          <span className="mb-1 block text-muted">{t("admin.ai.settings.globalCap")}</span>
          <input
            data-testid="admin-ai-global-cap"
            type="number"
            min={0}
            placeholder={t("admin.ai.settings.globalCapPlaceholder")}
            value={globalCap}
            onChange={(e) => setGlobalCap(e.target.value)}
            className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-1.5 text-sm text-ink"
          />
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            data-testid="admin-ai-settings-save"
            disabled={saveM.isPending}
            onClick={saveTextAndCap}
          >
            {saveM.isPending ? t("admin.form.saving") : t("admin.form.save")}
          </Button>
          {statusMsg && (
            <span className="text-sm text-muted" data-testid="admin-ai-settings-status">
              {statusMsg}
            </span>
          )}
        </div>
      </div>

    </div>
  );
}
