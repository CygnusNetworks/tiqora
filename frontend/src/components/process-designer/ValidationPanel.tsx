import { useTranslation } from "react-i18next";
import type { ValidationIssue } from "@/lib/processDesignerApi";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/cn";
import { issueText } from "./issueText";

/**
 * Result of "Prüfen", grouped by severity (errors first). A click on an entry reports the issue so
 * the page can select the entity on the canvas.
 */
export function ValidationPanel({
  issues,
  loading,
  error,
  onSelect,
  onRecheck,
}: {
  issues: ValidationIssue[] | undefined;
  loading: boolean;
  error: boolean;
  onSelect: (issue: ValidationIssue) => void;
  onRecheck: () => void;
}) {
  const { t } = useTranslation();
  const errors = (issues ?? []).filter((i) => i.severity === "error");
  const warnings = (issues ?? []).filter((i) => i.severity === "warning");

  const group = (severity: "error" | "warning", list: ValidationIssue[]) =>
    list.length === 0 ? null : (
      <section data-testid={`validation-group-${severity}`}>
        <h3
          className={cn(
            "text-xs font-medium uppercase tracking-wide",
            severity === "error" ? "text-danger" : "text-amber",
          )}
        >
          {t(severity === "error" ? "admin.processDesigner.validation.errors" : "admin.processDesigner.validation.warnings")}{" "}
          ({list.length})
        </h3>
        <ul className="mt-1 divide-y divide-hairline rounded-md border border-hairline">
          {list.map((issue, idx) => (
            <li key={`${issue.code}:${issue.entity_id}:${issue.field}:${idx}`}>
              <button
                type="button"
                onClick={() => onSelect(issue)}
                data-testid="validation-issue"
                className="flex w-full flex-col items-start gap-0.5 px-3 py-1.5 text-left text-sm hover:bg-surface-subtle"
              >
                <span className="text-xs text-muted">
                  {issue.entity_type === "Process" ? t("admin.processDesigner.validation.process") : issue.params.name ?? issue.entity_id}
                </span>
                <span className="text-ink">{issueText(t, issue)}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    );

  return (
    <div className="space-y-3" data-testid="validation-panel">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-ink">{t("admin.processDesigner.validation.title")}</h2>
        <Button size="sm" onClick={onRecheck} disabled={loading} data-testid="validation-recheck">
          {t("admin.processDesigner.validation.recheck")}
        </Button>
      </div>
      {loading && !issues ? (
        <Spinner />
      ) : error ? (
        <p role="alert" className="text-sm text-danger">
          {t("admin.processDesigner.validation.loadError")}
        </p>
      ) : errors.length + warnings.length === 0 ? (
        <p className="text-sm text-muted" data-testid="validation-none">
          {t("admin.processDesigner.validation.none")}
        </p>
      ) : (
        <>
          {group("error", errors)}
          {group("warning", warnings)}
        </>
      )}
    </div>
  );
}
