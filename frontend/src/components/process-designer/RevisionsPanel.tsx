import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { formatDateTime } from "@/lib/format";
import { processDesignerApi, type ProcessDraft } from "@/lib/processDesignerApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useConfirm } from "@/components/ui/ConfirmDialog";
import { RevisionDiffView } from "./RevisionDiffView";

export const REVISIONS_QUERY_KEY = ["admin", "process-designer", "revisions"] as const;

/**
 * Deployed versions (newest first): who and when, "Vergleichen" with the previous or a picked
 * version, and "Wiederherstellen" which writes the process back as a draft (after confirmation).
 */
export function RevisionsPanel({
  processEntityId,
  onRestored,
}: {
  processEntityId: string;
  /** The restored draft; the page replaces its cached draft and drops local edits. */
  onRestored: (draft: ProcessDraft) => void;
}) {
  const { t, i18n } = useTranslation();
  const { confirm, dialog } = useConfirm();
  const revisionsQ = useQuery({
    queryKey: REVISIONS_QUERY_KEY,
    queryFn: ({ signal }) => processDesignerApi.revisions(signal),
  });
  const revisions = useMemo(() => [...(revisionsQ.data ?? [])].sort((a, b) => b.id - a.id), [revisionsQ.data]);

  // "previous" = the server default; a number picks the base revision, 0 compares against nothing.
  const [base, setBase] = useState<string>("previous");
  const [compare, setCompare] = useState<{ id: number; against?: number } | null>(null);
  const diffQ = useQuery({
    queryKey: [...REVISIONS_QUERY_KEY, "diff", compare?.id, compare?.against, processEntityId],
    queryFn: ({ signal }) =>
      processDesignerApi.revisionDiff(
        compare!.id,
        { against: compare!.against, process: processEntityId },
        signal,
      ),
    enabled: compare !== null,
  });

  const restoreM = useMutation({
    mutationFn: (id: number) => processDesignerApi.restoreRevision(id, processEntityId),
    onSuccess: (draft) => onRestored(draft),
  });

  const restore = async (id: number) => {
    const ok = await confirm({
      title: t("admin.processDesigner.revisions.restoreTitle"),
      message: t("admin.processDesigner.revisions.restoreConfirm", { id }),
      confirmLabel: t("admin.processDesigner.revisions.restore"),
      variant: "danger",
    });
    if (ok) restoreM.mutate(id);
  };

  return (
    <div className="space-y-3" data-testid="revisions-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-ink">{t("admin.processDesigner.revisions.title")}</h2>
        {revisions.length > 1 ? (
          <label className="flex items-center gap-2 text-xs text-muted">
            {t("admin.processDesigner.revisions.compareWith")}
            <select
              value={base}
              onChange={(e) => setBase(e.target.value)}
              data-testid="revisions-base"
              className="rounded-md border border-hairline bg-surface px-2 py-1 text-sm text-ink"
            >
              <option value="previous">{t("admin.processDesigner.revisions.previous")}</option>
              {revisions.map((r) => (
                <option key={r.id} value={String(r.id)}>
                  {t("admin.processDesigner.revisions.version", { id: r.id })}
                </option>
              ))}
              <option value="0">{t("admin.processDesigner.revisions.empty_base")}</option>
            </select>
          </label>
        ) : null}
      </div>

      {revisionsQ.isLoading ? (
        <Spinner />
      ) : revisionsQ.isError ? (
        <p role="alert" className="text-sm text-danger">
          {t("admin.processDesigner.revisions.loadError")}
        </p>
      ) : revisions.length === 0 ? (
        <p className="text-sm text-muted" data-testid="revisions-empty">
          {t("admin.processDesigner.revisions.empty")}
        </p>
      ) : (
        <ul className="divide-y divide-hairline rounded-md border border-hairline" data-testid="revisions-list">
          {revisions.map((r) => (
            <li
              key={r.id}
              data-testid={`revision-${r.id}`}
              className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 text-sm"
            >
              <span className="text-ink">
                {formatDateTime(r.deployed_at, toBcp47(i18n.language))}{" "}
                <span className="text-muted">
                  ({t(`admin.deploy.source.${r.source}`, { defaultValue: r.source })}
                  {r.deployed_by !== null ? `, ${t("admin.processDesigner.revisions.by", { id: r.deployed_by })}` : ""})
                </span>
              </span>
              <span className="flex gap-2">
                <Button
                  size="sm"
                  onClick={() => setCompare({ id: r.id, against: base === "previous" ? undefined : Number(base) })}
                  data-testid={`revision-compare-${r.id}`}
                >
                  {t("admin.processDesigner.revisions.compare")}
                </Button>
                <Button
                  size="sm"
                  onClick={() => void restore(r.id)}
                  disabled={restoreM.isPending}
                  data-testid={`revision-restore-${r.id}`}
                >
                  {t("admin.processDesigner.revisions.restore")}
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {restoreM.isError ? (
        <p role="alert" className="text-sm text-danger" data-testid="revisions-restore-error">
          {t("admin.processDesigner.revisions.restoreError")}
        </p>
      ) : null}

      {compare ? (
        diffQ.isLoading ? (
          <Spinner />
        ) : diffQ.isError || !diffQ.data ? (
          <p role="alert" className="text-sm text-danger">
            {t("admin.processDesigner.revisions.diffError")}
          </p>
        ) : (
          <div className="space-y-2">
            <RevisionDiffView diff={diffQ.data} />
            <Button size="sm" variant="ghost" onClick={() => setCompare(null)}>
              {t("admin.processDesigner.revisions.close")}
            </Button>
          </div>
        )
      ) : null}
      {dialog}
    </div>
  );
}
