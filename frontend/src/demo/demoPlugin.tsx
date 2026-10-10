/* eslint-disable react-refresh/only-export-components -- plugin module: components are exposed through the default export */
/**
 * Demo plugin `org.example.demo` for the public demo build (VITE_DEMO=1): a
 * page, a ticket panel and a home-page widget built from the plugin SDK
 * components. In-tree, so it imports the host runtime directly instead of the
 * shim URLs real plugins use. Wired up in `src/demo/start.ts`.
 */
import { Badge, Button, EmptyState, Panel, definePlugin, usePluginTranslation } from "@/plugins/sdkRuntime";
import { useState } from "react";

function DemoPage({ subPath }: { path: string; subPath: string }) {
  const { t } = usePluginTranslation();
  const [clicks, setClicks] = useState(0);
  return (
    <div className="space-y-3">
      <Panel title={t("page.panelTitle")} actions={<Badge tone="accent">{t("page.badge")}</Badge>}>
        <p className="text-sm text-ink">{t("page.body")}</p>
        {subPath ? <p className="mt-1 text-xs text-muted">{subPath}</p> : null}
        <div className="mt-3 flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => setClicks((n) => n + 1)}>
            {t("page.button")}
          </Button>
          <span className="text-xs text-muted">{clicks}</span>
        </div>
      </Panel>
      <EmptyState title={t("page.emptyTitle")} hint={t("page.emptyHint")} />
    </div>
  );
}

function DemoPanel({ ticketId }: { ticketId: number }) {
  const { t } = usePluginTranslation();
  return (
    <Panel title={t("panel.title")}>
      <p className="text-sm text-ink">{t("panel.body", { id: ticketId })}</p>
    </Panel>
  );
}

function DemoWidget({ settings }: { settings: { greeting?: string } }) {
  const { t } = usePluginTranslation();
  return (
    <Panel title={t("widget.title")}>
      <p className="text-sm text-ink">{settings.greeting ?? t("widget.body")}</p>
    </Panel>
  );
}

export default definePlugin({ components: { DemoPage, DemoPanel, DemoWidget } });
