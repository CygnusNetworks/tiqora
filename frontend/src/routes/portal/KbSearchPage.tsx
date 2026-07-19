import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { portalApi, type KbSearchHit } from "@/lib/portalApi";
import { Spinner } from "@/components/ui/Spinner";
import { Button } from "@/components/ui/Button";

export type PortalKbSearch = { q?: string };

export function KbSearchPage() {
  const { t } = useTranslation();
  const navigate = useNavigate({ from: "/portal/kb" });
  const search = useSearch({ from: "/portal/kb" }) as PortalKbSearch;
  const q = search.q ?? "";
  const [draft, setDraft] = useState(q);

  const searchQ = useQuery({
    queryKey: ["portal", "kb", "search", q],
    queryFn: () => portalApi.portalSearchKb({ q }),
    enabled: q.trim().length > 0,
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const term = draft.trim();
    void navigate({ search: { q: term || undefined }, replace: true });
  };

  // Dedupe hits by article — search returns per-chunk hits.
  const hits = searchQ.data?.hits ?? [];
  const seen = new Set<number>();
  const articles: KbSearchHit[] = [];
  for (const hit of hits) {
    if (seen.has(hit.article_id)) continue;
    seen.add(hit.article_id);
    articles.push(hit);
  }

  return (
    <div className="space-y-4" data-testid="portal-kb-search-page">
      <h1 className="font-display text-xl font-semibold text-ink">{t("portal.kb.title")}</h1>
      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          data-testid="portal-kb-search-input"
          type="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t("portal.kb.searchPlaceholder")}
          className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent focus:border-accent"
        />
        <Button type="submit" variant="primary" data-testid="portal-kb-search-submit">
          {t("search.submit")}
        </Button>
      </form>

      {!q.trim() ? (
        <p className="text-sm text-muted">{t("portal.kb.hint")}</p>
      ) : searchQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : articles.length === 0 ? (
        <div
          className="rounded-lg border border-dashed border-hairline bg-surface px-4 py-8 text-center"
          data-testid="portal-kb-search-empty"
        >
          <p className="text-sm font-medium text-ink">{t("portal.kb.noResults")}</p>
          <p className="mt-1 text-sm text-muted">{t("portal.kb.noResultsHint")}</p>
        </div>
      ) : (
        <ul className="space-y-2" data-testid="portal-kb-search-results">
          {articles.map((hit) => (
            <li key={hit.article_id}>
              <Link
                to="/portal/kb/$slug"
                params={{ slug: String(hit.article_id) }}
                className="block rounded-lg border border-hairline bg-surface px-4 py-3 transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                data-testid={`portal-kb-hit-${hit.article_id}`}
              >
                <p className="text-sm font-medium text-ink">{hit.title}</p>
                {hit.heading_path && (
                  <p className="text-xs text-muted">{hit.heading_path}</p>
                )}
                <p className="mt-1 line-clamp-2 text-sm text-muted">{hit.content}</p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
