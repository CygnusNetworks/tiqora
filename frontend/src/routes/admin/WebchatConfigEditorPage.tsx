import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import {
  chatAiStatusKey,
  chatConfigsKey,
  webchatApi,
  type ChatConfig,
} from "@/lib/webchatApi";
import {
  buildValues,
  draftFromConfig,
  newDraft,
  serverFieldErrors,
  tabOfPath,
  validateDraft,
  type Draft,
  type EditorTab,
  type FormErrors,
} from "@/lib/webchatForm";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Tabs, type TabItem } from "@/components/ui/Tabs";
import { GeneralTab } from "@/components/admin/webchat/GeneralTab";
import { HoursTab } from "@/components/admin/webchat/HoursTab";
import { AiTab } from "@/components/admin/webchat/AiTab";
import { PrechatTab } from "@/components/admin/webchat/PrechatTab";
import { LookTab } from "@/components/admin/webchat/LookTab";
import { ProtectionTab } from "@/components/admin/webchat/ProtectionTab";
import { EmbedTab } from "@/components/admin/webchat/EmbedTab";

const PRECHAT_DF_TYPES = new Set(["Text", "TextArea", "Dropdown", "Checkbox"]);
const TAB_ORDER: EditorTab[] = ["general", "hours", "ai", "prechat", "look", "protection", "embed"];

