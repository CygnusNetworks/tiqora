import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "@tanstack/react-router";
import { processDesignerApi } from "@/lib/processDesignerApi";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { DesignerCanvas } from "@/components/process-designer/DesignerCanvas";
import { usePhoneWidth } from "@/components/process-designer/usePhoneWidth";

/**
 * Process designer (lazy route `/admin/processes/$processEntityId`). Skeleton: loads the draft and
 * the catalogue and lays out palette, canvas and side panel; the editing parts follow in later tasks.
 * On phones (< 768 px) it is a read-only list.
 */
export function ProcessDesignerPage() {
  const { t } = useTranslation();
  const { processEntityId } = useParams({ from: "/admin/processes/$processEntityId" });
  const phone = usePhoneWidth();
  const draftQ = useQuery({
    queryKey: ["admin", "process-designer", "draft", processEntityId],
    queryFn: ({ signal }) => processDesignerApi.getProcess(processEntityId, signal),
  });
  const catalogQ = useQuery({
    queryKey: ["admin", "process-designer", "catalog"],
    queryFn: ({ signal }) => processDesignerApi.catalog(signal),
  });

  const draft = draftQ.data;
  return (
    <div className="flex h-full flex-col gap-3 p-4" data-testid="process-designer-page">
      <Link to="/admin/processes" className="text-sm text-accent hover:underline">
        {t("common.back")}
      </Link>

      {draftQ.isLoading || catalogQ.isLoading ? (
        <Spinner />
      ) : draftQ.isError || catalogQ.isError || !draft ? (
        <p className="text-sm text-danger">{t("admin.processDesigner.loadError")}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h1 className="font-display text-xl font-semibold text-ink">{draft.process.name}</h1>
            <div className="flex items-center gap-2">
              {draft.process.sync_state !== "synced" ? (
                <Badge tone="muted">{t("admin.processDesigner.draft")}</Badge>
              ) : null}
              <span className="text-xs text-muted">{t("admin.processDesigner.title")}</span>
            </div>
          </div>

          {phone ? (
            <div data-testid="process-designer-phone-list" className="space-y-2">
              <p className="text-sm text-muted">{t("admin.processDesigner.phoneReadOnly")}</p>
              <ul className="divide-y divide-hairline rounded-lg border border-hairline bg-surface">
                {draft.activities.map((a) => (
                  <li key={a.entity_id} data-testid={`process-designer-activity-${a.entity_id}`} className="px-4 py-2 text-sm text-ink">
                    {a.name}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div
              className="grid min-h-0 flex-1 grid-cols-[14rem_minmax(0,1fr)_20rem] gap-3"
              data-testid="process-designer-layout"
            >
              <aside className="rounded-lg border border-hairline bg-surface p-3" data-testid="process-designer-palette">
                <h2 className="text-xs font-medium uppercase tracking-wide text-muted">
                  {t("admin.processDesigner.palette")}
                </h2>
              </aside>
              <section className="min-h-0 rounded-lg border border-hairline bg-surface">
                <DesignerCanvas draft={draft} />
              </section>
              <aside className="rounded-lg border border-hairline bg-surface p-3" data-testid="process-designer-panel">
                <h2 className="text-xs font-medium uppercase tracking-wide text-muted">
                  {t("admin.processDesigner.panel")}
                </h2>
              </aside>
            </div>
          )}
        </>
      )}
    </div>
  );
}
