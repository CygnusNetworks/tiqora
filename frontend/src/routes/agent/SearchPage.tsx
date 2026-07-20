import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { KbSearchHit } from "@/lib/api";
import { priorityName } from "@/lib/priority";
import { stateLabel } from "@/lib/status";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

export type SearchSearch = { q?: string; offset?: number };

function highlight(text: string | null | undefined, q: string): string {
  if (!text) return "";
  // Meilisearch may return <em> already; also do a simple client highlight
  if (/<em>/i.test(text)) return text;
  if (!q.trim()) return escapeHtml(text);
  const re = new RegExp(`(${escapeRegExp(q.trim())})`, "gi");
  return escapeHtml(text).replace(re, "<mark>$1</mark>");
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function SearchPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/agent/search" });
  const search = useSearch({ from: "/agent/search" }) as SearchSearch;
  const q = search.q ?? "";
  const offset = search.offset ?? 0;

  const resultsQ = useQuery({
    queryKey: ["search", q, offset],
    queryFn: () => api.search({ q, offset, limit: 20 }),
    enabled: q.trim().length > 0,
  });

  const kbResultsQ = useQuery({
    queryKey: ["search", "kb", q],
    queryFn: () => api.searchKb({ q, limit: 20 }),
    enabled: q.trim().length > 0,
  });

  // KB search returns per-chunk hits; dedupe to one entry per article.
  const kbHits: KbSearchHit[] = [];
  const seenArticles = new Set<number>();
  for (const hit of kbResultsQ.data?.hits ?? []) {
    if (seenArticles.has(hit.article_id)) continue;
    seenArticles.add(hit.article_id);
    kbHits.push(hit);
  }

  const isLoading = resultsQ.isLoading || kbResultsQ.isLoading;
  const hasResults = Boolean(resultsQ.data) || Boolean(kbResultsQ.data);
  const totalHits = (resultsQ.data?.hits.length ?? 0) + kbHits.length;

  return (
    <div className="mx-auto w-full max-w-4xl space-y-4 px-4 py-6" data-testid="search-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("search.title")}</h1>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const term = String(fd.get("q") || "").trim();
          void navigate({ search: { q: term, offset: 0 } });
        }}
        className="flex gap-2"
      >
        <input
          name="q"
          defaultValue={q}
          data-testid="search-input"
          placeholder={t("search.placeholder")}
          className="flex-1 rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
        />
        <button
          type="submit"
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-ink transition-colors duration-100 hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
          data-testid="search-submit"
        >
          {t("search.submit")}
        </button>
      </form>

      {!q.trim() && (
        <p className="text-sm text-muted">{t("search.hint")}</p>
      )}

      {q.trim() && isLoading && (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      )}

      {q.trim() && hasResults && (
        <div className="space-y-6" data-testid="search-results">
          <section className="space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("search.groupTickets")}
            </h2>
            {resultsQ.data && (
              <p className="text-xs text-muted" data-testid="search-total">
                {t("search.results", {
                  total: resultsQ.data.estimated_total,
                  query: resultsQ.data.query,
                })}
              </p>
            )}
            <ul className="space-y-2">
              {(resultsQ.data?.hits ?? []).map((hit) => (
                <li key={hit.id}>
                  <Link
                    to="/agent/tickets/$ticketId"
                    params={{ ticketId: String(hit.id) }}
                    className="block rounded-lg border border-hairline bg-surface p-3 transition-colors duration-100 hover:border-accent/60 hover:bg-surface-subtle"
                    data-testid={`search-hit-${hit.id}`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-accent">{hit.tn}</span>
                      {hit.state && <Badge tone="muted">{stateLabel(t, hit.state)}</Badge>}
                      {hit.priority && <Badge>{priorityName(hit.priority)}</Badge>}
                      {hit.queue_name && (
                        <span className="text-xs text-muted">{hit.queue_name}</span>
                      )}
                    </div>
                    <p
                      className="mt-1 text-sm font-medium text-ink"
                      dangerouslySetInnerHTML={{
                        __html: highlight(hit.title, q),
                      }}
                    />
                    {hit.excerpt && (
                      <p
                        className="mt-1 text-xs text-muted line-clamp-2 [&_em]:bg-escalation/30 [&_em]:not-italic [&_mark]:bg-escalation/30"
                        dangerouslySetInnerHTML={{
                          __html: highlight(hit.excerpt, q),
                        }}
                      />
                    )}
                  </Link>
                </li>
              ))}
              {resultsQ.data && resultsQ.data.hits.length === 0 && (
                <li className="py-4 text-center text-sm text-muted">
                  {t("search.noResults")}
                </li>
              )}
            </ul>
          </section>

          <section className="space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
              {t("search.groupKb")}
            </h2>
            <ul className="space-y-2" data-testid="search-kb-results">
              {kbHits.map((hit) => (
                <li key={hit.article_id}>
                  <Link
                    to="/agent/kb/$articleId"
                    params={{ articleId: String(hit.article_id) }}
                    className="block rounded-lg border border-hairline bg-surface p-3 transition-colors duration-100 hover:border-accent/60 hover:bg-surface-subtle"
                    data-testid={`search-kb-hit-${hit.article_id}`}
                  >
                    <p
                      className="text-sm font-medium text-ink"
                      dangerouslySetInnerHTML={{ __html: highlight(hit.title, q) }}
                    />
                    {hit.heading_path && (
                      <p className="text-xs text-muted">{hit.heading_path}</p>
                    )}
                    <p
                      className="mt-1 text-xs text-muted line-clamp-2"
                      dangerouslySetInnerHTML={{
                        __html: highlight(hit.content, q),
                      }}
                    />
                  </Link>
                </li>
              ))}
              {kbResultsQ.data && kbHits.length === 0 && (
                <li className="py-4 text-center text-sm text-muted">
                  {t("search.noResults")}
                </li>
              )}
            </ul>
          </section>

          {totalHits === 0 && resultsQ.data && kbResultsQ.data && (
            <p className="text-center text-sm text-muted">{t("search.noResults")}</p>
          )}
        </div>
      )}
    </div>
  );
}
