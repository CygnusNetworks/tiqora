import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cmdbApi, type CiHistoryPage } from "@/lib/cmdbApi";
import { formatDateTime } from "@/lib/format";

/** Znuny `configitem_history_type` names, one `cmdb.history.events.*` key each. */
export const CI_HISTORY_EVENTS = [
  "ConfigItemCreate",
  "VersionCreate",
  "DeploymentStateUpdate",
  "IncidentStateUpdate",
  "ConfigItemDelete",
  "LinkAdd",
  "LinkDelete",
  "DefinitionUpdate",
  "NameUpdate",
  "ValueUpdate",
  "AttachmentAddPost",
  "AttachmentDeletePost",
] as const;

/** Change log of one CI (newest first), paged with `before_id`. */
export function CiHistoryTable({ ciId, pageSize = 25 }: { ciId: number; pageSize?: number }) {
  const { t, i18n } = useTranslation();
  const q = useInfiniteQuery({
    queryKey: ["cmdb", "ci", ciId, "history", pageSize],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => cmdbApi.history(ciId, { before_id: pageParam, limit: pageSize }),
    getNextPageParam: (last: CiHistoryPage) => (last.has_more && last.items.length > 0 ? last.items[last.items.length - 1].id : undefined),
  });
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  if (q.isError) {
    return (
      <p role="alert" className="text-sm text-danger">
        {t("cmdb.history.loadFailed")}
      </p>
    );
  }
  if (q.isSuccess && rows.length === 0) {
    return (
      <p className="text-sm text-muted" data-testid="ci-history-empty">
        {t("cmdb.history.empty")}
      </p>
    );
  }
  return (
    <div className="space-y-3" data-testid="ci-history">
      {q.isPending && <Spinner />}
      {rows.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-hairline">
          <table className="w-full text-sm">
            <thead className="bg-surface-subtle text-xs text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  {t("cmdb.history.when")}
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  {t("cmdb.history.who")}
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  {t("cmdb.history.event")}
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  {t("cmdb.history.detail")}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} data-testid={`ci-history-row-${r.id}`} className="border-t border-hairline align-top">
                  <td className="whitespace-nowrap px-3 py-2 text-muted">{formatDateTime(r.created_at, i18n.language)}</td>
                  <td className="px-3 py-2">{r.created_by_name ?? `#${r.created_by}`}</td>
                  <td className="px-3 py-2 font-medium text-ink">{t(`cmdb.history.events.${r.event}`, { defaultValue: r.event })}</td>
                  <td className="break-words px-3 py-2 text-muted">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {q.hasNextPage && (
        <Button size="sm" onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage} data-testid="ci-history-more">
          {q.isFetchingNextPage ? <Spinner /> : t("cmdb.detail.loadMore")}
        </Button>
      )}
    </div>
  );
}
