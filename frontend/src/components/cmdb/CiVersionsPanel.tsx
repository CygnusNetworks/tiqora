import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { cmdbApi, type CiVersionPage } from "@/lib/cmdbApi";
import { formatDateTime } from "@/lib/format";
import { CiAttributeView } from "./CiAttributeView";
import { CiDiffView } from "./CiDiffView";

/**
 * Versions of one CI, newest first. Pick two for a diff (always requested oldest to newest) or open
 * one to read it with the class definition it was saved under.
 */
export function CiVersionsPanel({ ciId, pageSize = 20 }: { ciId: number; pageSize?: number }) {
  const { t, i18n } = useTranslation();
  const [includeArchived, setIncludeArchived] = useState(false);
  const [picked, setPicked] = useState<number[]>([]);
  const [compare, setCompare] = useState<[number, number] | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  const q = useInfiniteQuery({
    queryKey: ["cmdb", "ci", ciId, "versions", pageSize, includeArchived],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => cmdbApi.versions(ciId, { before_id: pageParam, limit: pageSize, ...(includeArchived ? { include_archived: true } : {}) }),
    getNextPageParam: (last: CiVersionPage) => (last.has_more && last.items.length > 0 ? last.items[last.items.length - 1].id : undefined),
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const archivedCount = q.data?.pages[0]?.archived_count ?? 0;

  const diffQ = useQuery({
    queryKey: ["cmdb", "ci", ciId, "diff", compare],
    queryFn: () => cmdbApi.diff(ciId, (compare as [number, number])[0], (compare as [number, number])[1]),
    enabled: compare !== null,
  });
  const versionQ = useQuery({
    queryKey: ["cmdb", "ci", ciId, "version", openId],
    queryFn: () => cmdbApi.version(ciId, openId as number),
    enabled: openId !== null,
  });

  const toggle = (id: number) => {
    setCompare(null);
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev.slice(-1), id]));
  };
  const startCompare = () => {
    if (picked.length !== 2) return;
    const [a, b] = picked;
    setOpenId(null);
    setCompare(a < b ? [a, b] : [b, a]);
  };

  if (q.isError) {
    return (
      <p role="alert" className="text-sm text-danger">
        {t("cmdb.detail.versions.loadFailed")}
      </p>
    );
  }

  return (
    <div className="space-y-3" data-testid="ci-versions">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" disabled={picked.length !== 2} onClick={startCompare} data-testid="ci-versions-compare">
          {t("cmdb.detail.versions.showChanges")}
        </Button>
        <span className="text-xs text-muted">{t("cmdb.detail.versions.pickHint")}</span>
        {q.isFetching && <Spinner />}
      </div>

      {q.isSuccess && items.length === 0 && (
        <p className="text-sm text-muted" data-testid="ci-versions-empty">
          {t("cmdb.detail.versions.empty")}
        </p>
      )}

      {items.length > 0 && (
        <ul className="divide-y divide-hairline rounded-md border border-hairline">
          {items.map((v) => (
            <li key={v.id} data-testid={`ci-version-${v.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
              <input
                type="checkbox"
                checked={picked.includes(v.id)}
                onChange={() => toggle(v.id)}
                aria-label={t("cmdb.detail.versions.pick", { date: formatDateTime(v.created_at, i18n.language) })}
                data-testid={`ci-version-select-${v.id}`}
              />
              <button type="button" onClick={() => { setCompare(null); setOpenId(v.id); }} className="font-medium text-accent hover:underline" data-testid={`ci-version-open-${v.id}`}>
                {formatDateTime(v.created_at, i18n.language)}
              </button>
              <span className="text-muted">{v.created_by_name ?? `#${v.created_by}`}</span>
              <span className="text-muted">{t("cmdb.detail.versions.definition", { version: v.definition_version })}</span>
              <span className="text-muted">
                {v.deployment_state} · {v.incident_state}
              </span>
              {v.archived && <Badge tone="muted">{t("cmdb.detail.versions.archived")}</Badge>}
              {v.change_note && <span className="basis-full break-words text-xs text-muted">{v.change_note}</span>}
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap gap-2">
        {q.hasNextPage && (
          <Button size="sm" onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage} data-testid="ci-versions-more">
            {q.isFetchingNextPage ? <Spinner /> : t("cmdb.detail.loadMore")}
          </Button>
        )}
        {archivedCount > 0 && !includeArchived && (
          <Button size="sm" onClick={() => { setIncludeArchived(true); setPicked([]); setCompare(null); }} data-testid="ci-versions-archived">
            {t("cmdb.detail.versions.loadArchived", { count: archivedCount })}
          </Button>
        )}
      </div>

      {compare && (
        <section className="space-y-2 rounded-md border border-hairline p-3" aria-label={t("cmdb.detail.versions.changes")}>
          <h3 className="text-sm font-semibold text-ink">{t("cmdb.detail.versions.changes")}</h3>
          {diffQ.isPending && <Spinner />}
          {diffQ.isError && (
            <p role="alert" className="text-sm text-danger">
              {t("cmdb.detail.versions.loadFailed")}
            </p>
          )}
          {diffQ.data && <CiDiffView diff={diffQ.data} />}
        </section>
      )}

      {openId !== null && (
        <section className="space-y-2 rounded-md border border-hairline p-3" data-testid="ci-version-detail">
          {versionQ.isPending && <Spinner />}
          {versionQ.isError && (
            <p role="alert" className="text-sm text-danger">
              {t("cmdb.detail.versions.loadFailed")}
            </p>
          )}
          {versionQ.data && (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-ink">
                  {versionQ.data.name} · {formatDateTime(versionQ.data.created_at, i18n.language)} ·{" "}
                  {t("cmdb.detail.versions.definition", { version: versionQ.data.definition_version })}
                </h3>
                <Button size="sm" variant="ghost" onClick={() => setOpenId(null)}>
                  {t("cmdb.detail.versions.close")}
                </Button>
              </div>
              <CiAttributeView rendered={versionQ.data.rendered} />
            </>
          )}
        </section>
      )}
    </div>
  );
}
