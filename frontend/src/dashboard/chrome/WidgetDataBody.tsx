import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useWidgetData } from "../data";
import { WidgetError, WidgetForbidden } from "./WidgetError";
import { WidgetSkeleton } from "./WidgetSkeleton";
import { skeletonRows } from "./mode";

/**
 * The one data gate of every data widget: loading/pending -> skeleton, error -> error tile with
 * "Erneut versuchen" (this widget only), forbidden -> message without retry. `ok` hands the data
 * to `children`; `none` (no data delivered) hands `undefined`, and the widget shows its empty state.
 */
export function WidgetDataBody<T>({
  h,
  children,
}: {
  h: number;
  children: (data: T | undefined) => ReactNode;
}) {
  const { t } = useTranslation();
  const { status, data, error, refetch } = useWidgetData<T>();
  if (status === "loading" || status === "pending") return <WidgetSkeleton rows={skeletonRows(h)} />;
  if (status === "error")
    return <WidgetError message={error || t("dashboards.state.loadFailed")} onRetry={refetch} />;
  if (status === "forbidden") return <WidgetForbidden message={error || t("dashboards.state.forbidden")} />;
  return <>{children(status === "ok" ? data : undefined)}</>;
}

/** A widget's empty state: one sentence, optionally an action. */
export function WidgetEmpty({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex h-full flex-col items-start justify-center gap-2 text-sm text-muted">
      <p>{children}</p>
      {action}
    </div>
  );
}
