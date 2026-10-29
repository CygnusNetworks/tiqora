import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { changeAdminErrorText } from "./changeAdminUi";

/**
 * Error, loading and empty state of a change administration list (`${testId}-error`,
 * `${testId}-empty`); the list itself (`children`) only once there are rows.
 */
export function AdminListState({
  query,
  count,
  emptyText,
  testId,
  children,
}: {
  query: { isError: boolean; isPending: boolean; error: unknown };
  count: number;
  emptyText: string;
  testId: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  if (query.isError) {
    return (
      <p className="text-sm text-danger" role="alert" data-testid={`${testId}-error`}>
        {changeAdminErrorText(query.error, t)}
      </p>
    );
  }
  if (count === 0) {
    return (
      <p className="text-sm text-muted" data-testid={`${testId}-empty`}>
        {query.isPending ? t("common.loading") : emptyText}
      </p>
    );
  }
  return <>{children}</>;
}
