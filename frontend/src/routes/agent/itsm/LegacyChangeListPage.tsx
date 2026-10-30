import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { Spinner } from "@/components/ui/Spinner";
import { LegacySourceBadge } from "@/components/itsm/change/LegacySourceBadge";
import { changeErrorText, FIELD_CLASS, windowText } from "@/components/itsm/change/changeUi";
import { changeApi, changeErrorCode, changeKeys, type LegacyChangeQuery } from "@/lib/changeApi";

const PAGE = 25;
const DEBOUNCE_MS = 300;

/**
 * `/agent/itsm/legacy-changes`: the changes of Znuny's ITSMChangeManagement, read only. In Mode A
 * they are read live from Znuny, in Mode B from the archive. `?ticket_id=` (from the ticket zoom)
 * shows only the changes linked to that ticket. The live search also covers description and justification; the archive only number and title.
 */
export function LegacyChangeListPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { ticket_id: ticketId } = useSearch({ from: "/agent/itsm/legacy-changes" });
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);

  // Another filter starts at the first page (clearing the ticket filter on page 2 must not leave the list).
  useEffect(() => setOffset(0), [ticketId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(text.trim());
      setOffset(0);
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text]);

  const query: LegacyChangeQuery = { q: q || undefined, ticket_id: ticketId, limit: PAGE, offset };
  const listQ = useQuery({
    queryKey: changeKeys.legacyList(query),
    queryFn: ({ signal }) => changeApi.legacyList(query, signal),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const page = listQ.data;
  const items = page?.items ?? [];
  const total = page?.total ?? 0;
  const code = listQ.isError ? changeErrorCode(listQ.error) : null;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6" data-testid="legacyChangeListPage">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="font-display text-xl font-semibold text-ink">{t("itsm.change.pages.legacyList")}</h1>
        {page && <LegacySourceBadge source={page.source} />}
      </div>
      <p className="text-xs text-muted">{t("itsm.change.legacy.readOnly")}</p>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted" htmlFor="legacy-list-search">
            {t("itsm.change.legacy.search")}
          </label>
          <input
            id="legacy-list-search"
            data-testid="legacy-list-search"
            type="search"
            className={FIELD_CLASS}
            value={text}
            placeholder={t("itsm.change.legacy.searchPlaceholder")}
            onChange={(e) => setText(e.target.value)}
          />
          {page && (
            <p className="mt-1 text-xs text-muted" data-testid="legacy-list-search-hint">
              {t(page.source === "live" ? "itsm.change.legacy.searchScopeLive" : "itsm.change.legacy.searchScope")}
            </p>
          )}
        </div>
        {ticketId !== undefined && (
          <div className="flex items-end" data-testid="legacy-list-ticket-filter">
            <p className="flex flex-wrap items-center gap-2 text-sm text-ink">
              <Badge tone="default">{t("itsm.change.legacy.ticketFilter", { id: ticketId })}</Badge>
              <Button size="sm" variant="ghost" data-testid="legacy-list-ticket-clear" onClick={() => void navigate({ to: "/agent/itsm/legacy-changes", search: {} })}>
                {t("itsm.change.legacy.ticketFilterClear")}
              </Button>
            </p>
          </div>
        )}
      </div>

      {listQ.isError ? (
        <div className="rounded-lg border border-hairline bg-surface px-4 py-6 text-center text-sm text-danger" role="alert" data-testid="legacy-list-error" data-code={code ?? undefined}>
          {changeErrorText(listQ.error, t)}
        </div>
      ) : listQ.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : items.length === 0 ? (
        <p className="rounded-lg border border-hairline bg-surface px-4 py-8 text-center text-sm text-muted" data-testid="legacy-list-empty">
          {t("itsm.change.legacy.empty")}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-hairline bg-surface">
          <table className="w-full text-sm" data-testid="legacy-list-table">
            <thead className="border-b border-hairline text-left text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">{t("itsm.change.legacy.colChange")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.change.legacy.colState")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.change.list.colWindow")}</th>
                <th className="px-3 py-2 font-medium">{t("itsm.change.legacy.colWorkorders")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {items.map((c) => (
                <tr key={c.znuny_change_id} data-testid={`legacy-row-${c.znuny_change_id}`}>
                  <td className="px-3 py-2">
                    <RouterAnchor href={`/agent/itsm/legacy-changes/${c.znuny_change_id}`} className="text-accent hover:underline">
                      {c.title || c.number}
                    </RouterAnchor>
                    <div className="font-mono text-xs text-muted">{c.number}</div>
                  </td>
                  <td className="px-3 py-2 text-xs text-muted">{c.state}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-muted">{windowText(c.planned_start, c.planned_end) || t("itsm.change.list.noWindow")}</td>
                  <td className="px-3 py-2 text-xs text-muted">{c.workorder_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {items.length > 0 && (
        <div className="flex items-center justify-between gap-3 text-xs text-muted" data-testid="legacy-list-paging">
          <span data-testid="legacy-list-range">{t("itsm.change.list.range", { from: offset + 1, to: offset + items.length, total })}</span>
          <div className="flex gap-2">
            <Button size="sm" data-testid="legacy-list-prev" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              {t("itsm.change.list.prev")}
            </Button>
            <Button size="sm" data-testid="legacy-list-next" disabled={offset + items.length >= total} onClick={() => setOffset(offset + PAGE)}>
              {t("itsm.change.list.next")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
