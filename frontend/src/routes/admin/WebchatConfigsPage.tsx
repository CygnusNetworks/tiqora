import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { chatConfigsKey, webchatApi, type ChatConfig } from "@/lib/webchatApi";
import { DataTable, type DataTableColumn } from "@/components/admin/DataTable";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PlusIcon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/Spinner";
import { Switch } from "@/components/ui/Switch";
import { useConfirm } from "@/components/ui/ConfirmDialog";

const CHANNEL = "webchat";
const CHANNEL_KEY = ["admin", "channels", CHANNEL] as const;

export function WebchatConfigsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { confirm, dialog } = useConfirm();

  const listQ = useQuery({
    queryKey: chatConfigsKey,
    queryFn: ({ signal }) => webchatApi.listConfigs(signal),
  });
  const channelQ = useQuery({
    queryKey: CHANNEL_KEY,
    queryFn: ({ signal }) => api.adminChannels.get(CHANNEL, signal),
  });

  const switchM = useMutation({
    mutationFn: (enabled: boolean) => api.adminChannels.update(CHANNEL, { enabled, config: {} }),
    onSuccess: (data) => qc.setQueryData(CHANNEL_KEY, data),
  });
  const deleteM = useMutation({
    mutationFn: (id: number) => webchatApi.deleteConfig(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: chatConfigsKey }),
  });

  const edit = (row: ChatConfig) =>
    void navigate({ to: "/admin/webchat/$configId", params: { configId: String(row.id) } });

  const handleDelete = async (row: ChatConfig) => {
    const ok = await confirm({
      title: t("webchat.admin.list.title"),
      message: t("webchat.admin.list.deleteConfirm", { name: row.name }),
      variant: "danger",
    });
    if (ok) deleteM.mutate(row.id);
  };

  const columns: DataTableColumn<ChatConfig>[] = [
    {
      key: "name",
      header: t("webchat.admin.list.name"),
      render: (r) => (
        <Link
          to="/admin/webchat/$configId"
          params={{ configId: String(r.id) }}
          className="text-accent hover:underline"
          data-testid={`webchat-config-link-${r.id}`}
        >
          {r.name}
        </Link>
      ),
    },
    {
      key: "origins",
      header: t("webchat.admin.list.origins"),
      hideBelow: "lg",
      render: (r) => (
        <span className="text-xs text-muted">
          {(r.values.allowed_origins ?? []).join(", ") || "—"}
        </span>
      ),
    },
    { key: "topics", header: t("webchat.admin.list.topics"), render: (r) => <Badge tone="muted">{r.values.topics.length}</Badge> },
    {
      key: "valid",
      header: t("webchat.admin.list.active"),
      render: (r) =>
        r.valid ? <Badge tone="success">{t("admin.table.valid")}</Badge> : <Badge tone="muted">{t("admin.table.invalid")}</Badge>,
    },
    { key: "chats", header: t("webchat.admin.list.chats"), mono: true, render: (r) => r.session_count },
  ];

  const enabled = channelQ.data?.enabled ?? false;
  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 p-4" data-testid="webchat-configs-page">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="font-display text-xl font-semibold text-ink">{t("webchat.admin.list.title")}</h1>
          <p className="text-xs text-muted">{t("admin.pageDescriptions.webchat")}</p>
        </div>
        <Button variant="primary" data-testid="webchat-new" onClick={() => void navigate({ to: "/admin/webchat/$configId", params: { configId: "new" } })}>
          <PlusIcon className="h-4 w-4" />
          {t("webchat.admin.list.new")}
        </Button>
      </div>

      <div className="flex items-center gap-3 rounded-lg border border-hairline bg-surface p-3">
        {channelQ.isLoading ? (
          <Spinner />
        ) : (
          <Switch
            checked={enabled}
            disabled={channelQ.isError || switchM.isPending}
            onChange={(v) => switchM.mutate(v)}
            testId="webchat-master-switch"
            aria-labelledby="webchat-master-label"
            aria-describedby="webchat-master-hint"
          />
        )}
        <div>
          <p id="webchat-master-label" className="text-sm font-medium text-ink">
            {t("webchat.admin.list.masterSwitch")}
          </p>
          <p id="webchat-master-hint" className="text-xs text-muted">
            {t("webchat.admin.list.masterHint")}
          </p>
        </div>
      </div>
      {(switchM.isError || channelQ.isError) && (
        <p role="alert" className="text-sm text-danger" data-testid="webchat-master-error">
          {t("webchat.admin.saveFailed")}
        </p>
      )}

      <DataTable
        columns={columns}
        rows={listQ.data ?? []}
        rowKey={(r) => r.id}
        isLoading={listQ.isLoading}
        emptyLabel={t("webchat.admin.list.empty")}
        onEdit={edit}
        onDelete={(r) => void handleDelete(r)}
      />
      {listQ.isError && (
        <p role="alert" className="text-sm text-danger">
          {t("webchat.admin.loadFailed")}
        </p>
      )}
      {dialog}
    </div>
  );
}
