import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { RevisionDiff } from "@/lib/processDesignerApi";
import { cn } from "@/lib/cn";
import { diffLines } from "./lineDiff";

const CHANGE_TONE = {
  added: "text-green",
  removed: "text-danger",
  changed: "text-amber",
} as const;

/**
 * Comparison of two deployed versions: the changed entities as a list, below it the exported
 * definition side by side as plain text with line marks (no diff library).
 */
export function RevisionDiffView({ diff }: { diff: RevisionDiff }) {
  const { t } = useTranslation();
  const rows = useMemo(() => diffLines(diff.yaml_before, diff.yaml_after), [diff.yaml_before, diff.yaml_after]);

  const side = (which: "left" | "right") => (
    <pre
      className="min-w-0 overflow-x-auto rounded-md border border-hairline bg-surface-subtle py-1 font-mono text-xs"
      data-testid={`revision-diff-${which === "left" ? "before" : "after"}`}
    >
      {rows.map((row, idx) => {
        const text = row[which];
        const mark = text === null ? " " : row.kind === "same" ? " " : which === "left" ? "-" : "+";
        const marked = text !== null && row.kind !== "same";
        return (
          <span
            key={idx}
            data-kind={text === null ? "blank" : row.kind}
            className={cn(
              "block whitespace-pre px-2",
              marked && (which === "left" ? "bg-danger/10 text-danger" : "bg-green/10 text-green"),
            )}
          >
            {mark} {text ?? ""}
          </span>
        );
      })}
    </pre>
  );

  return (
    <div className="space-y-3" data-testid="revision-diff">
      <h3 className="text-sm font-medium text-ink">
        {t("admin.processDesigner.revisions.diffTitle", { from: diff.from_revision, to: diff.to_revision })}
      </h3>
      {diff.changes.length === 0 ? (
        <p className="text-sm text-muted" data-testid="revision-diff-empty">
          {t("admin.processDesigner.revisions.noChanges")}
        </p>
      ) : (
        <ul className="divide-y divide-hairline rounded-md border border-hairline" data-testid="revision-diff-changes">
          {diff.changes.map((c) => (
            <li
              key={`${c.entity_type}:${c.entity_id}`}
              data-testid={`revision-diff-change-${c.change}`}
              className="flex flex-wrap items-baseline gap-x-2 px-3 py-1.5 text-sm"
            >
              <span className={cn("text-xs font-medium uppercase", CHANGE_TONE[c.change])}>
                {t(`admin.processDesigner.revisions.${c.change}`)}
              </span>
              <span className="text-ink">{c.name || c.entity_id}</span>
              <span className="text-xs text-muted">{c.entity_type}</span>
              {c.changed_keys.length > 0 ? (
                <span className="text-xs text-muted">{c.changed_keys.join(", ")}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {rows.length > 0 ? (
        <div className="grid grid-cols-2 gap-2">
          <div className="min-w-0">
            <p className="mb-1 text-xs text-muted">{t("admin.processDesigner.revisions.before")}</p>
            {side("left")}
          </div>
          <div className="min-w-0">
            <p className="mb-1 text-xs text-muted">{t("admin.processDesigner.revisions.after")}</p>
            {side("right")}
          </div>
        </div>
      ) : null}
    </div>
  );
}