function WebchatConfigEditor({ configId }: { configId: number | null }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const isEdit = configId !== null;

  const [tab, setTab] = useState<EditorTab>("general");
  const [draft, setDraftState] = useState<Draft>(newDraft);
  const [dirty, setDirty] = useState(false);
  const [serverErrors, setServerErrors] = useState<FormErrors>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const loadedFor = useRef<number | null>(null);

  const configQ = useQuery({
    queryKey: [...chatConfigsKey, configId],
    enabled: isEdit,
    queryFn: ({ signal }) => webchatApi.getConfig(configId as number, signal),
  });
  const queuesQ = useQuery({
    queryKey: ["admin", "webchat", "queues"],
    queryFn: ({ signal }) => api.listReferenceQueues({}, signal),
    staleTime: 60_000,
  });
  const prioritiesQ = useQuery({
    queryKey: ["admin", "webchat", "priorities"],
    queryFn: ({ signal }) => api.listReferencePriorities(signal),
    staleTime: 60_000,
  });
  const dynamicQ = useQuery({
    queryKey: ["admin", "webchat", "dynamic-fields"],
    queryFn: ({ signal }) => api.adminDynamicFields.list({ page: 1, pageSize: 200, valid: "valid" }, signal),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (configQ.data && loadedFor.current !== configQ.data.id) {
      loadedFor.current = configQ.data.id;
      setDraftState(draftFromConfig(configQ.data));
    }
  }, [configQ.data]);

  const queues = useMemo(() => (queuesQ.data ?? []).map((q) => ({ value: q.id, label: q.name })), [queuesQ.data]);
  const priorities = useMemo(() => (prioritiesQ.data ?? []).map((p) => ({ value: p.id, label: p.name })), [prioritiesQ.data]);
  const dynamicFields = useMemo(
    () =>
      (dynamicQ.data?.items ?? [])
        .filter((f) => f.object_type === "Ticket" && PRECHAT_DF_TYPES.has(f.field_type))
        .map((f) => ({ value: f.name, label: f.label ? `${f.label} (${f.name})` : f.name })),
    [dynamicQ.data],
  );

  const clientErrors = useMemo(() => validateDraft(draft), [draft]);
  const shown: FormErrors = useMemo(
    () => ({ ...(dirty ? clientErrors : {}), ...serverErrors }),
    [dirty, clientErrors, serverErrors],
  );
  const hasClientErrors = Object.keys(clientErrors).length > 0;

  const setDraft = (next: Draft) => {
    setDraftState(next);
    setDirty(true);
    setSaved(false);
    setServerErrors({});
    setSaveError(null);
  };

  const onSaved = (cfg: ChatConfig) => {
    qc.setQueryData([...chatConfigsKey, cfg.id], cfg);
    void qc.invalidateQueries({ queryKey: chatConfigsKey, exact: true });
    void qc.invalidateQueries({ queryKey: chatAiStatusKey(cfg.id) });
    loadedFor.current = cfg.id;
    setDraftState(draftFromConfig(cfg));
    setDirty(false);
    setSaved(true);
  };

  const saveM = useMutation({
    mutationFn: () => {
      const values = buildValues(draft);
      const name = draft.name.trim();
      return isEdit
        ? webchatApi.updateConfig(configId, { name, valid: draft.valid, values })
        : webchatApi.createConfig({ name, valid: draft.valid, values });
    },
    onSuccess: (cfg) => {
      onSaved(cfg);
      if (!isEdit) void navigate({ to: "/admin/webchat/$configId", params: { configId: String(cfg.id) } });
    },
    onError: (err) => {
      const fields = serverFieldErrors(err);
      if (fields) {
        setServerErrors(fields);
        setSaveError(t("webchat.admin.checkFields"));
        const first = TAB_ORDER.find((tb) => Object.keys(fields).some((p) => tabOfPath(p) === tb));
        if (first && !Object.keys(fields).some((p) => tabOfPath(p) === tab)) setTab(first);
      } else {
        setSaveError(t("webchat.admin.saveFailed"));
      }
    },
  });

  const tabHasError = (tb: EditorTab) => Object.keys(shown).some((p) => tabOfPath(p) === tb);
  const tabs: TabItem[] = TAB_ORDER.map((id) => ({
    id,
    label: (
      <span className="inline-flex items-center gap-1.5">
        {t(`webchat.admin.tabs.${id}`)}
        {tabHasError(id) && (
          <span
            data-testid={`webchat-tab-${id}-error`}
            role="img"
            aria-label={t("webchat.admin.tabHasErrors")}
            className="h-1.5 w-1.5 rounded-full bg-danger"
          />
        )}
      </span>
    ),
  }));

  if (isEdit && configQ.isLoading) {
    return (
      <div className="flex justify-center p-8" data-testid="webchat-editor-loading">
        <Spinner />
      </div>
    );
  }
  if (isEdit && configQ.isError) {
    return (
      <p role="alert" className="p-6 text-sm text-danger" data-testid="webchat-editor-error">
        {t("webchat.admin.loadFailed")}
      </p>
    );
  }

  const common = { draft, errors: shown, onDraft: setDraft };
  const config = configQ.data ?? null;
  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 p-4" data-testid="webchat-editor-page">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">
            {isEdit ? draft.name || t("webchat.admin.editTitle") : t("webchat.admin.newTitle")}
          </h1>
          <p className="text-xs text-muted">{t("admin.pageDescriptions.webchat")}</p>
        </div>
        <div className="flex items-center gap-2">
          {saved && (
            <span role="status" data-testid="webchat-saved" className="text-xs text-muted">
              {t("webchat.admin.saved")}
            </span>
          )}
          <Link to="/admin/webchat" className="text-sm text-muted hover:text-ink hover:underline" data-testid="webchat-editor-cancel">
            {t("webchat.admin.back")}
          </Link>
          <Button
            variant="primary"
            data-testid="webchat-save"
            disabled={saveM.isPending || hasClientErrors}
            onClick={() => saveM.mutate()}
          >
            {saveM.isPending ? t("admin.form.saving") : t("admin.form.save")}
          </Button>
        </div>
      </div>

      <Tabs items={tabs} value={tab} onChange={(id) => setTab(id as EditorTab)} />

      {saveError && (
        <p role="alert" className="text-sm text-danger" data-testid="webchat-save-error">
          {saveError}
        </p>
      )}

      {shown.body && (
        <p role="alert" className="text-sm text-danger" data-testid="webchat-body-error">
          {shown.body}
        </p>
      )}

      <div role="tabpanel" aria-label={t(`webchat.admin.tabs.${tab}`)} className="rounded-lg border border-hairline bg-surface p-4">
        {tab === "general" && <GeneralTab {...common} queues={queues} priorities={priorities} />}
        {tab === "hours" && <HoursTab {...common} />}
        {tab === "ai" && <AiTab {...common} configId={config?.id ?? null} />}
        {tab === "prechat" && <PrechatTab {...common} dynamicFields={dynamicFields} />}
        {tab === "look" && <LookTab {...common} publicKey={config?.public_key ?? null} />}
        {tab === "protection" && <ProtectionTab {...common} />}
        {tab === "embed" && (
          <EmbedTab
            {...common}
            config={config}
            onRotated={(next) => {
              qc.setQueryData([...chatConfigsKey, next.id], next);
              void qc.invalidateQueries({ queryKey: chatConfigsKey, exact: true });
            }}
          />
        )}
      </div>
    </div>
  );
}

export function WebchatConfigEditorPage() {
  const { configId } = useParams({ from: "/admin/webchat/$configId" });
  const id = configId === "new" ? null : Number(configId);
  return <WebchatConfigEditor key={configId} configId={id !== null && Number.isFinite(id) ? id : null} />;
}
