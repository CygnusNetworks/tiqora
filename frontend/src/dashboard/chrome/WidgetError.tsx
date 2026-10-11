import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

/** Error tile of one widget; "Erneut versuchen" refetches only this widget. */
export function WidgetError({ message, onRetry }: { message: string; onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <div role="alert" className="flex h-full flex-col items-start justify-center gap-2.5 text-sm">
      <p className="text-danger">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="rounded-md border border-hairline bg-surface px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      >
        {t("dashboards.state.retry")}
      </button>
    </div>
  );
}

/** The viewer may not see this widget's data; no retry (it would answer the same). */
export function WidgetForbidden({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="flex h-full flex-col items-start justify-center gap-2 text-sm text-muted">
      <p>{message}</p>
      {action}
    </div>
  );
}
