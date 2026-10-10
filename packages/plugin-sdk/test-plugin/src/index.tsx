// Smallest real plugin, built by `pnpm check:preset` to prove the preset
// keeps every shared module external (see scripts/check-preset.mjs).
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Panel, definePlugin, usePluginApi, usePluginTranslation, type TicketPanelProps } from "@tiqora/plugin-sdk";

function HelloPanel({ ticketId }: TicketPanelProps) {
  const { t } = usePluginTranslation();
  const api = usePluginApi();
  const navigate = useNavigate();
  const status = useQuery({
    queryKey: ["test-plugin", "status", ticketId],
    queryFn: () => api.get<{ ok: boolean }>("status", { query: { ticket: ticketId } }),
  });
  return (
    <Panel title={t("panel.title")}>
      <button type="button" onClick={() => void navigate({ to: "/agent" })}>
        {status.data?.ok ? t("panel.ok") : t("panel.waiting")}
      </button>
    </Panel>
  );
}

export default definePlugin({ components: { HelloPanel } });
